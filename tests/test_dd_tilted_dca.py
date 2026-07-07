from __future__ import annotations

import unittest
from datetime import date, timedelta

from toss_trader.models import Candle
from toss_trader.strategy import DrawdownTiltedDCAStrategy
from toss_trader.strategy.base import StrategyContext


def candles(symbol: str, closes: list[float], start: date = date(2025, 1, 1)) -> list[Candle]:
    return [
        Candle(symbol, start + timedelta(days=i), close, close, close, close, 1000)
        for i, close in enumerate(closes)
    ]


def ctx_for(closes: list[float]) -> StrategyContext:
    cs = candles("QQQ", closes)
    return StrategyContext(today=cs[-1].dt, history={"QQQ": cs})


class DrawdownTiltedDCATest(unittest.TestCase):
    def test_drawdown_tiers(self) -> None:
        strat = DrawdownTiltedDCAStrategy()

        self.assertEqual(strat.target_weights(ctx_for([100.0] * 251 + [95.0])),
                         {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15})
        self.assertEqual(strat.target_weights(ctx_for([100.0] * 251 + [85.0])),
                         {"QQQ": 0.75, "SCHD": 0.15, "GLD": 0.10})
        self.assertEqual(strat.target_weights(ctx_for([100.0] * 251 + [75.0])),
                         {"QQQ": 0.90, "SCHD": 0.10, "GLD": 0.00})

    def test_boundary_values_are_inclusive(self) -> None:
        strat = DrawdownTiltedDCAStrategy()

        self.assertEqual(strat.target_weights(ctx_for([100.0] * 251 + [90.0])),
                         {"QQQ": 0.75, "SCHD": 0.15, "GLD": 0.10})
        self.assertEqual(strat.target_weights(ctx_for([100.0] * 251 + [80.0])),
                         {"QQQ": 0.90, "SCHD": 0.10, "GLD": 0.00})

    def test_insufficient_history_falls_back_to_baseline(self) -> None:
        strat = DrawdownTiltedDCAStrategy()

        self.assertEqual(strat.target_weights(ctx_for([100.0, 70.0])),
                         {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15})

    def test_lookback_window_ignores_old_highs(self) -> None:
        strat = DrawdownTiltedDCAStrategy(dd_lookback=252)
        closes = [200.0] + [100.0] * 252

        self.assertEqual(strat.target_weights(ctx_for(closes)),
                         {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15})


if __name__ == "__main__":
    unittest.main()
