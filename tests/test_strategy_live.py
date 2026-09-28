"""scripts/run_strategy.py — 페이퍼 전략 → 실계좌 브릿지 결정론적 테스트(fake client, 주문 없음 기본).

검증:
- compute_target_weights: 페이퍼 랩 decide 와 동일한 목표비중.
- build_order_plan: 매도 먼저·전량/부분(6dp 내림)·≤$10 분할·max-usd/BP 캡·sleeve-frac.
- leverage_gate: |leverageFactor|>1 거부(플래그로 허용), 1x 통과, 422 prerequisite 매핑.
- map_order_error: 422 코드 → 안내 매핑.
- kill_switch_check: 페이퍼 낙폭 / 당일 손익 한도 위반.
- execute_plan: 매도→매수 순서, 세션 멱등(재실행 중복 없음).
- dry-run: create_order 절대 호출 안 함.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_strategy as rs  # noqa: E402
from toss_trader.errors import TossAPIError  # noqa: E402
from toss_trader.models import Candle  # noqa: E402


def _noop(_m=None):
    pass


# ── fake clients ─────────────────────────────────────────────────────────────
class FakeOrderClient:
    """create_order 기록. fail_on(전역 인덱스)에서 exc(기본 RuntimeError)를 던진다."""

    def __init__(self, fail_on=None, exc=None):
        self.calls: list[dict] = []
        self.fail_on = fail_on
        self.exc = exc or RuntimeError("boom")
        self._n = 0

    def create_order(self, symbol, side, *, order_type="MARKET", quantity=None,
                     order_amount=None, client_order_id=None, **kw):
        i = self._n
        self._n += 1
        self.calls.append({"symbol": symbol, "side": side.upper(), "quantity": quantity,
                           "order_amount": order_amount, "cid": client_order_id})
        if self.fail_on is not None and i == self.fail_on:
            raise self.exc
        return {"orderId": f"o{i}", "clientOrderId": client_order_id}


class StocksClient:
    def __init__(self, factors: dict, raise_exc=None):
        self._factors = factors
        self._raise = raise_exc

    def get_stocks(self, symbols):
        if self._raise is not None:
            raise self._raise
        syms = symbols if isinstance(symbols, list) else str(symbols).split(",")
        return [{"symbol": s, "leverageFactor": self._factors.get(s)} for s in syms]


# ── compute_target_weights ───────────────────────────────────────────────────
class _LastCloseStrat:
    """마지막 종가가 임계 이상이면 QQQ 100%, 아니면 현금. state 무관(순수)."""
    name = "t"

    def universe(self):
        return ["QQQ"]

    def decide(self, history, state):
        h = history.get("QQQ") or []
        return {"QQQ": 1.0} if h and h[-1].close >= 100 else {}


def _c(close):
    return Candle("QQQ", date(2026, 9, 28), close, close, close, close, 1.0)


def test_compute_target_weights_matches_decide():
    strat = _LastCloseStrat()
    panel = {"QQQ": [_c(90), _c(105)]}
    assert rs.compute_target_weights(strat, panel) == {"QQQ": 1.0}
    panel2 = {"QQQ": [_c(90), _c(95)]}
    assert rs.compute_target_weights(strat, panel2) == {}
    # decide 를 그대로 호출하므로 동일 입력 → 동일 결정.
    assert rs.compute_target_weights(strat, panel) == strat.decide({"QQQ": panel["QQQ"]}, {})


# ── build_order_plan ─────────────────────────────────────────────────────────
def test_build_plan_sells_before_buys_and_full_exit():
    # 목표 QQQ 100%. TQQQ 보유(목표 0) → 전량 매도. 현금으로 QQQ 매수.
    target = {"QQQ": 1.0}
    holdings = {"TQQQ": {"qty": 2.0, "price": 50.0}, "QQQ": {"qty": 1.0, "price": 100.0}}
    plan = rs.build_order_plan(target, holdings, buying_power=100.0)
    assert plan.total_equity == 300.0 and plan.investable == 300.0
    assert [s.symbol for s in plan.sells] == ["TQQQ"]
    assert plan.sells[0].full_exit is True and plan.sells[0].qty == "2"
    assert [b.symbol for b in plan.buys] == ["QQQ"]        # 매도 뒤 매수
    assert plan.buys[0].usd == 100.0                       # BP 로 캡(목표미달 200 > BP 100)


def test_build_plan_partial_sell_rounds_down_6dp():
    # 목표 QQQ 50% of investable. 보유 QQQ 초과분만 부분 매도(6dp 내림), 단건.
    target = {"QQQ": 0.5}
    holdings = {"QQQ": {"qty": 3.0, "price": 33.3333}}     # 평가 ≈ 99.9999
    # investable = 0 BP + 99.9999 = 99.9999 ; 목표 QQQ = 49.99995 → 초과 ≈ 50 → 매도 수량 ≈ 1.5
    plan = rs.build_order_plan(target, holdings, buying_power=0.0)
    assert len(plan.sells) == 1 and plan.sells[0].full_exit is False
    q = plan.sells[0].qty
    assert "." in q and len(q.split(".")[1]) <= 6         # 소수점 6자리 이하
    assert float(q) <= 1.5 + 1e-9                          # 내림(초과 방지)
    assert plan.buys == []


def test_build_plan_chunks_and_max_usd_cap():
    target = {"QQQ": 1.0}
    plan = rs.build_order_plan(target, {}, buying_power=1000.0, max_usd=35.0)
    assert [b.symbol for b in plan.buys] == ["QQQ"]
    assert plan.buys[0].usd == 35.0
    assert plan.buys[0].chunks == [10.0, 10.0, 10.0, 5.0]  # ≤$10 분할
    assert plan.sells == []


def test_build_plan_buying_power_cap():
    target = {"QQQ": 1.0}
    plan = rs.build_order_plan(target, {}, buying_power=12.0)  # 목표 12, BP 12
    assert plan.buy_total <= 12.0 + 1e-9
    assert plan.buys[0].chunks == [10.0, 2.0]


def test_build_plan_sleeve_frac_scales_investable():
    target = {"QQQ": 1.0}
    holdings = {"QQQ": {"qty": 0.0, "price": 0.0}}
    plan = rs.build_order_plan(target, holdings, buying_power=100.0, sleeve_frac=0.25)
    # investable = 0.25 × 100 = 25 → QQQ 매수 목표 25(≤ BP 100)
    assert plan.investable == 25.0
    assert plan.buy_total == 25.0


def test_build_plan_two_buys_max_usd_split_across_symbols():
    target = {"QQQ": 0.5, "SCHD": 0.5}
    plan = rs.build_order_plan(target, {}, buying_power=1000.0, max_usd=30.0)
    assert plan.buy_total <= 30.0 + 1e-9                   # 총합이 max-usd 를 넘지 않음


# ── leverage_gate ────────────────────────────────────────────────────────────
def test_leverage_gate_refuses_leveraged():
    client = StocksClient({"TQQQ": 3.0, "QQQ": 1.0})
    ok, offending, factors = rs.leverage_gate(client, ["TQQQ", "QQQ"],
                                              allow_leveraged=False, log=_noop)
    assert ok is False and offending == ["TQQQ"] and factors["TQQQ"] == 3.0


def test_leverage_gate_allows_with_flag():
    client = StocksClient({"SQQQ": -3.0})                 # 인버스도 |factor|>1
    ok, offending, _ = rs.leverage_gate(client, ["SQQQ"], allow_leveraged=True, log=_noop)
    assert ok is True and offending == ["SQQQ"]


def test_leverage_gate_passes_non_leveraged():
    client = StocksClient({"QQQ": 1.0, "SCHD": None})     # None → 1x 취급
    ok, offending, _ = rs.leverage_gate(client, ["QQQ", "SCHD"],
                                        allow_leveraged=False, log=_noop)
    assert ok is True and offending == []


def test_leverage_gate_maps_prerequisite_error_to_refusal():
    exc = TossAPIError(422, "prerequisite-required", "need")
    client = StocksClient({}, raise_exc=exc)
    ok, offending, _ = rs.leverage_gate(client, ["TQQQ"], allow_leveraged=False, log=_noop)
    assert ok is False and offending == ["TQQQ"]


# ── map_order_error ──────────────────────────────────────────────────────────
def test_map_order_error_codes():
    assert rs.map_order_error(TossAPIError(422, "prerequisite-required", "x")) == rs.REG_MESSAGE
    assert rs.map_order_error(TossAPIError(422, "stock-restricted", "x")) == rs.REG_MESSAGE
    assert "계좌" in rs.map_order_error(TossAPIError(422, "account-restricted", "x"))
    assert "매수가능금액" in rs.map_order_error(TossAPIError(422, "insufficient-buying-power", "x"))
    assert rs.map_order_error(RuntimeError("nope")) is None


# ── kill_switch_check ────────────────────────────────────────────────────────
def test_kill_switch_paper_dd_breach():
    ok, reasons = rs.kill_switch_check(-0.40, None, max_paper_dd=0.35, max_intraday_loss=0.10)
    assert ok is False and any("낙폭" in r for r in reasons)


def test_kill_switch_intraday_breach():
    ok, reasons = rs.kill_switch_check(-0.05, -0.12, max_paper_dd=0.35, max_intraday_loss=0.10)
    assert ok is False and any("당일" in r for r in reasons)


def test_kill_switch_disabled_and_pass():
    assert rs.kill_switch_check(-0.99, -0.99, max_paper_dd=0.0, max_intraday_loss=0.0) == (True, [])
    assert rs.kill_switch_check(-0.10, -0.02, max_paper_dd=0.35, max_intraday_loss=0.10)[0] is True


# ── execute_plan: 매도→매수 순서 + 멱등 ──────────────────────────────────────
def _plan_sell_then_buy():
    return rs.OrderPlan(
        target={"QQQ": 1.0}, total_equity=300.0, investable=300.0,
        sells=[rs.SellOrder("TQQQ", "2", 50.0, 100.0, True)],
        buys=[rs.BuyOrder("QQQ", 35.0, [10.0, 10.0, 10.0, 5.0])])


def test_execute_plan_sells_then_buys_ordering(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "LIVE_DIR", tmp_path)
    fc = FakeOrderClient()
    st = rs._session_state("t", "2026-09-28")
    ok = rs.execute_plan(fc, _plan_sell_then_buy(), "2026-09-28", st, _noop,
                         split=True, buying_power=1000.0, name="t")
    assert ok is True
    sides = [c["side"] for c in fc.calls]
    assert sides[0] == "SELL" and set(sides[1:]) == {"BUY"}    # 매도 먼저
    assert fc.calls[0]["cid"] == "strat-sell-2026-09-28-TQQQ"
    assert [c["order_amount"] for c in fc.calls[1:]] == ["10.00", "10.00", "10.00", "5.00"]
    assert [c["cid"] for c in fc.calls[1:]] == [
        "strat-2026-09-28-QQQ-0", "strat-2026-09-28-QQQ-1",
        "strat-2026-09-28-QQQ-2", "strat-2026-09-28-QQQ-3"]


def test_execute_plan_idempotent_rerun_no_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "LIVE_DIR", tmp_path)
    st = rs._session_state("t", "2026-09-28")
    fc1 = FakeOrderClient()
    assert rs.execute_plan(fc1, _plan_sell_then_buy(), "2026-09-28", st, _noop,
                           split=True, buying_power=1000.0, name="t") is True
    n_first = len(fc1.calls)
    assert n_first == 5                                  # 1 sell + 4 buy chunks
    # 같은 세션 상태로 재실행 → 매도·매수 모두 이미 완료 → 신규 주문 0.
    fc2 = FakeOrderClient()
    assert rs.execute_plan(fc2, _plan_sell_then_buy(), "2026-09-28", st, _noop,
                           split=True, buying_power=1000.0, name="t") is True
    assert fc2.calls == []                               # 중복 접수 없음


def test_execute_plan_maps_leveraged_422_on_buy(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "LIVE_DIR", tmp_path)
    logs: list[str] = []
    st = rs._session_state("t", "2026-09-28")
    plan = rs.OrderPlan(target={"TQQQ": 1.0}, total_equity=100.0, investable=100.0,
                        sells=[], buys=[rs.BuyOrder("TQQQ", 20.0, [10.0, 10.0])])
    fc = FakeOrderClient(fail_on=0, exc=TossAPIError(422, "prerequisite-required", "need"))
    ok = rs.execute_plan(fc, plan, "2026-09-28", st, logs.append,
                         split=True, buying_power=1000.0, name="t")
    assert ok is False
    assert any(rs.REG_MESSAGE in m for m in logs)         # 422 → 규제 안내 매핑


# ── dry-run: create_order 절대 호출 안 함 ────────────────────────────────────
class DryRunClient(FakeOrderClient):
    def __init__(self, factors):
        super().__init__()
        self._factors = factors
        self.s = SimpleNamespace(account_seq="1", is_live=False)

    def get_accounts(self):
        return [{"accountSeq": "1"}]

    def get_buying_power(self, currency="USD"):
        return {"cashBuyingPower": "100"}

    def get_exchange_rate(self, *a, **k):
        return {"rate": "1500"}

    def get_holdings(self, symbol=None):
        return {"items": [{"symbol": "TQQQ", "quantity": "1", "lastPrice": "50",
                           "marketCountry": "US"}]}

    def get_stocks(self, symbols):
        syms = symbols if isinstance(symbols, list) else str(symbols).split(",")
        return [{"symbol": s, "leverageFactor": self._factors.get(s)} for s in syms]


def test_dry_run_never_calls_create_order(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(rs, "REPORTS", tmp_path)
    monkeypatch.setattr(rs, "REPORT_LATEST", tmp_path / "r.md")
    monkeypatch.setattr(rs, "PAPERLAB_DIR", tmp_path / "paperlab")

    strat = _LastCloseStrat()
    fake_plr = SimpleNamespace(_load_panel=lambda needed, offline: {"QQQ": [_c(105)]})
    monkeypatch.setattr(rs, "load_strategy", lambda name: (strat, fake_plr))
    fc = DryRunClient({"QQQ": 1.0, "TQQQ": 3.0})
    monkeypatch.setattr(rs, "get_settings", lambda: SimpleNamespace(
        account_seq="1", is_live=False, require_credentials=lambda: None))
    monkeypatch.setattr(rs, "TossClient", lambda s: fc)

    args = SimpleNamespace(strategy="t", execute=False, max_usd=None, sleeve_frac=1.0,
                           allow_leveraged_etp=False, max_paper_dd=0.35, max_intraday_loss=0.10,
                           split_small_orders=True, require_usd=False, offline=True)
    rc = rs.run(args)
    assert rc == 0
    assert fc.calls == []                                 # dry-run → 주문 없음
    assert (tmp_path / "r.md").exists()                   # 리포트는 생성
