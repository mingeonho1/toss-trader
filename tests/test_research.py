"""research 백테스터 결정론적 테스트 (순수 stdlib).

검증 포인트(요구사항):
- 비용 회계 손계산(수수료+반호가+슬리피지, **FX는 매매에 미부과**·티어별 반호가·10/25bps).
- 체결 지연(exec_lag) 정확성 + 시가 체결(exec_price='open').
- 무매매 밴드(rebalance_band) 리밸런싱.
- DCA 오버레이 현금흐름 → gate.xirr 정합(손계산 앵커 + 월적립 sanity + FX 드래그).
- lookahead_guard가 고의 누수 신호를 잡는다(인과 신호는 통과).
- 헬퍼(sma/ema/rsi/atr/…·확장 백분위·캘린더)의 값과 무-look-ahead.
- 벤치마크: 26년 × 30자산 < 5초.
"""
from __future__ import annotations

import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

# 저장소 관례: 소스는 src/ 아래(별도 설치 없음). test_gate.py와 동일 패턴.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import gate, research  # noqa: E402
from toss_trader.research import CostSpec, Trade  # noqa: E402


# ── 비용 스펙 ────────────────────────────────────────────────────────────────
def test_cost_spec_defaults_promo_tiers_and_stress():
    # 정정(2025-12-01~): 표준 수수료 기본 = 10bps(US 0.1%). promo 프리셋도 동일 10bps(하위호환).
    assert CostSpec().commission_bps == 10.0
    assert CostSpec.promo().commission_bps == 10.0
    assert research.STANDARD_COMMISSION_BPS == 10.0

    c = CostSpec.from_tiers(
        {"SPY": research.TIER_ETF, "TQQQ": research.TIER_LEVERAGED_ETF,
         "HOTX": research.TIER_SMALL_HOT},
        commission_bps=10.0, slippage_bps=5.0, fx_bps=20.0)
    # 반호가 티어: ETF 1 / 레버리지ETF 2 / 소형핫 15
    assert c.half_spread_for("SPY") == 1.0
    assert c.half_spread_for("TQQQ") == 2.0
    assert c.half_spread_for("HOTX") == 15.0
    assert c.half_spread_for("UNKNOWN") == 3.0            # 기본(대형주)
    # 매매비(편도) = 수수료+반호가+슬리피지, FX 제외
    assert c.trade_bps("SPY") == 10.0 + 1.0 + 5.0
    assert c.trade_bps("HOTX") == 10.0 + 15.0 + 5.0
    # FX는 입금에만
    assert c.fx_cost(100.0) == pytest.approx(100.0 * 20.0 * 1e-4)

    s = c.stress(2.0)
    assert s.commission_bps == 20.0 and s.slippage_bps == 10.0 and s.fx_bps == 40.0
    assert s.half_spread_for("HOTX") == 30.0
    assert s.trade_bps("SPY") == 2.0 * c.trade_bps("SPY")


# ── run_weights: 비용 손계산 ─────────────────────────────────────────────────
def test_run_weights_cost_hand_checked_single_trade():
    # 3일·1종목·평탄가격. exec_lag=1 → 신호(day0)를 day1에 체결. band=0.01로 day2 잔먼지 매매 차단.
    cost = CostSpec(commission_bps=10.0, slippage_bps=5.0, fx_bps=20.0,
                    half_spread_bps={"SPY": 1.0})     # trade_bps=16
    dates = [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)]
    closes = {"SPY": [100.0, 100.0, 100.0]}
    tw = [{"SPY": 1.0}] * 3
    r = research.run_weights(closes, dates, tw, exec_lag=1, rebalance_band=0.01,
                             cost=cost, start_equity=1.0)
    # 단 한 번(day1)만 체결: 노셔널 1.0 × 16bps = 0.0016. FX(20bps) 포함이면 0.0036 → 배제 확인.
    assert r.trade_count == 1
    assert r.total_cost == pytest.approx(0.0016)
    assert r.costs[0] == 0.0 and r.costs[1] == pytest.approx(0.0016) and r.costs[2] == 0.0
    assert r.equity[1] == pytest.approx(0.9984)
    assert r.equity[2] == pytest.approx(0.9984)
    # net = 시장수익 − 비용/직전자본. 평탄시장이라 gross=0, net=−0.0016.
    assert r.net_returns[1] == pytest.approx(-0.0016)
    assert r.gross_returns[1] == pytest.approx(0.0)
    assert r.turnover[1] == pytest.approx(1.0)


def test_run_weights_tiered_halfspread_and_commission_10_and_25():
    # 단일일·2종목·동시 편입. 티어별 반호가 + 수수료 10 vs 25 모두 손계산.
    dates = [date(2021, 6, 1)]
    closes = {"SPY": [100.0], "HOTX": [100.0]}
    tw = [{"SPY": 0.5, "HOTX": 0.5}]

    c10 = CostSpec.from_tiers({"SPY": research.TIER_ETF, "HOTX": research.TIER_SMALL_HOT},
                              commission_bps=10.0, slippage_bps=5.0, fx_bps=20.0)
    r10 = research.run_weights(closes, dates, tw, exec_lag=0, rebalance_band=0.0, cost=c10)
    # 0.5·(10+1+5)bps + 0.5·(10+15+5)bps = 0.0008 + 0.0015 = 0.0023 (FX 미부과)
    assert r10.trade_count == 2
    assert r10.total_cost == pytest.approx(0.0023)
    assert r10.equity[0] == pytest.approx(1.0 - 0.0023)

    c25 = CostSpec.from_tiers({"SPY": research.TIER_ETF, "HOTX": research.TIER_SMALL_HOT},
                              commission_bps=25.0, slippage_bps=5.0, fx_bps=20.0)
    r25 = research.run_weights(closes, dates, tw, exec_lag=0, rebalance_band=0.0, cost=c25)
    # 0.5·(25+1+5) + 0.5·(25+15+5) = 0.00155 + 0.00225 = 0.0038
    assert r25.total_cost == pytest.approx(0.0038)


# ── run_weights: 체결 지연 ───────────────────────────────────────────────────
def _zero_cost() -> CostSpec:
    return CostSpec(commission_bps=0.0, slippage_bps=0.0, fx_bps=0.0,
                    default_half_spread_bps=0.0)


def test_run_weights_exec_lag_captures_or_misses_jump():
    # day1에 SPY 진입 신호. day2에 +10% 점프. lag0은 점프 수취, lag1은 놓침.
    dates = [date(2020, 1, i) for i in range(1, 5)]
    closes = {"SPY": [100.0, 100.0, 110.0, 110.0]}
    tw = [{}, {"SPY": 1.0}, {"SPY": 1.0}, {"SPY": 1.0}]

    lag0 = research.run_weights(closes, dates, tw, exec_lag=0, cost=_zero_cost())
    lag1 = research.run_weights(closes, dates, tw, exec_lag=1, cost=_zero_cost())
    assert lag0.net_returns[2] == pytest.approx(0.10)     # 종가체결 즉시 → 점프 수취
    assert lag1.net_returns[2] == pytest.approx(0.0)      # 1일 지연 → 점프 놓침
    assert lag0.final_equity == pytest.approx(1.10)
    assert lag1.final_equity == pytest.approx(1.00)
    assert lag0.final_equity > lag1.final_equity


def test_run_weights_open_execution_fills_at_next_open():
    # exec_price='open': 신호를 다음날 '시가'에 체결. lag1이면 seg(시가→종가)만 수취.
    dates = [date(2020, 1, i) for i in range(1, 5)]
    closes = {"SPY": [100.0, 100.0, 110.0, 110.0]}
    opens = {"SPY": [100.0, 100.0, 105.0, 110.0]}
    tw = [{}, {"SPY": 1.0}, {"SPY": 1.0}, {"SPY": 1.0}]

    op = research.run_weights(closes, dates, tw, exec_lag=1, exec_price="open",
                              panel_opens=opens, cost=_zero_cost())
    cl = research.run_weights(closes, dates, tw, exec_lag=1, exec_price="close",
                              cost=_zero_cost())
    # 시가(105) 체결 → 105→110 = +4.7619% 수취. 종가(110) 체결은 점프 전량 놓침.
    assert op.equity[2] == pytest.approx(110.0 / 105.0)
    assert cl.equity[2] == pytest.approx(1.0)
    assert op.equity[2] > cl.equity[2]


# ── run_weights: 무매매 밴드 ─────────────────────────────────────────────────
def test_run_weights_rebalance_band_suppresses_small_drift():
    # 50/50 편입 후 SPY +50% → 비중 0.6/0.4(괴리 0.10). band에 따라 리밸런싱 여부 갈림.
    dates = [date(2020, 1, i) for i in range(1, 4)]
    closes = {"SPY": [100.0, 150.0, 150.0], "BND": [100.0, 100.0, 100.0]}
    tw = [{"SPY": 0.5, "BND": 0.5}] * 3

    tight = research.run_weights(closes, dates, tw, exec_lag=0, rebalance_band=0.0,
                                 cost=_zero_cost())
    wide = research.run_weights(closes, dates, tw, exec_lag=0, rebalance_band=0.30,
                                cost=_zero_cost())
    assert tight.trades[1] == 2        # 괴리 0.10 > 0 → 양쪽 리밸런싱
    assert wide.trades[1] == 0         # 괴리 0.10 < 0.30 → 매매 생략
    assert tight.trade_count == 4      # day0 편입 2 + day1 리밸 2
    assert wide.trade_count == 2       # day0 편입 2 만


def test_run_weights_cash_earns_cash_rate():
    # 전량 현금(빈 목표비중) + cash_rate → 매매 0, 자본이 현금이자로만 복리 성장.
    dates = [date(2020, 1, i) for i in range(1, 4)]
    closes = {"SPY": [100.0, 100.0, 100.0]}
    tw = [{}, {}, {}]
    r = research.run_weights(closes, dates, tw, exec_lag=0,
                             cash_rate=[0.0, 0.001, 0.002], cost=CostSpec())
    assert r.trade_count == 0
    assert r.equity[1] == pytest.approx(1.0 * 1.001)
    assert r.equity[2] == pytest.approx(1.0 * 1.001 * 1.002)


# ── run_trades ───────────────────────────────────────────────────────────────
def test_run_trades_per_trade_pnl_and_daily_series():
    cost = CostSpec(commission_bps=10.0, slippage_bps=5.0, fx_bps=20.0,
                    default_half_spread_bps=3.0)         # trade_bps=18, FX 미부과
    trades = [
        Trade(date(2020, 1, 1), 100.0, date(2020, 1, 2), 110.0, notional=1.0, symbol="X"),
        Trade(date(2020, 1, 3), 100.0, date(2020, 1, 4), 90.0, notional=1.0, symbol="X"),
    ]
    r = research.run_trades(trades, cost=cost, capital=1.0)
    # trade1: gross=+0.10, 비용=(1.0+1.1)·18bps=0.00378 → net=0.09622
    # trade2: gross=−0.10, 비용=(1.0+0.9)·18bps=0.00342 → net=−0.10342
    assert r.pnls[0] == pytest.approx(0.09622)
    assert r.pnls[1] == pytest.approx(-0.10342)
    assert r.total_cost == pytest.approx(0.00378 + 0.00342)
    assert r.n_trades == 2
    assert r.dates == [date(2020, 1, 2), date(2020, 1, 4)]
    assert r.daily_returns[0] == pytest.approx(0.09622)
    # gate.trade_tstat가 거래 PnL 리스트를 그대로 소비할 수 있어야 한다(레인2·3 통계단위).
    assert math.isfinite(gate.trade_tstat(r.pnls))


def test_run_trades_datetime_exit_buckets_by_day():
    trades = [
        Trade(datetime(2020, 1, 2, 9, 40), 10.0, datetime(2020, 1, 2, 15, 55),
              10.5, notional=0.5, symbol="Y"),
        Trade(datetime(2020, 1, 2, 10, 0), 20.0, datetime(2020, 1, 2, 15, 0),
              19.0, notional=0.5, symbol="Y"),
    ]
    r = research.run_trades(trades, cost=_zero_cost(), capital=1.0)
    assert r.dates == [date(2020, 1, 2)]                 # 같은 날 청산 → 1일에 합산
    assert r.daily_returns[0] == pytest.approx(sum(r.pnls) / 1.0)


# ── run_dca_overlay → XIRR ───────────────────────────────────────────────────
def test_dca_overlay_lump_sum_xirr_anchor():
    # 손 앵커: 100 일시 투자가 정확히 1년(366일, 2020 윤년) 뒤 200 → XIRR = 2^(365/366)−1.
    dates = [date(2020, 1, 1), date(2021, 1, 1)]
    closes = {"SPY": [100.0, 200.0]}
    tw = [{"SPY": 1.0}, {"SPY": 1.0}]
    r = research.run_dca_overlay(closes, dates, tw, monthly_usd=0.0, initial_usd=100.0,
                                 cost=_zero_cost())
    assert r.final_value == pytest.approx(200.0)
    assert r.deposits == [(date(2020, 1, 1), 100.0)]
    assert r.cashflows[0] == (date(2020, 1, 1), -100.0)
    assert r.cashflows[-1] == (date(2021, 1, 1), 200.0)
    x = gate.xirr(r.cashflows)
    assert x == pytest.approx(2.0 ** (365.0 / 366.0) - 1.0, abs=1e-4)
    # NPV(x) ≈ 0 자기일치
    npv = sum(cf * (1 + x) ** (-((d - dates[0]).days / 365.0)) for d, cf in r.cashflows)
    assert abs(npv) < 1e-6


def test_dca_overlay_monthly_cashflows_and_fx_drag():
    # 2년치 일별 상승장 + 월적립. 현금흐름 구조·총입금·XIRR 부호·FX 드래그 검증.
    start = date(2020, 1, 1)
    n = 730
    dates = [start + timedelta(days=i) for i in range(n)]
    closes = {"SPY": [100.0 * (1.0003 ** i) for i in range(n)]}   # ≈ +30%/yr
    tw = [{"SPY": 1.0}] * n
    n_months = len({(d.year, d.month) for d in dates})           # = 월적립 횟수(첫날 포함)

    cost = CostSpec(commission_bps=10.0, slippage_bps=5.0, fx_bps=20.0,
                    half_spread_bps={"SPY": 1.0})
    r = research.run_dca_overlay(closes, dates, tw, monthly_usd=35.0, initial_usd=32.0,
                                 cost=cost)
    assert len(r.deposits) == n_months
    assert r.total_deposited == pytest.approx(32.0 + 35.0 * n_months)
    assert r.cashflows[-1] == (dates[-1], r.final_value)
    assert all(cf < 0 for _, cf in r.cashflows[:-1])             # 입금은 음수
    assert r.total_fx_cost > 0
    x = gate.xirr(r.cashflows)
    assert math.isfinite(x) and x > 0                            # 상승장 → 양의 화폐가중수익

    # FX 드래그: FX=0이면 최종 평가액이 더 높아야 한다(입금비용만 다름).
    r_nofx = research.run_dca_overlay(
        closes, dates, tw, monthly_usd=35.0, initial_usd=32.0,
        cost=CostSpec(commission_bps=10.0, slippage_bps=5.0, fx_bps=0.0,
                      half_spread_bps={"SPY": 1.0}))
    assert r_nofx.final_value > r.final_value
    # gate.cashflows_from_dca(equity_curve 규칙 복원)와도 호환되는 곡선을 제공한다.
    assert r.equity_curve[-1] == (dates[-1], r.final_value)


# ── run_dca_overlay: rebalance 모드(매도 허용) ───────────────────────────────
def test_dca_rebalance_goes_to_cash_while_buy_only_cannot():
    # 100%→0% 리스크자산 전환. rebalance는 실제로 팔아 현금화, buy_only는 못 판다(과소 리스크).
    # 손계산: 상수가(100)로 4일 보유 후 day4 가격 2배 → '현금인지' 여부를 자본곡선으로 구분.
    dates = [date(2020, 3, 2), date(2020, 3, 3), date(2020, 3, 4),
             date(2020, 3, 5), date(2020, 3, 6)]                # 전부 3월 → 월적립 없음
    closes = {"RISK": [100.0, 100.0, 100.0, 100.0, 200.0]}      # day4에 +100%
    tw = [{"RISK": 1.0}, {"RISK": 1.0}, {"RISK": 0.0}, {"RISK": 0.0}, {"RISK": 0.0}]
    # trade_bps = 25(수수료)+3(기본 반호가)+5(슬리피지) = 33bps = 0.0033. FX=0.
    cost = CostSpec(commission_bps=25.0, slippage_bps=5.0, fx_bps=0.0,
                    default_half_spread_bps=3.0)

    # rebalance: exec_lag 기본 1, band 0.05로 day2 잔먼지 매매 차단.
    reb = research.run_dca_overlay(closes, dates, tw, monthly_usd=0.0, initial_usd=1000.0,
                                   cost=cost, mode="rebalance", rebalance_band=0.05)
    assert reb.mode == "rebalance"
    # 손계산 자본곡선:
    #  d0 입금 1000(현금) → d1 tw[0]=1.0 매수(노셔널 1000·33bps=3.3, 현금 −3.3) eq=996.7
    #  d2 tw[1]=1.0 괴리 0.33% < 밴드 → 무매매  d3 tw[2]=0.0 전량 매도(3.3) 현금화 eq=993.4
    #  d4 가격 2배지만 현금 보유 → 자본 불변(=현금 확인)
    assert reb.equity[0] == pytest.approx(1000.0)
    assert reb.equity[1] == pytest.approx(996.7)
    assert reb.equity[2] == pytest.approx(996.7)
    assert reb.equity[3] == pytest.approx(993.4)
    assert reb.equity[4] == pytest.approx(993.4)               # 현금 → 2배 점프에도 불변
    assert reb.final_value == pytest.approx(993.4)
    assert reb.trade_count == 2                                 # 매수 1 + 매도 1
    assert reb.n_buys == 1
    assert reb.total_turnover == pytest.approx(2000.0)          # 1000 매수 + 1000 매도
    assert reb.total_commission == pytest.approx(5.0)          # (1000+1000)·25bps
    assert reb.total_cost == pytest.approx(6.6)                # (1000+1000)·33bps
    assert reb.total_fx_cost == 0.0

    # buy_only: 절대 못 판다 → tw가 0%로 가도 100% 보유 유지 → day4 점프를 전량 수취.
    buy = research.run_dca_overlay(closes, dates, tw, monthly_usd=0.0, initial_usd=1000.0,
                                   cost=cost, mode="buy_only")
    assert buy.mode == "buy_only"
    assert buy.trade_count == 1 and buy.n_buys == 1             # 초기 편입 1회뿐
    assert buy.equity[3] == pytest.approx(1000.0 / 1.0033)      # 100% 보유(현금 0)
    assert buy.equity[4] == pytest.approx(2000.0 / 1.0033)      # 가격 2배 → 자본 2배
    assert buy.equity[4] == pytest.approx(2.0 * buy.equity[3])  # ≠ 현금(불변 아님)
    # 핵심 대비: 같은 신호에도 rebalance는 현금화(불변), buy_only는 보유(2배).
    assert reb.equity[4] == pytest.approx(reb.equity[3])
    assert buy.equity[4] > 1.9 * buy.equity[3]


def test_dca_rebalance_equals_run_weights_when_no_deposits_after_start():
    # 등가성: 시작 후 입금 0 + rebalance 모드면 run_dca_overlay ≈ run_weights × initial(동일 비용).
    n = 40
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    closes = {
        "SPY": [100.0 * (1.0 + 0.001 * i + 0.02 * math.sin(i / 2.0)) for i in range(n)],
        "TQQQ": [50.0 * (1.0 + 0.003 * i + 0.05 * math.sin(i / 1.5)) for i in range(n)],
    }
    tw = []
    for i in range(n):                                          # 매수·매도 모두 나오게 전환
        if i < 13:
            tw.append({"TQQQ": 1.0})
        elif i < 26:
            tw.append({"SPY": 0.5, "TQQQ": 0.5})
        else:
            tw.append({"SPY": 1.0})
    cost = CostSpec(commission_bps=25.0, slippage_bps=5.0, fx_bps=0.0,
                    default_half_spread_bps=3.0)
    cash = [0.0] + [0.0001] * (n - 1)
    initial = 1234.0

    rw = research.run_weights(closes, dates, tw, exec_lag=1, rebalance_band=0.0,
                              cost=cost, cash_rate=cash, start_equity=1.0)
    dca = research.run_dca_overlay(closes, dates, tw, monthly_usd=0.0, initial_usd=initial,
                                   cost=cost, cash_rate=cash, mode="rebalance",
                                   exec_lag=1, rebalance_band=0.0)
    assert dca.total_deposited == pytest.approx(initial)       # 시작 입금뿐
    assert len(dca.deposits) == 1
    for t in range(n):
        assert dca.equity[t] == pytest.approx(rw.equity[t] * initial, rel=1e-9, abs=1e-9)


# ── drawdown 헬퍼 ────────────────────────────────────────────────────────────
def test_unit_and_dollar_drawdown_helpers():
    eq = [100.0, 120.0, 90.0, 150.0, 120.0]
    dd = research.unit_drawdown(eq)
    assert dd[0] == 0.0 and dd[1] == 0.0                        # 신고점
    assert dd[2] == pytest.approx(90.0 / 120.0 - 1.0)          # −0.25
    assert dd[3] == 0.0                                         # 신고점 150
    assert dd[4] == pytest.approx(120.0 / 150.0 - 1.0)        # −0.20
    assert min(dd) == pytest.approx(-0.25)                     # MDD

    # 입금이 없으면 dollar_drawdown == unit_drawdown.
    assert research.dollar_drawdown(eq) == pytest.approx(research.unit_drawdown(eq))

    # 입금이 있으면 그만큼 고점 기준선이 올라 '입금=회복' 오인을 막는다.
    eq2 = [100.0, 80.0, 130.0, 110.0]
    dep = [0.0, 0.0, 40.0, 0.0]                                 # day2에 40 유입
    dd2 = research.dollar_drawdown(eq2, dep)
    assert dd2[1] == pytest.approx(-0.20)                       # 80/100−1
    assert dd2[2] == pytest.approx(130.0 / 140.0 - 1.0)       # peak=100+40=140 → 아직 낙폭
    raw = research.unit_drawdown(eq2)                           # 입금 무시하면 day2가 신고점(0)
    assert raw[2] == 0.0 and dd2[2] < 0.0
    # 길이 불일치는 에러.
    with pytest.raises(ValueError):
        research.dollar_drawdown(eq2, [0.0, 0.0])


# ── lookahead_guard ──────────────────────────────────────────────────────────
def _sma_cross_signal(closes, dates):
    """인과 신호: 종가 > 5일 SMA면 롱. 입력 ≤ t 만 참조."""
    spy = closes["SPY"]
    s = research.sma(spy, 5)
    out = []
    for t in range(len(dates)):
        out.append({"SPY": 1.0} if (s[t] is not None and spy[t] > s[t]) else {})
    return out


def _leaky_signal(closes, dates):
    """누수 신호: 각 t 비중이 '마지막(미래) 종가'에 의존 → 미래 정보 참조."""
    spy = closes["SPY"]
    last = spy[-1]
    return [{"SPY": min(1.0, spy[t] / last)} for t in range(len(dates))]


def _percentile_momentum_signal(closes, dates):
    """확장 백분위(무 look-ahead)로 만든 신호 — 가드를 통과해야 한다."""
    spy = closes["SPY"]
    pr = research.percentile_rank(spy)
    return [{"SPY": 1.0} if (pr[t] is not None and pr[t] > 0.5) else {}
            for t in range(len(dates))]


def _wavy_closes(n=60):
    return {"SPY": [100.0 + 10.0 * math.sin(i / 3.0) + 0.2 * i for i in range(n)]}


def test_lookahead_guard_passes_causal_signals():
    closes = _wavy_closes()
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(len(closes["SPY"]))]
    assert research.lookahead_guard(_sma_cross_signal, closes, dates) is True
    assert research.lookahead_guard(_percentile_momentum_signal, closes, dates) is True


def test_lookahead_guard_catches_leaky_signal():
    closes = _wavy_closes()
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(len(closes["SPY"]))]
    with pytest.raises(research.LookaheadLeak):
        research.lookahead_guard(_leaky_signal, closes, dates)


# ── 헬퍼 지표 ────────────────────────────────────────────────────────────────
def test_sma_ema_rsi_atr_values_and_warmup():
    assert research.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]

    e = research.ema([1.0, 1.0, 1.0], 2)
    assert e == [1.0, 1.0, 1.0]
    up = research.ema([1.0, 2.0, 3.0, 4.0], 3)
    assert up[0] == 1.0 and all(up[i] >= up[i - 1] for i in range(1, 4))

    # 전량 상승 → 손실 0 → Wilder RSI = 100, 워밍업(t<14)은 None.
    r = research.rsi([float(i) for i in range(1, 21)], 14)
    assert r[13] is None and r[14] == pytest.approx(100.0)

    highs = [10.0] * 5
    lows = [9.0] * 5
    closes = [9.5] * 5
    a = research.atr(highs, lows, closes, 3)              # TR 항상 1.0 → ATR 1.0
    assert a[1] is None and a[2] == pytest.approx(1.0) and a[4] == pytest.approx(1.0)


def test_rolling_zscore_percentile_realizedvol():
    assert research.rolling_max([1, 3, 2, 5, 4], 2) == [None, 3, 3, 5, 5]
    assert research.rolling_min([1, 3, 2, 5, 4], 2) == [None, 1, 2, 2, 4]

    # 확장 백분위(무 look-ahead): 각 t는 x[0..t]만 본다.
    assert research.percentile_rank([10, 20, 5, 30]) == pytest.approx(
        [1.0, 1.0, 1.0 / 3.0, 1.0])

    rets = [0.01, -0.01, 0.01, -0.01, 0.02]
    rv = research.realized_vol(rets, 2, annualize=True, ppy=252)
    assert rv[0] is None
    assert rv[1] == pytest.approx(
        research._std(rets[0:2], 1) * math.sqrt(252))

    z = research.zscore([1.0, 1.0, 1.0], 2)              # 표준편차 0 → None
    assert z[1] is None


def test_percentile_rank_is_causal_via_guard():
    # 확장 백분위로 만든 신호가 lookahead_guard를 통과 = look-ahead 없음(사양 §9).
    closes = _wavy_closes()
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(len(closes["SPY"]))]
    assert research.lookahead_guard(_percentile_momentum_signal, closes, dates) is True


# ── 캘린더(거래일 리스트 기반) ───────────────────────────────────────────────
def _weekdays(start: date, count: int) -> list[date]:
    out, d = [], start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_calendar_flags_from_trading_dates():
    # 2021-01-04(월)부터 평일 40개 → Jan/Feb 경계를 가진다.
    dates = _weekdays(date(2021, 1, 4), 40)
    ms = research.month_start_flags(dates)
    me = research.month_end_flags(dates)
    assert ms[0] is True
    assert me[-1] is True
    # 서로 다른 (연,월) 수 = 월초 플래그 수
    assert sum(ms) == len({(d.year, d.month) for d in dates})
    ftd = research.first_trading_day_of_month(dates)
    ltd = research.last_trading_day_of_month(dates)
    assert date(2021, 2, 1) in ftd                      # 2/1(월)이 2월 첫 거래일
    assert date(2021, 1, 29) in ltd                     # 1/29(금)이 1월 마지막 거래일

    tom = research.turn_of_month_flags(dates, days_before=1, days_after=2)
    idx = {d: i for i, d in enumerate(dates)}
    assert tom[idx[date(2021, 1, 29)]] is True          # 월말
    assert tom[idx[date(2021, 2, 1)]] is True           # 월초
    assert tom[idx[date(2021, 1, 14)]] is False         # 월 중간은 False


# ── 벤치마크 ─────────────────────────────────────────────────────────────────
def test_benchmark_26y_30assets_under_5s():
    import random
    rng = random.Random(0)
    n = 252 * 26                                          # 6552 거래일
    dates = [date(2000, 1, 1) + timedelta(days=i) for i in range(n)]
    syms = [f"S{i:02d}" for i in range(30)]
    closes: dict[str, list[float]] = {}
    for s in syms:
        px, series = 100.0, []
        for _ in range(n):
            px *= (1.0 + rng.gauss(0.0003, 0.01))
            series.append(px)
        closes[s] = series
    w = 1.0 / len(syms)
    tw = [{s: w for s in syms}] * n                      # 매일 균등 리밸런싱(회전 최대 스트레스)
    cost = CostSpec()

    t0 = time.perf_counter()
    r = research.run_weights(closes, dates, tw, exec_lag=1, rebalance_band=0.0, cost=cost)
    dt = time.perf_counter() - t0

    assert len(r.equity) == n
    assert r.trade_count > 0
    assert dt < 5.0, f"run_weights 26y×30 = {dt:.3f}s (>5s)"
