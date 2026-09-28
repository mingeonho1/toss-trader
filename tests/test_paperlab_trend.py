"""paperlab Trend/Vol 이식(lrs200·sma200_buffer·nine_sig) + 엔진 확장(MOC·적립) 회귀 테스트.

- lrs200_tqqq == experiments sig_a1(밴드0), sma200_buffer_tqqq == sig_a2, 픽스처 전 구간 목표 동일.
- nine_sig 분기 리밸런스 산술 == simulate_9sig qset 블록의 독립 재현(파라미터 동결).
- 엔진: exec_lag=0(MOC) 당일 종가 체결, real_monthly_contribution 월초 실장부 적립(단위장부 무영향).

표준 라이브러리(unittest)만. PYTHONPATH=src.
"""
from __future__ import annotations

import math
import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader.models import Candle  # noqa: E402
from toss_trader.paperlab import PaperLab, Strategy  # noqa: E402
from toss_trader.research import lookahead_guard  # noqa: E402
import c12_trend_vol as TV  # noqa: E402
from toss_trader.paperlab_strategies import lrs200, sma200_buffer, nine_sig  # noqa: E402


def _bizdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _walk(rng, n, s0, drift, vol, wave=0.02, period=90):
    px, out = s0, []
    for t in range(n):
        px *= math.exp(drift + wave * math.sin(2 * math.pi * t / period) + rng.gauss(0, vol))
        out.append(max(0.5, px))
    return out


def _same(a, b, tol=1e-9):
    a, b = a or {}, b or {}
    return all(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) <= tol for k in set(a) | set(b))


class A1A2EquivalenceTest(unittest.TestCase):
    def _run_incremental(self, decide, dates, qcloses):
        state, out = {}, []
        hist = {"QQQ": []}
        for t, d in enumerate(dates):
            hist["QQQ"].append(Candle("QQQ", d, qcloses[t], qcloses[t], qcloses[t], qcloses[t], 1e6))
            out.append(decide(hist, state))
        return out

    def test_lrs200_matches_sig_a1(self):
        rng = random.Random(11)
        dates = _bizdays(date(2016, 1, 4), 320)
        q = _walk(rng, 320, 200.0, 0.0005, 0.012, wave=0.03)
        tw_ref, _ = TV.sig_a1({"QQQ": q}, dates)          # 밴드0
        mine = self._run_incremental(lambda h, s: lrs200.decide_lrs(h, s, "TQQQ"), dates, q)
        bad = [t for t in range(len(dates)) if not _same(tw_ref[t], mine[t])]
        self.assertEqual(bad, [], f"lrs200≠sig_a1 at {bad[:5]}")
        self.assertTrue(any(w for w in mine) and any(not w for w in mine), "양 상태 모두 나와야")

    def test_sma200_buffer_matches_sig_a2(self):
        rng = random.Random(13)
        dates = _bizdays(date(2016, 1, 4), 340)
        q = _walk(rng, 340, 200.0, 0.0005, 0.013, wave=0.035)
        tw_ref, _ = TV.sig_a2({"QQQ": q}, dates)          # entry+5/exit-3
        mine = self._run_incremental(lambda h, s: sma200_buffer.decide_buffer(h, s), dates, q)
        bad = [t for t in range(len(dates)) if not _same(tw_ref[t], mine[t])]
        self.assertEqual(bad, [], f"buffer≠sig_a2 at {bad[:5]}")

    def test_lrs200_no_lookahead(self):
        rng = random.Random(5)
        dates = _bizdays(date(2016, 1, 4), 260)
        q = _walk(rng, 260, 200.0, 0.0005, 0.012)

        def sig(panel, ds):
            return self._run_incremental(lambda h, s: lrs200.decide_lrs(h, s, "TQQQ"), ds, panel["QQQ"])
        self.assertTrue(lookahead_guard(sig, {"QQQ": q}, dates))


# ── 9Sig: 분기 리밸런스 산술의 독립 재현(simulate_9sig qset 블록 transcription) ──
def _ref_ninesig_step(v_tq, v_ag, signal_line, ignore_sells, qcloses, prev_qclose, close_t,
                      growth=0.09):
    recent = qcloses[-8:] + [close_t]
    thirty_down = close_t <= 0.70 * max(recent) if recent else False
    spike = (prev_qclose is not None and prev_qclose > 0 and close_t / prev_qclose - 1.0 >= 1.0)
    target = signal_line * (1.0 + growth)
    if v_tq > target:
        if ignore_sells > 0:
            ignore_sells -= 1
        else:
            sell = v_tq - target
            v_tq -= sell
            v_ag += sell
            if v_ag > 0.30 * (v_tq + v_ag):
                v_tq = 0.6 * (v_tq + v_ag)
                v_ag = 0.4 * (v_tq + v_ag)
                signal_line = v_tq
                target = signal_line
    elif v_tq < target:
        need = target - v_tq
        cap = 0.9 * v_ag
        buy = min(need, cap)
        port2 = v_tq + v_ag
        if (v_ag - buy) < 0.10 * port2:
            buy = max(0.0, v_ag - 0.10 * port2)
        if buy > 0:
            v_tq += buy
            v_ag -= buy
    signal_line = target
    if thirty_down:
        ignore_sells = 2
    if spike and not thirty_down:
        v_tq = 0.6 * (v_tq + v_ag)
        v_ag = 0.4 * (v_tq + v_ag)
        signal_line = v_tq
    qcloses.append(close_t)
    prev_qclose = close_t
    return v_tq, v_ag, signal_line, ignore_sells, qcloses, prev_qclose


class NineSigStepTest(unittest.TestCase):
    def test_rebalance_matches_reference(self):
        rng = random.Random(21)
        m = {"v_tq": 0.6, "v_ag": 0.4, "signal_line": 0.6, "ignore_sells": 0,
             "qcloses": [], "prev_qclose": None}
        ref = (0.6, 0.4, 0.6, 0, [], None)
        close = 100.0
        for _ in range(48):                               # 12년치 분기
            f_tq = math.exp(rng.gauss(0, 0.25))
            f_ag = math.exp(rng.gauss(0, 0.02))
            m["v_tq"] *= f_tq
            m["v_ag"] *= f_ag
            rtq, rag, rsl, ris, rqc, rpq = ref
            rtq *= f_tq
            rag *= f_ag
            close *= (1.0 + rng.uniform(-0.35, 0.6))
            nine_sig.ninesig_rebalance(m, close)
            ref = _ref_ninesig_step(rtq, rag, rsl, ris, list(rqc), rpq, close)
            self.assertAlmostEqual(m["v_tq"], ref[0], places=10)
            self.assertAlmostEqual(m["v_ag"], ref[1], places=10)
            self.assertAlmostEqual(m["signal_line"], ref[2], places=10)
            self.assertEqual(m["ignore_sells"], ref[3])
            self.assertEqual(m["qcloses"], ref[4])

    def test_nine_sig_holds_between_quarters(self):
        # 분기 밖에서는 목표가 불변(엔진이 홀드), 분기 경계에서만 새 목표.
        dates = _bizdays(date(2021, 1, 4), 200)           # Jan~Oct 여러 분기 경계
        rng = random.Random(4)
        tq = _walk(rng, 200, 40.0, 0.001, 0.02)
        ag = _walk(rng, 200, 100.0, 0.0001, 0.003)
        st = {}
        strat = nine_sig.NineSig()
        prev = None
        changes = 0
        hist = {"TQQQ": [], "AGG": []}
        for t, d in enumerate(dates):
            hist["TQQQ"].append(Candle("TQQQ", d, tq[t], tq[t], tq[t], tq[t], 1e6))
            hist["AGG"].append(Candle("AGG", d, ag[t], ag[t], ag[t], ag[t], 1e6))
            w = strat.decide(hist, st)
            if prev is not None and not _same(prev, w):
                changes += 1
            prev = w
        # 초기 1회 + 분기 경계에서만 변함(2021 Q2/Q3/Q4 진입 → 3~4회 수준), 매일 변하지 않음.
        self.assertLessEqual(changes, 6)
        self.assertGreaterEqual(changes, 2)


class EngineExtTest(unittest.TestCase):
    def _panel(self, dates, symbols, px):
        return {s: {c.dt: c for c in
                    [Candle(s, d, px[i], px[i], px[i], px[i], 1e6) for i, d in enumerate(dates)]}
                for s in symbols}

    def test_moc_fills_same_day_close(self):
        class AlwaysX(Strategy):
            name = "moc_x"
            exec_lag = 0

            def universe(self):
                return ["X"]

            def decide(self, history, state):
                return {"X": 1.0} if history.get("X") else {}

        dates = _bizdays(date(2026, 1, 5), 4)
        px = [10.0, 20.0, 30.0, 40.0]
        by_date = self._panel(dates, ["X"], px)
        lab = PaperLab(AlwaysX())
        state = lab.run(lab.fresh_state(dates[0]), dates, by_date)
        buys = [f for f in state["unit"]["fills"] if f["side"] == "BUY"]
        self.assertTrue(buys)
        self.assertEqual(buys[0]["dt"], dates[0].isoformat(), "MOC는 당일(day0) 체결")
        self.assertAlmostEqual(buys[0]["price"], 10.0, places=9, msg="당일 종가로 체결")

    def test_monthly_contribution_real_book_only(self):
        class Cash(Strategy):
            name = "contrib_cash"
            real_monthly_contribution = 35.0

            def universe(self):
                return ["QQQ"]

            def decide(self, history, state):
                return {}                                 # 항상 현금 → 적립만 쌓임

        dates = _bizdays(date(2026, 1, 26), 12)           # 1월 말 → 2월 초 경계 포함
        px = [100.0] * len(dates)
        by_date = self._panel(dates, ["QQQ"], px)
        lab = PaperLab(Cash(), unit_usd=1000.0, real_usd=36.0)
        state = lab.run(lab.fresh_state(dates[0]), dates, by_date, contribute=True)
        self.assertAlmostEqual(state["real"]["cash"], 36.0 + 35.0, places=6)   # 1회 월초 적립
        self.assertAlmostEqual(state["unit"]["cash"], 1000.0, places=6)        # 단위장부 무적립
        self.assertAlmostEqual(state["real_contributed"], 35.0, places=6)

    def test_contribution_disabled_in_backfill_mode(self):
        class Cash(Strategy):
            name = "contrib_off"
            real_monthly_contribution = 35.0

            def universe(self):
                return ["QQQ"]

            def decide(self, history, state):
                return {}

        dates = _bizdays(date(2026, 1, 26), 12)
        px = [100.0] * len(dates)
        by_date = self._panel(dates, ["QQQ"], px)
        lab = PaperLab(Cash(), real_usd=36.0)
        state = lab.run(lab.fresh_state(dates[0]), dates, by_date, contribute=False)
        self.assertAlmostEqual(state["real"]["cash"], 36.0, places=6)
        self.assertAlmostEqual(state["real_contributed"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
