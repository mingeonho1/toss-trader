"""(B2) Holy Grail — TQQQ 자체 200SMA 레짐. Lane A PASS(c12_b2_holygrail).

규칙(experiments/c12_composer.py build_b2, 파라미터 동결 rw=10/sma_long=200/sma_short=20/t_tqqq=79/
t_lo=31/t_soxl=30):
  TQQQ>SMA200:  RSI10(TQQQ)>79 → UVXY / else TQQQ.
  TQQQ≤SMA200:  RSI10(TQQQ)<31 → TECL / RSI10(SOXL)<30 → SOXL /
                TQQQ<SMA20 → top1 RSI10(SQQQ,BSV) / else TQQQ.
체결: 신호 종가 t → 익일 t+1 종가.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from . import _composer as C

RW = 10
SMA_LONG = 200
SMA_SHORT = 20
T_TQQQ = 79
T_LO = 31
T_SOXL = 30

SIGNAL_SYMBOLS = ["TQQQ", "UVXY", "TECL", "SOXL", "SQQQ", "BSV"]


def decide_holy_grail(history: Mapping[str, Sequence[Candle]]) -> dict[str, float]:
    tqqq = C.px_now(history, "TQQQ")
    tqqq_l = C.sma_now(history, "TQQQ", SMA_LONG)
    if not C.ready(tqqq, tqqq_l):
        return {}
    if tqqq > tqqq_l:
        r_tqqq = C.rsi_now(history, "TQQQ", RW)
        if not C.ready(r_tqqq):
            return {}
        return {"UVXY": 1.0} if r_tqqq > T_TQQQ else {"TQQQ": 1.0}
    r_tqqq = C.rsi_now(history, "TQQQ", RW)
    r_soxl = C.rsi_now(history, "SOXL", RW)
    tqqq_s = C.sma_now(history, "TQQQ", SMA_SHORT)
    if not C.ready(r_tqqq, r_soxl, tqqq_s):
        return {}
    if r_tqqq < T_LO:
        return {"TECL": 1.0}
    if r_soxl < T_SOXL:
        return {"SOXL": 1.0}
    if tqqq < tqqq_s:
        return C.pick1_by_rsi(history, ["SQQQ", "BSV"], RW, bottom=False) or {}
    return {"TQQQ": 1.0}


class HolyGrail(Strategy):
    name = "holy_grail"
    fill_on = "close"

    def universe(self) -> list[str]:
        return list(SIGNAL_SYMBOLS)

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return decide_holy_grail(history)
