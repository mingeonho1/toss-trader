"""(A2) 200SMA +5/−3 버퍼 — 진입/청산 버퍼로 휩쏘 감소. Lane A PASS(c12_a2_buffer).

experiments/c12_trend_vol.py sig_a2 그대로(파라미터 동결 entry=0.05, exit=0.03):
  현금→투자: QQQ > SMA200×(1+0.05).
  투자→현금: QQQ < SMA200×(1−0.03).
  그 사이엔 직전 상태 유지(히스테리시스). 투자 시 100% TQQQ. 신호 종가 t → 익일 t+1 종가 체결.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ..research import sma

SMA_N = 200
ENTRY = 0.05
EXIT = 0.03
LEV = "TQQQ"


def decide_buffer(history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
    q = [c.close for c in history.get("QQQ", [])]
    if len(q) < SMA_N:
        return {}
    m = sma(q, SMA_N)[-1]
    if m is None or m <= 0:
        return {}
    px = q[-1]
    invested = bool(state.get("invested", False))
    if not invested and px > m * (1.0 + ENTRY):
        invested = True
    elif invested and px < m * (1.0 - EXIT):
        invested = False
    state["invested"] = invested
    return {LEV: 1.0} if invested else {}


class Sma200BufferTqqq(Strategy):
    name = "sma200_buffer_tqqq"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["QQQ", "TQQQ"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return decide_buffer(history, state)
