"""바이앤홀드 — 비교 기준선. 목표비중 고정(첫 매수 후 거의 무거래)."""
from __future__ import annotations

from .base import Strategy, StrategyContext


class BuyAndHoldStrategy(Strategy):
    name = "buy_and_hold"

    def __init__(self, universe: list[str], weights: dict[str, float] | None = None) -> None:
        self.universe = list(universe)
        if weights:
            self.weights = dict(weights)
        else:
            w = 1.0 / len(self.universe)
            self.weights = {s: w for s in self.universe}

    def target_weights(self, ctx: StrategyContext) -> dict[str, float]:
        return dict(self.weights)
