#!/usr/bin/env python3
"""Analyze collected microstructure JSONL snapshots.

The parser is deliberately defensive because Toss may change response shapes.
If orderbook levels cannot be recognized, the report still shows coverage,
latency, and error statistics.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IN = ROOT / "data" / "microstructure"
DEFAULT_REPORT = ROOT / "reports" / "microstructure_latest.md"


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _as_rows(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("trades", "items", "executions", "data", "result"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
    return []


def _field(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return row[name]
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _price(row: dict[str, Any]) -> float | None:
    return _f(_field(row, ("price", "lastPrice", "tradePrice", "executionPrice", "bidPrice", "askPrice")))


def _qty(row: dict[str, Any]) -> float | None:
    return _f(_field(row, ("quantity", "qty", "size", "volume", "bidSize", "askSize", "bidQuantity", "askQuantity")))


def _key_tokens(key: str) -> set[str]:
    """camelCase/snake_case 키를 소문자 토큰으로 분해. 복수형은 단수도 포함.

    부분 문자열 매칭은 forbiddenList 같은 무관한 키를 'bid'로 오인하므로
    토큰 단위 완전 일치로만 판정한다.
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
    tokens = {t for t in re.split(r"[^a-zA-Z]+", snake.lower()) if t}
    tokens |= {t[:-1] for t in tokens if t.endswith("s") and len(t) > 3}
    return tokens


def _extract_named_levels(raw: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    for key, value in raw.items():
        if _key_tokens(key) & set(names) and isinstance(value, list):
            rows = [x for x in value if isinstance(x, dict)]
            if rows:
                return rows
    for value in raw.values():
        if isinstance(value, dict):
            rows = _extract_named_levels(value, names)
            if rows:
                return rows
    return []


def parse_orderbook(raw: Any) -> dict[str, float] | None:
    """Return best bid/ask and imbalance if the raw response is recognizable."""
    bids = _extract_named_levels(raw, ("bid", "buy"))
    asks = _extract_named_levels(raw, ("ask", "sell", "offer"))
    bid_levels = []
    ask_levels = []
    for row in bids:
        px = _f(_field(row, ("bidPrice", "price", "bid")))
        qty = _f(_field(row, ("bidSize", "bidQuantity", "quantity", "qty", "size", "volume")))
        if px and qty:
            bid_levels.append((px, qty))
    for row in asks:
        px = _f(_field(row, ("askPrice", "price", "ask", "offerPrice")))
        qty = _f(_field(row, ("askSize", "askQuantity", "quantity", "qty", "size", "volume")))
        if px and qty:
            ask_levels.append((px, qty))
    if not bid_levels or not ask_levels:
        return None

    best_bid = max(px for px, _ in bid_levels)
    best_ask = min(px for px, _ in ask_levels)
    bid_size = sum(qty for _, qty in bid_levels[:5])
    ask_size = sum(qty for _, qty in ask_levels[:5])
    denom = bid_size + ask_size
    imbalance = (bid_size - ask_size) / denom if denom > 0 else None
    mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else None
    spread_bps = ((best_ask - best_bid) / mid * 10000.0) if mid else None
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "imbalance": imbalance,
    }


def parse_last_trade_price(raw: Any) -> float | None:
    rows = _as_rows(raw)
    for row in rows:
        if isinstance(row, dict):
            px = _price(row)
            if px and px > 0:
                return px
    return None


def _read_records(path: Path) -> list[dict[str, Any]]:
    rows = []
    skipped = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # 수집기가 append 도중 죽으면 마지막 줄이 잘릴 수 있다.
                # 그 한 줄 때문에 파일 전체 분석을 포기하지 않는다.
                skipped += 1
    if skipped:
        print(f"warning: {path}: skipped {skipped} corrupt line(s)", file=sys.stderr)
    rows.sort(key=lambda r: str(r.get("ts", "")))
    return rows


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * pct))))
    return vals[idx]


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(vx * vy)


def _forward_pairs(rows: list[dict[str, Any]], horizon_sec: float) -> tuple[list[float], list[float]]:
    enriched = []
    for row in rows:
        parsed = parse_orderbook(row.get("orderbook"))
        if not parsed or parsed.get("mid") is None or parsed.get("imbalance") is None:
            continue
        enriched.append((_ts(str(row["ts"])), parsed["mid"], parsed["imbalance"]))
    xs: list[float] = []
    ys: list[float] = []
    j = 0
    for i, (t0, mid0, imbalance) in enumerate(enriched):
        while j < len(enriched) and (enriched[j][0] - t0).total_seconds() < horizon_sec:
            j += 1
        if j >= len(enriched):
            break
        mid1 = enriched[j][1]
        if mid0 and mid1:
            xs.append(float(imbalance))
            ys.append(mid1 / mid0 - 1.0)
        if j <= i:
            j = i + 1
    return xs, ys


def analyze(path: Path, *, horizon_sec: float) -> dict[str, Any]:
    rows = _read_records(path)
    ok_rows = [r for r in rows if r.get("ok")]
    latencies = [_f(r.get("latency_ms")) for r in rows]
    latencies = [v for v in latencies if v is not None]
    parsed = [parse_orderbook(r.get("orderbook")) for r in rows]
    parsed = [p for p in parsed if p]
    spreads = [p["spread_bps"] for p in parsed if p.get("spread_bps") is not None]
    imbalances, fwd_rets = _forward_pairs(rows, horizon_sec)
    return {
        "path": str(path),
        "records": len(rows),
        "ok_records": len(ok_rows),
        "error_records": len(rows) - len(ok_rows),
        "latency_avg_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "parseable_orderbooks": len(parsed),
        "parseable_ratio": (len(parsed) / len(rows)) if rows else 0.0,
        "spread_avg_bps": (sum(spreads) / len(spreads)) if spreads else None,
        "forward_pairs": len(imbalances),
        "imbalance_forward_corr": _corr(imbalances, fwd_rets),
        "horizon_sec": horizon_sec,
    }


def _write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Microstructure Data Summary",
        "",
        "| File | Records | OK | Errors | Lat avg | Lat p95 | OB parse | Spread avg | Pairs | Imb->Ret corr |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        def fmt(v, suffix=""):
            return "n/a" if v is None else f"{v:.4f}{suffix}" if isinstance(v, float) else str(v)
        lines.append(
            f"| {row['path']} | {row['records']} | {row['ok_records']} | {row['error_records']} | "
            f"{fmt(row['latency_avg_ms'], 'ms')} | {fmt(row['latency_p95_ms'], 'ms')} | "
            f"{row['parseable_ratio']*100:.1f}% | {fmt(row['spread_avg_bps'], 'bps')} | "
            f"{row['forward_pairs']} | {fmt(row['imbalance_forward_corr'])} |"
        )
    lines.extend([
        "",
        "Notes:",
        "- Correlation is descriptive only. It is not a trading signal.",
        "- If orderbook parsing is low, keep collecting raw data and update the parser after inspecting Toss response shape.",
        "- Results must be compared against the 70bps roundtrip cost before any strategy work.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def _paths(root: Path, symbol: str | None, day: str | None) -> list[Path]:
    if symbol and day:
        return [root / day / f"{symbol.upper()}.jsonl"]
    if symbol:
        return sorted(root.glob(f"*/{symbol.upper()}.jsonl"))
    if day:
        return sorted((root / day).glob("*.jsonl"))
    return sorted(root.glob("*/*.jsonl"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize collected microstructure JSONL data.")
    parser.add_argument("--input-dir", default=str(DEFAULT_IN))
    parser.add_argument("--symbol")
    parser.add_argument("--date")
    parser.add_argument("--horizon-sec", type=float, default=300.0)
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()

    paths = [p for p in _paths(Path(args.input_dir), args.symbol, args.date) if p.exists()]
    if not paths:
        raise FileNotFoundError("no microstructure JSONL files found")
    rows = [analyze(path, horizon_sec=args.horizon_sec) for path in paths]
    _write_report(Path(args.report), rows)
    print(f"wrote {args.report} ({len(rows)} file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
