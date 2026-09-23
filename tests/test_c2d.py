"""c2d 단일종목 횡단면/어텐션 전략 테스트 (순수 stdlib + 합성데이터, 네트워크 불요).

검증 포인트:
- **모든 신호에 lookahead_guard**(사양 §2.3): 5개 전략 신호가 미래가격 교란에 불변(인과적).
- simulate_portfolio: 다음 시가 체결·일별 MTM·비용 회계 손계산.
- 신규상장(first_idx>0) 종목은 이력 충족 전 선택되지 않음(생존편향/상장 처리).
- decisions_to_daily 전방채움이 인과적(guard 통과).
- 어텐션 이벤트 → run_trades 거래 생성 + 거래단위 통계 유한.
"""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import research  # noqa: E402
from toss_trader.research import CostSpec, LookaheadLeak  # noqa: E402
import c2d_stocks as c2d  # noqa: E402


# ── 합성 패널 ────────────────────────────────────────────────────────────────
def _weekdays(start: date, count: int) -> list[date]:
    out, d = [], start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _synth_panel(n: int = 140, seed: int = 7) -> c2d.Panel:
    """결정론적 합성 패널: QQQ + 5개 종목. 서로 다른 추세/변동으로 랭킹이 갈리게."""
    dates = _weekdays(date(2021, 1, 4), n)
    p = c2d.Panel(dates=dates)
    syms = ["QQQ", "AAA", "BBB", "CCC", "DDD", "EEE"]
    for k, s in enumerate(syms):
        drift = 0.0004 * (k + 1)                 # 종목마다 다른 추세 → 모멘텀 랭킹 분산
        amp = 3.0 + k
        cl, op, hi, lo, vo = [], [], [], [], []
        px = 100.0 + 5 * k
        for i in range(n):
            px *= (1.0 + drift + 0.004 * math.sin((i + k) / 4.0))
            c = px
            o = c * (1.0 - 0.001 * math.cos((i + k) / 3.0))
            h = max(c, o) * 1.006
            low = min(c, o) * 0.994
            cl.append(c); op.append(o); hi.append(h); lo.append(low)
            vo.append(1_000_000.0 * (1.0 + 0.2 * math.sin(i / 5.0)) * (k + 1))
        p.close[s] = cl; p.open[s] = op; p.high[s] = hi; p.low[s] = lo; p.vol[s] = vo
        p.first_idx[s] = 0
        p.tier[s] = c2d._tier_for(s)
    p.universe = [s for s in syms if s != "QQQ"]
    return p


# ── 신호 어댑터(guard용): panel_closes 를 갈아끼우고 나머지 aux 는 고정 ───────
def _decide_signal_fn(base: c2d.Panel, decide_fn, **kwargs):
    def fn(panel_closes, dates):
        p = c2d.Panel(dates=list(dates))
        p.close = panel_closes                    # 교란 대상(미래 종가)
        p.open, p.high, p.low, p.vol = base.open, base.high, base.low, base.vol
        p.first_idx, p.tier, p.universe = base.first_idx, base.tier, base.universe
        dec = decide_fn(p, **kwargs)
        return c2d.decisions_to_daily(dec, len(dates))
    return fn


# ── lookahead_guard: 모든 전략 신호가 인과적(미래 교란에 불변) ────────────────
def test_lookahead_guard_momentum():
    p = _synth_panel()
    fn = _decide_signal_fn(p, c2d.decide_momentum, topk=3, lookback=25, skip=3, use_filter=False)
    assert research.lookahead_guard(fn, p.close, p.dates) is True
    # 추세필터 버전도 인과적
    fn2 = _decide_signal_fn(p, c2d.decide_momentum, topk=3, lookback=25, skip=3, use_filter=True)
    assert research.lookahead_guard(fn2, p.close, p.dates) is True


def test_lookahead_guard_high_proximity():
    p = _synth_panel()
    fn = _decide_signal_fn(p, c2d.decide_high_proximity, topk=3, window=25)
    assert research.lookahead_guard(fn, p.close, p.dates) is True


def test_lookahead_guard_reversal():
    p = _synth_panel()
    fn = _decide_signal_fn(p, c2d.decide_reversal, topk=3, lookback=5, top_dollar=5,
                           dv_window=10, use_filter=True)
    assert research.lookahead_guard(fn, p.close, p.dates) is True


def test_lookahead_guard_resid_momentum():
    p = _synth_panel()
    fn = _decide_signal_fn(p, c2d.decide_resid_momentum, topk=3, lookback=70, skip=3)
    assert research.lookahead_guard(fn, p.close, p.dates) is True


def test_lookahead_guard_attention():
    p = _synth_panel()
    # 강제 이벤트: 90일에 EEE 거래량 급증 + 당일 +5% + 종가=고가 근접
    t0 = 90
    p.vol["EEE"][t0] = p.vol["EEE"][t0] * 20.0
    p.close["EEE"][t0] = p.close["EEE"][t0 - 1] * 1.05
    p.high["EEE"][t0] = p.close["EEE"][t0]
    p.low["EEE"][t0] = p.close["EEE"][t0] * 0.95
    fn = c2d.attention_signal_fn(p)
    assert research.lookahead_guard(fn, p.close, p.dates) is True
    # 이벤트가 실제로 하나 이상 잡혀야 의미있는 검증
    sig = fn(p.close, p.dates)
    assert any(w for w in sig), "합성 이벤트가 신호로 잡히지 않음"


def test_lookahead_guard_catches_leak_control():
    """대조군: 미래 종가에 의존하는 누수 신호는 guard가 잡아야 한다."""
    p = _synth_panel(n=40)

    def leaky(panel_closes, dates):
        last = panel_closes["AAA"][-1]
        return [{"AAA": min(1.0, panel_closes["AAA"][t] / last)} for t in range(len(dates))]

    with pytest.raises(LookaheadLeak):
        research.lookahead_guard(leaky, p.close, p.dates)


# ── simulate_portfolio: 다음 시가 체결 + 일별 MTM 손계산 ─────────────────────
def _mini_panel(closes, opens) -> c2d.Panel:
    n = len(next(iter(closes.values())))
    dates = _weekdays(date(2022, 1, 3), n)
    p = c2d.Panel(dates=dates)
    for s in closes:
        p.close[s] = closes[s]; p.open[s] = opens[s]
        p.high[s] = closes[s]; p.low[s] = closes[s]; p.vol[s] = [1.0] * n
        p.first_idx[s] = 0; p.tier[s] = c2d._tier_for(s)
    p.universe = [s for s in closes if s != "QQQ"]
    return p


def test_simulate_portfolio_next_open_fill_zero_cost():
    # 결정 day0(AAA 100%) → day1 시가(105)에 체결. 종가로 MTM.
    p = _mini_panel({"QQQ": [100.0, 100.0, 100.0], "AAA": [100.0, 110.0, 121.0]},
                    {"QQQ": [100.0, 100.0, 100.0], "AAA": [100.0, 105.0, 116.0]})
    z = CostSpec(commission_bps=0.0, slippage_bps=0.0, fx_bps=0.0, default_half_spread_bps=0.0)
    sim = c2d.simulate_portfolio(p, {0: {"AAA": 1.0}}, z)
    assert sim.equity[0] == pytest.approx(1.0)
    assert sim.equity[1] == pytest.approx(110.0 / 105.0)      # 105 체결 → 110 종가
    assert sim.equity[2] == pytest.approx(121.0 / 105.0)      # 보유 지속(추가 결정 없음)
    assert sim.returns[1] == pytest.approx(110.0 / 105.0 - 1.0)
    assert sim.total_cost == pytest.approx(0.0)
    assert sim.n_rebalances == 1


def test_simulate_portfolio_cost_charged_at_fill():
    p = _mini_panel({"QQQ": [100.0, 100.0], "AAA": [100.0, 100.0]},
                    {"QQQ": [100.0, 100.0], "AAA": [100.0, 100.0]})
    # AAA=small_hot: 25+15+5=45bps=0.0045. 전액 매수 노셔널 1.0 → 비용 0.0045.
    cost = c2d.cost_for(p)
    assert cost.trade_bps("AAA") == pytest.approx(45.0)
    sim = c2d.simulate_portfolio(p, {0: {"AAA": 1.0}}, cost)
    assert sim.total_cost == pytest.approx(0.0045)
    assert sim.equity[1] == pytest.approx(1.0 - 0.0045)       # 평탄가격 → 비용만큼 감소
    assert sim.turnover[1] == pytest.approx(1.0)


def test_simulate_portfolio_cash_earns_rate_when_flat():
    # 결정 없음(전량 현금) + cash_rate → 매매 0, 현금이자로만 성장.
    p = _mini_panel({"QQQ": [100.0, 100.0, 100.0]}, {"QQQ": [100.0, 100.0, 100.0]})
    z = CostSpec(commission_bps=0.0, slippage_bps=0.0, fx_bps=0.0, default_half_spread_bps=0.0)
    sim = c2d.simulate_portfolio(p, {}, z, cash_rate=[0.0, 0.001, 0.002])
    assert sim.total_cost == pytest.approx(0.0)
    assert sim.equity[1] == pytest.approx(1.001)
    assert sim.equity[2] == pytest.approx(1.001 * 1.002)


# ── 신규상장/이력 부족 종목은 선택되지 않음 ─────────────────────────────────
def test_new_listing_not_selected_until_history():
    p = _synth_panel(n=140)
    # DDD 를 t=120 상장으로 만든다(이전은 평탄, first_idx=120).
    p.first_idx["DDD"] = 120
    fc = p.close["DDD"][120]
    for i in range(120):
        p.close["DDD"][i] = p.open["DDD"][i] = p.high["DDD"][i] = p.low["DDD"][i] = fc
        p.vol["DDD"][i] = 0.0
    dec = c2d.decide_momentum(p, topk=5, lookback=100, skip=5, use_filter=False)
    for t, w in dec.items():
        if t - 120 < 100:                        # 이력 100 미만이면 DDD 미포함
            assert "DDD" not in w
    # 자격 판단 헬퍼 직접 검증
    assert c2d._eligible(p, "DDD", 200, 100) is False   # 200-120=80 < 100
    assert c2d._eligible(p, "DDD", 121, 100) is False


# ── decisions_to_daily 전방채움 인과성 ──────────────────────────────────────
def test_decisions_to_daily_forward_fill():
    dec = {2: {"AAA": 1.0}, 5: {"BBB": 1.0}}
    daily = c2d.decisions_to_daily(dec, 8)
    assert daily[0] == {} and daily[1] == {}
    assert daily[2] == {"AAA": 1.0} and daily[4] == {"AAA": 1.0}
    assert daily[5] == {"BBB": 1.0} and daily[7] == {"BBB": 1.0}


# ── 어텐션 이벤트 → 거래 생성 + 거래단위 통계 ───────────────────────────────
def test_attention_trades_generated_and_stats_finite():
    from toss_trader import gate
    p = _synth_panel(n=140)
    t0 = 90
    p.vol["EEE"][t0] = p.vol["EEE"][t0] * 20.0
    p.close["EEE"][t0] = p.close["EEE"][t0 - 1] * 1.06
    p.high["EEE"][t0] = p.close["EEE"][t0]
    p.low["EEE"][t0] = p.close["EEE"][t0] * 0.95
    res = c2d.attention_trades(p, hold=5)
    assert res.n_trades >= 1
    # 거래단위 통계가 유한(레인2 §3.5)
    if res.n_trades >= 2:
        assert math.isfinite(gate.trade_tstat(res.pnls))
    # 진입일 리스트가 거래 수와 일치(설계/홀드아웃 분할 정합)
    entries = c2d._attention_entry_dates(p, hold=5)
    assert len(entries) == res.n_trades


# ── 티어 분류 ────────────────────────────────────────────────────────────────
def test_tier_classification():
    assert c2d._tier_for("QQQ") == research.TIER_ETF
    assert c2d._tier_for("XLK") == research.TIER_ETF
    assert c2d._tier_for("AAPL") == research.TIER_LARGE_CAP
    assert c2d._tier_for("MSTR") == research.TIER_SMALL_HOT      # 핫/변동성 → 15bp
