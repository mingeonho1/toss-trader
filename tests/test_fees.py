"""fees.TossFeeSchedule 결정론적 테스트 (순수 stdlib).

손계산 앵커(오케스트레이터 확정 사실, 2025-12-01~):
- 미국 표준 수수료 0.1%, 건당 체결금액 ≤ $10 무료, $0.01 미만 절사.
- 매도 규제수수료(불확실): SEC $20.60/$1M(min $0.01), FINRA TAF ~$0.000166/주(min $0.01, max $8.30).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from toss_trader.fees import TossFeeSchedule  # noqa: E402


# ── 매수 수수료(0.1%, ≤$10 무료, 센트 절사) ─────────────────────────────────
def test_buy_commission_free_threshold_and_truncation():
    f = TossFeeSchedule()
    assert f.order_fee("BUY", 9.99) == 0.0          # ≤$10 무료
    assert f.order_fee("BUY", 10.00) == 0.0         # 경계 포함 무료
    assert f.order_fee("BUY", 10.01) == 0.01        # 0.1%=$0.01001 → 절사 $0.01
    assert f.order_fee("BUY", 100.0) == 0.10        # 정확히 $0.10 (부동소수 0.0999… 아님)
    # 절사 핵심: $100×0.1%는 float로 0.099999…가 될 수 있으나 Decimal로 정확히 0.10.
    assert f.order_fee("BUY", 100.0) == pytest.approx(0.10, abs=0)


# ── 매도(수수료 + 규제 최소금액) ─────────────────────────────────────────────
def test_sell_commission_plus_regulatory_minimums():
    f = TossFeeSchedule()
    b = f.order_fee_breakdown("SELL", 100.0, shares=0.2)
    assert b.commission == 0.10                     # 0.1% of $100
    assert b.sec_fee == 0.01                         # 100×0.0000206=0.00206 → min $0.01
    assert b.taf == 0.01                             # 0.2×0.000166≈0 → min $0.01
    assert b.total == 0.12
    assert f.order_fee("SELL", 100.0, shares=0.2) == 0.12


def test_tiny_sell_is_expensive_due_to_reg_minimums():
    # ≤$10라 수수료는 무료지만 규제 최소($0.01+$0.01)가 붙어 소액 매도는 비싸다.
    f = TossFeeSchedule()
    assert f.order_fee("SELL", 5.0, shares=0.1) == 0.02
    # 규제 최소를 끄면(불확실 → 토글) 소액 매도도 0.
    f2 = TossFeeSchedule(apply_regulatory_min=False)
    assert f2.order_fee("SELL", 5.0, shares=0.1) == 0.0


def test_taf_cap_and_sec_scaling_on_large_sell():
    f = TossFeeSchedule()
    # 큰 매도: 수수료 0.1%, SEC 비례(센트 올림), TAF는 상한 $8.30.
    b = f.order_fee_breakdown("SELL", 1_000_000.0, shares=1_000_000.0)
    assert b.commission == 1000.0                    # 0.1% of $1M
    assert b.taf == 8.30                             # 1e6×0.000166=166 → 상한 8.30
    assert b.sec_fee == pytest.approx(20.60, abs=0.01)  # 1e6×0.0000206=20.60


def test_truncate_toggle_rounds_when_disabled():
    trunc = TossFeeSchedule()
    rnd = TossFeeSchedule(truncate_cents=False)
    # $10.01×0.1%=0.01001 → 절사 0.01 / 반올림 0.01 (동일)
    assert trunc.order_fee("BUY", 10.01) == 0.01
    # $10.06×0.1%=0.01006 → 절사 0.01 / 반올림 0.01
    assert trunc.order_fee("BUY", 10.06) == 0.01
    # $15.5×0.1%=0.0155 → 절사 0.01 / 반올림 0.02
    assert trunc.order_fee("BUY", 15.5) == 0.01
    assert rnd.order_fee("BUY", 15.5) == 0.02


# ── 분할 계획 ─────────────────────────────────────────────────────────────────
def test_plan_split_buy_35_into_four_free_chunks():
    f = TossFeeSchedule()
    p = f.plan_split("BUY", 35.0, price=None)
    assert p.notionals == [10.0, 10.0, 10.0, 5.0]
    assert p.n_orders == 4
    assert p.total_fee == 0.0                         # 전 청크 ≤$10 → 무료
    assert p.effective_bps == 0.0


def test_plan_split_buy_capped_leaves_one_paying_chunk():
    f = TossFeeSchedule()
    # max_orders=2: (2-1)개 $10 무료 + 나머지 1건에 몰아 과세 노셔널 최소화.
    p = f.plan_split("BUY", 35.0, price=None, max_orders=2)
    assert p.notionals == [10.0, 25.0]
    assert p.total_fee == pytest.approx(0.02)         # 25×0.1%=0.025 → 절사 0.02
    # 분할 안 한 단건($35)보다 싸야 한다.
    assert p.total_fee < f.order_fee("BUY", 35.0)


def test_plan_split_buy_min_chunk_merges_dust():
    f = TossFeeSchedule()
    # $10.30, min_chunk=$1 → [10, 0.30] 인데 0.30<$1 → 직전에 합쳐 [10.30] 단건.
    p = f.plan_split("BUY", 10.30, price=None, min_chunk=1.0)
    assert p.notionals == [10.30]


def test_plan_split_sell_is_single_order():
    f = TossFeeSchedule()
    p = f.plan_split("SELL", 100.0, price=50.0)       # 2주
    assert p.notionals == [100.0]                     # 규제 최소 때문에 쪼개지 않는다
    assert p.total_fee == pytest.approx(0.12)         # 0.10 + 0.01 + 0.01
    # 매도를 10건으로 쪼개면 규제 최소가 10배 → 더 비싸다(단건이 최적임을 대비 확인).
    split_cost = sum(f.order_fee("SELL", 10.0, shares=0.2) for _ in range(10))
    assert split_cost > p.total_fee


def test_plan_split_sub_threshold_buy_single_free():
    f = TossFeeSchedule()
    p = f.plan_split("BUY", 8.0, price=None)
    assert p.notionals == [8.0] and p.total_fee == 0.0


# ── 곡선 & fee_fn 훅 ─────────────────────────────────────────────────────────
def test_effective_bps_curve_shape():
    f = TossFeeSchedule()
    rows = {pt.notional: pt for pt in f.effective_bps_curve()}
    assert rows[9.99].buy_bps == 0.0 and rows[10.0].buy_bps == 0.0   # ≤$10 매수 무료
    assert rows[10.01].buy_bps > 0.0                                  # 임계 초과부터 과금
    # 매도 소액은 규제 최소로 실효 bps가 폭발(1$ 매도 → $0.02 → 200bps).
    assert rows[1.0].sell_bps > rows[100.0].sell_bps
    assert rows[1.0].sell_bps == pytest.approx(200.0)


def test_as_fee_fn_split_and_nosplit():
    f = TossFeeSchedule()
    split = f.as_fee_fn(split=True)
    nosplit = f.as_fee_fn(split=False)
    assert split("BUY", 35.0) == 0.0                  # 분할 → 무료
    assert nosplit("BUY", 35.0) == pytest.approx(0.03)  # 단건 0.1% 절사
    # 매도는 price로 TAF 산정.
    fn = f.as_fee_fn(split=False, price=50.0)
    assert fn("SELL", 100.0) == pytest.approx(0.12)


def test_order_fee_side_case_insensitive_and_abs():
    f = TossFeeSchedule()
    assert f.order_fee("buy", 100.0) == 0.10
    assert f.order_fee("BUY", -100.0) == 0.10          # 절대값 사용
