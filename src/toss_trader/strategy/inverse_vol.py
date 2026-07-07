"""역변동성 리스크 패리티 전략.

가격 방향을 예측하지 않고 최근 실현변동성의 역수에 비례해 목표 비중을 만든다.
월간 리밸런스용 전략이지만 인스턴스 상태에 의존하지 않는다. 같은 달 안에서는
그 달 첫 거래일 기준 히스토리로 계산해 반복 호출해도 같은 비중이 나온다.
"""
from __future__ import annotations

import math
from datetime import date

from .base import Strategy, StrategyContext


class InverseVolatilityStrategy(Strategy):
    name = "inverse_volatility"

    def __init__(self, symbols: list[str], vol_lookback: int = 63,
                 rebalance: str = "month", max_weight: float = 0.40) -> None:
        if vol_lookback <= 1:
            raise ValueError("vol_lookback은 2 이상이어야 합니다.")
        if max_weight <= 0:
            raise ValueError("max_weight는 양수여야 합니다.")
        self.symbols = list(symbols)
        self.vol_lookback = vol_lookback
        self.rebalance = rebalance
        self.max_weight = max_weight

    @property
    def warmup(self) -> int:
        return self.vol_lookback + 1

    def target_weights(self, ctx: StrategyContext) -> dict[str, float]:
        vols: dict[str, float] = {}
        for sym in self.symbols:
            closes = self._period_anchor_closes(ctx, sym)
            vol = self._realized_vol(closes)
            if vol is not None and vol > 0:
                vols[sym] = vol
        if not vols:
            return {}

        inv = {sym: 1.0 / vol for sym, vol in vols.items()}
        total = sum(inv.values())
        raw = {sym: value / total for sym, value in inv.items()}
        return self._apply_cap(raw)

    def _period_anchor_closes(self, ctx: StrategyContext, symbol: str) -> list[float]:
        candles = ctx.history.get(symbol, [])
        if not candles:
            return []
        anchor = self._anchor_date(candles, ctx.today)
        return [c.close for c in candles if c.dt <= anchor]

    def _anchor_date(self, candles, today: date) -> date:
        if self.rebalance == "month":
            same_period = [c.dt for c in candles if c.dt.year == today.year and c.dt.month == today.month]
            return same_period[0] if same_period else candles[-1].dt
        if self.rebalance == "quarter":
            q = (today.month - 1) // 3
            same_period = [
                c.dt for c in candles
                if c.dt.year == today.year and (c.dt.month - 1) // 3 == q
            ]
            return same_period[0] if same_period else candles[-1].dt
        return candles[-1].dt

    def _realized_vol(self, closes: list[float]) -> float | None:
        if len(closes) < self.vol_lookback + 1:
            return None
        window = closes[-self.vol_lookback - 1:]
        rets = [
            window[i] / window[i - 1] - 1.0
            for i in range(1, len(window))
            if window[i - 1] > 0
        ]
        if len(rets) < self.vol_lookback:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        vol = math.sqrt(var)
        return vol if vol > 0 else None

    def _apply_cap(self, raw: dict[str, float]) -> dict[str, float]:
        if not raw:
            return {}
        n = len(raw)
        cap = max(self.max_weight, 1.0 / n)
        remaining = dict(raw)
        capped: dict[str, float] = {}
        remaining_budget = 1.0

        while remaining:
            total = sum(remaining.values())
            if total <= 0:
                break
            proposed = {sym: remaining_budget * value / total for sym, value in remaining.items()}
            over = [sym for sym, weight in proposed.items() if weight > cap]
            if not over:
                capped.update(proposed)
                break
            for sym in over:
                capped[sym] = cap
                remaining_budget -= cap
                remaining.pop(sym, None)

        total = sum(capped.values())
        return {sym: weight / total for sym, weight in capped.items()} if total > 0 else {}
