"""intraday_shadow 오프라인 단위테스트 — 합성 세션으로 규칙/룩어헤드/수수료/멱등 검증.

실행: PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_intraday_shadow.py
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from toss_trader.fees import TossFeeSchedule
from toss_trader import intraday_shadow as ish
from toss_trader.intraday_shadow import (
    Bar, TradeSignal, build_trade_record, dedup_append, fill_price, gap_and_go_signal,
    momentum_signal, orb_signal, record_key, signals_for_symbol, simulate_trade, to_bars,
)

ET = timezone(timedelta(hours=-4))  # EDT, 캐시 파일과 동일 관례
DAY = datetime(2026, 9, 22, tzinfo=ET).date()


def _session(price_fn, *, start_min=9 * 60 + 30, end_min=16 * 60):
    """[start,end) ET 분봉을 price_fn(minute) 로 생성한 raw (dt, price) 리스트."""
    rows = []
    m = start_min
    while m < end_min:
        dt = datetime(DAY.year, DAY.month, DAY.day, m // 60, m % 60, tzinfo=ET)
        rows.append((dt, float(price_fn(m))))
        m += 1
    return rows


class TestBars(unittest.TestCase):
    def test_regular_flag_and_minute(self):
        raw = [
            (datetime(2026, 9, 22, 4, 0, tzinfo=ET), 100.0),   # 장전
            (datetime(2026, 9, 22, 9, 30, tzinfo=ET), 101.0),  # 정규장 시작
            (datetime(2026, 9, 22, 15, 59, tzinfo=ET), 102.0),
            (datetime(2026, 9, 22, 16, 0, tzinfo=ET), 103.0),  # 정규장 끝(제외)
        ]
        bars = to_bars(raw)
        self.assertEqual([b.regular for b in bars], [False, True, True, False])
        self.assertEqual(bars[1].minute, 9 * 60 + 30)

    def test_sorted_and_volume_default(self):
        raw = [(datetime(2026, 9, 22, 10, 0, tzinfo=ET), 5.0),
               (datetime(2026, 9, 22, 9, 30, tzinfo=ET), 4.0, 7.0)]
        bars = to_bars(raw)                       # ts 오름차순 정렬(09:30 먼저)
        self.assertLess(bars[0].ts, bars[1].ts)
        self.assertEqual(bars[0].volume, 7.0)     # 09:30 봉(vol 지정)
        self.assertEqual(bars[1].volume, 0.0)     # 10:00 봉(미지정 → 0)


class TestORB(unittest.TestCase):
    def _time_exit_session(self):
        # OR(09:30-09:34)=100, 09:35부터 101 상향돌파, 스톱 없음 → 15:55 시간청산.
        return to_bars(_session(lambda m: 100.0 if m < 575 else 101.0))

    def test_orb5_time_exit(self):
        sig = orb_signal(self._time_exit_session(), 5, rule="orb5_core")
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.exit_reason, "time")
        # 진입은 돌파 신호(09:35)의 다음 봉(09:36)에서 체결.
        self.assertEqual(datetime.fromtimestamp(sig.entry_ts, ET).minute, 36)
        # 시간청산은 15:55.
        exit_dt = datetime.fromtimestamp(sig.exit_ts, ET)
        self.assertEqual((exit_dt.hour, exit_dt.minute), (15, 55))
        self.assertEqual(sig.entry_ref, 101.0)

    def test_orb_stop(self):
        # 돌파 후 09:40에 OR_low(100) 이하로 → 다음 봉 스톱 체결.
        def pf(m):
            if m < 575:
                return 100.0
            if m < 580:
                return 101.0
            return 99.0
        sig = orb_signal(to_bars(_session(pf)), 5, rule="orb5_core")
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.exit_reason, "stop")
        self.assertEqual(sig.exit_ref, 99.0)

    def test_orb_no_breakout(self):
        # 종일 OR 범위 안 → 트레이드 없음.
        sig = orb_signal(to_bars(_session(lambda m: 100.0)), 5, rule="orb5_core")
        self.assertIsNone(sig)

    def test_orb15_uses_15min_range(self):
        # 09:40 에 100.5 로 살짝 오르지만 15분 OR_high(09:30-09:44 최대)는 그보다 큰 101.
        def pf(m):
            if m == 578:   # 09:38 스파이크
                return 101.0
            if m < 585:    # 09:30-09:44
                return 100.0
            return 100.6   # 09:45+ 는 101 미만 → 15분 OR 돌파 실패
        self.assertIsNone(orb_signal(to_bars(_session(pf)), 15, rule="orb15_core"))
        # 5분 OR(09:30-09:34)=100 기준으론 09:45의 100.6 이 돌파.
        self.assertIsNotNone(orb_signal(to_bars(_session(pf)), 5, rule="orb5_core"))


class TestNoLookAhead(unittest.TestCase):
    """룩어헤드 금지: 미래 봉 교란이 이미 내려진 결정을 바꾸면 안 된다(spec §2.3)."""

    def _base(self):
        return _session(lambda m: 100.0 if m < 575 else 101.0)

    def test_perturbing_after_exit_keeps_trade(self):
        base = self._base()
        sig = orb_signal(to_bars(base), 5, rule="orb5_core")
        assert sig is not None
        # exit_ts 이후 봉을 3배로 교란 → 트레이드 완전 동일.
        perturbed = [(dt, p * 3.0 if int(dt.timestamp()) > sig.exit_ts else p) for dt, p in base]
        sig2 = orb_signal(to_bars(perturbed), 5, rule="orb5_core")
        self.assertEqual(sig, sig2)

    def test_perturbing_after_entry_keeps_entry(self):
        base = self._base()
        sig = orb_signal(to_bars(base), 5, rule="orb5_core")
        assert sig is not None
        # entry_ts 이후 봉을 상향 교란(스톱 유발 안 함) → 진입 결정 불변(청산은 바뀔 수 있음).
        perturbed = [(dt, p * 3.0 if int(dt.timestamp()) > sig.entry_ts else p) for dt, p in base]
        sig2 = orb_signal(to_bars(perturbed), 5, rule="orb5_core")
        assert sig2 is not None
        self.assertEqual((sig.rule, sig.entry_ts, sig.entry_ref),
                         (sig2.rule, sig2.entry_ts, sig2.entry_ref))


class TestMomentum(unittest.TestCase):
    def test_signal_with_prior_close(self):
        # prior_close=100, 가격 상승 → 10:00>100 → 15:30 매수 / 15:59 매도.
        sig = momentum_signal(to_bars(_session(lambda m: 100.0 + (m - 570) * 0.01)),
                              prior_close=100.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        entry = datetime.fromtimestamp(sig.entry_ts, ET)
        exit_ = datetime.fromtimestamp(sig.exit_ts, ET)
        self.assertEqual((entry.hour, entry.minute), (15, 30))
        self.assertEqual((exit_.hour, exit_.minute), (15, 59))
        self.assertNotIn("prior_close_proxy_firstbar", sig.flags)

    def test_proxy_flag_when_no_prior_close(self):
        sig = momentum_signal(to_bars(_session(lambda m: 100.0 + (m - 570) * 0.01)),
                              prior_close=None)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertIn("prior_close_proxy_firstbar", sig.flags)

    def test_no_trade_when_flat(self):
        # 10:00 수익 ≤ 0 → 트레이드 없음.
        self.assertIsNone(momentum_signal(to_bars(_session(lambda m: 100.0)),
                                          prior_close=100.0))


class TestGapAndGo(unittest.TestCase):
    def test_gap_hold(self):
        # 시가 106, prior_close 100 → 갭 6%. 첫 15분 시가 위 유지 → 트레이드.
        def pf(m):
            if m < 585:
                return 106.0
            return 107.0
        sig = gap_and_go_signal(to_bars(_session(pf)), prior_close=100.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        entry = datetime.fromtimestamp(sig.entry_ts, ET)
        self.assertEqual((entry.hour, entry.minute), (9, 46))  # 09:45 확인 → 다음 봉

    def test_no_prior_close(self):
        self.assertIsNone(gap_and_go_signal(to_bars(_session(lambda m: 106.0)),
                                            prior_close=None))

    def test_small_gap_skipped(self):
        # 갭 2% (<5%) → 스킵.
        self.assertIsNone(gap_and_go_signal(to_bars(_session(lambda m: 102.0)),
                                            prior_close=100.0))

    def test_break_below_open_skipped(self):
        # 갭 6% 이나 첫 15분 내 시가 아래로 이탈 → 무효.
        def pf(m):
            if m == 580:   # 09:40 시가 아래
                return 105.0
            if m < 585:
                return 106.0
            return 107.0
        self.assertIsNone(gap_and_go_signal(to_bars(_session(pf)), prior_close=100.0))


class TestFees(unittest.TestCase):
    def test_fill_price_direction(self):
        # 매수는 불리(+), 매도는 유리(−). movers 15+5=20bp.
        self.assertAlmostEqual(fill_price(100.0, "BUY", "movers"), 100.0 * 1.0020, places=6)
        self.assertAlmostEqual(fill_price(100.0, "SELL", "movers"), 100.0 * 0.9980, places=6)
        # etf 1+5=6bp.
        self.assertAlmostEqual(fill_price(100.0, "BUY", "etf"), 100.0 * 1.0006, places=6)

    def test_simulate_fee_math(self):
        sched = TossFeeSchedule()
        r1000 = simulate_trade(100.0, 100.0, "etf", 1000.0, sched)
        # $1000 매수 수수료 = 0.1% = $1.00.
        self.assertAlmostEqual(r1000["buy_fee"], 1.00, places=2)
        r30 = simulate_trade(100.0, 100.0, "etf", 30.0, sched)
        self.assertAlmostEqual(r30["buy_fee"], 0.03, places=2)   # 0.1%*30
        # ≤$10 매수는 무료.
        r5 = simulate_trade(100.0, 100.0, "etf", 5.0, sched)
        self.assertEqual(r5["buy_fee"], 0.0)
        # 동일 진입=청산가여도 스프레드+수수료로 net<0, net_bps 일관.
        self.assertLess(r1000["net"], r1000["gross"])
        self.assertAlmostEqual(r1000["net_bps"], r1000["net"] / 1000.0 * 1e4, places=6)

    def test_build_record_has_both_sizes(self):
        sig = TradeSignal("orb5_core", 1, 2, 100.0, 101.0, "time")
        rec = build_trade_record(sig, symbol="qqq", session_date="2026-09-22", tier="etf")
        self.assertEqual(rec["symbol"], "QQQ")
        self.assertEqual(set(rec["sizes"]), {"30", "1000"})
        self.assertIn("net_bps", rec["sizes"]["30"])


class TestIdempotent(unittest.TestCase):
    def test_dedup_append(self):
        a = {"date": "2026-09-22", "rule": "orb5_core", "symbol": "QQQ"}
        b = {"date": "2026-09-22", "rule": "orb15_core", "symbol": "QQQ"}
        merged, added = dedup_append([], [a, b])
        self.assertEqual(added, 2)
        # 같은 (date,rule,symbol) 재삽입 → 스킵.
        merged2, added2 = dedup_append(merged, [dict(a), {**b, "extra": 1}])
        self.assertEqual(added2, 0)
        self.assertEqual(len(merged2), 2)

    def test_record_key(self):
        self.assertEqual(record_key({"date": "d", "rule": "r", "symbol": "qqq"}),
                         ("d", "r", "QQQ"))


class TestUniverse(unittest.TestCase):
    def test_core_symbol_rules(self):
        # 돌파(09:35)가 OR5(09:30-09:34) 밖 → orb5 발화. OR15(09:30-09:44) 안이라 orb15 미발화(정상).
        sess = to_bars(_session(lambda m: 100.0 if m < 575 else 101.0))
        rules = {s.rule for s in signals_for_symbol("QQQ", sess, prior_close=None)}
        self.assertIn("orb5_core", rules)
        self.assertNotIn("orb15_core", rules)
        self.assertTrue(rules <= {"orb5_core", "orb15_core", "momentum_core"})
        self.assertEqual(ish.tier_for("QQQ"), "etf")
        self.assertEqual(ish.tier_for("TQQQ"), "leveraged")

    def test_mover_symbol_rules(self):
        sess = to_bars(_session(lambda m: 100.0 if m < 575 else 101.0))
        rules = {s.rule for s in signals_for_symbol("JAGX", sess, prior_close=None)}
        self.assertTrue(rules <= {"orb5_movers", "orb15_movers", "gap_and_go_movers"})
        self.assertEqual(ish.tier_for("JAGX"), "movers")


if __name__ == "__main__":
    unittest.main()
