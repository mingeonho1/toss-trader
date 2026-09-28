"""c6a 감사 테스트 — 배당 이중계상 정정 항등식, 정정 합성의 방향/크기,
정상 부트스트랩 성질, 적립중단 시뮬레이터(꼬리 성장 회귀), 행동지표·유효표본 sanity.

데이터파일 불필요(토이 Candle 패널).  src/·c5a 미수정 전제.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import histdata as hd, research as R  # noqa: E402
import c5a_lifecycle as c5a  # noqa: E402
import c6a_lifecycle_audit as c6a  # noqa: E402


def _toy_candles(n=520, seed=11, drift=0.0004, vol=0.011):
    import random
    rng = random.Random(seed)
    d = date(1990, 1, 2)
    dates = []
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    px = [100.0]
    for _ in range(1, n):
        px.append(max(1.0, px[-1] * (1.0 + rng.gauss(drift, vol))))
    price = [hd.Candle("NDX", dt, p, p, p, p, 0.0) for dt, p in zip(dates, px)]
    dtb3 = [hd.Candle("RF", dt, 2.0, 2.0, 2.0, 2.0, 0.0) for dt in dates]  # 2%/yr
    return price, dtb3, dates


# ── 배당 이중계상 정정 항등식 ────────────────────────────────────────────────
def test_dividend_correction_identity():
    """정정 2x 일수익 == c5a식(2×총수익) 2x 일수익 − div/252 (매일, 정밀)."""
    price, dtb3, _ = _toy_candles()
    div = c6a.DIV_NDX
    # c5a식: 2×총수익 base
    tr = hd.index_total_return(price, div)
    c5a_qld = hd.synthetic_leveraged(tr, 2.0, annual_expense=c6a.EXP_LEV,
                                     borrow_spread=c6a.BORROW_SPREAD, rf_candles=dtb3,
                                     rf_kind="yield")
    c5a_c = [c.close for c in c5a_qld]
    corr_c = c6a.corrected_qld_closes(price, dtb3, div_yield=div)
    assert len(c5a_c) == len(corr_c)
    div_d = div / 252.0
    for i in range(1, len(c5a_c)):
        r_cur = c5a_c[i] / c5a_c[i - 1] - 1.0
        r_cor = corr_c[i] / corr_c[i - 1] - 1.0
        assert (r_cur - r_cor) == pytest.approx(div_d, abs=1e-12)


def test_correction_is_downward_and_sized():
    """정정은 항상 아래 방향이고, 연환산 CAGR 차이 ≈ div(0.7%) 근방(2x 슬리브 전량)."""
    price, dtb3, dates = _toy_candles(n=520)
    div = c6a.DIV_NDX
    tr = hd.index_total_return(price, div)
    c5a_c = [c.close for c in hd.synthetic_leveraged(
        tr, 2.0, annual_expense=c6a.EXP_LEV, borrow_spread=c6a.BORROW_SPREAD,
        rf_candles=dtb3, rf_kind="yield")]
    corr_c = c6a.corrected_qld_closes(price, dtb3, div_yield=div)
    assert corr_c[-1] < c5a_c[-1]                     # 정정 후 더 낮음
    yrs = (dates[-1] - dates[0]).days / 365.25
    cagr_cur = (c5a_c[-1] / c5a_c[0]) ** (1 / yrs) - 1
    cagr_cor = (corr_c[-1] / corr_c[0]) ** (1 / yrs) - 1
    # 일수익 차는 정확히 div/252 이지만, CAGR 차는 고수익·레버리지 경로에서 1차근사 div 를
    # 다소 상회한다(승법 교차항).  방향+크기 밴드로 점검(0.5×~2×div).
    diff = cagr_cur - cagr_cor
    assert div * 0.5 < diff < div * 2.0


def test_qqq_1x_unchanged_by_correction():
    """1x(QQQ)는 정정 대상이 아님 — build_price_panel 의 QQQ 는 총수익 base 그대로."""
    price, dtb3, dates = _toy_candles()
    p = c6a.build_price_panel(price, dtb3, div_yield=c6a.DIV_NDX)
    qqq_ref = hd.synthetic_leveraged(hd.index_total_return(price, c6a.DIV_NDX), 1.0,
                                     annual_expense=c6a.EXP_1X, borrow_spread=0.0,
                                     rf_candles=dtb3, rf_kind="yield")
    ref = [c.close for c in qqq_ref]
    assert p["QQQ"] == pytest.approx(ref, rel=1e-12)
    assert len(p["QQQ"]) == len(p["QLD"]) == len(p["dates"]) == len(dates)
    assert all(x > 0 for x in p["QLD"])


# ── 정상 부트스트랩 성질 ─────────────────────────────────────────────────────
def test_stationary_bootstrap_shape_and_range():
    import random
    rng = random.Random(1)
    idx = c6a.stationary_bootstrap_indices(500, 3000, 250, rng)
    assert len(idx) == 3000
    assert all(0 <= i < 500 for i in idx)


def test_stationary_bootstrap_mean_block():
    """평균 블록 길이 ≈ 목표(느슨).  연속 증가 런의 평균 길이로 근사."""
    import random
    rng = random.Random(7)
    idx = c6a.stationary_bootstrap_indices(2000, 60000, 250, rng)
    runs = []
    cur = 1
    for a, b in zip(idx, idx[1:]):
        if b == (a + 1) % 2000:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    mean_run = sum(runs) / len(runs)
    assert 150 < mean_run < 400          # 기대 250, 통계적 여유


def test_bootstrap_reproducible():
    import random
    a = c6a.stationary_bootstrap_indices(300, 1000, 200, random.Random(42))
    b = c6a.stationary_bootstrap_indices(300, 1000, 200, random.Random(42))
    assert a == b


# ── 적립중단 시뮬레이터: 미중단이면 c5a.simulate 와 동일, 꼬리 성장 회귀 ──────
def _toy_panel_dict(price, dtb3, dates):
    p = c6a.build_price_panel(price, dtb3, div_yield=c6a.DIV_NDX)
    return {"QQQ": p["QQQ"], "QLD": p["QLD"]}, p


def test_contrib_stop_equals_c5a_when_never_stopping():
    price, dtb3, dates = _toy_candles(n=400, seed=3)
    closes, panel = _toy_panel_dict(price, dtb3, dates)
    ref = c5a.simulate(closes, dates, 0, len(dates), "glide", sigma=panel["sigma"],
                       trend=panel["trend"], cash=panel["cash"]).final_value
    got = c6a.simulate_contrib_stop(closes, dates, 0, len(dates), "glide", panel, 10**9)
    assert got == pytest.approx(ref, rel=1e-9)


def test_contrib_stop_tail_grows_in_bull():
    """적립중단 후에도 잔여 자산이 상승장에서 계속 성장(제로성장 버그 회귀)."""
    price, dtb3, dates = _toy_candles(n=520, seed=1, drift=0.0009, vol=0.008)  # 강상승
    closes, panel = _toy_panel_dict(price, dtb3, dates)
    stop_idx = c5a._end_index(dates, 0, 1)                    # 1년 후 중단
    at_stop = c6a.simulate_contrib_stop(closes, dates, 0, stop_idx + 1, "glide", panel, 12)
    full = c6a.simulate_contrib_stop(closes, dates, 0, len(dates), "glide", panel, 12)
    assert full > at_stop * 1.05                             # 꼬리 구간에서 성장


def test_contrib_stop_reduces_deposits():
    """중단하면 미중단보다 최종자산이 작다(입금이 줄었으므로)."""
    price, dtb3, dates = _toy_candles(n=520, seed=9, drift=0.0006, vol=0.009)
    closes, panel = _toy_panel_dict(price, dtb3, dates)
    stopped = c6a.simulate_contrib_stop(closes, dates, 0, len(dates), "glide", panel, 12)
    never = c6a.simulate_contrib_stop(closes, dates, 0, len(dates), "glide", panel, 10**9)
    assert stopped < never


# ── 행동지표·유효표본 sanity ─────────────────────────────────────────────────
def test_behavioral_paths_sane():
    price, dtb3, dates = _toy_candles(n=520, seed=4)
    panel = c6a.build_price_panel(price, dtb3, div_yield=c6a.DIV_NDX)
    # 토이엔 20y 창이 없으므로 1년 지평으로 구조만 점검
    bh = c6a.behavioral_paths(panel, 1, "glide", design_years=(1990, 1995),
                              holdout_years=(1996, 2000))
    for period in ("design", "holdout"):
        b = bh.get(period)
        if not b:
            continue
        assert -1.0 - 1e-9 <= b["worst_dd"] <= 0.0 + 1e-9
        assert 0.0 <= b["p_dd_gt_50"] <= 1.0
        assert 0.0 <= b["median_underwater"] <= 1.0
        assert 0.0 <= b["median_dep_frac_at_trough"] <= 1.0 + 1e-9


def test_effective_sample():
    es = c6a.effective_sample((1986, 1999), (2000, 2016), 20, 40)
    assert es["eff_nonoverlap_total"] == pytest.approx(2.0)
    assert es["design_span_years"] == 14


def test_corrected_panel_matches_c5a_except_qld():
    """정정 패널은 c5a 합성패널과 QQQ/σ/추세/현금 동일, QLD 만 다름(≤)."""
    cp = c6a.build_corrected_panel()
    base = c5a.build_synthetic_panel()
    assert cp["QQQ"] == base["QQQ"]
    assert cp["dates"] == base["dates"]
    assert cp["QLD"] != base["QLD"]
    # 정정 QLD 최종 NAV 는 현행보다 작다(장기 배당 이중계상 제거)
    assert cp["QLD"][-1] < base["QLD"][-1]
