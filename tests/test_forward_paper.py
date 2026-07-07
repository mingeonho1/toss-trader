"""forward_paper_compare 하네스 상태 전이 회귀 테스트.

핵심 시나리오: 첫 실행에서 가격 stale로 매매가 막혔을 때 initialized가
박제되지 않고, 다음 정상 실행에서 buy_once 최초 배치가 반드시 실행돼야 한다.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import forward_paper_compare as fpc  # noqa: E402

from toss_trader.costs import CostModel  # noqa: E402


class BuyOnceStalePriceTest(unittest.TestCase):
    def test_blocked_first_run_keeps_gate_open(self) -> None:
        today = date(2026, 7, 7)
        pstate = fpc._empty_portfolio(1000.0, today=today)
        self.assertFalse(pstate["initialized"])

        # 1일차: stale 가격으로 매매가 막힌 실행 — 체결 0건.
        broker = fpc._broker_from_state(pstate, CostModel())
        fpc._update_portfolio_state(pstate, broker, [], ts="t1",
                                    mark_initialized=False)
        self.assertFalse(pstate.get("initialized"),
                         "체결 없이 막힌 실행이 initialized를 박제하면 안 된다")

        # 2일차: 가격 정상 — buy_once 게이트가 열려 있어야 하고 실제 매수돼야 한다.
        broker2 = fpc._broker_from_state(pstate, CostModel())
        self.assertFalse(pstate.get("initialized"))
        fills = fpc._rebalance(broker2, {"QQQ": 1.0}, {"QQQ": 100.0},
                               date(2026, 7, 8), threshold=0.0, min_trade_usd=1.0)
        self.assertTrue(fills, "정상 가격 복귀 후 최초 배치 매수가 일어나야 한다")
        fpc._update_portfolio_state(pstate, broker2, fills, ts="t2",
                                    mark_initialized=True)
        self.assertTrue(pstate["initialized"])
        self.assertTrue(pstate["positions"].get("QQQ", {}).get("quantity", 0) > 0)

    def test_normal_first_run_marks_initialized(self) -> None:
        today = date(2026, 7, 7)
        pstate = fpc._empty_portfolio(1000.0, today=today)
        broker = fpc._broker_from_state(pstate, CostModel())
        fills = fpc._rebalance(broker, {"QQQ": 0.6, "GLD": 0.4},
                               {"QQQ": 100.0, "GLD": 50.0},
                               today, threshold=0.0, min_trade_usd=1.0)
        self.assertTrue(fills)
        fpc._update_portfolio_state(pstate, broker, fills, ts="t1",
                                    mark_initialized=True)
        self.assertTrue(pstate["initialized"])


class StaleBlockReasonTest(unittest.TestCase):
    def test_stale_required_symbol_blocks(self) -> None:
        pstate = fpc._empty_portfolio(1000.0, today=date(2026, 7, 7))
        broker = fpc._broker_from_state(pstate, CostModel())
        reason = fpc._stale_block_reason({"QQQ": 1.0}, broker, fresh_symbols={"SPY"})
        self.assertTrue(reason)

    def test_all_fresh_does_not_block(self) -> None:
        pstate = fpc._empty_portfolio(1000.0, today=date(2026, 7, 7))
        broker = fpc._broker_from_state(pstate, CostModel())
        reason = fpc._stale_block_reason({"QQQ": 1.0}, broker, fresh_symbols={"QQQ"})
        self.assertFalse(reason)


if __name__ == "__main__":
    unittest.main()
