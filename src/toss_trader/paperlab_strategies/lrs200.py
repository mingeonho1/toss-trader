"""(A1) LRS 200SMA — QQQ>SMA200 → 레버리지 else 현금(**밴드 0**). Lane A PASS(c12_a1_lrs).

experiments/c12_trend_vol.py sig_a1 그대로: ``_cross_state(QQQ, SMA200, band=0.0)`` →
QQQ>SMA200 이면 100% TQQQ, 아니면 현금. 매일 평가, 신호 종가 t → 익일 t+1 종가 체결.

주의 — 기존 ``lrr_tqqq`` 와 **동일하지 않다**(별칭 아님): lrr_tqqq 는 2% 히스테리시스 밴드를 쓰지만
A1(lrs200_tqqq)은 밴드 0(경계 즉시 반응). 그래서 별도 전략으로 등록한다.

``lrs200_qqq``: 1x 섀도(레버리지 ETP 예탁금 회피) — 같은 신호로 TQQQ 대신 QQQ 보유.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ..research import sma

SMA_N = 200


def decide_lrs(history: Mapping[str, Sequence[Candle]], state: dict[str, Any], lev: str) -> dict[str, float]:
    q = [c.close for c in history.get("QQQ", [])]
    if len(q) < SMA_N:
        return {}
    m = sma(q, SMA_N)[-1]
    if m is None or m <= 0:
        return {}
    px = q[-1]
    cur = state.get("pos", "cash")
    if px > m:                       # band=0: 경계 위 → 투자
        cur = "lev"
    elif px < m:                     # 경계 아래 → 현금
        cur = "cash"
    # px==m 이면 직전 유지(_cross_state 규약)
    state["pos"] = cur
    return {lev: 1.0} if cur == "lev" else {}


class Lrs200Tqqq(Strategy):
    name = "lrs200_tqqq"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["QQQ", "TQQQ"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return decide_lrs(history, state, "TQQQ")


class Lrs200Qqq(Strategy):
    """1x 섀도 — QQQ>SMA200 이면 QQQ 100%(레버리지 없이 실행 가능)."""
    name = "lrs200_qqq"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["QQQ"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return decide_lrs(history, state, "QQQ")
