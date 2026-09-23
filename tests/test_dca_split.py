"""DCA 분할 매수(수수료 절감) 결정론적 테스트 — run_dca 실행기 + LiveBroker (fake client).

검증:
- plan_buy_chunks: ≤$10 무료 청크 분할이 이득일 때만 나눔.
- run_dca._execute_buys: 분할 cid(dca-{date}-{sym}-{k}, ≤36자), 실패 시 즉시 중단, 실행당 주문 한도.
- LiveBroker(split_small_orders=True): 금액매수를 분할 접수 후 단일 Fill로 합산.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_dca  # noqa: E402


# ── fake client (run_dca._execute_buys 용) ───────────────────────────────────
class FakeOrderClient:
    """create_order 호출을 기록. fail_on(전역 인덱스)에서 예외를 던진다."""

    def __init__(self, fail_on: int | None = None):
        self.calls: list[dict] = []
        self.fail_on = fail_on
        self._n = 0

    def create_order(self, symbol, side, *, order_type="MARKET", order_amount=None,
                     client_order_id=None, **kw):
        i = self._n
        self._n += 1
        self.calls.append({"symbol": symbol, "side": side, "order_amount": order_amount,
                           "cid": client_order_id})
        if self.fail_on is not None and i == self.fail_on:
            raise RuntimeError("boom")
        return {"orderId": f"o{i}", "clientOrderId": client_order_id}


@pytest.fixture(autouse=True)
def _no_state_writes(monkeypatch):
    # 세션 상태 파일 쓰기 부작용 제거(테스트 격리).
    monkeypatch.setattr(run_dca, "_record_session_progress", lambda *a, **k: None)


def _noop_log(_msg):  # 로그 무시
    pass


# ── client_order_id 빌더 ─────────────────────────────────────────────────────
def test_client_order_id_with_and_without_index():
    assert run_dca.client_order_id("2026-09-23", "QQQ") == "dca-2026-09-23-QQQ"
    assert run_dca.client_order_id("2026-09-23", "QQQ", 3) == "dca-2026-09-23-QQQ-3"
    # 긴 심볼 + 인덱스라도 ≤36자, 접미사(-k) 보존.
    cid = run_dca.client_order_id("2026-09-23", "VERYLONGTICKERNAME12345", 7)
    assert len(cid) <= 36 and cid.endswith("-7")


# ── plan_buy_chunks ──────────────────────────────────────────────────────────
def test_plan_buy_chunks_splits_only_when_beneficial():
    assert run_dca.plan_buy_chunks(35.0, split=True) == [10.0, 10.0, 10.0, 5.0]
    assert run_dca.plan_buy_chunks(35.0, split=False) == [35.0]        # 분할 OFF
    assert run_dca.plan_buy_chunks(8.0, split=True) == [8.0]            # ≤$10 단건
    assert run_dca.plan_buy_chunks(10.0, split=True) == [10.0]         # 경계 단건


# ── _execute_buys ────────────────────────────────────────────────────────────
def test_execute_buys_splits_into_free_chunks():
    fc = FakeOrderClient()
    done: set[str] = set()
    ok = run_dca._execute_buys(fc, [("QQQ", 35.0)], "2026-09-23", set(), done,
                               _noop_log, split=True)
    assert ok is True
    assert [c["order_amount"] for c in fc.calls] == ["10.00", "10.00", "10.00", "5.00"]
    assert [c["cid"] for c in fc.calls] == [
        "dca-2026-09-23-QQQ-0", "dca-2026-09-23-QQQ-1",
        "dca-2026-09-23-QQQ-2", "dca-2026-09-23-QQQ-3"]
    assert all(len(c["cid"]) <= 36 for c in fc.calls)
    assert "QQQ" in done


def test_execute_buys_no_split_single_order():
    fc = FakeOrderClient()
    done: set[str] = set()
    ok = run_dca._execute_buys(fc, [("QQQ", 35.0)], "2026-09-23", set(), done,
                               _noop_log, split=False)
    assert ok is True
    assert [c["order_amount"] for c in fc.calls] == ["35.00"]
    assert fc.calls[0]["cid"] == "dca-2026-09-23-QQQ"        # 단건은 -k 접미사 없음
    assert "QQQ" in done


def test_execute_buys_skips_already_done_symbols():
    fc = FakeOrderClient()
    done: set[str] = set()
    ok = run_dca._execute_buys(fc, [("QQQ", 35.0), ("GLD", 20.0)], "2026-09-23",
                               skip={"QQQ"}, done=done, log=_noop_log, split=True)
    assert ok is True
    assert {c["symbol"] for c in fc.calls} == {"GLD"}        # QQQ는 스킵
    assert "QQQ" not in done and "GLD" in done


def test_execute_buys_stops_splitting_on_error():
    fc = FakeOrderClient(fail_on=2)                          # 3번째 청크에서 실패
    done: set[str] = set()
    ok = run_dca._execute_buys(fc, [("QQQ", 35.0)], "2026-09-23", set(), done,
                               _noop_log, split=True)
    assert ok is False
    assert len(fc.calls) == 3                                # 0,1 접수 후 2에서 예외 → 중단
    assert "QQQ" not in done                                 # 부분 성공은 done 아님(재실행 시 cid 멱등)


def test_execute_buys_run_order_budget_caps_total_orders():
    fc = FakeOrderClient()
    done: set[str] = set()
    ok = run_dca._execute_buys(fc, [("QQQ", 35.0), ("GLD", 35.0)], "2026-09-23",
                               set(), done, _noop_log, split=True, max_orders_per_run=2)
    assert ok is True
    # 예산 2 → QQQ가 [10,25] 2건으로 소진, GLD는 다음 트리거로 보류.
    assert len(fc.calls) == 2
    assert {c["symbol"] for c in fc.calls} == {"QQQ"}
    assert "GLD" not in done


# ── LiveBroker 분할 매수 ─────────────────────────────────────────────────────
class _FakeSettings:
    is_live = True
    account_seq = "1"

    def require_account(self):
        pass


class FakeBrokerClient:
    """LiveBroker(split) 검증용. 금액매수를 price=$100 가정으로 체결(수수료 free 청크)."""

    def __init__(self):
        self.s = _FakeSettings()
        self.created: list[dict] = []
        self._orders: dict[str, dict] = {}

    def get_buying_power(self, currency="USD"):
        return {"currency": "USD", "cashBuyingPower": "1000"}

    def get_holdings(self, symbol=None):
        return {"items": []}

    def create_order(self, symbol, side, *, order_type="MARKET", quantity=None,
                     order_amount=None, price=None, time_in_force=None,
                     client_order_id=None, confirm_high_value=False):
        self.created.append({"symbol": symbol, "side": side.upper(),
                             "order_amount": order_amount, "cid": client_order_id})
        oid = f"o{len(self.created)}"
        amt = float(order_amount) if order_amount is not None else 0.0
        qty = amt / 100.0                                    # 체결가 $100 가정
        self._orders[oid] = {"orderId": oid, "status": "FILLED", "execution": {
            "filledQuantity": str(qty), "averageFilledPrice": "100.0",
            "filledAmount": str(amt), "commission": "0", "tax": None}}
        return {"orderId": oid, "clientOrderId": client_order_id}

    def get_order(self, order_id):
        return self._orders[order_id]


def test_live_broker_split_buy_aggregates_into_single_fill():
    from toss_trader.broker import LiveBroker

    fc = FakeBrokerClient()
    lb = LiveBroker(fc, require_live=False, poll_interval=0.0, split_small_orders=True)
    fill = lb.submit_market_order("QQQ", "BUY", amount=35.0, ref_price=100.0,
                                  dt=date(2026, 9, 23))
    # 4개 청크로 분할 접수 (≤$10 무료).
    assert [c["order_amount"] for c in fc.created] == ["10.00", "10.00", "10.00", "5.00"]
    assert [c["cid"] for c in fc.created] == [
        "dca-2026-09-23-QQQ-0", "dca-2026-09-23-QQQ-1",
        "dca-2026-09-23-QQQ-2", "dca-2026-09-23-QQQ-3"]
    assert all(c["cid"] is not None and len(c["cid"]) <= 36 for c in fc.created)
    # 합산 Fill: 총수량 0.35, 체결가 $100.
    assert fill is not None
    assert fill.quantity == pytest.approx(0.35)
    assert fill.price == pytest.approx(100.0)


def test_live_broker_split_off_places_single_amount_order():
    from toss_trader.broker import LiveBroker

    fc = FakeBrokerClient()
    lb = LiveBroker(fc, require_live=False, poll_interval=0.0)   # split 기본 OFF
    lb.submit_market_order("QQQ", "BUY", amount=35.0, ref_price=100.0,
                           dt=date(2026, 9, 23))
    assert [c["order_amount"] for c in fc.created] == ["35.00"]  # 분할 안 함
