from __future__ import annotations

import unittest
from datetime import date, timedelta

from toss_trader.models import Candle
from toss_trader.strategy import InverseVolatilityStrategy
from toss_trader.strategy.base import StrategyContext


def candles(symbol: str, closes: list[float], start: date = date(2026, 1, 1)) -> list[Candle]:
    return [
        Candle(symbol, start + timedelta(days=i), close, close, close, close, 1000)
        for i, close in enumerate(closes)
    ]


def path(start: float, returns: list[float]) -> list[float]:
    out = [start]
    for ret in returns:
        out.append(out[-1] * (1.0 + ret))
    return out


class InverseVolatilityStrategyTest(unittest.TestCase):
    def test_lower_vol_asset_gets_higher_weight(self) -> None:
        low = path(100, [0.003, -0.001] * 40)
        high = path(100, [0.03, -0.02] * 40)
        strat = InverseVolatilityStrategy(["LOW", "HIGH"], vol_lookback=20, max_weight=0.9)
        ctx = StrategyContext(today=date(2026, 3, 15), history={
            "LOW": candles("LOW", low),
            "HIGH": candles("HIGH", high),
        })

        weights = strat.target_weights(ctx)

        self.assertGreater(weights["LOW"], weights["HIGH"])
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_cap_is_applied_and_weights_sum_to_one(self) -> None:
        very_low = path(100, [0.001, -0.001] * 40)
        medium = path(100, [0.01, -0.008] * 40)
        high = path(100, [0.03, -0.02] * 40)
        strat = InverseVolatilityStrategy(["A", "B", "C"], vol_lookback=20, max_weight=0.4)
        ctx = StrategyContext(today=date(2026, 3, 15), history={
            "A": candles("A", very_low),
            "B": candles("B", medium),
            "C": candles("C", high),
        })

        weights = strat.target_weights(ctx)

        self.assertLessEqual(max(weights.values()), 0.400000001)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_insufficient_and_zero_vol_symbols_are_excluded(self) -> None:
        tradable = path(100, [0.01, -0.005] * 40)
        flat = [100.0] * 81
        short = [100.0, 101.0]
        strat = InverseVolatilityStrategy(["OK", "FLAT", "SHORT"], vol_lookback=20)
        ctx = StrategyContext(today=date(2026, 3, 15), history={
            "OK": candles("OK", tradable),
            "FLAT": candles("FLAT", flat),
            "SHORT": candles("SHORT", short),
        })

        weights = strat.target_weights(ctx)

        self.assertEqual(set(weights), {"OK"})
        self.assertAlmostEqual(weights["OK"], 1.0)

    def test_same_month_uses_same_anchor_weights(self) -> None:
        a = path(100, [0.004, -0.002] * 60)
        b = path(100, [0.015, -0.010] * 60)
        panel = {"A": candles("A", a), "B": candles("B", b)}
        strat = InverseVolatilityStrategy(["A", "B"], vol_lookback=20, max_weight=0.9)

        early = strat.target_weights(StrategyContext(today=date(2026, 3, 3), history=panel))
        late = strat.target_weights(StrategyContext(today=date(2026, 3, 28), history=panel))

        self.assertEqual(early, late)


if __name__ == "__main__":
    unittest.main()
