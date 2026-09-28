"""paperlab 프레임워크 회귀 테스트 — 멱등성·수수료·다음날체결(무 look-ahead)·TQQQ/SQQQ 전환·$36 소수점.

표준 라이브러리(unittest)만 사용. PYTHONPATH=src 로 실행.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.fees import TossFeeSchedule  # noqa: E402
from toss_trader.models import Candle  # noqa: E402
from toss_trader.paperlab import PaperBook, PaperLab, Strategy  # noqa: E402


# ─────────────────────────────────────────── 헬퍼: 합성 캔들/전략
def _series(symbol: str, dates: Sequence[date], opens, closes) -> list[Candle]:
    out = []
    for d, o, c in zip(dates, opens, closes):
        out.append(Candle(symbol, d, o, max(o, c), min(o, c), c, 1_000_000.0))
    return out


def _bizdays(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class ScriptStrategy(Strategy):
    """decide 호출 순번(0,1,2,...)에 따라 미리 짜둔 목표비중을 반환. 테스트용."""

    name = "script"

    def __init__(self, script: Sequence[dict[str, float]], symbols: Sequence[str],
                 fill_on: str = "close") -> None:
        self._script = list(script)
        self._symbols = list(symbols)
        self.fill_on = fill_on

    def universe(self) -> list[str]:
        return list(self._symbols)

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        i = state.get("i", 0)
        state["i"] = i + 1
        return dict(self._script[i]) if i < len(self._script) else (
            dict(self._script[-1]) if self._script else {})


# ─────────────────────────────────────────── 수수료 적용(브로커 단위)
class FeeApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fees = TossFeeSchedule()

    def test_small_buy_is_free(self) -> None:
        book = PaperBook(100.0, self.fees)
        rec = book.buy("X", budget=10.0, price=10.0, dt=date(2026, 1, 2))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["fee"], 0.0)                 # 건당 ≤$10 무료
        self.assertAlmostEqual(rec["qty"], 1.0, places=9)
        self.assertAlmostEqual(book.cash, 90.0, places=9)

    def test_large_buy_pays_commission(self) -> None:
        book = PaperBook(1000.0, self.fees)
        rec = book.buy("X", budget=1000.0, price=100.0, dt=date(2026, 1, 2))
        # budget=1000 → est_fee=trunc(1.00)=1.00 → notional=999 → fee=trunc(0.999)=0.99
        self.assertAlmostEqual(rec["fee"], 0.99, places=9)
        self.assertAlmostEqual(rec["qty"], 999.0 / 100.0, places=9)
        self.assertAlmostEqual(book.cash, 1000.0 - 999.0 - 0.99, places=9)

    def test_sell_pays_commission_plus_regulatory_min(self) -> None:
        book = PaperBook(100.0, self.fees)
        book.positions["X"] = {"qty": 0.3597, "avg": 100.0}
        book.cash = 0.0
        rec = book.sell("X", 0.3597, 100.0, dt=date(2026, 1, 3))
        # notional=35.97 → commission=trunc(0.03597)=0.03, SEC=min 0.01, TAF=min 0.01 → 0.05
        self.assertAlmostEqual(rec["fee"], 0.05, places=9)
        self.assertAlmostEqual(book.cash, 35.97 - 0.05, places=9)


# ─────────────────────────────────────────── $36 실스케일 소수점 수학
class RealScaleFractionalTest(unittest.TestCase):
    def test_36_book_full_deploy_fractional(self) -> None:
        fees = TossFeeSchedule()
        book = PaperBook(36.0, fees, start=36.0)
        fills = book.rebalance({"X": 1.0}, {"X": 100.0}, date(2026, 1, 2))
        self.assertEqual(len(fills), 1)
        # est_fee(36)=0.03 → notional=35.97 → fee=0.03 → qty=0.3597, cash=0
        self.assertAlmostEqual(book.positions["X"]["qty"], 35.97 / 100.0, places=9)
        self.assertAlmostEqual(book.cash, 0.0, places=6)
        self.assertAlmostEqual(book.total_fees, 0.03, places=9)

    def test_no_negative_cash_on_full_rebalance(self) -> None:
        fees = TossFeeSchedule()
        book = PaperBook(1000.0, fees)
        book.rebalance({"X": 1.0}, {"X": 37.123}, date(2026, 1, 2))
        self.assertGreaterEqual(book.cash, -1e-9)


# ─────────────────────────────────────────── 다음날 체결(무 look-ahead)
class NextDayFillTest(unittest.TestCase):
    def _panel(self):
        dates = _bizdays(date(2026, 1, 5), 4)
        qqq = _series("QQQ", dates, opens=[100, 110, 120, 130], closes=[101, 111, 121, 131])
        x = _series("X", dates, opens=[10, 20, 30, 40], closes=[11, 21, 31, 41])
        panel = {"QQQ": qqq, "X": x}
        by_date = {s: {c.dt: c for c in cs} for s, cs in panel.items()}
        return dates, panel, by_date

    def test_fill_uses_next_day_price_not_signal_day(self) -> None:
        dates, panel, by_date = self._panel()
        # day0 결정: X 100% → day1 체결(day1 종가 21). 이후 계속 X.
        strat = ScriptStrategy([{"X": 1.0}], ["QQQ", "X"], fill_on="close")
        lab = PaperLab(strat, unit_usd=1000.0, real_usd=36.0)
        state = lab.fresh_state(dates[0])
        state = lab.run(state, dates, by_date)

        buys = [f for f in state["unit"]["fills"] if f["side"] == "BUY"]
        self.assertTrue(buys)
        first = buys[0]
        self.assertEqual(first["dt"], dates[1].isoformat(),
                         "신호(day0 종가) 체결은 반드시 다음 거래일(day1)이어야 한다")
        self.assertAlmostEqual(first["price"], 21.0, places=9,
                               msg="체결가는 day1 종가여야 한다(당일 종가로 미리 체결 금지)")
        # day0 에쿼티 = 현금(아직 미체결) → 1000
        eq0 = state["equity"][0]
        self.assertEqual(eq0[0], dates[0].isoformat())
        self.assertAlmostEqual(eq0[1], 1000.0, places=6)

    def test_open_fill_uses_next_open(self) -> None:
        dates, panel, by_date = self._panel()
        strat = ScriptStrategy([{"X": 1.0}], ["QQQ", "X"], fill_on="open")
        lab = PaperLab(strat)
        state = lab.fresh_state(dates[0])
        state = lab.run(state, dates, by_date)
        buys = [f for f in state["unit"]["fills"] if f["side"] == "BUY"]
        self.assertAlmostEqual(buys[0]["price"], 20.0, places=9,
                               msg="fill_on=open 이면 day1 시가(20)로 체결")


# ─────────────────────────────────────────── TQQQ/SQQQ 전환
class SwitchingTest(unittest.TestCase):
    def test_tqqq_to_sqqq_switch(self) -> None:
        dates = _bizdays(date(2026, 1, 5), 5)
        flat = [100.0] * 5
        by_date = {
            "QQQ": {c.dt: c for c in _series("QQQ", dates, flat, flat)},
            "TQQQ": {c.dt: c for c in _series("TQQQ", dates, flat, flat)},
            "SQQQ": {c.dt: c for c in _series("SQQQ", dates, flat, flat)},
        }
        # day0→TQQQ, day1→SQQQ(전환), 이후 SQQQ 유지
        strat = ScriptStrategy([{"TQQQ": 1.0}, {"SQQQ": 1.0}], ["QQQ", "TQQQ", "SQQQ"])
        lab = PaperLab(strat)
        state = lab.fresh_state(dates[0])
        state = lab.run(state, dates, by_date)
        pos = state["unit"]["positions"]
        self.assertNotIn("TQQQ", pos, "SQQQ 전환 후 TQQQ 보유분은 청산돼야 한다")
        self.assertIn("SQQQ", pos)
        self.assertGreater(pos["SQQQ"]["qty"], 0.0)
        sells = [f for f in state["unit"]["fills"] if f["side"] == "SELL" and f["symbol"] == "TQQQ"]
        self.assertTrue(sells, "TQQQ→SQQQ 전환 시 TQQQ 매도 체결이 있어야 한다")


# ─────────────────────────────────────────── 멱등성
class IdempotencyTest(unittest.TestCase):
    def test_rerun_same_data_no_change(self) -> None:
        dates = _bizdays(date(2026, 1, 5), 6)
        px = [100.0, 101.0, 102.0, 101.0, 103.0, 104.0]
        by_date = {
            "QQQ": {c.dt: c for c in _series("QQQ", dates, px, px)},
            "X": {c.dt: c for c in _series("X", dates, px, px)},
        }
        strat = ScriptStrategy([{"X": 1.0}], ["QQQ", "X"])
        lab = PaperLab(strat)
        s1 = lab.run(lab.fresh_state(dates[0]), dates, by_date)
        n_fills = len(s1["unit"]["fills"])
        n_eq = len(s1["equity"])
        last = s1["last_date"]
        # 같은 캐시로 재실행 → 추가 체결·에쿼티 없음, 마지막 날짜 동일.
        s2 = lab.run(s1, dates, by_date)
        self.assertEqual(len(s2["unit"]["fills"]), n_fills)
        self.assertEqual(len(s2["equity"]), n_eq)
        self.assertEqual(s2["last_date"], last)


if __name__ == "__main__":
    unittest.main()
