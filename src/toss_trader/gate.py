"""Gate v2 — 전략 채택 평가 게이트 (순수 stdlib).

`docs/gate_v2_spec.md`의 검정 프로토콜을 구현한다. 외부 의존성 없이
`math`, `statistics`(NormalDist로 Φ/Φ⁻¹), `random`, `json`, `datetime`,
`bisect`, `hashlib`만 사용한다(키리스·재현성).

설계 원칙(사양 §0):
- 모든 통계 API는 **평범한 파이썬 리스트**(일별 수익 / 자본곡선 / 거래 PnL)를 받는다.
  데이터 로딩·백테스터와 완전히 분리되어, 어떤 실험 스크립트든 얹어 쓸 수 있다.
- DSR/PSR·부트스트랩에 쓰는 Sharpe·모멘트는 전부 **비연율 일(또는 거래)단위**로 통일한다.
- 부트스트랩은 전부 **정상 부트스트랩(Politis–Romano)** — 자기상관을 보존한다.
- 다중검정을 1급 시민으로: 모든 시도를 원장(`trials_ledger.jsonl`)에 적재하고
  DSR·Reality Check가 원장에서 N(또는 N_eff)을 읽는다.

지표는 두 곡선에서 분리해 잰다(사양 §1):
- 화폐가중(DCA) 곡선 → terminal wealth, XIRR, 달러 MDD.
- 단위자본(TWR) 곡선 → CAGR/Sharpe/MDD/Ulcer/CVaR/skew/kurt (리스크 판정).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import NormalDist

_Z = NormalDist()                       # _Z.cdf(x)=Φ, _Z.inv_cdf(p)=Φ⁻¹
_EULER = 0.5772156649015329             # 오일러–마스케로니 상수 γ
_E = math.e

DEFAULT_LEDGER = "reports/trials_ledger.jsonl"
DEFAULT_Q = 0.1                         # 정상 부트스트랩 기대 블록길이 1/q = 10봉

__all__ = [
    # 화폐가중
    "xirr", "dca_cashflows", "cashflows_from_dca",
    # 시간가중/리스크
    "returns_to_equity", "cagr", "sharpe", "annualized_vol", "max_drawdown",
    "ulcer_index", "martin_ratio", "calmar", "cvar", "skew_kurt",
    # 다중검정
    "stationary_bootstrap_indices", "probabilistic_sharpe_ratio",
    "expected_max_sharpe", "deflated_sharpe_ratio", "whites_reality_check",
    "hansen_spa", "n_eff_clusters",
    # 부트스트랩 CI
    "stationary_bootstrap_mean_ci", "block_bootstrap_ci",
    # 강건성
    "plateau_test", "subperiod_consistency", "rolling_window_winrate",
    "cost_stress", "breakeven_cost_bps", "start_date_randomization",
    "vol_match_scale",
    # 거래단위(레인2/3)
    "trade_tstat", "trade_pnl_bootstrap_ci", "profit_factor", "expectancy",
    # 워크포워드
    "anchored_holdout", "walk_forward_splits",
    # 시도 원장
    "config_hash", "ledger_append", "ledger_count", "ledger_trial_sharpes",
    "already_peeked", "append_holdout_peek", "PeekOnceError",
    # 결정
    "Decision", "decide",
]


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────
def _mean(x: list[float]) -> float:
    return sum(x) / len(x)


def _std(x: list[float], ddof: int = 1) -> float:
    n = len(x)
    if n - ddof <= 0:
        return 0.0
    m = sum(x) / n
    return math.sqrt(sum((v - m) ** 2 for v in x) / (n - ddof))


def _quantile_sorted(sorted_x: list[float], p: float) -> float:
    """정렬된 표본에서 p분위(선형보간). 부트스트랩 분포 CI용."""
    if not sorted_x:
        return float("nan")
    if len(sorted_x) == 1:
        return sorted_x[0]
    idx = p * (len(sorted_x) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_x[lo]
    frac = idx - lo
    return sorted_x[lo] * (1 - frac) + sorted_x[hi] * frac


def _pearson(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a = a[:n]
    b = b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((a[i] - ma) ** 2 for i in range(n)))
    db = math.sqrt(sum((b[i] - mb) ** 2 for i in range(n)))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


# ── 화폐가중 (DCA 곡선) ──────────────────────────────────────────────────────
def xirr(flows: list[tuple[date, float]], guess: float = 0.1) -> float:
    """화폐가중 내부수익률. NPV(r)=Σ cf_i·(1+r)^(-Δt_i/365).

    입금은 음(-), 최종 청산·평가는 양(+)의 현금흐름으로 넣는다.
    Newton 반복으로 먼저 풀고, 발산하거나 미분≈0이면 [-0.9999, 10] 이분법으로
    폴백한다. 현금흐름 부호가 한 방향뿐이면(전손실 등) 해가 없으므로 nan을 반환하며,
    호출부는 최종자산으로 폴백 해석해야 한다(사양 §1.3, §10).
    """
    if len(flows) < 2:
        return float("nan")
    flows = sorted(flows, key=lambda f: f[0])
    t0 = flows[0][0]
    amounts = [cf for _, cf in flows]
    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return float("nan")                       # 부호변화 없음 → 해 없음
    days = [(d - t0).days / 365.0 for d, _ in flows]

    def npv(r: float) -> float:
        b = 1.0 + r
        return sum(cf * b ** (-t) for cf, t in zip(amounts, days))

    def dnpv(r: float) -> float:
        b = 1.0 + r
        return sum(cf * (-t) * b ** (-t - 1.0) for cf, t in zip(amounts, days))

    # 1) Newton
    r = guess
    for _ in range(100):
        if r <= -0.9999:
            break
        f = npv(r)
        df = dnpv(r)
        if df == 0 or not math.isfinite(df):
            break
        step = f / df
        r_new = r - step
        if not math.isfinite(r_new):
            break
        if abs(r_new - r) < 1e-10:
            if -0.9999 < r_new < 1e9 and abs(npv(r_new)) < 1e-7:
                return r_new
            break
        r = r_new
    if -0.9999 < r < 1e9 and math.isfinite(npv(r)) and abs(npv(r)) < 1e-7:
        return r

    # 2) 이분법 폴백 (표준 투자 현금흐름에서 NPV는 r에 단조감소)
    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if not (math.isfinite(flo) and math.isfinite(fhi)) or flo * fhi > 0:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        fm = npv(mid)
        if abs(fm) < 1e-10 or (hi - lo) < 1e-12:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return 0.5 * (lo + hi)


def dca_cashflows(deposits: list[tuple[date, float]], final_value: float,
                  final_date: date) -> list[tuple[date, float]]:
    """입금 스케줄 + 최종 평가액 → XIRR용 현금흐름 리스트.

    deposits: (날짜, 입금액>0) 목록. 투자자 관점에서 지갑을 떠나므로 음(-)으로 부호 반전.
    최종 평가액은 청산 가정으로 양(+)의 마지막 현금흐름.
    """
    flows: list[tuple[date, float]] = [(d, -abs(amt)) for d, amt in deposits]
    flows.append((final_date, float(final_value)))
    return flows


def cashflows_from_dca(dca_result, monthly_usd: float,
                       initial_usd: float = 0.0) -> list[tuple[date, float]]:
    """`backtest.DcaResult`(또는 `.equity_curve`를 가진 객체)에서 현금흐름을 복원한다.

    DcaResult는 입금 스케줄을 따로 보관하지 않으므로(사양 대비 실装 한계), `run_dca`의
    규칙 — **각 월 첫 거래일에 monthly_usd 입금** — 을 그대로 재현해 자본곡선의 날짜에서
    입금일을 도출한다. initial_usd가 있으면 첫 입금에 합산한다. [명시적 해석: §10]
    """
    curve = list(getattr(dca_result, "equity_curve", dca_result))
    if not curve:
        return []
    deposits: list[tuple[date, float]] = []
    seen: set[tuple[int, int]] = set()
    for d, _ in curve:
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            deposits.append((d, monthly_usd))
    if deposits and initial_usd:
        d0, a0 = deposits[0]
        deposits[0] = (d0, a0 + initial_usd)
    final_date, final_value = curve[-1]
    return dca_cashflows(deposits, final_value, final_date)


# ── 시간가중 / 리스크 (단위자본 곡선) ────────────────────────────────────────
def returns_to_equity(returns: list[float], start: float = 1.0) -> list[float]:
    """일별 단순수익률 → 자본곡선(앞에 start 시점 포함, 길이 = len(returns)+1)."""
    eq = [start]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    return eq


def cagr(equity: list[float], days: int) -> float:
    """연복리성장률. (eq[-1]/eq[0])^(365/days) − 1. 달력일수 기준."""
    if len(equity) < 2 or equity[0] <= 0 or equity[-1] <= 0 or days <= 0:
        return 0.0
    return (equity[-1] / equity[0]) ** (365.0 / days) - 1.0


def sharpe(returns: list[float], ppy: int = 252, rf: float = 0.0) -> float:
    """연율화 Sharpe. rf는 연 무위험수익률(일할 차감). 보고용(사양 §1.4).

    주의: DSR/PSR은 **비연율 일단위 SR**을 쓴다(별도 계산). 여기서 나온 값을 DSR에 넣지 말 것.
    """
    if len(returns) < 2:
        return 0.0
    rf_daily = rf / ppy
    ex = [r - rf_daily for r in returns]
    sd = _std(ex, ddof=1)
    if sd == 0:
        return 0.0
    return (_mean(ex) / sd) * math.sqrt(ppy)


def annualized_vol(returns: list[float], ppy: int = 252) -> float:
    return _std(returns, ddof=1) * math.sqrt(ppy)


def max_drawdown(equity: list[float]) -> float:
    """최대낙폭(음수 분수). min_t(eq_t/peak_t − 1)."""
    peak, mdd = -math.inf, 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def ulcer_index(equity: list[float]) -> float:
    """Ulcer Index. R_t = 100·(eq_t/peak_t − 1)(≤0); UI = sqrt(mean(R_t²))."""
    if not equity:
        return 0.0
    peak = -math.inf
    sq = 0.0
    for v in equity:
        if v > peak:
            peak = v
        r = 100.0 * (v / peak - 1.0) if peak > 0 else 0.0
        sq += r * r
    return math.sqrt(sq / len(equity))


def martin_ratio(equity: list[float], days: int, ppy: int = 252,
                 rf: float = 0.0) -> float:
    """Martin ratio(=UPI) = (연율수익 − rf) / UI(분수단위).

    UI는 퍼센트(예 10% 낙폭 → 10)이므로 분수로 환산(UI/100)해 CAGR(분수)와 스케일을 맞춘다.
    """
    ui = ulcer_index(equity)
    if ui <= 0:
        return 0.0
    return (cagr(equity, days) - rf) / (ui / 100.0)


def calmar(cagr_: float, mdd: float) -> float:
    """Calmar = CAGR / |MDD|."""
    if mdd == 0:
        return 0.0
    return cagr_ / abs(mdd)


def cvar(returns: list[float], alpha: float = 0.05) -> float:
    """CVaR(=Expected Shortfall). 하위 α분위 평균수익(음수=손실).

    k = ceil(α·T), 정렬 후 최악 k개 평균.
    """
    if not returns:
        return 0.0
    s = sorted(returns)
    k = max(1, math.ceil(alpha * len(s)))
    return sum(s[:k]) / k


def skew_kurt(returns: list[float]) -> tuple[float, float]:
    """(표본 왜도, 표본 첨도). 첨도는 정규=3 규약(초과첨도 아님).

    모집단 적률 추정(편의 추정): skew=m3/m2^1.5, kurt=m4/m2². PSR/DSR의 γ3,γ4에 쓴다.
    """
    n = len(returns)
    if n < 2:
        return (0.0, 3.0)
    m = _mean(returns)
    m2 = sum((r - m) ** 2 for r in returns) / n
    if m2 == 0:
        return (0.0, 3.0)
    m3 = sum((r - m) ** 3 for r in returns) / n
    m4 = sum((r - m) ** 4 for r in returns) / n
    return (m3 / m2 ** 1.5, m4 / m2 ** 2)


# ── 다중검정 ─────────────────────────────────────────────────────────────────
def stationary_bootstrap_indices(T: int, q: float,
                                 rng: random.Random) -> list[int]:
    """정상 부트스트랩(Politis–Romano) 인덱스. 기대 블록길이 = 1/q.

    자기상관을 보존한다(iid 부트스트랩과의 결정적 차이). q≈0.1이면 L≈10봉.
    """
    if T <= 0:
        return []
    idx = [rng.randrange(T)]
    for _ in range(T - 1):
        if rng.random() < q:
            idx.append(rng.randrange(T))
        else:
            idx.append((idx[-1] + 1) % T)
    return idx


def probabilistic_sharpe_ratio(sr_hat: float, sr_star: float, T: int,
                               skew: float, kurt: float) -> float:
    """PSR(SR*) = Φ( ((SR_hat−SR*)·sqrt(T−1)) / sqrt(1 − γ3·SR_hat + ((γ4−1)/4)·SR_hat²) ).

    SR_hat/SR*/γ3/γ4는 모두 **비연율 일단위**. Bailey & López de Prado.
    """
    if T < 2:
        return float("nan")
    denom_sq = 1.0 - skew * sr_hat + ((kurt - 1.0) / 4.0) * sr_hat * sr_hat
    if denom_sq <= 0:
        return float("nan")
    z = ((sr_hat - sr_star) * math.sqrt(T - 1)) / math.sqrt(denom_sq)
    return _Z.cdf(z)


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """N회 시도의 우연 Sharpe 상한 SR*₀ = sqrt(V)·[(1−γ)Φ⁻¹(1−1/N)+γΦ⁻¹(1−1/(N·e))].

    V = 시도들의 비연율 SR 표본분산, N = N_eff. N<2면 다중검정 페널티 없음(0).
    """
    if n_trials < 2 or var_sr <= 0:
        return 0.0
    a = _Z.inv_cdf(1.0 - 1.0 / n_trials)
    b = _Z.inv_cdf(1.0 - 1.0 / (n_trials * _E))
    return math.sqrt(var_sr) * ((1.0 - _EULER) * a + _EULER * b)


def deflated_sharpe_ratio(returns: list[float], sr_trials: list[float],
                          n_eff: int | None = None) -> float:
    """DSR = PSR(SR* = SR*₀). 다중검정을 반영한 Sharpe 유의확률.

    returns에서 비연율 일 SR·skew·kurt·T를 구하고, sr_trials(비연율 일 SR들)의 표본분산 V로
    SR*₀=expected_max_sharpe(N,V)를 만들어 PSR에 넣는다. n_eff 미지정시 N=len(sr_trials).
    판정: DSR ≥ 0.95 통과 / 0.90–0.95 경계(사양 §3.3).
    """
    if len(returns) < 2:
        return float("nan")
    sd = _std(returns, ddof=1)
    if sd == 0:
        return float("nan")
    sr_hat = _mean(returns) / sd
    sk, ku = skew_kurt(returns)
    n = n_eff if n_eff is not None else len(sr_trials)
    v = _std(sr_trials, ddof=1) ** 2 if len(sr_trials) >= 2 else 0.0
    sr_star = expected_max_sharpe(n, v)
    return probabilistic_sharpe_ratio(sr_hat, sr_star, len(returns), sk, ku)


def _aligned_diffs(diffs: dict[str, list[float]]) -> tuple[list[str], int]:
    names = list(diffs)
    if not names:
        return [], 0
    T = min(len(diffs[k]) for k in names)
    return names, T


def whites_reality_check(diffs: dict[str, list[float]], q: float = DEFAULT_Q,
                         B: int = 5000, rng: random.Random | None = None
                         ) -> tuple[float, dict]:
    """White Reality Check p-value(재중심화 정상 부트스트랩).

    diffs[k] = 전략 k의 벤치(B0) 대비 일별 초과수익(단위자본 스트림). 모두 같은 T로 정렬 가정
    (짧은 쪽에 맞춰 절단). 통계량 V̄ = max_k sqrt(T)·d̄_k, H0는 −d̄_k 재중심화로 부과.
    반환: (p, per-strategy 통계 dict). p<0.05 → 최고 전략이 탐색 감안해도 벤치를 유의 초과(§3.4).
    """
    names, T = _aligned_diffs(diffs)
    if not names or T < 2:
        return (float("nan"), {})
    rng = rng or random.Random(0)
    sqrtT = math.sqrt(T)
    series = {k: diffs[k][:T] for k in names}
    dbar = {k: _mean(series[k]) for k in names}
    v_obs = max(sqrtT * dbar[k] for k in names)

    count = 0
    for _ in range(B):
        I = stationary_bootstrap_indices(T, q, rng)
        v_star = -math.inf
        for k in names:
            s = series[k]
            mboot = sum(s[i] for i in I) / T
            val = sqrtT * (mboot - dbar[k])          # 재중심화(H0 부과)
            if val > v_star:
                v_star = val
        if v_star >= v_obs:
            count += 1
    p = count / B
    stats = {k: {"d_bar": dbar[k], "sqrtT_dbar": sqrtT * dbar[k]} for k in names}
    stats["_V_obs"] = v_obs
    return (p, stats)


def hansen_spa(diffs: dict[str, list[float]], q: float = DEFAULT_Q,
               B: int = 5000, rng: random.Random | None = None) -> float:
    """Hansen SPA p-value(스튜던트화 + consistent recentering).

    각 k를 부트스트랩 표준편차 ω̂_k로 스튜던트화하고, d̄_k ≥ −sqrt((ω̂_k²/T)·2·loglogT)인
    전략만 재중심화 집합에 넣어(나쁜 전략이 임계값을 부풀리지 않게) 검정력을 지킨다.
    통계량 max_k(sqrt(T)·d̄_k/ω̂_k, 0). RC보다 검정력↑, 1차는 RC·2차 보강(사양 §3.4).
    """
    names, T = _aligned_diffs(diffs)
    if not names or T < 2:
        return float("nan")
    rng = rng or random.Random(0)
    sqrtT = math.sqrt(T)
    series = {k: diffs[k][:T] for k in names}
    dbar = {k: _mean(series[k]) for k in names}

    # 1) 부트스트랩 표본평균을 모아 ω̂_k(스케일)와 null 분포를 동시에 만든다.
    boot_means: dict[str, list[float]] = {k: [] for k in names}
    for _ in range(B):
        I = stationary_bootstrap_indices(T, q, rng)
        for k in names:
            s = series[k]
            boot_means[k].append(sum(s[i] for i in I) / T)
    omega = {k: sqrtT * _std(boot_means[k], ddof=0) for k in names}  # sd(sqrt(T)·mean)

    # 2) 관측 통계량
    def stud(k: str) -> float:
        return sqrtT * dbar[k] / omega[k] if omega[k] > 0 else 0.0
    v_obs = max(0.0, max(stud(k) for k in names))

    # 3) consistent recentering 집합
    llt = math.log(math.log(T)) if T > math.e ** math.e else 0.0
    thr = math.sqrt(2.0 * max(llt, 0.0))
    included = {k for k in names if omega[k] > 0 and stud(k) >= -thr}

    # 4) 부트스트랩 null 분포 (포함 전략만, 자기 d̄_k로 재중심화)
    count = 0
    for b in range(B):
        v_star = 0.0
        for k in included:
            z = sqrtT * (boot_means[k][b] - dbar[k]) / omega[k]
            if z > v_star:
                v_star = z
        if v_star >= v_obs:
            count += 1
    return count / B


def n_eff_clusters(streams: dict[str, list[float]], theta: float = 0.9) -> int:
    """유효 시도 수 N_eff = 수익스트림 상관 그리디 클러스터 수(사양 §3.2).

    거의 동일한 config(lookback 200 vs 201 등)를 하나로 흡수해 DSR·RC가 과도하게
    엄격해지는 것을 막는다. pearson(r, medoid) > theta면 같은 클러스터. 보수적 폴백은 raw N.
    """
    clusters: list[list[float]] = []          # 각 클러스터의 medoid(대표 스트림)
    for _, r in streams.items():
        joined = False
        for medoid in clusters:
            if _pearson(r, medoid) > theta:
                joined = True
                break
        if not joined:
            clusters.append(r)
    return max(1, len(clusters)) if streams else 0


# ── 부트스트랩 CI ────────────────────────────────────────────────────────────
def stationary_bootstrap_mean_ci(x: list[float], q: float = DEFAULT_Q,
                                 B: int = 5000, rng: random.Random | None = None,
                                 alpha: float = 0.05) -> tuple[float, float, float]:
    """정상 부트스트랩 평균의 (점추정, 하한, 상한). 자기상관 보존.

    반환 (mean(x), α/2 분위, 1−α/2 분위). 초과수익·거래PnL 평균의 진짜 엣지 검정용.
    """
    T = len(x)
    if T < 2:
        m = x[0] if x else float("nan")
        return (m, m, m)
    rng = rng or random.Random(0)
    means = []
    for _ in range(B):
        I = stationary_bootstrap_indices(T, q, rng)
        means.append(sum(x[i] for i in I) / T)
    means.sort()
    return (_mean(x), _quantile_sorted(means, alpha / 2.0),
            _quantile_sorted(means, 1.0 - alpha / 2.0))


def block_bootstrap_ci(x: list[float], block: int = 21, B: int = 5000,
                       rng: random.Random | None = None, alpha: float = 0.05
                       ) -> tuple[float, float, float]:
    """고정 블록길이 순환 블록 부트스트랩 평균의 (점추정, 하한, 상한).

    정상 부트스트랩의 대안(블록길이 고정). v1의 블록부트스트랩을 CI 형태로 승격(사양 부록 A).
    """
    T = len(x)
    if T < 2:
        m = x[0] if x else float("nan")
        return (m, m, m)
    block = max(1, min(block, T))
    rng = rng or random.Random(0)
    nb = max(1, math.ceil(T / block))
    means = []
    for _ in range(B):
        samp: list[float] = []
        for _ in range(nb):
            st = rng.randrange(T)
            samp.extend(x[(st + j) % T] for j in range(block))
        means.append(sum(samp) / len(samp))
    means.sort()
    return (_mean(x), _quantile_sorted(means, alpha / 2.0),
            _quantile_sorted(means, 1.0 - alpha / 2.0))


# ── 강건성 ───────────────────────────────────────────────────────────────────
def plateau_test(eval_fn, base_params: dict, grid=(0.5, 0.7, 0.8, 1.2, 1.5),
                 keep_frac: float = 0.8) -> dict:
    """파라미터 이웃 평탄성(고원 vs 뾰족봉우리) 검정(사양 §4.1).

    eval_fn(params) → {"net_excess": vs B0 초과(분수), "sharpe": 단위자본 Sharpe}.
    각 수치 파라미터를 grid 배수로 흔들어 이웃점을 만든다. 통과 조건:
      (1) 이웃의 ≥keep_frac 이 net_excess>0, 그리고
      (2) 이웃 median Sharpe ≥ 0.9 × 중심 Sharpe.
    """
    center = eval_fn(dict(base_params))
    center_sharpe = float(center.get("sharpe", 0.0))
    neigh_pos = 0
    neigh_sharpes: list[float] = []
    n = 0
    for key, val in base_params.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        for g in grid:
            params = dict(base_params)
            params[key] = type(val)(val * g) if isinstance(val, int) else val * g
            res = eval_fn(params)
            n += 1
            if float(res.get("net_excess", -1.0)) > 0:
                neigh_pos += 1
            neigh_sharpes.append(float(res.get("sharpe", 0.0)))
    frac_positive = (neigh_pos / n) if n else 0.0
    neigh_sharpes.sort()
    med = _quantile_sorted(neigh_sharpes, 0.5) if neigh_sharpes else 0.0
    passed = (n > 0 and frac_positive >= keep_frac
              and med >= 0.9 * center_sharpe)
    return {"pass": passed, "frac_positive": frac_positive,
            "neighbor_median_sharpe": med, "center_sharpe": center_sharpe,
            "n_neighbors": n}


def _index_at_or_after(dates: list[date], target: date) -> int:
    return bisect.bisect_left(dates, target)


def subperiod_consistency(strat_curve: list[float], bench_curve: list[float],
                          dates: list[date], window_years: int = 3,
                          step_months: int = 6) -> dict:
    """롤링 창(window_years, step_months)에서 후보가 B0를 이긴 창 비율(사양 §4.2).

    curve 들은 dates에 정렬된 자본곡선. 각 창의 총수익을 비교해 승/패를 센다.
    승률 ≥0.70 강건, 0.60–0.70 경계, <0.60 불합격(결정표에서 사용).
    """
    n = min(len(strat_curve), len(bench_curve), len(dates))
    if n < 2:
        return {"win_rate": 0.0, "wins": 0, "n_windows": 0, "windows": []}
    strat_curve, bench_curve, dates = strat_curve[:n], bench_curve[:n], dates[:n]
    windows = []
    wins = 0
    start = dates[0]
    while True:
        end = date(start.year + window_years, start.month,
                   min(start.day, 28))
        if end > dates[-1]:
            break
        i0 = _index_at_or_after(dates, start)
        i1 = _index_at_or_after(dates, end)
        if i1 >= n:
            i1 = n - 1
        if i1 <= i0 or strat_curve[i0] <= 0 or bench_curve[i0] <= 0:
            start = _add_months(start, step_months)
            continue
        sr = strat_curve[i1] / strat_curve[i0] - 1.0
        br = bench_curve[i1] / bench_curve[i0] - 1.0
        win = sr > br
        wins += int(win)
        windows.append({"start": start.isoformat(), "end": dates[i1].isoformat(),
                        "strat_ret": sr, "bench_ret": br, "win": win})
        start = _add_months(start, step_months)
    nw = len(windows)
    return {"win_rate": wins / nw if nw else 0.0, "wins": wins,
            "n_windows": nw, "windows": windows}


def rolling_window_winrate(strat_curve: list[float], bench_curve: list[float],
                           dates: list[date], window_years: int = 3,
                           step_months: int = 6) -> float:
    """롤링 창 승률만 반환하는 얇은 래퍼(사양 §4.2)."""
    return subperiod_consistency(strat_curve, bench_curve, dates,
                                 window_years, step_months)["win_rate"]


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, 28))


def cost_stress(eval_fn, mult: float = 2.0) -> dict:
    """비용 스트레스: 비용 mult배에서 net 초과(vs B0)>0 유지 여부(사양 §4.3).

    eval_fn(cost_mult: float) → {"net_excess": 분수}. 레인1은 총비용 2×, 레인3은 슬리피지 1.5–2×.
    """
    base = float(eval_fn(1.0).get("net_excess", 0.0))
    stressed = float(eval_fn(mult).get("net_excess", 0.0))
    return {"mult": mult, "net_excess_base": base, "net_excess_stressed": stressed,
            "pass": stressed > 0}


def breakeven_cost_bps(eval_fn, lo: float = 0.0, hi: float = 0.02) -> float:
    """초과이득(vs B0)이 0이 되는 왕복 비용을 이분탐색해 **bps**로 반환(사양 §4.3).

    eval_fn(roundtrip_cost_fraction: float) → net_excess(분수). 비용에 단조감소 가정.
    lo에서 이미 net≤0 → lo(=최악비용 없이도 이득 없음). hi에서 여전히 net>0 → hi(엣지가 hi 초과 생존).
    반환값이 현실 최악비용보다 여유 있게 커야 채택 후보(§4.3).
    """
    flo = float(eval_fn(lo))
    fhi = float(eval_fn(hi))
    if flo <= 0:
        return lo * 1e4
    if fhi > 0:
        return hi * 1e4
    a, b = lo, hi
    for _ in range(60):
        mid = 0.5 * (a + b)
        fm = float(eval_fn(mid))
        if abs(fm) < 1e-12:
            return mid * 1e4
        if fm > 0:
            a = mid
        else:
            b = mid
    return 0.5 * (a + b) * 1e4


def start_date_randomization(dca_fn_strat, dca_fn_bench,
                             offsets: list[int]) -> dict:
    """DCA 시작월 offset을 흔들어 전략이 B0를 이긴 비율(행운의 진입 제거, 사양 §4.4).

    dca_fn_strat(offset)·dca_fn_bench(offset) → 최종자산(또는 XIRR, 동일 척도).
    요구: ≥70% offset 승 + median 승폭(비율) > 비용스트레스 여유.
    """
    wins = 0
    margins: list[float] = []
    rows = []
    for s in offsets:
        strat = float(dca_fn_strat(s))
        bench = float(dca_fn_bench(s))
        win = strat > bench
        wins += int(win)
        margin = (strat / bench - 1.0) if bench > 0 else 0.0
        margins.append(margin)
        rows.append({"offset": s, "strat": strat, "bench": bench,
                     "margin": margin, "win": win})
    n = len(offsets)
    margins_sorted = sorted(margins)
    return {"win_rate": wins / n if n else 0.0, "wins": wins, "n": n,
            "median_margin": _quantile_sorted(margins_sorted, 0.5) if margins else 0.0,
            "rows": rows}


def vol_match_scale(returns: list[float], target_ann_vol: float = 0.10,
                    ppy: int = 252) -> float:
    """목표 연변동성에 맞추는 스케일계수 = target / 실현 연변동성(사양 §5.3).

    변동성 매칭 비교: 후보/B0를 이 계수로 스케일한 뒤 최종자산을 비교해 '레버리지·vol-target의
    진짜 시험'을 한다. 실현 변동성 0이면 nan.
    """
    av = annualized_vol(returns, ppy)
    if av == 0:
        return float("nan")
    return target_ann_vol / av


# ── 거래단위 (레인2/3) ───────────────────────────────────────────────────────
def trade_tstat(pnls: list[float]) -> float:
    """거래별 순PnL의 t = mean/(std/sqrt(n)). 인트라데이 필요조건 t≥3(사양 §3.5)."""
    n = len(pnls)
    if n < 2:
        return 0.0
    sd = _std(pnls, ddof=1)
    if sd == 0:
        return 0.0
    return _mean(pnls) / (sd / math.sqrt(n))


def trade_pnl_bootstrap_ci(pnls: list[float], q: float = DEFAULT_Q,
                           B: int = 5000, rng: random.Random | None = None,
                           alpha: float = 0.05) -> tuple[float, float, float]:
    """거래 PnL 정상 부트스트랩 평균의 (점추정, 하한, 상한). CI 하한>0 요구(사양 §3.5)."""
    return stationary_bootstrap_mean_ci(pnls, q=q, B=B, rng=rng, alpha=alpha)


def profit_factor(pnls: list[float]) -> float:
    """Profit factor = Σwin / |Σloss|. 손실 없으면 inf(이익 있을 때)."""
    win = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    if loss == 0:
        return math.inf if win > 0 else 0.0
    return win / abs(loss)


def expectancy(pnls: list[float]) -> float:
    """1거래당 기대 순손익."""
    return _mean(pnls) if pnls else 0.0


# ── 워크포워드 ───────────────────────────────────────────────────────────────
def anchored_holdout(dates: list[date],
                     design_end: date) -> tuple[list[int], list[int]]:
    """앵커드 홀드아웃 분할(peek-once). 설계기간(≤design_end)·홀드아웃(>design_end) 인덱스.

    설계기간에서만 튜닝, 확정 후 홀드아웃 1회 평가(사양 §2.1).
    """
    design = [i for i, d in enumerate(dates) if d <= design_end]
    holdout = [i for i, d in enumerate(dates) if d > design_end]
    return (design, holdout)


def walk_forward_splits(dates: list[date], train_days: int, test_days: int,
                        step_days: int, embargo_days: int
                        ) -> list[tuple[list[int], list[int]]]:
    """앵커드 워크포워드 분할(purge/embargo gap 포함, 사양 §2.2).

    train은 항상 시작점부터 확장(anchored). 각 폴드: train[start, T],
    gap=embargo_days, test(T+gap, T+gap+test_days]. T는 step_days씩 전진.
    반환: [(train_indices, test_indices), ...].
    """
    if not dates:
        return []
    start = dates[0]
    last = dates[-1]
    splits: list[tuple[list[int], list[int]]] = []
    T = start + timedelta(days=train_days)
    while True:
        test_start = T + timedelta(days=embargo_days)
        test_end = test_start + timedelta(days=test_days)
        if test_start > last:
            break
        train_idx = [i for i, d in enumerate(dates) if d <= T]
        test_idx = [i for i, d in enumerate(dates)
                    if test_start < d <= test_end]
        if train_idx and test_idx:
            splits.append((train_idx, test_idx))
        if test_end > last:
            break
        T = T + timedelta(days=step_days)
    return splits


# ── 시도 원장 (append-only) ──────────────────────────────────────────────────
class PeekOnceError(RuntimeError):
    """홀드아웃을 두 번 엿보려 할 때 발생(peek-once 강제, 사양 §2.1/§3.3)."""


def config_hash(config: dict) -> str:
    """config dict의 안정적 해시(정렬 JSON의 sha1 앞 12자리). 원장 config_hash용."""
    blob = json.dumps(config, sort_keys=True, ensure_ascii=False,
                      default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]


def ledger_append(path: str, record: dict) -> None:
    """원장에 1행(JSONL) 추가. ts 없으면 현재 UTC를 기입. 부모 디렉터리 자동 생성."""
    record = dict(record)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _iter_ledger(path: str):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def ledger_count(path: str, idea_id: str | None = None) -> int:
    """원장 시도 수. idea_id 지정시 그 아이디어만, None이면 전체."""
    return sum(1 for rec in _iter_ledger(path)
               if idea_id is None or rec.get("idea_id") == idea_id)


def ledger_trial_sharpes(path: str, idea_id: str | None = None) -> list[float]:
    """원장에서 비연율 일 SR(sr_daily) 목록. DSR·RC의 V(시도 SR 분산)용."""
    out: list[float] = []
    for rec in _iter_ledger(path):
        if idea_id is not None and rec.get("idea_id") != idea_id:
            continue
        v = rec.get("sr_daily")
        if isinstance(v, (int, float)) and math.isfinite(v):
            out.append(float(v))
    return out


def already_peeked(path: str, idea_id: str) -> bool:
    """이 아이디어가 이미 홀드아웃을 평가했는가(peek-once 강제 장치, 사양 §2.1)."""
    for rec in _iter_ledger(path):
        if rec.get("idea_id") != idea_id:
            continue
        if rec.get("period") == "holdout" or rec.get("peeked_holdout") is True:
            return True
    return False


def append_holdout_peek(path: str, idea_id: str, record: dict) -> None:
    """홀드아웃 평가 결과를 원장에 적재하되 **1회만** 허용.

    이미 엿봤으면 PeekOnceError를 던져 코드로 peek-once를 강제한다(사양 §2.1). 이 함수를 통하지
    않은 수기 홀드아웃 평가는 게이트 증거로 불인정(§3.1).
    """
    if already_peeked(path, idea_id):
        raise PeekOnceError(
            f"idea_id={idea_id!r} 는 이미 홀드아웃을 1회 평가했습니다. "
            f"다시 보려면 새 idea_id(새 아이디어, N++)로 등록하세요(사양 §2.1).")
    rec = dict(record)
    rec["idea_id"] = idea_id
    rec["period"] = "holdout"
    rec["peeked_holdout"] = True
    ledger_append(path, rec)


# ── 결정표 (PASS / CONDITIONAL / FAIL) ───────────────────────────────────────
_RANK = {"PASS": 0, "CONDITIONAL": 1, "FAIL": 2, "SKIP": -1}
MDD_HARD_CAP = -0.50                      # 단위자본 OOS MDD 하드캡(사양 §5.1)


@dataclass
class Decision:
    verdict: str                          # PASS | CONDITIONAL | FAIL
    reasons: list[str] = field(default_factory=list)
    axes: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:             # str(decision) == verdict (사양 §10 시그니처)
        return self.verdict

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "axes": self.axes, "reasons": self.reasons}


def _combine(axes: dict[str, str]) -> str:
    graded = [v for v in axes.values() if v != "SKIP"]
    if not graded:
        return "CONDITIONAL"
    if any(v == "FAIL" for v in graded):
        return "FAIL"
    if any(v == "CONDITIONAL" for v in graded):
        return "CONDITIONAL"
    return "PASS"


def _grade(cond_pass: bool, cond_fail: bool) -> str:
    if cond_fail:
        return "FAIL"
    return "PASS" if cond_pass else "CONDITIONAL"


def decide(metrics: dict) -> Decision:
    """§6 결정표를 인코딩해 PASS/CONDITIONAL/FAIL과 사유를 낸다.

    metrics는 레인별 축 입력을 담는다. 모든 축을 AND로 결합하고, 하나라도 FAIL이면 전체 FAIL,
    FAIL이 없고 CONDITIONAL이 있으면 CONDITIONAL, 전부 PASS면 PASS다.

    레인 공통 키(레인1/2):
      terminal_vs_b0   후보 최종자산 / B0 (>1 이면 B0 초과)
      terminal_vs_b1   후보 최종자산 / B1 (온전성; <1 이면 B1 열위)
      dsr              Deflated Sharpe (≥0.95 PASS, 0.90–0.95 경계)
      rc_pvalue        Reality Check p (<0.05 PASS)
      plateau_pass     bool / plateau_borderline bool
      subperiod_winrate 롤링 창 승률(≥0.70 PASS, 0.60–0.70 경계)
      cost_stress_mult 순엣지가 살아남는 최대 비용배수(≥2 PASS, ≥1.5 경계)
      start_date_winrate DCA 시작일 랜덤화 승률(≥0.70 PASS, 0.55–0.70 경계)
      mdd              단위자본 OOS MDD(≤−0.50 하드캡 위반)
      calmar_not_worse / ulcer_not_worse bool (B0 대비 비열위)
    레인2 추가: biased_universe(bool), survivorship_haircut_ok(bool), trade_tstat.
    레인3(인트라데이): n_trades, trade_tstat, trade_ci_lo, slippage_stress_pass.
      → 백테스트로는 절대 PASS 없음. 필요조건 충족시 최대 CONDITIONAL(포워드 전용, §8.3).
    """
    lane = int(metrics.get("lane", 1))
    if lane == 3:
        return _decide_lane3(metrics)

    axes: dict[str, str] = {}
    reasons: list[str] = []

    # 1) 경제적 이득
    tv0 = metrics.get("terminal_vs_b0")
    tv1 = metrics.get("terminal_vs_b1")
    if tv0 is None:
        axes["economic"] = "SKIP"
    else:
        if tv0 < 1.0:
            axes["economic"] = "FAIL"
            reasons.append(f"경제: 최종자산이 B0 미달(vs_B0={tv0:.3f}) → FAIL")
        elif tv1 is not None and tv1 < 1.0:
            axes["economic"] = "CONDITIONAL"
            reasons.append(f"경제: B0는 이기나 B1 열위(vs_B1={tv1:.3f}) → 온전성 경계")
        else:
            axes["economic"] = "PASS"

    # 레인2 생존편향 haircut: 편향 유니버스에서 haircut 미충족이면 경제 축을 강등
    if lane == 2 and metrics.get("biased_universe"):
        if metrics.get("survivorship_haircut_ok") is False and axes.get("economic") == "PASS":
            axes["economic"] = "CONDITIONAL"
            reasons.append("경제(레인2): 편향 유니버스 haircut 마진 미달 → 경계 강등(§8.2)")

    # 2) 유의성(다중검정)
    dsr = metrics.get("dsr")
    rcp = metrics.get("rc_pvalue")
    if dsr is None and rcp is None:
        axes["significance"] = "SKIP"
    else:
        dsr_pass = dsr is not None and dsr >= 0.95
        dsr_border = dsr is not None and 0.90 <= dsr < 0.95
        rc_pass = rcp is not None and rcp < 0.05
        if dsr_pass and rc_pass:
            axes["significance"] = "PASS"
        elif (dsr_pass or rc_pass) or dsr_border:
            axes["significance"] = "CONDITIONAL"
            reasons.append(f"유의성: 하나만 통과 또는 DSR 경계(DSR={dsr}, RC_p={rcp}) → 경계")
        else:
            axes["significance"] = "FAIL"
            reasons.append(f"유의성: DSR·RC 둘 다 미달(DSR={dsr}, RC_p={rcp}) → FAIL")

    # 레인2는 거래단위 유의성도 요구(t≥3)
    if lane == 2 and metrics.get("trade_tstat") is not None:
        if metrics["trade_tstat"] < 3.0 and axes.get("significance") == "PASS":
            axes["significance"] = "CONDITIONAL"
            reasons.append(f"유의성(레인2): 거래 t={metrics['trade_tstat']:.2f}<3 → 경계(§3.5)")

    # 3) 평탄성
    if "plateau_pass" in metrics:
        if metrics["plateau_pass"]:
            axes["plateau"] = "PASS"
        elif metrics.get("plateau_borderline"):
            axes["plateau"] = "CONDITIONAL"
            reasons.append("평탄성: 경계")
        else:
            axes["plateau"] = "FAIL"
            reasons.append("평탄성: 뾰족봉우리(과적합) → FAIL")
    else:
        axes["plateau"] = "SKIP"

    # 4) 서브구간 일관성
    wr = metrics.get("subperiod_winrate")
    if wr is None:
        axes["subperiod"] = "SKIP"
    else:
        regime_conc = bool(metrics.get("subperiod_regime_concentrated"))
        if wr >= 0.70 and not regime_conc:
            axes["subperiod"] = "PASS"
        elif wr >= 0.60:
            axes["subperiod"] = "CONDITIONAL"
            reasons.append(f"서브구간: 승률 {wr:.2f}(0.60–0.70) 또는 레짐 편중 → 경계")
        else:
            axes["subperiod"] = "FAIL"
            reasons.append(f"서브구간: 승률 {wr:.2f}<0.60 → FAIL")

    # 5) 비용 스트레스
    csm = metrics.get("cost_stress_mult")
    if csm is None:
        axes["cost_stress"] = "SKIP"
    else:
        if csm >= 2.0:
            axes["cost_stress"] = "PASS"
        elif csm >= 1.5:
            axes["cost_stress"] = "CONDITIONAL"
            reasons.append(f"비용: {csm:.2f}×까지만 생존(1.5–2×) → 경계")
        else:
            axes["cost_stress"] = "FAIL"
            reasons.append(f"비용: {csm:.2f}× 에서 이미 net≤0 → FAIL")

    # 6) 시작일 랜덤화
    sdw = metrics.get("start_date_winrate")
    if sdw is None:
        axes["start_date"] = "SKIP"
    else:
        if sdw >= 0.70:
            axes["start_date"] = "PASS"
        elif sdw >= 0.55:
            axes["start_date"] = "CONDITIONAL"
            reasons.append(f"시작일: 승률 {sdw:.2f}(0.55–0.70) → 경계")
        else:
            axes["start_date"] = "FAIL"
            reasons.append(f"시작일: 승률 {sdw:.2f}<0.55 → FAIL")

    # 7) 리스크(하드캡 + Calmar/Ulcer 비열위)
    mdd = metrics.get("mdd")
    if mdd is None:
        axes["risk"] = "SKIP"
    else:
        if mdd <= MDD_HARD_CAP:
            axes["risk"] = "FAIL"
            reasons.append(f"리스크: 단위자본 MDD {mdd:.2%} ≤ 하드캡 −50% → FAIL(§5.1)")
        else:
            calmar_ok = metrics.get("calmar_not_worse", True)
            ulcer_ok = metrics.get("ulcer_not_worse", True)
            if calmar_ok and ulcer_ok:
                axes["risk"] = "PASS"
            else:
                axes["risk"] = "CONDITIONAL"
                reasons.append("리스크: 캡은 지키나 Calmar/Ulcer 열위 → 소액 슬리브 한정(§5.3)")

    verdict = _combine(axes)
    if verdict == "PASS":
        reasons.insert(0, "전 축 green → 채택 절차(소액 슬리브+포워드 페이퍼, §6.1) 진입")
    return Decision(verdict=verdict, reasons=reasons, axes=axes)


def _decide_lane3(metrics: dict) -> Decision:
    """레인3(인트라데이): 백테스트로는 '채택' 없음 — 포워드 페이퍼로 종결(사양 §8.3).

    필요조건(모두 충족해야 CONDITIONAL, 하나라도 미달이면 값싼 기각=FAIL):
      n_trades≥200, per-trade t≥3.0, 거래PnL 부트 CI 하한>0, 슬리피지 2× 스트레스 생존.
    """
    axes: dict[str, str] = {}
    reasons: list[str] = []
    n = metrics.get("n_trades")
    t = metrics.get("trade_tstat")
    ci_lo = metrics.get("trade_ci_lo")
    slip_ok = metrics.get("slippage_stress_pass")

    ok = True
    if n is None or n < 200:
        ok = False
        reasons.append(f"레인3: n_trades={n} < 200 → 통계주장 불가, 값싼 기각")
    if t is None or t < 3.0:
        ok = False
        reasons.append(f"레인3: per-trade t={t} < 3.0 → 미달")
    if ci_lo is None or ci_lo <= 0:
        ok = False
        reasons.append(f"레인3: 거래PnL 부트 CI 하한={ci_lo} ≤ 0 → 미달")
    if not slip_ok:
        ok = False
        reasons.append("레인3: 슬리피지 2× 스트레스 미생존 → 미달")

    if ok:
        axes["intraday_necessary"] = "PASS"
        reasons.append("레인3 필요조건 충족 → CONDITIONAL(포워드 페이퍼 편입 후보). "
                       "마이크로구조 수집→검증→페이퍼 순서로만 승격(§8.3). 백테스트 채택 불가.")
        return Decision(verdict="CONDITIONAL", reasons=reasons, axes=axes)
    axes["intraday_necessary"] = "FAIL"
    return Decision(verdict="FAIL", reasons=reasons, axes=axes)
