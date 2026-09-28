"""(b) lrr_tqqq_sqqq — lrr_tqqq 와 동일하나 200일선 아래면 100% SQQQ(하락 베팅, 인버스 매수).

파라미터 동결: SMA=200, band=2%. 위 밴드 → TQQQ, 아래 밴드 → SQQQ, 밴드 안쪽 → 직전 유지.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ..research import sma

SMA_N = 200
BAND = 0.02


class LrrTqqqSqqq(Strategy):
    name = "lrr_tqqq_sqqq"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["QQQ", "TQQQ", "SQQQ"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        q = [c.close for c in history.get("QQQ", [])]
        if len(q) < SMA_N:
            return {}
        m = sma(q, SMA_N)[-1]
        if m is None or m <= 0:
            return {}
        px = q[-1]
        cur = state.get("pos")
        if px > m * (1 + BAND):
            cur = "tqqq"
        elif px < m * (1 - BAND):
            cur = "sqqq"
        elif cur is None:                       # 최초·밴드 안쪽: 원시 레짐으로 초기화
            cur = "tqqq" if px >= m else "sqqq"
        state["pos"] = cur
        if cur == "tqqq":
            return {"TQQQ": 1.0}
        if cur == "sqqq":
            return {"SQQQ": 1.0}
        return {}
