"""(e) mom_top5_ndx — 나스닥100 중 12-1 모멘텀 상위 5 동일비중, 월 1회 리밸런스.

12-1 모멘텀 = P(t−21)/P(t−252) − 1 (최근 1개월 스킵). 포워드-온리 → 생존편향 무관.
캐시에 데이터가 있는 이름만 후보(런타임 교집합). 파라미터 동결: lookback 252/skip 21/top5/월간.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ._universes import NDX_100

LOOKBACK = 252
SKIP = 21
TOP_N = 5


class MomTop5Ndx(Strategy):
    name = "mom_top5_ndx"
    fill_on = "close"

    def __init__(self, universe: Sequence[str] | None = None) -> None:
        self._universe = list(universe) if universe else list(NDX_100)

    def universe(self) -> list[str]:
        return list(self._universe)

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        # 오늘 날짜 = 유니버스에서 마지막 캔들이 있는 심볼의 최신 날짜.
        today = None
        for s in self._universe:
            h = history.get(s)
            if h:
                d = h[-1].dt
                if today is None or d > today:
                    today = d
        if today is None:
            return {}
        period = [today.year, today.month]
        if state.get("period") == period and state.get("weights"):
            return {k: float(v) for k, v in state["weights"].items()}

        scores: list[tuple[float, str]] = []
        for s in self._universe:
            closes = [c.close for c in history.get(s, [])]
            if len(closes) < LOOKBACK + 1:
                continue
            p_old = closes[-(LOOKBACK + 1)]
            p_recent = closes[-(SKIP + 1)]
            if p_old <= 0 or p_recent <= 0:
                continue
            scores.append((p_recent / p_old - 1.0, s))
        scores.sort(reverse=True)
        picks = [s for _, s in scores[:TOP_N]]
        weights = {s: 1.0 / len(picks) for s in picks} if picks else {}
        state["period"] = period
        state["weights"] = weights
        return dict(weights)
