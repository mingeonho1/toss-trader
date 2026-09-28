#!/usr/bin/env python3
"""c12s — "레버리지 ETF 없는 레버리지"(Lane A, 사이클12 `c12s`).

동기: 이력 없는 소액 한국 리테일 계좌는 규제 게이트(해외 레버리지 ETP 첫 거래 시 기본예탁금
₩1,000만 + 사전교육; 2026-05 시행, 2026-07 보도상 ₩3,000만으로 상향; 단일종목 레버리지는 추가교육)
때문에 TQQQ/QLD 등을 실거래로 **못 살 수 있다**(docs/aggressive_strategy_catalog.md §0-1).
대안: **완전히 허용되는 일반 고베타 단일주식**(소수점 매수 가능)으로 공격적(≈2x) 노출을 만든다.

이 스크립트는 **src/ 를 수정하지 않고 소비만** 한다. c3c_pit 의 PIT(as-of) S&P500 멤버십 기계,
c2d_stocks 의 포트폴리오 시뮬레이터(다음시가 체결·일별 MTM)·비용 티어를 재사용하고, 고베타
선별·베타 캐시·Lane A 판정만 새로 얹는다.

사전등록(실행 전 고정, 튜닝 금지 — 아이디어당 사전등록 1개 config + 평탄성 이웃 ±20~50%):
  1) c12s_hibeta_basket : 월간, PIT S&P500 as-of 멤버 중 252d QQQ 베타 상위 10(최소가 $5,
                          63d 달러거래대금 상위 300) 동일가중. 목표 포트폴리오 베타 ≈ 2. 상시투자.
  2) c12s_hibeta_trend  : (1)의 바스켓을 QQQ>SMA200 일 때만, 아니면 현금(LRR 아이디어를 TQQQ 대신
                          고베타 주식으로 구현).
  3) c12s_mom_hibeta    : 베타 상위 50 이름 중 12-1 모멘텀 top5, 월간, QQQ>SMA200 필터.
  4) c12s_ep_gap        : Episodic Pivot(카탈로그 F5) — 갭>+10% & RVOL>3 & 종가 상단, 다음시가 매수,
                          10일 SMA 이탈 시 청산. PIT 유니버스, 동시 최대 3포지션. 거래단위 통계.
  5) c12s_btc_proxy     : G3형 BTC 추세(FRED CBBTCUSD > 100d SMA) → MSTR/COIN(1x 단일주, 허용) else 현금.
  6) c12s_hibeta_ddguard: (창의) 고베타 top10 바스켓 + **드로다운 가드** — QQQ가 100일 고점 대비 10%↓면
                          현금. 레버리지 ETP를 죽이는 좌측꼬리(급락)를 SMA200보다 빠르게 회피.

비용(task 지정): 0.1%/side(=10bp) + 반호가 대형주 3bp / 핫 15bp + 슬리피지 5bp, 다음시가 체결.
  2× 스트레스, 마이크로 레짐(≤$10 매수 무료 = 수수료 0). 분할 2016-09~2021-12 / 홀드아웃 2022~2026-09.

데이터 한계(c3c 계승): as-of S&P500 멤버 중 캐시 보유는 ~27~31%(대형·생존주 편중). 커버리지를
보고하고, 편중이 크므로 **모든 성과를 상한(UPPER BOUND)** 으로 취급한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata, research  # noqa: E402
from toss_trader.research import CostSpec  # noqa: E402
import gate_eval  # noqa: E402
import c2d_stocks as c2d  # noqa: E402
import c3c_pit as c3c  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
LANE = "A"                                # 원장 레인(부록 v3 Lane A)
DESIGN_END = date(2021, 12, 31)
START = date(2016, 9, 1)
END = date(2026, 9, 30)
BENCH = "QQQ"

DV_TOP = 300          # 63d 달러거래대금 상위 300(≈지수 내 대형·유동주)
BETA_WIN = 252        # 베타 추정 창(거래일)
MIN_PRICE = 5.0       # 최소 가격($, 원시 종가)
BANKRUPTCY_MDD = -0.95  # 전표본 MDD 하드캡(파산 가드, 부록 v3-1)


# ─────────────────────────────────────────────────────────────────────────────
# 패널: c3c PIT 패널 재사용 + 핫 이름 티어 재분류(3bp 대형 / 15bp 핫).
# ─────────────────────────────────────────────────────────────────────────────
def build_panel() -> tuple[c2d.Panel, list]:
    recs = c3c.load_pit_records()
    panel = c3c.build_pit_panel(recs)
    # 티어 재분류: ETF(QQQ/XL*) = 1bp, c2d LARGE_CAP 화이트리스트 = 3bp, 그 외 핫 고변동 = 15bp.
    for s in list(panel.close):
        if s == BENCH or s.startswith("XL"):
            panel.tier[s] = research.TIER_ETF
        else:
            panel.tier[s] = c2d._tier_for(s)   # LARGE_CAP→3bp, else small_hot→15bp
    panel._beta_cache = {}                       # type: ignore[attr-defined]
    return panel, recs


def cost_for(panel: c2d.Panel, *, mult: float = 1.0, micro: bool = False,
             commission_bps: float = 10.0) -> CostSpec:
    """task 비용: 0.1%/side(10bp) + 티어 반호가 + 5bp 슬리피지. micro=수수료 0(≤$10 무료). mult=스트레스."""
    comm = 0.0 if micro else commission_bps
    c = CostSpec.from_tiers(panel.tier, commission_bps=comm, slippage_bps=5.0, fx_bps=20.0)
    return c.stress(mult) if mult != 1.0 else c


# ─────────────────────────────────────────────────────────────────────────────
# 베타(252d, QQQ 대비). panel.close 를 직접 읽어 look-ahead 가드에 민감. per-panel 메모.
# ─────────────────────────────────────────────────────────────────────────────
def beta_to_bench(panel: c2d.Panel, s: str, t: int, win: int = BETA_WIN) -> float | None:
    cache = getattr(panel, "_beta_cache", None)
    if cache is None:
        cache = {}
        panel._beta_cache = cache            # type: ignore[attr-defined]
    key = (s, t, win)
    if key in cache:
        return cache[key]
    cb = panel.close[BENCH]
    cs = panel.close.get(s)
    a = t - win + 1
    if cs is None or a < 1:
        cache[key] = None
        return None
    rq: list[float] = []
    rs: list[float] = []
    for i in range(a, t + 1):
        if cb[i - 1] > 0 and cs[i - 1] > 0:
            rq.append(cb[i] / cb[i - 1] - 1.0)
            rs.append(cs[i] / cs[i - 1] - 1.0)
    if len(rq) < int(win * 0.8):              # 실측 이력 부족 → 후보 제외
        cache[key] = None
        return None
    mb = sum(rq) / len(rq)
    ms = sum(rs) / len(rs)
    var = sum((x - mb) ** 2 for x in rq)
    if var <= 0:
        cache[key] = None
        return None
    cov = sum((rs[i] - ms) * (rq[i] - mb) for i in range(len(rq)))
    v = cov / var
    cache[key] = v
    return v


def clone_with_closes(panel: c2d.Panel, closes: dict[str, list]) -> c2d.Panel:
    """look-ahead 가드용: 종가만 교체한 얕은 사본(그 외 필드 공유, 베타 캐시는 새로)."""
    c = c2d.Panel(dates=panel.dates)
    c.close = {s: list(v) for s, v in closes.items()}
    for fld in ("open", "high", "low", "vol", "first_idx", "tier"):
        setattr(c, fld, getattr(panel, fld))
    c.universe = panel.universe
    for attr in ("rawclose", "last_idx", "pit_elig", "added_idx", "missing_members", "pit_records"):
        if hasattr(panel, attr):
            setattr(c, attr, getattr(panel, attr))
    c._beta_cache = {}                        # type: ignore[attr-defined]
    return c


# ─────────────────────────────────────────────────────────────────────────────
# 결정 함수(종가 t 정보만 사용, 인과적). 실행은 c2d.simulate_portfolio 가 다음 시가에.
# ─────────────────────────────────────────────────────────────────────────────
def decide_hibeta_basket(panel: c2d.Panel, *, topk: int = 10, beta_win: int = BETA_WIN,
                         dv_top: int = DV_TOP, min_price: float = MIN_PRICE,
                         use_filter: bool = False, guard: bool = False,
                         dd_win: int = 100, dd_thresh: float = 0.10) -> dict[int, dict[str, float]]:
    """월간, PIT as-of 멤버 중 베타 상위 topk 동일가중. use_filter=QQQ>SMA200 else 현금.
    guard=QQQ가 dd_win 고점 대비 dd_thresh 이상 하락 시 현금(드로다운 가드)."""
    sma_b = research.sma(panel.close[BENCH], 200)
    rmax_b = research.rolling_max(panel.close[BENCH], dd_win) if guard else None
    me = research.month_end_flags(panel.dates)
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(me):
        if not f:
            continue
        if use_filter:
            sb = sma_b[t]
            if not (sb is not None and panel.close[BENCH][t] > sb):
                out[t] = {}                    # 현금
                continue
        if guard:
            hm = rmax_b[t]                      # type: ignore[index]
            if hm and panel.close[BENCH][t] < (1.0 - dd_thresh) * hm:
                out[t] = {}                    # 급락 → 현금
                continue
        scored = []
        for s in c3c.pit_candidates(panel, t, need=beta_win, dv_top=dv_top):
            if panel.rawclose[s][t] < min_price:   # type: ignore[attr-defined]
                continue
            b = beta_to_bench(panel, s, t, beta_win)
            if b is not None:
                scored.append((b, s))
        scored.sort(reverse=True)
        out[t] = c2d._equal_weight([s for _, s in scored[:topk]])
    return out


def decide_mom_hibeta(panel: c2d.Panel, *, topk: int = 5, beta_top: int = 50,
                      beta_win: int = BETA_WIN, lookback: int = 252, skip: int = 21,
                      dv_top: int = DV_TOP, min_price: float = MIN_PRICE,
                      use_filter: bool = True) -> dict[int, dict[str, float]]:
    """베타 상위 beta_top 이름 중 12-1 모멘텀 top-k EW, 월간, QQQ>SMA200 필터(else 현금)."""
    sma_b = research.sma(panel.close[BENCH], 200)
    me = research.month_end_flags(panel.dates)
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(me):
        if not f:
            continue
        if use_filter:
            sb = sma_b[t]
            if not (sb is not None and panel.close[BENCH][t] > sb):
                out[t] = {}
                continue
        need = max(beta_win, lookback)
        betas = []
        for s in c3c.pit_candidates(panel, t, need=need, dv_top=dv_top):
            if panel.rawclose[s][t] < min_price:   # type: ignore[attr-defined]
                continue
            b = beta_to_bench(panel, s, t, beta_win)
            if b is not None:
                betas.append((b, s))
        betas.sort(reverse=True)
        top_names = [s for _, s in betas[:beta_top]]
        mom = []
        for s in top_names:
            c0 = panel.close[s][t - lookback]
            c1 = panel.close[s][t - skip]
            if c0 > 0:
                mom.append((c1 / c0 - 1.0, s))
        mom.sort(reverse=True)
        out[t] = c2d._equal_weight([s for _, s in mom[:topk]])
    return out


def make_signal_fn(panel: c2d.Panel, decide_fn, **params):
    """look-ahead 가드용: (perturbed closes, dates) → 일별 목표비중. 종가 교체 사본에서 재계산."""
    def fn(panel_closes, dates):
        clone = clone_with_closes(panel, dict(panel_closes))
        dec = decide_fn(clone, **params)
        return c2d.decisions_to_daily(dec, len(dates))
    return fn


# ─────────────────────────────────────────────────────────────────────────────
# 지표 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _terminal(returns: list[float]) -> float:
    eq = 1.0
    for r in returns:
        eq *= (1.0 + r)
    return eq


def realized_beta(strat: list[float], bench: list[float]) -> float:
    n = min(len(strat), len(bench))
    if n < 2:
        return float("nan")
    a, b = strat[:n], bench[:n]
    mb = sum(b) / n
    var = sum((x - mb) ** 2 for x in b)
    if var <= 0:
        return float("nan")
    ma = sum(a) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / var


def _cagr(returns: list[float], dates: list[date]) -> float:
    if not returns or len(dates) < 2:
        return float("nan")
    return gate.cagr(gate.returns_to_equity(returns), max((dates[-1] - dates[0]).days, 1))


def _split_idx(ret_dates: list[date]) -> tuple[list[int], list[int]]:
    di = [i for i, d in enumerate(ret_dates) if d <= DESIGN_END]
    hi = [i for i, d in enumerate(ret_dates) if d > DESIGN_END]
    return di, hi


def lane_a_verdict(*, a_pass: bool, b_pass: bool, c_pass: bool, bankruptcy: bool) -> tuple[str, list[str]]:
    """부록 v3 Lane A: (a) 설계·홀드아웃 모두 CAGR>QQQ, (b) 이웃 60%↑ CAGR>QQQ, (c) 2x비용에서도 (a).
    파산 가드(전표본 MDD≤−95%)면 FAIL. DSR/SPA 는 보고만."""
    reasons = []
    if bankruptcy:
        return "FAIL", ["파산 가드: 전표본 MDD ≤ −95% (복구 불가) → FAIL"]
    if not a_pass:
        reasons.append("(a) 설계·홀드아웃 CAGR>QQQ 미충족 → FAIL")
        return "FAIL", reasons
    if a_pass and b_pass and c_pass:
        return "PASS", ["(a)(b)(c) 모두 충족 → Lane A PASS(페이퍼 편입 후보)"]
    if not b_pass:
        reasons.append("(b) 이웃 60% CAGR>QQQ 미달")
    if not c_pass:
        reasons.append("(c) 2×비용에서 (a) 붕괴")
    reasons.append("(a)만 충족 → CONDITIONAL")
    return "CONDITIONAL", reasons


# ─────────────────────────────────────────────────────────────────────────────
# 포트폴리오 아이디어 평가(Lane A). 아이디어 1·2·3·6.
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_lane_a(panel: c2d.Panel, idea_id: str, decide_fn, base_params: dict,
                    neighbors: list[dict], bench_ret: list[float], cash_rate, *,
                    qqq_cagr_des: float, qqq_cagr_hol: float, qqq_cagr_full: float,
                    log: bool = True, rc_B: int = 1500) -> dict:
    cost = cost_for(panel)
    zcost = CostSpec(commission_bps=0.0, slippage_bps=0.0, fx_bps=0.0, default_half_spread_bps=0.0)
    dates = panel.dates
    ret_dates = dates[1:]
    di, hi = _split_idx(ret_dates)
    bench_stream = bench_ret[1:]

    def sim_returns(params, c):
        dec = decide_fn(panel, **params)
        sim = c2d.simulate_portfolio(panel, dec, c, cash_rate=cash_rate)
        return sim, sim.returns[1:]

    sim_c, cand = sim_returns(base_params, cost)
    _, gross = sim_returns(base_params, zcost)
    _, micro = sim_returns(base_params, cost_for(panel, micro=True))

    d_des = [ret_dates[i] for i in di]
    d_hol = [ret_dates[i] for i in hi]
    r_des = [cand[i] for i in di]
    r_hol = [cand[i] for i in hi]
    b_des = [bench_stream[i] for i in di]
    b_hol = [bench_stream[i] for i in hi]

    cagr_des = _cagr(r_des, d_des)
    cagr_hol = _cagr(r_hol, d_hol)
    cagr_full = _cagr(cand, ret_dates)
    cagr_micro_hol = _cagr([micro[i] for i in hi], d_hol)
    mdd_full = gate.max_drawdown(gate.returns_to_equity(cand))
    mdd_des = gate.max_drawdown(gate.returns_to_equity(r_des))
    mdd_hol = gate.max_drawdown(gate.returns_to_equity(r_hol))
    rbeta_des = realized_beta(r_des, b_des)
    rbeta_hol = realized_beta(r_hol, b_hol)

    # 이웃(평탄성 + Lane A (b) + DSR용 N). 전 구간 CAGR>QQQ 비율.
    streams_des: dict[str, list[float]] = {}
    neigh_full: dict[str, list[float]] = {}
    sr_trials: list[float] = []
    frac_full = frac_des = frac_hol = 0
    n_neigh = len(neighbors)
    for j, p in enumerate(neighbors):
        _, nret = sim_returns(p, cost)
        neigh_full[f"n{j}"] = nret
        nd = [nret[i] for i in di]
        streams_des[f"n{j}"] = nd
        sr_trials.append(c2d._sr_daily(nd))
        if _cagr(nret, ret_dates) > qqq_cagr_full:
            frac_full += 1
        if _cagr(nd, d_des) > qqq_cagr_des:
            frac_des += 1
        if _cagr([nret[i] for i in hi], d_hol) > qqq_cagr_hol:
            frac_hol += 1
    streams_des["center"] = r_des
    sr_trials.append(c2d._sr_daily(r_des))
    n_eff = gate.n_eff_clusters(streams_des)
    frac_full_r = frac_full / n_neigh if n_neigh else 0.0
    frac_des_r = frac_des / n_neigh if n_neigh else 0.0
    frac_hol_r = frac_hol / n_neigh if n_neigh else 0.0

    # 비용 2× 스트레스: 설계·홀드아웃 모두 CAGR>QQQ 유지?
    _, cand2 = sim_returns(base_params, cost_for(panel, mult=2.0))
    cagr_des_2x = _cagr([cand2[i] for i in di], d_des)
    cagr_hol_2x = _cagr([cand2[i] for i in hi], d_hol)

    # DSR / RC / SPA (부록 v3: 보고만)
    um_hol = gate_eval.unit_capital_metrics(r_hol, dates=d_hol, sr_trials=sr_trials, n_eff=n_eff)
    fam_hol = {name: [nf[i] - bench_stream[i] for i in hi] for name, nf in neigh_full.items()}
    rc_hol = gate_eval.reality_check([r_hol[k] - b_hol[k] for k in range(len(r_hol))],
                                     family_excess=fam_hol, B=rc_B)

    # Lane A 판정
    a_pass = (cagr_des > qqq_cagr_des) and (cagr_hol > qqq_cagr_hol)
    b_pass = frac_full_r >= 0.60
    c_pass = (cagr_des_2x > qqq_cagr_des) and (cagr_hol_2x > qqq_cagr_hol)
    bankruptcy = mdd_full <= BANKRUPTCY_MDD
    verdict, reasons = lane_a_verdict(a_pass=a_pass, b_pass=b_pass, c_pass=c_pass, bankruptcy=bankruptcy)

    if log:
        cfg = dict(base_params); cfg["idea"] = idea_id
        um_des = gate_eval.unit_capital_metrics(r_des, dates=d_des, sr_trials=sr_trials, n_eff=n_eff)
        gate_eval.log_evaluation(idea_id, cfg, LANE, "design", um_des,
                                 gate_eval.money_weighted_metrics(benchmark_terminal=_terminal(b_des),
                                                                  dca_equity=gate.returns_to_equity(r_des)),
                                 window=(d_des[0], d_des[-1]) if d_des else None,
                                 universe=panel.universe, ledger_path=LEDGER)
        for j, p in enumerate(neighbors):
            npar = dict(p); npar["idea"] = idea_id; npar["neighbor"] = j
            um_n = gate_eval.unit_capital_metrics(streams_des[f"n{j}"], dates=d_des)
            gate_eval.log_evaluation(idea_id, npar, LANE, "design", um_n, {},
                                     window=(d_des[0], d_des[-1]) if d_des else None,
                                     ledger_path=LEDGER)
        if hi:
            try:
                gate_eval.log_evaluation(idea_id, cfg, LANE, "holdout", um_hol,
                                         gate_eval.money_weighted_metrics(
                                             benchmark_terminal=_terminal(b_hol),
                                             dca_equity=gate.returns_to_equity(r_hol)),
                                         window=(d_hol[0], d_hol[-1]), universe=panel.universe,
                                         ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    return {
        "idea": idea_id, "params": base_params, "kind": "portfolio",
        "design": {"cagr": cagr_des, "mdd": mdd_des, "sharpe": gate.sharpe(r_des),
                   "realized_beta": rbeta_des, "cagr_vs_qqq": cagr_des - qqq_cagr_des},
        "holdout": {"cagr": cagr_hol, "mdd": mdd_hol, "sharpe": gate.sharpe(r_hol),
                    "realized_beta": rbeta_hol, "cagr_vs_qqq": cagr_hol - qqq_cagr_hol,
                    "cagr_2x": cagr_hol_2x, "cagr_micro": cagr_micro_hol,
                    "dsr": um_hol.get("dsr"), "rc_p": rc_hol["rc_pvalue"], "spa_p": rc_hol["spa_pvalue"]},
        "full": {"cagr": cagr_full, "mdd": mdd_full},
        "turnover_yr": sim_c.turnover_per_year(),
        "neighbor_frac_full": frac_full_r, "neighbor_frac_design": frac_des_r,
        "neighbor_frac_holdout": frac_hol_r, "n_neighbors": n_neigh,
        "n_eff": n_eff,
        "lane_a": {"a_pass": a_pass, "b_pass": b_pass, "c_pass": c_pass, "bankruptcy": bankruptcy},
        "verdict": verdict, "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 아이디어 4: Episodic Pivot(F5). PIT 유니버스, 동시 최대 3포지션. 거래단위 통계 + 포트 자본곡선.
# ─────────────────────────────────────────────────────────────────────────────
def ep_gap_scan(panel: c2d.Panel, *, gap: float = 0.10, rvol_k: float = 3.0, rng_min: float = 0.5,
                avg_win: int = 50, sma_exit: int = 10, max_hold: int = 60,
                min_price: float = MIN_PRICE) -> list[tuple[int, str, int]]:
    """신호 스캔 → [(신호일 t, 심볼, 청산트리거 인덱스)]. 갭=시가/전일종가−1, 다음시가 진입,
    종가<SMA(sma_exit) 첫날 트리거(다음시가 청산) 또는 max_hold. PIT as-of & live & 가격>min_price."""
    n = len(panel.dates)
    sigs: list[tuple[int, str, int]] = []
    for s in panel.universe:
        cl, op, hi, lo, vo = (panel.close[s], panel.open[s], panel.high[s],
                              panel.low[s], panel.vol[s])
        rc = panel.rawclose[s]                 # type: ignore[attr-defined]
        smae = research.sma(cl, sma_exit)
        for t in range(1, n - 1):
            if not c3c._pit_live(panel, s, t, avg_win):
                continue
            if s not in panel.pit_elig[t]:      # type: ignore[attr-defined]  as-of 멤버
                continue
            if rc[t] < min_price or cl[t - 1] <= 0 or op[t] <= 0:
                continue
            gap_t = op[t] / cl[t - 1] - 1.0
            avgv = sum(vo[t - avg_win:t]) / avg_win if t >= avg_win else 0.0
            rvol = vo[t] / avgv if avgv > 0 else 0.0
            rng = (cl[t] - lo[t]) / (hi[t] - lo[t]) if hi[t] > lo[t] else 0.0
            if gap_t > gap and rvol > rvol_k and rng >= rng_min:
                e_i = t + 1
                if op[e_i] <= 0:
                    continue
                x_trig = None
                for u in range(e_i, min(e_i + max_hold, n - 1)):
                    if smae[u] is not None and cl[u] < smae[u]:
                        x_trig = u
                        break
                if x_trig is None:
                    x_trig = min(e_i + max_hold, n - 1)
                sigs.append((t, s, x_trig))
    sigs.sort(key=lambda x: (x[0], x[1]))
    return sigs


def ep_build(panel: c2d.Panel, *, max_pos: int = 3, **scan_kw):
    """신호를 슬롯(동시 max_pos)으로 배정 → (일별 목표비중 decisions, 실제 체결 trade 리스트)."""
    sigs = ep_gap_scan(panel, **scan_kw)
    by_day: dict[int, list[tuple[str, int]]] = {}
    for t, s, xt in sigs:
        by_day.setdefault(t, []).append((s, xt))
    n = len(panel.dates)
    active: dict[str, int] = {}                # symbol -> 청산트리거 인덱스(종가일)
    decisions: dict[int, dict[str, float]] = {}
    taken: list[research.Trade] = []
    entry_x: dict[str, int] = {}
    for t in range(n):
        changed = False
        for s, xt in list(active.items()):     # 청산 트리거(종가 t) → 다음시가 청산
            if xt == t:
                del active[s]
                changed = True
                e_i = entry_x.pop(s + "_e", None)
                x_i = t + 1 if t + 1 < n else t
                if e_i is not None and panel.open[s][e_i] > 0 and panel.open[s][x_i] > 0:
                    taken.append(research.Trade(entry_dt=panel.dates[e_i], entry_price=panel.open[s][e_i],
                                                exit_dt=panel.dates[x_i], exit_price=panel.open[s][x_i],
                                                notional=1.0, symbol=s))
        for s, xt in by_day.get(t, []):        # 신규 신호(종가 t) → 다음시가 진입(슬롯 여유 시)
            if len(active) < max_pos and s not in active:
                active[s] = xt
                entry_x[s + "_e"] = t + 1
                changed = True
        if changed:
            decisions[t] = c2d._equal_weight(list(active.keys()))
    # 잔여 포지션(창 끝) 청산
    for s, e_i in list(entry_x.items()):
        sym = s[:-2]
        x_i = n - 1
        if panel.open[sym][e_i] > 0 and panel.open[sym][x_i] > 0:
            taken.append(research.Trade(entry_dt=panel.dates[e_i], entry_price=panel.open[sym][e_i],
                                        exit_dt=panel.dates[x_i], exit_price=panel.open[sym][x_i],
                                        notional=1.0, symbol=sym))
    taken.sort(key=lambda tr: tr.entry_dt)
    return decisions, taken, len(sigs)


def evaluate_ep(panel: c2d.Panel, idea_id: str, base_params: dict, cash_rate, *,
                qqq_cagr_des: float, qqq_cagr_hol: float, bench_ret: list[float],
                log: bool = True) -> dict:
    decisions, taken, n_sig = ep_build(panel, **base_params)
    cost = cost_for(panel)
    sim = c2d.simulate_portfolio(panel, decisions, cost, cash_rate=cash_rate)
    cand = sim.returns[1:]
    ret_dates = panel.dates[1:]
    di, hi = _split_idx(ret_dates)
    bench_stream = bench_ret[1:]
    d_des = [ret_dates[i] for i in di]; d_hol = [ret_dates[i] for i in hi]
    r_des = [cand[i] for i in di]; r_hol = [cand[i] for i in hi]
    b_des = [bench_stream[i] for i in di]; b_hol = [bench_stream[i] for i in hi]

    # 거래단위 통계(진입일로 분할)
    def tstats(pnls):
        if len(pnls) < 2:
            return {"n": len(pnls)}
        _, lo, _ = gate.trade_pnl_bootstrap_ci(pnls, B=1500)
        return {"n": len(pnls), "tstat": gate.trade_tstat(pnls), "ci_lo": lo,
                "profit_factor": gate.profit_factor(pnls), "expectancy": gate.expectancy(pnls),
                "win_rate": sum(1 for p in pnls if p > 0) / len(pnls),
                "mean_ret_bps": gate._mean(pnls) * 1e4}
    tr = research.run_trades(taken, cost=cost, capital=1.0)
    tr2 = research.run_trades(taken, cost=cost_for(panel, mult=2.0), capital=1.0)
    des_p = [p for p, t in zip(tr.pnls, taken) if research._as_date(t.entry_dt) <= DESIGN_END]
    hol_p = [p for p, t in zip(tr.pnls, taken) if research._as_date(t.entry_dt) > DESIGN_END]
    hol_p2 = [p for p, t in zip(tr2.pnls, taken) if research._as_date(t.entry_dt) > DESIGN_END]

    cagr_des = _cagr(r_des, d_des); cagr_hol = _cagr(r_hol, d_hol)
    mdd_full = gate.max_drawdown(gate.returns_to_equity(cand))
    st_hol = tstats(hol_p)
    # 이벤트/재량 기원 → 백테스트 판정은 최대 CONDITIONAL(포워드 전용, 카탈로그 F5·부록 v3-4).
    a_ok = (cagr_des > qqq_cagr_des) and (cagr_hol > qqq_cagr_hol)
    t_ok = st_hol.get("tstat", 0) is not None and st_hol.get("tstat", 0) >= 3.0
    if mdd_full <= BANKRUPTCY_MDD:
        verdict, reasons = "FAIL", ["파산 가드 위반"]
    elif a_ok and t_ok:
        verdict = "CONDITIONAL"
        reasons = ["EP는 이벤트·재량 기원 → 백테스트 최대 CONDITIONAL(포워드 페이퍼 전용)"]
    else:
        verdict = "FAIL"
        reasons = [f"홀드 CAGR>QQQ={a_ok}, 거래 t≥3={t_ok} 미충족"]

    if log:
        cfg = dict(base_params); cfg["idea"] = idea_id
        um = {"sr_daily": c2d._sr_daily(cand), "T": len(cand),
              "max_drawdown": mdd_full, "sr_annual": gate.sharpe(cand)}
        gate_eval.log_evaluation(idea_id, cfg, LANE, "design",
                                 {"sr_daily": c2d._sr_daily(r_des), "T": len(r_des),
                                  "max_drawdown": gate.max_drawdown(gate.returns_to_equity(r_des)),
                                  "sr_annual": gate.sharpe(r_des)}, {},
                                 window=(d_des[0], d_des[-1]) if d_des else None,
                                 universe=panel.universe, ledger_path=LEDGER)
        if hi:
            try:
                gate_eval.log_evaluation(idea_id, cfg, LANE, "holdout",
                                         {"sr_daily": c2d._sr_daily(r_hol), "T": len(r_hol),
                                          "max_drawdown": gate.max_drawdown(gate.returns_to_equity(r_hol)),
                                          "sr_annual": gate.sharpe(r_hol)}, {},
                                         window=(d_hol[0], d_hol[-1]), universe=panel.universe,
                                         ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    return {
        "idea": idea_id, "params": base_params, "kind": "trades",
        "n_signals": n_sig, "n_taken": len(taken),
        "design": {"cagr": cagr_des, "mdd": gate.max_drawdown(gate.returns_to_equity(r_des)),
                   "sharpe": gate.sharpe(r_des), "realized_beta": realized_beta(r_des, b_des),
                   "cagr_vs_qqq": cagr_des - qqq_cagr_des, "trades": tstats(des_p)},
        "holdout": {"cagr": cagr_hol, "mdd": gate.max_drawdown(gate.returns_to_equity(r_hol)),
                    "sharpe": gate.sharpe(r_hol), "realized_beta": realized_beta(r_hol, b_hol),
                    "cagr_vs_qqq": cagr_hol - qqq_cagr_hol, "trades": st_hol,
                    "trades_2xcost": tstats(hol_p2)},
        "full": {"cagr": _cagr(cand, ret_dates), "mdd": mdd_full},
        "turnover_yr": sim.turnover_per_year(),
        "verdict": verdict, "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 아이디어 5: BTC 추세(CBBTCUSD>SMA100) → MSTR/COIN(1x, 허용) else 현금. 별도 패널.
# ─────────────────────────────────────────────────────────────────────────────
def build_btc_panel() -> c2d.Panel:
    p = c2d.load_panel([BENCH, "MSTR", "COIN"], calendar_symbol=BENCH, start=START, end=END)
    p.universe = [s for s in ("MSTR", "COIN") if s in p.close]
    return p


def btc_close_on_dates(dates: list[date]) -> list[float]:
    """CBBTCUSD(FRED)를 패널 거래일에 전방채움 정렬(인과적 — t 이하 관측만)."""
    obs = histdata.load_fred("CBBTCUSD", start=date(2014, 1, 1), end=END)
    by = {c.dt: c.close for c in obs if c.close and c.close > 0}
    out = [0.0] * len(dates)
    last = 0.0
    for i, d in enumerate(dates):
        v = by.get(d)
        if v is not None and v > 0:
            last = v
        out[i] = last
    return out


def decide_btc_proxy(panel: c2d.Panel, btc_close: list[float], *, sma_win: int = 100,
                     names: tuple[str, ...] = ("MSTR", "COIN")) -> dict[int, dict[str, float]]:
    """BTC>SMA(sma_win) 이면 live 한 names 동일가중, 아니면 현금. 목표 변경 시에만 결정(회전 절감)."""
    sma_b = research.sma(btc_close, sma_win)
    out: dict[int, dict[str, float]] = {}
    last: dict[str, float] | None = None
    live_names = [s for s in names if s in panel.close]
    for t in range(len(panel.dates)):
        sb = sma_b[t]
        on = sb is not None and btc_close[t] > 0 and btc_close[t] > sb
        if on:
            avail = [s for s in live_names if c2d._eligible(panel, s, t, 1)]
            tgt = c2d._equal_weight(avail)
        else:
            tgt = {}
        if last is None or tgt != last:
            out[t] = tgt
            last = tgt
    return out


def evaluate_btc(panel: c2d.Panel, idea_id: str, btc_close: list[float], base_params: dict,
                 neighbors: list[dict], cash_rate, *, qqq_cagr_des: float, qqq_cagr_hol: float,
                 qqq_cagr_full: float, bench_ret: list[float], log: bool = True,
                 rc_B: int = 1500) -> dict:
    cost = cost_for(panel)
    dates = panel.dates
    ret_dates = dates[1:]
    di, hi = _split_idx(ret_dates)
    bench_stream = bench_ret[1:]

    def sim_returns(params, c):
        dec = decide_btc_proxy(panel, btc_close, **params)
        sim = c2d.simulate_portfolio(panel, dec, c, cash_rate=cash_rate)
        return sim, sim.returns[1:]

    sim_c, cand = sim_returns(base_params, cost)
    d_des = [ret_dates[i] for i in di]; d_hol = [ret_dates[i] for i in hi]
    r_des = [cand[i] for i in di]; r_hol = [cand[i] for i in hi]
    b_des = [bench_stream[i] for i in di]; b_hol = [bench_stream[i] for i in hi]
    _, micro = sim_returns(base_params, cost_for(panel, micro=True))
    _, cand2 = sim_returns(base_params, cost_for(panel, mult=2.0))

    cagr_des = _cagr(r_des, d_des); cagr_hol = _cagr(r_hol, d_hol)
    cagr_full = _cagr(cand, ret_dates)
    mdd_full = gate.max_drawdown(gate.returns_to_equity(cand))

    sr_trials = []; frac_full = 0
    neigh_full = {}
    for j, p in enumerate(neighbors):
        _, nret = sim_returns(p, cost)
        neigh_full[f"n{j}"] = nret
        sr_trials.append(c2d._sr_daily([nret[i] for i in di]))
        if _cagr(nret, ret_dates) > qqq_cagr_full:
            frac_full += 1
    sr_trials.append(c2d._sr_daily(r_des))
    frac_full_r = frac_full / len(neighbors) if neighbors else 0.0
    n_eff = gate.n_eff_clusters({**{k: [v[i] for i in di] for k, v in neigh_full.items()},
                                 "center": r_des})
    um_hol = gate_eval.unit_capital_metrics(r_hol, dates=d_hol, sr_trials=sr_trials, n_eff=n_eff)
    fam_hol = {k: [v[i] - bench_stream[i] for i in hi] for k, v in neigh_full.items()}
    rc_hol = gate_eval.reality_check([r_hol[k] - b_hol[k] for k in range(len(r_hol))],
                                     family_excess=fam_hol, B=rc_B)

    cagr_des_2x = _cagr([cand2[i] for i in di], d_des)
    cagr_hol_2x = _cagr([cand2[i] for i in hi], d_hol)
    a_pass = (cagr_des > qqq_cagr_des) and (cagr_hol > qqq_cagr_hol)
    b_pass = frac_full_r >= 0.60
    c_pass = (cagr_des_2x > qqq_cagr_des) and (cagr_hol_2x > qqq_cagr_hol)
    verdict, reasons = lane_a_verdict(a_pass=a_pass, b_pass=b_pass, c_pass=c_pass,
                                      bankruptcy=mdd_full <= BANKRUPTCY_MDD)

    if log:
        cfg = dict(base_params); cfg["idea"] = idea_id
        um_des = gate_eval.unit_capital_metrics(r_des, dates=d_des, sr_trials=sr_trials, n_eff=n_eff)
        gate_eval.log_evaluation(idea_id, cfg, LANE, "design", um_des,
                                 gate_eval.money_weighted_metrics(benchmark_terminal=_terminal(b_des),
                                                                  dca_equity=gate.returns_to_equity(r_des)),
                                 window=(d_des[0], d_des[-1]) if d_des else None,
                                 universe=panel.universe, ledger_path=LEDGER)
        for j, p in enumerate(neighbors):
            npar = dict(p); npar["idea"] = idea_id; npar["neighbor"] = j
            um_n = gate_eval.unit_capital_metrics([neigh_full[f"n{j}"][i] for i in di], dates=d_des)
            gate_eval.log_evaluation(idea_id, npar, LANE, "design", um_n, {},
                                     window=(d_des[0], d_des[-1]) if d_des else None,
                                     ledger_path=LEDGER)
        if hi:
            try:
                gate_eval.log_evaluation(idea_id, cfg, LANE, "holdout", um_hol,
                                         gate_eval.money_weighted_metrics(
                                             benchmark_terminal=_terminal(b_hol),
                                             dca_equity=gate.returns_to_equity(r_hol)),
                                         window=(d_hol[0], d_hol[-1]), universe=panel.universe,
                                         ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    return {
        "idea": idea_id, "params": base_params, "kind": "portfolio",
        "design": {"cagr": cagr_des, "mdd": gate.max_drawdown(gate.returns_to_equity(r_des)),
                   "sharpe": gate.sharpe(r_des), "realized_beta": realized_beta(r_des, b_des),
                   "cagr_vs_qqq": cagr_des - qqq_cagr_des},
        "holdout": {"cagr": cagr_hol, "mdd": gate.max_drawdown(gate.returns_to_equity(r_hol)),
                    "sharpe": gate.sharpe(r_hol), "realized_beta": realized_beta(r_hol, b_hol),
                    "cagr_vs_qqq": cagr_hol - qqq_cagr_hol, "cagr_2x": cagr_hol_2x,
                    "cagr_micro": _cagr([micro[i] for i in hi], d_hol),
                    "dsr": um_hol.get("dsr"), "rc_p": rc_hol["rc_pvalue"], "spa_p": rc_hol["spa_pvalue"]},
        "full": {"cagr": cagr_full, "mdd": mdd_full},
        "turnover_yr": sim_c.turnover_per_year(),
        "neighbor_frac_full": frac_full_r, "n_neighbors": len(neighbors), "n_eff": n_eff,
        "lane_a": {"a_pass": a_pass, "b_pass": b_pass, "c_pass": c_pass},
        "coin_start": next((panel.dates[panel.first_idx["COIN"]].isoformat()
                            for _ in [0] if "COIN" in panel.first_idx), None),
        "verdict": verdict, "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 벤치마크 B&H: QQQ / QLD(2x) / TQQQ(3x). 설계/홀드아웃/전구간.
# ─────────────────────────────────────────────────────────────────────────────
def benchmark_bh() -> dict:
    bp = c2d.load_panel([BENCH, "QLD", "TQQQ"], calendar_symbol=BENCH, start=START, end=END)
    ret_dates = bp.dates[1:]
    di, hi = _split_idx(ret_dates)
    qqq = research.to_returns(bp.close[BENCH])[1:]
    out = {}
    for sym in (BENCH, "QLD", "TQQQ"):
        if sym not in bp.close:
            continue
        r = research.to_returns(bp.close[sym])[1:]
        d_des = [ret_dates[i] for i in di]; d_hol = [ret_dates[i] for i in hi]
        seg = {}
        for name, idxs, dd in (("design", di, d_des), ("holdout", hi, d_hol), ("full", range(len(r)), ret_dates)):
            rr = [r[i] for i in idxs]
            bb = [qqq[i] for i in idxs]
            seg[name] = {"cagr": _cagr(rr, list(dd)),
                         "mdd": gate.max_drawdown(gate.returns_to_equity(rr)),
                         "sharpe": gate.sharpe(rr), "realized_beta": realized_beta(rr, bb)}
        out[sym] = seg
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 사전등록 config + 이웃(±20~50%)
# ─────────────────────────────────────────────────────────────────────────────
def _basket_neighbors(base: dict) -> list[dict]:
    out = []
    for k in (5, 8, 12, 15):
        if k != base["topk"]:
            p = dict(base); p["topk"] = k; out.append(p)
    for g in (0.7, 1.3):
        p = dict(base); p["beta_win"] = int(round(base["beta_win"] * g)); out.append(p)
    return out


def _mom_neighbors(base: dict) -> list[dict]:
    out = []
    for k in (3, 7):
        p = dict(base); p["topk"] = k; out.append(p)
    for bt in (30, 70):
        p = dict(base); p["beta_top"] = bt; out.append(p)
    for g in (0.8, 1.2):
        p = dict(base); p["lookback"] = int(round(base["lookback"] * g)); out.append(p)
    return out


def _ddguard_neighbors(base: dict) -> list[dict]:
    out = []
    for k in (8, 12):
        p = dict(base); p["topk"] = k; out.append(p)
    for th in (0.07, 0.15):
        p = dict(base); p["dd_thresh"] = th; out.append(p)
    for w in (50, 150):
        p = dict(base); p["dd_win"] = w; out.append(p)
    return out


def _btc_neighbors(base: dict) -> list[dict]:
    return [{**base, "sma_win": w} for w in (55, 75, 150, 200)]


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="c12s 고베타 단일주식 = '레버리지 ETF 없는 레버리지'(Lane A)")
    ap.add_argument("--no-ledger", action="store_true", help="원장 적재 생략(개발/디버그)")
    ap.add_argument("--out", type=str, default="", help="결과 JSON 저장 경로")
    ap.add_argument("--quick", action="store_true", help="RC 부트스트랩 축소(빠른 점검)")
    ap.add_argument("--only", type=str, default="", help="일부 아이디어만(쉼표구분 id)")
    args = ap.parse_args()
    log = not args.no_ledger
    rc_B = 400 if args.quick else 1500
    only = set(s.strip() for s in args.only.split(",") if s.strip())

    panel, recs = build_panel()
    cr = c2d.cash_rate_series(panel)
    br = c2d.bench_returns(panel)
    ret_dates = panel.dates[1:]
    di, hi = _split_idx(ret_dates)
    qqq_stream = br[1:]
    qqq_des = _cagr([qqq_stream[i] for i in di], [ret_dates[i] for i in di])
    qqq_hol = _cagr([qqq_stream[i] for i in hi], [ret_dates[i] for i in hi])
    qqq_full = _cagr(qqq_stream, ret_dates)
    cov = c3c.pit_coverage(panel)
    print(f"PIT 유니버스(가용) {len(panel.universe)} | 거래일 {len(panel.dates)} "
          f"{panel.dates[0]}~{panel.dates[-1]} | 커버리지 설계 {cov['design_mean']:.1%} 홀드 {cov['holdout_mean']:.1%}",
          flush=True)
    print(f"QQQ B&H CAGR 설계 {qqq_des:.2%} 홀드 {qqq_hol:.2%} 전구간 {qqq_full:.2%}", flush=True)

    results: dict = {
        "meta": {"universe_n": len(panel.universe), "n_days": len(panel.dates),
                 "dates": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
                 "coverage": cov, "qqq_cagr": {"design": qqq_des, "holdout": qqq_hol, "full": qqq_full},
                 "cost": "10bp/side + 3bp(large)/15bp(hot) half-spread + 5bp slip; next-open; 2x stress; micro=free",
                 "lane": LANE, "upper_bound_caveat": "as-of 멤버 커버리지 ~30% (대형·생존주 편중) → 성과는 상한"},
        "benchmarks": benchmark_bh(),
        "ideas": {},
    }

    basket_base = {"topk": 10, "beta_win": 252, "dv_top": DV_TOP, "min_price": MIN_PRICE, "use_filter": False}
    trend_base = {**basket_base, "use_filter": True}
    mom_base = {"topk": 5, "beta_top": 50, "beta_win": 252, "lookback": 252, "skip": 21,
                "dv_top": DV_TOP, "min_price": MIN_PRICE, "use_filter": True}
    ddg_base = {"topk": 10, "beta_win": 252, "dv_top": DV_TOP, "min_price": MIN_PRICE,
                "use_filter": False, "guard": True, "dd_win": 100, "dd_thresh": 0.10}
    ep_base = {"gap": 0.10, "rvol_k": 3.0, "rng_min": 0.5, "avg_win": 50, "sma_exit": 10,
               "max_hold": 60, "min_price": MIN_PRICE, "max_pos": 3}

    plan = [
        ("c12s_hibeta_basket", "port", decide_hibeta_basket, basket_base, _basket_neighbors(basket_base)),
        ("c12s_hibeta_trend", "port", decide_hibeta_basket, trend_base, _basket_neighbors(trend_base)),
        ("c12s_mom_hibeta", "port", decide_mom_hibeta, mom_base, _mom_neighbors(mom_base)),
        ("c12s_ep_gap", "ep", None, ep_base, []),
        ("c12s_btc_proxy", "btc", None, {"sma_win": 100}, None),
        ("c12s_hibeta_ddguard", "port", decide_hibeta_basket, ddg_base, _ddguard_neighbors(ddg_base)),
    ]

    for idea_id, kind, fn, base, neigh in plan:
        if only and idea_id not in only:
            continue
        print(f"\n=== {idea_id} {base} ===", flush=True)
        if kind == "port":
            res = evaluate_lane_a(panel, idea_id, fn, base, neigh, br, cr,
                                  qqq_cagr_des=qqq_des, qqq_cagr_hol=qqq_hol, qqq_cagr_full=qqq_full,
                                  log=log, rc_B=rc_B)
        elif kind == "ep":
            res = evaluate_ep(panel, idea_id, base, cr, qqq_cagr_des=qqq_des, qqq_cagr_hol=qqq_hol,
                              bench_ret=br, log=log)
        else:  # btc
            bpanel = build_btc_panel()
            bcr = c2d.cash_rate_series(bpanel)
            bbr = c2d.bench_returns(bpanel)
            bclose = btc_close_on_dates(bpanel.dates)
            bret_dates = bpanel.dates[1:]
            bdi, bhi = _split_idx(bret_dates)
            bqs = bbr[1:]
            res = evaluate_btc(bpanel, idea_id, bclose, base, _btc_neighbors(base), bcr,
                               qqq_cagr_des=_cagr([bqs[i] for i in bdi], [bret_dates[i] for i in bdi]),
                               qqq_cagr_hol=_cagr([bqs[i] for i in bhi], [bret_dates[i] for i in bhi]),
                               qqq_cagr_full=_cagr(bqs, bret_dates), bench_ret=bbr, log=log, rc_B=rc_B)
        results["ideas"][idea_id] = res
        h = res["holdout"]
        extra = ""
        if res["kind"] == "trades":
            extra = f"n_taken={res['n_taken']} t={h['trades'].get('tstat')}"
        print(f"  홀드 CAGR {h['cagr']:.2%} MDD {h['mdd']:.1%} Sharpe {h['sharpe']:.2f} "
              f"β {h.get('realized_beta', float('nan')):.2f} turn {res['turnover_yr']:.1f}/yr "
              f"→ {res['verdict']} {extra}", flush=True)

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\n결과 저장: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
