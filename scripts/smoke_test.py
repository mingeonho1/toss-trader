#!/usr/bin/env python3
"""읽기 전용 스모크 테스트 — 주문은 절대 내지 않는다.

토스 OpenAPI 키 발급 + .env 작성 후 실행:
    python scripts/smoke_test.py

각 호출의 응답 구조를 덤프한다. 명세에 없던 실제 필드명을 여기서 확인해
Phase 2(marketdata/costs 정규화)의 근거로 삼는다.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.client import TossClient  # noqa: E402
from toss_trader.config import get_settings  # noqa: E402
from toss_trader.errors import TossError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def show(label: str, fn):
    try:
        result = fn()
        preview = json.dumps(result, ensure_ascii=False, indent=2)
        if len(preview) > 1800:
            preview = preview[:1800] + "\n... (생략)"
        print(f"\n=== {label} ===\n{preview}")
        return result
    except TossError as e:
        print(f"\n=== {label} === ❌ {e}")
    except Exception as e:  # noqa: BLE001
        print(f"\n=== {label} === ⚠️ 예상치 못한 오류: {e!r}")
    return None


def main() -> int:
    s = get_settings()
    print(f"mode={s.trading_mode}  base={s.base_url}  account={'set' if s.has_account else '없음'}")
    try:
        s.require_credentials()
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1

    client = TossClient(s)

    # --- 인증/시세 (계좌 불필요) ---
    show("OAuth 토큰", lambda: {"ok": bool(client._ensure_token())})
    show("미국 장 캘린더", lambda: client.get_market_calendar("US"))
    show("환율 USD→KRW", lambda: client.get_exchange_rate("USD", "KRW"))
    show("현재가 AAPL,MSFT", lambda: client.get_prices(["AAPL", "MSFT"]))
    show("일봉 AAPL (5)", lambda: client.get_candles("AAPL", interval="1d", count=5))
    show("종목정보 AAPL,MSFT", lambda: client.get_stocks(["AAPL", "MSFT"]))

    # --- 계좌 목록 → accountSeq 자동 추출 ---
    accounts = show("계좌 목록", client.get_accounts)
    seq = s.account_seq
    if not seq and isinstance(accounts, list) and accounts:
        seq = str(accounts[0].get("accountSeq", "") or "")
        if seq:
            print(f"\nℹ️ .env에 ACCOUNT_SEQ 미설정 → 계좌 목록의 accountSeq={seq} 로 이어서 검증합니다.")
            print(f"   (영구 설정하려면 .env에 ACCOUNT_SEQ={seq} 추가)")
            client.s = dataclasses.replace(client.s, account_seq=seq)

    # --- 계좌/자산 (account_seq 필요) ---
    if seq:
        show("보유 주식", client.get_holdings)
        show("매수가능금액(USD)", lambda: client.get_buying_power("USD"))
        show("수수료율", client.get_commissions)
    else:
        print("\n⚠️ accountSeq를 확보하지 못해 계좌/자산 검증을 건너뜁니다.")

    print("\n✅ 스모크 테스트 완료 (주문은 실행하지 않음).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
