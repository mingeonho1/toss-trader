#!/usr/bin/env python3
"""c12 — 공격형 수익 레인(Lane A) 추세·변동성 전략 배치 평가. Cycle 12, id prefix `c12`.

사전등록·규약: experiments/README.md, docs/gate_v2_spec.md 부록 v3(Lane A),
규칙 원전: docs/aggressive_strategy_catalog.md (A/C/D/G 군).

대상 전략(카탈로그 규칙 그대로 1회 평가):
  A1 LRS(200SMA)        A2 200SMA +5/−3 버퍼    A3 9Sig(TQQQ/AGG)
  A4 Alvarez 4조건 레짐  A5 In&Out Distilled Bear
  C1 HFEA 55/45          C2 TQQQ/TMF 50/50 격월+폭락필터
  D1 VRatio(VIX/VIX3M)   D2 VRP(VIX−HV10)
  G1 오버나이트 TQQQ     G2 오버나이트 IBIT      G3 BTC 추세→IBIT
  G5 3x 모멘텀 로테이션  G6 Accelerating Dual Momentum(레버리지판)

데이터(키 불필요, histdata 소비만):
  실물 ETF 2016-09~2026-09 (Nasdaq 폴백, 시가 포함) — 1차 평가.
  합성 3x/2x(FRED NASDAQ100 1986~) — A1/A2 장기 런.
  VIX(FRED VIXCLS) · VIX3M(CBOE keyless CSV, 로컬 캐시) · BTC(FRED CBBTCUSD 2014~).

실행 규칙: 신호 close t → 체결 close t+1(exec_lag=1). G1/G2 오버나이트는 close 매수→익일 open 매도(실 시가).
비용: 토스 0.1%/side(10bp) + 반호가 티어 + 슬리피지 5bp. 2× 스트레스, 마이크로(≤$10 무료) 병기.
각 레버리지 전략은 1x 섀도(QQQ/SPY/IBIT 등) 동일신호도 보고(규제 기본예탁금 대비).

Lane A 판정(부록 v3): (a) 설계·홀드아웃 모두 CAGR>QQQ, (b) 이웃 60%+ CAGR>QQQ,
  (c) 2× 비용에서도 (a), 파산가드 전표본 MDD>−95%. DSR/RC/SPA는 보고만.

src/ 는 수정하지 않는다. 재현: PYTHONPATH=src .venv/bin/python experiments/c12_trend_vol.py [--fast]
  결과표·판정을 reports/cycle12_trend_vol.md 로, 원장(lane "A")을 reports/trials_ledger.jsonl 에 적재.
"""
from __future__ import annotations

import bisect
import json
import math
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
from toss_trader.models import Candle  # noqa: E402
import gate_eval  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle12_trend_vol.md"
RESULTS_JSON = ROOT / "reports" / "c12_trend_vol_results.json"
CBOE_CACHE = hd.CACHE_DIR / "cboe"

# ── 구간 상수(사전등록) ──────────────────────────────────────────────────────
REAL_START = date(2016, 9, 22)
REAL_END = date(2026, 12, 31)
DESIGN_END = date(2021, 12, 31)      # 실물 10년: 설계 2016-09~2021-12, 홀드아웃 2022~
SYN_DESIGN_END = date(2008, 12, 31)  # 합성 장기: 설계 1986~2008, 홀드아웃 2009~
TRADING_DAYS = 252
LANE = "A"

# ── 비용 티어 ────────────────────────────────────────────────────────────────
MEGA_ETF = ["QQQ", "SPY", "IEF", "TLT", "BIL", "GLD", "SGOV", "AGG", "BND", "TIP",
            "SHY", "VWO", "SCZ", "EEM", "EFA", "VEA", "XLI", "XLU", "SLV", "DBB",
            "UUP", "IBIT", "SCHD", "VTI"]
LEV_ETF = ["TQQQ", "SQQQ", "QLD", "SSO", "UPRO", "SPXU", "TMF", "TMV", "SOXL", "SOXS",
           "TECL", "TECS", "TNA", "CURE", "LABU", "FAS", "DRN", "FNGU",
           "L1", "L2", "L3", "L-3"]
HOT = ["SVXY", "VIXY", "UVXY", "SVIX", "MSTU", "MSTX", "BITX", "CONL", "MSTR",
       "COIN", "NVDL", "TSLL"]
TIER_MAP: dict[str, str] = {}
for _s in MEGA_ETF:
    TIER_MAP[_s] = R.TIER_ETF
for _s in LEV_ETF:
    TIER_MAP[_s] = R.TIER_LEVERAGED_ETF
for _s in HOT:
    TIER_MAP[_s] = R.TIER_SMALL_HOT


def cost_spec(symbols, *, commission_bps: float = 10.0, mult: float = 1.0) -> R.CostSpec:
    tiers = {s: TIER_MAP.get(s, R.TIER_LARGE_CAP) for s in symbols}
    cs = R.CostSpec.from_tiers(tiers, commission_bps=commission_bps, slippage_bps=5.0)
    return cs.stress(mult) if mult != 1.0 else cs


# ── 데이터 로딩(캐시 소비만) ─────────────────────────────────────────────────
_CANDLE_CACHE: dict[str, list[Candle]] = {}


def _candles(sym: str) -> list[Candle]:
    if sym not in _CANDLE_CACHE:
        _CANDLE_CACHE[sym] = hd.load_symbol(sym, adjusted=True)
    return _CANDLE_CACHE[sym]


def _ffill(candles: list[Candle], dates: list[date]) -> list[float]:
    ks = [c.dt for c in candles]
    vs = [c.close for c in candles]
    out: list[float] = []
    last = vs[0] if vs else 0.0
    for d in dates:
        i = bisect.bisect_right(ks, d) - 1
        out.append(vs[i] if i >= 0 else last)
    return out


def load_vix3m() -> list[Candle]:
    """CBOE VIX3M 일봉(keyless CSV, 로컬 캐시). 종가만 사용."""
    path = CBOE_CACHE / "VIX3M.json"
    if path.exists():
        rows = json.loads(path.read_text())["rows"]
    else:
        url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv"
        raw = hd._http_get(url, timeout=30).decode("utf-8", "replace")
        rows = []
        for ln in raw.strip().splitlines()[1:]:
            p = ln.split(",")
            if len(p) < 5:
                continue
            try:
                mm, dd, yy = p[0].split("/")
                d = date(int(yy), int(mm), int(dd))
                v = float(p[4])
            except ValueError:
                continue
            rows.append({"d": d.isoformat(), "c": v})
        CBOE_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"series": "VIX3M", "rows": rows}))
    return [Candle("VIX3M", date.fromisoformat(r["d"]), r["c"], r["c"], r["c"], r["c"], 0.0)
            for r in sorted(rows, key=lambda r: r["d"])]


def load_btc() -> list[Candle]:
    return hd.load_fred("CBBTCUSD")


@dataclass
class Panel:
    dates: list[date]
    closes: dict[str, list[float]]   # 체결 가능 종가
    opens: dict[str, list[float]]    # 체결 가능 시가
    aux: dict[str, list[float]]      # 비체결 보조 시리즈(VIX/VIX3M/BTC)
    cash: list[float]                # 일간 현금수익률(DTB3)

    def full(self) -> dict[str, list[float]]:
        """신호 함수용 전체 패널(체결 종가 + 보조). lookahead_guard 대상."""
        d = dict(self.closes)
        d.update(self.aux)
        return d


def build_panel(tradeable: list[str], aux_syms: list[str] | None = None) -> Panel:
    """tradeable 심볼들의 공통 거래일로 정렬한 실물 패널 + 보조 시리즈 + 현금수익률."""
    cs = {s: _candles(s) for s in tradeable}
    common = set.intersection(*[{c.dt for c in cs[s]} for s in tradeable])
    dates = sorted(d for d in common if REAL_START <= d <= REAL_END)
    closes: dict[str, list[float]] = {}
    opens: dict[str, list[float]] = {}
    for s in tradeable:
        by = {c.dt: c for c in cs[s]}
        closes[s] = [by[d].close for d in dates]
        opens[s] = [by[d].open for d in dates]
    aux: dict[str, list[float]] = {}
    for a in (aux_syms or []):
        if a == "VIX":
            aux["VIX"] = _ffill(hd.load_fred("VIXCLS"), dates)
        elif a == "VIX3M":
            aux["VIX3M"] = _ffill(load_vix3m(), dates)
        elif a == "BTC":
            aux["BTC"] = _ffill(load_btc(), dates)
        elif a == "SPX":
            aux["SPX"] = _ffill(hd.load_fred("SP500"), dates)
        else:
            aux[a] = _ffill(_candles(a), dates)
    dtb3 = hd.load_fred("DTB3")
    cash = _cash_rate(dtb3, dates)
    return Panel(dates, closes, opens, aux, cash)


def _cash_rate(dtb3: list[Candle], dates: list[date]) -> list[float]:
    ks = [c.dt for c in dtb3]
    vs = [c.close for c in dtb3]
    out = []
    for d in dates:
        i = bisect.bisect_right(ks, d) - 1
        a = vs[i] if i >= 0 else (vs[0] if vs else 0.0)
        out.append(max(0.0, (a / 100.0) / TRADING_DAYS))
    return out


# ── 합성 3x/2x 패널(FRED NDX, 1986~) — A1/A2 장기 런 ─────────────────────────
def build_synth_panel() -> Panel:
    ndx = hd.load_fred("NASDAQ100")
    dtb3 = hd.load_fred("DTB3")
    vix = hd.load_fred("VIXCLS")
    tr = hd.index_total_return(ndx, 0.007, symbol="NDXTR")
    L1 = hd.synthetic_leveraged(tr, 1.0, annual_expense=0.002, borrow_spread=0.0,
                                rf_candles=dtb3, rf_kind="yield", symbol="QQQ")
    L3 = hd.synthetic_leveraged(tr, 3.0, annual_expense=0.0095, borrow_spread=0.005,
                                rf_candles=dtb3, rf_kind="yield", symbol="TQQQ")
    dates = [c.dt for c in L1]
    closes = {"QQQ": [c.close for c in L1], "TQQQ": [c.close for c in L3]}
    opens = {"QQQ": list(closes["QQQ"]), "TQQQ": list(closes["TQQQ"])}
    aux = {"VIX": _ffill(vix, dates)}
    return Panel(dates, closes, opens, aux, _cash_rate(dtb3, dates))


# ── 인디케이터(인과적) ───────────────────────────────────────────────────────
def _sma(x, w):
    return R.sma(x, w)


def _cross_state(price, sma, band=0.0):
    """price>sma*(1+band) → True, price<sma*(1-band) → False, 그사이 유지(히스테리시스)."""
    n = len(price)
    st = [False] * n
    cur = False
    for t in range(n):
        s = sma[t]
        if s is not None:
            if price[t] > s * (1.0 + band):
                cur = True
            elif price[t] < s * (1.0 - band):
                cur = False
        st[t] = cur
    return st


def _ret_k(x, t, k):
    if t - k < 0 or x[t - k] <= 0:
        return None
    return x[t] / x[t - k] - 1.0


def week_end_flags(dates):
    n = len(dates)
    out = [False] * n
    for t in range(n):
        if t == n - 1 or dates[t].isocalendar()[:2] != dates[t + 1].isocalendar()[:2]:
            out[t] = True
    return out


# ── 통합 시뮬레이터(캘린더 리밸런싱 + 비용 + exec_lag) ───────────────────────
@dataclass
class SimResult:
    dates: list[date]
    equity: list[float]
    net: list[float]
    gross: list[float]
    trades: int
    total_cost: float

    def net_stream(self):
        return self.net[1:]


def simulate(closes, dates, targets, signal_days, *, cost, cash_rate=None,
             exec_lag=1, start=1.0) -> SimResult:
    """targets[s](신호일 s의 목표비중)를 s+exec_lag 일에 체결. 그 사이엔 드리프트(무매매).

    - 비용: 매매 노셔널 × trade_bps(심볼). FX 없음(USD 상주).
    - 잔여(1−Σw)는 현금 → cash_rate 이자.
    """
    syms = list(closes)
    n = len(dates)
    exec_src = [-1] * n
    for s in range(n):
        if signal_days[s]:
            e = s + exec_lag
            if 0 <= e < n:
                exec_src[e] = s
    val = {s: 0.0 for s in syms}
    cash = float(start)
    equity = [0.0] * n
    net = [0.0] * n
    gross = [0.0] * n
    legs = 0
    tot_cost = 0.0

    def rebal(eq_ref, tw):
        nonlocal cash, legs, tot_cost
        if eq_ref <= 0:
            return 0.0
        c = 0.0
        for s in syms:
            tgt = tw.get(s, 0.0)
            new = tgt * eq_ref
            cur = val[s]
            if abs(new - cur) > 1e-12:
                c += abs(new - cur) * cost.trade_bps(s) * 1e-4
                val[s] = new
                legs += 1
        cash = eq_ref - sum(val.values()) - c
        tot_cost += c
        return c

    eq_prev = None
    for t in range(n):
        c = 0.0
        if t == 0:
            if exec_src[0] >= 0:
                c = rebal(start, targets[exec_src[0]] or {})
            equity[0] = sum(val.values()) + cash
            eq_prev = equity[0]
            continue
        if cash_rate is not None:
            cash *= (1.0 + cash_rate[t])
        for s in syms:
            pc = closes[s][t - 1]
            if pc > 0:
                val[s] *= closes[s][t] / pc
        eq_ref = sum(val.values()) + cash
        if exec_src[t] >= 0:
            c = rebal(eq_ref, targets[exec_src[t]] or {})
        eq_now = sum(val.values()) + cash
        equity[t] = eq_now
        net[t] = (eq_now / eq_prev - 1.0) if eq_prev > 0 else 0.0
        gross[t] = net[t] + (c / eq_prev if eq_prev > 0 else 0.0)
        eq_prev = eq_now
    return SimResult(list(dates), equity, net, gross, legs, tot_cost)


# ── 지표 헬퍼 ────────────────────────────────────────────────────────────────
def _win_idx(dates, s, e):
    return [i for i, d in enumerate(dates) if s <= d <= e]


def window_cagr(equity, dates, s, e):
    idx = _win_idx(dates, s, e)
    if len(idx) < 2:
        return None
    eq = [equity[i] for i in idx]
    if eq[0] <= 0:
        return None
    days = max((dates[idx[-1]] - dates[idx[0]]).days, 1)
    return gate.cagr(eq, days)


def window_mdd(equity, dates, s, e):
    idx = _win_idx(dates, s, e)
    if len(idx) < 2:
        return None
    return gate.max_drawdown([equity[i] for i in idx])


def full_metrics(res: SimResult):
    eq = res.equity
    dates = res.dates
    days = max((dates[-1] - dates[0]).days, 1)
    yrs = days / 365.25
    cg = gate.cagr(eq, days)
    gross_eq = gate.returns_to_equity(res.gross[1:])
    gross_cg = gate.cagr(gross_eq, days) if len(gross_eq) >= 2 else cg
    return {
        "cagr": cg,
        "total_return": eq[-1] / eq[0] - 1.0 if eq[0] > 0 else None,
        "mdd": gate.max_drawdown(eq),
        "sharpe": gate.sharpe(res.net[1:]),
        "trades_yr": res.trades / yrs if yrs > 0 else 0.0,
        "cost_drag": gross_cg - cg,
        "years": yrs,
    }


def bench_cagr(sym, dates, s, e):
    """실물 심볼 매수보유 CAGR(윈도)."""
    by = {c.dt: c.close for c in _candles(sym)}
    idx = [d for d in dates if s <= d <= e and d in by]
    if len(idx) < 2:
        return None
    days = max((idx[-1] - idx[0]).days, 1)
    return gate.cagr([by[idx[0]], by[idx[-1]]], days)


# ── 전략 신호 함수(전부 (panel,dates)->(targets, signal_days), 인과적) ────────
def _daily(n):
    return [True] * n


# A1 LRS 200SMA -------------------------------------------------------------
def sig_a1(panel, dates, *, sma_win=200, lev="TQQQ"):
    q = panel["QQQ"]
    st = _cross_state(q, _sma(q, sma_win), band=0.0)
    tw = [{lev: 1.0} if st[t] else {} for t in range(len(dates))]
    return tw, _daily(len(dates))


# A2 200SMA +5/−3 버퍼 -------------------------------------------------------
def sig_a2(panel, dates, *, entry=0.05, exit=0.03, lev="TQQQ"):
    q = panel["QQQ"]
    sm = _sma(q, 200)
    n = len(dates)
    st = [False] * n
    cur = False
    for t in range(n):
        s = sm[t]
        if s is not None:
            if not cur and q[t] > s * (1.0 + entry):
                cur = True
            elif cur and q[t] < s * (1.0 - exit):
                cur = False
        st[t] = cur
    tw = [{lev: 1.0} if st[t] else {} for t in range(n)]
    return tw, _daily(n)


# A4 Alvarez 월간 4조건 레짐 -------------------------------------------------
def sig_a4(panel, dates, *, vix_thr=25.0, shadow=False):
    agg = {"SPY": 0.5, "QQQ": 0.5} if shadow else {"UPRO": 0.5, "TQQQ": 0.5}
    spx = panel["SPX"]
    sm = _sma(spx, 200)
    vix = panel["VIX"]

    def mom(x, t):
        r1, r3, r6, r12 = (_ret_k(x, t, 21), _ret_k(x, t, 63),
                           _ret_k(x, t, 126), _ret_k(x, t, 252))
        if None in (r1, r3, r6, r12):
            return None
        return (12 * r1 + 4 * r3 + 2 * r6 + r12) / 4.0

    n = len(dates)
    tw = [{} for _ in range(n)]
    me = R.month_end_flags(dates)
    for t in range(n):
        mv, mb = mom(panel["VWO"], t), mom(panel["BND"], t)
        c1 = vix[t] <= vix_thr
        c2 = sm[t] is not None and spx[t] > sm[t]
        c3 = mv is not None and mv > 0
        c4 = mb is not None and mb > 0
        nf = 4 - (c1 + c2 + c3 + c4)
        if mv is None or mb is None or sm[t] is None:
            tw[t] = {"SPY": 1.0}          # 워밍업: 방어(1x)
        elif nf == 0:
            tw[t] = dict(agg)
        elif nf in (1, 2):
            tw[t] = {"QQQ": 0.5, "SPY": 0.5}
        else:
            tw[t] = {"TLT": 1.0}
    return tw, me


# A5 In&Out Distilled Bear ---------------------------------------------------
def sig_a5(panel, dates, *, base_ret=85, lev="TQQQ", bond="TLT"):
    q = panel["QQQ"]
    rets = R.to_returns(q)
    vola = R.realized_vol(rets, 126, annualize=True)
    n = len(dates)

    def r(sym, t, period):
        return _ret_k(panel[sym], t, period)

    tw = [{} for _ in range(n)]
    state = "IN"
    out_day = None
    wait_at_out = 0
    for t in range(n):
        v = vola[t]
        if v is None:
            tw[t] = {lev: 1.0}
            continue
        period = max(1, int((1.0 - v) * base_ret))
        wait = int(v * base_ret)
        rs = [r("SLV", t, period), r("GLD", t, period), r("XLI", t, period),
              r("XLU", t, period), r("DBB", t, period), r("UUP", t, period)]
        exit_sig = (None not in rs and rs[0] < rs[1] and rs[2] < rs[3] and rs[4] < rs[5])
        if exit_sig:
            out_day = t
            state = "OUT"
            wait_at_out = wait
        if state == "OUT" and out_day is not None and (t - out_day) >= wait_at_out:
            state = "IN"
        tw[t] = {lev: 1.0} if state == "IN" else {bond: 1.0}
    return tw, _daily(n)


# C1 HFEA 55/45 quarterly ----------------------------------------------------
def sig_c1(panel, dates, *, w_upro=0.55, w_tmf=0.45, monthly=False, shadow=False):
    risk, bond = ("SPY", "TLT") if shadow else ("UPRO", "TMF")
    n = len(dates)
    tw = [{risk: w_upro, bond: w_tmf} for _ in range(n)]
    if monthly:
        flags = R.month_start_flags(dates)
    else:
        ms = R.month_start_flags(dates)
        flags = [ms[t] and dates[t].month in (1, 4, 7, 10) for t in range(n)]
    return tw, flags


# C2 TQQQ/TMF 50/50 격월 + 폭락필터 -----------------------------------------
def sig_c2(panel, dates, *, crash=-0.20, shadow=False):
    risk, bond = ("QQQ", "TLT") if shadow else ("TQQQ", "TMF")
    tqqq = panel["TQQQ"]                # 폭락 트리거는 항상 실제 TQQQ 기준
    n = len(dates)
    tw = [{} for _ in range(n)]
    me = R.month_end_flags(dates)
    in_crash = False
    ref = None
    trans = [False] * n
    for t in range(n):
        if t > 0 and tqqq[t - 1] > 0:
            rr = tqqq[t] / tqqq[t - 1] - 1.0
            if not in_crash and rr <= crash:
                in_crash = True
                ref = tqqq[t - 1]
                trans[t] = True
            elif in_crash and ref is not None and tqqq[t] >= ref:
                in_crash = False
                trans[t] = True
        tw[t] = {"IEF": 1.0} if in_crash else {risk: 0.5, bond: 0.5}
    bimonthly = [me[t] and dates[t].month % 2 == 0 for t in range(n)]
    flags = [bimonthly[t] or trans[t] for t in range(n)]
    return tw, flags


# D1 VRatio (VIX/VIX3M) ------------------------------------------------------
def sig_d1(panel, dates, *, enter=0.95, exit=1.00, short="SVXY"):
    vix, v3m = panel["VIX"], panel["VIX3M"]
    n = len(dates)
    tw = [{} for _ in range(n)]
    inpos = False
    for t in range(n):
        if v3m[t] and v3m[t] > 0:
            vr = vix[t] / v3m[t]
            if not inpos and vr < enter:
                inpos = True
            elif inpos and vr > exit:
                inpos = False
        tw[t] = {short: 1.0} if inpos else {}
    return tw, _daily(n)


# D2 VRP (VIX − HV10) --------------------------------------------------------
def sig_d2(panel, dates, *, smooth=5, short="SVXY"):
    vix = panel["VIX"]
    spx = panel["SPX"]
    logr = R.to_returns(spx, log=True)
    hv = R.realized_vol(logr, 10, annualize=True)
    n = len(dates)
    vrp_raw = [((vix[t] - hv[t] * 100.0) if hv[t] is not None else None) for t in range(n)]
    vrp = R.sma([x if x is not None else 0.0 for x in vrp_raw], smooth)
    tw = [{} for _ in range(n)]
    for t in range(n):
        ok = vrp[t] is not None and all(vrp_raw[max(0, t - smooth + 1):t + 1])
        tw[t] = {short: 1.0} if (vrp[t] is not None and vrp[t] > 0 and vrp_raw[t] is not None) else {}
    return tw, _daily(n)


# G3 BTC 추세 → IBIT ---------------------------------------------------------
def sig_g3(panel, dates, *, ns=(20, 55, 100), lev="IBIT"):
    btc = panel["BTC"]
    n = len(dates)
    states = {k: [False] * n for k in ns}
    for k in ns:
        rmax = R.rolling_max(btc, k)
        rmin = R.rolling_min(btc, max(2, k // 2))
        cur = False
        for t in range(n):
            if t > 0 and rmax[t - 1] is not None and btc[t] > rmax[t - 1]:
                cur = True
            elif t > 0 and rmin[t - 1] is not None and btc[t] < rmin[t - 1]:
                cur = False
            states[k][t] = cur
    tw = [{} for _ in range(n)]
    for t in range(n):
        frac = sum(states[k][t] for k in ns) / len(ns)
        tw[t] = {lev: frac} if frac > 0 else {}
    return tw, _daily(n)


# G5 3x 모멘텀 로테이션 ------------------------------------------------------
G5_UNIVERSE = ["TQQQ", "SOXL", "TECL", "UPRO", "TNA", "CURE", "LABU", "FAS", "DRN"]


def sig_g5(panel, dates, *, lookback=63, topn=1):
    spy = panel["SPY"]
    spy_sma = _sma(spy, 200)
    smas = {s: _sma(panel[s], 50) for s in G5_UNIVERSE}
    n = len(dates)
    we = week_end_flags(dates)
    tw = [{} for _ in range(n)]
    for t in range(n):
        if spy_sma[t] is None or spy[t] < spy_sma[t]:
            tw[t] = {}
            continue
        scored = []
        for s in G5_UNIVERSE:
            rr = _ret_k(panel[s], t, lookback)
            if rr is not None and smas[s][t] is not None and panel[s][t] > smas[s][t]:
                scored.append((rr, s))
        scored.sort(reverse=True)
        picks = [s for _, s in scored[:topn]]
        tw[t] = {s: 1.0 / len(picks) for s in picks} if picks else {}
    return tw, we


# G6 Accelerating Dual Momentum(레버리지판) ---------------------------------
def sig_g6(panel, dates, *, wins=(21, 63, 126), shadow=False):
    risk, defensive_long = ("SPY", "TLT") if shadow else ("UPRO", "TMF")
    intl, tip = "SCZ", "TIP"
    spy, scz = panel["SPY"], panel["SCZ"]
    tlt = panel["TLT"]
    k1, k3, k6 = wins

    def score(x, t):
        r1, r3, r6 = _ret_k(x, t, k1), _ret_k(x, t, k3), _ret_k(x, t, k6)
        if None in (r1, r3, r6):
            return None
        return (r1 + r3 + r6) / 3.0

    n = len(dates)
    me = R.month_end_flags(dates)
    tw = [{} for _ in range(n)]
    for t in range(n):
        ss, si = score(spy, t), score(scz, t)
        if ss is None or si is None:
            tw[t] = {"SPY": 1.0}
            continue
        if ss > si and ss > 0:
            tw[t] = {risk: 1.0}
        elif si > ss and si > 0:
            tw[t] = {intl: 1.0}
        else:
            r1_tlt, r1_tip = _ret_k(tlt, t, 21), _ret_k(panel[tip], t, 21)
            if (r1_tlt or -1) >= (r1_tip or -1):
                tw[t] = {defensive_long: 1.0}
            else:
                tw[t] = {tip: 1.0}
    return tw, me


# ── A3 9Sig 전용 시뮬레이터(자본경로 의존) ──────────────────────────────────
def simulate_9sig(panel, dates, *, growth=0.09, cost=None, cash_rate=None,
                  tqqq="TQQQ", bond="AGG", start=1.0) -> SimResult:
    """분기말 9% 신호선 리밸런싱(무적립). 매수캡·30Down·리셋 포함(카탈로그 A3)."""
    tq = panel[tqqq]
    ag = panel[bond]
    n = len(dates)
    me = R.month_end_flags(dates)
    qends = [t for t in range(n) if me[t] and dates[t].month in (3, 6, 9, 12)]
    qset = set(qends)
    v_tq = 0.6 * start
    v_ag = 0.4 * start
    signal_line = v_tq
    ignore_sells = 0
    qcloses: list[float] = []      # 분기말 TQQQ 종가 히스토리(30Down)
    prev_qclose = None
    equity = [0.0] * n
    net = [0.0] * n
    gross = [0.0] * n
    legs = 0
    tot_cost = 0.0
    tb_tq = cost.trade_bps(tqqq) * 1e-4
    tb_ag = cost.trade_bps(bond) * 1e-4
    eq_prev = None
    for t in range(n):
        c = 0.0
        if t == 0:
            equity[0] = v_tq + v_ag
            eq_prev = equity[0]
            continue
        if tq[t - 1] > 0:
            v_tq *= tq[t] / tq[t - 1]
        if ag[t - 1] > 0:
            v_ag *= ag[t] / ag[t - 1]
        if t in qset:
            port = v_tq + v_ag
            close_t = tq[t]
            recent = qcloses[-8:] + [close_t]
            thirty_down = close_t <= 0.70 * max(recent) if recent else False
            spike = (prev_qclose is not None and prev_qclose > 0
                     and close_t / prev_qclose - 1.0 >= 1.0)
            target = signal_line * (1.0 + growth)
            if v_tq > target:                 # 매도신호
                if ignore_sells > 0:
                    ignore_sells -= 1
                else:
                    sell = v_tq - target
                    c += sell * tb_tq + sell * tb_ag
                    v_tq -= sell
                    v_ag += sell
                    legs += 2
                    if v_ag > 0.30 * (v_tq + v_ag):     # Base reset
                        v_tq = 0.6 * (v_tq + v_ag)
                        v_ag = 0.4 * (v_tq + v_ag)
                        signal_line = v_tq
                        target = signal_line
            elif v_tq < target:               # 매수신호(캡)
                need = target - v_tq
                cap = 0.9 * v_ag
                buy = min(need, cap)
                port2 = v_tq + v_ag
                if (v_ag - buy) < 0.10 * port2:
                    buy = max(0.0, v_ag - 0.10 * port2)
                if buy > 0:
                    c += buy * tb_tq + buy * tb_ag
                    v_tq += buy
                    v_ag -= buy
                    legs += 2
            signal_line = target
            if thirty_down:
                ignore_sells = 2
            if spike and not thirty_down:      # Spike reset
                v_tq = 0.6 * (v_tq + v_ag)
                v_ag = 0.4 * (v_tq + v_ag)
                signal_line = v_tq
            qcloses.append(close_t)
            prev_qclose = close_t
        eq_now = v_tq + v_ag
        equity[t] = eq_now
        net[t] = (eq_now / eq_prev - 1.0) if eq_prev > 0 else 0.0
        gross[t] = net[t] + (c / eq_prev if eq_prev > 0 else 0.0)
        eq_prev = eq_now
    return SimResult(list(dates), equity, net, gross, legs, tot_cost)


# ── G1/G2 오버나이트 전용(close t 매수 → 익일 open 매도, 복리 일수익 스트림) ──
def overnight_series(panel, opens, dates, sym, *, filt_key=None, filt_win=200,
                     cost=None, cash_rate=None, free_commission=False) -> SimResult:
    """오버나이트만 보유: 진입일(close) 필터 통과 시 그날 밤 보유 → 익일 open 청산.

    일수익(익일 t) = open_t/close_{t−1} − 1 − 왕복비용(진입+청산). 미보유일은 현금수익.
    free_commission=True면 수수료 성분(≤$10 분할무료) 제외, 반호가+슬리피지만.
    """
    closes = panel[sym]
    opens_ = opens[sym]
    n = len(dates)
    flt = [True] * n
    if filt_key is not None:
        fx = panel[filt_key]
        sm = _sma(fx, filt_win)
        for t in range(n):
            flt[t] = sm[t] is not None and fx[t] > sm[t]
    rt = 2.0 * cost.trade_bps(sym) * 1e-4          # 왕복(진입+청산)
    if free_commission:
        rt = 2.0 * (cost.trade_bps(sym) - cost.commission_bps) * 1e-4
    net = [0.0] * n
    trades = 0
    for t in range(1, n):
        if flt[t - 1] and closes[t - 1] > 0 and opens_[t] > 0:
            net[t] = opens_[t] / closes[t - 1] - 1.0 - rt
            trades += 2
        else:
            net[t] = cash_rate[t] if cash_rate is not None else 0.0
    eq = [1.0]
    for t in range(1, n):
        eq.append(eq[-1] * (1.0 + net[t]))
    gross = [0.0] + [net[t] + (rt if flt[t - 1] else 0.0) for t in range(1, n)]
    return SimResult(list(dates), eq, net, gross, trades, 0.0)


# ── 벤치마크 스트림(단위자본 QQQ / TQQQ 매수보유) ────────────────────────────
def bh_returns(sym, dates):
    by = {c.dt: c.close for c in _candles(sym)}
    px = [by.get(d) for d in dates]
    out = [0.0]
    for t in range(1, len(dates)):
        a, b = px[t - 1], px[t]
        out.append(b / a - 1.0 if (a and b and a > 0) else 0.0)
    return out


HOLD_START = date(2022, 1, 1)
PUBLIC = {
    "c12_a1_lrs": date(2016, 4, 1), "c12_a2_buffer": date(2024, 1, 1),
    "c12_a3_9sig": date(2019, 1, 1), "c12_a4_alvarez": date(2024, 4, 1),
    "c12_a5_inout": date(2021, 1, 1), "c12_c1_hfea": date(2019, 9, 1),
    "c12_c2_tqqqtmf": date(2021, 1, 1), "c12_d1_vratio": date(2013, 3, 1),
    "c12_d2_vrp": date(2013, 3, 1), "c12_g1_on_tqqq": date(2008, 1, 1),
    "c12_g2_on_ibit": date(2025, 12, 1), "c12_g3_btc": date(2025, 5, 1),
    "c12_g5_rot3x": date(2022, 1, 1), "c12_g6_adm": date(2018, 6, 1),
}


def lane_a_verdict(des, hold, q_des, q_hold, mdd, plateau, des2, hold2):
    """부록 v3 판정: (a)설계·홀드아웃 CAGR>QQQ (b)이웃60%+ (c)2×비용 유지, 파산가드 MDD>−95%."""
    if mdd is None or mdd <= -0.95:
        return "FAIL(파산가드)"
    if des is None or hold is None or q_des is None or q_hold is None:
        return "N/A(설계표본부족)"
    a = des > q_des and hold > q_hold
    c = (des2 is not None and hold2 is not None and des2 > q_des and hold2 > q_hold)
    b = plateau is not None and plateau >= 0.60
    if not a:
        return "FAIL"
    if a and b and c:
        return "PASS"
    return "CONDITIONAL"


@dataclass
class Row:
    id: str
    name: str
    group: str
    m: dict
    des: float | None
    hold: float | None
    q_des: float | None
    q_hold: float | None
    q_full: float | None
    tqqq_full: float | None
    pp: float | None
    pp_q: float | None
    plateau: float | None
    des2: float | None
    hold2: float | None
    shadow_cg: float | None
    shadow_mdd: float | None
    verdict: str
    note: str
    dsr: float | None = None
    rc_p: float | None = None


def evaluate(idea_id, name, group, panel, run, primary, neighbors, *,
             shadow=None, note="", tradeable=None, do_log=True):
    dates = panel.dates
    tradeable = tradeable or list(panel.closes)
    c1 = cost_spec(tradeable)
    c2 = cost_spec(tradeable, mult=2.0)
    res = run(primary, c1)
    m = full_metrics(res)
    des = window_cagr(res.equity, dates, REAL_START, DESIGN_END)
    hold = window_cagr(res.equity, dates, HOLD_START, REAL_END)
    q_des = bench_cagr("QQQ", dates, REAL_START, DESIGN_END)
    q_hold = bench_cagr("QQQ", dates, HOLD_START, REAL_END)
    q_full = bench_cagr("QQQ", dates, dates[0], dates[-1])
    tqqq_full = bench_cagr("TQQQ", dates, dates[0], dates[-1])
    pd_ = PUBLIC.get(idea_id)
    pp_start = max(pd_, dates[0]) if pd_ else dates[0]
    pp = window_cagr(res.equity, dates, pp_start, REAL_END)
    pp_q = bench_cagr("QQQ", dates, pp_start, REAL_END)
    res2 = run(primary, c2)
    des2 = window_cagr(res2.equity, dates, REAL_START, DESIGN_END)
    hold2 = window_cagr(res2.equity, dates, HOLD_START, REAL_END)
    nb = []
    for npar in neighbors:
        rn = run(npar, c1)
        nb.append((npar, window_cagr(rn.equity, dates, dates[0], dates[-1]), rn))
    allcg = [m["cagr"]] + [cg for _, cg, _ in nb if cg is not None]
    plateau = (sum(1 for cg in allcg if q_full is not None and cg > q_full) / len(allcg)
               if allcg else None)
    shadow_cg = shadow_mdd = None
    if shadow is not None:
        rs = run(shadow, c1)
        sm = full_metrics(rs)
        shadow_cg, shadow_mdd = sm["cagr"], sm["mdd"]
    verdict = lane_a_verdict(des, hold, q_des, q_hold, m["mdd"], plateau, des2, hold2)
    dsr = rc_p = None
    if do_log and not gate.already_peeked(LEDGER, idea_id):
        bench = bh_returns("QQQ", dates)
        for npar, _cg, rn in nb:
            di = [i for i, d in enumerate(dates[1:]) if d <= DESIGN_END]
            ds = [rn.net[i + 1] for i in di]
            if len(ds) >= 2:
                um = gate_eval.unit_capital_metrics(ds, dates=[dates[i + 1] for i in di])
                gate_eval.log_evaluation(idea_id, {"neighbor": str(npar)}, LANE, "design",
                                         um, {}, ledger_path=LEDGER)
        try:
            sp = gate_eval.run_splits(idea_id, dict(primary), LANE, res.net[1:], bench[1:],
                                      dates, DESIGN_END, ledger_path=LEDGER, log=True)
            axis = sp.get("holdout") or sp.get("design") or {}
            dsr = (axis.get("unit") or {}).get("dsr")
            rc_p = (axis.get("reality_check") or {}).get("rc_pvalue")
        except gate.PeekOnceError:
            pass
    return Row(idea_id, name, group, m, des, hold, q_des, q_hold, q_full, tqqq_full,
               pp, pp_q, plateau, des2, hold2, shadow_cg, shadow_mdd, verdict, note,
               dsr, rc_p)


# ── 전략별 실행 래퍼 ─────────────────────────────────────────────────────────
def _wrun(panel, sig):
    def run(params, cost):
        tw, sd = sig(panel.full(), panel.dates, **params)
        return simulate(panel.closes, panel.dates, tw, sd, cost=cost,
                        cash_rate=panel.cash, exec_lag=1)
    return run


def _onrun(panel, sym):
    def run(params, cost):
        return overnight_series(panel.full(), panel.opens, panel.dates, sym,
                                filt_key=params.get("filt_key"),
                                filt_win=params.get("filt_win", 200), cost=cost,
                                cash_rate=panel.cash, free_commission=params.get("free", True))
    return run


def build_registry(fast=False):
    """(Row 생성용) 전략 스펙 리스트. 각 원소: dict(kwargs for evaluate)."""
    specs = []

    def add(**kw):
        specs.append(kw)

    # A1 LRS ---------------------------------------------------------------
    p = build_panel(["QQQ", "TQQQ", "BIL"])
    run = _wrun(p, sig_a1)
    nb = [{"sma_win": w} for w in ([150, 250] if fast else [150, 175, 225, 250])]
    add(idea_id="c12_a1_lrs", name="A1 LRS 200SMA", group="A", panel=p, run=run,
        primary={"sma_win": 200, "lev": "TQQQ"}, neighbors=[{**n, "lev": "TQQQ"} for n in nb],
        shadow={"sma_win": 200, "lev": "QQQ"}, tradeable=["QQQ", "TQQQ"],
        note="QQQ>SMA200 → TQQQ else 현금(BIL). 일간.")

    # A2 200SMA buffer -----------------------------------------------------
    p = build_panel(["QQQ", "TQQQ", "BIL"])
    run = _wrun(p, sig_a2)
    grid = [(0.03, 0.02), (0.04, 0.03), (0.06, 0.04), (0.05, 0.02), (0.04, 0.04), (0.06, 0.05)]
    if fast:
        grid = grid[:2]
    add(idea_id="c12_a2_buffer", name="A2 200SMA +5/−3", group="A", panel=p, run=run,
        primary={"entry": 0.05, "exit": 0.03, "lev": "TQQQ"},
        neighbors=[{"entry": e, "exit": x, "lev": "TQQQ"} for e, x in grid],
        shadow={"entry": 0.05, "exit": 0.03, "lev": "QQQ"}, tradeable=["QQQ", "TQQQ"],
        note="진입 +5% / 청산 −3% 버퍼(격자평균).")

    # A3 9Sig --------------------------------------------------------------
    p = build_panel(["TQQQ", "AGG"])

    def run_a3(params, cost):
        return simulate_9sig(p.full(), p.dates, cost=cost, cash_rate=p.cash, **params)
    gg = [0.07, 0.11] if fast else [0.07, 0.08, 0.10, 0.11]
    add(idea_id="c12_a3_9sig", name="A3 9Sig TQQQ/AGG", group="A", panel=p, run=run_a3,
        primary={"growth": 0.09, "tqqq": "TQQQ", "bond": "AGG"},
        neighbors=[{"growth": g, "tqqq": "TQQQ", "bond": "AGG"} for g in gg],
        shadow={"growth": 0.09, "tqqq": "QQQ", "bond": "AGG"}, tradeable=["TQQQ", "AGG", "QQQ"],
        note="분기 9% 신호선·매수캡·30Down·리셋. 무적립. AGG=집계채권.")
    specs[-1]["panel"] = build_panel(["TQQQ", "AGG", "QQQ"])
    p3 = specs[-1]["panel"]
    specs[-1]["run"] = lambda params, cost, _p=p3: simulate_9sig(
        _p.full(), _p.dates, cost=cost, cash_rate=_p.cash, **params)

    # A4 Alvarez -----------------------------------------------------------
    p = build_panel(["UPRO", "TQQQ", "QQQ", "SPY", "TLT"], ["VIX", "SPX", "VWO", "BND"])
    run = _wrun(p, sig_a4)
    vt = [20, 30] if fast else [20, 22, 28, 30]
    add(idea_id="c12_a4_alvarez", name="A4 Alvarez 레짐", group="A", panel=p, run=run,
        primary={"vix_thr": 25.0}, neighbors=[{"vix_thr": float(v)} for v in vt],
        shadow={"vix_thr": 25.0, "shadow": True},
        tradeable=["UPRO", "TQQQ", "QQQ", "SPY", "TLT"],
        note="월간 4조건(VIX≤25·SPX>SMA200·M(VWO)>0·M(BND)>0). 방어 TLT.")

    # A5 In&Out ------------------------------------------------------------
    p = build_panel(["TQQQ", "TLT", "QQQ", "SLV", "GLD", "XLI", "XLU", "DBB", "UUP"])
    run = _wrun(p, sig_a5)
    br = [70, 100] if fast else [70, 77, 93, 100]
    add(idea_id="c12_a5_inout", name="A5 In&Out DBear", group="A", panel=p, run=run,
        primary={"base_ret": 85, "lev": "TQQQ", "bond": "TLT"},
        neighbors=[{"base_ret": b, "lev": "TQQQ", "bond": "TLT"} for b in br],
        shadow={"base_ret": 85, "lev": "QQQ", "bond": "TLT"},
        tradeable=["TQQQ", "TLT"], note="Distilled Bear: SLV<GLD·XLI<XLU·DBB<UUP → OUT(TLT).")

    # C1 HFEA --------------------------------------------------------------
    p = build_panel(["UPRO", "TMF", "SPY", "TLT"])
    run = _wrun(p, sig_c1)
    nbc = [{"w_upro": 0.40, "w_tmf": 0.60}, {"w_upro": 0.50, "w_tmf": 0.50}]
    if not fast:
        nbc += [{"w_upro": 0.60, "w_tmf": 0.40}, {"w_upro": 0.55, "w_tmf": 0.45, "monthly": True}]
    add(idea_id="c12_c1_hfea", name="C1 HFEA 55/45", group="C", panel=p, run=run,
        primary={"w_upro": 0.55, "w_tmf": 0.45}, neighbors=nbc,
        shadow={"w_upro": 0.55, "w_tmf": 0.45, "shadow": True}, tradeable=["UPRO", "TMF"],
        note="55% UPRO / 45% TMF 분기 리밸.")

    # C2 TQQQ/TMF ----------------------------------------------------------
    p = build_panel(["TQQQ", "TMF", "IEF", "QQQ", "TLT"])
    run = _wrun(p, sig_c2)
    cc = [-0.15, -0.25] if fast else [-0.15, -0.17, -0.23, -0.25]
    add(idea_id="c12_c2_tqqqtmf", name="C2 TQQQ/TMF 50/50", group="C", panel=p, run=run,
        primary={"crash": -0.20}, neighbors=[{"crash": c} for c in cc],
        shadow={"crash": -0.20, "shadow": True}, tradeable=["TQQQ", "TMF", "IEF"],
        note="격월 50/50 + TQQQ −20% 폭락 시 IEF.")

    # D1 VRatio ------------------------------------------------------------
    p = build_panel(["SVXY", "VIXY", "BIL"], ["VIX", "VIX3M"])
    run = _wrun(p, sig_d1)
    en = [0.90, 1.00] if fast else [0.90, 0.93, 0.97, 1.00]
    add(idea_id="c12_d1_vratio", name="D1 VRatio SVXY", group="D", panel=p, run=run,
        primary={"enter": 0.95, "exit": 1.00, "short": "SVXY"},
        neighbors=[{"enter": e, "exit": 1.00, "short": "SVXY"} for e in en],
        shadow=None, tradeable=["SVXY", "VIXY"],
        note="VIX/VIX3M<0.95 → SVXY(−0.5x). 실 SVXY 2018-09~(−0.5x era).")

    # D2 VRP ---------------------------------------------------------------
    p = build_panel(["SVXY", "BIL"], ["VIX", "SPX"])
    run = _wrun(p, sig_d2)
    sm = [3, 10] if fast else [3, 4, 7, 10]
    add(idea_id="c12_d2_vrp", name="D2 VRP SVXY", group="D", panel=p, run=run,
        primary={"smooth": 5, "short": "SVXY"},
        neighbors=[{"smooth": s, "short": "SVXY"} for s in sm],
        shadow=None, tradeable=["SVXY"], note="SMA5(VIX−HV10)>0 → SVXY.")

    # G1 Overnight TQQQ ----------------------------------------------------
    p = build_panel(["TQQQ", "QQQ", "BIL"])
    run = _onrun(p, "TQQQ")
    nbg = [{"filt_key": None, "free": True}] if fast else [
        {"filt_key": None, "free": True}, {"filt_key": "QQQ", "filt_win": 150, "free": True},
        {"filt_key": "QQQ", "filt_win": 250, "free": True}, {"filt_key": "QQQ", "free": False}]
    add(idea_id="c12_g1_on_tqqq", name="G1 오버나이트 TQQQ", group="G", panel=p, run=run,
        primary={"filt_key": "QQQ", "filt_win": 200, "free": True}, neighbors=nbg,
        shadow=None, tradeable=["TQQQ"],
        note="close 매수→익일 open 매도, QQQ>SMA200. ≤$10 분할무료(micro) 필수.")
    p_g1 = p

    def run_g1_shadow(params, cost, _p=p_g1):
        return overnight_series(_p.full(), _p.opens, _p.dates, "QQQ",
                                filt_key="QQQ", filt_win=200, cost=cost_spec(["QQQ"]),
                                cash_rate=_p.cash, free_commission=True)
    specs[-1]["shadow"] = "G1SHADOW"
    specs[-1]["_shadow_run"] = run_g1_shadow

    # G2 Overnight IBIT ----------------------------------------------------
    p = build_panel(["IBIT"], ["BTC"])
    run = _onrun(p, "IBIT")
    nbg2 = [{"filt_key": None, "free": True}] if fast else [
        {"filt_key": None, "free": True}, {"filt_key": "BTC", "filt_win": 30, "free": True},
        {"filt_key": "BTC", "filt_win": 70, "free": True}]
    add(idea_id="c12_g2_on_ibit", name="G2 오버나이트 IBIT", group="G", panel=p, run=run,
        primary={"filt_key": "BTC", "filt_win": 50, "free": True}, neighbors=nbg2,
        shadow=None, tradeable=["IBIT"], note="IBIT close→open, BTC>SMA50. IBIT 2024-01~(홀드아웃 전용).")

    # G3 BTC trend ---------------------------------------------------------
    p = build_panel(["IBIT"], ["BTC"])
    run = _wrun(p, sig_g3)
    ns = [(15, 40, 80), (25, 70, 120)] if fast else [(15, 40, 80), (25, 70, 120), (10, 30, 60), (30, 90, 150)]
    add(idea_id="c12_g3_btc", name="G3 BTC→IBIT", group="G", panel=p, run=run,
        primary={"ns": (20, 55, 100), "lev": "IBIT"},
        neighbors=[{"ns": x, "lev": "IBIT"} for x in ns],
        shadow=None, tradeable=["IBIT"], note="Donchian(20/55/100) 앙상블 프랙션 → IBIT. 2024-01~.")

    # G5 3x rotation -------------------------------------------------------
    p = build_panel(G5_UNIVERSE + ["SPY"])
    run = _wrun(p, sig_g5)
    nbg5 = [{"lookback": 42, "topn": 1}, {"lookback": 84, "topn": 2}]
    if not fast:
        nbg5 += [{"lookback": 63, "topn": 2}, {"lookback": 42, "topn": 2}]
    add(idea_id="c12_g5_rot3x", name="G5 3x 모멘텀 로테이션", group="G", panel=p, run=run,
        primary={"lookback": 63, "topn": 1}, neighbors=nbg5,
        shadow=None, tradeable=list(G5_UNIVERSE),
        note="주간: SPY>SMA200 & top-1 3x by 63일수익 & close>SMA50.")

    # G6 Accelerating Dual Momentum ---------------------------------------
    p = build_panel(["UPRO", "SCZ", "TMF", "TIP", "SPY", "TLT"])
    run = _wrun(p, sig_g6)
    nbg6 = [{"wins": (21, 42, 84)}, {"wins": (42, 84, 168)}]
    if not fast:
        nbg6 += [{"wins": (21, 63, 189)}, {"wins": (10, 42, 126)}]
    add(idea_id="c12_g6_adm", name="G6 Accel Dual Mom", group="G", panel=p, run=run,
        primary={"wins": (21, 63, 126)}, neighbors=nbg6,
        shadow={"wins": (21, 63, 126), "shadow": True},
        tradeable=["UPRO", "SCZ", "TMF", "TIP"],
        note="월간 (R1+R3+R6)/3: SPY vs SCZ → UPRO/SCZ, else TMF/TIP.")

    return specs


# ── 장기 합성 A1/A2 (1986~2026) ──────────────────────────────────────────────
def synth_run(sig, params):
    p = build_synth_panel()
    tw, sd = sig(p.full(), p.dates, **params)
    cost = cost_spec(["QQQ", "TQQQ"])
    res = simulate(p.closes, p.dates, tw, sd, cost=cost, cash_rate=p.cash, exec_lag=1)
    dates = p.dates
    d0, d1 = dates[0], dates[-1]
    return {
        "cagr": full_metrics(res)["cagr"], "mdd": gate.max_drawdown(res.equity),
        "des": window_cagr(res.equity, dates, d0, SYN_DESIGN_END),
        "hold": window_cagr(res.equity, dates, date(2009, 1, 1), d1),
        "qqq": bench_cagr_syn(p, "QQQ", d0, d1),
        "tqqq": bench_cagr_syn(p, "TQQQ", d0, d1),
        "start": d0, "end": d1,
    }


def bench_cagr_syn(panel, sym, s, e):
    idx = [i for i, d in enumerate(panel.dates) if s <= d <= e]
    if len(idx) < 2:
        return None
    eq = [panel.closes[sym][i] for i in idx]
    days = max((panel.dates[idx[-1]] - panel.dates[idx[0]]).days, 1)
    return gate.cagr(eq, days)


# ── 리포트 ───────────────────────────────────────────────────────────────────
def _pct(v, nd=1):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v * 100:+.{nd}f}%"


def _f(v, nd=2):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{nd}f}"


def write_report(rows: list[Row], synth: dict):
    L = []
    L.append("# Cycle 12 — 공격형 수익 레인(Lane A): 추세·변동성 배치 평가\n")
    L.append(f"> 생성 {date.today().isoformat()} · 리서치 전용(코드/실거래 변경 없음) · "
             "src 미수정 · 원장 lane \"A\"\n")
    L.append("## 사전등록 (결과 보기 전 확정)\n")
    L.append("- **규칙 원전**: `docs/aggressive_strategy_catalog.md` A/C/D/G군을 규칙 그대로 1회 평가"
             "(재튜닝 금지). 공개일 이후를 진짜 OOS로 표기.\n")
    L.append("- **데이터**: 실물 ETF 2016-09~2026-09(Nasdaq 폴백, 시가 포함). VIX=FRED VIXCLS, "
             "VIX3M=CBOE keyless CSV, BTC=FRED CBBTCUSD(2014~). 합성 3x/2x=FRED NASDAQ100(1986~).\n")
    L.append("- **분할**: 실물 설계 2016-09~2021-12 / 홀드아웃 2022-01~2026-09. 합성 설계 1986~2008 / 홀드아웃 2009~.\n")
    L.append("- **체결**: 신호 close t → close t+1(exec_lag=1). G1/G2 오버나이트=close 매수→익일 실 open 매도.\n")
    L.append("- **비용**: 토스 0.1%/side(10bp) + 반호가(메가1·레버2·변동성/단일종목15bp) + 슬리피지 5bp. "
             "2× 스트레스·마이크로(≤$10 분할무료) 병기.\n")
    L.append("- **Lane A 판정(부록 v3)**: (a) 설계·홀드아웃 **모두** CAGR>QQQ, (b) 이웃(±20~50%) 60%+ CAGR>QQQ, "
             "(c) 2×비용에서도 (a), 파산가드 전표본 MDD>−95%. DSR/RC/SPA는 **보고만**.\n")
    L.append("- **데이터 한계(정직 고지)**: Nasdaq 폴백은 NYSE Arca ETF(SPY·TLT·IEF·UPRO·TMF·SVXY 등)의 "
             "배당재투자를 누락(분할만 반영) → 채권·고배당 레그 총수익 소폭 과소. 실 SVXY 2018-09~(−0.5x era, "
             "2018-02 볼마게돈 제외). IBIT 2024-01·CONL 2022-08·MSTU 2024-09·FNGU 2025-02 = 표본 짧음.\n")

    L.append("\n## 1. 헤드라인 (실물, 순비용, 전표본 2016-09~2026-09)\n")
    L.append("| ID | 전략 | CAGR | 총수익 | MDD | Sharpe | 거래/년 | 비용drag | 설계 | 홀드아웃 | 판정 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in rows:
        L.append(f"| {r.id.replace('c12_','')} | {r.name} | {_pct(r.m['cagr'])} | "
                 f"{_pct(r.m['total_return'],0)} | {_pct(r.m['mdd'])} | {_f(r.m['sharpe'])} | "
                 f"{_f(r.m['trades_yr'],0)} | {_pct(r.m['cost_drag'])} | {_pct(r.des)} | "
                 f"{_pct(r.hold)} | {r.verdict} |")
    L.append("\n벤치(전표본): QQQ B&H "
             f"{_pct(rows[0].q_full)}, TQQQ B&H {_pct(rows[0].tqqq_full)}. "
             "설계/홀드아웃 QQQ는 아래 표.\n")

    L.append("\n## 2. Lane A 판정 근거 (vs QQQ 분할 · 이웃평탄 · 2×비용 · 1x섀도 · OOS)\n")
    L.append("| ID | 설계 CAGR (QQQ) | 홀드아웃 CAGR (QQQ) | 이웃>QQQ | 2×비용 설계/홀드 | 1x섀도 CAGR/MDD | 공개후 OOS (QQQ) | DSR/RCp | 판정 |")
    L.append("|---|---|---|---:|---|---|---|---|---|")
    for r in rows:
        sh = (f"{_pct(r.shadow_cg)}/{_pct(r.shadow_mdd)}" if r.shadow_cg is not None else "N/A")
        L.append(f"| {r.id.replace('c12_','')} | {_pct(r.des)} ({_pct(r.q_des)}) | "
                 f"{_pct(r.hold)} ({_pct(r.q_hold)}) | {_pct(r.plateau,0)} | "
                 f"{_pct(r.des2)}/{_pct(r.hold2)} | {sh} | {_pct(r.pp)} ({_pct(r.pp_q)}) | "
                 f"{_f(r.dsr)}/{_f(r.rc_p,3)} | {r.verdict} |")

    L.append("\n## 3. 장기 합성 A1/A2 (FRED NASDAQ100 1986~2026, 합성 3x TQQQ)\n")
    L.append("| 전략 | CAGR | MDD | 설계 1986–2008 | 홀드아웃 2009– | 합성 QQQ | 합성 TQQQ B&H |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for k, nm in (("A1", "A1 LRS 200SMA"), ("A2", "A2 200SMA +5/−3")):
        s = synth[k]
        L.append(f"| {nm} | {_pct(s['cagr'])} | {_pct(s['mdd'])} | {_pct(s['des'])} | "
                 f"{_pct(s['hold'])} | {_pct(s['qqq'])} | {_pct(s['tqqq'])} |")
    L.append(f"\n합성 구간 {synth['A1']['start']}~{synth['A1']['end']}. 합성 3x는 일일리셋·조달비용"
             "(DTB3+0.5%)·경비 0.95% 반영(닷컴·GFC 포함).\n")

    L.append("\n## 4. 전략별 규칙·주석\n")
    for r in rows:
        L.append(f"- **{r.id.replace('c12_','')} {r.name}** — {r.note}")

    L.append("\n## 5. 정직한 해석\n")
    passes = [r for r in rows if r.verdict == "PASS"]
    conds = [r for r in rows if r.verdict.startswith("COND")]
    L.append(f"- **PASS({len(passes)})**: " + (", ".join(r.name for r in passes) or "없음") +
             " — 설계·홀드아웃 모두 QQQ 초과 + 이웃평탄 + 2×비용 견딤.")
    L.append(f"- **CONDITIONAL({len(conds)})**: " + (", ".join(r.name for r in conds) or "없음") +
             " — (a) 충족하나 이웃평탄 또는 2×비용에서 흔들림.")
    L.append("- 나머지는 홀드아웃(2022~)에서 QQQ 미달 또는 표본부족. 레버리지 리스크패리티(C군)·"
             "변동성 숏(D군)은 2022 동반하락/−0.5x SVXY로 홀드아웃 약세가 구조적.")
    L.append("- **최종 판정은 포워드 페이퍼(부록 v3-4)**: PASS는 공격형 페이퍼 랩 3개월+ 실시세 검증 후 사용자 승인 시 실거래.")
    L.append("- 오버나이트(G1/G2)는 ≤$10 분할무료 없이는 원천 불가(bps 비용에서 CAGR 음수). 마이크로 계좌 전용 에지.")

    REPORT.write_text("\n".join(L) + "\n")


def main(fast=False):
    print("== c12 Lane A 배치 평가 ==")
    specs = build_registry(fast=fast)
    rows: list[Row] = []
    for sp in specs:
        shadow_run = sp.pop("_shadow_run", None)
        shadow_marker = sp.get("shadow")
        if shadow_marker == "G1SHADOW":
            sp["shadow"] = None
        r = evaluate(**sp, do_log=not fast)
        if shadow_run is not None:              # 오버나이트 1x 섀도(구조가 달라 별도 실행)
            rs = shadow_run({}, cost_spec(["QQQ"]))
            sm = full_metrics(rs)
            r.shadow_cg, r.shadow_mdd = sm["cagr"], sm["mdd"]
        rows.append(r)
        print(f"  {r.id:18s} {r.verdict:16s} CAGR {_pct(r.m['cagr'])} des {_pct(r.des)} hold {_pct(r.hold)}")
    print("  합성 A1/A2 …")
    synth = {"A1": synth_run(sig_a1, {"sma_win": 200, "lev": "TQQQ"}),
             "A2": synth_run(sig_a2, {"entry": 0.05, "exit": 0.03, "lev": "TQQQ"})}
    write_report(rows, synth)
    RESULTS_JSON.write_text(json.dumps(
        {r.id: {"verdict": r.verdict, "cagr": r.m["cagr"], "mdd": r.m["mdd"],
                "sharpe": r.m["sharpe"], "des": r.des, "hold": r.hold,
                "plateau": r.plateau, "shadow_cg": r.shadow_cg} for r in rows}, indent=2))
    print(f"리포트 → {REPORT}")
    return rows, synth


if __name__ == "__main__":
    main(fast="--fast" in sys.argv)
