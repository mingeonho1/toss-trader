#!/usr/bin/env python3
"""c3c PIT 멤버 일봉 프리페치 (키 불필요, Nasdaq→Stooq, Yahoo 백오프 회피).

fja05680/sp500 PIT 멤버십에서 나온 '창 내 전(全) 편입 종목' 유니언을 캐시에 채운다.
Yahoo v8 은 이 네트워크에서 하드-429 → 실패 심볼마다 ~30s 백오프가 걸리므로,
여기서는 **Yahoo 를 건너뛰고** Nasdaq(assetclass=stocks/etf) 를 우선, 없으면 Stooq(단일 CSV)
만 시도한다. 둘 다 비면 '데이터 없음'(상장폐지/피인수/리네임)으로 기록한다.

- 요청 간격 ≥1.5s(정중). Nasdaq 429 면 해당 심볼 스킵(우회 금지).
- 성공분은 histdata 캐시 포맷으로 data/_hist_cache 에 저장(load_symbol 이 그대로 소비).
- 산출: data/pit/fetch_status.json — {symbol: {status, source, n, first, last}}.

사용: PYTHONPATH=src .venv/bin/python scripts/fetch_pit_members.py [--limit N] [--sleep S]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import json as _json
import urllib.parse as _uparse
import urllib.request as _ureq

from toss_trader import histdata  # noqa: E402

PIT_DIR = ROOT / "data" / "pit"
STATUS_PATH = PIT_DIR / "fetch_status.json"

# 바운디드 opener: 짧은 타임아웃·단일시도로 Nasdaq 스로틀 행(hang)을 회피(우회 아님, 그냥 스킵).
_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        ctx = histdata.build_ssl_context()
        op = _ureq.build_opener(_ureq.HTTPSHandler(context=ctx))
        op.addheaders = [("User-Agent", "toss-trader-research/0.1 (mingh@patsol.kr)")]
        _OPENER = op
    return _OPENER


def _nasdaq_bounded(url: str, timeout: float = 12.0) -> dict:
    req = _ureq.Request(url, headers=dict(histdata._NASDAQ_HEADERS))
    with _opener().open(req, timeout=timeout) as resp:
        return _json.loads(resp.read())


def _cache_write(symbol: str, source: str, rows: list[dict]) -> None:
    payload = {"symbol": symbol, "source": source,
               "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "rows": rows}
    histdata.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    histdata._cache_path(symbol).write_text(json.dumps(payload))


def _try_nasdaq(symbol: str, timeout: float = 12.0) -> list[dict]:
    """Nasdaq historical+dividends → 배당조정 rows. 각 요청 단일시도·짧은 타임아웃(행 회피)."""
    from datetime import timedelta
    end = date.today()
    start = end - timedelta(days=365 * 11)
    variants = [symbol]
    if "." in symbol:
        variants += [symbol.replace(".", "/"), symbol.replace(".", "")]
    for v in variants:
        q = _uparse.quote(v.upper(), safe="")
        for ac in ("stocks", "etf"):
            try:
                hist = _nasdaq_bounded(
                    f"https://api.nasdaq.com/api/quote/{q}/historical"
                    f"?assetclass={ac}&fromdate={start}&todate={end}&limit=9999", timeout)
                rows = histdata._parse_nasdaq_historical(hist)
                if not rows:
                    continue
                try:
                    dv = _nasdaq_bounded(
                        f"https://api.nasdaq.com/api/quote/{q}/dividends?assetclass={ac}", timeout)
                    divs = histdata._parse_nasdaq_dividends(dv)
                except Exception:  # noqa: BLE001  배당 실패 시 미조정 반환
                    divs = []
                return histdata._apply_dividend_adjustment(rows, divs)
            except Exception:  # noqa: BLE001  타임아웃/오류 → 다음 변형/스킵
                continue
    return []


def _try_stooq(symbol: str) -> list[dict]:
    try:
        rows = histdata._fetch_stooq_daily(symbol)
        return rows or []
    except Exception:  # noqa: BLE001
        return []


def ever_members(csv_path: Path, start: date, end: date) -> list[str]:
    import csv
    import io
    txt = csv_path.read_text()
    recs = []
    for r in csv.reader(io.StringIO(txt)):
        if len(r) < 2 or r[0] == "date":
            continue
        try:
            d = date.fromisoformat(r[0])
        except ValueError:
            continue
        recs.append((d, [t.strip() for t in r[1].split(",") if t.strip()]))
    recs.sort()
    # asof(start) membership
    cur: list[str] = []
    for d, tk in recs:
        if d <= start:
            cur = tk
    ever = set(cur)
    for d, tk in recs:
        if start <= d <= end:
            ever |= set(tk)
    return sorted(ever)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="테스트용 최대 심볼 수(0=전체)")
    ap.add_argument("--sleep", type=float, default=1.6, help="심볼 간 간격(초)")
    ap.add_argument("--symbols", type=str, default="", help="쉼표구분 심볼(디버그)")
    args = ap.parse_args()

    csv_path = PIT_DIR / "sp500_components_changes.csv"
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = ever_members(csv_path, date(2016, 9, 1), date(2026, 9, 30))
    cached = {p.stem for p in histdata.CACHE_DIR.glob("*.json")}

    status: dict = {}
    if STATUS_PATH.exists():
        try:
            status = json.loads(STATUS_PATH.read_text())
        except Exception:  # noqa: BLE001
            status = {}

    todo = [s for s in syms if s.replace(".", "_") not in cached and status.get(s, {}).get("status") != "nodata"]
    if args.limit:
        todo = todo[:args.limit]
    print(f"ever-members={len(syms)} cached_or_done skipped, todo={len(todo)}", flush=True)

    n_ok = n_no = 0
    for i, s in enumerate(todo):
        t0 = time.time()
        rows = _try_nasdaq(s)
        src = "nasdaq"
        if not rows:
            rows = _try_stooq(s)
            src = "stooq"
        if rows:
            _cache_write(s, src, rows)
            status[s] = {"status": "ok", "source": src, "n": len(rows),
                         "first": rows[0]["d"], "last": rows[-1]["d"]}
            n_ok += 1
            tag = "OK"
        else:
            status[s] = {"status": "nodata", "source": None, "n": 0}
            n_no += 1
            tag = "NODATA"
        if i % 10 == 0 or tag == "NODATA":
            print(f"[{i+1}/{len(todo)}] {s}: {tag} src={src if rows else '-'} "
                  f"n={len(rows)} ({time.time()-t0:.1f}s) ok={n_ok} nodata={n_no}", flush=True)
        # 주기적 상태 저장(중단 대비)
        if i % 20 == 0:
            PIT_DIR.mkdir(parents=True, exist_ok=True)
            STATUS_PATH.write_text(json.dumps(status, indent=0))
        time.sleep(args.sleep)

    PIT_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=0))
    print(f"DONE ok={n_ok} nodata={n_no} total_status={len(status)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
