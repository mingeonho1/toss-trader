"""(f) hot_rvol_swing — 캐시 유니버스에서 종가 +5%↑ & RVOL>3 & 고점 근처 종목을 다음 시가에 매수,
5거래일 보유, 동시 최대 3포지션.

파라미터 동결: gain>5%, RVOL>3(직전 20일 평균거래량 대비), 종가≥고가×0.985, hold=5, max=3.
다음 '시가' 체결(``fill_on="open"``; 시가 없으면 엔진이 종가로 폴백). 동일비중.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ._universes import DEFAULT_SWING_UNIVERSE, ETF_LIKE

GAIN_MIN = 0.05
RVOL_MIN = 3.0
VOL_WINDOW = 20
NEAR_HIGH = 0.985
HOLD_DAYS = 5
MAX_POS = 3


class HotRvolSwing(Strategy):
    name = "hot_rvol_swing"
    fill_on = "open"

    def __init__(self, universe: Sequence[str] | None = None) -> None:
        base = list(universe) if universe else list(DEFAULT_SWING_UNIVERSE)
        self._universe = [s for s in base if s not in ETF_LIKE]

    def universe(self) -> list[str]:
        return list(self._universe)

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        holds: dict[str, int] = {k: int(v) for k, v in (state.get("holds") or {}).items()}

        # 1) 보유 종목 나이 +1, 5일 이상 보유분 청산.
        for s in list(holds):
            holds[s] += 1
            if holds[s] >= HOLD_DAYS:
                del holds[s]

        # 2) 새 신호 스캔(이미 보유중/여유슬롯 없으면 스킵).
        slots = MAX_POS - len(holds)
        if slots > 0:
            cands: list[tuple[float, str]] = []
            for s in self._universe:
                if s in holds:
                    continue
                h = history.get(s)
                if not h or len(h) < VOL_WINDOW + 2:
                    continue
                c = h[-1]
                prev = h[-2]
                if prev.close <= 0 or c.high <= 0 or c.high <= c.low:
                    continue
                gain = c.close / prev.close - 1.0
                if gain <= GAIN_MIN:
                    continue
                if c.close < NEAR_HIGH * c.high:               # 고점 근처 마감
                    continue
                prior_vol = [x.volume for x in h[-(VOL_WINDOW + 1):-1]]
                avg = sum(prior_vol) / len(prior_vol) if prior_vol else 0.0
                if avg <= 0:
                    continue
                rvol = c.volume / avg
                if rvol <= RVOL_MIN:
                    continue
                cands.append((rvol, s))
            cands.sort(reverse=True)                            # RVOL 높은 순
            for _, s in cands[:slots]:
                holds[s] = 0

        state["holds"] = holds
        if not holds:
            return {}
        w = 1.0 / len(holds)
        return {s: w for s in holds}
