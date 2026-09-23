"""토스증권 OpenAPI REST 클라이언트 (표준 라이브러리만 사용).

source of truth: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json (v1.2.17)

설계 포인트
- OAuth2 client_credentials 토큰을 **파일 캐시(data/.token_cache.json, chmod 600, fcntl 락)**로
  여러 프로세스가 공유한다. 새 토큰 발급은 이전 토큰을 무효화하므로(스탬피드=상호 무효화),
  발급은 파일락 + 더블체크로 직렬화한다. 만료 60초 전 자동 재발급.
- 401(invalid/expired/revoked-token)은 캐시를 먼저 재확인(다른 프로세스가 갱신했을 수 있음) 후
  필요 시 1회 재발급하고 재시도.
- 429는 Retry-After를 존중해 대기 후 재시도. 5xx는 지수 백오프.
  409 `request-in-progress`(멱등키 처리중)는 백오프 재시도, 500 `maintenance`는
  data.retryAfterSeconds가 임계치 이하면 대기 후 재시도·초과면 즉시 실패.
  그 외 4xx 비즈니스 에러는 재시도하지 않는다.
- 그룹별 토큰버킷으로 호출 한도를 선제 제어(v1.2.17 Rate Limits Group).
- 주문 생성은 clientOrderId(멱등키)로 네트워크 재시도 시 중복주문 방지.
  422 `idempotency-key-conflict`(동일 cid·다른 본문)는 이미 접수된 것으로 보고 기존 주문을 조회한다.
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
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .errors import (
    AuthError,
    IdempotencyConflictError,
    MaintenanceError,
    RateLimitError,
    RequestInProgressError,
    TossAPIError,
    TossError,
)
from .ratelimit import DEFAULT_GROUP, make_buckets

try:
    import fcntl  # POSIX 전용(파일 락). Windows에선 없음 → 락 없이 동작.
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger("toss_trader.client")

_USER_AGENT = "toss-trader/0.1 (+stdlib)"
_TOKEN_SKEW = 60.0        # 만료 여유(초): 이 시간 이전에 재발급
_UNSET = object()         # token_cache_path 기본값 판별용 센티널

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


def _default_token_cache_path() -> Path:
    """프로젝트 루트의 data/.token_cache.json (cwd와 무관하게 안정적)."""
    return Path(__file__).resolve().parents[2] / "data" / ".token_cache.json"


def _leg(leg: Any) -> Any:
    """조건주문 leg(ConditionRequest) 정규화: 십진 필드를 문자열로, orderSide 대문자로."""
    if not isinstance(leg, dict):
        return leg
    out = dict(leg)
    for k in ("triggerPrice", "orderPrice"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    if out.get("orderSide"):
        out["orderSide"] = str(out["orderSide"]).upper()
    return out


class TossClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 10.0,
        max_retries: int = 4,
        maintenance_max_wait: float = 30.0,
        token_cache_path: Any = _UNSET,
        urlopen: Any = None,
        sleep: Any = None,
    ) -> None:
        self.s = settings or get_settings()
        self.s.require_credentials()
        self.timeout = timeout
        self.max_retries = max_retries
        self.maintenance_max_wait = maintenance_max_wait
        self._buckets = make_buckets()
        self._ssl_ctx = build_ssl_context()
        self._token: str | None = None
        self._token_exp: float = 0.0  # time.time()(월클록 unix) 기준 만료 시각(스큐 적용 전)
        # 주입 가능한 전송/대기 훅(테스트에서 네트워크·sleep 제거).
        self._urlopen = urlopen or self._default_urlopen
        self._sleep = sleep or time.sleep
        # 토큰 파일 캐시 경로: 기본=프로젝트 data/.token_cache.json,
        # 환경변수 TOSS_TOKEN_CACHE로 재정의, None이면 파일 캐시 비활성(메모리만).
        if token_cache_path is _UNSET:
            env = os.environ.get("TOSS_TOKEN_CACHE")
            token_cache_path = env if env else _default_token_cache_path()
        self._token_cache_path: Path | None = (
            Path(token_cache_path) if token_cache_path else None
        )

    def _default_urlopen(self, req: urllib.request.Request):
        return urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx)

    # ------------------------------------------------------------------ auth
    def _issue_token(self) -> None:
        """실제 OAuth 토큰 발급(네트워크) 후 메모리+파일 캐시에 기록."""
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
            with self._urlopen(req) as resp:
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
        expires_in = float(payload.get("expires_in", 1800))
        self._token_exp = time.time() + expires_in  # 월클록 절대만료(프로세스 간 공유용)
        self._write_token_cache(self._token, self._token_exp)
        logger.info("OAuth 토큰 발급 (expires_in=%ss)", int(expires_in))

    def _ensure_token(self) -> str:
        now = time.time()
        if self._token is not None and now < self._token_exp - _TOKEN_SKEW:
            return self._token
        # 다른 프로세스가 이미 발급했을 수 있으니 파일 캐시부터 확인.
        cached = self._read_token_cache()
        if cached and now < cached[1] - _TOKEN_SKEW:
            self._token, self._token_exp = cached
            return self._token
        self._fetch_token_locked(failed_token=None)
        assert self._token is not None
        return self._token

    def _fetch_token_locked(self, *, failed_token: str | None) -> None:
        """파일락 + 더블체크로 토큰 발급을 직렬화한다(동시 발급=상호 무효화 방지).

        failed_token이 주어지면(401 이후), 캐시에 그것과 다른 유효 토큰이 있으면 채택하고,
        아니면 새로 발급한다(실패 토큰 재사용 금지).
        """
        with self._token_lock():
            now = time.time()
            cached = self._read_token_cache()
            if cached and cached[0] != failed_token and now < cached[1] - _TOKEN_SKEW:
                self._token, self._token_exp = cached
                logger.info("토큰 캐시 채택(다른 프로세스 갱신분)")
                return
            self._issue_token()

    def _refresh_after_401(self, failed_token: str | None) -> None:
        """401 처리: 캐시에 최신 토큰이 있으면 채택, 없으면 재발급(실패 토큰 무효화)."""
        self._token = None
        now = time.time()
        cached = self._read_token_cache()
        if cached and cached[0] != failed_token and now < cached[1] - _TOKEN_SKEW:
            self._token, self._token_exp = cached
            logger.info("401 후 캐시의 최신 토큰 채택(다른 프로세스 갱신)")
            return
        self._fetch_token_locked(failed_token=failed_token)

    # --- 토큰 파일 캐시 (chmod 600 + fcntl 락) ---
    @contextmanager
    def _token_lock(self):
        if not self._token_cache_path or fcntl is None:
            yield
            return
        lock_path = self._token_cache_path.with_name(self._token_cache_path.name + ".lock")
        f = None
        try:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            f = open(lock_path, "w")  # noqa: SIM115
        except OSError:
            f = None
        try:
            if f is not None:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                except OSError:
                    pass
            yield
        finally:
            if f is not None:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                f.close()

    def _read_token_cache(self) -> tuple[str, float] | None:
        if not self._token_cache_path:
            return None
        try:
            with self._token_cache_path.open("r", encoding="utf-8") as f:
                if fcntl is not None:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    except OSError:
                        pass
                data = json.load(f)
            tok = data.get("access_token")
            exp = float(data.get("expires_at", 0))
            if tok:
                return str(tok), exp
        except (OSError, ValueError, TypeError):
            return None
        return None

    def _write_token_cache(self, token: str, expires_at: float) -> None:
        if not self._token_cache_path:
            return
        try:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            # 생성 시부터 0600. 이미 존재하면 chmod로 보정.
            fd = os.open(
                str(self._token_cache_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if fcntl is not None:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    except OSError:
                        pass
                json.dump({"access_token": token, "expires_at": expires_at}, f)
            os.chmod(self._token_cache_path, 0o600)
        except OSError as e:
            logger.warning("토큰 캐시 기록 실패(%s) — 메모리 캐시만 사용", e)

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
                with self._urlopen(req) as resp:
                    raw = resp.read().decode()
                    self._note_rate_limit(group, dict(resp.headers or {}))
                return self._unwrap(raw)
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                err = self._parse_error(e.code, raw, dict(e.headers or {}))

                # 401: 캐시 재확인 후 1회 재발급
                if e.code == 401 and not token_refreshed:
                    logger.warning("401 %s → 토큰 갱신 후 재시도", err.code)
                    token_refreshed = True
                    self._refresh_after_401(failed_token=token)
                    continue

                # 409 request-in-progress: 멱등키가 중복접수를 막으므로 백오프 재시도 안전
                if isinstance(err, RequestInProgressError) and attempt <= self.max_retries:
                    wait = min(8.0, 0.5 * 2 ** (attempt - 1))
                    logger.warning("409 request-in-progress → %.2fs 백오프 재시도 (%d/%d)",
                                   wait, attempt, self.max_retries)
                    self._sleep(wait)
                    continue

                # 429: Retry-After 존중
                if isinstance(err, RateLimitError) and attempt <= self.max_retries:
                    wait = err.retry_after
                    logger.warning("429 rate-limit → %.2fs 대기 후 재시도 (%d/%d)",
                                   wait, attempt, self.max_retries)
                    self._sleep(wait)
                    continue

                # 500 maintenance: retryAfterSeconds가 임계치 초과면 즉시 실패, 이하면 대기 재시도
                if isinstance(err, MaintenanceError):
                    ra = err.retry_after_seconds
                    if ra is not None and ra > self.maintenance_max_wait:
                        logger.error("maintenance retryAfter=%ss > 임계치 %ss → 즉시 실패",
                                     ra, self.maintenance_max_wait)
                        raise err
                    if attempt <= self.max_retries:
                        wait = ra if ra is not None else min(8.0, 0.5 * 2 ** (attempt - 1))
                        wait = min(self.maintenance_max_wait, wait)
                        logger.warning("maintenance → %.2fs 후 재시도 (%d/%d)",
                                       wait, attempt, self.max_retries)
                        self._sleep(wait)
                        continue
                    raise err

                # 그 외 5xx: 지수 백오프
                if 500 <= e.code < 600 and attempt <= self.max_retries:
                    wait = min(8.0, 0.5 * 2 ** (attempt - 1))
                    logger.warning("%d 서버오류(%s) → %.2fs 백오프 재시도 (%d/%d)",
                                   e.code, err.code, wait, attempt, self.max_retries)
                    self._sleep(wait)
                    continue

                # 그 외 4xx 비즈니스 에러는 재시도하지 않는다.
                raise err
            except urllib.error.URLError as e:
                # 네트워크 일시 오류는 백오프 재시도 (멱등 보장은 clientOrderId가 담당)
                if attempt <= self.max_retries:
                    wait = min(8.0, 0.5 * 2 ** (attempt - 1))
                    logger.warning("네트워크 오류(%s) → %.2fs 백오프 재시도 (%d/%d)",
                                   e, wait, attempt, self.max_retries)
                    self._sleep(wait)
                    continue
                raise TossError(f"네트워크 오류: {e}") from e

    def _note_rate_limit(self, group: str, headers: dict[str, str]) -> None:
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
                    self._sleep(wait)
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
        if code == "request-in-progress":
            return RequestInProgressError(status, code, message, data, request_id)
        if code == "idempotency-key-conflict":
            return IdempotencyConflictError(status, code, message, data, request_id)
        if code == "maintenance":
            ras: float | None = None
            if isinstance(data, dict) and data.get("retryAfterSeconds") is not None:
                try:
                    ras = float(data.get("retryAfterSeconds"))
                except (TypeError, ValueError):
                    ras = None
            return MaintenanceError(status, code, message, data, request_id,
                                    retry_after_seconds=ras)
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

    def stocks_all(self, market: str, *, status: str | None = "ACTIVE",
                   security_type: str | None = None,
                   common_share: bool | None = None) -> Any:
        """마켓(거래소)별 종목 리스트(유니버스 구성용). 페이지네이션 없이 한 번에 반환.

        - market(필수): KOSPI|KOSDAQ|NYSE|NASDAQ|AMEX|KR_ETC|US_ETC
        - status: SCHEDULED|ACTIVE(기본)|DELISTED
        - securityType: STOCK|ETF|REIT|... (스펙 enum). None이면 전체
        - commonShare: 보통주만(True)/그 외
        마켓당 수천 건(저변동)이라 하루 1회 조회 후 캐싱 권장. (그룹 STOCK_ALL)
        """
        market = market.upper()
        valid = {"KOSPI", "KOSDAQ", "NYSE", "NASDAQ", "AMEX", "KR_ETC", "US_ETC"}
        if market not in valid:
            raise ValueError(f"market은 {sorted(valid)} 중 하나여야 합니다.")
        params: dict[str, Any] = {"market": market, "status": status,
                                  "securityType": security_type}
        if common_share is not None:
            params["commonShare"] = "true" if common_share else "false"
        return self._request("GET", "/api/v1/stocks/all", group="STOCK_ALL", params=params)

    def get_warnings(self, symbol: str) -> Any:
        """매수 유의사항/VI. symbol은 path 파라미터."""
        return self._request("GET", f"/api/v1/stocks/{symbol}/warnings", group="STOCK")

    # ============================================================== Rankings
    def rankings(self, ranking_type: str, market_country: str, duration: str, *,
                 exclude_investment_caution: bool | None = None,
                 count: int | None = None) -> Any:
        """주식 랭킹. 상위 100위까지.

        - type(필수): MARKET_TRADING_AMOUNT|MARKET_TRADING_VOLUME|TOP_GAINERS|TOP_LOSERS|
          TOSS_SECURITIES_TRADING_AMOUNT|TOSS_SECURITIES_TRADING_VOLUME
        - marketCountry(필수): KR|US
        - duration(필수): realtime|1d|1w|1mo|3mo|6mo|1y (TOP_GAINERS/LOSERS는 realtime 불가)
        - count: 1~100(기본 100). (그룹 RANKING)
        """
        params: dict[str, Any] = {
            "type": ranking_type,
            "marketCountry": market_country.upper(),
            "duration": duration,
        }
        if exclude_investment_caution is not None:
            params["excludeInvestmentCaution"] = "true" if exclude_investment_caution else "false"
        if count is not None:
            params["count"] = max(1, min(100, int(count)))
        return self._request("GET", "/api/v1/rankings", group="RANKING", params=params)

    # ========================================================== Market Info
    def get_market_calendar(self, market: str = "US", date: str | None = None) -> Any:
        """장 운영 정보(전일/당일/익일). date는 YYYY-MM-DD(옵션).

        응답 today: {date, dayMarket, preMarket, regularMarket, afterMarket}
        각 세션 {startTime, endTime}은 ISO8601(KST offset). 휴장이면 세션 null.
        """
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
        """시장별 매매 수수료율. 응답: [{marketCountry(KR|US),
        commissionRate(소수 비율, 예 "0.0025"=0.25%), startDate|null, endDate|null}]."""
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
        - order_amount(금액주문)는 US MARKET 전용이며 정규장(종료 1시간 전까지)에만 가능
        - LIMIT은 price 필수, MARKET은 price 무시
        - quantity는 기본 양의 정수만(소수점은 US MARKET SELL에만 허용 — 서버 검증)
        - client_order_id: 멱등키(≤36자, [A-Za-z0-9_-]). 미지정 시 자동 생성.
          동일 cid 재요청 시 서버가 원주문을 그대로 반환(10분 유효). 다른 본문으로 재요청하면
          422 idempotency-key-conflict → 기존 주문을 조회해 재사용한다.
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

        cid = client_order_id or f"tt-{uuid.uuid4().hex}"
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "clientOrderId": cid,
        }
        if quantity is not None:
            body["quantity"] = str(quantity)
        if order_amount is not None:
            body["orderAmount"] = str(order_amount)
        if price is not None and order_type == "LIMIT":
            body["price"] = str(price)
        if time_in_force is not None:
            tif = time_in_force.upper()
            if tif not in ("DAY", "CLS", "OPG"):
                raise ValueError("time_in_force는 DAY, CLS 또는 OPG.")
            body["timeInForce"] = tif
        if confirm_high_value:
            body["confirmHighValueOrder"] = True

        try:
            return self._request("POST", "/api/v1/orders", group="ORDER",
                                 body=body, with_account=True)
        except IdempotencyConflictError:
            # 동일 cid로 다른 본문을 재요청 → 이미 접수된 주문이 존재. 기존 주문을 조회해 재사용.
            logger.warning("idempotency-key-conflict(cid=%s) → 기존 주문 조회로 재사용", cid)
            found = self.find_order_by_client_id(cid, symbol=symbol, side=side)
            if found:
                return found
            raise

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
        """주문 상세(모든 상태 조회 가능). execution(체결수량/평단/체결금액/수수료/세금) 포함."""
        return self._request("GET", f"/api/v1/orders/{order_id}",
                             group="ORDER_HISTORY", with_account=True)

    def list_orders(self, status: str, *, symbol: str | None = None,
                    from_: str | None = None, to: str | None = None,
                    cursor: str | None = None, limit: int | None = None) -> Any:
        """주문 목록. status 필수: OPEN(진행중) | CLOSED(종료).

        응답: {orders: [...], nextCursor|null, hasNext}. CLOSED만 페이지네이션(cursor/limit).
        limit은 1~100(초과 시 100으로 클램프).
        ⚠️ Order 스키마는 clientOrderId를 응답하지 않는다(생성 응답만 echo).
        """
        status = status.upper()
        if status not in ("OPEN", "CLOSED"):
            raise ValueError("status는 OPEN 또는 CLOSED.")
        if limit is not None:
            limit = max(1, min(100, int(limit)))
        return self._request("GET", "/api/v1/orders", group="ORDER_HISTORY",
                             params={"status": status, "symbol": symbol,
                                     "from": from_, "to": to,
                                     "cursor": cursor, "limit": limit},
                             with_account=True)

    def find_order_by_client_id(self, client_order_id: str, *,
                                symbol: str | None = None,
                                side: str | None = None,
                                statuses: tuple[str, ...] = ("OPEN", "CLOSED")) -> Any:
        """clientOrderId로 접수된 기존 주문을 찾는다(best-effort).

        ⚠️ v1.2.17 Order/PaginatedOrderResponse 스키마는 clientOrderId를 응답하지 않으므로
        정확한 cid 매칭이 불가하다. 대신 symbol(+side)로 OPEN→CLOSED를 훑어 가장 최근(orderedAt
        기준) 주문을 반환한다. **진짜 멱등 보장은 서버측 clientOrderId dedup**(동일 cid 재요청=원주문
        반환, 10분 유효)이며, 이 조회는 idempotency-key-conflict(다른 본문 재요청)나 사전 중복확인의
        보조 수단이다.
        """
        best: Any = None
        for status in statuses:
            try:
                resp = self.list_orders(status, symbol=symbol, limit=100)
            except TossError:
                continue
            orders = resp.get("orders") if isinstance(resp, dict) else None
            for o in (orders or []):
                if side and str(o.get("side", "")).upper() != side.upper():
                    continue
                if best is None or str(o.get("orderedAt", "")) > str(best.get("orderedAt", "")):
                    best = o
            if best is not None:
                break
        return best

    # ==================================================== Conditional Orders
    def create_conditional_order(self, symbol: str, *, cond_type: str,
                                 quantity: str | float, order_type: str,
                                 expire_date: str, first: dict,
                                 second: dict | None = None,
                                 client_order_id: str | None = None,
                                 confirm_high_value: bool = False) -> Any:
        """조건주문 생성. 응답: {conditionalOrderId, clientOrderId|null}.

        - cond_type → 본문 `type`: SINGLE|OCO|OTO
        - quantity: 그룹 공통 수량(문자열 직렬화)
        - order_type → `orderType`: LIMIT(각 leg orderPrice 필수)|MARKET. OCO/OTO는 LIMIT만.
        - expire_date → `expireDate`(YYYY-MM-DD, 필수)
        - first/second: ConditionRequest dict {orderSide, triggerPrice, orderPrice?}
        - client_order_id → `clientOrderId`(멱등키, 선택)
        (그룹 CONDITIONAL_ORDER)
        """
        body: dict[str, Any] = {
            "symbol": symbol,
            "type": cond_type.upper(),
            "quantity": str(quantity),
            "orderType": order_type.upper(),
            "expireDate": expire_date,
            "first": _leg(first),
        }
        if second is not None:
            body["second"] = _leg(second)
        if client_order_id:
            body["clientOrderId"] = client_order_id
        if confirm_high_value:
            body["confirmHighValueOrder"] = True
        return self._request("POST", "/api/v1/conditional-orders",
                             group="CONDITIONAL_ORDER", body=body, with_account=True)

    def list_conditional_orders(self, status: str, *, symbol: str | None = None,
                                cursor: str | None = None,
                                limit: int | None = None) -> Any:
        """조건주문 목록. status 필수: OPEN|CLOSED. 커서 기반 페이지네이션(limit 1~100).

        응답: {conditionalOrders 또는 items..., nextCursor|null, hasNext}. type 필드로 SINGLE/OCO/OTO 구분.
        (그룹 CONDITIONAL_ORDER_HISTORY)
        """
        status = status.upper()
        if status not in ("OPEN", "CLOSED"):
            raise ValueError("status는 OPEN 또는 CLOSED.")
        if limit is not None:
            limit = max(1, min(100, int(limit)))
        return self._request("GET", "/api/v1/conditional-orders",
                             group="CONDITIONAL_ORDER_HISTORY",
                             params={"status": status, "symbol": symbol,
                                     "cursor": cursor, "limit": limit},
                             with_account=True)

    def get_conditional_order(self, conditional_order_id: str) -> Any:
        """조건주문 단건 상세(진행중+종료 모두). (그룹 CONDITIONAL_ORDER_HISTORY)"""
        return self._request("GET", f"/api/v1/conditional-orders/{conditional_order_id}",
                             group="CONDITIONAL_ORDER_HISTORY", with_account=True)

    def cancel_conditional_order(self, conditional_order_id: str) -> Any:
        """조건주문 취소(HTTP DELETE). (그룹 CONDITIONAL_ORDER)"""
        return self._request("DELETE", f"/api/v1/conditional-orders/{conditional_order_id}",
                             group="CONDITIONAL_ORDER", with_account=True)

    def modify_conditional_order(self, conditional_order_id: str, *, cond_type: str,
                                 quantity: str | float, order_type: str,
                                 expire_date: str, first: dict,
                                 second: dict | None = None,
                                 confirm_high_value: bool = False) -> Any:
        """조건주문 수정. 전체 재설정(유지할 조건도 전달). expireDate 필수.

        ⚠️ 수정 = 기존 취소 + 신규 생성 → **새 conditionalOrderId 발급**, 기존 ID 무효화.
        응답의 conditionalOrderId를 이후 조회·수정·취소에 사용하라. (그룹 CONDITIONAL_ORDER)
        """
        body: dict[str, Any] = {
            "type": cond_type.upper(),
            "quantity": str(quantity),
            "orderType": order_type.upper(),
            "expireDate": expire_date,
            "first": _leg(first),
        }
        if second is not None:
            body["second"] = _leg(second)
        if confirm_high_value:
            body["confirmHighValueOrder"] = True
        return self._request("POST",
                             f"/api/v1/conditional-orders/{conditional_order_id}/modify",
                             group="CONDITIONAL_ORDER", body=body, with_account=True)
