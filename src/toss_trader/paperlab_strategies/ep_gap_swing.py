"""(F5) ep_gap_swing — Episodic Pivot / 실적 갭 지속(대형주, 다음 시가 진입).

무료 일봉 근사(docs/aggressive_strategy_catalog.md §F5, docs/intraday_shadow_rules.md §6.3):
스캔(종가 t): 갭 open(t)/close(t−1)−1 ≥ +10% ; 거래량 vol(t) ≥ 2×avg20(일봉이 장중창 근사) ;
비관심 close(t−1) ≤ 0.80×52주고 **또는** |close(t−1)/close(t−61)−1| < 15% ; close(t) > $5 ;
avg20 달러거래대금 > $20M. 진입: t+1 시가(``fill_on="open"``), 동일비중, 최대 5종목.
청산(근사): 최소 3거래일 보유 후 종가 < SMA20 이면 전량 ; 하드캡 40거래일. 3–5일 부분매도는 생략.
파라미터 전부 동결.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ..research import sma
from ._universes import NDX_100

GAP_MIN = 0.10
VOL_MULT = 2.0
VOL_WINDOW = 20
NEGLECT_DRAW = 0.20            # 52주 고점 대비 ≥20% 하락(비관심)
NEGLECT_RET = 0.15            # |60일 수익| < 15%
LOOKBACK_52W = 252
RET_WINDOW = 60
PRICE_MIN = 5.0
DOLLAR_VOL_MIN = 20_000_000.0
MAX_POS = 5
MIN_HOLD = 3
EXIT_SMA = 20
MAX_HOLD = 40
MIN_HISTORY = RET_WINDOW + 2


class EpGapSwing(Strategy):
    name = "ep_gap_swing"
    fill_on = "open"

    def __init__(self, universe: Sequence[str] | None = None) -> None:
        self._universe = list(universe) if universe else list(NDX_100)

    def universe(self) -> list[str]:
        return list(self._universe)

    def decide(self, history: Mapping[str, Sequence[Candle]],
               state: dict[str, Any]) -> dict[str, float]:
        holds: dict[str, int] = {k: int(v) for k, v in (state.get("holds") or {}).items()}

        today = None
        for s in self._universe:
            h = history.get(s)
            if h and (today is None or h[-1].dt > today):
                today = h[-1].dt
        if today is None:
            return {}

        # 1) 보유 종목 나이 +1, 청산 판정(최소보유 후 SMA20 하회, 또는 하드캡).
        for s in list(holds):
            holds[s] += 1
            h = history.get(s)
            if not h:
                continue
            held = holds[s]
            closes = [c.close for c in h]
            if held >= MAX_HOLD:
                del holds[s]
                continue
            if held >= MIN_HOLD and len(closes) >= EXIT_SMA:
                m = sma(closes, EXIT_SMA)[-1]
                if m is not None and closes[-1] < m:
                    del holds[s]

        # 2) 신규 스캔(여유 슬롯).
        slots = MAX_POS - len(holds)
        if slots > 0:
            cands: list[tuple[float, str]] = []
            for s in self._universe:
                if s in holds:
                    continue
                sig = self._entry_gap(history.get(s))
                if sig is not None:
                    cands.append((sig, s))
            cands.sort(reverse=True)                # 갭 큰 순
            for _, s in cands[:slots]:
                holds[s] = 0

        state["holds"] = holds
        if not holds:
            return {}
        w = 1.0 / len(holds)
        return {s: w for s in holds}

    @staticmethod
    def _entry_gap(h: Sequence[Candle] | None) -> float | None:
        """F5 진입 조건 충족 시 갭(정렬 키) 반환, 아니면 None."""
        if not h or len(h) < MIN_HISTORY:
            return None
        c = h[-1]
        prev = h[-2]
        if prev.close <= 0 or c.open <= 0 or c.close <= PRICE_MIN:
            return None
        gap = c.open / prev.close - 1.0
        if gap < GAP_MIN:
            return None
        prior_vol = [x.volume for x in h[-(VOL_WINDOW + 1):-1]]
        avg = sum(prior_vol) / len(prior_vol) if prior_vol else 0.0
        if avg <= 0 or c.volume < VOL_MULT * avg:
            return None
        dvals = [x.close * x.volume for x in h[-VOL_WINDOW:]]
        if not dvals or sum(dvals) / len(dvals) <= DOLLAR_VOL_MIN:
            return None
        window = min(LOOKBACK_52W, len(h) - 1)
        hi = max(x.high for x in h[-(window + 1):-1])
        neglected = hi > 0 and prev.close <= (1.0 - NEGLECT_DRAW) * hi
        if not neglected and len(h) >= RET_WINDOW + 2:
            base = h[-(RET_WINDOW + 2)].close
            if base > 0:
                neglected = abs(prev.close / base - 1.0) < NEGLECT_RET
        if not neglected:
            return None
        return gap
