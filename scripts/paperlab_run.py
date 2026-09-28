#!/usr/bin/env python3
"""공격형 포워드 페이퍼 랩 러너 — 모든 전략을 키 없는 일봉 종가로 매 새 거래일마다 전진(멱등·주문 없음).

동작:
  1) 필요한 심볼을 캐시(data/_hist_cache)에서 로드(조정종가). 코어 심볼(QQQ/TQQQ/SQQQ)이 없으면
     1회 전체 히스토리 페치(폴백; 429면 즉시 중단). 포워드 갱신은 Nasdaq 최근창으로 정중히(≥1.5s,
     429 즉시 중단) 코어 심볼만 갱신한다.
  2) 각 전략 상태를 data/paperlab/{name}/ 에서 로드/초기화 → 마지막 처리일 이후 새 거래일만 전진.
  3) reports/paperlab_latest.md 리더보드 작성.

옵션:
  --backfill-from YYYY-MM-DD  과거 재현(BACKTEST) 별도 리포트 reports/paperlab_backfill.md 작성(페이퍼 아님).
  --reset                     모든 페이퍼 장부 재초기화(시작일 = START_DATE).
  --no-refresh                포워드 코어 심볼 네트워크 갱신 생략(캐시만).
  --offline                   네트워크 전면 금지(캐시만; 누락 코어 심볼은 그냥 건너뜀).

사용:
  PYTHONPATH=src python scripts/paperlab_run.py                       # 포워드 전진 + 리더보드
  PYTHONPATH=src python scripts/paperlab_run.py --backfill-from 2021-01-01 --offline
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import histdata                                   # noqa: E402
from toss_trader.models import Candle                              # noqa: E402
from toss_trader.paperlab import (PaperLab, load_state, render_backfill,  # noqa: E402
                                  render_leaderboard, save_state)
from toss_trader.paperlab_strategies import build_roster          # noqa: E402
from toss_trader.paperlab_strategies._universes import ETF_LIKE, NDX_100  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAPERLAB_DIR = DATA / "paperlab"
HIST_CACHE = DATA / "_hist_cache"
REPORTS = ROOT / "reports"
LEADERBOARD = REPORTS / "paperlab_latest.md"
BACKFILL_REPORT = REPORTS / "paperlab_backfill.md"

START_DATE = date(2026, 9, 28)        # 페이퍼 시작일(오늘의 다음 거래일; 부록 v3 목표 변경 시점)
CORE_SYMBOLS = ["QQQ", "TQQQ", "SQQQ"]
REFRESH_SPACING = 1.6                  # 정중 폴링(≥1.5s)
REFRESH_DAYS = 14


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    print(f"{_now()} {msg}", flush=True)


# ─────────────────────────────────────────── 데이터 로딩(캐시 우선, 코어만 네트워크)
def _cache_file(symbol: str) -> Path:
    safe = symbol.replace("^", "_").replace("/", "_")
    return HIST_CACHE / f"{safe}.json"


def _load_from_cache(symbol: str) -> list[Candle] | None:
    if not _cache_file(symbol).exists():
        return None
    try:
        return histdata.load_symbol(symbol, adjusted=True)      # 캐시 존재 → 네트워크 없음
    except Exception as exc:  # noqa: BLE001
        _log(f"cache load failed {symbol}: {type(exc).__name__}: {exc}")
        return None


def _fetch_full(symbol: str) -> list[Candle] | None:
    """누락 코어 심볼 1회 전체 히스토리 페치(Yahoo→Nasdaq→Stooq). 429/실패면 None."""
    try:
        return histdata.load_symbol(symbol, adjusted=True, force=False)
    except Exception as exc:  # noqa: BLE001
        _log(f"full fetch failed {symbol}: {type(exc).__name__}: {exc}")
        return None


def _polite_refresh(symbols: Sequence[str], *, sleep: Callable[[float], None] = time.sleep) -> str | None:
    """코어 심볼 최근 종가만 Nasdaq 최근창으로 정중히 갱신(새 거래일만 병합). 429/오류 즉시 중단."""
    import json
    for i, sym in enumerate(symbols):
        if i > 0:
            sleep(REFRESH_SPACING)
        try:
            rows = histdata.fetch_nasdaq_recent(sym, days=REFRESH_DAYS, assetclass="etf")
        except Exception as exc:  # noqa: BLE001  429/네트워크/파싱 → 중단(정중)
            return f"{sym}: {type(exc).__name__}: {exc}"
        if not rows:
            continue
        path = _cache_file(sym)
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else \
                {"symbol": sym, "source": "nasdaq", "rows": []}
            by_d = {str(r["d"]): r for r in (payload.get("rows") or []) if "d" in r}
            for r in rows:
                if str(r.get("d")) not in by_d:                 # 새 거래일만 추가
                    by_d[str(r["d"])] = r
            payload["rows"] = [by_d[k] for k in sorted(by_d)]
            payload["fetched"] = _now()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            import os
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            return f"{sym}: write {type(exc).__name__}: {exc}"
    return None


def _load_panel(symbols: Sequence[str], *, offline: bool) -> dict[str, list[Candle]]:
    panel: dict[str, list[Candle]] = {}
    for sym in symbols:
        candles = _load_from_cache(sym)
        if candles is None and sym in CORE_SYMBOLS and not offline:
            _log(f"core symbol {sym} missing from cache → fetching full history once")
            candles = _fetch_full(sym)
        if candles:
            panel[sym] = candles
    return panel


def _by_date(panel: Mapping[str, list[Candle]]) -> dict[str, dict[date, Candle]]:
    return {sym: {c.dt: c for c in candles} for sym, candles in panel.items()}


def _master_dates(panel: Mapping[str, list[Candle]], *, ref: str = "QQQ") -> list[date]:
    if ref in panel and panel[ref]:
        return [c.dt for c in panel[ref]]
    all_dates: set[date] = set()
    for candles in panel.values():
        all_dates.update(c.dt for c in candles)
    return sorted(all_dates)


def _bench_closes(panel: Mapping[str, list[Candle]]) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    for sym in ("QQQ", "TQQQ"):
        if sym in panel:
            out[sym] = {c.dt: c.close for c in panel[sym]}
    return out


# ─────────────────────────────────────────── 로스터 구성(데이터 인지 유니버스 주입)
def _cached_universe() -> list[str]:
    """캐시된 단일종목 유니버스(hot_rvol_swing 스캔용). ETF/레버리지 제외, FRED/지수 제외."""
    syms: list[str] = []
    for p in sorted(HIST_CACHE.glob("*.json")):
        name = p.stem
        if name.startswith("_") or name in ETF_LIKE:
            continue
        if name.isupper() or name.replace(".", "").isalpha():
            syms.append(name)
    return syms


def _build(swing_universe: Sequence[str] | None, mom_universe: Sequence[str] | None):
    return build_roster(swing_universe=swing_universe, mom_universe=mom_universe)


# ─────────────────────────────────────────── 실행: 포워드 페이퍼
def run_forward(args: argparse.Namespace) -> int:
    swing_uni = _cached_universe()
    mom_uni = [s for s in NDX_100 if _cache_file(s).exists()]
    roster = _build(swing_uni, mom_uni)

    needed: set[str] = {"QQQ", "TQQQ"}
    for strat in roster:
        needed.update(strat.universe())

    if not args.offline and not args.no_refresh:
        stopped = _polite_refresh(CORE_SYMBOLS)
        _log(f"refresh core closes: stopped={stopped}")

    panel = _load_panel(sorted(needed), offline=args.offline)
    if "QQQ" not in panel:
        _log("FATAL: QQQ cache missing; cannot run.")
        return 1
    by_date = _by_date(panel)
    master = _master_dates(panel)     # 전체 날짜(워밍업 포함); 엔진이 START_DATE 이전은 신호용으로만 사용

    summaries: list[dict[str, Any]] = []
    for strat in roster:
        lab = PaperLab(strat)
        state = None if args.reset else load_state(PAPERLAB_DIR, strat.name)
        if state is None:
            state = lab.fresh_state(START_DATE)
        state = lab.run(state, master, by_date)
        save_state(PAPERLAB_DIR, strat.name, state)
        summaries.append(lab.summarize(state))

    report = render_leaderboard(summaries, bench=_bench_closes(panel),
                                generated=_now(), start_default=START_DATE)
    REPORTS.mkdir(parents=True, exist_ok=True)
    LEADERBOARD.write_text(report, encoding="utf-8")
    live = sum(1 for s in summaries if s["days_live"] > 0)
    window = sum(1 for d in master if d >= START_DATE)
    _log(f"paperlab forward: strategies={len(summaries)} with_sessions={live} "
         f"window_sessions={window} → {LEADERBOARD}")
    return 0


# ─────────────────────────────────────────── 실행: 백필(BACKTEST)
def run_backfill(args: argparse.Namespace, backfill_from: date) -> int:
    swing_uni = _cached_universe()
    mom_uni = [s for s in NDX_100 if _cache_file(s).exists()]
    roster = _build(swing_uni, mom_uni)

    needed: set[str] = {"QQQ", "TQQQ"}
    for strat in roster:
        needed.update(strat.universe())
    panel = _load_panel(sorted(needed), offline=args.offline)
    if "QQQ" not in panel:
        _log("FATAL: QQQ cache missing; cannot backfill.")
        return 1
    by_date = _by_date(panel)
    master = _master_dates(panel)     # 전체 날짜(워밍업 포함); 엔진이 backfill_from 이전은 신호용으로만 사용

    summaries: list[dict[str, Any]] = []
    for strat in roster:
        lab = PaperLab(strat)
        state = lab.fresh_state(backfill_from)
        state = lab.run(state, master, by_date)
        # 검사 편의를 위해 별도 서브폴더에 멱등 저장(페이퍼 장부와 분리).
        save_state(PAPERLAB_DIR / "_backtest", strat.name, state)
        summaries.append(lab.summarize(state))

    report = render_backfill(summaries, bench=_bench_closes(panel),
                             generated=_now(), backfill_from=backfill_from)
    REPORTS.mkdir(parents=True, exist_ok=True)
    BACKFILL_REPORT.write_text(report, encoding="utf-8")
    window = sum(1 for d in master if d >= backfill_from)
    _log(f"paperlab backfill from {backfill_from}: strategies={len(summaries)} "
         f"window_sessions={window} → {BACKFILL_REPORT}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Aggressive forward paper lab runner (no real orders).")
    ap.add_argument("--backfill-from", type=str, default=None,
                    help="also run a historical BACKTEST replay from YYYY-MM-DD into a separate report")
    ap.add_argument("--reset", action="store_true", help="re-initialize all paper books")
    ap.add_argument("--no-refresh", action="store_true", help="skip network refresh of core closes")
    ap.add_argument("--offline", action="store_true", help="no network at all (cache only)")
    ap.add_argument("--backfill-only", action="store_true", help="run only the backfill replay")
    args = ap.parse_args(argv)

    rc = 0
    if args.backfill_from:
        bf = date.fromisoformat(args.backfill_from)
        rc |= run_backfill(args, bf)
    if not args.backfill_only:
        rc |= run_forward(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
