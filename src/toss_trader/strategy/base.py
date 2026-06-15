"""전략 베이스. 전략은 '목표 비중(target weights)'만 결정한다.

목표비중 방식의 장점:
- 미국 소수점 매수(amount 단위)와 자연스럽게 맞고,
- 백테스트/페이퍼/실거래에서 동일한 리밸런싱 로직을 재사용할 수 있다.
weight 합은 1.0 이하(나머지는 현금). 빈 dict면 전량 현금.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from ..models import Candle


@dataclass
class StrategyContext:
    today: date
    # symbol -> 과거~당일까지의 일봉 리스트(오름차순). lookahead 방지를 위해 당일 종가까지만 포함.
    history: dict[str, list[Candle]] = field(default_factory=dict)

    def closes(self, symbol: str) -> list[float]:
        return [c.close for c in self.history.get(symbol, [])]

    def last_close(self, symbol: str) -> float | None:
        h = self.history.get(symbol)
        return h[-1].close if h else None


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def target_weights(self, ctx: StrategyContext) -> dict[str, float]:
        """오늘의 목표 비중. 예: {'AAPL': 0.5, 'MSFT': 0.5} 또는 {} (전량 현금)."""

    @property
    def warmup(self) -> int:
        """시그널 계산에 필요한 최소 일수. 백테스터가 이 기간은 건너뛴다."""
        return 0


def sma(values: list[float], window: int) -> float | None:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window
