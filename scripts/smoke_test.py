#!/usr/bin/env python3
"""읽기 전용 스모크 테스트 — 주문은 절대 내지 않는다.

토스 OpenAPI 키 발급 + .env 작성 후 실행:
    python scripts/smoke_test.py

각 호출의 응답 구조를 덤프한다. 명세에 없던 실제 필드명을 여기서 확인해
Phase 2(marketdata/costs 정규화)의 근거로 삼는다.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.client import TossClient  # noqa: E402
from toss_trader.config import get_settings  # noqa: E402
from toss_trader.errors import TossError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def show(label: str, fn) -> None:
    try:
        result = fn()
        preview = json.dumps(result, ensure_ascii=False, indent=2)
        if len(preview) > 1500:
            preview = preview[:1500] + "\n... (생략)"
        print(f"\n=== {label} ===\n{preview}")
    except TossError as e:
        print(f"\n=== {label} === ❌ {e}")
    except Exception as e:  # noqa: BLE001
        print(f"\n=== {label} === ⚠️ 예상치 못한 오류: {e!r}")


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
    show("환율", client.get_exchange_rate)
    show("현재가 AAPL,MSFT", lambda: client.get_prices(["AAPL", "MSFT"]))
    show("일봉 AAPL (5)", lambda: client.get_candles("AAPL", interval="1d", count=5))
    show("종목정보 AAPL", lambda: client.get_stock_info("AAPL"))

    # --- 계좌 (account_seq 필요) ---
    show("계좌 목록", client.get_accounts)
    if s.has_account:
        show("보유 주식", client.get_holdings)
        show("매수가능금액", lambda: client.get_buying_power())
        show("수수료", client.get_commissions)
    else:
        print("\nℹ️ TOSS_ACCOUNT_SEQ 미설정 → 계좌 목록에서 accountSeq를 확인해 .env에 넣으세요.")

    print("\n✅ 스모크 테스트 완료 (주문은 실행하지 않음).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
