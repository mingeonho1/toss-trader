"""시세 소스 추상화 — 엔진이 토스/리플레이 어디서든 동일하게 동작하게.

- ReplayMarketData: 합성/과거 패널을 날짜별로 재생(키 불필요, 페이퍼/백테스트 검증용).
- TossMarketData: TossClient 래핑. 응답 필드명은 명세 미확정이라 방어적으로 파싱하고,
  smoke_test로 실제 필드 확인 후 _norm_* 매핑을 확정한다(TODO 표시).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from .models import Candle


class MarketDataSource(Protocol):
    def history(self, symbol: str, count: int) -> list[Candle]: ...
    def price(self, symbol: str) -> float | None: ...


class ReplayMarketData:
    """패널을 '현재일(cursor)'까지만 보여준다. set_cursor로 하루씩 전진."""

    def __init__(self, panel: dict[str, list[Candle]]) -> None:
        self._panel = {s: sorted(c, key=lambda x: x.dt) for s, c in panel.items()}
        self.cursor: date | None = None

    def set_cursor(self, d: date) -> None:
        self.cursor = d

    def history(self, symbol: str, count: int) -> list[Candle]:
        candles = self._panel.get(symbol, [])
        if self.cursor is not None:
            candles = [c for c in candles if c.dt <= self.cursor]
        return candles[-count:] if count > 0 else candles

    def price(self, symbol: str) -> float | None:
        h = self.history(symbol, 1)
        return h[-1].close if h else None


class TossMarketData:
    """실거래/포워드페이퍼용. 키 발급 후 smoke_test 결과로 필드매핑 확정 예정."""

    def __init__(self, client) -> None:  # client: TossClient
        self.client = client

    def history(self, symbol: str, count: int) -> list[Candle]:
        raw = self.client.get_candles(symbol, interval="1d", count=min(200, count))
        rows = raw if isinstance(raw, list) else raw.get("candles", raw.get("items", []))
        return [self._norm_candle(symbol, r) for r in rows]

    def price(self, symbol: str) -> float | None:
        raw = self.client.get_prices([symbol])
        rows = raw if isinstance(raw, list) else raw.get("prices", raw.get("items", []))
        for r in rows:
            if str(r.get("symbol", r.get("ticker", ""))).upper() == symbol.upper():
                return self._to_float(r.get("price", r.get("close", r.get("last"))))
        return None

    # --- 방어적 정규화 (TODO: smoke_test로 실제 필드 확정) ---
    @staticmethod
    def _to_float(v) -> float:
        return float(str(v).replace(",", "")) if v is not None else 0.0

    def _norm_candle(self, symbol: str, r: dict) -> Candle:
        dt_raw = r.get("date") or r.get("dt") or r.get("timestamp") or r.get("baseDate")
        try:
            d = datetime.fromisoformat(str(dt_raw)[:10]).date()
        except (ValueError, TypeError):
            d = date.today()
        f = self._to_float
        return Candle(symbol, d,
                      f(r.get("open", r.get("o"))), f(r.get("high", r.get("h"))),
                      f(r.get("low", r.get("l"))), f(r.get("close", r.get("c"))),
                      f(r.get("volume", r.get("v", 0))))
