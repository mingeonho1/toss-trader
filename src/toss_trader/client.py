"""토스증권 OpenAPI REST 클라이언트 (표준 라이브러리만 사용).

source of truth: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json

설계 포인트
- OAuth2 client_credentials 토큰을 캐시하고 만료 60초 전 자동 재발급.
- 401(invalid/expired-token)은 토큰 1회 재발급 후 자동 재시도.
- 429는 Retry-After를 존중해 대기 후 재시도. 5xx는 지수 백오프.
- 그룹별 토큰버킷으로 호출 한도를 선제 제어.
- 주문 생성은 clientOrderId(멱등키)로 네트워크 재시도 시 중복주문 방지.
- 응답은 {"result": ...} envelope을 벗겨 result만 반환(OAuth 토큰 응답 제외).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .config import Settings, get_settings
from .errors import AuthError, RateLimitError, TossAPIError, TossError
from .ratelimit import make_buckets

logger = logging.getLogger("toss_trader.client")

_USER_AGENT = "toss-trader/0.1 (+stdlib)"


class TossClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 10.0,
        max_retries: int = 4,
    ) -> None:
        self.s = settings or get_settings()
        self.s.require_credentials()
        self.timeout = timeout
        self.max_retries = max_retries
        self._buckets = make_buckets()
        self._token: str | None = None
        self._token_exp: float = 0.0  # time.monotonic() 기준 만료 시각

    # ------------------------------------------------------------------ auth
    def _fetch_token(self) -> None:
        self._buckets["AUTH"].acquire()
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.s.client_id,
                "client_secret": self.s.client_secret,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.s.base_url}/oauth2/token",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise AuthError(e.code, "token-issue-failed",
                            f"토큰 발급 실패: {e.read().decode(errors='replace')}") from e
        except urllib.error.URLError as e:
            raise TossError(f"토큰 발급 네트워크 오류: {e}") from e

        self._token = payload["access_token"]
        # 만료 60초 전에 갱신되도록 여유를 둔다.
        expires_in = float(payload.get("expires_in", 1800))
        self._token_exp = time.monotonic() + max(30.0, expires_in - 60.0)
        logger.info("OAuth 토큰 발급 (expires_in=%ss)", int(expires_in))

    def _ensure_token(self) -> str:
        if self._token is None or time.monotonic() >= self._token_exp:
            self._fetch_token()
        assert self._token is not None
        return self._token

    # --------------------------------------------------------------- request
    def _request(
        self,
        method: str,
        path: str,
        *,
        group: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        with_account: bool = False,
    ) -> Any:
        url = f"{self.s.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url = f"{url}?{urllib.parse.urlencode(clean)}"

        attempt = 0
        token_refreshed = False
        while True:
            attempt += 1
            self._buckets.get(group, self._buckets["MARKET"]).acquire()
            token = self._ensure_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            }
            data = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(body).encode()
            if with_account:
                if not self.s.account_seq:
                    raise TossError("이 호출에는 TOSS_ACCOUNT_SEQ가 필요합니다.")
                headers["X-Tossinvest-Account"] = self.s.account_seq

            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode()
                return self._unwrap(raw)
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                err = self._parse_error(e.code, raw, dict(e.headers or {}))

                # 401: 토큰 1회 재발급 후 재시도
                if e.code == 401 and not token_refreshed:
                    logger.warning("401 %s → 토큰 재발급 후 재시도", err.code)
                    self._token = None
                    token_refreshed = True
                    continue

                # 429: Retry-After 존중
                if isinstance(err, RateLimitError) and attempt <= self.max_retries:
                    wait = err.retry_after
                    logger.warning("429 rate-limit → %.2fs 대기 후 재시도 (%d/%d)",
                                   wait, attempt, self.max_retries)
                    time.sleep(wait)
                    continue

                # 5xx: 지수 백오프
                if 500 <= e.code < 600 and attempt <= self.max_retries:
                    wait = min(8.0, 0.5 * 2 ** (attempt - 1))
                    logger.warning("%d 서버오류 → %.2fs 백오프 재시도 (%d/%d)",
                                   e.code, wait, attempt, self.max_retries)
                    time.sleep(wait)
                    continue

                raise err
            except urllib.error.URLError as e:
                # 네트워크 일시 오류는 백오프 재시도 (멱등 보장은 clientOrderId가 담당)
                if attempt <= self.max_retries:
                    wait = min(8.0, 0.5 * 2 ** (attempt - 1))
                    logger.warning("네트워크 오류(%s) → %.2fs 백오프 재시도 (%d/%d)",
                                   e, wait, attempt, self.max_retries)
                    time.sleep(wait)
                    continue
                raise TossError(f"네트워크 오류: {e}") from e

    @staticmethod
    def _unwrap(raw: str) -> Any:
        if not raw:
            return None
        payload = json.loads(raw)
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"]
        return payload

    @staticmethod
    def _parse_error(status: int, raw: str, headers: dict[str, str]) -> TossAPIError:
        code, message, data, request_id = "unknown", raw[:300], None, None
        try:
            err = json.loads(raw).get("error", {})
            code = err.get("code", code)
            message = err.get("message", message)
            data = err.get("data")
            request_id = err.get("requestId")
        except (ValueError, AttributeError):
            pass

        if status == 429:
            retry_after = 1.0
            for k, v in headers.items():
                if k.lower() == "retry-after":
                    try:
                        retry_after = float(v)
                    except ValueError:
                        pass
            return RateLimitError(status, code, message, data, request_id,
                                  retry_after=retry_after)
        if status == 401:
            return AuthError(status, code, message, data, request_id)
        return TossAPIError(status, code, message, data, request_id)

    # ===================================================== Market Data (읽기)
    def get_prices(self, symbols: list[str] | str) -> Any:
        """현재가 일괄 조회. 미국 티커는 알파벳(AAPL), 최대 200개."""
        syms = symbols if isinstance(symbols, str) else ",".join(symbols)
        return self._request("GET", "/api/v1/prices", group="MARKET",
                             params={"symbols": syms})

    def get_candles(self, symbol: str, interval: str = "1d", count: int = 100,
                    before: str | None = None, adjusted: bool = True) -> Any:
        """OHLC 캔들. interval은 '1d'(스윙) 또는 '1m'. count 최대 200. before는 ISO8601 커서."""
        if interval not in ("1d", "1m"):
            raise ValueError("interval은 '1d' 또는 '1m'만 허용됩니다.")
        return self._request("GET", "/api/v1/candles", group="MARKET", params={
            "symbol": symbol, "interval": interval,
            "count": max(1, min(200, count)),
            "before": before,
            "adjusted": "true" if adjusted else "false",
        })

    def get_orderbook(self, symbol: str) -> Any:
        return self._request("GET", "/api/v1/orderbook", group="MARKET",
                             params={"symbol": symbol})

    def get_trades(self, symbol: str) -> Any:
        return self._request("GET", "/api/v1/trades", group="MARKET",
                             params={"symbol": symbol})

    def get_price_limits(self, symbol: str) -> Any:
        return self._request("GET", "/api/v1/price-limits", group="MARKET",
                             params={"symbol": symbol})

    # ============================================================ Stock Info
    def get_stock_info(self, symbol: str | None = None) -> Any:
        return self._request("GET", "/api/v1/stocks", group="MARKET",
                             params={"symbol": symbol} if symbol else None)

    def get_warnings(self, symbol: str) -> Any:
        return self._request("GET", f"/api/v1/stocks/{symbol}/warnings", group="MARKET")

    # ========================================================== Market Info
    def get_market_calendar(self, market: str = "US") -> Any:
        market = market.upper()
        if market not in ("US", "KR"):
            raise ValueError("market은 'US' 또는 'KR'.")
        return self._request("GET", f"/api/v1/market-calendar/{market}", group="MARKET")

    def get_exchange_rate(self) -> Any:
        return self._request("GET", "/api/v1/exchange-rate", group="MARKET")

    # ====================================================== Account & Asset
    def get_accounts(self) -> Any:
        """계좌 목록. accountSeq를 여기서 확인해 .env에 넣는다."""
        return self._request("GET", "/api/v1/accounts", group="ACCOUNT")

    def get_holdings(self) -> Any:
        return self._request("GET", "/api/v1/holdings", group="ACCOUNT",
                             with_account=True)

    def get_buying_power(self, symbol: str | None = None) -> Any:
        return self._request("GET", "/api/v1/buying-power", group="ASSET",
                             params={"symbol": symbol} if symbol else None,
                             with_account=True)

    def get_sellable_quantity(self, symbol: str) -> Any:
        return self._request("GET", "/api/v1/sellable-quantity", group="ASSET",
                             params={"symbol": symbol}, with_account=True)

    def get_commissions(self) -> Any:
        return self._request("GET", "/api/v1/commissions", group="ASSET",
                             with_account=True)

    # ================================================================ Orders
    def create_order(
        self,
        symbol: str,
        side: str,
        *,
        order_type: str = "LIMIT",
        quantity: str | float | None = None,
        order_amount: str | float | None = None,
        price: str | float | None = None,
        time_in_force: str | None = None,
        client_order_id: str | None = None,
    ) -> Any:
        """주문 생성.

        - side: BUY | SELL,  order_type: LIMIT | MARKET
        - 수량 단위는 quantity, 미국 금액 단위(소수점 매수)는 order_amount 중 택1
        - LIMIT은 price 필수
        - client_order_id: 멱등키. 미지정 시 자동 생성하여 중복주문을 방지한다.
        """
        side = side.upper()
        order_type = order_type.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side는 BUY 또는 SELL.")
        if order_type not in ("LIMIT", "MARKET"):
            raise ValueError("order_type은 LIMIT 또는 MARKET.")
        if (quantity is None) == (order_amount is None):
            raise ValueError("quantity 또는 order_amount 중 정확히 하나를 지정하세요.")
        if order_type == "LIMIT" and price is None:
            raise ValueError("LIMIT 주문은 price가 필요합니다.")

        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "clientOrderId": client_order_id or f"tt-{uuid.uuid4().hex}",
        }
        if quantity is not None:
            body["quantity"] = str(quantity)
        if order_amount is not None:
            body["orderAmount"] = str(order_amount)
        if price is not None:
            body["price"] = str(price)
        if time_in_force is not None:
            tif = time_in_force.upper()
            if tif not in ("DAY", "CLS"):
                raise ValueError("time_in_force는 DAY 또는 CLS.")
            body["timeInForce"] = tif

        return self._request("POST", "/api/v1/orders", group="ORDER",
                             body=body, with_account=True)

    def cancel_order(self, order_id: str) -> Any:
        return self._request("POST", f"/api/v1/orders/{order_id}/cancel",
                             group="ORDER", with_account=True)

    def modify_order(self, order_id: str, *, quantity: str | float | None = None,
                     price: str | float | None = None) -> Any:
        body: dict[str, Any] = {}
        if quantity is not None:
            body["quantity"] = str(quantity)
        if price is not None:
            body["price"] = str(price)
        if not body:
            raise ValueError("정정할 quantity 또는 price를 지정하세요.")
        return self._request("POST", f"/api/v1/orders/{order_id}/modify",
                             group="ORDER", body=body, with_account=True)

    def get_order(self, order_id: str) -> Any:
        return self._request("GET", f"/api/v1/orders/{order_id}",
                             group="ORDER", with_account=True)

    def list_orders(self, **params: Any) -> Any:
        return self._request("GET", "/api/v1/orders", group="ORDER",
                             params=params or None, with_account=True)
