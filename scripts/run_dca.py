#!/usr/bin/env python3
"""기본 운용 = 적립식(DCA) + 분산 바이앤홀드.

게이트 검증 결론(reports/strategy_gate_2026-06-28.md): 이 시드·비용에선 액티브 타이밍이
B&H를 못 이긴다. 그래서 기본 전략을 '분산 바스켓을 매월 적립 매수 후 보유(매도 최소)'로 확정.

사용:
  python scripts/run_dca.py --backtest        # 분산안 과거 검증(키 불필요, 캐시 사용)
  python scripts/run_dca.py                    # 실계좌 적립 매수 '플랜'만 출력(dry-run, 주문 없음)
  python scripts/run_dca.py --execute          # 실주문(정규장에서만). TRADING_MODE=live 필요.

설계: 신규 현금(입금액)으로 target 비중 미달분을 매수만 한다(매도 없음→저회전).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.backtest import run_dca               # noqa: E402
from toss_trader.client import TossClient              # noqa: E402
from toss_trader.config import get_settings            # noqa: E402
from toss_trader.costs import CostModel                # noqa: E402
from toss_trader.marketdata import TossMarketData      # noqa: E402
from toss_trader.models import Candle                  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

# ── 확정된 기본 분산 배분 ──────────────────────────────────────────────
# QQQ 코어(성장) + SCHD(배당·저베타 분산) + GLD(위기 비상관 헤지).
# 게이트 검증상 QQQ 집중이 수익은 최고였으나 MDD −35%+. 분산으로 낙폭을 낮추되
# 성장 노출은 유지. 합 = 1.0.
DEFAULT_ALLOCATION = {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15}

CACHE = Path(__file__).resolve().parent.parent / "data" / "_candle_cache"
ALLOC_CANDIDATES = {
    "QQQ 100%": {"QQQ": 1.0},
    "QQQ60/SCHD25/GLD15 (기본)": {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15},
    "분산5 QQQ40/SPY20/SCHD20/GLD10/EFA10": {"QQQ": 0.40, "SPY": 0.20, "SCHD": 0.20, "GLD": 0.10, "EFA": 0.10},
}


def _load_cached(sym: str, depth: int = 2500) -> list[Candle]:
    f = CACHE / f"{sym}_{depth}.json"
    if not f.exists():
        f = CACHE / f"{sym}_1250.json"
    rows = json.loads(f.read_text())
    return [Candle(sym, date.fromisoformat(r["d"]), r["o"], r["h"], r["l"], r["c"], r["v"]) for r in rows]


def backtest_mode() -> int:
    fx = 1541.6
    monthly = 50_000.0 / fx   # ₩5만/월 가정
    cost = CostModel()
    syms = sorted({s for w in ALLOC_CANDIDATES.values() for s in w})
    panel = {s: _load_cached(s) for s in syms}
    start = max(c[0].dt for c in panel.values())
    panel = {s: [c for c in cs if c.dt >= start] for s, cs in panel.items()}
    print(f"적립식 검증 | 월 ${monthly:.2f}(₩50,000) | 비용 {cost.roundtrip_bps:.0f}bps | "
          f"{start}~{panel[syms[0]][-1].dt}\n")
    for name, w in ALLOC_CANDIDATES.items():
        res = run_dca(panel, w, monthly_usd=monthly, cost_model=cost)
        print(f"  [{name}]\n     {res.summary()}")
    print("\n→ 기본 배분 확정: QQQ60/SCHD25/GLD15 (성장 노출 유지 + 분산으로 낙폭 완화).")
    print("  (QQQ100은 수익 최고지만 낙폭 최대. 곧 쓸 돈이 아니면 QQQ100도 합리적 — 취향/위험감내에 따라.)")
    return 0


def live_plan(execute: bool) -> int:
    s = get_settings()
    s.require_credentials()
    client = TossClient(s)
    accounts = client.get_accounts()
    if not s.account_seq and isinstance(accounts, list) and accounts:
        import dataclasses
        client.s = dataclasses.replace(client.s, account_seq=str(accounts[0]["accountSeq"]))
    if not client.s.account_seq:
        print("❌ accountSeq를 확인할 수 없습니다 (.env ACCOUNT_SEQ).")
        return 1

    krw = float(client.get_buying_power("KRW").get("cashBuyingPower", 0) or 0)
    er = client.get_exchange_rate("USD", "KRW")
    fx = float(er.get("rate", 0) or 0)
    avail_usd = krw / fx if fx else 0.0
    holdings = client.get_holdings()
    held = {it["symbol"]: float(it.get("quantity", 0) or 0) * float(it.get("lastPrice", 0) or 0)
            for it in (holdings.get("items", []) if isinstance(holdings, dict) else [])}
    held_total = sum(held.values())
    print(f"계좌 {client.s.account_seq} | 매수가능 ₩{krw:,.0f} (= ${avail_usd:.2f} @ {fx}) | "
          f"보유평가 ${held_total:.2f}")

    # 목표: (보유 + 가용)을 target 비중으로. 미달분을 가용현금으로 매수만.
    total_after = held_total + avail_usd
    print(f"\n적립 매수 플랜 (목표배분 {DEFAULT_ALLOCATION}):")
    plan = []
    remaining = avail_usd
    targets = sorted(DEFAULT_ALLOCATION, key=lambda x: DEFAULT_ALLOCATION[x] * total_after - held.get(x, 0), reverse=True)
    for sym in targets:
        target_val = DEFAULT_ALLOCATION[sym] * total_after
        need = max(0.0, target_val - held.get(sym, 0.0))
        buy = min(need, remaining)
        if buy >= 1.0:   # 1달러 미만 조각 매수는 생략
            plan.append((sym, round(buy, 2)))
            remaining -= buy
    if not plan:
        print("  (매수할 미달분 없음 또는 가용현금 부족)")
        return 0
    for sym, amt in plan:
        print(f"  • {sym}: ${amt:.2f} 시장가 매수 (US MARKET, 정규장)")

    if not execute:
        print("\nℹ️ dry-run입니다. 실제 주문하려면 --execute (TRADING_MODE=live 필요, 정규장에서만).")
        return 0

    if not client.s.is_live:
        print("\n❌ --execute에는 TRADING_MODE=live 필요. 지금은 paper라 주문하지 않습니다.")
        return 1
    cal = client.get_market_calendar("US")
    reg = (cal.get("today", {}) or {}).get("regularMarket")
    if not reg:
        print("\n❌ 오늘은 미국 정규장이 아닙니다(금액주문은 정규장 전용). 주문하지 않습니다.")
        return 1
    print("\n⚠️ 실주문 실행:")
    for sym, amt in plan:
        try:
            resp = client.create_order(sym, "BUY", order_type="MARKET", order_amount=f"{amt:.2f}")
            print(f"  ✅ {sym} ${amt:.2f} 주문 접수: orderId={resp.get('orderId')}")
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {sym} 주문 실패: {e}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true", help="분산안 과거 검증")
    ap.add_argument("--execute", action="store_true", help="실주문 실행(정규장·live 필요)")
    args = ap.parse_args()
    if args.backtest:
        return backtest_mode()
    return live_plan(args.execute)


if __name__ == "__main__":
    raise SystemExit(main())
