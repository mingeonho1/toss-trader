"""(B1) FTLT — "TQQQ For The Long Term" (namuan canonical). Lane A PASS(c12_b1_ftlt).

규칙(experiments/c12_composer.py build_b1 그대로, 파라미터 동결):
  SPY>SMA200(강세):  RSI10(TQQQ)>79 → UVXY / RSI10(SPXL)>80 → UVXY / else TQQQ.
  SPY≤SMA200(약세):  RSI10(TQQQ)<31 → TECL / RSI10(SPY)<30 → SPXL /
                     RSI10(UVXY)>74 → (>84면 추세블록, else UVXY) / else 추세블록.
  추세블록: TQQQ>SMA20 → (RSI10(SQQQ)<31 → SQQQ, else TQQQ) / else top1 RSI10(SQQQ,BSV).

체결: 신호 종가 t → 익일 t+1 종가(보수적, 통과 규약). 변형:
  - ``ftlt_moc``   : exec_lag=0 → **당일 종가** 체결(낙관적, Composer 방식).
  - ``ftlt_1x``    : 1x 섀도(레버리지 ETP 예탁금 회피) — 목표를 QQQ/SPY/PSQ 등으로 매핑, UVXY/BSV=현금.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from . import _composer as C

# 동결 파라미터(registry b1p).
RW = 10
SMA_LONG = 200
SMA_SHORT = 20
T_TQQQ = 79
T_SPXL = 80
T_LO = 31
T_SPY_LO = 30
T_UVXY = 74
T_UVXY_HI = 84
T_SQQQ = 31

SIGNAL_SYMBOLS = ["SPY", "TQQQ", "SPXL", "UVXY", "TECL", "SQQQ", "BSV"]


def _trend_block(history: Mapping[str, Sequence[Candle]]) -> dict[str, float] | None:
    tqqq = C.px_now(history, "TQQQ")
    tqqq_sma = C.sma_now(history, "TQQQ", SMA_SHORT)
    if not C.ready(tqqq, tqqq_sma):
        return None
    if tqqq > tqqq_sma:
        r_sqqq = C.rsi_now(history, "SQQQ", RW)
        if not C.ready(r_sqqq):
            return None
        return {"SQQQ": 1.0} if r_sqqq < T_SQQQ else {"TQQQ": 1.0}
    return C.pick1_by_rsi(history, ["SQQQ", "BSV"], RW, bottom=False)   # top1


def decide_ftlt(history: Mapping[str, Sequence[Candle]]) -> dict[str, float]:
    """오늘 FTLT 목표비중(단일 심볼 100% 또는 현금)."""
    spy = C.px_now(history, "SPY")
    spy_sma = C.sma_now(history, "SPY", SMA_LONG)
    if not C.ready(spy, spy_sma):
        return {}
    if spy > spy_sma:                                         # 강세 레짐
        r_tqqq = C.rsi_now(history, "TQQQ", RW)
        r_spxl = C.rsi_now(history, "SPXL", RW)
        if not C.ready(r_tqqq, r_spxl):
            return {}
        if r_tqqq > T_TQQQ:
            return {"UVXY": 1.0}
        if r_spxl > T_SPXL:
            return {"UVXY": 1.0}
        return {"TQQQ": 1.0}
    # 약세 레짐
    r_tqqq = C.rsi_now(history, "TQQQ", RW)
    r_spy = C.rsi_now(history, "SPY", RW)
    r_uvxy = C.rsi_now(history, "UVXY", RW)
    if not C.ready(r_tqqq, r_spy, r_uvxy):
        return {}
    if r_tqqq < T_LO:
        return {"TECL": 1.0}
    if r_spy < T_SPY_LO:
        return {"SPXL": 1.0}
    if r_uvxy > T_UVXY:
        if r_uvxy > T_UVXY_HI:
            return _trend_block(history) or {}
        return {"UVXY": 1.0}
    return _trend_block(history) or {}


class Ftlt(Strategy):
    name = "ftlt"
    fill_on = "close"          # 신호 종가 t → 익일 t+1 종가(보수적)

    def universe(self) -> list[str]:
        return list(SIGNAL_SYMBOLS)

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return decide_ftlt(history)


class FtltMoc(Ftlt):
    """(낙관적) 당일 종가 체결(exec_lag=0, Composer 방식). same-close 체결이라 성과가 관대할 수 있음."""
    name = "ftlt_moc"
    exec_lag = 0


class Ftlt1x(Strategy):
    """1x 섀도 — 레버리지 ETP 예탁금(₩10M) 없이 실행 가능한 비레버리지 버전. 신호는 동일(레버리지 심볼),
    체결은 1x 매핑(TQQQ→QQQ, SPXL→SPY, TECL→QQQ, SQQQ→PSQ, UVXY/BSV→현금)."""
    name = "ftlt_1x"
    fill_on = "close"
    group = "retail"           # 실계좌 가능: 보유는 1x(QQQ/SPY/PSQ)뿐, 레버리지 심볼은 신호에만.

    def universe(self) -> list[str]:
        return list(dict.fromkeys(SIGNAL_SYMBOLS + ["QQQ", "PSQ"]))

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return C.to_shadow_1x(decide_ftlt(history))
