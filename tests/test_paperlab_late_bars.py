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


class _SignalSwitch(Strategy):
    """신호 전용 심볼 S(보유 안 함)의 등락으로 X/Y 중 하나를 보유(holy_grail 의 TQQQ RSI 류)."""
    name = "signal_switch"
    fill_on = "close"

    def universe(self) -> list[str]:
        return ["S", "X", "Y"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        s = history["S"]
        if len(s) < 2:
            return {"X": 1.0}
        return {"X": 1.0} if s[-1].close >= s[-2].close else {"Y": 1.0}


class _TqqqSignalSwitch(_SignalSwitch):
    """holy_grail 류: 신호 전용 심볼이 ETF(TQQQ)."""
    name = "tqqq_signal_switch"

    def universe(self) -> list[str]:
        return ["TQQQ", "X", "Y"]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        return super().decide({"S": history["TQQQ"]}, state)


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

    def test_late_signal_symbol_defers(self) -> None:
        # 감사 2026-11-28 F2: 보유하지 않는 신호 심볼 S 의 d 봉이 1세션 늦으면 d 를 처리하지 않는다.
        px = [100.0, 102.0, 101.0, 104.0, 110.0, 108.0, 111.0, 105.0]
        full = {
            "S": _by_date("S", self.dates, px, px),
            "X": _by_date("X", self.dates, px, [p * 1.01 for p in px]),
            "Y": _by_date("Y", self.dates, px, [p * 0.98 for p in px]),
        }
        late = {s: dict(m) for s, m in full.items()}
        late["S"].pop(self.dates[-1])

        lab = PaperLab(_SignalSwitch())
        ref = lab.run(lab.fresh_state(self.dates[0]), self.dates, full)

        s = lab.run(lab.fresh_state(self.dates[0]), self.dates, late)
        self.assertEqual(s["last_date"], self.dates[-2].isoformat(),
                         "신호 전용 심볼 봉이 늦은 날은 처리하지 않아야 한다")
        s = lab.run(s, self.dates, full)
        self.assertEqual(s["equity"], ref["equity"])
        self.assertEqual(s["unit"]["fills"], ref["unit"]["fills"])
        self.assertEqual(s["pending"], ref["pending"])

    def test_signal_only_symbol_two_sessions_late_carries_forward(self) -> None:
        # F15 리뷰: 신호 전용 심볼은 1세션만 대기 — 2세션 늦으면 정체 없이 캐리포워드(장부 전진).
        px = [100.0, 102.0, 101.0, 104.0, 110.0, 108.0, 111.0, 105.0]
        late = {"S": _by_date("S", self.dates[:-2], px, px),
                "X": _by_date("X", self.dates, px, px),
                "Y": _by_date("Y", self.dates, px, px)}
        lab = PaperLab(_SignalSwitch())
        s = lab.run(lab.fresh_state(self.dates[0]), self.dates, late)
        self.assertEqual(s["last_date"], self.dates[-1].isoformat())

    def test_signal_only_etf_two_sessions_late_defers(self) -> None:
        # 2차 감사 A: holy_grail 류 — 신호 전용 TQQQ(ETF)가 2세션 늦으면 3세션 규칙으로 보류하고,
        # 봉이 채워진 뒤 재실행 결과 = 완전 데이터 경로(11-09 류 오래된 신호 오염 방지).
        px = [100.0, 102.0, 101.0, 104.0, 110.0, 108.0, 111.0, 105.0]
        full = {"TQQQ": _by_date("TQQQ", self.dates, px, px),
                "X": _by_date("X", self.dates, px, [p * 1.01 for p in px]),
                "Y": _by_date("Y", self.dates, px, [p * 0.98 for p in px])}
        late = {s: dict(m) for s, m in full.items()}
        for d in self.dates[-2:]:
            late["TQQQ"].pop(d)
        lab = PaperLab(_TqqqSignalSwitch())
        ref = lab.run(lab.fresh_state(self.dates[0]), self.dates, full)
        s = lab.run(lab.fresh_state(self.dates[0]), self.dates, late)
        self.assertEqual(s["last_date"], self.dates[-3].isoformat())
        s = lab.run(s, self.dates, full)
        self.assertEqual(s["equity"], ref["equity"])
        self.assertEqual(s["unit"]["fills"], ref["unit"]["fills"])

    def test_held_symbol_two_sessions_late_still_waits(self) -> None:
        # 보유 심볼은 3세션 대기 유지(2세션 늦음 → 보류).
        lab = PaperLab(_Hold())
        s = lab.run(lab.fresh_state(self.dates[0]), self.dates, self._late("X", 2))
        self.assertEqual(s["last_date"], self.dates[-3].isoformat())

    def test_overnight_late_bar_no_false_return(self) -> None:
        lab = PaperLab(OvernightTqqq())
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

    def test_gap_in_lookback_window_excluded(self) -> None:
        # F15 리뷰: 룩백 창 안에 >5영업일 캐시 구멍(전환일 갭) → 후보 제외. 창 밖 구멍·짧은 결측은 무해.
        strat = MomTop5Ndx(["A", "B", "C", "D", "E", "F", "STALE"])
        hist = self._hist(stale_lag=0)
        hist["STALE"] = hist["STALE"][:-40] + hist["STALE"][-30:]       # 창 안 10영업일 구멍
        self.assertNotIn("STALE", strat.decide(hist, {}))
        hist2 = self._hist(stale_lag=0)
        hist2["STALE"] = hist2["STALE"][:-40] + hist2["STALE"][-36:]    # 4봉 결측 = 5영업일 갭 → 유지
        self.assertIn("STALE", strat.decide(hist2, {}))

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
