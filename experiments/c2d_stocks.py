#!/usr/bin/env python3
"""c2d — 단일종목 횡단면 / 어텐션 전략 (레인2, 사이클2 아이디어군 `c2d`).

사양: docs/gate_v2_spec.md(레인2 §8.2), 규약: experiments/README.md.
데이터: data/_hist_cache 의 일봉(총수익 근사, Nasdaq 폴백은 배당 누락 가능 — 리포트에 명시).

이 스크립트는 **src/ 를 수정하지 않고 소비만** 한다. research.CostSpec(비용 티어),
research 헬퍼(sma/rolling_max/캘린더/lookahead_guard), gate/gate_eval(판정·원장)을 재사용하되,
포트폴리오 회전(월간/주간 리밸런스)의 **다음 시가 체결 + 일별 MTM 자본곡선**은 여기서
자체 시뮬레이터(`simulate_portfolio`)로 구현한다. run_weights는 목표비중을 매일 재부과해
월간 회전에 부적합(일별 회전비용 과대)하기 때문이다.

사전등록(실행 전 고정, 튜닝 금지):
  1) 12-1 모멘텀 top3/top5, 월간, 다음시가, 추세필터(QQQ>SMA200 else 현금) on/off.
  2) 52주 신고가 근접(close/252d-high) top3, 월간.
  3) 비정상 거래량 어텐션 스윙: RVOL>3 & 당일 +3% & 종가 고가근접 → 다음시가 매수, N=5/10 보유.
  4) 단기 반전: 주간, top-50 달러거래대금 중 5일 최악 3종목 매수(QQQ>SMA200), 1주 보유.
  5) (창의) 잔차 모멘텀: QQQ 베타(252d) 제거한 잔차의 12-1 누적 top3, 월간.
평탄성 이웃(±20~50%)만 함께 원장에 적재, 그리드서치 금지.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata, research  # noqa: E402
from toss_trader.research import CostSpec  # noqa: E402
import gate_eval  # noqa: E402
import fetch_history as fh  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
DESIGN_END = date(2021, 12, 31)          # 설계 2016-09~2021-12, 홀드아웃 2022~2026 (레인2 10년표본)
START = date(2016, 9, 1)
END = date(2026, 9, 30)

BENCH = "QQQ"                            # 벤치마크 = QQQ B&H (task 지정)

# 유동성 티어(반호가): 메가/대형 = 3bp, 나머지 핫/변동성 = 15bp (보수적). ETF/QQQ = 1bp.
LARGE_CAP = {
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "AVGO", "TSLA", "NFLX",
    "ADBE", "CRM", "ORCL", "CSCO", "QCOM", "TXN", "INTC", "AMD", "INTU", "AMGN",
    "PEP", "COST", "TMUS", "CMCSA", "HON", "SBUX", "GILD", "BKNG", "ADP", "ISRG",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "XOM", "CVX", "WMT", "DIS", "NKE",
    "KO", "PFE", "MRK", "T", "VZ", "BA", "CAT", "GE", "MU", "AMAT", "NOW", "PYPL",
}


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로딩: 기준 거래일(달력)에 정렬. 상장 전은 첫 실측가로 평탄 채움(비중 0으로만 사용).
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Panel:
    dates: list[date]
    close: dict[str, list[float]] = field(default_factory=dict)
    open: dict[str, list[float]] = field(default_factory=dict)
    high: dict[str, list[float]] = field(default_factory=dict)
    low: dict[str, list[float]] = field(default_factory=dict)
    vol: dict[str, list[float]] = field(default_factory=dict)
    first_idx: dict[str, int] = field(default_factory=dict)   # 첫 실측 인덱스(상장/캐시 시작)
    tier: dict[str, str] = field(default_factory=dict)
    universe: list[str] = field(default_factory=list)         # 선택 가능 종목(벤치/필터 제외)


def _tier_for(sym: str) -> str:
    if sym in ("QQQ", "SPY") or sym.startswith("XL"):
        return research.TIER_ETF
    return research.TIER_LARGE_CAP if sym in LARGE_CAP else research.TIER_SMALL_HOT


def _cached_symbols() -> set[str]:
    return {p.stem for p in histdata.CACHE_DIR.glob("*.json")}


def load_panel(symbols: list[str], *, calendar_symbol: str = "QQQ",
               start: date = START, end: date = END,
               adjusted: bool = True) -> Panel:
    """캐시된 심볼을 기준 거래일(calendar_symbol)에 정렬한 Panel 로 로드.

    상장 전(첫 실측 이전) 봉은 첫 실측 종가/시가로 평탄 채움, 거래량 0 → 비중 0으로만 참조돼
    수익에 기여하지 않는다. 캐시 없는 심볼은 조용히 제외한다(유니버스는 실제 가용분).
    """
    cached = _cached_symbols()
    cal = histdata.load_symbol(calendar_symbol, start=start, end=end, adjusted=adjusted)
    cal_dates = [c.dt for c in cal]
    di = {d: i for i, d in enumerate(cal_dates)}
    n = len(cal_dates)
    p = Panel(dates=cal_dates)

    syms = [s for s in symbols if s.replace("^", "_") in cached]
    if calendar_symbol not in syms:
        syms = [calendar_symbol] + syms
    for s in syms:
        try:
            candles = histdata.load_symbol(s, start=start, end=end, adjusted=adjusted)
        except Exception:  # noqa: BLE001
            continue
        candles = [c for c in candles if c.dt in di]
        if len(candles) < 60:
            continue
        by = {c.dt: c for c in candles}
        c_close = [0.0] * n; c_open = [0.0] * n
        c_high = [0.0] * n; c_low = [0.0] * n; c_vol = [0.0] * n
        first = None
        last_c = None
        for i, d in enumerate(cal_dates):
            c = by.get(d)
            if c is not None and c.close > 0:
                if first is None:
                    first = i
                c_close[i] = c.close; c_open[i] = c.open if c.open > 0 else c.close
                c_high[i] = c.high if c.high > 0 else c.close
                c_low[i] = c.low if c.low > 0 else c.close
                c_vol[i] = c.volume
                last_c = c.close
            else:
                # 실측 없음: 직전 유효가로 채움(상장 후 결측), 상장 전은 첫 실측가로 뒤채움.
                c_close[i] = last_c if last_c else 0.0
                c_open[i] = c_high[i] = c_low[i] = c_close[i]
                c_vol[i] = 0.0
        if first is None:
            continue
        # 상장 전 구간을 첫 실측 종가로 평탄 채움(양수 보장; 비중 0으로만 참조).
        fc = c_close[first]
        for i in range(first):
            c_close[i] = c_open[i] = c_high[i] = c_low[i] = fc
        p.close[s] = c_close; p.open[s] = c_open; p.high[s] = c_high
        p.low[s] = c_low; p.vol[s] = c_vol; p.first_idx[s] = first
        p.tier[s] = _tier_for(s)
    p.universe = [s for s in p.close if s != calendar_symbol and s != BENCH]
    return p


def cost_for(panel: Panel, *, commission_bps: float = 25.0, slippage_bps: float = 5.0,
             fx_bps: float = 20.0, mult: float = 1.0) -> CostSpec:
    """패널 티어에서 CostSpec 구성(수수료 25bp 표준). mult 로 비용 스트레스."""
    c = CostSpec.from_tiers(panel.tier, commission_bps=commission_bps,
                            slippage_bps=slippage_bps, fx_bps=fx_bps)
    return c.stress(mult) if mult != 1.0 else c


# ─────────────────────────────────────────────────────────────────────────────
# 캘린더 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def week_end_flags(dates: list[date]) -> list[bool]:
    """각 날짜가 해당 ISO주의 마지막 거래일인가(다음 거래일이 다른 주이거나 마지막)."""
    n = len(dates)
    out = [False] * n
    for t in range(n):
        if t == n - 1:
            out[t] = True
        else:
            a = dates[t].isocalendar()
            b = dates[t + 1].isocalendar()
            out[t] = (a[0], a[1]) != (b[0], b[1])
    return out


def _eligible(panel: Panel, s: str, t: int, need: int) -> bool:
    """t 시점에 심볼 s 가 need 거래일 이상의 실측 이력을 가졌는가(신규상장 자연 편입)."""
    fi = panel.first_idx.get(s)
    return fi is not None and t - fi >= need


# ─────────────────────────────────────────────────────────────────────────────
# 결정(decide_*): 리밸런스일 인덱스 → 목표비중(그 날 종가까지의 정보만 사용, 인과적).
#   실행은 simulate_portfolio 가 '다음 시가'에 한다.
# ─────────────────────────────────────────────────────────────────────────────
def _equal_weight(names: list[str]) -> dict[str, float]:
    if not names:
        return {}
    w = 1.0 / len(names)
    return {s: w for s in names}


def _trend_ok(panel: Panel, t: int, sma_bench: list, use_filter: bool) -> bool:
    if not use_filter:
        return True
    s = sma_bench[t]
    return s is not None and panel.close[BENCH][t] > s


def decide_momentum(panel: Panel, *, topk: int = 3, lookback: int = 252, skip: int = 21,
                    use_filter: bool = False) -> dict[int, dict[str, float]]:
    """12-1 모멘텀: mom = close[m-skip]/close[m-lookback]-1, 월말 결정 top-k EW."""
    sma_b = research.sma(panel.close[BENCH], 200)
    me = research.month_end_flags(panel.dates)
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(me):
        if not f:
            continue
        if not _trend_ok(panel, t, sma_b, use_filter):
            out[t] = {}
            continue
        scored = []
        for s in panel.universe:
            if not _eligible(panel, s, t, lookback):
                continue
            c0 = panel.close[s][t - lookback]
            c1 = panel.close[s][t - skip]
            if c0 > 0:
                scored.append((c1 / c0 - 1.0, s))
        scored.sort(reverse=True)
        out[t] = _equal_weight([s for _, s in scored[:topk]])
    return out


def decide_high_proximity(panel: Panel, *, topk: int = 3, window: int = 252
                          ) -> dict[int, dict[str, float]]:
    """52주 신고가 근접: ratio = close[m]/max(close[m-window+1..m]), 월말 top-k EW."""
    me = research.month_end_flags(panel.dates)
    rmax = {s: research.rolling_max(panel.close[s], window) for s in panel.universe}
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(me):
        if not f:
            continue
        scored = []
        for s in panel.universe:
            if not _eligible(panel, s, t, window):
                continue
            hi = rmax[s][t]
            c = panel.close[s][t]
            if hi and hi > 0:
                scored.append((c / hi, s))
        scored.sort(reverse=True)
        out[t] = _equal_weight([s for _, s in scored[:topk]])
    return out


def decide_reversal(panel: Panel, *, topk: int = 3, lookback: int = 5, top_dollar: int = 50,
                    dv_window: int = 21, use_filter: bool = True
                    ) -> dict[int, dict[str, float]]:
    """단기반전: 주말, top-달러거래대금 중 5일 최악 top-k 매수(QQQ>SMA200 else 현금)."""
    sma_b = research.sma(panel.close[BENCH], 200)
    we = week_end_flags(panel.dates)
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(we):
        if not f:
            continue
        if not _trend_ok(panel, t, sma_b, use_filter):
            out[t] = {}
            continue
        pool = []
        for s in panel.universe:
            if not _eligible(panel, s, t, max(lookback, dv_window)):
                continue
            dv = sum(panel.close[s][t - k] * panel.vol[s][t - k] for k in range(dv_window)) / dv_window
            pool.append((dv, s))
        pool.sort(reverse=True)
        cand = [s for _, s in pool[:top_dollar]]
        scored = []
        for s in cand:
            c0 = panel.close[s][t - lookback]
            if c0 > 0:
                scored.append((panel.close[s][t] / c0 - 1.0, s))
        scored.sort()                         # 오름차순 → 최악(가장 하락)이 앞
        out[t] = _equal_weight([s for _, s in scored[:topk]])
    return out


def decide_resid_momentum(panel: Panel, *, topk: int = 3, lookback: int = 252, skip: int = 21
                          ) -> dict[int, dict[str, float]]:
    """잔차 모멘텀: [m-lookback, m-skip] 창에서 QQQ 베타 제거 후 잔차 누적수익 top-k EW."""
    me = research.month_end_flags(panel.dates)
    bench_c = panel.close[BENCH]
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(me):
        if not f:
            continue
        a, b = t - lookback, t - skip          # 창 [a, b]
        if a < 1:
            continue
        rb = [bench_c[i] / bench_c[i - 1] - 1.0 for i in range(a, b + 1) if bench_c[i - 1] > 0]
        mb = sum(rb) / len(rb) if rb else 0.0
        var_b = sum((x - mb) ** 2 for x in rb)
        scored = []
        for s in panel.universe:
            if not _eligible(panel, s, t, lookback):
                continue
            cs = panel.close[s]
            rs = [cs[i] / cs[i - 1] - 1.0 for i in range(a, b + 1) if cs[i - 1] > 0]
            m = min(len(rs), len(rb))
            if m < 60 or var_b <= 0:
                continue
            rs_, rb_ = rs[:m], rb[:m]
            ms = sum(rs_) / m
            cov = sum((rs_[i] - ms) * (rb_[i] - mb) for i in range(m))
            beta = cov / var_b
            resid_cum = sum(rs_[i] - beta * rb_[i] for i in range(m))
            scored.append((resid_cum, s))
        scored.sort(reverse=True)
        out[t] = _equal_weight([s for _, s in scored[:topk]])
    return out


# ── 결정 → 일별 목표비중(guard용, 인과적 전방채움) ───────────────────────────
def decisions_to_daily(decisions: dict[int, dict[str, float]], n: int) -> list[dict[str, float]]:
    """리밸런스 결정을 일별 목표비중으로 전방채움(i의 비중 = i 이하 마지막 결정). 인과적."""
    out: list[dict[str, float]] = [{} for _ in range(n)]
    cur: dict[str, float] = {}
    keys = sorted(decisions)
    ki = 0
    for i in range(n):
        while ki < len(keys) and keys[ki] <= i:
            cur = decisions[keys[ki]]
            ki += 1
        out[i] = dict(cur)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 포트폴리오 시뮬레이터: 결정(종가 t) → 다음 시가(t+1) 체결, 일별 종가 MTM.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SimResult:
    dates: list[date]
    equity: list[float]
    returns: list[float]              # 일별 순수익([0]=0)
    turnover: list[float]             # 일별 회전(체결노셔널/시가자본)
    total_cost: float
    n_rebalances: int

    @property
    def net_stream(self) -> list[float]:
        return self.returns[1:]

    def turnover_per_year(self) -> float:
        yrs = max((self.dates[-1] - self.dates[0]).days / 365.0, 1e-9)
        return sum(self.turnover) / yrs


def simulate_portfolio(panel: Panel, decisions: dict[int, dict[str, float]], cost: CostSpec,
                       *, cash_rate: list[float] | None = None,
                       start_equity: float = 1.0) -> SimResult:
    """결정을 다음 시가에 체결하고 종가로 일별 MTM. 상장전(비중0) 심볼은 기여 없음."""
    syms = list(panel.close)
    n = len(panel.dates)
    shares = {s: 0.0 for s in syms}
    cash = float(start_equity)
    equity = [0.0] * n
    returns = [0.0] * n
    turnover = [0.0] * n
    total_cost = 0.0
    pending: dict[str, float] | None = None
    prev_eq = start_equity

    for t in range(n):
        if cash_rate is not None and t > 0:
            cash *= (1.0 + cash_rate[t])
        # 다음 시가 체결
        if pending is not None:
            op = {s: panel.open[s][t] for s in syms}
            eq_open = cash + sum(shares[s] * op[s] for s in syms)
            if eq_open > 0:
                traded = 0.0
                c_paid = 0.0
                new_shares = dict(shares)
                for s in syms:
                    tgt_val = pending.get(s, 0.0) * eq_open
                    cur_val = shares[s] * op[s]
                    dv = tgt_val - cur_val
                    if abs(dv) > 1e-12 and op[s] > 0:
                        traded += abs(dv)
                        c_paid += abs(dv) * cost.trade_bps(s) * research.BPS
                        new_shares[s] = tgt_val / op[s]
                shares = new_shares
                cash = eq_open - sum(shares[s] * op[s] for s in syms) - c_paid
                total_cost += c_paid
                turnover[t] = traded / eq_open
            pending = None
        # 종가 MTM
        eq_close = cash + sum(shares[s] * panel.close[s][t] for s in syms)
        equity[t] = eq_close
        returns[t] = (eq_close / prev_eq - 1.0) if prev_eq > 0 else 0.0
        prev_eq = eq_close
        if t in decisions:
            pending = decisions[t]

    return SimResult(dates=panel.dates, equity=equity, returns=returns, turnover=turnover,
                     total_cost=total_cost, n_rebalances=len(decisions))


# ─────────────────────────────────────────────────────────────────────────────
# 어텐션 스윙(전략3): 이벤트 → run_trades(거래단위 통계). 인과적 신호.
# ─────────────────────────────────────────────────────────────────────────────
def attention_trades(panel: Panel, *, hold: int = 5, rvol_k: float = 3.0,
                     up: float = 0.03, near_high: float = 0.7, avg_win: int = 50,
                     cost: CostSpec | None = None) -> research.TradesResult:
    """RVOL>rvol_k & 당일수익>up & 종가 고가근접>near_high → 다음시가 매수, hold일 보유."""
    cost = cost or cost_for(panel)
    trades: list[research.Trade] = []
    n = len(panel.dates)
    for s in panel.universe:
        cl, op, hi, lo, vo = (panel.close[s], panel.open[s], panel.high[s],
                              panel.low[s], panel.vol[s])
        for t in range(n):
            if not _eligible(panel, s, t, avg_win):
                continue
            if t + 1 + hold >= n:
                break
            avgv = sum(vo[t - avg_win:t]) / avg_win
            if avgv <= 0:
                continue
            rvol = vo[t] / avgv
            ret1 = (cl[t] / cl[t - 1] - 1.0) if cl[t - 1] > 0 else 0.0
            rng = (cl[t] - lo[t]) / (hi[t] - lo[t]) if hi[t] > lo[t] else 0.0
            if rvol > rvol_k and ret1 > up and rng > near_high:
                e_i, x_i = t + 1, t + 1 + hold
                if op[e_i] > 0 and op[x_i] > 0:
                    trades.append(research.Trade(
                        entry_dt=panel.dates[e_i], entry_price=op[e_i],
                        exit_dt=panel.dates[x_i], exit_price=op[x_i],
                        notional=1.0, symbol=s))
    return research.run_trades(trades, cost=cost, capital=1.0)


def attention_signal_fn(panel: Panel, *, rvol_k: float = 3.0, up: float = 0.03,
                        near_high: float = 0.7, avg_win: int = 50):
    """guard용: 이벤트 발생일에 해당 종목 비중 1.0(인과 — OHLCV≤t만 참조)."""
    def fn(panel_closes, dates):
        n = len(dates)
        out = [{} for _ in range(n)]
        for s in panel.universe:
            cl = panel_closes[s]
            hi, lo, vo = panel.high[s], panel.low[s], panel.vol[s]
            for t in range(n):
                if not _eligible(panel, s, t, avg_win):
                    continue
                avgv = sum(vo[t - avg_win:t]) / avg_win if t >= avg_win else 0.0
                if avgv <= 0:
                    continue
                rvol = vo[t] / avgv
                ret1 = (cl[t] / cl[t - 1] - 1.0) if cl[t - 1] > 0 else 0.0
                rng = (cl[t] - lo[t]) / (hi[t] - lo[t]) if hi[t] > lo[t] else 0.0
                if rvol > rvol_k and ret1 > up and rng > near_high:
                    out[t] = dict(out[t]); out[t][s] = 1.0
        return out
    return fn


# ─────────────────────────────────────────────────────────────────────────────
# 벤치마크 스트림 + 평가
# ─────────────────────────────────────────────────────────────────────────────
def bench_returns(panel: Panel) -> list[float]:
    """QQQ B&H 일별 순수익([0]=0)."""
    c = panel.close[BENCH]
    return research.to_returns(c)


def cash_rate_series(panel: Panel) -> list[float] | None:
    """DTB3(3M T-bill) → 일간 현금수익률(캘린더 전방채움). 없으면 None."""
    try:
        rf = histdata.load_fred("DTB3", start=START, end=END)
    except Exception:  # noqa: BLE001
        return None
    by = {c.dt: c.close for c in rf}
    out = [0.0] * len(panel.dates)
    last = 0.0
    for i, d in enumerate(panel.dates):
        # 직전 유효 수익률 사용
        v = by.get(d)
        if v is not None:
            last = v
        out[i] = max(0.0, (last / 100.0) / 252.0)
    return out


def perf_summary(dates: list[date], returns: list[float]) -> dict:
    """일별 순수익 스트림 → CAGR/Sharpe/MDD/Ulcer/Calmar 요약."""
    r = returns[1:] if returns and returns[0] == 0.0 else returns
    eq = gate.returns_to_equity(r)
    days = max((dates[-1] - dates[0]).days, 1)
    cg = gate.cagr(eq, days)
    mdd = gate.max_drawdown(eq)
    return {
        "cagr": cg, "sharpe": gate.sharpe(r), "mdd": mdd,
        "ulcer": gate.ulcer_index(eq), "calmar": gate.calmar(cg, mdd),
        "terminal_unit": eq[-1], "T": len(r),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 분할·평가 유틸
# ─────────────────────────────────────────────────────────────────────────────
def _split_idx(ret_dates: list[date], design_end: date) -> tuple[list[int], list[int]]:
    di = [i for i, d in enumerate(ret_dates) if d <= design_end]
    hi = [i for i, d in enumerate(ret_dates) if d > design_end]
    return di, hi


def _sr_daily(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sd = gate._std(returns, ddof=1)
    return (gate._mean(returns) / sd) if sd > 0 else 0.0


def _terminal(returns: list[float]) -> float:
    eq = 1.0
    for r in returns:
        eq *= (1.0 + r)
    return eq


def _neighbor_params(base: dict) -> list[dict]:
    """평탄성/N 이웃(±20~50%): lookback ×{0.7,0.8,1.2,1.5}, topk 근방. 그리드서치 아님."""
    out = []
    for key in ("lookback", "window"):
        if key in base:
            for g in (0.7, 0.8, 1.2, 1.5):
                p = dict(base); p[key] = int(base[key] * g); out.append(p)
    if "topk" in base:
        for k in (max(1, base["topk"] - 1), base["topk"] + 1, base["topk"] + 2):
            if k != base["topk"]:
                p = dict(base); p["topk"] = k; out.append(p)
    return out


def evaluate_portfolio_idea(panel: Panel, idea_id: str, decide_fn, base_params: dict,
                            bench_ret: list[float], cash_rate: list[float] | None, *,
                            design_end: date = DESIGN_END, log: bool = True,
                            rc_B: int = 1500) -> dict:
    """포트폴리오 아이디어 1개(사전등록 config + 이웃)를 평가·원장적재.

    반환: 설계/홀드아웃 성과, DSR(N_eff), RC/SPA, 비용스트레스(2x·10bp), 회전/비용드래그, 판정.
    """
    cost = cost_for(panel)
    zcost = CostSpec(commission_bps=0.0, slippage_bps=0.0, fx_bps=0.0, default_half_spread_bps=0.0)
    dates = panel.dates
    ret_dates = dates[1:]
    di, hi = _split_idx(ret_dates, design_end)

    def sim_returns(params, c):
        dec = decide_fn(panel, **params)
        sim = simulate_portfolio(panel, dec, c, cash_rate=cash_rate)
        return sim, sim.returns[1:]

    sim_c, cand = sim_returns(base_params, cost)
    _, gross = sim_returns(base_params, zcost)

    # 이웃(평탄성 + DSR용 N)
    streams: dict[str, list[float]] = {}
    family_excess: dict[str, list[float]] = {}
    sr_trials: list[float] = []
    neigh_pos = 0; neigh_sh: list[float] = []
    cand_des = [cand[i] for i in di]
    center_sh = gate.sharpe(cand_des)
    bench_des = [bench_ret[1:][i] for i in di]
    for j, p in enumerate(_neighbor_params(base_params)):
        _, nret = sim_returns(p, cost)
        nd = [nret[i] for i in di]
        streams[f"n{j}"] = nd
        sr_trials.append(_sr_daily(nd))
        ex = [nd[k] - bench_des[k] for k in range(len(nd))]
        family_excess[f"n{j}"] = ex
        nx = _terminal(nd) / _terminal(bench_des) - 1.0
        if nx > 0:
            neigh_pos += 1
        neigh_sh.append(gate.sharpe(nd))
    streams["center"] = cand_des
    sr_trials.append(_sr_daily(cand_des))
    n_eff = gate.n_eff_clusters(streams)
    frac_pos = neigh_pos / len(neigh_sh) if neigh_sh else 0.0
    neigh_sh.sort()
    med_sh = neigh_sh[len(neigh_sh) // 2] if neigh_sh else 0.0
    plateau_pass = bool(neigh_sh and frac_pos >= 0.8 and med_sh >= 0.9 * center_sh)
    plateau_border = bool(neigh_sh and frac_pos >= 0.6 and not plateau_pass)

    # 설계/홀드아웃 지표
    def split_metrics(idxs):
        r = [cand[i] for i in idxs]
        b = [bench_ret[1:][i] for i in idxs]
        d = [ret_dates[i] for i in idxs]
        um = gate_eval.unit_capital_metrics(r, dates=d, sr_trials=sr_trials, n_eff=n_eff)
        excess = [r[k] - b[k] for k in range(len(r))]
        rc = gate_eval.reality_check(excess, family_excess=family_excess, B=rc_B)
        term_vs = _terminal(r) / _terminal(b) if _terminal(b) > 0 else float("nan")
        return r, b, d, um, rc, term_vs

    r_des, b_des, d_des, um_des, rc_des, tvs_des = split_metrics(di)
    r_hol, b_hol, d_hol, um_hol, rc_hol, tvs_hol = split_metrics(hi)

    # 비용 스트레스(2x) + 10bp what-if: 홀드아웃 순초과(vs QQQ) 부호로 판정
    def net_excess_full(c):
        _, rr = sim_returns(base_params, c)
        rh = [rr[i] for i in hi]
        return _terminal(rh) / _terminal(b_hol) - 1.0 if _terminal(b_hol) > 0 else -1.0
    nx_1x = _terminal(r_hol) / _terminal(b_hol) - 1.0
    nx_2x = net_excess_full(cost_for(panel, mult=2.0))
    nx_10 = net_excess_full(cost_for(panel, commission_bps=10.0))
    cost_stress_mult = 2.0 if nx_2x > 0 else (1.0 if nx_1x > 0 else 0.0)

    # 회전/비용드래그(전체 구간)
    turnover_yr = sim_c.turnover_per_year()
    days_full = max((dates[-1] - dates[0]).days, 1)
    cagr_net = gate.cagr(gate.returns_to_equity(cand), days_full)
    cagr_gross = gate.cagr(gate.returns_to_equity(gross), days_full)
    cost_drag = cagr_gross - cagr_net

    # 서브구간 승률(전체)
    eq_strat = gate.returns_to_equity(cand)
    eq_bench = gate.returns_to_equity(bench_ret[1:])
    sub = gate.subperiod_consistency(eq_strat, eq_bench, ret_dates)

    # 판정(홀드아웃 기준 축)
    axis = {
        "lane": 2, "biased_universe": True,
        "survivorship_haircut_ok": (tvs_hol ** (365.0 / max((d_hol[-1] - d_hol[0]).days, 1)) - 1.0) >= 0.04
        if hi else False,
        "terminal_vs_b0": tvs_hol, "terminal_vs_b1": tvs_hol,
        "dsr": um_hol.get("dsr"), "rc_pvalue": rc_hol["rc_pvalue"],
        "plateau_pass": plateau_pass, "plateau_borderline": plateau_border,
        "subperiod_winrate": sub["win_rate"],
        "cost_stress_mult": cost_stress_mult,
        "mdd": um_hol.get("max_drawdown"),
    }
    decision = gate.decide(axis)

    # 원장 적재(설계 center+이웃, 홀드아웃 center 1회 — peek-once)
    if log:
        cfg = dict(base_params); cfg["idea"] = idea_id
        gate_eval.log_evaluation(idea_id, cfg, 2, "design", um_des,
                                 gate_eval.money_weighted_metrics(benchmark_terminal=_terminal(b_des),
                                                                  dca_equity=gate.returns_to_equity(r_des)),
                                 window=(d_des[0], d_des[-1]) if d_des else None,
                                 universe=panel.universe, ledger_path=LEDGER)
        for j, p in enumerate(_neighbor_params(base_params)):
            npar = dict(p); npar["idea"] = idea_id; npar["neighbor"] = j
            nd = streams[f"n{j}"]
            um_n = gate_eval.unit_capital_metrics(nd, dates=d_des)
            gate_eval.log_evaluation(idea_id, npar, 2, "design", um_n, {},
                                     window=(d_des[0], d_des[-1]) if d_des else None,
                                     ledger_path=LEDGER)
        if hi:
            try:
                gate_eval.log_evaluation(idea_id, cfg, 2, "holdout", um_hol,
                                         gate_eval.money_weighted_metrics(
                                             benchmark_terminal=_terminal(b_hol),
                                             dca_equity=gate.returns_to_equity(r_hol)),
                                         window=(d_hol[0], d_hol[-1]), universe=panel.universe,
                                         ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    return {
        "idea": idea_id, "params": base_params,
        "design": {"cagr": um_des.get("cagr"), "sharpe": um_des.get("sr_annual"),
                   "mdd": um_des.get("max_drawdown"), "terminal_vs_qqq": tvs_des,
                   "dsr": um_des.get("dsr"), "rc_p": rc_des["rc_pvalue"], "spa_p": rc_des["spa_pvalue"]},
        "holdout": {"cagr": um_hol.get("cagr"), "sharpe": um_hol.get("sr_annual"),
                    "mdd": um_hol.get("max_drawdown"), "terminal_vs_qqq": tvs_hol,
                    "dsr": um_hol.get("dsr"), "rc_p": rc_hol["rc_pvalue"], "spa_p": rc_hol["spa_pvalue"]},
        "turnover_yr": turnover_yr, "cost_drag": cost_drag,
        "cagr_gross": cagr_gross, "cagr_net": cagr_net,
        "nx_1x_holdout": nx_1x, "nx_2x_holdout": nx_2x, "nx_10bp_holdout": nx_10,
        "n_eff": n_eff, "n_trials": len(sr_trials),
        "plateau_pass": plateau_pass, "plateau_frac_pos": frac_pos,
        "subperiod_winrate": sub["win_rate"],
        "verdict": decision.verdict, "reasons": decision.reasons,
    }


def evaluate_attention(panel: Panel, idea_id: str, *, hold: int, log: bool = True,
                       design_end: date = DESIGN_END) -> dict:
    """어텐션 스윙(전략3): 거래단위 통계(레인2 §3.5). 설계/홀드아웃 분할."""
    tr = attention_trades(panel, hold=hold, cost=cost_for(panel))
    tr_2x = attention_trades(panel, hold=hold, cost=cost_for(panel, mult=2.0))

    # 진입일 기준 설계/홀드아웃 분할(attention_trades 와 동일 순서).
    entries = _attention_entry_dates(panel, hold=hold)
    des_p = [res_pnl for res_pnl, ed in zip(tr.pnls, entries) if ed <= design_end]
    hol_p = [res_pnl for res_pnl, ed in zip(tr.pnls, entries) if ed > design_end]
    hol_p2 = [res_pnl for res_pnl, ed in zip(tr_2x.pnls, entries) if ed > design_end]

    def trade_stats(pnls):
        if len(pnls) < 2:
            return {"n": len(pnls)}
        _, lo, _ = gate.trade_pnl_bootstrap_ci(pnls, B=1500)
        return {"n": len(pnls), "tstat": gate.trade_tstat(pnls),
                "ci_lo": lo, "profit_factor": gate.profit_factor(pnls),
                "expectancy": gate.expectancy(pnls), "mean_ret_bps": gate._mean(pnls) * 1e4,
                "win_rate": sum(1 for p in pnls if p > 0) / len(pnls)}

    st_all = trade_stats(tr.pnls)
    st_des = trade_stats(des_p)
    st_hol = trade_stats(hol_p)
    st_hol2 = trade_stats(hol_p2)

    axis = {"lane": 3, "n_trades": st_hol.get("n", 0), "trade_tstat": st_hol.get("tstat"),
            "trade_ci_lo": st_hol.get("ci_lo"),
            "slippage_stress_pass": st_hol2.get("ci_lo", -1) is not None and st_hol2.get("ci_lo", -1) > 0}
    decision = gate.decide(axis)

    if log:
        cfg = {"idea": idea_id, "hold": hold, "rvol": 3.0, "up": 0.03, "near_high": 0.7}
        um = {"sr_daily": _sr_daily(tr.daily_returns), "T": st_all.get("n"),
              "max_drawdown": gate.max_drawdown(tr.equity)}
        gate_eval.log_evaluation(idea_id, cfg, 2, "design", um, {}, ledger_path=LEDGER)
        if hol_p:
            try:
                gate_eval.log_evaluation(idea_id, cfg, 2, "holdout",
                                         {"sr_daily": _sr_daily(hol_p), "T": len(hol_p)},
                                         {}, ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    return {"idea": idea_id, "hold": hold, "all": st_all, "design": st_des,
            "holdout": st_hol, "holdout_2xcost": st_hol2, "verdict": decision.verdict,
            "reasons": decision.reasons}


def _attention_entry_dates(panel: Panel, *, hold: int, rvol_k: float = 3.0, up: float = 0.03,
                           near_high: float = 0.7, avg_win: int = 50) -> list[date]:
    """attention_trades 와 동일 순서의 진입일 리스트(설계/홀드아웃 분할용)."""
    out: list[date] = []
    n = len(panel.dates)
    for s in panel.universe:
        cl, hi, lo, vo = panel.close[s], panel.high[s], panel.low[s], panel.vol[s]
        op = panel.open[s]
        for t in range(n):
            if not _eligible(panel, s, t, avg_win):
                continue
            if t + 1 + hold >= n:
                break
            avgv = sum(vo[t - avg_win:t]) / avg_win
            if avgv <= 0:
                continue
            rvol = vo[t] / avgv
            ret1 = (cl[t] / cl[t - 1] - 1.0) if cl[t - 1] > 0 else 0.0
            rng = (cl[t] - lo[t]) / (hi[t] - lo[t]) if hi[t] > lo[t] else 0.0
            if rvol > rvol_k and ret1 > up and rng > near_high:
                if op[t + 1] > 0 and op[t + 1 + hold] > 0:
                    out.append(panel.dates[t + 1])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 부가 분석: leave-top-names-out, top-2 기여, 섹터 ETF 프록시
# ─────────────────────────────────────────────────────────────────────────────
def leave_top_out(panel: Panel, exclude: list[str], bench_ret, cash_rate) -> dict:
    """지정 종목 제외 후 12-1 모멘텀 top3 재평가(상한이 상위 소수에 얼마나 의존하나)."""
    sub = Panel(dates=panel.dates)
    keep = [s for s in panel.close if s not in exclude]
    for s in keep:
        for fld in ("close", "open", "high", "low", "vol"):
            getattr(sub, fld)[s] = getattr(panel, fld)[s]
        sub.first_idx[s] = panel.first_idx[s]; sub.tier[s] = panel.tier[s]
    sub.universe = [s for s in keep if s not in (BENCH, "QQQ", "SPY")]
    dec = decide_momentum(sub, topk=3, use_filter=False)
    sim = simulate_portfolio(sub, dec, cost_for(sub), cash_rate=cash_rate)
    days = max((sub.dates[-1] - sub.dates[0]).days, 1)
    return {"excluded": exclude, "cagr": gate.cagr(gate.returns_to_equity(sim.returns[1:]), days),
            "sharpe": gate.sharpe(sim.returns[1:]),
            "mdd": gate.max_drawdown(gate.returns_to_equity(sim.returns[1:]))}


def sector_proxy(cash_rate_fn=cash_rate_series) -> dict:
    """섹터 SPDR(생존편향-free) 9종에 동일 규칙 적용 — 로직의 단일종목-무관 유효성 점검."""
    sectors = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB"]
    sp = load_panel(sectors, calendar_symbol="QQQ")
    sp.universe = [s for s in sectors if s in sp.close]
    cr = cash_rate_fn(sp)
    br = bench_returns(sp)
    days = max((sp.dates[-1] - sp.dates[0]).days, 1)
    out = {"n_sectors": len(sp.universe)}
    specs = {
        "mom_top2": (decide_momentum, {"topk": 2, "use_filter": False}),
        "highprox_top2": (decide_high_proximity, {"topk": 2}),
        "reversal_top2": (decide_reversal, {"topk": 2, "top_dollar": 9}),
        "resid_top2": (decide_resid_momentum, {"topk": 2}),
    }
    for name, (fn, kw) in specs.items():
        dec = fn(sp, **kw)
        sim = simulate_portfolio(sp, dec, cost_for(sp), cash_rate=cr)
        r = sim.returns[1:]
        out[name] = {"cagr": gate.cagr(gate.returns_to_equity(r), days),
                     "sharpe": gate.sharpe(r), "mdd": gate.max_drawdown(gate.returns_to_equity(r)),
                     "turnover_yr": sim.turnover_per_year()}
    out["qqq_bh_cagr"] = gate.cagr(gate.returns_to_equity(br[1:]), days)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="c2d 단일종목 횡단면/어텐션 전략")
    ap.add_argument("--no-ledger", action="store_true", help="원장 적재 생략(개발/디버그)")
    ap.add_argument("--out", type=str, default="", help="결과 JSON 저장 경로")
    ap.add_argument("--quick", action="store_true", help="RC 부트스트랩 축소(빠른 점검)")
    args = ap.parse_args()
    log = not args.no_ledger
    rc_B = 400 if args.quick else 1500

    panel = load_panel(fh.SINGLE_STOCKS, calendar_symbol="QQQ")
    cr = cash_rate_series(panel)
    br = bench_returns(panel)
    print(f"유니버스(가용): {len(panel.universe)}종목 | 거래일 {len(panel.dates)} "
          f"({panel.dates[0]}~{panel.dates[-1]})", flush=True)

    results: dict = {"universe": panel.universe, "n_days": len(panel.dates),
                     "dates": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()]}

    ideas = [
        ("c2d_mom_top3", decide_momentum, {"topk": 3, "lookback": 252, "skip": 21, "use_filter": False}),
        ("c2d_mom_top5", decide_momentum, {"topk": 5, "lookback": 252, "skip": 21, "use_filter": False}),
        ("c2d_mom_top3_trend", decide_momentum, {"topk": 3, "lookback": 252, "skip": 21, "use_filter": True}),
        ("c2d_highprox_top3", decide_high_proximity, {"topk": 3, "window": 252}),
        ("c2d_reversal_w3", decide_reversal, {"topk": 3, "lookback": 5, "top_dollar": 50, "use_filter": True}),
        ("c2d_resid_mom_top3", decide_resid_momentum, {"topk": 3, "lookback": 252, "skip": 21}),
    ]
    results["portfolio"] = {}
    for idea_id, fn, params in ideas:
        print(f"\n=== {idea_id} {params} ===", flush=True)
        res = evaluate_portfolio_idea(panel, idea_id, fn, params, br, cr, log=log, rc_B=rc_B)
        results["portfolio"][idea_id] = res
        print(f"  홀드아웃 CAGR {res['holdout']['cagr']:.2%} Sharpe {res['holdout']['sharpe']:.2f} "
              f"MDD {res['holdout']['mdd']:.1%} vsQQQ×{res['holdout']['terminal_vs_qqq']:.2f} "
              f"DSR {res['holdout']['dsr']} RCp {res['holdout']['rc_p']} → {res['verdict']}", flush=True)

    results["attention"] = {}
    for hold in (5, 10):
        idea_id = f"c2d_attention_h{hold}"
        print(f"\n=== {idea_id} ===", flush=True)
        res = evaluate_attention(panel, idea_id, hold=hold, log=log)
        results["attention"][idea_id] = res
        h = res["holdout"]
        print(f"  홀드아웃 n={h.get('n')} t={h.get('tstat')} PF={h.get('profit_factor')} "
              f"→ {res['verdict']}", flush=True)

    print("\n=== leave-top-names-out (12-1 mom top3) ===", flush=True)
    full = results["portfolio"]["c2d_mom_top3"]
    lto = leave_top_out(panel, ["MSTR", "NVDA"], br, cr)
    results["leave_top_out"] = {"full_holdout_cagr": full["holdout"]["cagr"],
                                "full_full_cagr": full["cagr_net"], "ex_MSTR_NVDA": lto}
    print(f"  전체 CAGR {full['cagr_net']:.2%} → MSTR·NVDA 제외 {lto['cagr']:.2%}", flush=True)

    print("\n=== 섹터 ETF 프록시(생존편향-free) ===", flush=True)
    results["sector_proxy"] = sector_proxy()
    for k, v in results["sector_proxy"].items():
        if isinstance(v, dict):
            print(f"  {k}: CAGR {v['cagr']:.2%} Sharpe {v['sharpe']:.2f} MDD {v['mdd']:.1%}", flush=True)
    print(f"  QQQ B&H CAGR {results['sector_proxy']['qqq_bh_cagr']:.2%}", flush=True)

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\n결과 저장: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
