#!/usr/bin/env python3
"""결정론적 자가검증 — 리스크관리/엔진 보호경로를 단정적으로 테스트(키 불필요).

CI처럼 매번 돌려 회귀를 막는다. 실패 시 비정상 종료.
"""
from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.broker import LiveBroker, PaperBroker  # noqa: E402
from toss_trader.costs import CostModel             # noqa: E402
from toss_trader.engine import TradingEngine        # noqa: E402
from toss_trader.journal import JournalWriter       # noqa: E402
from toss_trader.marketdata import ReplayMarketData  # noqa: E402
from toss_trader.models import Candle, Position     # noqa: E402
from toss_trader.risk import RiskConfig, RiskManager  # noqa: E402
from toss_trader.strategy.base import Strategy, StrategyContext  # noqa: E402

TMP = Path(__file__).resolve().parent.parent / "data" / "_selftest"


class AlwaysHold(Strategy):
    """무조건 한 종목 풀비중 — 청산은 오직 리스크매니저가 하도록."""
    name = "always_hold"

    def __init__(self, symbol: str):
        self.symbol = symbol

    def target_weights(self, ctx: StrategyContext) -> dict[str, float]:
        return {self.symbol: 1.0}


def test_risk_unit() -> None:
    rm = RiskManager(RiskConfig(stop_loss_pct=0.08, take_profit_pct=0.25,
                                trailing_stop_pct=0.12, daily_max_loss_pct=0.05,
                                min_trade_usd=5.0, max_position_weight=0.5))
    pos = {"X": Position("X", quantity=1, avg_price=100.0)}
    # 손절
    assert rm.evaluate_exits(pos, {"X": 91.0})[0][1].startswith("손절")
    # 익절
    assert rm.evaluate_exits(pos, {"X": 126.0})[0][1].startswith("익절")
    # 트레일링: 고점 120 찍고 -12% 이상 하락(105)
    rm.observe(pos, {"X": 120.0})
    assert rm.evaluate_exits(pos, {"X": 105.0})[0][1].startswith("트레일링")
    # 정상 구간은 청산 없음
    rm2 = RiskManager(RiskConfig(trailing_stop_pct=0.12))
    rm2.observe(pos, {"X": 100.0})
    assert rm2.evaluate_exits(pos, {"X": 101.0}) == []
    # 일일 손실 차단
    rm.start_day(1000.0)
    assert rm.check_daily_halt(940.0) is True
    assert rm.adjust_weights({"X": 1.0}, 940.0) == {}   # 차단 시 신규진입 없음
    # 비중 캡 + 조각거래 금지
    rm.start_day(1000.0)
    adj = rm.adjust_weights({"X": 1.0, "Y": 0.001}, 1000.0)
    assert adj["X"] == 0.5 and "Y" not in adj   # 캡 0.5, Y는 $1<min $5라 제외
    print("  ✓ RiskManager 단위테스트 통과 (손절/익절/트레일링/일일차단/캡/조각거래)")


def test_engine_stop_and_halt() -> None:
    if TMP.exists():
        shutil.rmtree(TMP)
    # X: 100 유지하다 day4에 -15% 급락 → 손절 + 일일차단 동시 유발
    days = [date(2025, 3, d) for d in (3, 4, 5, 6)]
    panel = {"X": [
        Candle("X", days[0], 100, 100, 100, 100.0, 1000),
        Candle("X", days[1], 100, 100, 100, 100.0, 1000),
        Candle("X", days[2], 100, 100, 100, 100.0, 1000),
        Candle("X", days[3], 85,  85,  85,  85.0, 1000),
    ]}
    data = ReplayMarketData(panel)
    broker = PaperBroker(cash=1000.0, cost_model=CostModel())
    risk = RiskManager(RiskConfig(stop_loss_pct=0.08, daily_max_loss_pct=0.05,
                                  trailing_stop_pct=0.0, max_position_weight=1.0))
    journal = JournalWriter(journal_dir=str(TMP / "journal"), data_dir=str(TMP))
    engine = TradingEngine(data=data, broker=broker, strategy=AlwaysHold("X"),
                           risk=risk, journal=journal, universe=["X"],
                           rebalance_threshold=0.02)

    reports = []
    for d in days:
        data.set_cursor(d)
        reports.append(engine.run_once(d))

    # day1: 매수로 포지션 형성
    assert any(f["side"] == "BUY" for f in reports[0].fills), "초기 매수 실패"
    # day4: 손절 청산 발생 + 포지션 0
    r4 = reports[3]
    assert any("손절" in reason for _, reason in r4.exits), f"손절 미발동: {r4.exits}"
    assert any(f["side"] == "SELL" for f in r4.fills), "손절 매도체결 없음"
    assert broker.position("X").quantity == 0.0, "손절 후 포지션이 남음"
    # day4: 일일차단으로 재매수 금지 (SELL만 있고 BUY 없음)
    assert r4.halted is True, "일일손실 차단 미발동"
    assert not any(f["side"] == "BUY" for f in r4.fills), "차단됐는데 재매수 발생"
    # 일지/로그 생성 확인
    assert (TMP / "runs.jsonl").exists() and list((TMP / "journal").glob("*.md"))
    shutil.rmtree(TMP)
    print("  ✓ 엔진 보호경로 통과 (손절 청산 → 일일차단 → 재매수 차단 → 일지기록)")


class _FakeSettings:
    def __init__(self, is_live: bool = True):
        self.is_live = is_live
        self.account_seq = "1"

    def require_account(self) -> None:
        pass


class FakeTossClient:
    """LiveBroker 검증용 인메모리 가짜 클라이언트. 실주문 없이 토스 응답 형태를 흉내낸다.

    체결은 항상 px=100에 즉시 FILLED, 수수료 0.1%(미국 실요율) 가정.
    create_order가 내부 잔고/보유를 갱신하므로 sync()가 실제처럼 반영된다.
    """
    PX = 100.0
    COMM = 0.001  # 0.1%

    def __init__(self, cash: float = 100.0, is_live: bool = True):
        self.s = _FakeSettings(is_live)
        self._cash = cash
        self._holdings: dict[str, dict] = {}
        self._orders: dict[str, dict] = {}
        self._n = 0
        self.create_calls: list[dict] = []

    def get_buying_power(self, currency: str = "USD") -> dict:
        return {"currency": "USD", "cashBuyingPower": str(self._cash)}

    def get_holdings(self, symbol: str | None = None) -> dict:
        items = [{"symbol": s, "marketCountry": "US", "currency": "USD",
                  "quantity": str(v["q"]), "averagePurchasePrice": str(v["avg"])}
                 for s, v in self._holdings.items() if v["q"] > 1e-12]
        return {"items": items}

    def create_order(self, symbol, side, *, order_type="LIMIT", quantity=None,
                     order_amount=None, price=None, time_in_force=None,
                     client_order_id=None, confirm_high_value=False) -> dict:
        self.create_calls.append({"symbol": symbol, "side": side.upper(),
                                  "order_type": order_type, "quantity": quantity,
                                  "order_amount": order_amount})
        self._n += 1
        oid = f"ord-{self._n}"
        px = self.PX
        if side.upper() == "BUY":
            if order_amount is not None:
                amt = float(order_amount)
                comm = amt * self.COMM
                qty = (amt - comm) / px
            else:
                qty = float(quantity)
                comm = qty * px * self.COMM
            cur = self._holdings.get(symbol, {"q": 0.0, "avg": 0.0})
            new_q = cur["q"] + qty
            cur["avg"] = (cur["avg"] * cur["q"] + qty * px) / new_q if new_q else 0.0
            cur["q"] = new_q
            self._holdings[symbol] = cur
            self._cash -= qty * px + comm
            filled = qty
        else:  # SELL
            qty = float(quantity)
            comm = qty * px * self.COMM
            cur = self._holdings.get(symbol, {"q": 0.0, "avg": 0.0})
            cur["q"] = max(0.0, cur["q"] - qty)
            self._holdings[symbol] = cur
            self._cash += qty * px - comm
            filled = qty
        self._orders[oid] = {
            "orderId": oid, "symbol": symbol, "side": side.upper(), "status": "FILLED",
            "execution": {"filledQuantity": str(filled), "averageFilledPrice": str(px),
                          "commission": str(round(comm, 6)), "tax": None},
        }
        return {"orderId": oid, "clientOrderId": client_order_id}

    def get_order(self, order_id: str) -> dict:
        return self._orders[order_id]


def test_engine_halt_holds_positions() -> None:
    """일일손실 차단은 '신규진입 중단'이어야 하며, 보유분을 강제 청산하면 안 된다.

    회귀 방지: 손절선(-8%)엔 안 닿지만 포트폴리오가 일일한도(-5%)를 넘긴 날,
    halt가 걸려도 보유 포지션이 유지되는지 검증. (과거 버그: halt 시 전량 시장가 투매)
    """
    if TMP.exists():
        shutil.rmtree(TMP)
    days = [date(2025, 5, d) for d in (1, 2)]
    panel = {"X": [
        Candle("X", days[0], 100, 100, 100, 100.0, 1000),
        Candle("X", days[1], 94,  94,  94,  94.0, 1000),   # -6%: 손절(-8%) 미달, 일일(-5%) 초과
    ]}
    data = ReplayMarketData(panel)
    broker = PaperBroker(cash=1000.0, cost_model=CostModel())
    risk = RiskManager(RiskConfig(stop_loss_pct=0.08, take_profit_pct=0.25,
                                  trailing_stop_pct=0.0, daily_max_loss_pct=0.05,
                                  max_position_weight=1.0, min_trade_usd=0.0))
    journal = JournalWriter(journal_dir=str(TMP / "journal"), data_dir=str(TMP))
    engine = TradingEngine(data=data, broker=broker, strategy=AlwaysHold("X"),
                           risk=risk, journal=journal, universe=["X"],
                           rebalance_threshold=0.02)

    data.set_cursor(days[0])
    r1 = engine.run_once(days[0])
    qty_day1 = broker.position("X").quantity   # 차단일(day2) 전 보유 수량 스냅샷
    assert qty_day1 > 0, f"day1 매수 실패: {r1.fills} errors={r1.errors}"

    data.set_cursor(days[1])
    r2 = engine.run_once(days[1])
    assert r2.halted is True, "일일손실 차단 미발동"
    assert not any("손절" in reason for _, reason in r2.exits), "이 시나리오는 손절 미발동이어야 함"
    # 핵심 단언: 차단일에 보유분이 강제 청산되면 안 된다.
    assert not any(f["side"] == "SELL" for f in r2.fills), \
        f"차단일에 강제 청산 발생(버그): {r2.fills}"
    assert broker.position("X").quantity == qty_day1, "차단일에 보유 수량이 변함(강제청산)"
    shutil.rmtree(TMP)
    print("  ✓ 엔진 차단=동결 통과 (손실차단일에 보유분 강제청산 없음 / 신규진입만 중단)")


def test_live_broker() -> None:
    today = date(2026, 6, 28)
    # 1) 안전장치: paper에서 require_live=True면 생성 거부
    try:
        LiveBroker(FakeTossClient(is_live=False))
        raise AssertionError("paper 모드에서 LiveBroker가 생성됨(거부돼야 함)")
    except RuntimeError:
        pass

    # 2) 금액 매수 → 실체결 기반 Fill + 동기화
    fc = FakeTossClient(cash=100.0)
    lb = LiveBroker(fc, require_live=False, poll_interval=0.0)
    assert lb.cash == 100.0 and lb.positions == {}
    buy = lb.submit_market_order("AAPL", "BUY", amount=50.0, ref_price=100.0, dt=today)
    assert buy and buy.side == "BUY"
    call = fc.create_calls[-1]
    assert call["order_type"] == "MARKET" and call["order_amount"] == "50.00", call
    # qty = (50 - 0.05)/100 = 0.4995, 수수료 0.05
    assert abs(buy.quantity - 0.4995) < 1e-9, buy.quantity
    assert abs(buy.cost - 0.05) < 1e-6, buy.cost
    assert abs(lb.positions["AAPL"].quantity - 0.4995) < 1e-9
    assert abs(lb.cash - 50.0) < 1e-6, lb.cash

    # 3) 수량 매도(US 소수점 MARKET SELL) → 실현손익/동기화
    sell = lb.submit_market_order("AAPL", "SELL", quantity=0.4995, ref_price=100.0, dt=today)
    assert sell and sell.side == "SELL"
    scall = fc.create_calls[-1]
    assert scall["order_type"] == "MARKET" and abs(float(scall["quantity"]) - 0.4995) < 1e-9
    assert abs(sell.realized_pnl) < 1e-6  # 100에 사서 100에 팖 → 실현손익 ~0
    assert "AAPL" not in lb.positions or lb.positions["AAPL"].quantity == 0.0
    print("  ✓ LiveBroker 통과 (live 가드 / 금액매수·수량매도 실체결 Fill / 잔고·포지션 동기화)")


def test_cost_from_commissions() -> None:
    commissions = [{"marketCountry": "KR", "commissionRate": "0"},
                   {"marketCountry": "US", "commissionRate": "0.1"}]
    cm = CostModel.from_commissions(commissions, market="US")
    assert abs(cm.commission_bps - 10.0) < 1e-9, cm.commission_bps  # 0.1% = 10bps
    print("  ✓ CostModel.from_commissions 통과 (US 0.1% → 10bps)")


def main() -> int:
    print("결정론적 자가검증 시작")
    test_risk_unit()
    test_engine_stop_and_halt()
    test_engine_halt_holds_positions()
    test_live_broker()
    test_cost_from_commissions()
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
