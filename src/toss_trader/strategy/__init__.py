from .base import Strategy, StrategyContext
from .buy_and_hold import BuyAndHoldStrategy
from .dual_momentum import DualMomentumStrategy
from .regime_filter import RegimeFilterStrategy
from .sma_cross import SmaCrossStrategy

__all__ = [
    "Strategy", "StrategyContext", "SmaCrossStrategy",
    "BuyAndHoldStrategy", "DualMomentumStrategy", "RegimeFilterStrategy",
]
