"""paperlab c13a 신호형 hibeta-실행 이식(ftlt/holygrail/simple/buffer_hibeta + psq) 회귀 테스트.

- remap_signal 이 experiments/c13a_signal_hibeta.py remap_leg 와 **모든 레그 범주에서 동일**(cash·PSQ 변형).
- 실행 심볼이 비레버리지(고베타 바스켓 + QQQ/BIL/SHY, psq 변형은 +PSQ)만 나온다.
- 그룹 = 실계좌 가능(비레버리지). look-ahead 없음.
- 바스켓 = c13a HIBETA 규칙(월간 리밸런스): 그 달 첫 거래일의 select_basket 을 달 내내 고정.
- 결측 가드: QQQ 봉은 있는데 종목 봉이 늦은 날엔 직전 바스켓 유지(빈 바스켓 → 전량 청산 금지).

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
from toss_trader.paperlab import classify_group  # noqa: E402
from toss_trader.research import lookahead_guard  # noqa: E402
import c13a_signal_hibeta as C13A  # noqa: E402
from toss_trader.paperlab_strategies import hibeta_signal as HS  # noqa: E402
from toss_trader.paperlab_strategies import hibeta_basket as HB  # noqa: E402


def _bizdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class RemapEquivalenceTest(unittest.TestCase):
    CASES = [
        {"TQQQ": 1.0}, {"SPXL": 1.0}, {"TECL": 1.0}, {"SOXL": 1.0}, {"FNGU": 1.0},
        {"UVXY": 1.0}, {"VIXY": 1.0},
        {"SQQQ": 1.0}, {"SOXS": 1.0}, {"SH": 1.0},
        {"BSV": 1.0}, {"BIL": 1.0}, {"SHY": 1.0}, {"TMF": 1.0}, {"AGG": 1.0},
        {"QQQ": 1.0}, {"SPY": 1.0}, {"SMH": 1.0}, {"PSQ": 1.0},
        {}, {"ZZZ_UNKNOWN": 1.0},
    ]

    def test_matches_remap_leg(self):
        for sig in self.CASES:
            for inv in ("cash", "PSQ"):
                mine = HS.remap_signal(sig, {C13A.HIBETA: 1.0}, inverse_to=inv)
                ref, _unknown = C13A.remap_leg(dict(sig), inverse_to=inv)
                self.assertEqual(mine, ref, f"sig={sig} inv={inv}: {mine} != {ref}")


def _panel(symbols, n=330, seed=17):
    rng = random.Random(seed)
    dates = _bizdays(date(2016, 1, 4), n)
    closes = {}
    for i, s in enumerate(symbols):
        base = {"SPY": 300.0, "QQQ": 200.0, "TQQQ": 30.0, "UVXY": 200.0, "BIL": 91.0,
                "SHY": 82.0, "PSQ": 12.0}.get(s, 40.0)
        px, seq = base, []
        drift = -0.0025 if s == "UVXY" else (0.00005 if s in ("BIL", "SHY") else 0.0005)
        vol = 0.0005 if s in ("BIL", "SHY") else (0.05 if s == "UVXY" else 0.02)
        for t in range(n):
            px *= math.exp(drift + 0.02 * math.sin(2 * math.pi * t / (90 + i)) + rng.gauss(0, vol))
            seq.append(max(0.5, px))
        closes[s] = seq
    return dates, closes


class HibetaSignalWiringTest(unittest.TestCase):
    def _run(self, strat, dates, closes, symbols):
        st, out = {}, []
        hist = {s: [] for s in symbols}
        for t, d in enumerate(dates):
            for s in symbols:
                px = closes[s][t]
                hist[s].append(Candle(s, d, px, px, px, px, 1e6))
            out.append(strat.decide(hist, st))
        return out

    def test_ftlt_hibeta_exec_symbols_nonleveraged(self):
        cands = ["B20", "B25", "B30", "B15", "B18", "B22", "B24", "B26", "B28", "B12", "B14"]
        strat = HS.FtltHibeta(cands)
        syms = strat.universe()
        dates, closes = _panel(syms)
        # 고베타 후보는 QQQ 상관 고베타로 구성.
        rng = random.Random(2)
        rq = [0.0] + [rng.gauss(0.0004, 0.011) for _ in range(len(dates) - 1)]
        q = [200.0]
        for t in range(1, len(dates)):
            q.append(q[-1] * (1 + rq[t]))
        closes["QQQ"] = q
        for i, c in enumerate(cands):
            b = 1.0 + 0.1 * i
            cc = [50.0]
            for t in range(1, len(dates)):
                cc.append(max(1.0, cc[-1] * (1 + b * rq[t])))
            closes[c] = cc
        out = self._run(strat, dates, closes, syms)
        allowed = set(cands) | {"QQQ", "BIL", "SHY"}
        seen_basket = False
        for w in out:
            for s in w:
                self.assertIn(s, allowed, f"비허용(레버리지) 실행 심볼 노출: {s}")
            if len([s for s in w if s in cands]) >= 2:
                seen_basket = True
        self.assertTrue(seen_basket, "위험선호 국면에서 고베타 바스켓이 실행돼야 한다")

    def test_psq_variant_allows_psq(self):
        strat = HS.FtltHibetaPsq(["B20", "B25", "B30"])
        self.assertEqual(strat._inverse_to, "PSQ")
        self.assertIn("PSQ", strat.universe())

    def test_group_is_retail_despite_leveraged_signal_syms(self):
        for cls in (HS.FtltHibeta, HS.HolygrailHibeta, HS.SimpleHibeta, HS.BufferHibeta):
            strat = cls(["B20", "B25"])
            self.assertEqual(classify_group(strat.universe(), getattr(strat, "group", None)),
                             "retail", f"{cls.__name__} 는 실계좌(비레버리지) 그룹이어야")

    def test_buffer_hibeta_no_lookahead(self):
        cands = ["B20", "B25", "B30"]
        strat = HS.BufferHibeta(cands)
        syms = strat.universe()
        dates, closes = _panel(syms, n=270)

        def sig(panel_closes, ds):
            s2 = HS.BufferHibeta(cands)
            st, out = {}, []
            hist = {s: [] for s in syms}
            for t, d in enumerate(ds):
                for s in syms:
                    px = panel_closes[s][t]
                    hist[s].append(Candle(s, d, px, px, px, px, 1e6))
                out.append(s2.decide(hist, st))
            return out
        self.assertTrue(lookahead_guard(sig, closes, dates))


class _AlwaysRiskOn(HS._HibetaSignal):
    name = "always_on_hibeta"
    _signal_syms = ["TQQQ"]

    def _signal(self, history, state):
        return {"TQQQ": 1.0}


def _beta_panel(cands, n=330, seed=5):
    """QQQ + 베타가 촘촘한(1.0~1.3) 후보 + 고유잡음 → 일별 베타 순위가 자주 바뀌는 패널."""
    rng = random.Random(seed)
    dates = _bizdays(date(2016, 1, 4), n)
    rq = [0.0] + [rng.gauss(0.0004, 0.011) for _ in range(n - 1)]
    closes = {"QQQ": [200.0]}
    for t in range(1, n):
        closes["QQQ"].append(closes["QQQ"][-1] * (1 + rq[t]))
    for i, c in enumerate(cands):
        b = 1.0 + 0.3 * i / max(1, len(cands) - 1)
        cc = [50.0]
        for t in range(1, n):
            cc.append(max(6.0, cc[-1] * (1 + b * rq[t] + rng.gauss(0, 0.01))))
        closes[c] = cc
    return dates, closes


class C13aMonthlyBasketTest(unittest.TestCase):
    CANDS = [f"B{i:02d}" for i in range(14)]

    def _hist_upto(self, syms, dates, closes, t, skip=()):
        hist = {s: [] for s in syms}
        for k in range(t + 1):
            for s in syms:
                if k == t and s in skip:
                    continue
                px = closes[s][k]
                hist[s].append(Candle(s, dates[k], px, px, px, px, 1e6))
        return hist

    def test_basket_fixed_per_month_equals_first_trading_day_selection(self):
        strat = _AlwaysRiskOn(self.CANDS)
        dates, closes = _beta_panel(self.CANDS)
        for s in ("TQQQ", "BIL", "SHY", "PSQ"):
            closes[s] = [10.0] * len(dates)
        syms = strat.universe()
        st: dict = {}
        hist = {s: [] for s in syms}
        month_first: dict = {}
        daily_would_change = False
        for t, d in enumerate(dates):
            for s in syms:
                px = closes[s][t]
                hist[s].append(Candle(s, d, px, px, px, px, 1e6))
            out = strat.decide(hist, st)
            if not out:
                continue
            key = (d.year, d.month)
            if key not in month_first:
                month_first[key] = out
                # c13a: 바스켓 = 그 달 첫(유효) 거래일 select_basket.
                self.assertEqual(out, HB.select_basket(hist, strat._candidates))
            else:
                self.assertEqual(out, month_first[key], f"{d}: 달 중간에 바스켓이 바뀜")
                if HB.select_basket(hist, strat._candidates) != out:
                    daily_would_change = True
        self.assertGreaterEqual(len(month_first), 3)
        self.assertTrue(daily_would_change, "테스트 패널이 일별 재선택과 월간 고정을 구분하지 못함")

    def test_missing_stock_bars_hold_previous_basket(self):
        dates, closes = _beta_panel(self.CANDS)
        for s in ("TQQQ", "BIL", "SHY", "PSQ"):
            closes[s] = [10.0] * len(dates)
        t_new = next(t for t in range(300, len(dates)) if dates[t].month != dates[t - 1].month)
        for strat in (_AlwaysRiskOn(self.CANDS), HB.HibetaBasket(self.CANDS)):
            syms = strat.universe()
            st: dict = {}
            prev = strat.decide(self._hist_upto(syms, dates, closes, t_new - 1), st)
            self.assertTrue(prev)
            # 새 달 첫 거래일: QQQ 봉만 있고 종목 봉은 늦음 → 직전 바스켓 유지.
            held = strat.decide(self._hist_upto(syms, dates, closes, t_new, skip=self.CANDS), st)
            self.assertEqual(held, prev, f"{type(strat).__name__}: 결측일에 바스켓이 바뀜/비워짐")
            # 다음 날 종목 봉 도착 → 새 달 바스켓 재선택(재시도).
            h2 = self._hist_upto(syms, dates, closes, t_new + 1)
            self.assertEqual(strat.decide(h2, st), HB.select_basket(h2, strat._candidates))


if __name__ == "__main__":
    unittest.main()
