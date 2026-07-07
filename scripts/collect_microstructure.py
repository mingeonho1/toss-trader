#!/usr/bin/env python3
"""Collect Toss orderbook/trades snapshots as raw JSONL.

This script never places orders. It only calls read-only market-data endpoints
and stores raw responses for later validation.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.client import TossClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "microstructure"
LOG = ROOT / "data" / "microstructure_collector.log"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(msg: str) -> None:
    line = f"{_now().isoformat()} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _symbols(raw: str) -> list[str]:
    out = []
    for item in raw.replace(" ", ",").split(","):
        sym = item.strip().upper()
        if sym and sym not in out:
            out.append(sym)
    if not out:
        raise ValueError("--symbols must contain at least one symbol")
    return out


def _session_info(client: TossClient, offline_date: str | None = None) -> dict[str, Any]:
    if offline_date:
        return {"session_date": offline_date, "regular_market": None}
    try:
        cal = client.get_market_calendar("US")
        today = cal.get("today", {}) if isinstance(cal, dict) else {}
        return {
            "session_date": str(today.get("date") or date.today().isoformat()),
            "regular_market": today.get("regularMarket"),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "session_date": date.today().isoformat(),
            "regular_market": None,
            "calendar_error": str(exc),
        }


def _call_ms(fn) -> tuple[Any | None, float, str | None]:
    start = time.monotonic()
    try:
        return fn(), (time.monotonic() - start) * 1000.0, None
    except Exception as exc:  # noqa: BLE001
        return None, (time.monotonic() - start) * 1000.0, str(exc)


def collect_symbol(client: TossClient, symbol: str, *, trades_count: int,
                   session: dict[str, Any]) -> dict[str, Any]:
    ts = _now().isoformat()
    started = time.monotonic()
    orderbook, orderbook_ms, orderbook_err = _call_ms(lambda: client.get_orderbook(symbol))
    trades, trades_ms, trades_err = _call_ms(lambda: client.get_trades(symbol, trades_count))
    errors = {}
    if orderbook_err:
        errors["orderbook"] = orderbook_err
    if trades_err:
        errors["trades"] = trades_err
    if session.get("calendar_error"):
        errors["calendar"] = session["calendar_error"]
    return {
        "schema": 1,
        "ts": ts,
        "session_date": session.get("session_date"),
        "regular_market": session.get("regular_market"),
        "symbol": symbol,
        "ok": not errors,
        "latency_ms": (time.monotonic() - started) * 1000.0,
        "latency": {
            "orderbook_ms": orderbook_ms,
            "trades_ms": trades_ms,
        },
        "errors": errors,
        "orderbook": orderbook,
        "trades": trades,
    }


def _write_record(out_dir: Path, record: dict[str, Any]) -> Path:
    session_date = str(record.get("session_date") or date.today().isoformat())
    symbol = str(record["symbol"]).upper()
    path = out_dir / session_date / f"{symbol}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def run_once(client: TossClient, symbols: list[str], args: argparse.Namespace) -> int:
    session = _session_info(client, args.offline_date)
    if args.regular_only and session.get("regular_market") is not True:
        _log(f"skip: US regular market is not open (session={session.get('session_date')})")
        return 0

    ok = True
    for symbol in symbols:
        record = collect_symbol(client, symbol, trades_count=args.trades_count, session=session)
        if args.dry_run:
            print(json.dumps(record, ensure_ascii=False, indent=2))
        else:
            path = _write_record(Path(args.out_dir), record)
            _log(
                f"stored {symbol} ok={record['ok']} latency={record['latency_ms']:.1f}ms "
                f"path={path}"
            )
        ok = ok and bool(record["ok"])
        if args.symbol_pause_sec > 0:
            time.sleep(args.symbol_pause_sec)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect read-only Toss orderbook/trades snapshots.")
    parser.add_argument("--symbols", default="QQQ",
                        help="comma/space separated symbols, e.g. QQQ,SPY")
    parser.add_argument("--interval-sec", type=float, default=30.0,
                        help="poll interval in watch mode")
    parser.add_argument("--symbol-pause-sec", type=float, default=0.25,
                        help="pause between symbols to avoid bursty polling")
    parser.add_argument("--duration-sec", type=float,
                        help="stop after this many seconds in watch mode")
    parser.add_argument("--max-samples", type=int,
                        help="stop after this many polling cycles")
    parser.add_argument("--once", action="store_true",
                        help="collect one polling cycle and exit")
    parser.add_argument("--regular-only", action="store_true",
                        help="skip collection unless Toss reports US regular market open")
    parser.add_argument("--trades-count", type=int, default=50,
                        help="recent trades count per symbol, max 50")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT),
                        help="output root for JSONL files")
    parser.add_argument("--dry-run", action="store_true",
                        help="print records instead of writing files")
    parser.add_argument("--offline-date",
                        help="testing hook: skip market calendar and use this session date")
    args = parser.parse_args()

    if args.interval_sec <= 0:
        raise ValueError("--interval-sec must be positive")
    if args.symbol_pause_sec < 0:
        raise ValueError("--symbol-pause-sec must be >= 0")

    symbols = _symbols(args.symbols)
    client = TossClient()

    started = time.monotonic()
    cycles = 0
    while True:
        cycles += 1
        try:
            rc = run_once(client, symbols, args)
        except Exception as exc:  # noqa: BLE001
            # 장시간 무인 수집이 전제라, 한 사이클의 예기치 못한 실패(디스크/IO 등)로
            # 프로세스가 죽지 않게 한다. --once면 실패를 종료코드로 알린다.
            rc = 1
            try:
                _log(f"collect cycle failed: {exc}")
            except OSError:
                print(f"collect cycle failed (log write also failed): {exc}", flush=True)
        if args.once:
            return rc
        if args.max_samples is not None and cycles >= args.max_samples:
            return rc
        if args.duration_sec is not None and time.monotonic() - started >= args.duration_sec:
            return rc
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
