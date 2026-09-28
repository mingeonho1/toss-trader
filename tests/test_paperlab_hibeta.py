"""paperlab hibeta_basket + btc_proxy 회귀 테스트.

- 베타 추정이 구성 베타를 복원하고, 상위 topk 동일가중을 고른다(월간 리밸런스, 최소가 $5 필터).
- btc_proxy: BTC 프록시(IBIT) > SMA100 이면 MSTR/COIN 동일가중 else 현금.
- 둘 다 look-ahead 없음.

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

from toss_trader.models import Candle  # noqa: E402
from toss_trader.research import lookahead_guard  # noqa: E402
from toss_trader.paperlab_strategies import hibeta_basket as HB  # noqa: E402
from toss_trader.paperlab_strategies import btc_proxy as BP  # noqa: E402


def _bizdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _beta_panel(n=300, seed=9):
    """QQQ 랜덤워크 + 종목별 close = 구성 베타로 만든 수익(노이즈 0 → 베타 정확 복원)."""
    rng = random.Random(seed)
    dates = _bizdays(date(2016, 1, 4), n)
    rq = [0.0] + [rng.gauss(0.0004, 0.011) for _ in range(n - 1)]
    q = [200.0]
    for t in range(1, n):
        q.append(q[-1] * (1.0 + rq[t]))
    betas = {"B05": 0.5, "B08": 0.8, "B10": 1.0, "B12": 1.2, "B15": 1.5, "B18": 1.8,
             "B20": 2.0, "B22": 2.2, "B25": 2.5, "B28": 2.8, "B30": 3.0, "B14": 1.4,
             "B16": 1.6, "B24": 2.4, "B26": 2.6}
    closes = {"QQQ": q}
    for name, b in betas.items():
        c = [50.0]
        for t in range(1, n):
            c.append(max(0.5, c[-1] * (1.0 + b * rq[t])))
        closes[name] = c
    return dates, closes, betas


def _history(dates, closes, symbols, upto=None):
    upto = len(dates) if upto is None else upto
    h = {s: [] for s in symbols}
    for t in range(upto):
        for s in symbols:
            px = closes[s][t]
            h[s].append(Candle(s, dates[t], px, px, px, px, 1e6))
    return h


class HibetaSelectionTest(unittest.TestCase):
    def test_selects_top10_highest_beta(self):
        dates, closes, betas = _beta_panel()
        names = [s for s in closes if s != "QQQ"]
        hist = _history(dates, closes, ["QQQ"] + names)
        tgt = HB.select_basket(hist, names, topk=10)
        self.assertEqual(len(tgt), 10)
        for w in tgt.values():
            self.assertAlmostEqual(w, 0.1, places=9)          # 동일가중
        # 구성 베타 상위 10개가 뽑혀야 한다.
        top10 = sorted(betas, key=betas.get, reverse=True)[:10]
        self.assertEqual(set(tgt), set(top10))

    def test_beta_recovers_construction(self):
        dates, closes, betas = _beta_panel()
        qmap = {d: closes["QQQ"][i] for i, d in enumerate(dates)}
        stock = [Candle("B20", dates[i], closes["B20"][i], closes["B20"][i],
                        closes["B20"][i], closes["B20"][i], 1e6) for i in range(len(dates))]
        b = HB._beta_to_bench(stock, qmap, 252)
        self.assertAlmostEqual(b, 2.0, places=6)

    def test_min_price_and_history_filters(self):
        dates, closes, betas = _beta_panel()
        names = [s for s in closes if s != "QQQ"]
        # B30(최고베타)을 $5 미만으로 강등 → 제외돼야.
        closes = dict(closes)
        closes["B30"] = [x * 0.01 for x in closes["B30"]]     # ~ $0.5
        hist = _history(dates, closes, ["QQQ"] + names)
        tgt = HB.select_basket(hist, names, topk=10)
        self.assertNotIn("B30", tgt)

    def test_monthly_rebalance_only(self):
        dates, closes, betas = _beta_panel(n=300)
        names = [s for s in closes if s != "QQQ"]
        strat = HB.HibetaBasket(names)
        st, prev, changes = {}, None, 0
        hist = {s: [] for s in ["QQQ"] + names}
        for t, d in enumerate(dates):
            for s in ["QQQ"] + names:
                px = closes[s][t]
                hist[s].append(Candle(s, d, px, px, px, px, 1e6))
            w = strat.decide(hist, st)
            key = tuple(sorted(w))
            if prev is not None and key != prev:
                changes += 1
            prev = key
        # 베타 워밍업(253일) 후 월초에만 구성이 바뀜 → 소수 회(매일 아님).
        self.assertLessEqual(changes, 4)

    def test_hibeta_no_lookahead(self):
        dates, closes, betas = _beta_panel(n=270)
        names = [s for s in closes if s != "QQQ"]

        def sig(panel_closes, ds):
            strat = HB.HibetaBasket(names)
            st, out = {}, []
            hist = {s: [] for s in ["QQQ"] + names}
            for t, d in enumerate(ds):
                for s in ["QQQ"] + names:
                    px = panel_closes[s][t]
                    hist[s].append(Candle(s, d, px, px, px, px, 1e6))
                out.append(strat.decide(hist, st))
            return out
        self.assertTrue(lookahead_guard(sig, closes, dates))


class BtcProxyTest(unittest.TestCase):
    def _panel(self, n=160, seed=3):
        rng = random.Random(seed)
        dates = _bizdays(date(2024, 1, 2), n)
        # IBIT: 처음 상승(>SMA100), 후반 급락(<SMA100).
        ibit = []
        px = 30.0
        for t in range(n):
            drift = 0.010 if t < 120 else -0.03
            px = max(1.0, px * (1.0 + drift + rng.gauss(0, 0.01)))
            ibit.append(px)
        mstr = [max(1.0, 100.0 * (1.0 + 0.001 * t)) for t in range(n)]
        coin = [max(1.0, 80.0 * (1.0 + 0.001 * t)) for t in range(n)]
        return dates, {"IBIT": ibit, "MSTR": mstr, "COIN": coin}

    def test_on_off_by_sma100(self):
        dates, closes = self._panel()
        syms = ["IBIT", "MSTR", "COIN"]
        hist = _history(dates, closes, syms, upto=118)        # 상승 국면(>SMA100)
        on = BP.decide_btc_proxy(hist)
        self.assertEqual(set(on), {"MSTR", "COIN"})
        for w in on.values():
            self.assertAlmostEqual(w, 0.5, places=9)
        hist2 = _history(dates, closes, syms)                 # 급락 후(<SMA100)
        off = BP.decide_btc_proxy(hist2)
        self.assertEqual(off, {})

    def test_btc_proxy_no_lookahead(self):
        dates, closes = self._panel(n=150)

        def sig(panel_closes, ds):
            out = []
            syms = ["IBIT", "MSTR", "COIN"]
            hist = {s: [] for s in syms}
            for t, d in enumerate(ds):
                for s in syms:
                    px = panel_closes[s][t]
                    hist[s].append(Candle(s, d, px, px, px, px, 1e6))
                out.append(BP.decide_btc_proxy(hist))
            return out
        self.assertTrue(lookahead_guard(sig, closes, dates))


if __name__ == "__main__":
    unittest.main()
