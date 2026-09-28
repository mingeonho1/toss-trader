"""2026-09-28 신규 인트라데이 규칙 테스트 — Noise-Area(F3)·LETF late momentum(F6).

트리거·워밍업 게이팅·룩어헤드 금지·레버리지 변형·수수료 일관성 검증(표준 라이브러리 only).
실행: PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_intraday_shadow_newrules.py
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from toss_trader import intraday_shadow as ish
from toss_trader.intraday_shadow import (
    NOISE_CHECK_MINUTES, leverage_exit_ref, leverage_variant, letf_late_momentum_signal,
    noise_area_signal, session_move_profile, sigma_profile, to_bars, TradeSignal,
)

ET = timezone(timedelta(hours=-4))
DAY = datetime(2026, 9, 22, tzinfo=ET).date()


def _session(price_fn, *, start_min=9 * 60 + 30, end_min=16 * 60):
    rows = []
    m = start_min
    while m < end_min:
        dt = datetime(DAY.year, DAY.month, DAY.day, m // 60, m % 60, tzinfo=ET)
        rows.append((dt, float(price_fn(m))))
        m += 1
    return rows


def _minute(ts):
    dt = datetime.fromtimestamp(ts, ET)
    return dt.hour * 60 + dt.minute


SIGMA_1PCT = {m: 0.01 for m in NOISE_CHECK_MINUTES}   # UB = base × 1.01


class TestNoiseAreaTrigger(unittest.TestCase):
    def test_entry_at_first_30min_mark_above_UB_time_exit(self):
        # open=100 → UB=101(σ1%). 10:30(630)부터 102 상향돌파 → 10:30 진입, 종가 청산.
        bars = to_bars(_session(lambda m: 100.0 if m < 630 else 102.0))
        sig = noise_area_signal(bars, sigma_by_minute=SIGMA_1PCT,
                                prior_close=None, rule="noise_area_qqq")
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(_minute(sig.entry_ts), 630)      # 10:30 결정시각
        self.assertEqual(sig.entry_ref, 102.0)
        self.assertEqual(sig.exit_reason, "time")
        self.assertIn("no_vwap_boundary_only", sig.flags)
        self.assertIn("no_prior_close_ub_open_only", sig.flags)

    def test_trailing_stop_below_boundary(self):
        # 10:30 돌파(102) 후 11:00(660)에 경계 아래(100<101) → 스톱 청산.
        def pf(m):
            if m < 630:
                return 100.0
            if m < 660:
                return 102.0
            return 100.0
        sig = noise_area_signal(to_bars(_session(pf)), sigma_by_minute=SIGMA_1PCT,
                                prior_close=None, rule="noise_area_qqq")
        assert sig is not None
        self.assertEqual(sig.exit_reason, "stop")
        self.assertEqual(_minute(sig.exit_ts), 660)       # 11:00

    def test_gap_up_uses_prev_close_base(self):
        # prior_close=100, open=95(갭다운) → base=max(95,100)=100 → UB=101. 돌파 102.
        sig = noise_area_signal(to_bars(_session(lambda m: 95.0 if m < 630 else 102.0)),
                                sigma_by_minute=SIGMA_1PCT, prior_close=100.0,
                                rule="noise_area_qqq")
        assert sig is not None
        self.assertNotIn("no_prior_close_ub_open_only", sig.flags)

    def test_no_entry_when_below_boundary_all_day(self):
        sig = noise_area_signal(to_bars(_session(lambda m: 100.5)),
                                sigma_by_minute=SIGMA_1PCT, prior_close=None,
                                rule="noise_area_qqq")
        self.assertIsNone(sig)


class TestNoiseWarmup(unittest.TestCase):
    def test_sigma_profile_warmup_gate(self):
        prof = {m: 0.01 for m in NOISE_CHECK_MINUTES}
        self.assertIsNone(sigma_profile([prof] * 13))     # <14 세션 → 워밍업
        sig = sigma_profile([prof] * 14)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertAlmostEqual(sig[NOISE_CHECK_MINUTES[0]], 0.01, places=9)

    def test_none_sigma_yields_no_trade(self):
        bars = to_bars(_session(lambda m: 100.0 if m < 630 else 102.0))
        self.assertIsNone(noise_area_signal(bars, sigma_by_minute=None,
                                            prior_close=None, rule="noise_area_qqq"))

    def test_session_move_profile_shape(self):
        # open=100, 이후 110 → move(m)=|110/100-1|=0.10 (10:00 이후).
        prof = session_move_profile(to_bars(_session(lambda m: 100.0 if m < 571 else 110.0)))
        self.assertIn(600, prof)
        self.assertAlmostEqual(prof[600], 0.10, places=9)


class TestNoiseNoLookAhead(unittest.TestCase):
    def _base(self):
        return _session(lambda m: 100.0 if m < 630 else 102.0)

    def test_perturb_after_exit_keeps_trade(self):
        base = self._base()
        sig = noise_area_signal(to_bars(base), sigma_by_minute=SIGMA_1PCT,
                                prior_close=None, rule="noise_area_qqq")
        assert sig is not None
        pert = [(dt, p * 3.0 if int(dt.timestamp()) > sig.exit_ts else p) for dt, p in base]
        sig2 = noise_area_signal(to_bars(pert), sigma_by_minute=SIGMA_1PCT,
                                 prior_close=None, rule="noise_area_qqq")
        self.assertEqual(sig, sig2)

    def test_perturb_after_entry_keeps_entry(self):
        base = self._base()
        sig = noise_area_signal(to_bars(base), sigma_by_minute=SIGMA_1PCT,
                                prior_close=None, rule="noise_area_qqq")
        assert sig is not None
        pert = [(dt, p * 3.0 if int(dt.timestamp()) > sig.entry_ts else p) for dt, p in base]
        sig2 = noise_area_signal(to_bars(pert), sigma_by_minute=SIGMA_1PCT,
                                 prior_close=None, rule="noise_area_qqq")
        assert sig2 is not None
        self.assertEqual((sig.entry_ts, sig.entry_ref), (sig2.entry_ts, sig2.entry_ref))


class TestLeverageVariant(unittest.TestCase):
    def test_beta3_scales_intraday_return(self):
        # 1x +2% → 3x +6%.
        self.assertAlmostEqual(leverage_exit_ref(100.0, 102.0, 3.0), 106.0, places=9)
        sig = TradeSignal("noise_area_qqq", 1, 2, 100.0, 102.0, "time",
                          flags=("no_vwap_boundary_only",))
        lev = leverage_variant(sig, rule="noise_area_qqq_tqqq")
        self.assertEqual(lev.rule, "noise_area_qqq_tqqq")
        self.assertAlmostEqual(lev.exit_ref, 106.0, places=9)
        self.assertIn("letf_approx_3x", lev.flags)
        self.assertEqual((lev.entry_ts, lev.exit_ts), (1, 2))


class TestLetfLateMomentum(unittest.TestCase):
    def test_trigger_above_k(self):
        # prev_close=100, 14:00 가격 107 → r=7%>6% → 14:00 매수 / 15:45 매도.
        bars = to_bars(_session(lambda m: 100.0 if m < 840 else 107.0))
        sig = letf_late_momentum_signal(bars, prior_close=100.0)
        assert sig is not None
        self.assertEqual(_minute(sig.entry_ts), 14 * 60)
        self.assertEqual(_minute(sig.exit_ts), 15 * 60 + 45)
        self.assertEqual(sig.exit_reason, "time")
        self.assertIn("letf_symbol_tqqq", sig.flags)
        self.assertEqual(sig.entry_ref, 107.0)

    def test_no_trigger_below_k(self):
        # r=5% ≤ 6% → 트레이드 없음(롱온리, 숏 스킵).
        bars = to_bars(_session(lambda m: 100.0 if m < 840 else 105.0))
        self.assertIsNone(letf_late_momentum_signal(bars, prior_close=100.0))

    def test_no_prior_close_skips(self):
        bars = to_bars(_session(lambda m: 100.0 if m < 840 else 107.0))
        self.assertIsNone(letf_late_momentum_signal(bars, prior_close=None))

    def test_perturb_after_entry_keeps_signal(self):
        base = _session(lambda m: 100.0 if m < 840 else 107.0)
        sig = letf_late_momentum_signal(to_bars(base), prior_close=100.0)
        assert sig is not None
        pert = [(dt, p * 3.0 if int(dt.timestamp()) > sig.exit_ts else p) for dt, p in base]
        sig2 = letf_late_momentum_signal(to_bars(pert), prior_close=100.0)
        self.assertEqual(sig, sig2)


class TestBuildRecord(unittest.TestCase):
    def test_lev_record_tier_and_fees(self):
        sig = TradeSignal("noise_area_qqq", 1, 2, 100.0, 102.0, "time")
        lev = leverage_variant(sig, rule="noise_area_qqq_tqqq")
        rec = ish.build_trade_record(lev, symbol="TQQQ", session_date="2026-09-28",
                                     tier="leveraged")
        self.assertEqual(rec["rule"], "noise_area_qqq_tqqq")
        self.assertEqual(rec["symbol"], "TQQQ")
        self.assertEqual(set(rec["sizes"]), {"30", "1000"})
        self.assertGreater(rec["sizes"]["1000"]["gross"], 0.0)   # +6% 그로스


if __name__ == "__main__":
    unittest.main()
