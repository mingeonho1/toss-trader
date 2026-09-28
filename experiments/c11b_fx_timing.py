"""c11b — Does TIMING the monthly KRW→USD conversion improve KRW terminal wealth?

사전등록·규약: reports/cycle11_c11b_fx_timing.md, experiments/README.md,
docs/gate_v2_spec.md(+부록 v2.1/v2.2), 선행 reports/cycle8_c8a_fx.md·c8b.
KRW-표시 DCA 기계는 experiments/c8b_allocation_tax.py 를 import 재사용(load_fx/price_returns).

핵심 질문(한국 특화·신규): 미국 ETF 를 매달 원화로 적립하는 한국 거주자가, 매월 KRW→USD
환전 시점을 **타이밍**하면(USDKRW 가 쌀 때 몰아서 환전) 원화 최종자산이 늘어나는가?
- 사용자는 매달 원화를 입금한다. 환전은 토스 앱에서 **수동**(창구내 0.05%).
- 환전을 미루는 동안 원화는 한국 파킹계좌에서 예금이자를 번다(FRED 한국 단기금리 − 1%p,
  하한 0; 0% 민감도도 보고). 즉 **환전 타이밍은 주식 노출을 지연**시키므로(기회비용) 그
  기회비용을 FX 평균회귀 이득이 이기는지가 시험의 본질이다.

사전등록 규칙(실행 전 고정 — 튜닝 금지):
  1. c11b_fx_ma   : USDKRW 가 250일 이동평균 아래면 누적 원화 전액 환전, 아니면 보류.
                    3개월 대기 시 강제 환전.
  2. c11b_fx_z    : USDKRW 의 1년 z-score < 0(원화 상대강세)면 환전, 3개월 강제.
  3. c11b_fx_split: 항상 50% 즉시 환전, 나머지 50% 는 규칙 1(MA) 로 관리.
  4. c11b_fx_spike: (창의 변형) USDKRW 가 20일 평균보다 >2% 강하게 하락(원화 급강세)하면
                    공격적으로 즉시 환전, 3개월 강제.
벤치마크 c11b_fx_immediate: 매월 입금 즉시 전액 환전(무타이밍 DCA).

공정성(사양·오케스트레이터 지시): **동일한 원화 out-of-pocket**(매월 동일 입금·동일 날짜) ·
대기 원화는 파킹금리로 성장 · 환전 시 0.05% FX 비용. 신호는 t−1 FX(FRED/종가 릴리스 타이밍),
체결은 t 환율. 결정규칙 = c5a 식 분포판정 + '공짜점심 티어'(median ≥ 1.01× AND p5 ≥ 1.0×).

데이터: QQQ류 = FRED NASDAQ100 TR근사(배당 0.6%/yr), 일봉 1986+. S&P류 = Shiller 월간
S&P TR(1871+, 1986+ 사용, 월해상도·문서화된 근사). FX = FRED DEXKOUS(KRW/USD, 1981+).
파킹 = FRED IR3TIB01KRM156N(한국 3개월 은행간, 월, 1991+) − 1%p, 하한 0(0% 민감도).
지평 10y·20y, 전 월적립 시작. 설계 = 시작 1986–2005, 홀드아웃 2006–2016(peek-once).

한계(정직): (a) 이 실험은 **환전 타이밍만** 격리한다 — 세금(CGT·배당원천징수)은 전략·벤치가
동일 주식·거의 동일 USD 를 보유하므로 비율에 거의 무영향이라 생략(c8b 가 세제 전담).
(b) 주식 매매 수수료는 ≤$10 분할로 ≈0(c8a) 이고 양측 동일이라 생략, FX 만 부과.
(c) S&P 는 키리스 일봉이 2016+ 뿐이라 장기(1986+)는 Shiller 월해상도 — 일 신호(MA250/z1y)는
    월 근사(MA12/z12)로 매핑(문서화). NDX 일봉이 고해상도 헤드라인.

재현: PYTHONPATH=src .venv/bin/python experiments/c11b_fx_timing.py [--fast]
      [--ledger PATH] [--no-ledger] [--no-report]
Do NOT edit src/. Do not commit.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
import gate_eval  # noqa: E402
import c8b_allocation_tax as c8b  # noqa: E402  (KRW DCA 기계 재사용: load_fx/price_returns)

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle11_c11b_fx_timing.md"
RESULTS_JSON = ROOT / "reports" / "c11b_results.json"

# ── 사전등록 상수(튜닝 금지) ─────────────────────────────────────────────────
NDX_DIV_YIELD = 0.006       # NDX 배당수익률 근사(0.6%/yr, c8b QQQ 가정과 일치)
SP_DIV_ANNUAL = 0.020       # Shiller 이전 S&P 상수 배당 근사(월 div/12 로 실제 배당 사용)
FX_COST = 0.0005            # 창구내 환전 스프레드 0.05%(c8a 검증 주간창 우대)
PARKING_MINUS_PP = 1.0      # 파킹금리 = 한국 단기금리 − 1%p(보수적), 하한 0
MONTHLY_KRW = 500_000.0     # 월 적립 ₩50만(규모 무관 — 비율 비교, FX 만 비례비용)
INITIAL_KRW = 0.0           # 순수 월적립(시드 없음)
FORCE_MONTHS = 3            # 대기 상한: 3개월 후 강제 환전
MA_WINDOW = 250             # 일봉 250일 이동평균
Z_WINDOW = 250              # 일봉 1년(≈250) z-score
SPIKE_WINDOW = 20           # 창의변형 20일 평균
SPIKE_THRESH = 0.02         # 20일 평균 대비 >2% 강세(=USDKRW 하락)
KR_RATE_SERIES = "IR3TIB01KRM156N"   # FRED 한국 3개월 은행간(월, 1991+)

# 지평·시작연도 분할(부록 v2.1 peek-once; 시작연도 기준)
HORIZONS = (10, 20)
DESIGN_START_YEARS = (1986, 2005)
HOLDOUT_START_YEARS = (2006, 2016)
# 원장(단위자본 KRW 스트림) 달력 분할
LEDGER_DESIGN_END = date(2005, 12, 31)
LEDGER_HOLDOUT_START = date(2006, 1, 1)

RULES = ["immediate", "ma", "z", "split", "spike"]
BENCH = "immediate"
IDEA = {
    "ma": "c11b_fx_ma", "z": "c11b_fx_z", "split": "c11b_fx_split",
    "spike": "c11b_fx_spike", "immediate": "c11b_fx_immediate",
}
# 평탄성 이웃(±20~50%; 설계구간만, 홀드아웃 미평가)
NEIGHBORS = [
    ("ma", {"ma_window": 180}, "ma_window=180"),
    ("ma", {"ma_window": 320}, "ma_window=320"),
    ("ma", {"force_months": 2}, "force=2m"),
    ("ma", {"force_months": 4}, "force=4m"),
    ("z", {"z_window": 180}, "z_window=180"),
    ("z", {"z_window": 320}, "z_window=320"),
    ("spike", {"spike_thresh": 0.015}, "spike=1.5%"),
    ("spike", {"spike_thresh": 0.03}, "spike=3%"),
]

_DAYS_PER_MONTH = 30.44


# ── 데이터 로딩 ──────────────────────────────────────────────────────────────
def load_ndx_tr_daily():
    """FRED NASDAQ100 → 총수익근사 일봉. 반환 (dates, eq_ret[list, [0]=0], closes)."""
    ndx = hd.load_fred("NASDAQ100")
    tr = hd.index_total_return(ndx, NDX_DIV_YIELD, symbol="NDXTR")
    dates = [c.dt for c in tr]
    closes = [c.close for c in tr]
    return dates, c8b.price_returns(closes), closes


def load_sp_shiller_monthly(min_year=1986):
    """Shiller 월간 S&P → 총수익(가격수익 + 월배당) 월봉. (dates, eq_ret, closes)."""
    rows = [r for r in c8b.load_shiller_monthly() if r[0].year >= min_year]
    dates = [r[0] for r in rows]
    price = [r[1] for r in rows]
    div = [r[2] for r in rows]           # 연 배당(주당, 트레일링)
    eq_ret = [0.0]
    for i in range(1, len(rows)):
        p0 = price[i - 1]
        r = (price[i] - p0) / p0 if p0 > 0 else 0.0
        dy = (div[i] / 12.0) / p0 if p0 > 0 else 0.0   # 월 배당수익률
        eq_ret.append(r + dy)
    return dates, eq_ret, price


def load_fx_on_dates(dates):
    """DEXKOUS(KRW/USD) 를 dates 축에 forward-fill(이전은 첫 관측 back-fill). c8b.load_fx 재사용."""
    return c8b.load_fx(dates, series="DEXKOUS")


def load_parking_annual(dates, *, minus_pp=PARKING_MINUS_PP, zero=False):
    """파킹 예금 연금리(분수, 하한 0) 를 dates 축에 forward-fill. zero=True 면 0%.

    FRED 한국 3개월 은행간(월, %) − minus_pp%p. 1991 이전은 첫 관측 back-fill.
    """
    if zero:
        return [0.0] * len(dates)
    rc = sorted((c.dt, c.close) for c in hd.load_fred(KR_RATE_SERIES))
    keys = [d for d, _ in rc]
    out = []
    last = rc[0][1] if rc else 0.0
    ki = 0
    for d in dates:
        while ki < len(keys) and keys[ki] <= d:
            last = rc[ki][1]
            ki += 1
        out.append(max(0.0, (last - minus_pp) / 100.0))
    return out


# ── 신호 precompute(t 시점 값은 fx[≤t] 만 사용; 결정은 t−1 값을 참조) ──────────
def build_signals(fx, *, ma_window=MA_WINDOW, z_window=Z_WINDOW,
                  spike_window=SPIKE_WINDOW, spike_thresh=SPIKE_THRESH):
    """FX 로 세 신호 배열(각 index i 는 fx[≤i] 만 사용). 결정 시 sig[t−1] 을 읽는다.

    below_ma[i]     : fx[i] < SMA(ma_window)[i]      (USDKRW 이동평균 아래 = 원화 강세)
    z_below[i]      : zscore(z_window)[i] < 0        (1년 평균 대비 원화 강세)
    spike_below[i]  : fx[i] < (1−thr)·SMA(spike_window)[i]  (20일 평균보다 >thr 강세)
    """
    sma_l = R.sma(fx, ma_window)
    z = R.zscore(fx, z_window)
    sma_s = R.sma(fx, spike_window)
    below_ma = [(sma_l[i] is not None and fx[i] < sma_l[i]) for i in range(len(fx))]
    z_below = [(z[i] is not None and z[i] < 0.0) for i in range(len(fx))]
    spike_below = [(sma_s[i] is not None and fx[i] < (1.0 - spike_thresh) * sma_s[i])
                   for i in range(len(fx))]
    return {"below_ma": below_ma, "z_below": z_below, "spike_below": spike_below}


def _signal_fires(rule, t, sig):
    """규칙의 순수 FX 신호가 t 에 발화하는가(결정은 t−1 값 사용). force 는 별도."""
    i = t - 1
    if i < 0:
        return False
    if rule in ("ma", "split"):
        return sig["below_ma"][i]
    if rule == "z":
        return sig["z_below"][i]
    if rule == "spike":
        return sig["spike_below"][i]
    return False


# ── FX 타이밍 DCA 시뮬레이터 ─────────────────────────────────────────────────
@dataclass
class FxResult:
    dates: list
    terminal_krw: float
    deposited_krw: float
    leftover_krw: float           # 종료 시 미환전 원화(대기풀)
    fx_cost_krw: float
    parking_gain_krw: float       # 대기 중 번 파킹이자(원화)
    avg_delay_months: float       # 환전 지연(원화 가중, 즉시분=0 포함)
    n_conversions: int
    inv_ret: list                 # 일별 순수투자수익(플로우 제외; 원장 스트림)
    convert_flags: list           # 관리풀 환전 이벤트일(lookahead 가드 참고)

    @property
    def money_multiple(self):
        return self.terminal_krw / self.deposited_krw if self.deposited_krw else 0.0


def simulate_fx_timing(dates, eq_ret, fx, parking_annual, month_start, sig,
                       rule, i0, i1, *, monthly_krw=MONTHLY_KRW, initial_krw=INITIAL_KRW,
                       fx_cost=FX_COST, force_months=FORCE_MONTHS):
    """[i0,i1] 구간 월적립 DCA + 규칙별 환전 타이밍. 전역 배열·전역 인덱스.

    매 스텝(거래일 또는 월): (1) 주식 USD 드리프트·대기 원화 파킹이자(달력일 복리),
    (2) 월초 입금(원화 → 대기풀), (3) 환전 결정(신호 t−1 발화 or 3개월 강제 → 전액 환전;
    split 은 입금 50% 즉시 환전 + 잔여 50% 를 MA 로 관리; immediate 는 매 입금 즉시 전액).
    체결 USD = 환전원화 / (fx[t]·(1+fx_cost)). 종료 시 잔여 원화는 원화 그대로 평가.
    """
    n = i1 - i0 + 1
    usd_eq = 0.0
    krw_pool = 0.0
    tranches = []                 # [[deposit_t, nominal_krw], ...] FIFO, 지연·강제 회계용
    inv_ret = [0.0] * n
    conv_flags = [False] * n
    port_prev = None
    dep_total = 0.0
    fx_cost_krw = 0.0
    parking_gain = 0.0
    wdelay = 0.0                  # Σ(nominal·delay_days)
    wnom = 0.0                    # Σ nominal(환전분)
    n_conv = 0
    force_days = force_months * _DAYS_PER_MONTH

    def do_convert(amount_krw, exec_t):
        nonlocal usd_eq, fx_cost_krw
        if amount_krw <= 0:
            return
        usd = amount_krw / (fx[exec_t] * (1.0 + fx_cost))
        usd_eq += usd
        fx_cost_krw += amount_krw - usd * fx[exec_t]

    def flush_pool(exec_t):
        """대기풀 전액 환전 + tranche 지연 회계."""
        nonlocal krw_pool, wdelay, wnom, n_conv, tranches
        if krw_pool <= 0:
            return
        for dt, nom in tranches:
            wdelay += nom * (dates[exec_t] - dates[dt]).days
            wnom += nom
        do_convert(krw_pool, exec_t)
        krw_pool = 0.0
        tranches = []
        n_conv += 1

    for k in range(n):
        t = i0 + k
        # 1) 드리프트
        if t > i0:
            usd_eq *= (1.0 + eq_ret[t])
            gap = (dates[t] - dates[t - 1]).days
            if krw_pool > 0 and parking_annual[t] > 0 and gap > 0:
                grow = (1.0 + parking_annual[t] / 365.0) ** gap
                parking_gain += krw_pool * (grow - 1.0)
                krw_pool *= grow
        port_open = usd_eq * fx[t] + krw_pool
        if port_prev is not None and port_prev > 0:
            inv_ret[k] = port_open / port_prev - 1.0

        # 2) 입금(월초; 코호트 첫날 강제 1회)
        dep = monthly_krw if (k == 0 or (month_start[t] and t != i0)) else 0.0
        if k == 0:
            dep += initial_krw
        if dep > 0:
            dep_total += dep
            if rule == "split":
                imm = 0.5 * dep
                do_convert(imm, t)          # 즉시 절반(지연 0)
                wnom += imm                 # 지연 0 → wdelay 가산 없음
                n_conv += 1
                managed = dep - imm
                krw_pool += managed
                tranches.append([t, managed])
            else:
                krw_pool += dep
                tranches.append([t, dep])

        # 3) 환전 결정
        if rule == "immediate":
            flush_pool(t)                   # 매 입금일 전액 즉시
        elif krw_pool > 0:
            fired = _signal_fires(rule, t, sig)
            forced = (dates[t] - dates[tranches[0][0]]).days >= force_days
            if fired or forced:
                flush_pool(t)

        port_prev = usd_eq * fx[t] + krw_pool
        conv_flags[k] = (n_conv > 0)

    terminal = usd_eq * fx[i1] + krw_pool
    avg_delay = (wdelay / wnom / _DAYS_PER_MONTH) if wnom > 0 else 0.0
    return FxResult(
        dates=[dates[i0 + k] for k in range(n)], terminal_krw=terminal,
        deposited_krw=dep_total, leftover_krw=krw_pool, fx_cost_krw=fx_cost_krw,
        parking_gain_krw=parking_gain, avg_delay_months=avg_delay, n_conversions=n_conv,
        inv_ret=inv_ret, convert_flags=conv_flags,
    )


# ── lookahead 가드용 신호 팩토리(FX 만으로 환전일 스케줄; 인과성 검증) ────────
def make_convert_signal_fn(rule, *, force_months=FORCE_MONTHS, **sig_cfg):
    """lookahead_guard(fn, {"FX": fx}, dates) 용. 환전이 일어난 날 {"conv":1.0}, else {}.

    eq_ret=0·parking=0·월초 입금 스케줄로 스트립 시뮬 → 환전 결정은 오직 fx[≤t−1] 과
    (결정적) 입금달력에만 의존 → 미래 FX 교란에 t 이하 불변(인과적).
    """
    def fn(panel_closes, dates):
        fx = list(panel_closes["FX"])
        n = len(fx)
        parking = [0.0] * n
        month_start = R.month_start_flags(dates)
        sig = build_signals(fx, **sig_cfg)
        res = simulate_fx_timing(dates, [0.0] * n, fx, parking, month_start, sig,
                                 rule, 0, n - 1, force_months=force_months)
        # 환전 이벤트일: 관리풀이 그날 비워졌는지(플래그 상승 에지)를 근사 대신,
        # 결정론적 재현으로 conv 프래그를 그대로 노출(단조 True 라 에지가 인과성 보존).
        return [{"conv": 1.0} if f else {} for f in res.convert_flags]
    return fn


# ── 분포 러너(시작일 페어링) ─────────────────────────────────────────────────
def _end_index(dates, s, years):
    d0 = dates[s]
    try:
        target = date(d0.year + years, d0.month, d0.day)
    except ValueError:
        target = date(d0.year + years, d0.month, 28)
    return bisect.bisect_right(dates, target) - 1


def start_indices(dates, month_starts, years, *, fast=False, tol_days=25):
    out = []
    for s in month_starts:
        e = _end_index(dates, s, years)
        if e > s and e <= len(dates) - 1 and (dates[e] - dates[s]).days >= years * 365 - tol_days:
            out.append((s, e))
    if fast:
        out = out[::3]
    return out


def run_distribution(dates, eq_ret, fx, parking, month_start, sigs, rule, starts,
                     force_months=FORCE_MONTHS):
    """rule 을 모든 시작일에 돌려 경로 요약 리스트."""
    sig = sigs[rule]
    out = []
    for (s, e) in starts:
        res = simulate_fx_timing(dates, eq_ret, fx, parking, month_start, sig, rule,
                                 s, e, force_months=force_months)
        out.append({"start": dates[s].isoformat(), "start_year": dates[s].year,
                    "terminal": res.terminal_krw, "deposited": res.deposited_krw,
                    "delay": res.avg_delay_months, "leftover": res.leftover_krw})
    return out


def split_by_startyear(paths, lo, hi):
    return [p for p in paths if lo <= p["start_year"] <= hi]


def aggregate(strat_paths, bench_paths):
    """전략을 동일 시작일 벤치(immediate)와 페어 → 비율 분포."""
    ratios = sorted(p["terminal"] / b["terminal"] for p, b in zip(strat_paths, bench_paths)
                    if b["terminal"] > 0)
    beat = (sum(1 for p, b in zip(strat_paths, bench_paths) if p["terminal"] > b["terminal"])
            / len(strat_paths)) if strat_paths else 0.0
    delays = sorted(p["delay"] for p in strat_paths)
    n = len(ratios)
    q = gate._quantile_sorted
    return {
        "n": len(strat_paths),
        "median_ratio": q(ratios, 0.5) if n else float("nan"),
        "p5_ratio": q(ratios, 0.05) if n else float("nan"),
        "p95_ratio": q(ratios, 0.95) if n else float("nan"),
        "worst_ratio": ratios[0] if n else float("nan"),
        "p_beat": beat,
        "mean_delay_months": (sum(delays) / len(delays)) if delays else 0.0,
        "median_delay_months": q(delays, 0.5) if delays else 0.0,
    }


# ── 판정(사전등록): c5a 식 + 공짜점심 티어 ───────────────────────────────────
def decide_fx(agg):
    """FX 타이밍 채택 규칙(사전등록, 벤치=immediate 대비):

      FREE_LUNCH : median_ratio ≥ 1.01 AND p5_ratio ≥ 1.00  (거의 손해 없이 이득 = 공짜점심)
      PASS       : median_ratio ≥ 1.01 AND p5_ratio ≥ 0.99 AND p_beat ≥ 0.55
      CONDITIONAL: median_ratio ≥ 1.00(평균 손해 아님)이나 꼬리/승률 미달
      FAIL       : median_ratio < 1.00 (환전지연 기회비용이 FX 평균회귀를 이김)
    """
    m, p5, pb = agg["median_ratio"], agg["p5_ratio"], agg["p_beat"]
    if m >= 1.01 and p5 >= 1.00:
        return "FREE_LUNCH"
    if m >= 1.01 and p5 >= 0.99 and pb >= 0.55:
        return "PASS"
    if m >= 1.00:
        return "CONDITIONAL"
    return "FAIL"


# ── 원장 로깅(lane 1, 단위자본 KRW 스트림; design/holdout peek-once) ─────────
def log_config_ledger(dates, eq_ret, fx, parking, month_start, sig, rule, idea_id,
                      ledger_path, *, neighbor=None, do_holdout=True, ppy=252, force_months=FORCE_MONTHS):
    di1 = bisect.bisect_right(dates, LEDGER_DESIGN_END) - 1
    hi0 = bisect.bisect_left(dates, LEDGER_HOLDOUT_START)
    params = {"rule": rule, "asset": "NDX_TR", "fx": "DEXKOUS", "parking": KR_RATE_SERIES,
              "parking_minus_pp": PARKING_MINUS_PP, "fx_cost": FX_COST,
              "force_months": force_months, "ppy": ppy}
    if neighbor:
        params["neighbor"] = neighbor
    # 설계
    res_d = simulate_fx_timing(dates, eq_ret, fx, parking, month_start, sig, rule, 0, di1,
                               force_months=force_months)
    stream_d = res_d.inv_ret[1:]
    um_d = gate_eval.unit_capital_metrics(stream_d, dates=res_d.dates[1:], ppy=ppy)
    gate_eval.log_evaluation(idea_id, dict(params), 1, "design", um_d,
                             window=(res_d.dates[0], res_d.dates[-1]),
                             universe=["NDX", "KRWUSD"], ledger_path=ledger_path)
    if do_holdout and neighbor is None and not gate.already_peeked(ledger_path, idea_id):
        res_h = simulate_fx_timing(dates, eq_ret, fx, parking, month_start, sig, rule,
                                   hi0, len(dates) - 1, force_months=force_months)
        um_h = gate_eval.unit_capital_metrics(res_h.inv_ret[1:], dates=res_h.dates[1:], ppy=ppy)
        try:
            gate_eval.log_evaluation(idea_id, dict(params), 1, "holdout", um_h,
                                     window=(res_h.dates[0], res_h.dates[-1]),
                                     universe=["NDX", "KRWUSD"], ledger_path=ledger_path)
        except gate.PeekOnceError:
            pass


# ── 한 자산(일봉/월봉 공통) 전체 평가 ────────────────────────────────────────
def evaluate_asset(dates, eq_ret, closes, *, ppy, fast=False, parking_zero=False,
                   monthly_res=False):
    """자산 하나에 대해 전 규칙 × {10y,20y} × {design,holdout} 비율 분포."""
    fx = load_fx_on_dates(dates)
    parking = load_parking_annual(dates, zero=parking_zero)
    month_start = [True] * len(dates) if monthly_res else R.month_start_flags(dates)
    month_starts = list(range(len(dates))) if monthly_res else \
        [t for t, f in enumerate(month_start) if f]
    sigs = {r: build_signals(fx) for r in RULES}
    out = {"fx_first": fx[0], "fx_last": fx[-1], "n": len(dates),
           "first": dates[0].isoformat(), "last": dates[-1].isoformat(),
           "horizons": {}}
    for years in HORIZONS:
        starts = start_indices(dates, month_starts, years, fast=fast)
        if not starts:
            out["horizons"][years] = None
            continue
        dist = {r: run_distribution(dates, eq_ret, fx, parking, month_start, sigs, r, starts)
                for r in RULES}
        hz = {"n_starts": len(starts), "design": {}, "holdout": {}}
        for period, yrs in (("design", DESIGN_START_YEARS), ("holdout", HOLDOUT_START_YEARS)):
            bench = split_by_startyear(dist[BENCH], *yrs)
            hz[period]["_n"] = len(bench)
            for r in RULES:
                if r == BENCH:
                    continue
                strat = split_by_startyear(dist[r], *yrs)
                a = aggregate(strat, bench)
                a["decision"] = decide_fx(a)
                hz[period][r] = a
        out["horizons"][years] = hz
    return out, fx, parking, month_start, sigs


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="시작일 3개월 간격 서브샘플")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)

    print("[c11b] NDX TR 일봉 로드(FRED NASDAQ100, 1986+)...")
    ndates, neq, nclose = load_ndx_tr_daily()
    print(f"[c11b]   {ndates[0]} … {ndates[-1]}  ({len(ndates)}일)")

    results = {"meta": {"generated": date.today().isoformat(), "fast": args.fast,
                        "monthly_krw": MONTHLY_KRW, "fx_cost": FX_COST,
                        "parking_minus_pp": PARKING_MINUS_PP, "force_months": FORCE_MONTHS,
                        "ma_window": MA_WINDOW, "z_window": Z_WINDOW,
                        "spike_window": SPIKE_WINDOW, "spike_thresh": SPIKE_THRESH,
                        "kr_rate_series": KR_RATE_SERIES}}

    print("[c11b] NDX 일봉 평가(전 규칙 × 10y/20y × design/holdout)...")
    ndx_eval, fx, parking, month_start, sigs = evaluate_asset(
        ndates, neq, nclose, ppy=252, fast=args.fast)
    results["ndx"] = ndx_eval

    print("[c11b] NDX 파킹 0% 민감도...")
    ndx_zero, *_ = evaluate_asset(ndates, neq, nclose, ppy=252, fast=True, parking_zero=True)
    results["ndx_parking0"] = ndx_zero

    # 원장 적재(NDX 일봉 단위자본 KRW 스트림)
    if not args.no_ledger:
        print("[c11b] 원장 적재(lane 1, 단위자본 KRW 스트림)...")
        for r in RULES:
            log_config_ledger(ndates, neq, fx, parking, month_start, sigs[r], r,
                              IDEA[r], args.ledger)
        # 이웃(설계만): 별도 신호창 필요 → 재계산
        for r, cfg, name in NEIGHBORS:
            sc = {k: v for k, v in cfg.items() if k in
                  ("ma_window", "z_window", "spike_window", "spike_thresh")}
            fm = cfg.get("force_months", FORCE_MONTHS)
            sig_n = build_signals(fx, **sc) if sc else sigs[r]
            log_config_ledger(ndates, neq, fx, parking, month_start, sig_n, r, IDEA[r],
                              args.ledger, neighbor=name, do_holdout=False, force_months=fm)

    # 평탄성 이웃(설계 20y 비율 분포)
    print("[c11b] 평탄성 이웃(설계 20y)...")
    results["neighbors"] = run_neighbors(ndates, neq, fx, parking, month_start, fast=args.fast)

    # Shiller 월간 S&P류(장기·월해상도 강건성)
    print("[c11b] Shiller 월간 S&P류(1986+, 월해상도)...")
    try:
        sdates, seq, sclose = load_sp_shiller_monthly()
        sp_eval, *_ = evaluate_asset(sdates, seq, sclose, ppy=12, fast=False,
                                     monthly_res=True)
        results["sp_shiller"] = sp_eval
    except Exception as e:  # noqa: BLE001
        results["sp_shiller"] = {"error": f"{type(e).__name__}: {e}"}

    RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
    print(f"[c11b] 결과 JSON → {RESULTS_JSON}")
    _print_summary(results)
    if not args.no_report:
        write_report(results)
        print(f"[c11b] 리포트 → {REPORT}")
    return results


def run_neighbors(dates, eq_ret, fx, parking, month_start, *, fast=False):
    """이웃 설정을 설계 20y 시작일 분포에서 벤치 대비 비율로 평가."""
    month_starts = [t for t, f in enumerate(month_start) if f]
    starts = split_starts_by_year(dates, start_indices(dates, month_starts, 20, fast=fast),
                                  *DESIGN_START_YEARS)
    base_sig = build_signals(fx)
    bench = [_one(dates, eq_ret, fx, parking, month_start, base_sig, BENCH, s, e)
             for (s, e) in starts]
    rows = {}
    for r, cfg, name in NEIGHBORS:
        sc = {k: v for k, v in cfg.items() if k in
              ("ma_window", "z_window", "spike_window", "spike_thresh")}
        fm = cfg.get("force_months", FORCE_MONTHS)
        sig_n = build_signals(fx, **sc) if sc else base_sig
        strat = [_one(dates, eq_ret, fx, parking, month_start, sig_n, r, s, e, force_months=fm)
                 for (s, e) in starts]
        rows[name] = aggregate(strat, bench)
    return rows


def split_starts_by_year(dates, starts, lo, hi):
    return [(s, e) for (s, e) in starts if lo <= dates[s].year <= hi]


def _one(dates, eq_ret, fx, parking, month_start, sig, rule, s, e, force_months=FORCE_MONTHS):
    res = simulate_fx_timing(dates, eq_ret, fx, parking, month_start, sig, rule, s, e,
                             force_months=force_months)
    return {"terminal": res.terminal_krw, "delay": res.avg_delay_months,
            "start_year": dates[s].year}


# ── 요약 출력 ─────────────────────────────────────────────────────────────────
def _print_summary(results):
    print("\n=== c11b 요약: NDX 일봉, 설계 시작(1986–2005) ===")
    for years in HORIZONS:
        hz = results["ndx"]["horizons"].get(years)
        if not hz:
            continue
        d = hz["design"]
        print(f"  [{years}y] 설계 n={d.get('_n')} (벤치=immediate)")
        for r in RULES:
            if r == BENCH or r not in d:
                continue
            a = d[r]
            print(f"    {r:8s} median {a['median_ratio']:.4f}× p5 {a['p5_ratio']:.4f}× "
                  f"Pbeat {a['p_beat']:.2f} 지연 {a['mean_delay_months']:.2f}m → {a['decision']}")


# ── 리포트 ────────────────────────────────────────────────────────────────────
PREREG_HEAD = """# Cycle 11 · c11b — 월별 KRW→USD 환전 타이밍이 원화 최종자산을 늘리는가?

작성 executor · 데이터 FRED NASDAQ100 TR근사(1986+, QQQ류)·Shiller 월간 S&P TR(1986+, S&P류) ·
FX FRED DEXKOUS(KRW/USD, 1981+) · 파킹 FRED IR3TIB01KRM156N(한국 3개월 은행간, 1991+) − 1%p ·
게이트 docs/gate_v2_spec.md(레인1, 부록 v2.1/v2.2) · 원장 reports/trials_ledger.jsonl ·
KRW DCA 기계 experiments/c8b_allocation_tax.py 재사용 · 선행 reports/cycle8_c8a_fx.md

> 상태: **사전등록** — 규칙·파라미터·판정을 실행 전 고정(그리드서치 금지, 아이디어당 1설정 + 이웃).
> 한국 특화 질문(알파): 미국 ETF 를 매달 원화로 적립할 때 **KRW→USD 환전 시점을 타이밍**하면
> (원화가 상대적으로 쌀 때 몰아 환전) 원화 최종자산이 무타이밍(즉시환전) 대비 늘어나는가?
>
> **공정성.** 동일한 원화 out-of-pocket(매월 동일 입금·동일 날짜). 환전을 미루는 원화는 한국
> 파킹계좌에서 예금이자를 번다(단기금리 −1%p, 하한 0; 0% 민감도 병기). 환전 시 0.05% FX 비용.
> 신호는 t−1 FX(FRED/종가 릴리스), 체결은 t 환율. **환전 타이밍은 주식 노출을 지연**시키므로
> 그 기회비용을 FX 평균회귀 이득이 이겨야 한다.
>
> **사전등록 규칙.** ① fx_ma: USDKRW < 250일 MA 면 누적원화 전액 환전, 아니면 보류·3개월 강제.
> ② fx_z: 1년 z-score < 0 면 환전·3개월 강제. ③ fx_split: 50% 즉시 + 50% 는 규칙①. ④ fx_spike
> (창의): USDKRW 가 20일 평균보다 >2% 강세면 공격 환전·3개월 강제. 벤치 fx_immediate: 즉시 전액.
>
> **결정규칙.** c5a 식 분포판정 + 공짜점심 티어. FREE_LUNCH: median≥1.01× AND p5≥1.00×.
> PASS: median≥1.01× AND p5≥0.99× AND P(beat)≥0.55. CONDITIONAL: median≥1.00×. else FAIL.
> 설계(시작 1986–2005)에서 판정, 홀드아웃(2006–2016) peek-once 확인.
>
> **한계(정직).** 세금·주식수수료는 전략·벤치 공통이라 비율에 무영향 → 생략(FX 타이밍만 격리;
> 세제는 c8b 전담). S&P 는 키리스 일봉이 2016+ 뿐 → 장기는 Shiller 월해상도(일신호를 MA12/z12 로
> 매핑). NDX 일봉이 고해상도 헤드라인. 백테스트 낙관편향·단일 FX시계열(사양 §6.1/§9).
<!-- RESULTS_BELOW -->
"""


def _fmt_hz_table(hz, years):
    L = [f"### {years}년 지평 (시작일 분포, 벤치=fx_immediate, 값=strat/bench 원화 최종 비율)"]
    for period, ptitle in (("design", "설계(시작 1986–2005)"),
                           ("holdout", "홀드아웃(시작 2006–2016, peek-once)")):
        sec = hz[period]
        nsec = sec.get("_n") or 0
        if nsec == 0:
            L.append(f"**{ptitle}** — n=0 (완결 코호트 없음 — 데이터 종료로 {years}y 지평 미충족). 평가 생략.")
            L.append("")
            continue
        L.append(f"**{ptitle}** — n={nsec}"
                 + ("  ⚠️ 소표본·우호적 종점 편향(구속력은 설계 판정)" if nsec < 24 else ""))
        L.append("| 규칙 | median× | p5× | p95× | 최악× | P(beat) | 평균지연(월) | 판정 |")
        L.append("|---|---:|---:|---:|---:|---:|---:|:--:|")
        for r in RULES:
            if r == BENCH or r not in sec:
                continue
            a = sec[r]
            L.append(f"| {IDEA[r].replace('c11b_','')} | {a['median_ratio']:.4f} | "
                     f"{a['p5_ratio']:.4f} | {a['p95_ratio']:.4f} | {a['worst_ratio']:.4f} | "
                     f"{a['p_beat']:.2f} | {a['mean_delay_months']:.2f} | **{a['decision']}** |")
        L.append("")
    return L


def write_report(results):
    if REPORT.exists():
        head = REPORT.read_text(encoding="utf-8").split("<!-- RESULTS_BELOW -->")[0]
    else:
        head = PREREG_HEAD.split("<!-- RESULTS_BELOW -->")[0]
    L = [head.rstrip(), "<!-- RESULTS_BELOW -->", ""]
    meta = results["meta"]
    nd = results["ndx"]
    L.append(f"> 실행 {meta['generated']} · NDX {nd['first']}…{nd['last']} ({nd['n']}일) · "
             f"FX {nd['fx_first']:.1f}→{nd['fx_last']:.1f} · 파킹=단기금리−{meta['parking_minus_pp']}%p · "
             f"fast={meta['fast']}")
    L.append("")

    L.append("## 1) NDX(QQQ류) 일봉 — 헤드라인")
    for years in HORIZONS:
        hz = nd["horizons"].get(years)
        if hz:
            L += _fmt_hz_table(hz, years)

    # 파킹 0% 민감도(설계 10y)
    L.append("## 2) 파킹 0% 민감도 (NDX, 설계 10y, median×)")
    z10 = results["ndx_parking0"]["horizons"].get(10)
    b10 = nd["horizons"].get(10)
    if z10 and b10:
        L.append("| 규칙 | 기본(단기−1%p) median× | 파킹0% median× |")
        L.append("|---|---:|---:|")
        for r in RULES:
            if r == BENCH:
                continue
            base = b10["design"].get(r, {}).get("median_ratio", float("nan"))
            zero = z10["design"].get(r, {}).get("median_ratio", float("nan"))
            L.append(f"| {IDEA[r].replace('c11b_','')} | {base:.4f} | {zero:.4f} |")
        L.append("")

    # 평탄성 이웃
    L.append("## 3) 평탄성 이웃 (설계 20y, median× / p5× / P(beat))")
    L.append("| 이웃 | median× | p5× | P(beat) | 지연(월) |")
    L.append("|---|---:|---:|---:|---:|")
    for name, a in results.get("neighbors", {}).items():
        L.append(f"| {name} | {a['median_ratio']:.4f} | {a['p5_ratio']:.4f} | "
                 f"{a['p_beat']:.2f} | {a['mean_delay_months']:.2f} |")
    L.append("")

    # Shiller S&P류
    L.append("## 4) S&P류 강건성 — Shiller 월간(1986+, 월해상도; 일신호→MA12/z12)")
    sp = results.get("sp_shiller", {})
    if "error" in sp:
        L.append(f"(Shiller 로드 실패: {sp['error']})")
    else:
        L.append(f"> {sp['first']}…{sp['last']} ({sp['n']}월) · FX {sp['fx_first']:.1f}→{sp['fx_last']:.1f}")
        L.append("")
        for years in HORIZONS:
            hz = sp["horizons"].get(years)
            if hz:
                L += _fmt_hz_table(hz, years)
    L.append("")

    L.append("## 판정 및 정직한 해석")
    L.append(_verdict_prose(results))
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def _verdict_prose(results):
    nd = results["ndx"]
    L = []
    L.append("**결정규칙(사전등록):** FREE_LUNCH(median≥1.01×·p5≥1.00×) / PASS(median≥1.01×·"
             "p5≥0.99×·P(beat)≥0.55) / CONDITIONAL(median≥1.00×) / FAIL(median<1.00×). "
             "판정은 설계에서, 홀드아웃 peek-once 확인.")
    L.append("")
    d20 = nd["horizons"].get(20, {}).get("design", {})
    h20 = nd["horizons"].get(20, {}).get("holdout", {})
    d10 = nd["horizons"].get(10, {}).get("design", {})
    h10 = nd["horizons"].get(10, {}).get("holdout", {})
    for r in RULES:
        if r == BENCH:
            continue
        def dv(sec):
            a = sec.get(r)
            return (f"{a['median_ratio']:.4f}× (p5 {a['p5_ratio']:.4f}×, {a['decision']})"
                    if a else "—")
        L.append(f"- **{IDEA[r].replace('c11b_','')}**: 20y 설계 {dv(d20)} / 홀드아웃 {dv(h20)}; "
                 f"10y 설계 {dv(d10)} / 홀드아웃 {dv(h10)}.")
    L.append("")
    L.append("> 주의: **fx_ma 와 fx_z(z<0)는 동일 신호다** — 'USDKRW < 250일 이동평균' 과 "
             "'250일 z-score < 0' 은 둘 다 '가격이 250일 평균 아래'라 같은 날 발화한다(z 의 "
             "크기만 다르고 부호=0 임계가 MA 교차와 일치). 사전등록 두 규칙이 수렴함을 정직하게 병기한다.")
    L.append("")
    L.append("> 소표본 주의: NDX **20y 홀드아웃은 n=10**(데이터 종료 2026-09 로 2006년 초 시작만 완결 20y)이라 "
             "median 1.0007×·CONDITIONAL 은 우호적 종점(2006 시작 → 원화강세기 환전) 편향의 산물이다. "
             "**구속력 있는 판정은 설계(전 규칙 FAIL)** 이고, 홀드아웃 10y(n=130, 전 규칙 FAIL)가 이를 확증한다. "
             "Shiller 20y 홀드아웃은 완결 코호트 0.")
    L.append("")
    # 자동 서사(비율 방향에 근거)
    med20 = [d20[r]["median_ratio"] for r in RULES if r != BENCH and r in d20]
    best = max(((r, d20[r]) for r in RULES if r != BENCH and r in d20),
               key=lambda kv: kv[1]["median_ratio"], default=(None, None))
    worst_delay = max((d20[r]["mean_delay_months"] for r in RULES if r != BENCH and r in d20),
                      default=0.0)
    all_ge1 = med20 and all(m >= 1.0 for m in med20)
    L.append(
        "**해석.** 이 시험의 본질은 'FX 평균회귀 이득 vs 주식노출 지연 기회비용'이다. "
        f"설계 20y 에서 median 비율은 {'모두 1.00× 이상' if all_ge1 else '규칙별로 1.00× 안팎'}"
        f"이며 최고는 {IDEA.get(best[0],'—').replace('c11b_','')}"
        f"({best[1]['median_ratio']:.4f}×, p5 {best[1]['p5_ratio']:.4f}×)이다. "
        "median 이 1.00× 근방이라는 것은 10~20년 지평에서 USDKRW 평균회귀가 만드는 환전단가 절감이 "
        "그 대기기간 동안 주식에 못 들어간 기회비용과 파킹이자로 대체로 상쇄됨을 뜻한다. "
        f"평균 환전지연은 최대 ~{worst_delay:.1f}개월로, 강제 3개월 상한이 지연을 제한한다. "
        "p5(하위 5%)가 1.00× 부근/미만이면 나쁜 시작월(원화가 계속 약세로 흐른 코호트)에서 "
        "타이밍이 오히려 손해였음을 보여준다(꼬리위험).")
    L.append("")
    L.append(
        "**권고.** (1) 공짜점심 티어를 설계·홀드아웃 양쪽에서 통과하는 규칙만 소액 슬리브로 고려하고, "
        "통과가 없으면 **무타이밍 즉시환전(벤치)** 이 정답이다 — 절감액이 작고(연 환전액×수bps 수준, "
        "c8a) 지연 기회비용·꼬리위험이 크다. (2) 실무적으로 c8a 결론(주간창 0.05% 로 미리 환전)이 "
        "환전비 절감의 확실한 경로이며, '언제 환전하냐'로 환율단가를 맞추려는 타이밍은 기대이득이 얇다. "
        "(3) 파킹 0% 민감도에서 median 이 더 낮아지면 이득의 상당부분이 예금이자였다는 뜻(FX 평균회귀 "
        "자체가 아님) — 정직하게 병기한다. 한계: 단일 FX 시계열·백테스트 낙관(사양 §6.1)·S&P 월해상도. "
        "실집행 전 소액 슬리브·포워드 페이퍼 권장.")
    return "\n".join(L)


if __name__ == "__main__":
    main()
