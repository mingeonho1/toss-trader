#!/usr/bin/env python3
"""Forward paper comparison using Toss live prices.

This script never places real orders. It keeps virtual portfolios in
data/forward_paper_state.json, marks them to current Toss prices, and writes a
comparison report after every run.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.broker import PaperBroker              # noqa: E402
from toss_trader.client import TossClient               # noqa: E402
from toss_trader.config import get_settings             # noqa: E402
from toss_trader.costs import CostModel                 # noqa: E402
from toss_trader.marketdata import TossMarketData       # noqa: E402
from toss_trader.models import Candle, Fill, Position   # noqa: E402
from toss_trader.strategy import (BuyAndHoldStrategy, DualMomentumStrategy,  # noqa: E402
                                  RegimeFilterStrategy, SmaCrossStrategy)
from toss_trader.strategy.base import StrategyContext   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "_candle_cache"
STATE_FILE = DATA / "forward_paper_state.json"
LOG_FILE = DATA / "forward_paper.log"
REPORT_FILE = ROOT / "reports" / "forward_paper_latest.md"

BASELINE_WEIGHTS = {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15}
RISK_ASSETS = ["QQQ", "SPY", "EFA", "IWM", "GLD"]
ALL_SYMBOLS = sorted(set(BASELINE_WEIGHTS) | set(RISK_ASSETS) | {"IEF", "BIL"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    line = f"{_now()} {msg}"
    print(line, flush=True)
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.2f}%"


def _fmt_float(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _period_key(d: date, rebalance: str) -> str:
    if rebalance == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if rebalance == "quarter":
        return f"{d.year:04d}-Q{((d.month - 1) // 3) + 1}"
    return d.isoformat()


def _cache_path(symbol: str, depth: int) -> Path:
    return CACHE / f"{symbol}_{depth}.json"


def _load_cached(symbol: str, depth: int) -> list[Candle]:
    paths = [_cache_path(symbol, depth), _cache_path(symbol, 2500), _cache_path(symbol, 1250)]
    for path in paths:
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log(f"corrupt candle cache skipped: {path.name} ({exc})")
            continue
        candles = [
            Candle(symbol, date.fromisoformat(r["d"]), _f(r["o"]), _f(r["h"]),
                   _f(r["l"]), _f(r["c"]), _f(r["v"]))
            for r in rows
        ]
        return candles[-depth:]
    raise FileNotFoundError(f"cached candles not found: {symbol}")


def _save_cached(symbol: str, depth: int, candles: list[Candle]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = [
        {"d": c.dt.isoformat(), "o": c.open, "h": c.high,
         "l": c.low, "c": c.close, "v": c.volume}
        for c in candles
    ]
    path = _cache_path(symbol, depth)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows), encoding="utf-8")
    os.replace(tmp, path)


def _load_history(symbols: list[str], *, depth: int, client: TossClient | None,
                  offline_cache: bool) -> dict[str, list[Candle]]:
    md = TossMarketData(client) if client else None
    panel: dict[str, list[Candle]] = {}
    for sym in symbols:
        if not offline_cache and md is not None:
            try:
                candles = md.history(sym, depth)
                if candles:
                    _save_cached(sym, depth, candles)
                    panel[sym] = candles
                    continue
            except Exception as exc:  # noqa: BLE001
                _log(f"history fetch failed for {sym}, using cache if available: {exc}")
        panel[sym] = _load_cached(sym, depth)
    return panel


def _latest_cache_prices(panel: dict[str, list[Candle]]) -> dict[str, float]:
    return {sym: candles[-1].close for sym, candles in panel.items() if candles}


def _fetch_prices(client: TossClient, symbols: list[str]) -> dict[str, float]:
    raw = client.get_prices(symbols)
    rows = raw if isinstance(raw, list) else raw.get("prices", raw.get("items", []))
    out: dict[str, float] = {}
    for row in rows:
        sym = str(row.get("symbol", "")).upper()
        px = _f(row.get("lastPrice"))
        if sym and px > 0:
            out[sym] = px
    return out


def _attach_account(client: TossClient) -> None:
    if client.s.account_seq:
        return
    accounts = client.get_accounts()
    if isinstance(accounts, list) and accounts:
        client.s = dataclasses.replace(client.s, account_seq=str(accounts[0]["accountSeq"]))


def _account_seed_usd(client: TossClient) -> tuple[float, float, float]:
    _attach_account(client)
    krw = _f(client.get_buying_power("KRW").get("cashBuyingPower"))
    fx = _f(client.get_exchange_rate("USD", "KRW").get("rate"))
    return (krw / fx if fx > 0 else 0.0, krw, fx)


def _market_session(client: TossClient | None, offline_cache: bool) -> dict[str, Any]:
    if offline_cache or client is None:
        return {"session_date": date.today().isoformat(), "regular_market": None}
    try:
        cal = client.get_market_calendar("US")
        today = cal.get("today", {}) if isinstance(cal, dict) else {}
        return {
            "session_date": str(today.get("date") or date.today().isoformat()),
            "regular_market": today.get("regularMarket"),
        }
    except Exception as exc:  # noqa: BLE001
        _log(f"market calendar fetch failed: {exc}")
        return {"session_date": date.today().isoformat(), "regular_market": None}


def _strategy_specs() -> dict[str, dict[str, Any]]:
    return {
        "lumpsum_etf": {
            "label": "Lump-sum ETF baseline QQQ60/SCHD25/GLD15",
            "mode": "buy_once",
            "weights": dict(BASELINE_WEIGHTS),
        },
        "dual_momentum": {
            "label": "Dual momentum top1 -> IEF",
            "mode": "rebalance",
            "signal_rebalance": "month",
            "factory": lambda: DualMomentumStrategy(RISK_ASSETS, "IEF", 252, 1, "month"),
        },
        "regime_200d": {
            "label": "QQQ 200d regime filter -> IEF",
            "mode": "rebalance",
            "signal_rebalance": "month",
            "factory": lambda: RegimeFilterStrategy(
                BuyAndHoldStrategy(["QQQ"]), "QQQ", 200, "IEF", "month"),
        },
        "sma_cross": {
            "label": "SMA 20/60 trend top3",
            "mode": "rebalance",
            "signal_rebalance": "day",
            "factory": lambda: SmaCrossStrategy(RISK_ASSETS, 20, 60, 3),
        },
    }


def _compute_weights(spec: dict[str, Any], panel: dict[str, list[Candle]], today: date) -> dict[str, float]:
    if "weights" in spec:
        return dict(spec["weights"])
    strategy = spec["factory"]()
    history = {sym: [c for c in candles if c.dt <= today] for sym, candles in panel.items()}
    ctx = StrategyContext(today=today, history=history)
    return strategy.target_weights(ctx)


def _target_weights(sid: str, spec: dict[str, Any], pstate: dict[str, Any],
                    panel: dict[str, list[Candle]], today: date) -> dict[str, float]:
    if "weights" in spec:
        return dict(spec["weights"])

    rebalance = str(spec.get("signal_rebalance", "day"))
    period = _period_key(today, rebalance)
    signal = pstate.get("signal", {})
    if signal.get("period") == period and isinstance(signal.get("weights"), dict):
        return {sym: _f(weight) for sym, weight in signal["weights"].items()}

    weights = _compute_weights(spec, panel, today)
    pstate["signal"] = {"period": period, "weights": weights, "asof": today.isoformat()}
    _log(f"signal updated: {sid} period={period} weights={weights or 'cash'}")
    return weights


def _empty_portfolio(seed_usd: float, *, today: date | None = None) -> dict[str, Any]:
    period = _period_key(today, "month") if today else None
    return {
        "cash": seed_usd,
        "positions": {},
        "fills": [],
        "initial_equity": seed_usd,
        "total_contributed": seed_usd,
        "initialized": False,
        "last_contribution_period": period,
    }


def _migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    strategies = state.setdefault("strategies", {})
    if "dca_etf" in strategies and "lumpsum_etf" not in strategies:
        strategies["lumpsum_etf"] = strategies.pop("dca_etf")
    for pstate in strategies.values():
        pstate.setdefault("total_contributed", pstate.get("initial_equity", state.get("seed_usd", 0.0)))
        pstate.setdefault("initialized", bool(pstate.get("fills")))
    return state


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _migrate_state(json.loads(path.read_text(encoding="utf-8")))


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _fill_from_dict(row: dict[str, Any]) -> Fill:
    return Fill(
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        quantity=_f(row["quantity"]),
        price=_f(row["price"]),
        cost=_f(row["cost"]),
        dt=date.fromisoformat(str(row["dt"])),
        realized_pnl=_f(row.get("realized_pnl")),
    )


def _fill_to_dict(fill: Fill, *, ts: str) -> dict[str, Any]:
    return {
        "ts": ts,
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": fill.quantity,
        "price": fill.price,
        "cost": fill.cost,
        "dt": fill.dt.isoformat(),
        "realized_pnl": fill.realized_pnl,
    }


def _broker_from_state(pstate: dict[str, Any], cost: CostModel) -> PaperBroker:
    broker = PaperBroker(cash=_f(pstate.get("cash")), cost_model=cost)
    for sym, row in pstate.get("positions", {}).items():
        qty = _f(row.get("quantity"))
        avg = _f(row.get("avg_price"))
        if qty > 0:
            broker.positions[sym] = Position(sym, qty, avg)
    broker.fills = [_fill_from_dict(row) for row in pstate.get("fills", [])]
    return broker


def _update_portfolio_state(pstate: dict[str, Any], broker: PaperBroker,
                            new_fills: list[Fill], *, ts: str,
                            mark_initialized: bool = True) -> None:
    if abs(broker.cash) < 1e-10:
        broker.cash = 0.0
    fills = list(pstate.get("fills", []))
    fills.extend(_fill_to_dict(fill, ts=ts) for fill in new_fills)
    pstate["cash"] = broker.cash
    pstate["positions"] = {
        sym: {"quantity": pos.quantity, "avg_price": pos.avg_price}
        for sym, pos in broker.positions.items()
        if pos.quantity > 1e-12
    }
    pstate["fills"] = fills
    # stale 가격 등으로 매매가 막힌 실행에서 initialized를 박아버리면
    # buy_once/accumulate의 최초 배치 게이트가 영영 다시 열리지 않는다.
    if mark_initialized:
        pstate["initialized"] = True


def _required_symbols(weights: dict[str, float], broker: PaperBroker) -> set[str]:
    required = {sym for sym, weight in weights.items() if weight > 0}
    required.update(sym for sym, pos in broker.positions.items() if pos.quantity > 1e-12)
    return required


def _stale_block_reason(weights: dict[str, float], broker: PaperBroker,
                        fresh_symbols: set[str]) -> str | None:
    missing = sorted(_required_symbols(weights, broker) - fresh_symbols)
    if missing:
        return "stale price; trades skipped for " + ",".join(missing)
    return None


def _rebalance(broker: PaperBroker, weights: dict[str, float], prices: dict[str, float],
               today: date, *, threshold: float, min_trade_usd: float) -> list[Fill]:
    before = len(broker.fills)
    equity = broker.equity(prices)
    if equity <= 0:
        return []

    targets: dict[str, float] = {}
    for sym, weight in weights.items():
        if sym in prices:
            targets[sym] = max(0.0, weight) * equity
    for sym, pos in broker.positions.items():
        if pos.quantity > 0 and sym in prices and sym not in targets:
            targets[sym] = 0.0

    for sym, target_val in targets.items():
        price = prices.get(sym)
        if not price:
            continue
        cur_val = broker.position(sym).market_value(price)
        diff = target_val - cur_val
        if diff < -threshold * equity and -diff >= min_trade_usd:
            qty = min(broker.position(sym).quantity, (-diff) / price)
            broker.submit_market_order(sym, "SELL", quantity=qty, ref_price=price, dt=today)

    for sym, target_val in targets.items():
        price = prices.get(sym)
        if not price:
            continue
        cur_val = broker.position(sym).market_value(price)
        diff = target_val - cur_val
        amount = min(diff, broker.cash)
        if diff > threshold * equity and amount >= min_trade_usd:
            broker.submit_market_order(sym, "BUY", amount=amount, ref_price=price, dt=today)

    return broker.fills[before:]


def _accumulate(broker: PaperBroker, weights: dict[str, float], prices: dict[str, float],
                today: date, *, min_trade_usd: float) -> list[Fill]:
    before = len(broker.fills)
    cash = max(0.0, broker.cash)
    if cash <= 0:
        return []
    for sym, weight in sorted(weights.items(), key=lambda x: x[1], reverse=True):
        price = prices.get(sym)
        if not price or broker.cash <= 0:
            continue
        amount = min(cash * max(0.0, weight), broker.cash)
        if amount >= min_trade_usd:
            broker.submit_market_order(sym, "BUY", amount=amount, ref_price=price, dt=today)
    return broker.fills[before:]


def _position_text(broker: PaperBroker, prices: dict[str, float]) -> str:
    parts = []
    for sym, pos in sorted(broker.positions.items()):
        if pos.quantity <= 1e-12:
            continue
        px = prices.get(sym, pos.avg_price)
        value = pos.market_value(px)
        ret = (px / pos.avg_price - 1.0) if pos.avg_price > 0 else 0.0
        parts.append(f"{sym} ${value:.2f} ({ret * 100:+.2f}%)")
    return ", ".join(parts) if parts else "cash"


def _daily_records(state: dict[str, Any], sid: str, current: dict[str, Any]) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {}
    for snap in state.get("snapshots", []):
        row = snap.get("strategies", {}).get(sid)
        if not row:
            continue
        by_day[str(snap.get("session_date"))] = {
            "session_date": str(snap.get("session_date")),
            "equity": _f(row.get("equity")),
            "flow": _f(snap.get("flows", {}).get(sid)),
        }
    by_day[str(current["session_date"])] = current
    return [by_day[k] for k in sorted(by_day)]


def _daily_stats(state: dict[str, Any], sid: str, current: dict[str, Any]) -> dict[str, Any]:
    records = _daily_records(state, sid, current)
    if len(records) < 2:
        return {"twr": None, "mdd": None, "sharpe": None, "daily_n": 0}

    index = 1.0
    curve = [index]
    rets: list[float] = []
    for prev, cur in zip(records, records[1:]):
        prev_eq = _f(prev.get("equity"))
        eq = _f(cur.get("equity"))
        flow = _f(cur.get("flow"))
        if prev_eq <= 0:
            continue
        ret = (eq - flow) / prev_eq - 1.0
        rets.append(ret)
        index *= 1.0 + ret
        curve.append(index)

    peak = -math.inf
    mdd = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            mdd = min(mdd, value / peak - 1.0)

    sharpe = None
    if len(rets) >= 20:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        sharpe = (mean / std) * math.sqrt(252) if std > 0 else None
    return {"twr": index - 1.0, "mdd": mdd, "sharpe": sharpe, "daily_n": len(rets)}


def _write_report(path: Path, state: dict[str, Any], prices: dict[str, float],
                  stale_symbols: set[str], rows: list[dict[str, Any]], latest_ts: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Forward Paper Strategy Comparison",
        "",
        f"- Generated: `{latest_ts}`",
        f"- Session date: `{state.get('session_date')}`",
        "- Real orders: none. These are virtual fills using current prices plus the cost model.",
        f"- Cost model: roundtrip `{CostModel().roundtrip_bps:.0f}bps`",
        "- Purpose: operational validation and cost/turnover observation, not statistical ranking.",
        "",
        "## Current Prices",
        "",
        "| Symbol | Price | Freshness |",
        "|---|---:|---|",
    ]
    for sym in sorted(prices):
        freshness = "stale cache" if sym in stale_symbols else "fresh"
        lines.append(f"| {sym} | ${prices[sym]:.2f} | {freshness} |")

    lines.extend([
        "",
        "## Portfolios",
        "",
        "| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ])
    for row in rows:
        target = ", ".join(f"{s}:{w * 100:.0f}%" for s, w in sorted(row["weights"].items())) or "cash"
        lines.append(
            f"| {row['label']} | {row['mode']} | {target} | ${row['equity']:.2f} | "
            f"{_fmt_pct(row['money_return'])} | {_fmt_pct(row['twr'])} | ${row['cash']:.2f} | "
            f"{row['trade_count']} | ${row['total_cost']:.4f} | {_fmt_pct(row['mdd'])} | "
            f"{_fmt_float(row['sharpe'])} | {row['positions']} | {row['note']} |"
        )

    lines.extend(["", "## Latest Virtual Trades", ""])
    any_trade = False
    for row in rows:
        for fill in row["new_fills"]:
            any_trade = True
            lines.append(
                f"- {row['label']}: {fill.side} {fill.symbol} "
                f"{fill.quantity:.6f} @ ${fill.price:.2f} cost ${fill.cost:.4f}"
            )
    if not any_trade:
        lines.append("- No virtual trades on this run.")

    lines.extend([
        "",
        "## Notes",
        "",
        "- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.",
        "- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.",
        "- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.",
        "- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.",
        "- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.",
        "- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_once(args: argparse.Namespace) -> int:
    cost = CostModel()
    client: TossClient | None = None
    if not args.offline_cache:
        client = TossClient(get_settings())

    session = _market_session(client, args.offline_cache)
    today = date.fromisoformat(str(session["session_date"]))
    panel = _load_history(ALL_SYMBOLS, depth=args.history_depth, client=client,
                          offline_cache=args.offline_cache)

    cache_prices = _latest_cache_prices(panel)
    if args.offline_cache:
        prices = cache_prices
        fresh_symbols = set(prices)
    else:
        assert client is not None
        try:
            live_prices = _fetch_prices(client, ALL_SYMBOLS)
        except Exception as exc:  # noqa: BLE001
            _log(f"price fetch failed, marking with cached closes only: {exc}")
            live_prices = {}
        prices = {**cache_prices, **live_prices}
        fresh_symbols = set(live_prices)
    stale_symbols = set(prices) - fresh_symbols

    state_path = Path(args.state)
    state = None if args.reset else _load_state(state_path)

    if state is None:
        if args.cash_usd is not None:
            seed_usd = float(args.cash_usd)
            krw = 0.0
            fx = 0.0
        elif args.offline_cache:
            raise RuntimeError("--offline-cache initial run requires --cash-usd")
        else:
            assert client is not None
            seed_usd, krw, fx = _account_seed_usd(client)
        specs = _strategy_specs()
        state = {
            "version": 2,
            "created_at": _now(),
            "session_date": session["session_date"],
            "regular_market": session.get("regular_market"),
            "seed_usd": seed_usd,
            "seed_krw": krw,
            "fx": fx,
            "strategies": {sid: _empty_portfolio(seed_usd, today=today) for sid in specs},
            "snapshots": [],
        }
        _log(f"initialized forward paper state: seed ${seed_usd:.2f}")
    else:
        state["session_date"] = session["session_date"]
        state["regular_market"] = session.get("regular_market")

    ts = _now()
    specs = _strategy_specs()
    rows: list[dict[str, Any]] = []
    snapshot_flows: dict[str, float] = {}
    snapshot_rows: dict[str, dict[str, Any]] = {}

    for sid, spec in specs.items():
        pstate = state["strategies"].setdefault(
            sid, _empty_portfolio(_f(state.get("seed_usd")), today=today))
        broker = _broker_from_state(pstate, cost)
        weights = _target_weights(sid, spec, pstate, panel, today)
        note = ""
        flow = 0.0
        new_fills: list[Fill] = []

        if spec["mode"] == "accumulate":
            period = _period_key(today, "month")
            if pstate.get("initialized") and args.monthly_usd > 0:
                if pstate.get("last_contribution_period") != period:
                    broker.cash += args.monthly_usd
                    pstate["total_contributed"] = _f(pstate.get("total_contributed")) + args.monthly_usd
                    pstate["last_contribution_period"] = period
                    flow = args.monthly_usd
                    note = f"monthly contribution ${args.monthly_usd:.2f}"
            else:
                pstate["last_contribution_period"] = period

        block_reason = _stale_block_reason(weights, broker, fresh_symbols)
        if block_reason:
            note = f"{note}; {block_reason}" if note else block_reason
        elif spec["mode"] == "buy_once":
            if not pstate.get("initialized"):
                new_fills = _rebalance(
                    broker, weights, prices, today,
                    threshold=0.0, min_trade_usd=args.min_trade_usd)
        elif spec["mode"] == "accumulate":
            if (not pstate.get("initialized")) or flow > 0 or broker.cash >= args.min_trade_usd:
                new_fills = _accumulate(
                    broker, weights, prices, today, min_trade_usd=args.min_trade_usd)
        else:
            new_fills = _rebalance(
                broker, weights, prices, today,
                threshold=args.rebalance_threshold, min_trade_usd=args.min_trade_usd)

        _update_portfolio_state(pstate, broker, new_fills, ts=ts,
                                mark_initialized=not block_reason)
        pstate.setdefault("total_contributed", pstate.get("initial_equity", state.get("seed_usd", 0.0)))
        equity = broker.equity(prices)
        contributed = _f(pstate.get("total_contributed")) or _f(pstate.get("initial_equity"))
        money_return = (equity / contributed - 1.0) if contributed > 0 else None
        current_record = {"session_date": session["session_date"], "equity": equity, "flow": flow}
        stats = _daily_stats(state, sid, current_record)
        row = {
            "id": sid,
            "label": spec["label"],
            "mode": spec["mode"],
            "weights": weights,
            "equity": equity,
            "money_return": money_return,
            "twr": stats["twr"],
            "cash": broker.cash,
            "trade_count": len(broker.fills),
            "total_cost": sum(f.cost for f in broker.fills),
            "positions": _position_text(broker, prices),
            "new_fills": new_fills,
            "mdd": stats["mdd"],
            "sharpe": stats["sharpe"],
            "note": note,
        }
        rows.append(row)
        snapshot_flows[sid] = flow
        snapshot_rows[sid] = {
            "equity": equity,
            "money_return": money_return,
            "cash": broker.cash,
            "weights": weights,
            "total_contributed": contributed,
        }

    state.setdefault("snapshots", []).append({
        "ts": ts,
        "session_date": session["session_date"],
        "regular_market": session.get("regular_market"),
        "prices": {sym: prices[sym] for sym in sorted(prices)},
        "stale_symbols": sorted(stale_symbols),
        "flows": snapshot_flows,
        "strategies": snapshot_rows,
    })
    state["snapshots"] = state["snapshots"][-2000:]

    _save_state(state_path, state)
    _write_report(Path(args.report), state, prices, stale_symbols, rows, ts)

    _log(f"paper updated: strategies={len(rows)} stale={sorted(stale_symbols)} report={args.report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare DCA vs active quant strategies in a live-price paper ledger.")
    parser.add_argument("--watch", action="store_true",
                        help="run repeatedly until interrupted")
    parser.add_argument("--interval-sec", type=int, default=300,
                        help="watch interval in seconds")
    parser.add_argument("--reset", action="store_true",
                        help="start a new paper comparison state")
    parser.add_argument("--cash-usd", type=float,
                        help="initial virtual cash; otherwise uses KRW buying power / FX")
    parser.add_argument("--monthly-usd", type=float, default=0.0,
                        help="monthly virtual contribution for accumulate strategies")
    parser.add_argument("--state", default=str(STATE_FILE),
                        help="state JSON path")
    parser.add_argument("--report", default=str(REPORT_FILE),
                        help="markdown report path")
    parser.add_argument("--history-depth", type=int, default=320,
                        help="daily candles used for signals")
    parser.add_argument("--rebalance-threshold", type=float, default=0.05,
                        help="skip active trades below this fraction of equity")
    parser.add_argument("--min-trade-usd", type=float, default=1.0,
                        help="skip virtual trades smaller than this USD amount")
    parser.add_argument("--offline-cache", action="store_true",
                        help="use cached candle closes instead of Toss API, for local verification")
    args = parser.parse_args()

    if args.monthly_usd < 0:
        raise ValueError("--monthly-usd must be >= 0")

    if not args.watch:
        return run_once(args)

    while True:
        try:
            run_once(args)
            args.reset = False
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            _log(f"paper update failed: {exc}")
        time.sleep(max(5, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
