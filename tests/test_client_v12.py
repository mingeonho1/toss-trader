"""토스 OpenAPI v1.2.17 정합화 결정론적 테스트 (네트워크 없음).

fake urlopen(전송 훅)과 fake 클라이언트로 다음을 검증한다:
- 409 request-in-progress 백오프 재시도
- 422 idempotency-key-conflict → 기존 주문 조회(fetch) 경로
- 500 maintenance 처리(retryAfterSeconds 임계치 이하 재시도 / 초과 즉시 실패)
- 토큰 파일 캐시 공유(프로세스 간) + chmod 600
- 소수점 매도 수량 직렬화(내림 6자리, float 아티팩트 없음, 정수는 정수)
- 수수료 행 유효기간 선택(ratio→bps, 만료시 보수적 폴백)
- 정규장 접수시간 창 검사(서머타임/표준시)
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader.client import TossClient  # noqa: E402
from toss_trader.config import Settings  # noqa: E402
from toss_trader.costs import CostModel  # noqa: E402
from toss_trader.errors import IdempotencyConflictError, MaintenanceError  # noqa: E402


# --------------------------------------------------------------------- helpers
def _settings() -> Settings:
    return Settings(client_id="key", client_secret="sec", account_seq="1",
                    base_url="https://openapi.test", trading_mode="live",
                    gemini_api_key="")


class _Resp:
    """urlopen 성공 응답의 최소 인터페이스(컨텍스트 매니저 + read + headers)."""

    def __init__(self, payload, headers=None):
        body = payload if isinstance(payload, str) else json.dumps(payload)
        self._body = body.encode()
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(url, code, payload, headers=None):
    body = json.dumps(payload).encode()
    return urllib.error.HTTPError(url, code, "err", headers or {}, io.BytesIO(body))


def _token_resp():
    return _Resp({"access_token": "tok", "token_type": "Bearer", "expires_in": 3600})


def _make_client(urlopen, **kw):
    kw.setdefault("token_cache_path", None)   # 파일 캐시 비활성(테스트 격리)
    kw.setdefault("sleep", lambda *_: None)   # 백오프 대기 제거(빠른 테스트)
    return TossClient(_settings(), urlopen=urlopen, **kw)


# ------------------------------------------------------------------- 409 retry
def test_409_request_in_progress_retries():
    calls = {"order": 0}

    def urlopen(req):
        url = req.full_url
        if url.endswith("/oauth2/token"):
            return _token_resp()
        if url.endswith("/api/v1/orders") and req.get_method() == "POST":
            calls["order"] += 1
            if calls["order"] == 1:
                raise _http_error(url, 409, {"error": {
                    "code": "request-in-progress", "message": "처리 중"}})
            return _Resp({"result": {"orderId": "o1", "clientOrderId": "cid"}})
        return _Resp({"result": {}})

    c = _make_client(urlopen, max_retries=3)
    resp = c.create_order("AAPL", "BUY", order_type="MARKET",
                          order_amount="10.00", client_order_id="cid")
    assert resp["orderId"] == "o1"
    assert calls["order"] == 2, "409 request-in-progress는 1회 백오프 재시도돼야 한다"


# ------------------------------------------------------- idempotency conflict
def test_idempotency_conflict_fetches_existing():
    # 최근(≤10분) + 금액일치인 기존 주문만 재사용해야 하므로 orderedAt을 현재 근처로 만든다.
    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    def urlopen(req):
        url = req.full_url
        method = req.get_method()
        if url.endswith("/oauth2/token"):
            return _token_resp()
        if url.endswith("/api/v1/orders") and method == "POST":
            raise _http_error(url, 422, {"error": {
                "code": "idempotency-key-conflict",
                "message": "동일 clientOrderId 다른 본문"}})
        if "/api/v1/orders?" in url and method == "GET":
            # OPEN 목록에 기존 주문 존재(최근·금액 $10 일치)
            return _Resp({"result": {"orders": [
                {"orderId": "prev-1", "symbol": "AAPL", "side": "BUY",
                 "orderAmount": "10.00", "orderedAt": recent}],
                "nextCursor": None, "hasNext": False}})
        return _Resp({"result": {}})

    c = _make_client(urlopen)
    resp = c.create_order("AAPL", "BUY", order_type="MARKET",
                          order_amount="10.00", client_order_id="dca-2026-09-23-AAPL")
    assert resp["orderId"] == "prev-1", "idempotency 충돌 시 최근·금액일치 기존 주문을 재사용해야 한다"


def test_idempotency_conflict_rejects_stale_or_mismatched_order():
    """오매칭 방지: 기존 주문이 오래됐거나(>10분) 금액이 다르면 재사용하지 않고 재-raise.

    현재 버그: symbol+side만으로 가장 최근 주문을 반환 → 무관한 과거/타금액 주문을 done 처리.
    """
    scenario = {"orderedAt": None, "orderAmount": "10.00"}

    def urlopen(req):
        url = req.full_url
        method = req.get_method()
        if url.endswith("/oauth2/token"):
            return _token_resp()
        if url.endswith("/api/v1/orders") and method == "POST":
            raise _http_error(url, 422, {"error": {
                "code": "idempotency-key-conflict", "message": "conflict"}})
        if "/api/v1/orders?" in url and method == "GET":
            return _Resp({"result": {"orders": [
                {"orderId": "unrelated", "symbol": "AAPL", "side": "BUY",
                 "orderAmount": scenario["orderAmount"],
                 "orderedAt": scenario["orderedAt"]}],
                "nextCursor": None, "hasNext": False}})
        return _Resp({"result": {}})

    # (a) 2시간 전 주문(금액은 일치) → 시간창 밖 → 재-raise
    scenario["orderedAt"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    scenario["orderAmount"] = "10.00"
    c = _make_client(urlopen)
    with pytest.raises(IdempotencyConflictError):
        c.create_order("AAPL", "BUY", order_type="MARKET",
                       order_amount="10.00", client_order_id="cid")

    # (b) 최근 주문이지만 금액 불일치($50 vs $10) → 재-raise
    scenario["orderedAt"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    scenario["orderAmount"] = "50.00"
    c = _make_client(urlopen)
    with pytest.raises(IdempotencyConflictError):
        c.create_order("AAPL", "BUY", order_type="MARKET",
                       order_amount="10.00", client_order_id="cid")


def test_find_order_by_client_id_guards_direct():
    """find_order_by_client_id 단위 검증(now 주입, 결정론적)."""
    now = datetime(2026, 9, 23, 14, 41, tzinfo=timezone.utc)
    fresh = (now - timedelta(minutes=2)).isoformat()
    stale = (now - timedelta(minutes=30)).isoformat()

    def make(orders):
        def urlopen(req):
            url = req.full_url
            if url.endswith("/oauth2/token"):
                return _token_resp()
            if "/api/v1/orders?" in url:
                return _Resp({"result": {"orders": orders,
                                         "nextCursor": None, "hasNext": False}})
            return _Resp({"result": {}})
        return _make_client(urlopen)

    # 최근 + 금액일치 → 채택
    c = make([{"orderId": "ok", "symbol": "QQQ", "side": "BUY",
               "orderAmount": "10.00", "orderedAt": fresh}])
    got = c.find_order_by_client_id("cid", symbol="QQQ", side="BUY",
                                    order_amount="10.00", within_minutes=10, now=now)
    assert got and got["orderId"] == "ok"

    # 30분 전(시간창 밖) → None
    c = make([{"orderId": "old", "symbol": "QQQ", "side": "BUY",
               "orderAmount": "10.00", "orderedAt": stale}])
    assert c.find_order_by_client_id("cid", symbol="QQQ", side="BUY",
                                     order_amount="10.00", within_minutes=10, now=now) is None

    # 최근이지만 orderAmount 필드 없음 → 확인 불가 → None(보수적)
    c = make([{"orderId": "noamt", "symbol": "QQQ", "side": "BUY",
               "orderedAt": fresh}])
    assert c.find_order_by_client_id("cid", symbol="QQQ", side="BUY",
                                     order_amount="10.00", within_minutes=10, now=now) is None

    # 수량주문(SELL) 근사일치 → 채택
    c = make([{"orderId": "sell", "symbol": "QQQ", "side": "SELL",
               "quantity": "5", "orderedAt": fresh}])
    got = c.find_order_by_client_id("cid", symbol="QQQ", side="SELL",
                                    quantity="5", within_minutes=10, now=now)
    assert got and got["orderId"] == "sell"


def test_idempotency_conflict_reraises_when_not_found():
    def urlopen(req):
        url = req.full_url
        if url.endswith("/oauth2/token"):
            return _token_resp()
        if url.endswith("/api/v1/orders") and req.get_method() == "POST":
            raise _http_error(url, 422, {"error": {
                "code": "idempotency-key-conflict", "message": "conflict"}})
        if "/api/v1/orders?" in url:
            return _Resp({"result": {"orders": [], "nextCursor": None, "hasNext": False}})
        return _Resp({"result": {}})

    c = _make_client(urlopen)
    with pytest.raises(IdempotencyConflictError):
        c.create_order("AAPL", "BUY", order_type="MARKET",
                       order_amount="10.00", client_order_id="cid")


# --------------------------------------------------------------- maintenance
def test_maintenance_retries_under_threshold():
    calls = {"order": 0}
    slept = []

    def urlopen(req):
        url = req.full_url
        if url.endswith("/oauth2/token"):
            return _token_resp()
        if url.endswith("/api/v1/orders") and req.get_method() == "POST":
            calls["order"] += 1
            if calls["order"] == 1:
                raise _http_error(url, 500, {"error": {
                    "code": "maintenance", "message": "점검",
                    "data": {"retryAfterSeconds": 1}}})
            return _Resp({"result": {"orderId": "ok"}})
        return _Resp({"result": {}})

    c = _make_client(urlopen, max_retries=3, maintenance_max_wait=30.0,
                     sleep=lambda w: slept.append(w))
    resp = c.create_order("AAPL", "BUY", order_type="MARKET", order_amount="10.00")
    assert resp["orderId"] == "ok"
    assert calls["order"] == 2
    assert slept and slept[-1] == 1, "retryAfterSeconds만큼 대기 후 재시도해야 한다"


def test_maintenance_fails_fast_over_threshold():
    calls = {"order": 0}

    def urlopen(req):
        url = req.full_url
        if url.endswith("/oauth2/token"):
            return _token_resp()
        if url.endswith("/api/v1/orders") and req.get_method() == "POST":
            calls["order"] += 1
            raise _http_error(url, 500, {"error": {
                "code": "maintenance", "message": "점검",
                "data": {"retryAfterSeconds": 600}}})
        return _Resp({"result": {}})

    c = _make_client(urlopen, max_retries=3, maintenance_max_wait=30.0)
    with pytest.raises(MaintenanceError) as ei:
        c.create_order("AAPL", "BUY", order_type="MARKET", order_amount="10.00")
    assert ei.value.retry_after_seconds == 600
    assert calls["order"] == 1, "retryAfter가 임계치 초과면 즉시 실패(재시도 금지)"


# ------------------------------------------------------ business 4xx no-retry
def test_business_4xx_not_retried():
    calls = {"order": 0}

    def urlopen(req):
        url = req.full_url
        if url.endswith("/oauth2/token"):
            return _token_resp()
        if url.endswith("/api/v1/orders") and req.get_method() == "POST":
            calls["order"] += 1
            raise _http_error(url, 422, {"error": {
                "code": "insufficient-buying-power", "message": "부족"}})
        return _Resp({"result": {}})

    c = _make_client(urlopen, max_retries=3)
    with pytest.raises(Exception) as ei:
        c.create_order("AAPL", "BUY", order_type="MARKET", order_amount="10.00")
    assert ei.value.code == "insufficient-buying-power"
    assert calls["order"] == 1, "비즈니스 4xx는 재시도하지 않아야 한다"


# ------------------------------------------------------ token cache sharing
def test_token_cache_shared_across_clients(tmp_path):
    cache = tmp_path / ".token_cache.json"
    issues = {"n": 0}

    def urlopen(req):
        if req.full_url.endswith("/oauth2/token"):
            issues["n"] += 1
            return _Resp({"access_token": "shared-tok", "token_type": "Bearer",
                          "expires_in": 3600})
        return _Resp({"result": {}})

    a = TossClient(_settings(), urlopen=urlopen, sleep=lambda *_: None,
                   token_cache_path=str(cache))
    b = TossClient(_settings(), urlopen=urlopen, sleep=lambda *_: None,
                   token_cache_path=str(cache))
    ta = a._ensure_token()
    tb = b._ensure_token()  # 캐시를 재사용해야 함(재발급 X)
    assert ta == tb == "shared-tok"
    assert issues["n"] == 1, "두 번째 클라이언트는 파일 캐시를 재사용해 재발급하지 않아야 한다"
    mode = stat.S_IMODE(os.stat(cache).st_mode)
    assert mode == 0o600, f"토큰 캐시는 0600이어야 함(실제 {oct(mode)})"


def test_token_reissued_after_401_when_cache_stale(tmp_path):
    cache = tmp_path / ".token_cache.json"
    issued = []
    state = {"order": 0}

    def urlopen(req):
        url = req.full_url
        if url.endswith("/oauth2/token"):
            issued.append(1)
            return _Resp({"access_token": f"tok-{len(issued)}", "token_type": "Bearer",
                          "expires_in": 3600})
        if url.endswith("/api/v1/orders") and req.get_method() == "POST":
            state["order"] += 1
            if state["order"] == 1:
                raise _http_error(url, 401, {"error": {
                    "code": "expired-token", "message": "만료"}})
            return _Resp({"result": {"orderId": "ok"}})
        return _Resp({"result": {}})

    c = TossClient(_settings(), urlopen=urlopen, sleep=lambda *_: None,
                   token_cache_path=str(cache))
    resp = c.create_order("AAPL", "BUY", order_type="MARKET", order_amount="10.00")
    assert resp["orderId"] == "ok"
    assert len(issued) == 2, "401 후 실패 토큰과 동일한 캐시면 새 토큰을 발급해야 한다"


# --------------------------------------- fractional sell qty serialization
def test_fractional_sell_qty_serialization():
    from toss_trader.broker import _fmt_sell_qty

    assert _fmt_sell_qty(0.1 + 0.2) == "0.3"          # float 아티팩트 제거
    assert _fmt_sell_qty(1.234567891) == "1.234567"   # 6자리 '내림'
    assert _fmt_sell_qty(5.0) == "5"                   # 정수는 정수 문자열
    assert _fmt_sell_qty("0.4995") == "0.4995"
    assert _fmt_sell_qty(0.0000004) is None            # 6자리 내림 시 0 → None
    assert _fmt_sell_qty(0) is None
    assert _fmt_sell_qty(-1.0) is None


class _FakeSettings:
    is_live = True
    account_seq = "1"

    def require_account(self):
        pass


class _FakeBrokerClient:
    """LiveBroker 검증용 최소 fake. create_order의 quantity 문자열을 기록하고
    get_order는 filledAmount를 포함한 체결을 돌려준다."""

    def __init__(self):
        self.s = _FakeSettings()
        self.created = []
        self._orders = {}

    def get_buying_power(self, currency="USD"):
        return {"currency": "USD", "cashBuyingPower": "0"}

    def get_holdings(self, symbol=None):
        return {"items": []}

    def create_order(self, symbol, side, *, order_type="MARKET", quantity=None,
                     order_amount=None, price=None, time_in_force=None,
                     client_order_id=None, confirm_high_value=False):
        self.created.append({"symbol": symbol, "side": side.upper(),
                             "quantity": quantity, "order_amount": order_amount})
        oid = f"o{len(self.created)}"
        # 체결가 100 가정. filledAmount만 주고 averageFilledPrice는 null →
        # 브로커가 filledAmount/qty 로 체결가를 복원해야 한다.
        qty = float(quantity) if quantity is not None else 0.0
        self._orders[oid] = {"orderId": oid, "status": "FILLED", "execution": {
            "filledQuantity": str(quantity if quantity is not None else 0),
            "averageFilledPrice": None,
            "filledAmount": str(qty * 100.0), "commission": "1.0", "tax": None}}
        return {"orderId": oid, "clientOrderId": client_order_id}

    def get_order(self, order_id):
        return self._orders[order_id]


def test_live_broker_sell_serializes_and_uses_filled_amount():
    from toss_trader.broker import LiveBroker
    from toss_trader.models import Position

    fc = _FakeBrokerClient()
    lb = LiveBroker(fc, require_live=False, poll_interval=0.0)
    # 보유 포지션 세팅(평단 90) → filledAmount(1000)/qty(10)=100 체결가 복원, 실현손익=1000-90*10=100
    lb.positions["AAPL"] = Position("AAPL", quantity=10.0, avg_price=90.0)
    fill = lb.submit_market_order("AAPL", "SELL", quantity=10.123456789,
                                  ref_price=100.0, dt=date(2026, 9, 23))
    sent = fc.created[-1]
    assert sent["quantity"] == "10.123456", f"내림 6자리 직렬화 실패: {sent['quantity']}"
    assert fill is not None
    assert abs(fill.price - 100.0) < 1e-6, "filledAmount/qty로 체결가 복원"
    # 실현손익 = filledAmount − 평단×수량 = (100−90)×10.123456
    assert abs(fill.realized_pnl - 10.0 * 10.123456) < 1e-4, fill.realized_pnl


# ------------------------------------------------- commission row selection
def test_commission_row_selection_and_fallback():
    # 라이브 우선: 오늘을 포함하는 행이 있으면 그 값을 그대로 반영(합성행으로 '선택 로직'만 검증).
    # 오늘 포함 행이 없으면 표준요율(0.1%=10bps, 2025-12-01~)로 폴백.
    today = date(2026, 9, 23)
    comms = [
        {"marketCountry": "US", "commissionRate": "0.001",
         "startDate": None, "endDate": "2026-06-30"},   # 과거행(만료)
        {"marketCountry": "US", "commissionRate": "0.0025",
         "startDate": None, "endDate": None},            # 오늘 포함 → 이 행이 선택됨(라이브 우선)
    ]
    assert CostModel.from_commissions(comms, market="US", today=today).commission_bps == 25.0
    assert CostModel.from_commissions(comms, market="US",
                                      today=date(2026, 3, 1)).commission_bps == 10.0
    only_expired = [{"marketCountry": "US", "commissionRate": "0.001",
                     "startDate": None, "endDate": "2026-06-30"}]
    assert CostModel.from_commissions(only_expired, market="US",
                                      today=today).commission_bps == 10.0   # 폴백=표준 0.1%


# --------------------------------------------- market order window across DST
def test_order_window_across_dst():
    import run_dca as r

    KST = timezone(timedelta(hours=9))
    # 서머타임(EDT): 정규장 22:30~05:00 KST → 접수 마감 04:00 KST
    dst = {"startTime": "2026-03-25T22:30:00+09:00", "endTime": "2026-03-26T05:00:00+09:00"}
    # 표준시(EST, 11/1~): 정규장 23:30~06:00 KST → 접수 마감 05:00 KST
    est = {"startTime": "2026-11-05T23:30:00+09:00", "endTime": "2026-11-06T06:00:00+09:00"}

    def at(y, mo, d, h, mi):
        return datetime(y, mo, d, h, mi, tzinfo=KST)

    # 23:00 KST: 서머타임엔 창 안, 표준시엔 정규장 시작(23:30) 전이라 창 밖 → 이게 버그의 핵심
    assert r.order_window_status(dst, at(2026, 3, 25, 23, 0))[0] is True
    assert r.order_window_status(est, at(2026, 11, 5, 23, 0))[0] is False
    # 표준시에서 23:45는 창 안
    assert r.order_window_status(est, at(2026, 11, 5, 23, 45))[0] is True
    # 마감(정규장 종료 1시간 전) 이후는 창 밖
    assert r.order_window_status(dst, at(2026, 3, 26, 4, 30))[0] is False   # DST 마감 04:00
    assert r.order_window_status(est, at(2026, 11, 6, 5, 30))[0] is False   # EST 마감 05:00
    # 휴장/파싱 실패
    assert r.order_window_status(None)[0] is False
    assert r.client_order_id("2026-09-23", "QQQ") == "dca-2026-09-23-QQQ"
    assert len(r.client_order_id("2026-09-23", "VERYLONGTICKERNAME12345")) <= 36


# ------------------------------------------ half-day (early close) order window
def test_order_window_half_day_early_close():
    """반나절장(조기 종료 13:00 ET): 창은 캘린더 endTime(13:00 ET) − 1h 에 마감해야 한다.

    캘린더 regularMarket.endTime이 조기 종료(13:00 ET)를 반영하면 order_window가 그대로
    end−1h 를 쓰므로 반나절 처리가 자동으로 옳다(안전성 확인 — 별도 반나절 로직 불필요).
    """
    import run_dca as r

    KST = timezone(timedelta(hours=9))

    def at(y, mo, d, h, mi):
        return datetime(y, mo, d, h, mi, tzinfo=KST)

    # EST(11월): 09:30 ET=23:30 KST 시작, 조기종료 13:00 ET=익일 03:00 KST(=13:00 EST=18:00 UTC).
    half = {"startTime": "2026-11-27T23:30:00+09:00", "endTime": "2026-11-28T03:00:00+09:00"}
    start, close = r.order_window(half)
    assert start == at(2026, 11, 27, 23, 30)
    assert close == at(2026, 11, 28, 2, 0)          # 13:00 ET − 1h = 12:00 ET = 02:00 KST
    assert r.order_window_status(half, at(2026, 11, 28, 1, 30))[0] is True    # 01:30 창 안
    assert r.order_window_status(half, at(2026, 11, 28, 2, 30))[0] is False   # 02:30 마감 후
    # 정상장(16:00 ET=06:00 KST) 대비 조기 마감(02:00 < 05:00)이 앞당겨졌는지 확인.
    normal = {"startTime": "2026-11-27T23:30:00+09:00", "endTime": "2026-11-28T06:00:00+09:00"}
    _, nclose = r.order_window(normal)
    assert nclose == at(2026, 11, 28, 5, 0)
    assert close < nclose


# --------------------------------------- new thin wrappers: exact spec params
def _capture_client():
    captured = {}

    def urlopen(req):
        url = req.full_url
        if url.endswith("/oauth2/token"):
            return _token_resp()
        captured["method"] = req.get_method()
        captured["url"] = url
        captured["body"] = req.data.decode() if req.data else None
        return _Resp({"result": {}})

    return _make_client(urlopen), captured


def test_stocks_all_params():
    c, cap = _capture_client()
    c.stocks_all("NASDAQ", status="ACTIVE", security_type="ETF", common_share=True)
    assert "/api/v1/stocks/all?" in cap["url"]
    assert "market=NASDAQ" in cap["url"]
    assert "securityType=ETF" in cap["url"]
    assert "commonShare=true" in cap["url"]


def test_rankings_params():
    c, cap = _capture_client()
    c.rankings("TOP_GAINERS", "US", "1d", count=250)
    assert "/api/v1/rankings?" in cap["url"]
    assert "type=TOP_GAINERS" in cap["url"]
    assert "marketCountry=US" in cap["url"]
    assert "duration=1d" in cap["url"]
    assert "count=100" in cap["url"]  # 1~100 클램프


def test_conditional_order_body_keys():
    c, cap = _capture_client()
    c.create_conditional_order(
        "005930", cond_type="oco", quantity=100, order_type="limit",
        expire_date="2026-09-10",
        first={"orderSide": "sell", "triggerPrice": 305, "orderPrice": 305},
        second={"orderSide": "sell", "triggerPrice": 295, "orderPrice": 294.5},
        client_order_id="my-cid")
    body = json.loads(cap["body"])
    assert body["type"] == "OCO" and body["orderType"] == "LIMIT"
    assert body["quantity"] == "100" and body["expireDate"] == "2026-09-10"
    assert body["clientOrderId"] == "my-cid"
    assert body["first"] == {"orderSide": "SELL", "triggerPrice": "305", "orderPrice": "305"}
    assert body["second"]["triggerPrice"] == "295" and body["second"]["orderPrice"] == "294.5"


def test_cancel_conditional_order_uses_delete():
    c, cap = _capture_client()
    c.cancel_conditional_order("abc123")
    assert cap["method"] == "DELETE"
    assert cap["url"].endswith("/api/v1/conditional-orders/abc123")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
