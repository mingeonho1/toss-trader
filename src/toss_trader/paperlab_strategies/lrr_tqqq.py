"""(a) lrr_tqqq — QQQ 종가 > SMA200 → 100% TQQQ, 아니면 100% 현금. 매일 평가, 2% 밴드(히스테리시스).

파라미터 동결(frozen): SMA=200, band=2%. QQQ 로 신호, TQQQ 로 매매. 다음 거래일 종가 체결.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ..research import sma

SMA_N = 200
BAND = 0.02


class LrrTqqq(Strategy):
    name = "lrr_tqqq"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["QQQ", "TQQQ"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        q = [c.close for c in history.get("QQQ", [])]
        if len(q) < SMA_N:
            return {}
        m = sma(q, SMA_N)[-1]
        if m is None or m <= 0:
            return {}
        px = q[-1]
        cur = state.get("pos", "cash")
        if px > m * (1 + BAND):
            cur = "tqqq"
        elif px < m * (1 - BAND):
            cur = "cash"
        # 밴드 안쪽이면 직전 포지션 유지(히스테리시스)
        state["pos"] = cur
        return {"TQQQ": 1.0} if cur == "tqqq" else {}
