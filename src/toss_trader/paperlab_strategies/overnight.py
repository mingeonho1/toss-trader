"""(G1) 오버나이트 — 종가 매수 → 익일 시가 매도(close→open 만 보유).

- `overnight_tqqq`: 매일 TQQQ 종가 매수, 익일 시가 매도(무필터 헤드라인판).
- `overnight_qqq`: 동일 타이밍의 1x 섀도(QQQ).

`overnight = True` 로 표시하면 :class:`~toss_trader.paperlab.PaperLab` 이 전용 경로로 실행한다
(종가 매수 · 익일 시가 매도 · 매수는 ≤$10 분할 무료). `decide` 는 오늘 밤 보유 여부만 반환한다
({sym:1.0}=보유, {}=현금). 200SMA 필터판은 **미등록 변형**(docs/intraday_shadow_rules.md §6.3).
파라미터 동결: 무필터, 전액 배치, 매일.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy


class _Overnight(Strategy):
    overnight = True
    fill_on = "open"          # 문서화용(전용 경로가 종가매수/시가매도를 직접 처리)
    _symbol = ""

    def universe(self) -> list[str]:
        return [self._symbol]

    def decide(self, history: Mapping[str, Sequence[Candle]],
               state: dict[str, Any]) -> dict[str, float]:
        # 무필터 헤드라인판: 데이터가 있는 한 매일 밤 보유.
        return {self._symbol: 1.0} if history.get(self._symbol) else {}


class OvernightTqqq(_Overnight):
    name = "overnight_tqqq"
    _symbol = "TQQQ"


class OvernightQqq(_Overnight):
    name = "overnight_qqq"
    _symbol = "QQQ"
