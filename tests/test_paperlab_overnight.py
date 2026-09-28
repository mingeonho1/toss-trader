"""2026-09-28 신규 일간 페이퍼 전략 테스트 — overnight(G1)·ep_gap_swing(F5).

오버나이트 체결 정확성(종가매수/익일시가매도, 장중수익 미포함), ≤$10 분할 무료($36 장부),
멱등, ep_gap 갭 트리거·다음시가 진입 검증. 표준 라이브러리(unittest) only.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.models import Candle  # noqa: E402
from toss_trader.paperlab import PaperLab  # noqa: E402
from toss_trader.paperlab_strategies.ep_gap_swing import EpGapSwing  # noqa: E402
from toss_trader.paperlab_strategies.overnight import OvernightQqq  # noqa: E402


def _bizdays(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _candles(symbol, dates, opens, closes, vols=None):
    out = []
    for i, (d, o, c) in enumerate(zip(dates, opens, closes)):
        v = vols[i] if vols else 1_000_000.0
        out.append(Candle(symbol, d, o, max(o, c), min(o, c), c, v))
    return out


class OvernightFillTest(unittest.TestCase):
    def _run(self, book_real=36.0):
        # day0 close=100 → 매수. day1 open=110(밤 +10%) close=90(장중 급락, 미보유).
        # day2 open=99(밤 +10% vs 90) close=99. day3 청산용.
        dates = _bizdays(date(2026, 9, 28), 4)
        opens = [100.0, 110.0, 99.0, 100.0]
        closes = [100.0, 90.0, 99.0, 100.0]
        by_date = {"QQQ": {c.dt: c for c in _candles("QQQ", dates, opens, closes)}}
        lab = PaperLab(OvernightQqq(), unit_usd=1000.0, real_usd=book_real)
        state = lab.run(lab.fresh_state(dates[0]), dates, by_date)
        return dates, state

    def test_buy_at_close_sell_at_next_open(self):
        dates, state = self._run()
        fills = state["unit"]["fills"]
        buys = [f for f in fills if f["side"] == "BUY"]
        sells = [f for f in fills if f["side"] == "SELL"]
        self.assertTrue(buys and sells)
        self.assertAlmostEqual(buys[0]["price"], 100.0, places=9)   # 종가(day0) 매수
        self.assertAlmostEqual(sells[0]["price"], 110.0, places=9)  # 익일 시가(day1) 매도

    def test_overnight_captures_gain_not_intraday_drop(self):
        # day1 종가=90(장중 -18%)이지만 오버나이트는 시가 110에 청산 → +10% 반영, 장중 급락 미노출.
        dates, state = self._run()
        eq = {r[0]: r[1] for r in state["equity"]}
        self.assertGreater(eq[dates[1].isoformat()], 1000.0 * 1.05,
                           "야간 +10% 가 반영되어야 한다(장중 -18% 무관)")

    def test_36_book_buys_are_free(self):
        # $36 매수는 ≤$10 분할로 전부 무료.
        _, state = self._run(book_real=36.0)
        buy_fees = [f["fee"] for f in state["real"]["fills"] if f["side"] == "BUY"]
        self.assertTrue(buy_fees)
        self.assertTrue(all(abs(x) < 1e-12 for x in buy_fees), "≤$10 분할 매수는 무료")

    def test_idempotent_rerun(self):
        dates, s1 = self._run()
        lab = PaperLab(OvernightQqq(), unit_usd=1000.0, real_usd=36.0)
        by_date = {"QQQ": {c.dt: c for c in _candles(
            "QQQ", dates, [100.0, 110.0, 99.0, 100.0], [100.0, 90.0, 99.0, 100.0])}}
        s2 = lab.run(s1, dates, by_date)
        self.assertEqual(len(s2["unit"]["fills"]), len(s1["unit"]["fills"]))
        self.assertEqual(len(s2["equity"]), len(s1["equity"]))
        self.assertEqual(s2["last_date"], s1["last_date"])


class EpGapSwingTest(unittest.TestCase):
    def _panel(self, gap_open):
        n_flat = 63
        dates = _bizdays(date(2026, 1, 5), n_flat + 2)
        opens = [100.0] * n_flat + [gap_open, 113.0]
        closes = [100.0] * n_flat + [gap_open, 113.0]
        vols = [1_000_000.0] * n_flat + [3_000_000.0, 1_000_000.0]
        cs = _candles("AAA", dates, opens, closes, vols)
        return dates, {"AAA": {c.dt: c for c in cs}}

    def test_gap_entry_next_open(self):
        # 갭 +12%(112/100), 거래량 3×, 비관심(60일수익 0) → 갭 다음날 시가(113) 매수.
        dates, by_date = self._panel(gap_open=112.0)
        lab = PaperLab(EpGapSwing(["AAA"]))
        state = lab.run(lab.fresh_state(dates[0]), dates, by_date)
        buys = [f for f in state["unit"]["fills"] if f["side"] == "BUY"]
        self.assertTrue(buys, "갭 조건 충족 시 진입해야 한다")
        self.assertAlmostEqual(buys[0]["price"], 113.0, places=9)   # 다음 시가 진입
        self.assertEqual(buys[0]["dt"], dates[-1].isoformat())

    def test_small_gap_no_entry(self):
        # 갭 +5% (<10%) → 진입 없음.
        dates, by_date = self._panel(gap_open=105.0)
        lab = PaperLab(EpGapSwing(["AAA"]))
        state = lab.run(lab.fresh_state(dates[0]), dates, by_date)
        buys = [f for f in state["unit"]["fills"] if f["side"] == "BUY"]
        self.assertFalse(buys)


if __name__ == "__main__":
    unittest.main()
