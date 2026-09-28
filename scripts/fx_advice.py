#!/usr/bin/env python3
"""환전(FX) 최저비용 가이드 — 언제 KRW→USD 환전하면 싼지 + (가능하면) 현재 표시 스프레드.

**읽기 전용**: 주문을 내지 않는다. 자격증명이 있으면 /exchange-rate(참고 환율)를 조회해
표시 스프레드(basisPoint)를 보여주고, 없거나 실패해도 시간대 가이드는 **항상** 출력한다.

핵심(검증·공식, 2026-04-22 시행): 환전 수수료는 매매기준환율 스프레드 1% 기준으로,
국내 영업일 09:00–15:30 KST 는 95% 우대(≈0.05%), 그 외/주말/공휴일은 50% 우대(≈0.5%).
자동환전은 '환전이 처리되는 그 시각'의 우대율을 적용 → 미 정규장(야간)에 KRW 자동환전되면 0.5%.

사용:
  PYTHONPATH=src python scripts/fx_advice.py                 # 기본 ₩50,000 입금 기준
  PYTHONPATH=src python scripts/fx_advice.py --deposit-krw 100000
  PYTHONPATH=src python scripts/fx_advice.py --no-api        # 환율 조회 생략(오프라인 가이드만)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import fx as fxmod                    # noqa: E402


def _try_exchange_rate() -> dict | None:
    """/exchange-rate(USD→KRW) 조회. 자격증명/네트워크 없으면 None(가이드는 계속)."""
    try:
        from toss_trader.client import TossClient
        from toss_trader.config import get_settings
        s = get_settings()
        s.require_credentials()
        client = TossClient(s)
        resp = client.get_exchange_rate("USD", "KRW")
        return resp if isinstance(resp, dict) else None
    except Exception as e:  # noqa: BLE001
        print(f"  (환율 조회 생략 — 자격증명/네트워크 없음: {e})")
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="환전(FX) 최저비용 가이드 (읽기 전용)")
    ap.add_argument("--deposit-krw", type=float, default=50_000.0,
                    help="1회 입금(환전) 금액(원). 기본 ₩50,000.")
    ap.add_argument("--no-api", action="store_true", help="환율 API 조회 생략(오프라인 가이드만).")
    args = ap.parse_args(argv)

    model = fxmod.FxCostModel()
    now = datetime.now(fxmod.KST)
    in_win = model.window.contains(now)
    nxt = model.window.next_open(now)
    dep = args.deposit_krw

    print("=" * 66)
    print("토스증권 KRW→USD 환전 최저비용 가이드  (읽기 전용, 주문 없음)")
    print("=" * 66)
    print(f"지금(KST): {now:%Y-%m-%d %H:%M:%S} ({'평일' if now.weekday() < 5 else '주말'})")
    print(f"환전 우대창(평일 09:00–15:30 KST, 공휴일 제외) 안? → "
          f"{'예 ✅ (지금 환전하면 95% 우대 ≈ 0.05%)' if in_win else '아니오 ❌ (지금은 50% 우대 ≈ 0.5%)'}")
    if not in_win:
        print(f"다음 우대창 시작: {nxt:%Y-%m-%d %H:%M} KST")

    in_fee = dep * model.in_window_bps * 1e-4
    out_fee = dep * model.out_window_bps * 1e-4
    save = out_fee - in_fee
    print("-" * 66)
    print(f"입금 ₩{dep:,.0f} 기준 환전비 비교:")
    print(f"  · 주간창(09:00–15:30, 95% 우대 {model.in_window_bps:.0f}bps): ₩{in_fee:,.1f}")
    print(f"  · 야간/주말(50% 우대 {model.out_window_bps:.0f}bps)         : ₩{out_fee:,.1f}")
    print(f"  → 우대창에 환전하면 회당 ₩{save:,.1f} 절약  "
          f"(연 12회 적립 시 ≈ ₩{save * 12:,.0f}/년)")

    if not args.no_api:
        rate = _try_exchange_rate()
        if rate:
            disp = fxmod.displayed_spread_bps(rate.get("rate"), rate.get("midRate"))
            print("-" * 66)
            print("현재 표시 환율(/exchange-rate, 참고용):")
            print(f"  rate(매수)={rate.get('rate')}  midRate(매매기준)={rate.get('midRate')}  "
                  f"basisPoint={rate.get('basisPoint')}")
            if disp is not None:
                print(f"  표시 스프레드 ≈ {disp:.1f}bps  "
                      "⚠️ 표시값일 뿐, 실제 환전 우대율(시간대별 5/50bps)과 다르다.")

    print("-" * 66)
    print("권장(공식 근거):")
    print("  1) 계좌를 **USD로 미리 채워두기** — 국내 영업일 09:00–15:30 KST 에 앱에서 KRW→USD를")
    print("     직접 환전(95% 우대). 마감 임박(15:29대)엔 시간차로 50% 적용될 수 있어 여유 있게.")
    print("  2) 그러면 야간 미 정규장 DCA 매수는 이미 있는 USD로 체결 → 자동환전(0.5%) 회피.")
    print("  3) API엔 환전/예약환전 엔드포인트가 없다 → **환전은 앱에서 수동/정기(주식모으기 아님)**로.")
    print("  4) run_dca.py --require-usd: USD 부족 시 야간 자동환전 대신 매수를 보류(우대창 환전 후 재개).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
