"""토스 게이트웨이 gzip 응답 바디 처리 테스트(네트워크 없음).

게이트웨이는 클라이언트가 Accept-Encoding을 보내지 않아도 일부 응답(특히 에러
바디, 예: 403 OAuth access_denied)을 gzip으로 압축해 준다. `resp.read().decode()`는
바이너리 쓰레기를 만들어 OAuth 에러 코드/설명을 파싱하지 못했다. `_body_text`가
gzip 매직바이트/Content-Encoding을 감지해 해제하도록 한 뒤의 동작을 검증한다.

- gzip 403 OAuth 에러 → AuthError.code == 'access_denied' + 설명/힌트 파싱
- gzip 200 성공 JSON 정상 파싱(토큰 발급 / 일반 요청)
- 비압축 바디는 종전과 동일(성공/에러 모두)
"""
from __future__ import annotations

import gzip
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from toss_trader.client import TossClient  # noqa: E402
from toss_trader.config import Settings  # noqa: E402
from toss_trader.errors import AuthError  # noqa: E402


def _settings() -> Settings:
    return Settings(client_id="key", client_secret="sec", account_seq="1",
                    base_url="https://openapi.test", trading_mode="live",
                    gemini_api_key="")


def _gz(obj) -> bytes:
    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)
    elif isinstance(obj, str):
        data = obj.encode()
    else:
        data = json.dumps(obj).encode()
    return gzip.compress(data)


class _RawResp:
    """urlopen 성공 응답 최소 인터페이스: 바이트 바디를 그대로 돌려준다."""

    def __init__(self, body: bytes, headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(url, code, body: bytes, headers=None):
    return urllib.error.HTTPError(url, code, "err", headers or {}, io.BytesIO(body))


def _client(urlopen):
    return TossClient(_settings(), urlopen=urlopen, sleep=lambda *_: None,
                      token_cache_path=None)


# --------------------------------------------------------- gzip OAuth 에러 파싱
def test_gzip_oauth_error_parsed_by_magic_bytes():
    # 실제 관찰: Content-Encoding 헤더 없이 gzip 바디만 온다 → 매직바이트로 식별해야 함.
    err = _gz({"error": "access_denied",
               "error_description": "IP address not allowed"})

    def urlopen(req):
        raise _http_error(req.full_url, 403, err)

    with pytest.raises(AuthError) as ei:
        _client(urlopen)._issue_token()
    e = ei.value
    assert e.http_status == 403
    assert e.code == "access_denied"
    assert "IP address not allowed" in e.message      # error_description 파싱됨
    assert "IP address not allowed" in str(e)          # 로그/예외 문자열에 노출
    assert "허용 IP" in e.hint                          # access_denied 한국어 힌트


def test_gzip_oauth_error_parsed_by_content_encoding_header():
    err = _gz({"error": "access_denied",
               "error_description": "IP address not allowed"})

    def urlopen(req):
        raise _http_error(req.full_url, 403, err,
                          headers={"Content-Encoding": "gzip"})

    with pytest.raises(AuthError) as ei:
        _client(urlopen)._issue_token()
    assert ei.value.code == "access_denied"
    assert "IP address not allowed" in ei.value.message


# ----------------------------------------------------------- gzip 200 성공 파싱
def test_gzip_token_success_parses():
    ok = _gz({"access_token": "tok", "token_type": "Bearer", "expires_in": 3600})

    def urlopen(req):
        return _RawResp(ok)

    assert _client(urlopen)._ensure_token() == "tok"


def test_gzip_request_success_parses():
    tok = _gz({"access_token": "tok", "expires_in": 3600})
    prices = _gz({"result": [{"symbol": "AAPL", "lastPrice": "100"}]})

    def urlopen(req):
        if req.full_url.endswith("/oauth2/token"):
            return _RawResp(tok)
        return _RawResp(prices, headers={"Content-Encoding": "gzip"})

    out = _client(urlopen).get_prices(["AAPL"])
    assert out == [{"symbol": "AAPL", "lastPrice": "100"}]


# -------------------------------------------------------- 비압축 바디는 종전과 동일
def test_uncompressed_success_unchanged():
    def urlopen(req):
        if req.full_url.endswith("/oauth2/token"):
            return _RawResp(json.dumps(
                {"access_token": "tok", "expires_in": 3600}).encode())
        return _RawResp(json.dumps({"result": {"ok": True}}).encode())

    assert _client(urlopen).get_accounts() == {"ok": True}


def test_uncompressed_oauth_error_unchanged():
    body = json.dumps({"error": "invalid_client",
                       "error_description": "bad secret"}).encode()

    def urlopen(req):
        raise _http_error(req.full_url, 401, body)

    with pytest.raises(AuthError) as ei:
        _client(urlopen)._issue_token()
    assert ei.value.code == "invalid_client"
    assert "bad secret" in ei.value.message


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
