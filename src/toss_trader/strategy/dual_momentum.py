"""듀얼모멘텀 — 단일/소수 포지션 로테이션 (저회전, 소액 친화).

GEM(Global Equities Momentum) 변형:
- 상대모멘텀: risk_assets 중 lookback 수익률 상위 top_n 선택.
- 절대모멘텀: 선택 자산의 모멘텀이 안전자산(defensive) 모멘텀(없으면 0) 이하이면
  → 위험회피(defensive 보유, 없으면 현금).
- 월/분기 단위로만 신호를 갱신해 회전율을 구조적으로 억제(소액 비용의 핵심).

소액($32)에 맞춰 기본 top_n=1 → 항상 한 자산에 100%(현금 방치 없음, 매매 최소).
"""
from __future__ import annotations

from datetime import date

from .base import Strategy, StrategyContext


class DualMomentumStrategy(Strategy):
    name = "dual_momentum"

    def __init__(self, risk_assets: list[str], defensive: str | None = "IEF",
                 lookback: int = 252, top_n: int = 1, rebalance: str = "month") -> None:
        self.risk_assets = list(risk_assets)
        self.defensive = defensive
        self.lookback = lookback
        self.top_n = max(1, top_n)
        self.rebalance = rebalance
        self._key = None
        self._w: dict[str, float] = {}

    @property
    def warmup(self) -> int:
        return self.lookback + 1

    def _period_key(self, d: date):
        if self.rebalance == "month":
            return (d.year, d.month)
        if self.rebalance == "quarter":
            return (d.year, (d.month - 1) // 3)
        return d.toordinal()

    def _mom(self, closes: list[float]):
        if len(closes) <= self.lookback:
            return None
        past = closes[-1 - self.lookback]
        return (closes[-1] / past - 1.0) if past > 0 else None

    def target_weights(self, ctx: StrategyContext) -> dict[str, float]:
        key = self._period_key(ctx.today)
        if key == self._key:
            return dict(self._w)   # 같은 기간이면 직전 비중 유지(매매 억제)
        self._key = key

        moms = {s: m for s in self.risk_assets if (m := self._mom(ctx.closes(s))) is not None}
        if not moms:
            self._w = {}
            return {}
        ranked = sorted(moms, key=lambda s: moms[s], reverse=True)
        gate = self._mom(ctx.closes(self.defensive)) if self.defensive else None
        gate = gate if gate is not None else 0.0
        winners = [s for s in ranked[: self.top_n] if moms[s] > gate]
        if winners:
            w = {s: 1.0 / len(winners) for s in winners}
        elif self.defensive:
            w = {self.defensive: 1.0}
        else:
            w = {}
        self._w = w
        return dict(w)
