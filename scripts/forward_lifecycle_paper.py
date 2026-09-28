#!/usr/bin/env python3
"""포워드 페이퍼: 라이프사이클 슬리브(QQQ/QLD) vs DCA-QQQ 를 오늘부터 누적 비교.

**실주문을 절대 내지 않는다.** 두 가상 포트폴리오를 data/forward_lifecycle_state.json 에
두고, 실현재가(자격증명이 있으면)로 마크투마켓하며, 매 실행 후 리포트를 쓴다. 자격증명이
없거나 --offline-cache 면 data/_hist_cache/{SYM}.json(Nasdaq 일봉) 마지막 종가를 쓴다.

목적: 라이프사이클 정책(src/toss_trader/policy_lifecycle.py)의 증거를 점증 축적하는 것.
채택은 미결정이며, 이 스크립트는 관측만 한다.

사용:
  PYTHONPATH=src python scripts/forward_lifecycle_paper.py --reset --cash-usd 100 --monthly-usd 35
  PYTHONPATH=src python scripts/forward_lifecycle_paper.py --offline-cache --reset --cash-usd 100
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from toss_trader import policy_lifecycle as pl          # noqa: E402
from toss_trader.broker import PaperBroker              # noqa: E402
from toss_trader.config import get_settings             # noqa: E402
from toss_trader.costs import CostModel                 # noqa: E402
from toss_trader.models import Fill, Position           # noqa: E402

DATA = ROOT / "data"
HIST = DATA / "_hist_cache"
STATE_FILE = DATA / "forward_lifecycle_state.json"
LOG_FILE = DATA / "forward_lifecycle.log"
REPORT_FILE = ROOT / "reports" / "forward_lifecycle_latest.md"

SYMBOLS = [pl.QQQ, pl.QLD]
LIFECYCLE_BAND = 0.3
PORTFOLIOS = ("lifecycle_sleeve", "dca_qqq")


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


def _period(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ── 가격 ─────────────────────────────────────────────────────────────────────
def _cached_prices(syms: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in syms:
        p = HIST / f"{s}.json"
        if not p.exists():
            continue
        try:
            rows = (json.loads(p.read_text(encoding="utf-8")).get("rows")) or []
        except (OSError, json.JSONDecodeError):
            continue
        if rows:
            px = _f(rows[-1].get("c"))
            if px > 0:
                out[s] = px
    return out


def _live_prices(client, syms: list[str]) -> dict[str, float]:
    raw = client.get_prices(syms)
    rows = raw if isinstance(raw, list) else raw.get("prices", raw.get("items", []))
    out: dict[str, float] = {}
    for row in rows or []:
        sym = str(row.get("symbol", "")).upper()
        px = _f(row.get("lastPrice"))
        if sym and px > 0:
            out[sym] = px
    return out


# ── 상태 (직렬화/역직렬화) ───────────────────────────────────────────────────
def _empty_pf(seed: float, period: str) -> dict[str, Any]:
    return {"cash": seed, "positions": {}, "fills": [], "total_contributed": seed,
            "last_contribution_period": period, "initialized": False}


def _broker_from_pf(pf: dict[str, Any], cost: CostModel) -> PaperBroker:
    b = PaperBroker(cash=_f(pf.get("cash")), cost_model=cost)
    for sym, row in pf.get("positions", {}).items():
        qty = _f(row.get("quantity"))
        if qty > 0:
            b.positions[sym] = Position(sym, qty, _f(row.get("avg_price")))
    b.fills = [Fill(str(r["symbol"]), str(r["side"]), _f(r["quantity"]), _f(r["price"]),
                    _f(r["cost"]), date.fromisoformat(str(r["dt"])), _f(r.get("realized_pnl")))
               for r in pf.get("fills", [])]
    return b


def _pf_from_broker(pf: dict[str, Any], b: PaperBroker) -> None:
    if abs(b.cash) < 1e-10:
        b.cash = 0.0
    pf["cash"] = b.cash
    pf["positions"] = {sym: {"quantity": p.quantity, "avg_price": p.avg_price}
                       for sym, p in b.positions.items() if p.quantity > 1e-12}
    pf["fills"] = [{"symbol": f.symbol, "side": f.side, "quantity": f.quantity,
                    "price": f.price, "cost": f.cost, "dt": f.dt.isoformat(),
                    "realized_pnl": f.realized_pnl} for f in b.fills]


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _months_remaining(state: dict[str, Any], plan_years: int, today: date) -> int:
    start = state.get("plan_start_month") or _period(today)
    try:
        sy, sm = (int(x) for x in str(start).split("-")[:2])
        elapsed = max(0, (today.year - sy) * 12 + (today.month - sm))
    except (ValueError, TypeError):
        elapsed = 0
    return max(0, plan_years * 12 - elapsed)


# ── 투자 스텝(가상 체결) ─────────────────────────────────────────────────────
def _step_lifecycle(b: PaperBroker, prices: dict[str, float], today: date, *,
                    months_remaining: int, monthly: float, e_max: float,
                    min_trade: float) -> tuple[list[Fill], float | None, str]:
    qqq_px, qld_px = prices.get(pl.QQQ), prices.get(pl.QLD)
    if not qqq_px or not qld_px:
        return [], None, "stale price (QQQ/QLD 필요)"
    holdings = {pl.QQQ: b.position(pl.QQQ).market_value(qqq_px),
                pl.QLD: b.position(pl.QLD).market_value(qld_px)}
    W = holdings[pl.QQQ] + holdings[pl.QLD]
    E = pl.lifecycle_target(W, monthly, months_remaining, e_max=e_max)
    dep = pl.deposit_plan(holdings, b.cash, E, band=LIFECYCLE_BAND)
    before = len(b.fills)
    for sym, usd in dep.sells.items():           # 디레버리지 매도(페이퍼에선 실제 수행)
        px = prices[sym]
        if px > 0:
            b.submit_market_order(sym, "SELL", quantity=usd / px, ref_price=px, dt=today)
    for sym, usd in dep.buys.items():
        if usd >= min_trade:
            b.submit_market_order(sym, "BUY", amount=usd, ref_price=prices[sym], dt=today)
    return b.fills[before:], E, ""


def _step_dca_qqq(b: PaperBroker, prices: dict[str, float], today: date, *,
                  min_trade: float) -> tuple[list[Fill], str]:
    qqq_px = prices.get(pl.QQQ)
    if not qqq_px:
        return [], "stale price (QQQ 필요)"
    before = len(b.fills)
    if b.cash >= min_trade:
        b.submit_market_order(pl.QQQ, "BUY", amount=b.cash, ref_price=qqq_px, dt=today)
    return b.fills[before:], ""


# ── 리포트 ───────────────────────────────────────────────────────────────────
def _write_report(path: Path, state: dict[str, Any], prices: dict[str, float],
                  rows: list[dict[str, Any]], ts: str, offline: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    L = [
        "# Forward Lifecycle Paper (sleeve vs DCA-QQQ)",
        "",
        f"- Generated: `{ts}`",
        f"- Price source: `{'cached Nasdaq daily close' if offline else 'live Toss'}`",
        "- Real orders: none. Virtual fills using current prices + cost model.",
        f"- Plan start: `{state.get('plan_start_month')}` · months remaining: "
        f"`{state.get('months_remaining')}`",
        "- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.",
        "",
        "## Prices",
        "",
        "| Symbol | Price |",
        "|---|---:|",
    ]
    for s in SYMBOLS:
        L.append(f"| {s} | ${prices.get(s, 0.0):.2f} |")
    L += ["", "## Portfolios", "",
          "| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |",
          "|---|---:|---:|---:|---:|---:|---|---|"]
    for r in rows:
        E = "n/a" if r["E"] is None else f"{r['E']:.3f}"
        mr = "n/a" if r["money_return"] is None else f"{r['money_return'] * 100:+.2f}%"
        L.append(f"| {r['id']} | ${r['equity']:.2f} | ${r['contributed']:.2f} | {mr} | {E} | "
                 f"${r['cash']:.2f} | {r['positions']} | {r['note']} |")
    L += ["", "## Notes", "",
          "- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.",
          "- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).",
          "- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.", ""]
    path.write_text("\n".join(L), encoding="utf-8")


# ── 러너 ─────────────────────────────────────────────────────────────────────
def run_once(args: argparse.Namespace) -> int:
    cost = CostModel()
    client = None
    prices: dict[str, float]
    if args.offline_cache:
        prices = _cached_prices(SYMBOLS)
    else:
        try:
            client = _make_client()
            prices = _live_prices(client, SYMBOLS)
        except Exception as exc:  # noqa: BLE001
            _log(f"live price fetch failed → cached fallback: {exc}")
            prices = {}
        for s, px in _cached_prices(SYMBOLS).items():
            prices.setdefault(s, px)

    today = date.today()
    state = None if args.reset else _load_state(Path(args.state))
    if state is None:
        seed = _seed_usd(args, client)
        state = {"created_at": _now(), "plan_start_month": _period(today),
                 "seed_usd": seed, "snapshots": [],
                 "portfolios": {p: _empty_pf(seed, _period(today)) for p in PORTFOLIOS}}
        _log(f"initialized forward lifecycle state: seed ${seed:.2f}")

    months_remaining = _months_remaining(state, args.plan_years, today)
    state["months_remaining"] = months_remaining
    ts = _now()
    rows: list[dict[str, Any]] = []
    period = _period(today)

    for pid in PORTFOLIOS:
        pf = state["portfolios"].setdefault(pid, _empty_pf(_f(state.get("seed_usd")), period))
        b = _broker_from_pf(pf, cost)
        note = ""
        # 월 적립(초기화 이후, 새로운 달)
        if pf.get("initialized") and args.monthly_usd > 0 and pf.get("last_contribution_period") != period:
            b.cash += args.monthly_usd
            pf["total_contributed"] = _f(pf.get("total_contributed")) + args.monthly_usd
            pf["last_contribution_period"] = period
            note = f"contribution ${args.monthly_usd:.2f}"
        elif not pf.get("initialized"):
            pf["last_contribution_period"] = period

        E = None
        if pid == "lifecycle_sleeve":
            fills, E, blk = _step_lifecycle(
                b, prices, today, months_remaining=months_remaining,
                monthly=args.monthly_usd or (b.cash if not pf.get("initialized") else 0.0),
                e_max=args.emax, min_trade=args.min_trade_usd)
        else:
            fills, blk = _step_dca_qqq(b, prices, today, min_trade=args.min_trade_usd)
        if blk:
            note = f"{note}; {blk}" if note else blk
        else:
            pf["initialized"] = True

        _pf_from_broker(pf, b)
        equity = b.equity(prices)
        contributed = _f(pf.get("total_contributed"))
        rows.append({
            "id": pid, "equity": equity, "contributed": contributed,
            "money_return": (equity / contributed - 1.0) if contributed > 0 else None,
            "E": E, "cash": b.cash,
            "positions": ", ".join(
                f"{s}:{p.quantity:.4f}" for s, p in sorted(b.positions.items())
                if p.quantity > 1e-12) or "cash",
            "note": note,
        })

    snap = {
        "ts": ts, "date": today.isoformat(), "prices": dict(prices),
        "portfolios": {r["id"]: {"equity": r["equity"], "contributed": r["contributed"],
                                 "E": r["E"]} for r in rows}}
    snaps = state.setdefault("snapshots", [])
    if args.offline_cache:
        # 크리덴셜 없는 경로: 세션일 1개 = 스냅샷 1개(멱등). 같은 세션일 재실행은 교체.
        snaps = [s for s in snaps if str(s.get("date")) != str(snap["date"])]
        snaps.append(snap)
        snaps.sort(key=lambda s: str(s.get("date")))
    else:
        snaps.append(snap)
    state["snapshots"] = snaps[-2000:]
    _save_state(Path(args.state), state)
    _write_report(Path(args.report), state, prices, rows, ts, args.offline_cache or client is None)
    _log(f"lifecycle paper updated: prices={ {s: round(prices.get(s, 0.0), 2) for s in SYMBOLS} } "
         f"report={args.report}")
    return 0


def _make_client():
    from toss_trader.client import TossClient
    import dataclasses
    s = get_settings()
    s.require_credentials()
    client = TossClient(s)
    if not s.account_seq:
        accounts = client.get_accounts()
        if isinstance(accounts, list) and accounts:
            client.s = dataclasses.replace(client.s, account_seq=str(accounts[0]["accountSeq"]))
    return client


def _seed_usd(args: argparse.Namespace, client) -> float:
    if args.cash_usd is not None:
        return float(args.cash_usd)
    if client is None:
        raise RuntimeError("초기 실행에는 --cash-usd 가 필요합니다(offline/자격증명 없음).")
    krw = _f(client.get_buying_power("KRW").get("cashBuyingPower"))
    fx = _f(client.get_exchange_rate("USD", "KRW").get("rate"))
    return krw / fx if fx > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Forward paper: lifecycle sleeve (QQQ/QLD) vs DCA-QQQ. No real orders.")
    ap.add_argument("--watch", action="store_true", help="반복 실행(Ctrl-C까지)")
    ap.add_argument("--interval-sec", type=int, default=300)
    ap.add_argument("--reset", action="store_true", help="새 페이퍼 상태로 시작")
    ap.add_argument("--cash-usd", type=float, help="초기 가상 시드(미지정 시 계좌 매수가능/FX)")
    ap.add_argument("--monthly-usd", type=float, default=35.0, help="월 가상 적립액")
    ap.add_argument("--plan-years", type=int, default=25, help="생애 적립 계획(년)")
    ap.add_argument("--emax", type=float, default=2.0, help="슬리브 목표노출 상한")
    ap.add_argument("--min-trade-usd", type=float, default=1.0)
    ap.add_argument("--state", default=str(STATE_FILE))
    ap.add_argument("--report", default=str(REPORT_FILE))
    ap.add_argument("--offline-cache", action="store_true",
                    help="Toss API 대신 캐시된 Nasdaq 일봉 종가 사용(로컬 검증)")
    args = ap.parse_args()
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
            _log(f"lifecycle paper update failed: {exc}")
        time.sleep(max(5, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
