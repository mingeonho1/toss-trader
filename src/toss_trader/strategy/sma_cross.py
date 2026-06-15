"""이동평균 크로스 + 추세 필터 (스윙용 기본 전략).

규칙(종목별 독립):
- 단기 SMA > 장기 SMA  → 상승추세로 보고 '보유 후보'
- 추가 필터: 종가가 장기 SMA 위 (약세장 회피)
- 매 시점 보유 후보들을 동일비중으로 나눠 가짐. 후보 없으면 전량 현금.

이건 '검증된 단순 베이스라인'이다. 화려해서가 아니라, 비용 차감 후에도
기대값이 남는지 먼저 확인할 출발점으로 쓴다. 파라미터/유니버스는 추후 워크포워드로 최적화.
"""
from __future__ import annotations

from .base import Strategy, StrategyContext, sma


class SmaCrossStrategy(Strategy):
    name = "sma_cross"

    def __init__(self, universe: list[str], fast: int = 20, slow: int = 60,
                 max_positions: int = 3) -> None:
        if fast >= slow:
            raise ValueError("fast는 slow보다 작아야 합니다.")
        self.universe = universe
        self.fast = fast
        self.slow = slow
        self.max_positions = max_positions

    @property
    def warmup(self) -> int:
        return self.slow + 1

    def target_weights(self, ctx: StrategyContext) -> dict[str, float]:
        candidates: list[tuple[str, float]] = []
        for sym in self.universe:
            closes = ctx.closes(sym)
            f = sma(closes, self.fast)
            s = sma(closes, self.slow)
            if f is None or s is None:
                continue
            price = closes[-1]
            if f > s and price > s:
                # 추세 강도(단기/장기 이격)를 점수로 → 상위 종목 선택
                candidates.append((sym, f / s - 1.0))

        if not candidates:
            return {}
        candidates.sort(key=lambda x: x[1], reverse=True)
        chosen = [sym for sym, _ in candidates[: self.max_positions]]
        w = 1.0 / len(chosen)
        return {sym: w for sym in chosen}
