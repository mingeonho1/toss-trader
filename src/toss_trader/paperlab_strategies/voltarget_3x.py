"""(d) voltarget_3x — 변동성 타깃 레버리지. lev = clip(0.25 / EWMA_vol(QQQ), 0, 3), TQQQ+현금.

주간 리밸런스(밴드 0.5 레버리지 단위). w_TQQQ = lev/3. 파라미터 동결:
target_vol=0.25(연율), EWMA span=20, clip[0,3], weekly, band=0.5.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ..research import ema, to_returns

TARGET_VOL = 0.25
EWMA_SPAN = 20
LEV_MIN, LEV_MAX = 0.0, 3.0
BAND = 0.5           # 레버리지 단위 변화가 이 값 이하이면 리밸런스 생략
PPY = 252
MIN_OBS = 21


def _ewma_vol(closes: Sequence[float]) -> float | None:
    rets = to_returns(closes)[1:]                # 첫 0.0 제외
    if len(rets) < MIN_OBS - 1:
        return None
    var = ema([r * r for r in rets], EWMA_SPAN)[-1]
    if var is None or var < 0:
        return None
    return math.sqrt(var * PPY)


class Voltarget3x(Strategy):
    name = "voltarget_3x"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["QQQ", "TQQQ"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        candles = history.get("QQQ", [])
        if len(candles) < MIN_OBS:
            return {}
        iso = candles[-1].dt.isocalendar()
        week = [iso[0], iso[1]]
        cur_lev = state.get("lev")

        # 주간 게이팅: 같은 주면 직전 레버리지 유지.
        if state.get("week") == week and cur_lev is not None:
            lev_use = cur_lev
        else:
            vol = _ewma_vol([c.close for c in candles])
            if vol is None or vol <= 0:
                return {} if cur_lev is None else _weights(cur_lev)
            cand = min(LEV_MAX, max(LEV_MIN, TARGET_VOL / vol))
            if cur_lev is None or abs(cand - cur_lev) > BAND:
                lev_use = cand
            else:
                lev_use = cur_lev
            state["week"] = week
        state["lev"] = lev_use
        return _weights(lev_use)


def _weights(lev: float) -> dict[str, float]:
    w = min(1.0, max(0.0, lev / 3.0))
    return {"TQQQ": w} if w > 0 else {}
