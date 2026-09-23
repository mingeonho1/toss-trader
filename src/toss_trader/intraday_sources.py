"""키 불필요 인트라데이(1분/5분/60분) 미국주식 바 소스 — histdata.py 는 건드리지 않는 신규 모듈.

`histdata.load_intraday` 는 Yahoo 한 소스만 쓰고 이 네트워크에서 자주 429가 난다. 여기서는
**여러 키 없는 공개 소스**를 폴백/누적하고, `data/_hist_cache/intraday/{SYM}_{interval}.json`
(histdata 인트라데이 캐시와 동일 레이아웃)에 **ts(epoch) 기준 병합-누적**한다.

소스 (2026-09 이 네트워크에서 실측):
  1) Yahoo Finance v8 chart (interval=1m/5m/60m): **진짜 OHLCV + 히스토리**(1m≈7일, 5m≈60일,
     60m≈730일). 단, 이 네트워크에서 429 스로틀이 잦다 → 요청 간격 ≥1.5s, 429면 그 실행 동안
     해당 소스를 **중단(우회 금지)**. `RateLimited` 예외로 호출측에 알린다.
  2) Nasdaq `api.nasdaq.com/api/quote/{SYM}/chart?assetclass=stocks|etf`: 키 없이 **직전 세션
     1분 데이터**(확장장 04:00~20:00 ET 포함, ~960틱)를 준다. 매 호출 = 최근 1세션만(백필 깊이 없음).
     ⚠️ **분당 '체결 마지막가'만** 준다(OHLC 아님) → o=h=l=c=price 로 채우고, 거래량은 신뢰 소스가
        없어 v=0 으로 둔다(honest). `volumeChart` 는 가격을 미러링해 거래량으로 못 쓴다(실측 확인).
        5분/15분 등은 이 1분 last-price 를 버킷 집계 → 버킷 내 진짜 고저 범위가 생겨 의미 있는 OHLC.
  3) Nasdaq `api.nasdaq.com/api/marketmovers`: 당일 상승률 상위(MostAdvanced)·거래량 상위
     (MostActiveByShareVolume) 심볼 → 워치리스트 확장(고 RVOL 데이트레이딩 후보)에 사용.

바 공용 포맷(=histdata 인트라데이 row 와 호환, 추가로 `src` 태그):
    {"ts": epoch(int), "et": ISO ET datetime, "o","h","l","c","v": float,
     "regular": bool(정규장 09:30~16:00 ET), "src": "yahoo"|"nasdaq"}
`src` 는 histdata 파서/로더가 무시하므로 완전 호환(양방향으로 읽기 가능).

로그인/API키 필요 소스, 봇차단 우회는 쓰지 않는다. 모든 페치는 histdata 의 검증 SSL·쿠키
세션·백오프를 재사용한다.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import histdata as _hd
from .histdata import (  # 재사용(중복 방지). 사설 심볼이지만 같은 패키지 내부라 안정적.
    HistDataError,
    INTRADAY_CACHE_DIR,
    IntradayBar,
    _ET,
    _NASDAQ_HEADERS,
    _http_get,
    _is_regular_session,
    _parse_yahoo_intraday,
)

__all__ = [
    "RateLimited", "SOURCE_RANK", "INTRADAY_CACHE_DIR",
    "parse_nasdaq_chart", "parse_yahoo_intraday", "parse_movers",
    "aggregate", "merge_rows", "merge_write", "rows_to_bars",
    "fetch_nasdaq", "fetch_yahoo", "fetch_movers",
    "interval_minutes",
]


class RateLimited(HistDataError):
    """소스가 429로 레이트리밋. 호출측은 이 실행 동안 해당 소스를 중단해야 한다(우회 금지)."""


# 병합 시 소스 우선순위: 진짜 OHLCV(yahoo)가 price-only(nasdaq)를 덮지 못하게(반대는 허용).
SOURCE_RANK = {"yahoo": 3, "nasdaq": 1}

# Yahoo 인터벌별 1회 조회 기본 범위(일). histdata._INTRADAY_MAX_DAYS 와 정합.
_YAHOO_RANGE_DAYS = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60,
                     "60m": 730, "90m": 60, "1h": 730}

# assetclass 힌트(Nasdaq chart 는 stocks/etf 를 구분). 틀리면 다른 클래스로 폴백.
_ETF_HINTS = {
    "SPY", "QQQ", "TQQQ", "SQQQ", "QLD", "SSO", "UPRO", "IWM", "DIA", "VOO", "VTI",
    "VEA", "VNQ", "EEM", "GLD", "SLV", "DBC", "TLT", "IEF", "SHY", "BIL", "SCHD",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    "SMH", "SOXL", "SOXS", "ARKK", "HYG", "LQD",
}

_INTERVAL_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30,
                     "60m": 60, "1h": 60, "90m": 90}


def interval_minutes(interval: str) -> int:
    """'5m'→5, '1h'→60. 미지원 인터벌은 ValueError."""
    if interval not in _INTERVAL_MINUTES:
        raise ValueError(f"지원하지 않는 인터벌: {interval} (지원: {sorted(_INTERVAL_MINUTES)})")
    return _INTERVAL_MINUTES[interval]


# --------------------------------------------------------------------------- 순수 파서
def _to_float(s) -> float | None:
    """'$34.46' / '1,096.16' / '+5.0%' / 'N/A' → float|None(파싱 불가는 None)."""
    if s is None:
        return None
    t = str(s).replace("$", "").replace(",", "").replace("%", "").strip().lstrip("+")
    if t in ("", "N/A", "--", "null", "NaN", "none"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _et_from_nasdaq_millis(x_ms) -> datetime:
    """Nasdaq chart `x`(ms)는 ET 벽시계 시각을 UTC 필드로 인코딩한다(예: 12:00 ET → 12:00Z).

    → UTC로 읽어 naive 로 떼고 America/New_York 을 부여해 올바른 ET tz-aware 로 만든다.
    tzdb 부재 시 3~11월 EDT(-4)/그 외 EST(-5) 근사(드문 폴백).
    """
    naive = datetime.fromtimestamp(float(x_ms) / 1000.0, tz=timezone.utc).replace(tzinfo=None)
    if _ET is not None:
        return naive.replace(tzinfo=_ET)
    off = -4 if 3 <= naive.month <= 11 else -5
    return naive.replace(tzinfo=timezone(timedelta(hours=off)))


def parse_nasdaq_chart(data: dict, *, symbol: str | None = None) -> list[dict]:
    """Nasdaq /chart JSON → 1분 rows(공용 포맷). 네트워크 없음(테스트 가능).

    ⚠️ Nasdaq은 분당 '체결 마지막가'만 준다(OHLC 아님) → o=h=l=c=price, v=0.0(신뢰 거래량 없음).
    확장장(04:00~20:00 ET) 포함. 동일 ts 중복은 첫값 유지. ts 오름차순 정렬.
    """
    d = (data or {}).get("data") or {}
    chart = d.get("chart") or []
    rows: list[dict] = []
    seen: set[int] = set()
    for pt in chart:
        if not isinstance(pt, dict):
            continue
        x = pt.get("x")
        price = pt.get("y")
        if price is None:
            price = (pt.get("z") or {}).get("value")
        price = _to_float(price)
        if x is None or price is None:
            continue
        try:
            et = _et_from_nasdaq_millis(x)
        except (ValueError, OverflowError, OSError):
            continue
        ts = int(et.timestamp())
        if ts in seen:
            continue
        seen.add(ts)
        rows.append({"ts": ts, "et": et.isoformat(),
                     "o": price, "h": price, "l": price, "c": price, "v": 0.0,
                     "regular": _is_regular_session(et), "src": "nasdaq"})
    rows.sort(key=lambda r: r["ts"])
    return rows


def parse_yahoo_intraday(data: dict) -> list[dict]:
    """Yahoo v8 인트라데이 JSON → rows(진짜 OHLCV). histdata 파서 재사용 + src='yahoo' 태그."""
    rows = _parse_yahoo_intraday(data)
    for r in rows:
        r["src"] = "yahoo"
    return rows


# marketmovers 섹션 별칭.
MOVERS_GAINERS = "MostAdvanced"
MOVERS_ACTIVE_SHARE = "MostActiveByShareVolume"
MOVERS_ACTIVE_DOLLAR = "MostActiveByDollarVolume"
MOVERS_DECLINED = "MostDeclined"


def parse_movers(data: dict, *, section: str = MOVERS_GAINERS,
                 assetclass: str = "STOCKS") -> list[dict]:
    """Nasdaq marketmovers JSON → [{symbol, price, change}]. 순수 파서.

    section: MostAdvanced(상승률)·MostActiveByShareVolume(거래량)·MostActiveByDollarVolume·
             MostDeclined·Nasdaq100Movers. assetclass: STOCKS|ETF|MUTUALFUNDS.
    """
    d = (data or {}).get("data") or {}
    cls = d.get(assetclass) or {}
    blk = cls.get(section) or {}
    rows = (blk.get("table") or {}).get("rows") or []
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = (r.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append({"symbol": sym, "price": _to_float(r.get("lastSalePrice")),
                    "change": (str(r.get("change") or "")).strip()})
    return out


# --------------------------------------------------------------------------- 집계/병합(순수)
def aggregate(rows: list[dict], minutes: int) -> list[dict]:
    """1분 rows → N분 바. 버킷=floor(ts/period)*period(경계 정렬 → Yahoo N분과 ts 정합).

    o=버킷 첫봉, h=max, l=min, c=버킷 마지막봉, v=합. et/regular 은 버킷 경계 시각 기준.
    Nasdaq(price-only) 1분을 모으면 버킷 내 진짜 고저 범위가 생긴다. src 는 단일이면 그대로, 혼합이면 'mixed'.
    minutes<=1 이거나 빈 입력은 정렬만 해서 반환.
    """
    if minutes <= 1 or not rows:
        return sorted((dict(r) for r in rows), key=lambda r: r["ts"])
    period = minutes * 60
    buckets: dict[int, list[dict]] = {}
    for r in sorted(rows, key=lambda x: x["ts"]):
        b = (int(r["ts"]) // period) * period
        buckets.setdefault(b, []).append(r)
    out: list[dict] = []
    for b in sorted(buckets):
        grp = buckets[b]
        srcs = {r.get("src") for r in grp if r.get("src")}
        if _ET is not None:
            et_dt = datetime.fromtimestamp(b, tz=_ET)
        else:
            et_dt = datetime.fromtimestamp(b, tz=timezone.utc)
        out.append({
            "ts": b, "et": et_dt.isoformat(),
            "o": grp[0]["o"], "h": max(r["h"] for r in grp),
            "l": min(r["l"] for r in grp), "c": grp[-1]["c"],
            "v": float(sum(r.get("v", 0.0) or 0.0 for r in grp)),
            "regular": _is_regular_session(et_dt),
            "src": (next(iter(srcs)) if len(srcs) == 1 else "mixed"),
        })
    return out


def _rank(row: dict) -> int:
    return SOURCE_RANK.get(row.get("src"), 0)


def merge_rows(old: list[dict], new: list[dict]) -> list[dict]:
    """ts로 병합·중복제거·정렬. 동일 ts면 소스 우선순위 높은 쪽 유지, 동급이면 새 값이 덮는다.

    price-only Nasdaq 바가 이미 저장된 진짜 OHLCV Yahoo 바를 덮지 않도록(정정은 동급/상위만).
    """
    by_ts: dict[int, dict] = {int(r["ts"]): r for r in old}
    for r in new:
        k = int(r["ts"])
        cur = by_ts.get(k)
        if cur is None or _rank(r) >= _rank(cur):
            by_ts[k] = r
    return [by_ts[k] for k in sorted(by_ts)]


def rows_to_bars(symbol: str, rows: list[dict], *, regular_only: bool = False) -> list[IntradayBar]:
    """공용 rows → IntradayBar 리스트(오름차순). regular_only=True면 정규장 봉만."""
    out: list[IntradayBar] = []
    for r in sorted(rows, key=lambda x: x["ts"]):
        if regular_only and not r.get("regular"):
            continue
        out.append(IntradayBar(symbol, datetime.fromisoformat(r["et"]),
                               float(r["o"]), float(r["h"]), float(r["l"]),
                               float(r["c"]), float(r["v"]), bool(r["regular"])))
    return out


# --------------------------------------------------------------------------- 캐시 병합-쓰기
def _cache_path(symbol: str, interval: str, cache_dir: Path | str | None = None) -> Path:
    base = Path(cache_dir) if cache_dir is not None else INTRADAY_CACHE_DIR
    safe = symbol.replace("^", "_").replace("/", "_")
    return base / f"{safe}_{interval}.json"


def merge_write(symbol: str, interval: str, new_rows: list[dict], *,
                cache_dir: Path | str | None = None) -> dict:
    """새 rows를 캐시에 병합·저장(histdata 인트라데이 캐시와 동일 레이아웃). 요약 dict 반환.

    반환: {symbol, interval, path, before, added, updated, total, fetched_rows}.
    파일: {"symbol","interval","fetched","rows":[...]}. rows 는 ts 오름차순.
    """
    path = _cache_path(symbol, interval, cache_dir)
    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text()).get("rows", []) or []
        except (ValueError, OSError):
            existing = []
    before_ts = {int(r["ts"]) for r in existing}
    before = len(existing)
    merged = merge_rows(existing, new_rows)
    added = sum(1 for r in merged if int(r["ts"]) not in before_ts)
    payload = {"symbol": symbol, "interval": interval,
               "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "rows": merged}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return {"symbol": symbol, "interval": interval, "path": str(path),
            "before": before, "added": added, "updated": len(merged) - before - added,
            "total": len(merged), "fetched_rows": len(new_rows)}


# --------------------------------------------------------------------------- 페치(네트워크)
def _nasdaq_chart_json(symbol: str, assetclass: str) -> dict:
    q = urllib.parse.quote(symbol.upper(), safe="")
    url = f"https://api.nasdaq.com/api/quote/{q}/chart?assetclass={assetclass}"
    return json.loads(_http_get(url, timeout=30, headers=dict(_NASDAQ_HEADERS)))


def fetch_nasdaq(symbol: str, *, assetclass: str | None = None) -> list[dict]:
    """Nasdaq /chart → 직전 세션 1분 rows(공용 포맷). assetclass 미지정 시 힌트로 순서 결정 후 폴백.

    429는 RateLimited 로 올린다(호출측이 중단하도록). 빈 응답이면 HistDataError.
    """
    if assetclass:
        classes = [assetclass]
    elif symbol.upper() in _ETF_HINTS:
        classes = ["etf", "stocks"]
    else:
        classes = ["stocks", "etf"]
    last: Exception | None = None
    for ac in classes:
        try:
            data = _nasdaq_chart_json(symbol, ac)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited(f"nasdaq 429: {symbol}") from e
            last = e
            continue
        except Exception as e:  # noqa: BLE001  다음 클래스 시도
            last = e
            continue
        rows = parse_nasdaq_chart(data, symbol=symbol)
        if rows:
            return rows
        last = HistDataError(f"nasdaq {symbol} ({ac}): 빈 chart")
    if isinstance(last, Exception):
        raise last
    raise HistDataError(f"nasdaq {symbol}: 데이터 없음")


def _yahoo_intraday_json(symbol: str, interval: str, period1: int, period2: int) -> dict:
    """Yahoo v8 인트라데이 원본 JSON. query1/query2 순회. 429는 즉시 RateLimited(재시도 없음)."""
    q = urllib.parse.quote(symbol, safe="")
    last: Exception | None = None
    for host in _hd._YAHOO_HOSTS:
        url = (f"https://{host}/v8/finance/chart/{q}"
               f"?period1={period1}&period2={period2}&interval={interval}")
        try:
            with _hd._opener().open(url, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # 우회 금지: 첫 429에서 즉시 중단 신호(다른 호스트로도 재시도 안 함).
                raise RateLimited(f"yahoo 429: {symbol} {interval}") from e
            last = e
        except Exception as e:  # noqa: BLE001  다음 호스트 시도
            last = e
    if isinstance(last, Exception):
        raise last
    raise HistDataError(f"yahoo {symbol} {interval}: 데이터 없음")


def fetch_yahoo(symbol: str, interval: str = "5m", *, days: int | None = None,
                now: int | None = None) -> list[dict]:
    """Yahoo v8 → 인트라데이 rows(진짜 OHLCV). days 미지정 시 인터벌별 기본 범위.

    429는 RateLimited. 호출측은 ≥1.5s 간격 유지 + 429면 이번 실행 동안 Yahoo 중단.
    """
    if interval not in _YAHOO_RANGE_DAYS:
        raise ValueError(f"Yahoo 미지원 인터벌: {interval} (지원: {sorted(_YAHOO_RANGE_DAYS)})")
    now = int(now if now is not None else time.time())
    span = (days if days is not None else _YAHOO_RANGE_DAYS[interval]) * 86400
    data = _yahoo_intraday_json(symbol, interval, now - span, now)
    return parse_yahoo_intraday(data)


def fetch_movers() -> dict:
    """Nasdaq marketmovers 원본 JSON(키 없음). 파싱은 parse_movers()."""
    return json.loads(_http_get("https://api.nasdaq.com/api/marketmovers",
                                timeout=30, headers=dict(_NASDAQ_HEADERS)))
