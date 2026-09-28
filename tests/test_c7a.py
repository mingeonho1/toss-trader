"""c7a 감사 테스트 — crash_lev 인과성, 시장 ATH vs 코호트 ATH, 적립중단 미러(중단=∞→c6c 동일),
부트스트랩 MC 성질, 행동재무 B1 대비 회계, 상관 헬퍼, 배당정정 적용.

데이터파일 불필요(토이 Candle 패널).  src/·c5a·c6a·c6c 미수정 전제.
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
import c6c_contrib as c6c  # noqa: E402
import c7a_crashlev_audit as c7a  # noqa: E402


def _toy_candles(n=760, seed=11):
    """상승→>30% 크래시→회복 포함(crash_lev 라우팅 발동).  2001-01 시작."""
    import random
    rng = random.Random(seed)
    d = date(2001, 1, 2)
    dates = []
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    px = [100.0]
    for i in range(1, n):
        mu = 0.0009 if i < 250 else (-0.007 if i < 360 else 0.0011)
        px.append(max(1.0, px[-1] * (1.0 + rng.gauss(mu, 0.011))))
    price = [hd.Candle("NDX", dt, p, p, p, p, 0.0) for dt, p in zip(dates, px)]
    dtb3 = [hd.Candle("RF", dt, 2.0, 2.0, 2.0, 2.0, 0.0) for dt in dates]
    return price, dtb3, dates


def _toy_panel(n=760, seed=11):
    price, dtb3, dates = _toy_candles(n, seed)
    p = c7a.build_price_panel_c6c(price, dtb3, div_yield=c7a.DIV_NDX)
    p["ndx"] = price
    p["dtb3"] = dtb3
    return p, dates


# ── 인과성(음성대조는 test_c6c 에서; 여기선 재확인) ──────────────────────────
def test_crashlev_no_lookahead():
    p, dates = _toy_panel()
    closes = {"QQQ": p["QQQ"], "QLD": p["QLD"]}
    assert R.lookahead_guard(c6c.make_signal_fn("crash_lev"), closes, dates) is True


# ── 배당정정 적용(정정 QLD < 이중계상 QLD) ───────────────────────────────────
def test_corrected_qld_is_downward():
    price, dtb3, _ = _toy_candles()
    tr = hd.index_total_return(price, c7a.DIV_NDX)
    dbl = [c.close for c in hd.synthetic_leveraged(
        tr, 2.0, annual_expense=c7a.EXP_LEV, borrow_spread=c7a.BORROW_SPREAD,
        rf_candles=dtb3, rf_kind="yield")]
    corr = c7a.c6a.corrected_qld_closes(price, dtb3, div_yield=c7a.DIV_NDX)
    assert corr[-1] < dbl[-1]


# ── 시장 ATH vs 코호트 ATH: 신선한 투자자는 시작 시 레버리지 아님 ───────────
def test_cohort_ath_no_leverage_at_own_peak():
    """자기 시작일이 자기 고점인 코호트: 초기엔 QLD 로 라우팅하지 않는다.

    상승만 하는 토이에서 코호트 crash_lev 는 사실상 B1(QQQ) 과 같아야 한다(라우팅 없음).
    """
    import random
    rng = random.Random(3)
    d = date(2001, 1, 2)
    dates = []
    while len(dates) < 400:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    px = [100.0]
    for _ in range(1, 400):
        px.append(px[-1] * (1.0 + abs(rng.gauss(0.0008, 0.004))))   # 항상 상승
    price = [hd.Candle("NDX", dt, p, p, p, p, 0.0) for dt, p in zip(dates, px)]
    dtb3 = [hd.Candle("RF", dt, 2.0, 2.0, 2.0, 2.0, 0.0) for dt in dates]
    panel = c7a.build_price_panel_c6c(price, dtb3, div_yield=c7a.DIV_NDX)
    starts = [(0, len(dates) - 1)]
    rel = c7a.run_crashlev_relative_ath(panel, 1, starts, {})
    b1 = c6c.run_distribution(panel, 1, "qqq", {}, starts)
    # 라우팅이 없으면 crash_lev(코호트) ≈ B1
    assert rel[0]["terminal"] == pytest.approx(b1[0]["terminal"], rel=1e-9)


def test_market_ath_can_lever_from_inherited_drawdown():
    """시장 ATH 백테스트에서, 사전에 −30% 낙폭이 있는 코호트는 시작부터 QLD 라우팅될 수 있다.

    코호트 ATH 변형은 그 상속 낙폭을 무시 → 두 변형의 결과가 달라진다(라우팅 여부).
    """
    p, dates = _toy_panel()
    # 크래시 저점 근방에서 시작하는 코호트(시장 ATH 는 여전히 깊은 낙폭)
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, 1, fast=False)
    # 크래시 이후 시작만
    late = [(s, e) for (s, e) in starts if s >= 300]
    if not late:
        pytest.skip("토이에 크래시 후 시작 없음")
    mk = c6c.run_distribution(p, 1, "crash_lev", {}, late)
    co = c7a.run_crashlev_relative_ath(p, 1, late, {})
    # 적어도 한 시작일에서 시장/코호트 결과가 다르다(라우팅 상속 효과)
    diffs = [abs(a["terminal"] - b["terminal"]) for a, b in zip(mk, co)]
    assert max(diffs) > 1e-6


# ── 적립중단 미러: 중단=∞ 면 c6c.simulate 최종과 동일 ────────────────────────
@pytest.mark.parametrize("strat", ["crash_lev", "qqq"])
def test_contrib_stop_equals_c6c_when_never_stopping(strat):
    p, dates = _toy_panel()
    cash = [0.0] * len(dates)
    ref = c6c.simulate(p, 0, len(dates), strat, {}, cash=cash).final_value
    got = c7a.simulate_contrib_stop(p, 0, len(dates), strat, {}, cash, 10 ** 9)
    assert got == pytest.approx(ref, rel=1e-9)


def test_contrib_stop_reduces_final():
    p, dates = _toy_panel()
    cash = [0.0] * len(dates)
    stopped = c7a.simulate_contrib_stop(p, 0, len(dates), "crash_lev", {}, cash, 6)
    never = c7a.simulate_contrib_stop(p, 0, len(dates), "crash_lev", {}, cash, 10 ** 9)
    assert stopped < never


# ── 부트스트랩 MC 성질 ───────────────────────────────────────────────────────
def test_bootstrap_mc_finite_and_corr_bounded():
    p, _ = _toy_panel()
    mc = c7a.bootstrap_mc(p, years=2, n_paths=8, mean_block=60, seed=1)
    assert mc["crash_lev"]["n_paths"] >= 1
    assert mc["crash_lev"]["ratio_median"] > 0
    assert mc["glide"]["ratio_median"] > 0
    for c in (mc["corr_pearson"], mc["corr_spearman"]):
        assert c != c or (-1.0 - 1e-9 <= c <= 1.0 + 1e-9)


# ── 행동재무 B1 대비 회계 ────────────────────────────────────────────────────
def test_behavioral_vs_b1_ranges():
    p, _ = _toy_panel()
    cash = [0.0] * len(p["dates"])
    bh = c7a.behavioral_vs_b1_crashlev(p, 1, cash=cash)
    got = [v for v in bh.values() if v]
    assert got, "구간 표본 없음"
    for b in got:
        for key in ("strat_median_worst_dd", "b1_median_worst_dd",
                    "median_b1_at_strat_trough"):
            assert -1.0 - 1e-9 <= b[key] <= 0.0 + 1e-9
        for key in ("strat_p_dd_gt_50", "b1_p_dd_gt_50", "strat_median_uw", "b1_median_uw"):
            assert 0.0 <= b[key] <= 1.0 + 1e-9
        # crash_lev 는 레버리지 → 최악낙폭이 B1 보다 얕지 않다(같거나 더 깊다)
        assert b["strat_median_worst_dd"] <= b["b1_median_worst_dd"] + 1e-9


# ── 상관 헬퍼 손검산 ─────────────────────────────────────────────────────────
def test_pearson_spearman_extremes():
    a = [1.0, 2.0, 3.0, 4.0]
    assert c7a._pearson(a, a) == pytest.approx(1.0)
    assert c7a._pearson(a, [4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)
    # 단조(비선형)에서 Spearman=1
    assert c7a._spearman(a, [1.0, 4.0, 9.0, 16.0]) == pytest.approx(1.0)


# ── dist_crashlev 구조 ───────────────────────────────────────────────────────
def test_dist_crashlev_structure_and_decision():
    p, dates = _toy_panel()
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, 1, fast=False)
    d = c7a.dist_crashlev(p, 1, starts, cash=[0.0] * len(dates))
    sec = d.get("holdout") or d.get("design")
    assert sec is not None
    assert set(("b1", "crash_lev")).issubset(sec)
    assert sec["crash_lev"]["decision"]["verdict"] in ("PASS", "CONDITIONAL", "FAIL", "USEFUL")
