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
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .config import Settings, get_settings
from .errors import AuthError, RateLimitError, TossAPIError, TossError
from .ratelimit import DEFAULT_GROUP, make_buckets

logger = logging.getLogger("toss_trader.client")

_USER_AGENT = "toss-trader/0.1 (+stdlib)"

# macOS의 python.org 빌드는 'Install Certificates.command' 미실행 시 기본 CA 번들이
# 비어 SSL 검증이 실패한다. 검증을 끄지 않고(자격증명 보호) 유효한 CA를 자동 탐색한다.
_CA_CANDIDATES = (
    "/etc/ssl/cert.pem",                          # macOS/BSD 시스템 번들
    "/etc/ssl/certs/ca-certificates.crt",         # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",           # RHEL/CentOS
    "/opt/homebrew/etc/openssl@3/cert.pem",       # Homebrew (Apple Silicon)
    "/usr/local/etc/openssl@3/cert.pem",          # Homebrew (Intel)
)


def _ca_count(ctx: ssl.SSLContext) -> int:
    try:
        return ctx.cert_store_stats().get("x509_ca", 0)
    except Exception:  # noqa: BLE001
        return 0


def build_ssl_context() -> ssl.SSLContext:
    """검증을 유지한 채 유효한 CA 번들을 가진 SSL 컨텍스트를 반환한다.

    우선순위: SSL_CERT_FILE(OpenSSL이 자동 반영) → 기본 컨텍스트 → 알려진 시스템 번들
    경로 → certifi(설치돼 있으면). 모두 실패하면 기본 컨텍스트를 그대로 반환(검증 유지).
    """
    ctx = ssl.create_default_context()
    if _ca_count(ctx) > 0:
        return ctx
    candidates: list[str] = []
    env = os.environ.get("SSL_CERT_FILE")
    if env:
        candidates.append(env)
    candidates.extend(_CA_CANDIDATES)
    try:
        import certifi  # 선택적: 있으면 활용, 없어도 무방(의존성 0 유지)
        candidates.append(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        if path and os.path.exists(path):
            try:
                c = ssl.create_default_context(cafile=path)
                if _ca_count(c) > 0:
                    logger.info("SSL CA 번들 사용: %s", path)
                    return c
            except Exception:  # noqa: BLE001
                continue
    logger.warning("유효한 CA 번들을 찾지 못해 기본 컨텍스트를 사용합니다(검증 유지).")
    return ctx


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
        self._ssl_ctx = build_ssl_context()
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
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=self._ssl_ctx) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # /oauth2/token은 BFF envelope이 아닌 OAuth2 표준 에러 포맷
            # {error, error_description, error_uri}을 사용한다(`error`로 식별).
            raw = e.read().decode(errors="replace")
            code, desc = "token-issue-failed", raw[:300]
            try:
                ej = json.loads(raw)
                code = ej.get("error", code)
                desc = ej.get("error_description") or desc
            except (ValueError, AttributeError):
                pass
            raise AuthError(e.code, code, f"토큰 발급 실패: {desc}") from e
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
            self._buckets.get(group, self._buckets[DEFAULT_GROUP]).acquire()
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
                with urllib.request.urlopen(req, timeout=self.timeout,
                                            context=self._ssl_ctx) as resp:
                    raw = resp.read().decode()
                    self._note_rate_limit(group, dict(resp.headers or {}))
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
    def _note_rate_limit(group: str, headers: dict[str, str]) -> None:
        """X-RateLimit 헤더로 적응 throttle. 버킷이 비었다고(Remaining<=0) 알려오면
        Reset 초만큼 선제 대기해 429를 사전에 회피한다(헤더 없으면 무동작)."""
        h = {k.lower(): v for k, v in headers.items()}
        rem, reset = h.get("x-ratelimit-remaining"), h.get("x-ratelimit-reset")
        if rem is None:
            return
        try:
            if int(rem) <= 0 and reset is not None:
                wait = min(5.0, max(0.0, float(reset)))
                if wait > 0:
                    logger.info("X-RateLimit 소진(%s) → %.2fs 선제 대기", group, wait)
                    time.sleep(wait)
        except (ValueError, TypeError):
            pass

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
        """현재가 일괄 조회. 미국 티커는 알파벳(AAPL), 최대 200개.

        응답: [{symbol, timestamp|null, lastPrice(decimal str), currency(KRW|USD)}, ...]
        """
        syms = symbols if isinstance(symbols, str) else ",".join(symbols)
        return self._request("GET", "/api/v1/prices", group="MARKET_DATA",
                             params={"symbols": syms})

    def get_candles(self, symbol: str, interval: str = "1d", count: int = 100,
                    before: str | None = None, adjusted: bool = True) -> Any:
        """OHLC 캔들. interval은 '1d'(스윙) 또는 '1m'. count 최대 200. before는 ISO8601 커서.

        응답: {candles: [{timestamp, openPrice, highPrice, lowPrice, closePrice,
        volume, currency}], nextBefore|null}. (별도 그룹 MARKET_DATA_CHART)
        """
        if interval not in ("1d", "1m"):
            raise ValueError("interval은 '1d' 또는 '1m'만 허용됩니다.")
        return self._request("GET", "/api/v1/candles", group="MARKET_DATA_CHART", params={
            "symbol": symbol, "interval": interval,
            "count": max(1, min(200, count)),
            "before": before,
            "adjusted": "true" if adjusted else "false",
        })

    def get_orderbook(self, symbol: str) -> Any:
        return self._request("GET", "/api/v1/orderbook", group="MARKET_DATA",
                             params={"symbol": symbol})

    def get_trades(self, symbol: str, count: int = 50) -> Any:
        """당일 최근 체결 내역. count 최대 50."""
        return self._request("GET", "/api/v1/trades", group="MARKET_DATA",
                             params={"symbol": symbol, "count": max(1, min(50, count))})

    def get_price_limits(self, symbol: str) -> Any:
        """상/하한가. 미국 주식은 가격제한이 없어 upper/lowerLimitPrice가 null."""
        return self._request("GET", "/api/v1/price-limits", group="MARKET_DATA",
                             params={"symbol": symbol})

    # ============================================================ Stock Info
    def get_stocks(self, symbols: list[str] | str) -> Any:
        """종목 기본 정보(다건). symbols는 콤마구분 최대 200개.

        응답: [{symbol, name, englishName, market, securityType, status,
        currency, sharesOutstanding, ...}, ...]
        """
        syms = symbols if isinstance(symbols, str) else ",".join(symbols)
        return self._request("GET", "/api/v1/stocks", group="STOCK",
                             params={"symbols": syms})

    def get_warnings(self, symbol: str) -> Any:
        """매수 유의사항/VI. symbol은 path 파라미터."""
        return self._request("GET", f"/api/v1/stocks/{symbol}/warnings", group="STOCK")

    # ========================================================== Market Info
    def get_market_calendar(self, market: str = "US", date: str | None = None) -> Any:
        """장 운영 정보(전일/당일/익일). date는 YYYY-MM-DD(옵션)."""
        market = market.upper()
        if market not in ("US", "KR"):
            raise ValueError("market은 'US' 또는 'KR'.")
        return self._request("GET", f"/api/v1/market-calendar/{market}",
                             group="MARKET_INFO",
                             params={"date": date} if date else None)

    def get_exchange_rate(self, base_currency: str = "USD", quote_currency: str = "KRW",
                          date_time: str | None = None) -> Any:
        """환율 조회. baseCurrency/quoteCurrency 필수(둘 다 KRW|USD).

        기본값 USD→KRW (1 USD = ? KRW). dateTime 미지정 시 현재 유효환율.
        응답: {baseCurrency, quoteCurrency, rate, midRate, basisPoint, ...}.
        """
        base_currency, quote_currency = base_currency.upper(), quote_currency.upper()
        for label, cur in (("base_currency", base_currency), ("quote_currency", quote_currency)):
            if cur not in ("KRW", "USD"):
                raise ValueError(f"{label}는 'KRW' 또는 'USD'.")
        return self._request("GET", "/api/v1/exchange-rate", group="MARKET_INFO", params={
            "baseCurrency": base_currency, "quoteCurrency": quote_currency,
            "dateTime": date_time,
        })

    # ====================================================== Account & Asset
    def get_accounts(self) -> Any:
        """계좌 목록. 응답 [{accountNo, accountSeq(int), accountType}, ...].
        accountSeq를 여기서 확인해 .env(ACCOUNT_SEQ)에 넣고 X-Tossinvest-Account로 쓴다."""
        return self._request("GET", "/api/v1/accounts", group="ACCOUNT")

    def get_holdings(self, symbol: str | None = None) -> Any:
        """보유 주식. 응답에 종목별 수량/평단/평가/손익과 계좌 합산 요약 포함."""
        return self._request("GET", "/api/v1/holdings", group="ASSET",
                             params={"symbol": symbol} if symbol else None,
                             with_account=True)

    def get_buying_power(self, currency: str = "USD") -> Any:
        """매수가능금액. currency(KRW|USD) 필수. 응답: {currency, cashBuyingPower}."""
        currency = currency.upper()
        if currency not in ("KRW", "USD"):
            raise ValueError("currency는 'KRW' 또는 'USD'.")
        return self._request("GET", "/api/v1/buying-power", group="ORDER_INFO",
                             params={"currency": currency}, with_account=True)

    def get_sellable_quantity(self, symbol: str) -> Any:
        """판매가능수량. 응답: {sellableQuantity}. (US는 소수점 가능)"""
        return self._request("GET", "/api/v1/sellable-quantity", group="ORDER_INFO",
                             params={"symbol": symbol}, with_account=True)

    def get_commissions(self) -> Any:
        """시장별 매매 수수료율. 응답: [{marketCountry(KR|US), commissionRate(%), ...}]."""
        return self._request("GET", "/api/v1/commissions", group="ORDER_INFO",
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
        confirm_high_value: bool = False,
    ) -> Any:
        """주문 생성. 응답: {orderId, clientOrderId|null}.

        - side: BUY | SELL,  order_type: LIMIT | MARKET
        - 수량 단위는 quantity, 미국 금액 단위(소수점 매수)는 order_amount 중 택1
        - order_amount(금액주문)는 US MARKET 전용이며 정규장에만 가능 → order_type=MARKET 강제
        - LIMIT은 price 필수, MARKET은 price 무시
        - quantity는 기본 양의 정수만(소수점은 US MARKET SELL에만 허용 — 서버 검증)
        - client_order_id: 멱등키. 미지정 시 자동 생성하여 네트워크 재시도 시 중복주문 방지.
        - confirm_high_value: 1억원 이상 주문 시 True 필요(착오주문 방지).
        """
        side = side.upper()
        order_type = order_type.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side는 BUY 또는 SELL.")
        if order_type not in ("LIMIT", "MARKET"):
            raise ValueError("order_type은 LIMIT 또는 MARKET.")
        if (quantity is None) == (order_amount is None):
            raise ValueError("quantity 또는 order_amount 중 정확히 하나를 지정하세요.")
        if order_amount is not None and order_type != "MARKET":
            raise ValueError("order_amount(금액주문)는 US MARKET 전용입니다 (order_type='MARKET').")
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
        if price is not None and order_type == "LIMIT":
            body["price"] = str(price)
        if time_in_force is not None:
            tif = time_in_force.upper()
            if tif not in ("DAY", "CLS"):
                raise ValueError("time_in_force는 DAY 또는 CLS.")
            body["timeInForce"] = tif
        if confirm_high_value:
            body["confirmHighValueOrder"] = True

        return self._request("POST", "/api/v1/orders", group="ORDER",
                             body=body, with_account=True)

    def cancel_order(self, order_id: str) -> Any:
        """주문 취소. 응답: {orderId} (취소로 새로 발급된 식별자)."""
        return self._request("POST", f"/api/v1/orders/{order_id}/cancel",
                             group="ORDER", with_account=True)

    def modify_order(self, order_id: str, *, order_type: str,
                     quantity: str | float | None = None,
                     price: str | float | None = None,
                     confirm_high_value: bool = False) -> Any:
        """주문 정정. orderType 필수(LIMIT|MARKET).

        - KR 주식: quantity 필수(양의 정수). US 주식: quantity 전달 불가(가격 변경만).
        - LIMIT으로 정정 시 price 필수. 응답: {orderId} (정정으로 새 식별자 발급).
        """
        order_type = order_type.upper()
        if order_type not in ("LIMIT", "MARKET"):
            raise ValueError("order_type은 LIMIT 또는 MARKET.")
        if order_type == "LIMIT" and price is None:
            raise ValueError("LIMIT으로 정정 시 price가 필요합니다.")
        body: dict[str, Any] = {"orderType": order_type}
        if quantity is not None:
            body["quantity"] = str(quantity)
        if price is not None and order_type == "LIMIT":
            body["price"] = str(price)
        if confirm_high_value:
            body["confirmHighValueOrder"] = True
        return self._request("POST", f"/api/v1/orders/{order_id}/modify",
                             group="ORDER", body=body, with_account=True)

    def get_order(self, order_id: str) -> Any:
        """주문 상세(모든 상태 조회 가능). execution(체결수량/평단/수수료/세금) 포함."""
        return self._request("GET", f"/api/v1/orders/{order_id}",
                             group="ORDER_HISTORY", with_account=True)

    def list_orders(self, status: str, *, symbol: str | None = None,
                    from_: str | None = None, to: str | None = None,
                    cursor: str | None = None, limit: int | None = None) -> Any:
        """주문 목록. status 필수: OPEN(진행중) | CLOSED(종료).

        응답: {orders: [...], nextCursor|null, hasNext}. CLOSED만 페이지네이션(cursor/limit).
        """
        status = status.upper()
        if status not in ("OPEN", "CLOSED"):
            raise ValueError("status는 OPEN 또는 CLOSED.")
        return self._request("GET", "/api/v1/orders", group="ORDER_HISTORY",
                             params={"status": status, "symbol": symbol,
                                     "from": from_, "to": to,
                                     "cursor": cursor, "limit": limit},
                             with_account=True)
