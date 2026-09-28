"""paperlab Composer 이식(ftlt·holy_grail·simple_rsi_uvxy) 회귀 테스트.

핵심: 엔진이 매일 넘겨주는 '오늘까지의' 히스토리로 계산한 decide 목표비중이, 원본
experiments/c12_composer.py build_b1/build_b2/build_b5 가 정렬 패널에서 산출한 W[t] 와
**픽스처 전 구간에서 동일**함을 확인한다(파라미터 동결, 재튜닝 없음). + look-ahead 가드.

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

from toss_trader import research as R  # noqa: E402
from toss_trader.models import Candle  # noqa: E402
from toss_trader.research import lookahead_guard  # noqa: E402
import c12_composer as CC  # noqa: E402
from toss_trader.paperlab_strategies import ftlt, holy_grail, simple_rsi_uvxy  # noqa: E402


def _bizdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _walk(rng: random.Random, n: int, *, s0: float, drift: float, vol: float,
          wave: float = 0.0, period: int = 90) -> list[float]:
    """분기 파동 + 랜덤워크(레짐/과매수 분기 모두 자극). 양수 종가 보장."""
    px = s0
    out = []
    for t in range(n):
        w = wave * math.sin(2 * math.pi * t / period)
        px *= math.exp(drift + w + rng.gauss(0.0, vol))
        out.append(max(0.5, px))
    return out


def _panel(symbols, n=340, seed=7):
    rng = random.Random(seed)
    dates = _bizdays(date(2016, 1, 4), n)
    cfg = {
        "SPY": (300.0, 0.0004, 0.010, 0.010), "TQQQ": (30.0, 0.0009, 0.030, 0.020),
        "SPXL": (40.0, 0.0007, 0.025, 0.015), "UVXY": (200.0, -0.0025, 0.055, 0.030),
        "TECL": (25.0, 0.0009, 0.030, 0.020), "SQQQ": (60.0, -0.0012, 0.030, 0.020),
        "BSV": (78.0, 0.0001, 0.002, 0.001), "SOXL": (20.0, 0.0010, 0.035, 0.025),
        "BIL": (91.0, 0.00005, 0.0005, 0.0), "QQQ": (200.0, 0.0005, 0.012, 0.010),
    }
    S = {}
    for i, s in enumerate(symbols):
        s0, dr, vo, wv = cfg.get(s, (50.0, 0.0003, 0.02, 0.01))
        S[s] = _walk(rng, n, s0=s0, drift=dr, vol=vo, wave=wv, period=90 + 7 * i)
    return dates, S


def _candles(dates, closes):
    return [Candle("X", d, c, c, c, c, 1e6) for d, c in zip(dates, closes)]


def _incremental_targets(decide_fn, dates, S, symbols):
    """decide_fn(history) 를 매일(t) 오늘까지 히스토리로 호출해 목표비중 리스트 반환."""
    hist = {s: [] for s in symbols}
    out = []
    for t, d in enumerate(dates):
        for s in symbols:
            hist[s].append(Candle(s, d, S[s][t], S[s][t], S[s][t], S[s][t], 1e6))
        out.append(decide_fn(hist))
    return out


def _same(a, b, tol=1e-9):
    a = a or {}
    b = b or {}
    keys = set(a) | set(b)
    return all(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) <= tol for k in keys)


class ComposerEquivalenceTest(unittest.TestCase):
    def _check(self, build_fn, params, decide_fn, symbols):
        dates, S = _panel(symbols)
        W_ref = build_fn(S, dates, params)                # 원본: 정렬 패널 전 구간
        mine = _incremental_targets(decide_fn, dates, S, symbols)
        self.assertEqual(len(W_ref), len(mine))
        mism = [(t, W_ref[t], mine[t]) for t in range(len(dates)) if not _same(W_ref[t], mine[t])]
        self.assertEqual(mism, [], f"{build_fn.__name__} 불일치 {len(mism)}건 예: {mism[:3]}")
        # 신호가 실제로 여러 종목을 오갔는지(테스트가 자명하지 않은지) 확인.
        seen = {next(iter(w)) for w in mine if w}
        self.assertGreaterEqual(len(seen), 2, f"{build_fn.__name__}: 분기 자극 부족 {seen}")

    def test_ftlt_matches_build_b1(self):
        p = dict(rw=10, sma_long=200, sma_short=20, t_tqqq=79, t_spxl=80, t_lo=31,
                 t_spy_lo=30, t_uvxy=74, t_uvxy_hi=84, t_sqqq=31)
        self._check(CC.build_b1, p, lambda h: ftlt.decide_ftlt(h),
                    ["SPY", "TQQQ", "SPXL", "UVXY", "TECL", "SQQQ", "BSV"])

    def test_holy_grail_matches_build_b2(self):
        p = dict(rw=10, sma_long=200, sma_short=20, t_tqqq=79, t_lo=31, t_soxl=30)
        self._check(CC.build_b2, p, lambda h: holy_grail.decide_holy_grail(h),
                    ["TQQQ", "UVXY", "TECL", "SOXL", "SQQQ", "BSV"])

    def test_simple_rsi_matches_build_b5(self):
        p = dict(rw=10, t_tqqq=79, defense="UVXY")
        self._check(CC.build_b5, p, lambda h: simple_rsi_uvxy.decide_simple_rsi(h),
                    ["TQQQ", "UVXY", "BIL"])


class ComposerLookaheadTest(unittest.TestCase):
    def _guard(self, decide_fn, symbols):
        dates, S = _panel(symbols)

        def signal_fn(panel_closes, ds):
            hist = {s: [] for s in symbols}
            out = []
            for t, d in enumerate(ds):
                for s in symbols:
                    px = panel_closes[s][t]
                    hist[s].append(Candle(s, d, px, px, px, px, 1e6))
                out.append(decide_fn(hist))
            return out

        self.assertTrue(lookahead_guard(signal_fn, S, dates))

    def test_ftlt_no_lookahead(self):
        self._guard(lambda h: ftlt.decide_ftlt(h),
                    ["SPY", "TQQQ", "SPXL", "UVXY", "TECL", "SQQQ", "BSV"])

    def test_holy_grail_no_lookahead(self):
        self._guard(lambda h: holy_grail.decide_holy_grail(h),
                    ["TQQQ", "UVXY", "TECL", "SOXL", "SQQQ", "BSV"])

    def test_ftlt_1x_shadow_maps(self):
        # 1x 섀도: 레버리지 목표가 QQQ/SPY/PSQ 로 매핑되고 UVXY/BSV 는 현금(빈 dict).
        dates, S = _panel(["SPY", "TQQQ", "SPXL", "UVXY", "TECL", "SQQQ", "BSV"])
        symbols = ["SPY", "TQQQ", "SPXL", "UVXY", "TECL", "SQQQ", "BSV"]
        base = _incremental_targets(lambda h: ftlt.decide_ftlt(h), dates, S, symbols)
        shad = _incremental_targets(lambda h: ftlt.C.to_shadow_1x(ftlt.decide_ftlt(h)),
                                    dates, S, symbols)
        for b, sh in zip(base, shad):
            if b == {"TQQQ": 1.0}:
                self.assertEqual(sh, {"QQQ": 1.0})
            if b == {"UVXY": 1.0}:
                self.assertEqual(sh, {})            # 변동성 → 현금
            if b == {"SQQQ": 1.0}:
                self.assertEqual(sh, {"PSQ": 1.0})


if __name__ == "__main__":
    unittest.main()
