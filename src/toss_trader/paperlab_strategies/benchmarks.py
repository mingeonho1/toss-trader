"""(g) 벤치마크 전략 — qqq_bh / tqqq_bh. 항상 100% 보유(사실상 1회 매수 후 홀드).

같은 브로커/수수료로 돌려 소액 수수료 드래그까지 리더보드에 노출한다.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy


class _BuyHold(Strategy):
    _symbol = ""

    def universe(self) -> list[str]:
        return [self._symbol]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        h = history.get(self._symbol)
        return {self._symbol: 1.0} if h else {}


class QqqBH(_BuyHold):
    name = "qqq_bh"
    fill_on = "close"
    _symbol = "QQQ"


class TqqqBH(_BuyHold):
    name = "tqqq_bh"
    fill_on = "close"
    _symbol = "TQQQ"
