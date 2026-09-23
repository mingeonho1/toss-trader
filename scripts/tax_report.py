#!/usr/bin/env python3
"""해외주식 양도세 리포트 (dry-run 전용, 주문 없음).

현재 연도의 **실현손익(원장)**·**미실현손익(원화)**을 보여주고 연말 하베스팅 플랜을 추천한다.
`scripts/run_dca.py --tax-report` 가 이 모듈의 run()을 호출한다(독립 실행도 가능).

⚠️ 원화 원가(cost basis)의 정밀도:
- 토스 holdings는 averagePurchasePrice(USD 평단)만 주고 **취득일 환율을 주지 않으며**,
  실현손익 엔드포인트도 없다. 그래서 라이브 모드의 원화 원가는 **현재 환율로 근사**한다
  (= 환차익 미반영 → 과세이익을 과소평가). 정확한 계산은 FX가 찍힌 매수내역(로트)이 필요.
- 정확 모드: `--fixture PATH`(오프라인) 또는 향후 FX-스탬프 로트 영속화로 대체 가능.

실현손익은 우리 원장(data/tax_ledger.json)만 근거로 한다(브로커는 실현손익 API 없음).
본 출력은 참고용이며 확정 세액이 아니다(신고 전 확인).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.tax import (                          # noqa: E402
    BASIC_DEDUCTION_KRW,
    OVERSEAS_CG_RATE,
    Holding,
    TaxLedger,
    annual_tax,
    harvest_plan,
)

DEFAULT_COMMISSION_RATE = 0.0025   # 하베스팅 편도 수수료(환전 없음 → 수수료만)


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def make_fee_fn(commission_rate: float):
    return lambda notional_usd: abs(notional_usd) * commission_rate


def build_report_lines(*, holdings: list[Holding], fx_now: float,
                       realized_ytd_krw: float, year: int, today: date,
                       fee_fn, approximate: bool,
                       safety_factor: float = 1.5) -> list[str]:
    """순수 렌더링: 실현/미실현 + 하베스팅 플랜 → 텍스트 라인 목록(테스트 가능)."""
    out: list[str] = []
    out.append(f"═══ 해외주식 양도세 리포트 {year} (dry-run, 주문 없음) ═══")
    tax_ytd = annual_tax(realized_ytd_krw)
    room = max(0.0, BASIC_DEDUCTION_KRW - realized_ytd_krw)
    out.append(f"실현손익(YTD, 통산) : ₩{_fmt(realized_ytd_krw)}  "
               f"→ 예상세액 ₩{_fmt(tax_ytd)} (공제 ₩{_fmt(BASIC_DEDUCTION_KRW)} 반영)")
    out.append(f"남은 기본공제        : ₩{_fmt(room)}")
    out.append("")
    out.append("보유 미실현손익(원화, 현재 환율 %s):" % _fmt(fx_now))
    if approximate:
        out.append("  ⚠️ 원화 원가는 현재 환율 근사(취득일 환율 미제공) → 환차익 미반영, 과세이익 과소평가.")
    total_unreal = 0.0
    for h in sorted(holdings, key=lambda x: x.unrealized_krw(fx_now), reverse=True):
        u = h.unrealized_krw(fx_now)
        total_unreal += u
        out.append(f"  {h.symbol:<6} {h.quantity:>10.4f}주 @ ${h.price_usd:>8.2f}  "
                   f"평가 ₩{_fmt(h.market_value_krw(fx_now)):>14}  "
                   f"미실현 ₩{_fmt(u):>14}")
    out.append(f"  {'합계':<6} {'':>10}      {'':>10}  "
               f"미실현 ₩{_fmt(total_unreal)}")
    out.append("")
    plan = harvest_plan(holdings, fx_now, realized_ytd_krw, fee_fn=fee_fn,
                        safety_factor=safety_factor, today=today)
    out.append("하베스팅 플랜:")
    out.append("  " + plan.summary())
    for a in plan.actions:
        kind = "익절→재매수" if a.kind == "gain_harvest" else "손절→재매수"
        out.append(f"    · {a.symbol} {kind}: 매도 {a.sell_quantity:.4f}주 즉시 재매수 "
                   f"(@${a.price_usd:.2f}) | 실현 ₩{_fmt(a.realized_krw)} | "
                   f"왕복비용 ₩{_fmt(a.fee_krw)}")
    if plan.recommended:
        out.append("  → 이 주문들은 자동 실행되지 않습니다(dry-run). 직접 정규장에 실행하세요.")
    out.append("")
    out.append("※ 참고용, 확정 세액 아님. 워시세일 없음(즉시 재매수 가능). 손실은 연내 통산만(이월 불가).")
    return out


def _holdings_from_client(items, fx_now) -> list[Holding]:
    """토스 holdings items → Holding(원화 원가 = 수량×USD평단×현재환율, 근사)."""
    out: list[Holding] = []
    for it in items or []:
        sym = it.get("symbol")
        qty = float(it.get("quantity", 0) or 0)
        px = float(it.get("lastPrice", 0) or 0)
        avg = float(it.get("averagePurchasePrice", 0) or 0)
        if not sym or qty <= 0:
            continue
        cost_basis_krw = qty * avg * fx_now      # ⚠️ 취득일 환율 미제공 → 현재 환율 근사
        out.append(Holding(sym, qty, px, cost_basis_krw))
    return out


def _load_fixture(path: Path):
    """오프라인 정확 모드: {fx_now, realized_ytd_krw, holdings:[{symbol,quantity,price_usd,cost_basis_krw}]}."""
    d = json.loads(path.read_text(encoding="utf-8"))
    holdings = [Holding(h["symbol"], float(h["quantity"]), float(h["price_usd"]),
                        float(h["cost_basis_krw"])) for h in d.get("holdings", [])]
    return holdings, float(d["fx_now"]), float(d.get("realized_ytd_krw", 0.0))


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description="해외주식 양도세 리포트(dry-run)")
    ap.add_argument("--fixture", help="오프라인 정확 모드 JSON(취득원가 원화 포함)")
    ap.add_argument("--ledger", help="세금 원장 경로(기본 data/tax_ledger.json)")
    ap.add_argument("--safety", type=float, default=1.5, help="하베스팅 수수료 안전계수")
    ap.add_argument("--today", help="기준일 YYYY-MM-DD(12월 판정용; 기본 오늘)")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    fee_fn = make_fee_fn(DEFAULT_COMMISSION_RATE)

    if args.fixture:                              # 오프라인 정확 모드
        holdings, fx_now, realized_ytd = _load_fixture(Path(args.fixture))
        approximate = False
    else:                                         # 라이브(토스) 모드
        from toss_trader.client import TossClient        # noqa: E402
        from toss_trader.config import get_settings      # noqa: E402
        from toss_trader.costs import CostModel          # noqa: E402
        s = get_settings()
        s.require_credentials()
        client = TossClient(s)
        if not client.s.account_seq:
            accts = client.get_accounts()
            if isinstance(accts, list) and accts:
                import dataclasses
                client.s = dataclasses.replace(client.s,
                                               account_seq=str(accts[0]["accountSeq"]))
        fx_now = float(client.get_exchange_rate("USD", "KRW").get("rate", 0) or 0)
        holdings_raw = client.get_holdings()
        items = holdings_raw.get("items", []) if isinstance(holdings_raw, dict) else []
        holdings = _holdings_from_client(items, fx_now)
        # 실 수수료율 반영(하베스팅 왕복비용).
        try:
            cm = CostModel.from_commissions(client.get_commissions(), market="US")
            fee_fn = make_fee_fn(cm.commission_bps * 1e-4)
        except Exception:  # noqa: BLE001  수수료 조회 실패 시 기본율
            pass
        ledger = TaxLedger(path=Path(args.ledger)) if args.ledger else TaxLedger()
        realized_ytd = ledger.realized_ytd(today.year)
        approximate = True

    lines = build_report_lines(
        holdings=holdings, fx_now=fx_now, realized_ytd_krw=realized_ytd,
        year=today.year, today=today, fee_fn=fee_fn, approximate=approximate,
        safety_factor=args.safety)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
