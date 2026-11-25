"""감사 2026-11-25 R1 회귀 — 늦게 들어온 봉(장부 구멍)·mom_top5_ndx 멈춘 캐시.

- 엔진: 보유/목표 심볼의 오늘 봉이 늦으면(≤3세션) 그 날을 처리하지 않고 다음 실행에서 계산 →
  봉이 채워진 뒤 재실행 결과 = 처음부터 완전한 데이터로 돌린 결과(일반/오버나이트 경로 모두).
- 오래 멈춘(죽은) 심볼은 대기하지 않는다(영구 정체 방지).
- mom_top5_ndx: 마지막 봉이 3세션 넘게 뒤처진 심볼은 후보에서 빠지고, 그런 심볼이 든 월 캐시는 재계산.

표준 라이브러리(unittest)만. PYTHONPATH=src.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.models import Candle  # noqa: E402
from toss_trader.paperlab import PaperLab, Strategy  # noqa: E402
from toss_trader.paperlab_strategies.mom_top5_ndx import MomTop5Ndx  # noqa: E402
from toss_trader.paperlab_strategies.overnight import OvernightTqqq  # noqa: E402


def _bizdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _by_date(sym: str, dates: Sequence[date], opens, closes) -> dict[date, Candle]:
    return {d: Candle(sym, d, o, max(o, c), min(o, c), c, 1e6) for d, o, c in zip(dates, opens, closes)}


class _Hold(Strategy):
    name = "hold_x"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["QQQ", "X"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return {"X": 1.0}


class LateBarEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = _bizdays(date(2026, 11, 2), 8)
        px = [100.0, 102.0, 101.0, 104.0, 110.0, 108.0, 111.0, 112.0]
        self.full = {
            "QQQ": _by_date("QQQ", self.dates, px, px),
            "X": _by_date("X", self.dates, px, [p * 1.01 for p in px]),
            "TQQQ": _by_date("TQQQ", self.dates, px, [p * 0.99 for p in px]),
        }

    def _late(self, sym: str, k: int) -> dict:
        """sym 의 마지막 k 개 봉이 아직 안 들어온 캐시."""
        late = {s: dict(m) for s, m in self.full.items()}
        for d in self.dates[-k:]:
            late[sym].pop(d)
        return late

    def test_late_held_bar_defers_then_matches_full_path(self) -> None:
        lab = PaperLab(_Hold())
        ref = lab.run(lab.fresh_state(self.dates[0]), self.dates, self.full)

        s = lab.run(lab.fresh_state(self.dates[0]), self.dates, self._late("X", 2))
        self.assertEqual(s["last_date"], self.dates[-3].isoformat(),
                         "보유 심볼 봉이 늦은 날은 처리(마크·last_date 진행)하지 않아야 한다")
        s = lab.run(s, self.dates, self.full)          # 다음 실행: 봉이 채워짐
        self.assertEqual(s["equity"], ref["equity"])
        self.assertEqual(s["unit"]["fills"], ref["unit"]["fills"])

    def test_overnight_late_bar_no_false_return(self) -> None:
        lab = PaperLab(OvernightTqqq())
        ref = lab.run(lab.fresh_state(self.dates[0]), self.dates, self.full)

        s = lab.run(lab.fresh_state(self.dates[0]), self.dates, self._late("TQQQ", 1))
        self.assertEqual(s["last_date"], self.dates[-2].isoformat())
        s = lab.run(s, self.dates, self.full)
        self.assertEqual(s["equity"], ref["equity"])
        self.assertEqual(s["unit"]["fills"], ref["unit"]["fills"])

    def test_late_signal_symbol_defers(self) -> None:
        # 감사 2026-11-26: 신호 전용 심볼(보유 안 함)의 봉이 늦어도 그 날은 보류해야 한다(holy_grail TQQQ RSI).
        class _Signal(_Hold):
            name = "signal_tqqq"

            def universe(self) -> list[str]:
                return ["QQQ", "X", "TQQQ"]

            def decide(self, history, state):
                t = history["TQQQ"]
                return {"X": 1.0} if len(t) >= 2 and t[-1].close > t[-2].close else {"QQQ": 1.0}

        lab = PaperLab(_Signal())
        ref = lab.run(lab.fresh_state(self.dates[0]), self.dates, self.full)
        s = lab.run(lab.fresh_state(self.dates[0]), self.dates, self._late("TQQQ", 1))
        self.assertEqual(s["last_date"], self.dates[-2].isoformat())
        s = lab.run(s, self.dates, self.full)
        self.assertEqual(s["equity"], ref["equity"])
        self.assertEqual(s["unit"]["fills"], ref["unit"]["fills"])

    def test_dead_symbol_does_not_stall(self) -> None:
        # X 캐시가 day1 에서 멈춤(지연 6세션 > 3) → 대기하지 않고 직전가 캐리로 끝까지 진행.
        lab = PaperLab(_Hold())
        s = lab.run(lab.fresh_state(self.dates[0]), self.dates, self._late("X", 6))
        self.assertEqual(s["last_date"], self.dates[-1].isoformat())


class MomStaleFilterTest(unittest.TestCase):
    def _hist(self, stale_lag: int) -> dict[str, list[Candle]]:
        dates = _bizdays(date(2025, 1, 2), 300)
        hist: dict[str, list[Candle]] = {}
        for i, s in enumerate(["A", "B", "C", "D", "E", "F", "STALE"]):
            g = 1.0 + 0.0005 * (i + 1)           # STALE 가 모멘텀 1위
            seq, px = [], 50.0
            for d in dates:
                px *= g
                seq.append(Candle(s, d, px, px, px, px, 1e6))
            hist[s] = seq[:-stale_lag] if (s == "STALE" and stale_lag) else seq
        return hist

    def test_stale_symbol_excluded(self) -> None:
        strat = MomTop5Ndx(["A", "B", "C", "D", "E", "F", "STALE"])
        w = strat.decide(self._hist(stale_lag=10), {})
        self.assertNotIn("STALE", w)
        self.assertEqual(len(w), 5)

    def test_recent_lag_within_3_sessions_kept(self) -> None:
        strat = MomTop5Ndx(["A", "B", "C", "D", "E", "F", "STALE"])
        self.assertIn("STALE", strat.decide(self._hist(stale_lag=3), {}))

    def test_cached_month_with_stale_symbol_recomputed(self) -> None:
        strat = MomTop5Ndx(["A", "B", "C", "D", "E", "F", "STALE"])
        hist = self._hist(stale_lag=10)
        today = hist["A"][-1].dt
        st = {"period": [today.year, today.month],
              "weights": {s: 0.2 for s in ["STALE", "F", "E", "D", "C"]}}
        w = strat.decide(hist, st)
        self.assertNotIn("STALE", w)
        self.assertNotIn("STALE", st["weights"])


if __name__ == "__main__":
    unittest.main()
