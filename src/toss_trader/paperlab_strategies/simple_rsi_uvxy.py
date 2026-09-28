"""(B5) Simple RSI — RSI10(TQQQ)>79 → UVXY, else TQQQ. Lane A PASS(c12_b5_simple).

experiments/c12_composer.py build_b5 그대로(파라미터 동결 rw=10/t_tqqq=79/defense=UVXY). B1/B2 의
ablation 기준선인데 단독으로도 PASS. 체결: 신호 종가 t → 익일 t+1 종가.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from . import _composer as C

RW = 10
T_TQQQ = 79
DEFENSE = "UVXY"


def decide_simple_rsi(history: Mapping[str, Sequence[Candle]]) -> dict[str, float]:
    r = C.rsi_now(history, "TQQQ", RW)
    if not C.ready(r):
        return {}
    return {DEFENSE: 1.0} if r > T_TQQQ else {"TQQQ": 1.0}


class SimpleRsiUvxy(Strategy):
    name = "simple_rsi_uvxy"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["TQQQ", "UVXY", "BIL"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return decide_simple_rsi(history)
