"""tests for experiments/c12_composer — look-ahead guards + hand-checked FTLT tree.

규약(docs/gate_v2_spec §2.3/§9): 모든 전략 신호 함수는 lookahead_guard 필수. 여기서는
합성 패널(랜덤워크)로 8개 빌더 전부를 가드하고(네트워크 불필요), B1 FTLT 트리의 각 리프를
손으로 계산한 입력으로 검증한다. 추가로 비용 티어·1x 섀도 매핑·micro 수수료·Lane A 판정 로직.
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import research as R  # noqa: E402
import c12_composer as C  # noqa: E402


# ── 합성 패널 유틸(네트워크 불필요) ─────────────────────────────────────────
def _dates(n):
    return [date(2016, 1, 4) + timedelta(days=i) for i in range(n)]


def _walk(rng, n, start=100.0, drift=0.0, vol=0.02):
    px = [start]
    for _ in range(n - 1):
        px.append(max(1e-3, px[-1] * (1.0 + drift + rng.gauss(0.0, vol))))
    return px


def synth_series(strat, n=420, seed=7):
    """전략이 참조하는 전 심볼을 랜덤워크로. OHLC 필요시 |H/|L 도 추가."""
    rng = random.Random(seed + hash(strat.sid) % 1000)
    syms = list(dict.fromkeys(list(strat.symbols) + (["QQQ"] if strat.ohlc else [])))
    S = {s: _walk(rng, n, start=50.0 + 10 * (i % 5), drift=0.0002 * ((i % 3) - 1))
         for i, s in enumerate(syms)}
    if strat.ohlc:
        c = S[strat.ohlc]
        S[f"{strat.ohlc}|H"] = [v * (1.0 + abs(rng.gauss(0, 0.01))) for v in c]
        S[f"{strat.ohlc}|L"] = [v * (1.0 - abs(rng.gauss(0, 0.01))) for v in c]
    return S, _dates(n)


# ── 1) look-ahead 가드: 모든 빌더 ───────────────────────────────────────────
@pytest.mark.parametrize("strat", C.registry(), ids=lambda s: s.sid)
def test_lookahead_guard_each_builder(strat):
    S, dates = synth_series(strat)
    fn = lambda pc, d, b=strat.build, p=strat.base_p: b(pc, d, p)  # noqa: E731
    assert R.lookahead_guard(fn, S, dates) is True


# ── 2) FTLT(B1) 트리 손검증 — 각 리프를 정확 입력으로 ─────────────────────────
def _b1_leaf(overrides, n=300):
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    base = {s: [50.0] * n for s in ["SPY", "TQQQ", "SPXL", "UVXY", "TECL", "SQQQ", "BSV"]}
    base.update(overrides(n))
    W = C.build_b1(base, dates, C.registry()[0].base_p)
    return W[-1]


def test_ftlt_bull_overbought_to_uvxy():
    # 강세(SPY↑ > SMA200) + RSI(TQQQ,10)>79 → UVXY
    leaf = _b1_leaf(lambda n: {"SPY": [100.0 + i for i in range(n)],
                               "TQQQ": [100.0 + i for i in range(n)],
                               "SPXL": [100.0 + i for i in range(n)]})
    assert leaf == {"UVXY": 1.0}


def test_ftlt_bull_normal_to_tqqq():
    # 강세 + RSI 중립(≤79/80) → TQQQ
    osc = lambda n: [100.0 + (1 if i % 2 else -1) for i in range(n)]  # noqa: E731
    leaf = _b1_leaf(lambda n: {"SPY": [100.0 + i for i in range(n)],
                               "TQQQ": osc(n), "SPXL": osc(n)})
    assert leaf == {"TQQQ": 1.0}


def test_ftlt_bear_oversold_to_tecl():
    # 약세(SPY↓ < SMA200) + RSI(TQQQ,10)<31 → TECL
    leaf = _b1_leaf(lambda n: {"SPY": [400.0 - i for i in range(n)],
                               "TQQQ": [400.0 - i for i in range(n)],
                               "SPXL": [400.0 - i for i in range(n)]})
    assert leaf == {"TECL": 1.0}


def test_ftlt_bear_spy_oversold_to_spxl():
    # 약세 + RSI(TQQQ) 중립 + RSI(SPY,10)<30 → SPXL(=UPRO)
    osc = lambda n: [100.0 + (1 if i % 2 else -1) for i in range(n)]  # noqa: E731
    leaf = _b1_leaf(lambda n: {"SPY": [400.0 - i for i in range(n)],
                               "TQQQ": osc(n), "SPXL": osc(n)})
    assert leaf == {"SPXL": 1.0}


def test_ftlt_trendblock_to_sqqq():
    # 약세 + RSI(TQQQ)·RSI(SPY)·RSI(UVXY) 중립 → TREND_BLOCK;
    # price(TQQQ)>SMA20 & RSI(SQQQ,10)<31 → SQQQ
    def ov(n):
        spy = [400.0 - i if i < 250 else 150.0 + (2 if i % 2 else -2) for i in range(n)]
        tqqq = [100.0 + 0.05 * i + (1 if i % 2 else -1) for i in range(n)]
        uvxy = [50.0 + (3 if i % 2 else -3) for i in range(n)]      # 중립 RSI
        sqqq = [400.0 - 1.0 * i for i in range(n)]                  # RSI10=0 <31
        return {"SPY": spy, "TQQQ": tqqq, "UVXY": uvxy, "SQQQ": sqqq}
    assert _b1_leaf(ov) == {"SQQQ": 1.0}


def test_ftlt_trendblock_downtrend_top1_of_sqqq_bsv():
    # 약세 + 중립 RSI → TREND_BLOCK; price(TQQQ)<SMA20 → top1 RSI(10) of [SQQQ,BSV]
    def ov(n):
        spy = [400.0 - i if i < 250 else 150.0 + (2 if i % 2 else -2) for i in range(n)]
        tqqq = [200.0 - 0.3 * i + (1 if i % 2 else -1) for i in range(n)]  # 하락 → price<SMA20
        uvxy = [50.0 + (3 if i % 2 else -3) for i in range(n)]
        sqqq = [100.0 + 1.0 * i for i in range(n)]   # RSI↑ (상승) → top1
        bsv = [100.0 - 0.2 * i for i in range(n)]    # RSI↓
        return {"SPY": spy, "TQQQ": tqqq, "UVXY": uvxy, "SQQQ": sqqq, "BSV": bsv}
    # SQQQ RSI > BSV RSI → top1 = SQQQ
    assert _b1_leaf(ov) == {"SQQQ": 1.0}


# ── 3) 비용 티어(사양 §7 + 태스크): 레버리지 2bp·UVXY 5bp·ETF 1bp ─────────────
def test_cost_tiers():
    cs = C.build_cost(1.0)
    assert cs.commission_bps == 10.0 and cs.slippage_bps == 5.0
    assert cs.half_spread_for("TQQQ") == 2.0     # 레버리지
    assert cs.half_spread_for("UVXY") == 5.0     # 변동성
    assert cs.half_spread_for("QQQ") == 1.0      # 기본 ETF
    # trade_bps = 수수료 + 반호가 + 슬리피지
    assert cs.trade_bps("TQQQ") == pytest.approx(17.0)
    assert cs.trade_bps("QQQ") == pytest.approx(16.0)
    cs2 = C.build_cost(2.0)
    assert cs2.trade_bps("TQQQ") == pytest.approx(34.0)


def test_micro_fee_buys_free():
    fn = C.micro_fee_fn()
    assert fn("BUY", 1000.0) == pytest.approx((2.0 + 5.0) * 1e-4 * 1000.0)   # 수수료 0
    assert fn("SELL", 1000.0) == pytest.approx((10.0 + 2.0 + 5.0) * 1e-4 * 1000.0)
    assert fn("BUY", 1000.0) < fn("SELL", 1000.0)


# ── 4) 1x 섀도 매핑 ──────────────────────────────────────────────────────────
def test_shadow_mapping():
    w = [{"TQQQ": 1.0}, {"SQQQ": 1.0}, {"SOXL": 0.5, "TECS": 0.5}, {"UVXY": 1.0},
         {"SOXS": 1.0}]
    sh = C.shadow_weights(w)
    assert sh[0] == {"QQQ": 1.0}
    assert sh[1] == {"PSQ": 1.0}
    assert sh[2] == {"SMH": 0.5, "PSQ": 0.5}      # SOXL→SMH, TECS→PSQ
    assert sh[3] == {}                             # UVXY → 현금
    assert sh[4] == {"PSQ": 1.0}


# ── 5) Lane A 판정 로직(부록 v3) ─────────────────────────────────────────────
def _splits(dc, hc):
    return {"design": {"cagr": dc}, "holdout": {"cagr": hc}}


def _bench(qd, qh):
    return {"qqq": {"design": qd, "holdout": qh}}


def test_lane_a_pass():
    v = C.lane_a_verdict(_splits(1.0, 0.3), _splits(0.9, 0.25),
                         _bench(0.2, 0.1), neigh_frac=0.8, full_mdd=-0.6)
    assert v == "PASS"


def test_lane_a_bankruptcy_guard():
    v = C.lane_a_verdict(_splits(2.0, 1.0), _splits(2.0, 1.0),
                         _bench(0.2, 0.1), neigh_frac=1.0, full_mdd=-0.96)
    assert v.startswith("FAIL")


def test_lane_a_fail_on_design_below_qqq():
    v = C.lane_a_verdict(_splits(0.1, 0.3), _splits(0.1, 0.3),
                         _bench(0.2, 0.1), neigh_frac=1.0, full_mdd=-0.5)
    assert v == "FAIL"


def test_lane_a_conditional_when_2x_fails():
    # (a) 통과 + 이웃 통과, 그러나 2×비용에서 홀드아웃이 QQQ 아래 → CONDITIONAL
    v = C.lane_a_verdict(_splits(0.5, 0.15), _splits(0.5, 0.05),
                         _bench(0.2, 0.1), neigh_frac=0.8, full_mdd=-0.6)
    assert v == "CONDITIONAL"
