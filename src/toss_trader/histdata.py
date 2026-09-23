"""키 불필요 장기 히스토리 시세 소스 — 백테스트용 총수익(배당재투자) 일봉 + 인트라데이.

토스 API는 자격증명이 필요하고 ~2500봉(~10년)만 준다. 여기서는 키 없이
2000~현재까지, 배당조정(총수익) 일봉을 받는다.

소스 우선순위 (fetch_symbol 은 순서대로 폴백):
  1) Yahoo Finance v8 chart API (기본): `adjclose`로 배당+분할 조정.
     OHLC를 `adjclose/close` 비율로 스케일 → 배당 재투자(총수익) 가격.
  2) Nasdaq API (폴백): historical(~10년 분할반영 OHLCV) + dividends 로 배당조정 adjclose를
     자체 계산해 총수익 시계열을 만든다. Yahoo가 막혔을 때의 실동작 대체 소스.
     ⚠️ Nasdaq /dividends 는 **나스닥 상장 종목만** 배당내역을 준다(QQQ·TQQQ·대부분 대형 단일주 OK).
        NYSE Arca 상장 ETF(SPY·SPDR 섹터·ProShares·iShares 등)는 배당이 비어(200 rows=None) 와서
        분할반영만 된 가격(=raw)이 되고 배당은 누락된다. 이 심볼들의 총수익이 필요하면 Yahoo가 필수.
  3) Stooq CSV (폴백): 원시 OHLCV(분할반영, 배당 미반영). adjclose 없음 → 조정계수 1.

장기 지수/금리/변동성(키 불필요, 종가만): FRED CSV — `load_fred(series)` 로 NASDAQ100(1986+),
NASDAQCOM(1971+), DTB3(rf), VIXCLS(1990+), DGS10 등. `index_total_return`으로 배당수익률을
더해 총수익 근사(예: FRED NASDAQ100 → 3x 합성 → 실물 TQQQ 검증).

캐시: `data/_hist_cache/{SYM}.json` (data/ 는 gitignore). 캐시에는 원시 OHLCV와
adjclose를 함께 저장하고, adjusted/raw 변환은 로드 시점에 적용한다(한 캐시로 둘 다 생성).

인트라데이: `load_intraday(symbol, interval)` — 1m(~최대 30일까지 청크), 5m/15m(60일),
60m(730일). America/New_York 타임스탬프 + 정규장 플래그. 재요청 시 병합 누적.

주의 (Yahoo adjclose 특성):
  - v8 `close`는 분할반영(split-adjusted), `adjclose`는 분할+배당반영. 따라서
    `adjclose/close`는 순수 배당재투자 계수(과거일수록 <1, 최신일 =1)이다.
  - 배당/분할 데이터 정정으로 과거 adjclose가 바뀔 수 있어 캐시를 갱신하면 값이 달라질 수 있다.
  - 극히 오래된 구간은 adjclose가 null일 수 있어 그 봉은 계수 1로 폴백한다.
"""
from __future__ import annotations

import bisect
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path

from .models import Candle

try:
    # 검증 유지 CA 자동탐색 로직을 재사용(중복 방지). client 리팩터에도 견디도록 폴백 제공.
    from .client import build_ssl_context
except Exception:  # noqa: BLE001  client API 변경/부재 시 자립 폴백
    import os
    import ssl

    _CA_CANDIDATES = (
        "/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt", "/opt/homebrew/etc/openssl@3/cert.pem",
        "/usr/local/etc/openssl@3/cert.pem",
    )

    def build_ssl_context() -> "ssl.SSLContext":
        """검증을 유지한 채 유효한 CA 번들을 가진 SSL 컨텍스트(자체 폴백)."""
        ctx = ssl.create_default_context()
        try:
            if ctx.cert_store_stats().get("x509_ca", 0) > 0:
                return ctx
        except Exception:  # noqa: BLE001
            pass
        cands = [os.environ.get("SSL_CERT_FILE", ""), *_CA_CANDIDATES]
        try:
            import certifi
            cands.append(certifi.where())
        except Exception:  # noqa: BLE001
            pass
        for path in cands:
            if path and os.path.exists(path):
                try:
                    c = ssl.create_default_context(cafile=path)
                    if c.cert_store_stats().get("x509_ca", 0) > 0:
                        return c
                except Exception:  # noqa: BLE001
                    continue
        return ctx

try:  # 표준 라이브러리(3.9+). 시스템 tz DB 없으면 폴백.
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    _ET = None

# --------------------------------------------------------------------------- 경로/상수
_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _ROOT / "data" / "_hist_cache"
INTRADAY_CACHE_DIR = CACHE_DIR / "intraday"
FRED_CACHE_DIR = CACHE_DIR / "fred"

_NASDAQ_HIST_YEARS = 20   # Nasdaq API는 ~10년만 주지만 넉넉히 요청(초과분은 무시됨).
_NASDAQ_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")

# Yahoo 인트라데이 인터벌별 1회 요청 최대 조회 범위(일). 1m은 짧아 청크 반복이 필요.
_INTRADAY_MAX_DAYS = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60,
                      "60m": 730, "90m": 60, "1h": 730}
# 1m 누적 목표 범위(청크로 긁어 최대 ~30일까지 시도).
_ONE_MIN_TARGET_DAYS = 30

_ET_OPEN = (9, 30)    # 정규장 시작 09:30 ET
_ET_CLOSE = (16, 0)   # 정규장 종료 16:00 ET


# --------------------------------------------------------------------------- HTTP
_opener_singleton = None


def _opener():
    """쿠키 세션을 유지하는 공유 opener(검증 유지 SSL). Yahoo가 세션 쿠키를 요구할 수 있어 홈에서 시드."""
    global _opener_singleton
    if _opener_singleton is None:
        ctx = build_ssl_context()
        op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=ctx),
        )
        op.addheaders = [("User-Agent", _UA), ("Accept", "*/*"),
                         ("Accept-Language", "en-US,en;q=0.9")]
        try:
            op.open("https://finance.yahoo.com/", timeout=15).read()
        except Exception:  # noqa: BLE001  쿠키 시드 실패는 치명적이지 않음
            pass
        _opener_singleton = op
    return _opener_singleton


class HistDataError(RuntimeError):
    """모든 소스에서 데이터를 얻지 못했을 때."""


def _http_get(url: str, *, timeout: float = 30.0, retries: int = 4,
              headers: dict | None = None) -> bytes:
    """GET with 백오프. 429/5xx는 Retry-After 존중 후 재시도. 마지막 예외를 올림.

    headers 를 주면 per-request 헤더로 Request를 만든다(예: Nasdaq는 Origin/Referer 필요).
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            target = urllib.request.Request(url, headers=headers) if headers else url
            with _opener().open(target, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                ra = e.headers.get("Retry-After")
                wait = float(ra) if (ra and ra.isdigit()) else 2.0 * (2 ** attempt)
                time.sleep(min(wait, 30.0))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2.0 * (2 ** attempt))
                continue
            raise
    assert last is not None
    raise last


# --------------------------------------------------------------------------- 시간 유틸
def _et_datetime(ts: int, gmtoffset: int | None = None) -> datetime:
    """epoch(초) → America/New_York tz-aware datetime. zoneinfo 없으면 gmtoffset 폴백."""
    if _ET is not None:
        return datetime.fromtimestamp(ts, tz=_ET)
    off = gmtoffset if gmtoffset is not None else -5 * 3600
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(seconds=off)))


def _trade_date(ts: int, gmtoffset: int | None = None) -> date:
    """일봉 거래일(ET 기준). 일봉 타임스탬프는 장중 시각이라 ET 날짜로 환산하면 정확."""
    return _et_datetime(ts, gmtoffset).date()


def _is_regular_session(dt: datetime) -> bool:
    """ET 정규장(평일 09:30~16:00) 여부."""
    if dt.weekday() >= 5:
        return False
    hm = (dt.hour, dt.minute)
    return _ET_OPEN <= hm < _ET_CLOSE


# --------------------------------------------------------------------------- Yahoo 파싱(순수)
def _parse_yahoo_chart(data: dict) -> list[dict]:
    """Yahoo v8 chart JSON → 원시 일봉 rows(list of dict). 네트워크 없음(테스트 가능).

    각 row: {"d": ISO date, "o","h","l","c","v": float, "a": adjclose|None}.
    close 가 null 인 봉(휴장/결측)은 건너뛴다. open/high/low null 은 close 로 채운다.
    """
    chart = data.get("chart") or {}
    if chart.get("error"):
        raise HistDataError(f"yahoo error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise HistDataError("yahoo: empty result")
    res = results[0]
    ts = res.get("timestamp") or []
    meta = res.get("meta") or {}
    gmtoff = meta.get("gmtoffset")
    ind = res.get("indicators") or {}
    quote = (ind.get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    adj_arr = (ind.get("adjclose") or [{}])[0].get("adjclose") if ind.get("adjclose") else None

    rows: list[dict] = []
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        o = opens[i] if i < len(opens) and opens[i] is not None else c
        h = highs[i] if i < len(highs) and highs[i] is not None else c
        low = lows[i] if i < len(lows) and lows[i] is not None else c
        v = vols[i] if i < len(vols) and vols[i] is not None else 0
        a = adj_arr[i] if (adj_arr and i < len(adj_arr) and adj_arr[i] is not None) else None
        rows.append({"d": _trade_date(int(t), gmtoff).isoformat(),
                     "o": float(o), "h": float(h), "l": float(low),
                     "c": float(c), "v": float(v),
                     "a": (float(a) if a is not None else None)})
    return rows


def _parse_yahoo_intraday(data: dict) -> list[dict]:
    """Yahoo v8 chart(인트라데이) JSON → rows. row에 epoch ts와 ET/정규장 플래그 포함.

    각 row: {"ts": epoch, "et": ISO ET datetime, "o","h","l","c","v", "regular": bool}.
    """
    chart = data.get("chart") or {}
    if chart.get("error"):
        raise HistDataError(f"yahoo error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise HistDataError("yahoo: empty result")
    res = results[0]
    ts = res.get("timestamp") or []
    meta = res.get("meta") or {}
    gmtoff = meta.get("gmtoffset")
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs = quote.get("open") or [], quote.get("high") or []
    lows, closes = quote.get("low") or [], quote.get("close") or []
    vols = quote.get("volume") or []

    rows: list[dict] = []
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        o = opens[i] if i < len(opens) and opens[i] is not None else c
        h = highs[i] if i < len(highs) and highs[i] is not None else c
        low = lows[i] if i < len(lows) and lows[i] is not None else c
        v = vols[i] if i < len(vols) and vols[i] is not None else 0
        et = _et_datetime(int(t), gmtoff)
        rows.append({"ts": int(t), "et": et.isoformat(),
                     "o": float(o), "h": float(h), "l": float(low),
                     "c": float(c), "v": float(v),
                     "regular": _is_regular_session(et)})
    return rows


def _parse_stooq_csv(text: str) -> list[dict]:
    """Stooq 일봉 CSV → rows(adjclose 없음 → a=None). 헤더: Date,Open,High,Low,Close,Volume."""
    rows: list[dict] = []
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return rows
    header = [h.strip().lower() for h in lines[0].split(",")]
    if "date" not in header or "close" not in header:
        # 도전(JS challenge)/에러 페이지 등 CSV가 아님
        raise HistDataError("stooq: unexpected response (not CSV)")
    idx = {name: header.index(name) for name in header}
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < len(header):
            continue
        try:
            d = date.fromisoformat(parts[idx["date"]].strip())
            o = float(parts[idx["open"]]); h = float(parts[idx["high"]])
            low = float(parts[idx["low"]]); c = float(parts[idx["close"]])
            v = float(parts[idx["volume"]]) if "volume" in idx and parts[idx["volume"]].strip() else 0.0
        except (ValueError, KeyError, IndexError):
            continue
        rows.append({"d": d.isoformat(), "o": o, "h": h, "l": low,
                     "c": c, "v": v, "a": None})
    return rows


# --------------------------------------------------------------------------- 조정(순수)
def _adj_factor(row: dict) -> float:
    """총수익 조정계수 = adjclose/close. adjclose 없거나 close<=0 이면 1.0."""
    a = row.get("a")
    c = row.get("c")
    if a is None or not c or c <= 0:
        return 1.0
    return a / c


def rows_to_candles(symbol: str, rows: list[dict], *, adjusted: bool = True) -> list[Candle]:
    """캐시 rows → Candle 리스트(오름차순). adjusted=True면 OHLC를 adjclose/close로 스케일.

    adjusted=False 는 소스 원시가(Yahoo=분할반영·배당미반영). 거래량은 조정하지 않는다(원시).
    """
    out: list[Candle] = []
    for r in rows:
        f = _adj_factor(r) if adjusted else 1.0
        out.append(Candle(symbol, date.fromisoformat(r["d"]),
                          r["o"] * f, r["h"] * f, r["l"] * f, r["c"] * f, r["v"]))
    out.sort(key=lambda c: c.dt)
    return out


# --------------------------------------------------------------------------- Yahoo/Stooq 페치(네트워크)
def _yahoo_chart_raw(symbol: str, *, period1: int = 0, period2: int | None = None,
                     interval: str = "1d", events: str = "div,split") -> dict:
    """Yahoo v8 chart 원본 JSON. query1/query2 순회. 실패 시 마지막 예외 올림."""
    if period2 is None:
        period2 = int(time.time())
    q = urllib.parse.quote(symbol, safe="")
    last: Exception | None = None
    for host in _YAHOO_HOSTS:
        url = (f"https://{host}/v8/finance/chart/{q}?period1={period1}&period2={period2}"
               f"&interval={interval}&includeAdjustedClose=true")
        if events:
            url += f"&events={events}"
        try:
            return json.loads(_http_get(url))
        except Exception as e:  # noqa: BLE001  다음 호스트 시도
            last = e
    assert last is not None
    raise last


def _fetch_yahoo_daily(symbol: str) -> list[dict]:
    return _parse_yahoo_chart(_yahoo_chart_raw(symbol, period1=0, interval="1d"))


def _fetch_stooq_daily(symbol: str) -> list[dict]:
    sym = symbol.lower().lstrip("^")
    url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(sym)}.us&i=d"
    return _parse_stooq_csv(_http_get(url).decode("utf-8", "replace"))


# --------------------------------------------------------------------------- 캐시/공개 API
def _cache_path(symbol: str) -> Path:
    safe = symbol.replace("^", "_").replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def fetch_symbol(symbol: str, *, force: bool = False, prefer: str = "yahoo") -> dict:
    """심볼의 원시 rows를 캐시에 확보하고 캐시 dict를 반환.

    캐시 있으면(force=False) 그대로 사용. 없거나 force면 Yahoo→Stooq 순으로 페치.
    반환: {"symbol","source","fetched","rows":[...]}.
    """
    path = _cache_path(symbol)
    if path.exists() and not force:
        return json.loads(path.read_text())

    errors: list[str] = []
    all_src = ["yahoo", "nasdaq", "stooq"]
    order = ([prefer] + [s for s in all_src if s != prefer]) if prefer in all_src else all_src
    rows: list[dict] = []
    source = ""
    for src in order:
        try:
            if src == "yahoo":
                rows = _fetch_yahoo_daily(symbol)
            elif src == "nasdaq":
                rows = _fetch_nasdaq_daily(symbol)
            else:
                rows = _fetch_stooq_daily(symbol)
            if rows:
                source = src
                break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src}: {type(e).__name__}: {e}")
    if not rows:
        raise HistDataError(f"{symbol}: 모든 소스 실패 → " + " | ".join(errors))

    payload = {"symbol": symbol, "source": source,
               "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "rows": rows}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return payload


def load_symbol(symbol: str, start: date | None = None, end: date | None = None,
                adjusted: bool = True, *, force: bool = False) -> list[Candle]:
    """단일 심볼 Candle 리스트(오름차순, 조정 옵션). 캐시 없으면 페치."""
    payload = fetch_symbol(symbol, force=force)
    candles = rows_to_candles(symbol, payload["rows"], adjusted=adjusted)
    if start is not None:
        candles = [c for c in candles if c.dt >= start]
    if end is not None:
        candles = [c for c in candles if c.dt <= end]
    return candles


def load_panel(symbols, start: date | None = None, end: date | None = None,
               adjusted: bool = True, *, force: bool = False) -> dict[str, list[Candle]]:
    """여러 심볼 → {symbol: [Candle...]} (각자 오름차순). 실패 심볼은 건너뛰지 않고 예외."""
    panel: dict[str, list[Candle]] = {}
    for sym in symbols:
        panel[sym] = load_symbol(sym, start=start, end=end, adjusted=adjusted, force=force)
    return panel


def align_panel(panel: dict[str, list[Candle]]) -> dict[str, list[Candle]]:
    """패널을 '공통 거래일(모든 심볼에 존재하는 날짜)'로 정렬·교집합.

    각 심볼 리스트를 공통 날짜집합으로 필터해 길이·날짜가 완전히 일치하게 만든다.
    """
    if not panel:
        return {}
    date_sets = [set(c.dt for c in candles) for candles in panel.values()]
    common = set.intersection(*date_sets) if date_sets else set()
    return {sym: [c for c in sorted(candles, key=lambda x: x.dt) if c.dt in common]
            for sym, candles in panel.items()}


# --------------------------------------------------------------------------- 합성 레버리지 ETF
def _rf_daily_map(base_dates: list[date], rf_candles: list[Candle] | None,
                  rf_kind: str, rf_annual: float, trading_days: int) -> dict[date, float]:
    """날짜→일간 무위험수익률. rf_candles 있으면 사용(price=일수익, yield=연율/거래일), 없으면 상수."""
    const = rf_annual / trading_days
    if not rf_candles:
        return {d: const for d in base_dates}
    rf = sorted(rf_candles, key=lambda c: c.dt)
    out: dict[date, float] = {}
    if rf_kind == "price":  # 예: BIL 총수익 가격 → 일간수익률이 곧 rf
        prev = None
        for c in rf:
            if prev is not None and prev.close > 0:
                out[c.dt] = max(0.0, c.close / prev.close - 1.0)
            prev = c
    else:  # "yield": 예 ^IRX = 13주 T-bill 할인율(%) → 연율/거래일
        for c in rf:
            out[c.dt] = max(0.0, (c.close / 100.0) / trading_days)
    # base 날짜에 대해 직전 유효값 forward-fill, 없으면 상수
    filled: dict[date, float] = {}
    keys = sorted(out)
    last = const
    ki = 0
    for d in sorted(base_dates):
        while ki < len(keys) and keys[ki] <= d:
            last = out[keys[ki]]
            ki += 1
        filled[d] = last if keys else const
    return filled


def synthetic_leveraged(base_candles: list[Candle], leverage: float,
                        annual_expense: float = 0.0095,
                        borrow_spread: float = 0.005, *,
                        symbol: str | None = None,
                        rf_annual: float = 0.02,
                        rf_candles: list[Candle] | None = None,
                        rf_kind: str = "price",
                        trading_days: int = 252) -> list[Candle]:
    """기초자산 일봉에서 일일 리밸런싱 Lx 합성 시계열을 만든다(예: QQQ→3x 'TQQQ-sim').

    일간 모델(종가 기준):
        r_lev = L·r_base − (L−1)·(rf_daily + borrow_spread_daily) − expense_daily
    - r_base: 기초자산 일간 종가수익률.
    - 금융비용은 차입분(L−1)에만 부과(무위험 + 차입 스프레드). 비용/스프레드는 연율/거래일로 환산.
    - rf_daily: rf_candles(BIL 총수익=price, ^IRX 수익률=yield)에서, 없으면 rf_annual 상수.
    산출: 종가 앵커 Candle(open=high=low=close=합성 NAV). 인트라 경로는 정의 불가라 종가만 모델.
    거래량은 기초자산 값을 참고로 실어 준다. 첫날 NAV=기초자산 첫 종가(비교 편의).
    """
    base = sorted(base_candles, key=lambda c: c.dt)
    if len(base) < 2:
        return []
    if symbol is None:
        symbol = f"{base[0].symbol}-{leverage:g}x-sim"
    dates = [c.dt for c in base]
    rf_map = _rf_daily_map(dates, rf_candles, rf_kind, rf_annual, trading_days)
    exp_d = annual_expense / trading_days
    spr_d = borrow_spread / trading_days

    out: list[Candle] = [Candle(symbol, base[0].dt, base[0].close, base[0].close,
                                base[0].close, base[0].close, base[0].volume)]
    nav = base[0].close
    for i in range(1, len(base)):
        prev_c, cur = base[i - 1], base[i]
        r = (cur.close / prev_c.close - 1.0) if prev_c.close > 0 else 0.0
        rf_d = rf_map.get(cur.dt, rf_annual / trading_days)
        financing = (leverage - 1.0) * (rf_d + spr_d)
        r_lev = leverage * r - financing - exp_d
        nav *= (1.0 + r_lev)
        out.append(Candle(symbol, cur.dt, nav, nav, nav, nav, cur.volume))
    return out


# --------------------------------------------------------------------------- 검증(합성 vs 실물)
def _cagr(equity: list[float], days: int) -> float:
    if len(equity) < 2 or equity[0] <= 0 or equity[-1] <= 0 or days <= 0:
        return 0.0
    years = max(days / 365.25, 1e-9)
    return (equity[-1] / equity[0]) ** (1.0 / years) - 1.0


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / (sxx ** 0.5 * syy ** 0.5)


def validate_synthetic(base_candles: list[Candle], real_candles: list[Candle],
                       leverage: float, *, trading_days: int = 252, **syn_kwargs) -> dict:
    """합성 Lx vs 실물 ETF 검증(겹치는 구간). 일간수익률 상관 + 연환산 추적오차/차이 리포트.

    반환: {overlap_start, overlap_end, n, corr, ann_tracking_diff, ann_tracking_error,
           cagr_syn, cagr_real}. n<2면 corr=0.
    """
    syn = synthetic_leveraged(base_candles, leverage, trading_days=trading_days, **syn_kwargs)
    syn_by = {c.dt: c.close for c in syn}
    real_by = {c.dt: c.close for c in sorted(real_candles, key=lambda c: c.dt)}
    common = sorted(set(syn_by) & set(real_by))
    if len(common) < 2:
        return {"overlap_start": None, "overlap_end": None, "n": len(common),
                "corr": 0.0, "ann_tracking_diff": 0.0, "ann_tracking_error": 0.0,
                "cagr_syn": 0.0, "cagr_real": 0.0}
    s = [syn_by[d] for d in common]
    rl = [real_by[d] for d in common]
    s_ret = [s[i] / s[i - 1] - 1.0 for i in range(1, len(s)) if s[i - 1] > 0]
    r_ret = [rl[i] / rl[i - 1] - 1.0 for i in range(1, len(rl)) if rl[i - 1] > 0]
    m = min(len(s_ret), len(r_ret))
    s_ret, r_ret = s_ret[:m], r_ret[:m]
    diff = [a - b for a, b in zip(s_ret, r_ret)]
    mean_diff = sum(diff) / len(diff) if diff else 0.0
    var = sum((d - mean_diff) ** 2 for d in diff) / (len(diff) - 1) if len(diff) > 1 else 0.0
    days = (common[-1] - common[0]).days
    return {
        "overlap_start": common[0].isoformat(),
        "overlap_end": common[-1].isoformat(),
        "n": len(common),
        "corr": _pearson(s_ret, r_ret),
        "ann_tracking_diff": mean_diff * trading_days,          # 합성−실물 연환산 초과수익
        "ann_tracking_error": (var ** 0.5) * (trading_days ** 0.5),  # 연환산 추적오차(std)
        "cagr_syn": _cagr(s, days),
        "cagr_real": _cagr(rl, days),
    }


# --------------------------------------------------------------------------- 인트라데이
@dataclass(frozen=True)
class IntradayBar:
    symbol: str
    dt: datetime      # America/New_York tz-aware
    open: float
    high: float
    low: float
    close: float
    volume: float
    regular: bool     # 정규장(09:30~16:00 ET) 여부


def _intraday_cache_path(symbol: str, interval: str) -> Path:
    safe = symbol.replace("^", "_").replace("/", "_")
    return INTRADAY_CACHE_DIR / f"{safe}_{interval}.json"


def _merge_intraday_rows(old: list[dict], new: list[dict]) -> list[dict]:
    """ts(epoch) 키로 병합·중복제거·정렬. 새 값이 기존을 덮어씀(정정 반영)."""
    by_ts: dict[int, dict] = {r["ts"]: r for r in old}
    for r in new:
        by_ts[r["ts"]] = r
    return [by_ts[k] for k in sorted(by_ts)]


def _fetch_intraday_chunk(symbol: str, interval: str, period1: int, period2: int) -> list[dict]:
    data = _yahoo_chart_raw(symbol, period1=period1, period2=period2,
                            interval=interval, events="")
    return _parse_yahoo_intraday(data)


def load_intraday(symbol: str, interval: str = "5m", *, force: bool = False,
                  now: int | None = None) -> list[IntradayBar]:
    """인트라데이 바(오름차순). Yahoo 인터벌 제약에 맞춰 조회하고 캐시에 병합 누적.

    - 1m: 1회 최대 7일 → 7일 청크를 뒤로 반복해 ~30일까지 시도.
    - 5m/15m: 60일, 60m/1h: 730일 범위로 1회 조회.
    캐시: data/_hist_cache/intraday/{SYM}_{interval}.json (재요청 시 ts 기준 병합).
    """
    if interval not in _INTRADAY_MAX_DAYS:
        raise ValueError(f"지원하지 않는 인터벌: {interval} (지원: {sorted(_INTRADAY_MAX_DAYS)})")
    now = now or int(time.time())
    path = _intraday_cache_path(symbol, interval)
    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text()).get("rows", [])
        except Exception:  # noqa: BLE001
            existing = []
    if existing and not force:
        return _rows_to_intraday(symbol, existing)

    fetched: list[dict] = []
    max_days = _INTRADAY_MAX_DAYS[interval]
    if interval == "1m":
        # 7일 청크를 뒤로 반복(Yahoo 1m 범위 한계 회피), ~30일 목표.
        span = 7 * 86400
        end = now
        earliest = now - _ONE_MIN_TARGET_DAYS * 86400
        while end > earliest:
            start = max(earliest, end - span)
            try:
                fetched.extend(_fetch_intraday_chunk(symbol, interval, start, end))
            except Exception:  # noqa: BLE001  일부 청크 실패는 무시하고 진행
                pass
            end = start
            time.sleep(0.3)
    else:
        start = now - max_days * 86400
        fetched = _fetch_intraday_chunk(symbol, interval, start, now)

    merged = _merge_intraday_rows(existing, fetched)
    INTRADAY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"symbol": symbol, "interval": interval,
                                "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "rows": merged}))
    return _rows_to_intraday(symbol, merged)


def _rows_to_intraday(symbol: str, rows: list[dict]) -> list[IntradayBar]:
    out = [IntradayBar(symbol, datetime.fromisoformat(r["et"]), r["o"], r["h"],
                       r["l"], r["c"], r["v"], bool(r["regular"]))
           for r in sorted(rows, key=lambda x: x["ts"])]
    return out


# --------------------------------------------------------------------------- FRED (지수/금리/변동성)
def _parse_fred_csv(text: str) -> list[dict]:
    """FRED fredgraph.csv → 종가전용 rows. 헤더 'DATE,<ID>'(또는 observation_date,...).

    결측치('.'/''/'NA')는 건너뛴다. 지수/금리/변동성 레벨을 close에 담고 OHLC 동일, a=None.
    """
    rows: list[dict] = []
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return rows
    for ln in lines[1:]:                      # 첫 줄은 헤더
        parts = ln.split(",")
        if len(parts) < 2:
            continue
        ds, vs = parts[0].strip(), parts[1].strip()
        if vs in (".", "", "NA", "NaN", "null"):
            continue
        try:
            d = date.fromisoformat(ds)
            v = float(vs)
        except ValueError:
            continue
        rows.append({"d": d.isoformat(), "o": v, "h": v, "l": v, "c": v, "v": 0.0, "a": None})
    return rows


def _fetch_fred(series: str) -> list[dict]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={urllib.parse.quote(series)}"
    return _parse_fred_csv(_http_get(url, timeout=60).decode("utf-8", "replace"))


def load_fred(series: str, start: date | None = None, end: date | None = None, *,
              force: bool = False) -> list[Candle]:
    """FRED 시계열 → 종가전용 Candle 리스트(오름차순). 캐시: data/_hist_cache/fred/{ID}.json.

    예: load_fred("NASDAQ100"), load_fred("DTB3")(3개월 T-bill 수익률), load_fred("VIXCLS").
    지수/금리/변동성 '레벨'이라 배당조정 개념이 없다 → OHLC=close, adjusted 옵션 무의미.
    """
    path = FRED_CACHE_DIR / f"{series}.json"
    if path.exists() and not force:
        payload = json.loads(path.read_text())
    else:
        rows = _fetch_fred(series)
        if not rows:
            raise HistDataError(f"FRED {series}: 빈/무효 응답")
        payload = {"series": series, "source": "fred",
                   "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "rows": rows}
        FRED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    candles = rows_to_candles(series, payload["rows"], adjusted=False)
    if start is not None:
        candles = [c for c in candles if c.dt >= start]
    if end is not None:
        candles = [c for c in candles if c.dt <= end]
    return candles


def index_total_return(index_candles: list[Candle], div_yield_annual: float, *,
                       trading_days: int = 252, symbol: str | None = None) -> list[Candle]:
    """가격지수(예: FRED NASDAQ100)에 연배당수익률을 더해 총수익(배당재투자)을 근사.

    TR_t = TR_(t-1) · (1 + 가격수익률 + div_yield_annual/trading_days). 종가전용 Candle,
    첫날 NAV=지수 첫 종가. 실물 대비 정밀하진 않지만(배당 시점/재투자 단순화) 장기 총수익 근사.
    """
    base = sorted(index_candles, key=lambda c: c.dt)
    if len(base) < 2:
        return []
    sym = symbol or f"{base[0].symbol}-TR"
    dy = div_yield_annual / trading_days
    out: list[Candle] = [Candle(sym, base[0].dt, base[0].close, base[0].close,
                                base[0].close, base[0].close, 0.0)]
    nav = base[0].close
    for i in range(1, len(base)):
        prev_c, cur = base[i - 1], base[i]
        r = (cur.close / prev_c.close - 1.0) if prev_c.close > 0 else 0.0
        nav *= (1.0 + r + dy)
        out.append(Candle(sym, cur.dt, nav, nav, nav, nav, 0.0))
    return out


# --------------------------------------------------------------------------- Nasdaq API (폴백)
def _clean_num(s) -> float:
    """'$28.1775' / '40,711,790' / 'N/A' → float. 파싱 불가는 ValueError."""
    t = str(s).replace("$", "").replace(",", "").strip()
    if t in ("", "N/A", "--", "null"):
        raise ValueError(f"non-numeric: {s!r}")
    return float(t)


def _mdy(s: str) -> date:
    """'MM/DD/YYYY' → date."""
    m, d, y = str(s).strip().split("/")
    return date(int(y), int(m), int(d))


def _parse_nasdaq_historical(data: dict) -> list[dict]:
    """Nasdaq /historical JSON → rows(오름차순, a=None). 가격의 '$'/',' 제거, 날짜 MM/DD/YYYY."""
    rows: list[dict] = []
    tt = (data.get("data") or {}).get("tradesTable") or {}
    for r in (tt.get("rows") or []):
        try:
            d = _mdy(r["date"])
            c = _clean_num(r["close"])
            o = _clean_num(r.get("open", r["close"]))
            h = _clean_num(r.get("high", r["close"]))
            low = _clean_num(r.get("low", r["close"]))
        except (KeyError, ValueError):
            continue
        try:
            v = _clean_num(r.get("volume", "0"))
        except ValueError:
            v = 0.0
        rows.append({"d": d.isoformat(), "o": o, "h": h, "l": low, "c": c, "v": v, "a": None})
    rows.sort(key=lambda x: x["d"])
    return rows


def _parse_nasdaq_dividends(data: dict) -> list[tuple[date, float]]:
    """Nasdaq /dividends JSON → [(ex_date, amount)]. exOrEffDate=N/A / 금액무효는 제외."""
    out: list[tuple[date, float]] = []
    dv = (data.get("data") or {}).get("dividends") or {}
    for r in (dv.get("rows") or []):
        ex, amt = r.get("exOrEffDate"), r.get("amount")
        if not ex or str(ex).upper() == "N/A" or amt in (None, ""):
            continue
        try:
            out.append((_mdy(ex), _clean_num(amt)))
        except ValueError:
            continue
    return out


def _apply_dividend_adjustment(rows: list[dict],
                               dividends: list[tuple[date, float]]) -> list[dict]:
    """분할반영 rows에 배당조정 adjclose(a)를 채운다 → 총수익(배당재투자).

    관례(Yahoo/CRSP): ex-date D 배당 d는 D 직전 종가 C로 계수 (1 − d/C)를 만들어
    **D 이전(strictly before) 모든 봉**의 가격에 곱한다. a[i] = close[i] · ∏(미래 배당 계수).
    최신봉은 a==close(계수 1). rows는 입력을 in-place 갱신해 반환.
    """
    if not rows:
        return rows
    dates = [date.fromisoformat(r["d"]) for r in rows]
    closes = [r["c"] for r in rows]
    n = len(rows)
    ratio_at = [1.0] * n                       # ex_idx 위치의 (1 − d/prevclose) 누적
    for ex, amt in dividends:
        ex_idx = bisect.bisect_left(dates, ex)  # ex-date(비거래일이면 그 이후 첫 거래일)
        if ex_idx <= 0 or ex_idx >= n:
            continue                            # 데이터 밖 또는 이전 봉 없음 → 무시
        prev_close = closes[ex_idx - 1]
        if prev_close <= 0:
            continue
        ratio_at[ex_idx] *= max(0.0, 1.0 - amt / prev_close)
    # a[i] = close[i] · ∏_{E>i} ratio_at[E]  (suffix product)
    run = 1.0
    for i in range(n - 1, -1, -1):
        rows[i]["a"] = closes[i] * run
        run *= ratio_at[i]
    return rows


def _nasdaq_json(url: str) -> dict:
    return json.loads(_http_get(url, timeout=30, headers=dict(_NASDAQ_HEADERS)))


def _fetch_nasdaq_daily(symbol: str, assetclass: str | None = None) -> list[dict]:
    """Nasdaq historical + dividends → 배당조정 rows. assetclass 미지정 시 etf→stocks 순 시도."""
    end = date.today()
    start = date(end.year - _NASDAQ_HIST_YEARS, end.month, end.day)
    q = urllib.parse.quote(symbol.upper(), safe="")
    classes = [assetclass] if assetclass else ["etf", "stocks"]
    last: Exception | None = None
    for ac in classes:
        try:
            hist = _nasdaq_json(f"https://api.nasdaq.com/api/quote/{q}/historical"
                                f"?assetclass={ac}&fromdate={start}&todate={end}&limit=9999")
            rows = _parse_nasdaq_historical(hist)
            if not rows:
                continue
            try:
                dv = _nasdaq_json(f"https://api.nasdaq.com/api/quote/{q}/dividends?assetclass={ac}")
                divs = _parse_nasdaq_dividends(dv)
            except Exception:  # noqa: BLE001  배당 조회 실패 시 미조정(raw)으로라도 반환
                divs = []
            return _apply_dividend_adjustment(rows, divs)
        except Exception as e:  # noqa: BLE001
            last = e
    if last is not None:
        raise last
    raise HistDataError(f"nasdaq {symbol}: 빈 응답")
