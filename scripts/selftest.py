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

from toss_trader.broker import PaperBroker          # noqa: E402
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


def main() -> int:
    print("결정론적 자가검증 시작")
    test_risk_unit()
    test_engine_stop_and_halt()
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
