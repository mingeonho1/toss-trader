"""(c) rsi2_tqqq — QQQ>SMA200 & RSI2(QQQ)<10 → 100% TQQQ. 청산: QQQ 종가 > SMA5.

Larry Connors 식 RSI2 딥바이 + 200일 레짐 필터, TQQQ 로 발현. 파라미터 동결: 200/5/RSI2<10.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ..research import rsi, sma

SMA_REGIME = 200
SMA_EXIT = 5
RSI_N = 2
RSI_ENTER = 10.0


class Rsi2Tqqq(Strategy):
    name = "rsi2_tqqq"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["QQQ", "TQQQ"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        q = [c.close for c in history.get("QQQ", [])]
        if len(q) < SMA_REGIME + 1:
            return {}
        m200 = sma(q, SMA_REGIME)[-1]
        m5 = sma(q, SMA_EXIT)[-1]
        r2 = rsi(q, RSI_N)[-1]
        if m200 is None or m5 is None:
            return {}
        px = q[-1]
        cur = state.get("pos", "cash")
        if cur == "tqqq":
            if px > m5:                          # 청산 규칙
                cur = "cash"
        else:
            if px > m200 and r2 is not None and r2 < RSI_ENTER:
                cur = "tqqq"
        state["pos"] = cur
        return {"TQQQ": 1.0} if cur == "tqqq" else {}
