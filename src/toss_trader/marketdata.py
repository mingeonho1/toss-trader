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
    """실거래/포워드페이퍼용. 필드매핑은 토스 OpenAPI v1.1.5 실응답으로 확정.

    - candles: {candles:[{timestamp, openPrice, highPrice, lowPrice, closePrice,
      volume, currency}], nextBefore}. 최신→과거 내림차순으로 옴 → 오름차순 정렬해 반환.
    - prices: [{symbol, timestamp|null, lastPrice, currency}]. lastPrice가 현재가.
    """

    def __init__(self, client) -> None:  # client: TossClient
        self.client = client

    def history(self, symbol: str, count: int) -> list[Candle]:
        """일봉 count개. count>200이면 nextBefore 커서로 페이지네이션해 모은다."""
        collected: list[Candle] = []
        before: str | None = None
        seen_ts: set[str] = set()
        remaining = max(1, count)
        while remaining > 0:
            page = self.client.get_candles(symbol, interval="1d",
                                           count=min(200, remaining), before=before)
            rows = page.get("candles", []) if isinstance(page, dict) else (page or [])
            if not rows:
                break
            for r in rows:
                ts = str(r.get("timestamp", ""))
                if ts and ts in seen_ts:
                    continue
                seen_ts.add(ts)
                collected.append(self._norm_candle(symbol, r))
            before = page.get("nextBefore") if isinstance(page, dict) else None
            remaining = count - len(collected)
            if not before:
                break
        # API는 내림차순으로 주므로 날짜 오름차순으로 정렬(전략은 마지막이 최신이라 가정).
        collected.sort(key=lambda c: c.dt)
        return collected[-count:] if count > 0 else collected

    def price(self, symbol: str) -> float | None:
        raw = self.client.get_prices([symbol])
        rows = raw if isinstance(raw, list) else raw.get("prices", raw.get("items", []))
        for r in rows:
            if str(r.get("symbol", "")).upper() == symbol.upper():
                px = self._to_float(r.get("lastPrice"))
                return px if px > 0 else None
        return None

    # --- 정규화 (토스 OpenAPI v1.1.5 실응답 기준) ---
    @staticmethod
    def _to_float(v) -> float:
        if v is None:
            return 0.0
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            return 0.0

    def _norm_candle(self, symbol: str, r: dict) -> Candle:
        # timestamp 예: "2026-06-26T13:00:00.000+09:00" → 일봉 날짜는 앞 10자리.
        dt_raw = r.get("timestamp") or r.get("date")
        try:
            d = datetime.fromisoformat(str(dt_raw)[:10]).date()
        except (ValueError, TypeError):
            d = date.today()
        f = self._to_float
        return Candle(symbol, d,
                      f(r.get("openPrice")), f(r.get("highPrice")),
                      f(r.get("lowPrice")), f(r.get("closePrice")),
                      f(r.get("volume")))
