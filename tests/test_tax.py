"""해외주식 양도소득세 최적화 모듈 테스트.

커버: 이동평균 원가 산정, 환율(취득/매도일) 반영, 기본공제 경계, 하베스팅이 공제를
초과하지 않음, 손실 하베스팅 통산 상계, 수수료 게이트(절세<비용×안전계수면 비추천).
"""
from __future__ import annotations

import unittest
from datetime import date

from toss_trader.tax import (
    BASIC_DEDUCTION_KRW,
    OVERSEAS_CG_RATE,
    Holding,
    LotBook,
    Method,
    TaxLedger,
    annual_tax,
    harvest_plan,
)

# 편의: 편도 수수료 = notional의 bps. 하베스팅은 통화전환 없음 → 수수료+슬리피지만.
def fee_fn_bps(bps: float):
    return lambda notional_usd: abs(notional_usd) * bps * 1e-4


ZERO_FEE = lambda notional_usd: 0.0
DEC = date(2025, 12, 15)   # 12월(이익 하베스팅 적기)
JUN = date(2025, 6, 15)


class AnnualTaxTest(unittest.TestCase):
    def test_below_deduction_is_zero(self) -> None:
        self.assertEqual(annual_tax(0.0), 0.0)
        self.assertEqual(annual_tax(2_499_999.0), 0.0)
        self.assertEqual(annual_tax(BASIC_DEDUCTION_KRW), 0.0)  # 경계: 정확히 공제면 0

    def test_loss_is_zero(self) -> None:
        self.assertEqual(annual_tax(-5_000_000.0), 0.0)

    def test_just_above_deduction(self) -> None:
        # 250만 + 100만 초과 → 100만 × 22% = 220,000
        self.assertAlmostEqual(annual_tax(3_500_000.0), 1_000_000.0 * OVERSEAS_CG_RATE)

    def test_rate_is_22_percent(self) -> None:
        taxable = 10_000_000.0
        self.assertAlmostEqual(annual_tax(BASIC_DEDUCTION_KRW + taxable),
                               taxable * 0.22)


class MovingAverageBasisTest(unittest.TestCase):
    def test_weighted_average_krw_cost(self) -> None:
        book = LotBook(Method.MOVING_AVERAGE)
        # 10주 @ $100, 환율 1000 → 원가 1,000,000원 (주당 100,000)
        book.buy("QQQ", 10, 100.0, 1000.0, date(2020, 1, 1))
        # 10주 @ $200, 환율 1300 → 원가 2,600,000원 (주당 260,000)
        book.buy("QQQ", 10, 200.0, 1300.0, date(2022, 1, 1))
        self.assertAlmostEqual(book.quantity("QQQ"), 20.0)
        # 총 원가 3,600,000 / 20주 = 180,000/주 (이동평균, 원화)
        self.assertAlmostEqual(book.cost_basis_krw("QQQ"), 3_600_000.0)
        self.assertAlmostEqual(book.avg_krw_per_share("QQQ"), 180_000.0)

    def test_purchase_fee_added_to_basis(self) -> None:
        book = LotBook(Method.MOVING_AVERAGE)
        book.buy("QQQ", 10, 100.0, 1000.0, date(2020, 1, 1), fee_krw=5_000.0)
        # 1,000,000 + 5,000 필요경비 = 1,005,000
        self.assertAlmostEqual(book.cost_basis_krw("QQQ"), 1_005_000.0)

    def test_sell_uses_average_and_sale_fx(self) -> None:
        book = LotBook(Method.MOVING_AVERAGE)
        book.buy("QQQ", 10, 100.0, 1000.0, date(2020, 1, 1))   # 평단 100,000원/주
        # 5주 매도 @ $150, 매도일 환율 1200 → 양도가액 5×150×1200 = 900,000
        sale = book.sell("QQQ", 5, 150.0, 1200.0, date(2023, 1, 1))
        self.assertAlmostEqual(sale.proceeds_krw, 900_000.0)
        self.assertAlmostEqual(sale.cost_krw, 5 * 100_000.0)     # 500,000
        self.assertAlmostEqual(sale.gain_krw, 400_000.0)         # 환차익 포함
        # 잔여 5주, 평단 불변
        self.assertAlmostEqual(book.quantity("QQQ"), 5.0)
        self.assertAlmostEqual(book.avg_krw_per_share("QQQ"), 100_000.0)

    def test_sale_fee_reduces_gain(self) -> None:
        book = LotBook(Method.MOVING_AVERAGE)
        book.buy("QQQ", 10, 100.0, 1000.0, date(2020, 1, 1))
        sale = book.sell("QQQ", 5, 150.0, 1200.0, date(2023, 1, 1), fee_krw=10_000.0)
        self.assertAlmostEqual(sale.gain_krw, 400_000.0 - 10_000.0)

    def test_oversell_raises(self) -> None:
        book = LotBook()
        book.buy("QQQ", 1, 100.0, 1000.0, date(2020, 1, 1))
        with self.assertRaises(ValueError):
            book.sell("QQQ", 2, 100.0, 1000.0, date(2021, 1, 1))


class FifoBasisTest(unittest.TestCase):
    def test_fifo_consumes_oldest_first(self) -> None:
        book = LotBook(Method.FIFO)
        book.buy("QQQ", 10, 100.0, 1000.0, date(2020, 1, 1))   # 로트1: 100,000/주
        book.buy("QQQ", 10, 200.0, 1300.0, date(2022, 1, 1))   # 로트2: 260,000/주
        # 15주 매도 → 로트1 10주(1,000,000) + 로트2 5주(1,300,000) = 2,300,000 원가
        sale = book.sell("QQQ", 15, 300.0, 1400.0, date(2023, 1, 1))
        self.assertAlmostEqual(sale.cost_krw, 2_300_000.0)
        self.assertAlmostEqual(book.quantity("QQQ"), 5.0)
        # 잔여 5주는 로트2 → 원가 260,000/주
        self.assertAlmostEqual(book.avg_krw_per_share("QQQ"), 260_000.0)

    def test_fifo_vs_moving_average_differ(self) -> None:
        def cost_after(method):
            b = LotBook(method)
            b.buy("QQQ", 10, 100.0, 1000.0, date(2020, 1, 1))
            b.buy("QQQ", 10, 200.0, 1300.0, date(2022, 1, 1))
            return b.sell("QQQ", 5, 300.0, 1400.0, date(2023, 1, 1)).cost_krw
        fifo = cost_after(Method.FIFO)          # 5주 @ 로트1 = 500,000
        ma = cost_after(Method.MOVING_AVERAGE)  # 5주 @ 평단 180,000 = 900,000
        self.assertAlmostEqual(fifo, 500_000.0)
        self.assertAlmostEqual(ma, 900_000.0)
        self.assertNotAlmostEqual(fifo, ma)


class LedgerTest(unittest.TestCase):
    def test_realized_by_year_nets_across_symbols(self) -> None:
        ledger = TaxLedger(path=None)
        book = LotBook()
        book.buy("QQQ", 10, 100.0, 1000.0, date(2024, 1, 1))
        book.buy("SPY", 10, 100.0, 1000.0, date(2024, 1, 1))
        ledger.record(book.sell("QQQ", 10, 200.0, 1000.0, date(2024, 6, 1)))  # +1,000,000
        ledger.record(book.sell("SPY", 10, 50.0, 1000.0, date(2024, 6, 1)))   # -500,000
        self.assertAlmostEqual(ledger.realized_ytd(2024), 500_000.0)          # 통산
        self.assertAlmostEqual(ledger.realized_ytd(2023), 0.0)

    def test_persistence_roundtrip(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tax_ledger.json"
            l1 = TaxLedger(path=p)
            book = LotBook()
            book.buy("QQQ", 10, 100.0, 1000.0, date(2024, 1, 1))
            l1.record(book.sell("QQQ", 10, 300.0, 1000.0, date(2024, 6, 1)))
            l1.save()
            l2 = TaxLedger(path=p)   # 재로드
            self.assertAlmostEqual(l2.realized_ytd(2024), 2_000_000.0)
            self.assertAlmostEqual(l2.tax_for_year(2024),
                                   annual_tax(2_000_000.0))  # 공제 이하 → 0


class GainHarvestTest(unittest.TestCase):
    def _holding(self, unrealized_krw: float, fx=1300.0, price=100.0):
        # cost_basis = value - unrealized
        qty = 100.0
        value = qty * price * fx
        return Holding("QQQ", qty, price, value - unrealized_krw)

    def test_never_exceeds_remaining_deduction(self) -> None:
        # 미실현 이익 1,000만인데 남은 공제는 250만 → 실현은 250만까지만.
        h = self._holding(10_000_000.0)
        plan = harvest_plan([h], 1300.0, 0.0, fee_fn=ZERO_FEE, today=DEC)
        self.assertEqual(plan.mode, "gain")
        self.assertLessEqual(plan.harvested_krw, BASIC_DEDUCTION_KRW + 1e-6)
        self.assertAlmostEqual(plan.harvested_krw, BASIC_DEDUCTION_KRW)
        # 절세 = 실현이익 × 22%
        self.assertAlmostEqual(plan.tax_saved_krw, BASIC_DEDUCTION_KRW * OVERSEAS_CG_RATE)

    def test_partial_remaining_deduction(self) -> None:
        # 이미 100만 실현 → 남은 공제 150만까지만 하베스팅.
        h = self._holding(10_000_000.0)
        plan = harvest_plan([h], 1300.0, 1_000_000.0, fee_fn=ZERO_FEE, today=DEC)
        self.assertAlmostEqual(plan.harvested_krw, 1_500_000.0)

    def test_sell_quantity_realizes_target_gain(self) -> None:
        # 미실현 이익이 정확히 250만이면 전량이 아니라 필요한 비율만 매도.
        h = self._holding(5_000_000.0)   # 미실현 500만
        plan = harvest_plan([h], 1300.0, 0.0, fee_fn=ZERO_FEE, today=DEC)
        a = plan.actions[0]
        # 실현이익 = sell_qty/qty × 미실현. 목표 250만 → 비율 0.5 → 50주.
        self.assertAlmostEqual(a.sell_quantity, 50.0)
        self.assertAlmostEqual(a.realized_krw, BASIC_DEDUCTION_KRW)

    def test_no_gain_when_deduction_used(self) -> None:
        # 이미 공제 초과 실현 + 손실 없음 → 하베스팅 없음.
        h = self._holding(10_000_000.0)
        plan = harvest_plan([h], 1300.0, 3_000_000.0, fee_fn=ZERO_FEE, today=DEC)
        self.assertFalse(plan.recommended)
        self.assertEqual(plan.mode, "loss")  # 공제 초과 → 손실 검토했으나 손실 없음
        self.assertEqual(len(plan.actions), 0)

    def test_not_december_not_recommended(self) -> None:
        h = self._holding(10_000_000.0)
        plan = harvest_plan([h], 1300.0, 0.0, fee_fn=ZERO_FEE, today=JUN)
        self.assertFalse(plan.recommended)
        self.assertIn("12", plan.reason)


class LossHarvestTest(unittest.TestCase):
    def test_offsets_only_excess_above_deduction(self) -> None:
        # 이미 실현이익 400만(공제 250만 초과분 150만). 미실현 손실 -1,000만.
        # 상계 가치는 초과분 150만까지만.
        qty, price, fx = 100.0, 100.0, 1300.0
        value = qty * price * fx
        loser = Holding("SPY", qty, price, value + 10_000_000.0)  # cost>value → 손실
        plan = harvest_plan([loser], fx, 4_000_000.0, fee_fn=ZERO_FEE, today=DEC)
        self.assertEqual(plan.mode, "loss")
        self.assertAlmostEqual(plan.harvested_krw, -1_500_000.0)   # 상계 150만
        self.assertAlmostEqual(plan.tax_saved_krw, 1_500_000.0 * OVERSEAS_CG_RATE)
        self.assertTrue(plan.recommended)

    def test_no_offset_when_below_deduction(self) -> None:
        # 실현이익이 공제 이하 → 상계할 과세이익 없음(손실 실현 무의미, 이월 불가).
        # 공제 미만이라 경로는 이익 하베스팅이지만 보유가 손실뿐 → 아무 액션 없음/비추천.
        qty, price, fx = 100.0, 100.0, 1300.0
        value = qty * price * fx
        loser = Holding("SPY", qty, price, value + 10_000_000.0)
        plan = harvest_plan([loser], fx, 1_000_000.0, fee_fn=ZERO_FEE, today=DEC)
        self.assertFalse(plan.recommended)
        self.assertEqual(len(plan.actions), 0)


class FeeGateTest(unittest.TestCase):
    def test_gain_harvest_declined_when_saving_below_fee_x_safety(self) -> None:
        # 아주 작은 미실현 이익 → 절세 < 비용×안전계수 → 비추천.
        qty, price, fx = 100.0, 100.0, 1300.0   # notional $10,000
        value = qty * price * fx
        tiny_gain = 10_000.0                      # 미실현 이익 1만원
        h = Holding("QQQ", qty, price, value - tiny_gain)
        # 편도 수수료 25bps → 라운드트립 notional 비율 큼. 절세 = 0.22×1만 = 2,200원.
        plan = harvest_plan([h], fx, 0.0, fee_fn=fee_fn_bps(25.0),
                            safety_factor=1.5, today=DEC)
        self.assertFalse(plan.recommended)
        self.assertLess(plan.tax_saved_krw, 1.5 * plan.fee_krw)

    def test_gain_harvest_recommended_when_saving_clears_gate(self) -> None:
        # 충분히 큰 이익 → 절세 ≥ 비용×안전계수 → 추천.
        h = Holding("QQQ", 100.0, 100.0,
                    100.0 * 100.0 * 1300.0 - BASIC_DEDUCTION_KRW)  # 미실현 = 공제
        plan = harvest_plan([h], 1300.0, 0.0, fee_fn=fee_fn_bps(25.0),
                            safety_factor=1.5, today=DEC)
        self.assertTrue(plan.recommended)
        self.assertGreaterEqual(plan.tax_saved_krw, 1.5 * plan.fee_krw)
        self.assertGreater(plan.net_benefit_krw, 0.0)

    def test_safety_factor_respected(self) -> None:
        # 경계에서 안전계수를 키우면 비추천으로 뒤집힌다.
        h = Holding("QQQ", 100.0, 100.0, 100.0 * 100.0 * 1300.0 - 200_000.0)
        cheap = harvest_plan([h], 1300.0, 0.0, fee_fn=fee_fn_bps(5.0),
                             safety_factor=1.5, today=DEC)
        strict = harvest_plan([h], 1300.0, 0.0, fee_fn=fee_fn_bps(5.0),
                              safety_factor=1000.0, today=DEC)
        self.assertTrue(cheap.recommended)
        self.assertFalse(strict.recommended)


if __name__ == "__main__":
    unittest.main()
