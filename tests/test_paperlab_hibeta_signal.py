"""paperlab c13a 신호형 hibeta-실행 이식(ftlt/holygrail/simple/buffer_hibeta + psq) 회귀 테스트.

- remap_signal 이 experiments/c13a_signal_hibeta.py remap_leg 와 **모든 레그 범주에서 동일**(cash·PSQ 변형).
- 실행 심볼이 비레버리지(고베타 바스켓 + QQQ/BIL/SHY, psq 변형은 +PSQ)만 나온다.
- 그룹 = 실계좌 가능(비레버리지). look-ahead 없음.

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


if __name__ == "__main__":
    unittest.main()
