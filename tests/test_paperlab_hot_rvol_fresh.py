"""감사 2026-11-28 R4 회귀 — hot_rvol_swing decide() 신선도 검사.

정체(캐시가 멈춘) 심볼의 마지막 봉이 조건을 만족해도 진입하면 안 된다. 그 심볼은 체결가가 없어
pending 이 영원히 미체결(부분체결 금지)로 남고, 진짜 신호(AAPL)까지 막아 장부가 동결된다.

표준 라이브러리(unittest)만. PYTHONPATH=src.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.models import Candle  # noqa: E402
from toss_trader.paperlab import PaperLab  # noqa: E402
from toss_trader.paperlab_strategies.hot_rvol_swing import HotRvolSwing  # noqa: E402


def _bizdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bars(sym: str, dates, pass_day: date | None) -> dict[date, Candle]:
    """평탄(100) 봉 + pass_day 에 gain 8%·종가=고가·RVOL 5 신호 봉."""
    out = {d: Candle(sym, d, 100.0, 100.5, 99.5, 100.0, 1e6) for d in dates}
    if pass_day is not None:
        out[pass_day] = Candle(sym, pass_day, 101.0, 108.0, 100.0, 108.0, 5e6)
    return out


class HotRvolFreshnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = _bizdays(date(2026, 8, 3), 50)
        self.strat = HotRvolSwing(universe=["A", "AAPL"])

    def test_decide_skips_stale_symbol_but_takes_fresh(self) -> None:
        d = self.dates
        stale = _bars("A", d[:30], pass_day=d[29])               # 마지막 봉(d[29])이 조건 충족, 이후 정체
        fresh = _bars("AAPL", d[:41], pass_day=d[40])            # 오늘(d[40]) 진짜 신호
        hist = {"A": [stale[x] for x in sorted(stale)], "AAPL": [fresh[x] for x in sorted(fresh)]}
        self.assertEqual(self.strat.decide(hist, {}), {"AAPL": 1.0})
        # 신선 신호가 없으면 정체 봉만으로는 진입하지 않는다.
        hist["AAPL"] = [_bars("AAPL", d[:41], None)[x] for x in d[:41]]
        self.assertEqual(self.strat.decide(hist, {}), {})

    def test_decide_skips_first_bar_after_cache_gap(self) -> None:
        # F15 리뷰: 캐시 구멍(>5영업일) 직후 첫 봉의 gain 은 여러 날 수익 → 신호 금지. 5영업일 이내는 허용.
        d = self.dates
        bars = _bars("AAPL", d[:41], pass_day=d[40])
        gap = [bars[x] for x in d[:30]] + [bars[d[40]]]                # d[29]→d[40] = 11영업일 갭
        self.assertEqual(self.strat.decide({"AAPL": gap}, {}), {})
        ok = [bars[x] for x in d[:35]] + [bars[d[40]]]                 # d[34]→d[40] = 6영업일
        self.assertEqual(self.strat.decide({"AAPL": ok}, {}), {})
        ok = [bars[x] for x in d[:36]] + [bars[d[40]]]                 # d[35]→d[40] = 5영업일 → 허용
        self.assertEqual(self.strat.decide({"AAPL": ok}, {}), {"AAPL": 1.0})

    def test_engine_stale_pass_no_pending_and_fresh_signal_fills(self) -> None:
        d = self.dates
        by_date = {"A": _bars("A", d[:30], pass_day=d[29]),     # 포워드 시작(d[30]) 전에 정체
                   "AAPL": _bars("AAPL", d, pass_day=d[40])}
        lab = PaperLab(self.strat)
        st = lab.run(lab.fresh_state(d[30]), d, by_date)
        fills = [(f["symbol"], f["side"]) for f in st["unit"]["fills"]]
        self.assertNotIn("A", {s for s, _ in fills})
        self.assertNotIn("A", st["pending"] or {})
        self.assertNotIn("A", st["strategy_state"].get("holds") or {})
        self.assertIn(("AAPL", "BUY"), fills)                  # d[41] 시가 진입
        self.assertIn(("AAPL", "SELL"), fills)                 # 5세션 보유 후 청산


if __name__ == "__main__":
    unittest.main()
