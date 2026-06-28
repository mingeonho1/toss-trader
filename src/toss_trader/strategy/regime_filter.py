"""레짐 필터 — 시장 추세 게이트로 약세장만 회피하는 오버레이.

시장 대표자산(market)의 종가가 ma일 이동평균 위면 inner 전략을 그대로 따르고,
아래면 위험회피(defensive 보유, 없으면 현금). 월/분기 단위로만 판단해 휩쏘(경계선
진동)와 회전율을 억제한다. B&H의 큰 낙폭을 깎되 강세장 노출은 대부분 유지하는 게 목표.
"""
from __future__ import annotations

from datetime import date

from .base import Strategy, StrategyContext, sma


class RegimeFilterStrategy(Strategy):
    name = "regime_filter"

    def __init__(self, inner: Strategy, market: str = "SPY", ma: int = 200,
                 defensive: str | None = None, rebalance: str = "month") -> None:
        self.inner = inner
        self.market = market
        self.ma = ma
        self.defensive = defensive
        self.rebalance = rebalance
        self._key = None
        self._w: dict[str, float] = {}

    @property
    def warmup(self) -> int:
        return max(self.ma + 1, self.inner.warmup)

    def _period_key(self, d: date):
        if self.rebalance == "month":
            return (d.year, d.month)
        if self.rebalance == "quarter":
            return (d.year, (d.month - 1) // 3)
        return d.toordinal()

    def target_weights(self, ctx: StrategyContext) -> dict[str, float]:
        key = self._period_key(ctx.today)
        if key == self._key:
            return dict(self._w)
        self._key = key

        closes = ctx.closes(self.market)
        ma_val = sma(closes, self.ma)
        if ma_val is None:
            self._w = {}
            return {}
        if closes[-1] >= ma_val:        # 리스크온: 추세 위
            w = self.inner.target_weights(ctx)
        else:                            # 리스크오프: 추세 아래 → 회피
            w = {self.defensive: 1.0} if self.defensive else {}
        self._w = w
        return dict(w)
