"""낙폭 가변 DCA 전략.

이 전략의 target_weights는 전체 포트폴리오 목표비중이 아니라 그 달 신규 자금의
매수 배분이다. 따라서 일반 리밸런스 엔진이 아니라 매수전용 accumulate 모드에서만 쓴다.
"""
from __future__ import annotations

from .base import Strategy, StrategyContext

DEFAULT_BASE_WEIGHTS = {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15}
DEFAULT_TIERS = (
    (0.10, {"QQQ": 0.75, "SCHD": 0.15, "GLD": 0.10}),
    (0.20, {"QQQ": 0.90, "SCHD": 0.10, "GLD": 0.00}),
)


class DrawdownTiltedDCAStrategy(Strategy):
    name = "drawdown_tilted_dca"

    def __init__(
        self,
        base_weights: dict[str, float] | None = None,
        *,
        ref_symbol: str = "QQQ",
        dd_lookback: int = 252,
        tiers=DEFAULT_TIERS,
    ) -> None:
        if dd_lookback <= 1:
            raise ValueError("dd_lookback은 2 이상이어야 합니다.")
        self.base_weights = self._normalize(base_weights or DEFAULT_BASE_WEIGHTS)
        self.ref_symbol = ref_symbol
        self.dd_lookback = dd_lookback
        self.tiers = tuple((float(th), self._normalize(weights)) for th, weights in tiers)

    @property
    def warmup(self) -> int:
        return self.dd_lookback + 1

    def target_weights(self, ctx: StrategyContext) -> dict[str, float]:
        closes = ctx.closes(self.ref_symbol)
        if len(closes) < self.dd_lookback:
            return dict(self.base_weights)
        window = closes[-self.dd_lookback:]
        high = max(window)
        last = window[-1]
        if high <= 0 or last <= 0:
            return dict(self.base_weights)

        drawdown = 1.0 - last / high
        selected = self.base_weights
        for threshold, weights in sorted(self.tiers, key=lambda x: x[0]):
            if drawdown + 1e-12 >= threshold:
                selected = weights
        return dict(selected)

    @staticmethod
    def _normalize(weights: dict[str, float]) -> dict[str, float]:
        clean = {sym: max(0.0, float(weight)) for sym, weight in weights.items()}
        total = sum(clean.values())
        if total <= 0:
            raise ValueError("weights 합은 양수여야 합니다.")
        return {sym: weight / total for sym, weight in clean.items()}
