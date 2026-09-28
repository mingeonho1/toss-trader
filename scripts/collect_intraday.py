#!/usr/bin/env python3
"""키 없는 인트라데이(1분/5분) 미국주식 바 수집기 — 워치리스트 + 당일 무버스 누적.

미국 장 마감 후(또는 온디맨드) 1회 실행해 직전 세션의 분봉을 워치리스트에 추가한다.
소스는 `toss_trader.intraday_sources`(Yahoo v8 → 진짜 OHLCV, Nasdaq /chart → 직전 세션 1분
last-price). Yahoo 429면 그 실행 동안 Yahoo를 중단하고 Nasdaq으로 폴백한다(우회 없음).

주문/체결/전략 편입 없음 — 순수 데이터 수집. 자체 인트라데이 데이터셋을 시간에 걸쳐 축적한다.

예:
  # 직전 세션 1분봉을 워치리스트에 누적(Nasdaq만; Yahoo 재-throttle 방지)
  PYTHONPATH=src python scripts/collect_intraday.py --source nasdaq
  # 상승률/거래량 상위 12종목까지 추가
  PYTHONPATH=src python scripts/collect_intraday.py --source nasdaq --movers 12
  # Yahoo가 열릴 때 백필(1m≈7일, 5m≈60일, 60m≈730일); 429면 자동 중단
  PYTHONPATH=src python scripts/collect_intraday.py --backfill --intervals 1m,5m,60m
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import intraday_sources as isrc  # noqa: E402
from toss_trader.intraday_sources import INTRADAY_CACHE_DIR  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "intraday_collector.log"

# 워치리스트(장기 매매 아이디어 후보). SQQQ는 롱온리라 제외.
WATCHLIST = ["SPY", "QQQ", "TQQQ", "NVDA", "TSLA", "AAPL", "AMD", "META",
             "MSFT", "AMZN", "PLTR", "MSTR", "SMCI", "COIN"]

# 비백필(당일 append) 시 Yahoo 조회 범위(일). 넉넉히 잡아도 병합이 중복 제거.
_NONBACKFILL_DAYS = {"1m": 2, "2m": 5, "5m": 5, "15m": 10, "30m": 15,
                     "60m": 20, "1h": 20, "90m": 15}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(msg: str) -> None:
    line = f"{_now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _symbols(raw: str) -> list[str]:
    out: list[str] = []
    for item in (raw or "").replace(" ", ",").split(","):
        s = item.strip().upper()
        if s and s not in out:
            out.append(s)
    return out


def _space(state: dict, src: str, min_gap: float) -> None:
    """소스별 최소 요청 간격 보장(정중한 폴링). 요청 직전에 호출."""
    last = state["last"].get(src)
    now = time.monotonic()
    if last is not None:
        wait = min_gap - (now - last)
        if wait > 0:
            time.sleep(wait)
    state["last"][src] = time.monotonic()


def _fetch(symbol: str, interval: str, args: argparse.Namespace,
           state: dict) -> tuple[list[dict], str]:
    """(rows, source) 반환. source=auto면 yahoo→nasdaq. Yahoo 429는 실행 동안 래치 중단."""
    order = ["yahoo", "nasdaq"] if args.source == "auto" else [args.source]
    errs: list[str] = []
    for src in order:
        if src == "yahoo" and not state["yahoo_ok"]:
            continue
        try:
            if src == "yahoo":
                _space(state, "yahoo", args.yahoo_spacing)
                days = None if args.backfill else _NONBACKFILL_DAYS.get(interval)
                return isrc.fetch_yahoo(symbol, interval, days=days), "yahoo"
            _space(state, "nasdaq", args.nasdaq_spacing)
            base = isrc.fetch_nasdaq(symbol)
            minutes = isrc.interval_minutes(interval)
            rows = base if minutes <= 1 else isrc.aggregate(base, minutes)
            return rows, "nasdaq"
        except isrc.RateLimited as e:
            errs.append(str(e))
            if src == "yahoo":
                state["yahoo_ok"] = False
                _log(f"    429 → 이번 실행 동안 Yahoo 중단, Nasdaq 폴백 ({e})")
            continue
        except Exception as e:  # noqa: BLE001  다음 소스 시도
            errs.append(f"{src}: {type(e).__name__}: {e}")
            continue
    raise isrc.HistDataError(f"{symbol} {interval}: 모든 소스 실패 → " + " | ".join(errs))


def _movers_symbols(args: argparse.Namespace, state: dict) -> list[str]:
    """Nasdaq marketmovers → 당일 상승률/거래량 상위 심볼(가격 필터·개수 제한)."""
    if args.movers <= 0:
        return []
    try:
        _space(state, "nasdaq", args.nasdaq_spacing)
        data = isrc.fetch_movers()
    except Exception as e:  # noqa: BLE001
        _log(f"movers 조회 실패(스킵): {type(e).__name__}: {e}")
        return []
    wanted = [s.strip().lower() for s in (args.movers_sections or "").split(",") if s.strip()]
    sections = []
    if "gainers" in wanted:
        sections.append(isrc.MOVERS_GAINERS)
    if "active" in wanted:
        sections.append(isrc.MOVERS_ACTIVE_SHARE)
    if "dollar" in wanted:
        sections.append(isrc.MOVERS_ACTIVE_DOLLAR)
    picked: list[str] = []
    for sec in sections:
        for cls in ("STOCKS", "ETF"):
            for row in isrc.parse_movers(data, section=sec, assetclass=cls):
                px = row["price"]
                if px is not None and px < args.movers_min_price:
                    continue
                if row["symbol"] not in picked:
                    picked.append(row["symbol"])
    if picked:
        _log(f"movers 후보({len(picked)}): {', '.join(picked)}")
    return picked[:args.movers]


def run(args: argparse.Namespace) -> int:
    state = {"yahoo_ok": args.source in ("auto", "yahoo"), "last": {}}
    # 인터벌은 소문자 유지(_symbols는 티커용이라 대문자화하므로 되돌린다).
    intervals = [s.lower() for s in _symbols(args.intervals)] or ["1m"]
    exclude = set(_symbols(args.exclude))

    symbols = _symbols(args.symbols) if args.symbols else list(WATCHLIST)
    symbols += [s for s in _movers_symbols(args, state) if s not in symbols]
    symbols = [s for s in symbols if s not in exclude]
    if not symbols:
        _log("수집할 심볼이 없다.")
        return 1

    _log(f"start: {len(symbols)}종목 × {intervals} · source={args.source} "
         f"backfill={args.backfill} regular_only={args.regular_only} dry_run={args.dry_run}")
    cache_dir = Path(args.out_dir) if args.out_dir else None
    summary: dict[str, dict] = {}
    failures = 0
    for sym in symbols:
        for interval in intervals:
            key = f"{sym}_{interval}"
            try:
                rows, src = _fetch(sym, interval, args, state)
            except Exception as e:  # noqa: BLE001
                failures += 1
                _log(f"  {key}: FAIL {type(e).__name__}: {e}")
                summary[key] = {"error": str(e)}
                continue
            if args.regular_only:
                rows = [r for r in rows if r.get("regular")]
            if args.dry_run:
                reg = sum(1 for r in rows if r.get("regular"))
                _log(f"  {key}: [dry-run] src={src} fetched={len(rows)} (regular={reg})")
                summary[key] = {"source": src, "fetched": len(rows), "regular": reg}
                continue
            res = isrc.merge_write(sym, interval, rows, cache_dir=cache_dir)
            res["source"] = src
            summary[key] = res
            _log(f"  {key}: src={src} fetched={res['fetched_rows']} "
                 f"added={res['added']} updated={res['updated']} total={res['total']}")

    total_bars = sum(v.get("total", 0) for v in summary.values() if "error" not in v)
    _log(f"done: series={len(summary)} failures={failures} total_bars_in_cache={total_bars}")
    print(json.dumps({"summary": summary, "failures": failures}, ensure_ascii=False))
    return 0 if failures == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="키 없는 인트라데이 분봉 수집기(누적).")
    p.add_argument("--symbols", default="",
                   help="comma/space 구분 심볼. 비우면 기본 워치리스트.")
    p.add_argument("--intervals", default="1m",
                   help="comma 구분 인터벌(1m,5m,60m ...). 기본 1m.")
    p.add_argument("--source", choices=["auto", "nasdaq", "yahoo"], default="auto",
                   help="auto: Yahoo 우선→429면 Nasdaq 폴백. 기본 auto.")
    p.add_argument("--backfill", action="store_true",
                   help="각 소스가 허용하는 최대 히스토리를 당긴다(Yahoo 넓은 범위; Nasdaq은 직전 1세션).")
    p.add_argument("--movers", type=int, default=0,
                   help="당일 무버스에서 최대 N종목 추가(0=사용 안 함).")
    p.add_argument("--movers-sections", default="gainers,active",
                   help="무버스 섹션: gainers,active,dollar 중 comma 조합.")
    p.add_argument("--movers-min-price", type=float, default=5.0,
                   help="무버스 최소 가격 필터(저가 초저유동성 제외). 기본 $5.")
    p.add_argument("--exclude", default="SQQQ",
                   help="제외 심볼(comma). 기본 SQQQ(롱온리).")
    p.add_argument("--regular-only", action="store_true",
                   help="정규장(09:30~16:00 ET) 봉만 저장(기본은 확장장 포함 저장).")
    p.add_argument("--out-dir", default="",
                   help=f"캐시 디렉터리. 기본 {INTRADAY_CACHE_DIR}.")
    p.add_argument("--dry-run", action="store_true",
                   help="쓰지 않고 페치 개수만 출력.")
    p.add_argument("--then-shadow", action="store_true",
                   help="수집 직후 레인3 인트라데이 섀도(scripts/intraday_shadow_run.py) 를 이어 실행.")
    p.add_argument("--yahoo-spacing", type=float, default=1.6,
                   help="Yahoo 요청 최소 간격(초). 기본 1.6(≥1.5 준수).")
    p.add_argument("--nasdaq-spacing", type=float, default=0.6,
                   help="Nasdaq 요청 최소 간격(초). 기본 0.6.")
    args = p.parse_args()
    if args.yahoo_spacing < 1.5:
        _log(f"경고: yahoo-spacing {args.yahoo_spacing}s < 1.5s 권고. 1.5로 올림.")
        args.yahoo_spacing = 1.5
    rc = run(args)
    if args.then_shadow and not args.dry_run:
        rc = _then_shadow() or rc
    return rc


def _then_shadow() -> int:
    """수집 후 레인3 섀도 실행기를 이어서 돌린다(가상 트레이드 누적). 실패해도 수집 rc 는 보존."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import intraday_shadow_run  # noqa: E402
        _log("--then-shadow: intraday_shadow_run 실행")
        return intraday_shadow_run.run([])
    except Exception as e:  # noqa: BLE001
        _log(f"--then-shadow 실패(수집은 성공): {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
