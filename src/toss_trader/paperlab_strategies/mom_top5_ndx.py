"""(e) mom_top5_ndx — 나스닥100 중 12-1 모멘텀 상위 5 동일비중, 월 1회 리밸런스.

12-1 모멘텀 = P(t−21)/P(t−252) − 1 (최근 1개월 스킵). 포워드-온리 → 생존편향 무관.
캐시에 데이터가 있는 이름만 후보(런타임 교집합). 파라미터 동결: lookback 252/skip 21/top5/월간.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import MAX_BAR_GAP_BDAYS, Strategy, bday_gap
from ._universes import NDX_100

LOOKBACK = 252
SKIP = 21
TOP_N = 5
STALE_SESSIONS = 3          # 마지막 봉이 기준일보다 3세션 넘게 뒤처진(캐시 멈춤) 심볼은 후보 제외


def _lag_sessions(ref: Sequence[Candle], last_dt) -> int:
    """기준 히스토리(ref)에서 last_dt 이후 세션 수."""
    n = 0
    for c in reversed(ref):
        if c.dt <= last_dt:
            break
        n += 1
    return n


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
        ref: Sequence[Candle] = []
        for s in self._universe:
            h = history.get(s)
            if h:
                d = h[-1].dt
                if today is None or d > today:
                    today, ref = d, h
        if today is None:
            return {}

        def fresh(sym: str) -> bool:
            h = history.get(sym)
            return bool(h) and _lag_sessions(ref, h[-1].dt) <= STALE_SESSIONS

        period = [today.year, today.month]
        cached = state.get("weights")
        if state.get("period") == period and cached and all(fresh(k) for k in cached):
            return {k: float(v) for k, v in cached.items()}

        scores: list[tuple[float, str]] = []
        for s in self._universe:
            if not fresh(s):
                continue          # 멈춘 캐시(예: INTC 09-22) → 영원히 미체결 pending 방지
            hist = history.get(s, [])
            closes = [c.close for c in hist]
            if len(closes) < LOOKBACK + 1:
                continue
            win = hist[-(LOOKBACK + 1):]          # 룩백 창 안 캐시 구멍(>5영업일) → 왜곡 순위 방지(F15 리뷰)
            if any(bday_gap(a.dt, b.dt) > MAX_BAR_GAP_BDAYS for a, b in zip(win, win[1:])):
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
