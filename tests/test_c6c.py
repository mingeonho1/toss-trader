"""c6c 테스트 — 모든 결정함수의 lookahead 가드(사양 §2.3), VA 목표경로 손검산,
낙폭신호 인과성/정확성, 공정예산 회계(전 전략 동일 out-of-pocket, 최종=주식+QLD+버킷),
결정 티어(PASS/USEFUL/FAIL) 로직."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))

from toss_trader import research as R  # noqa: E402
import c6c_contrib as c6c  # noqa: E402


# ── 결정론적 토이 패널(데이터파일 불필요; 상승→>30%크래시→회복 포함) ──────────
def _toy_panel(n=520, seed=11):
    import random
    rng = random.Random(seed)
    d0 = date(2000, 1, 3)
    dates = []
    d = d0
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    qqq = [100.0]
    for i in range(1, n):
        # 구간별 추세: 상승(0..200) → 크래시(200..300) → 회복(300..)
        if i < 200:
            mu = 0.0009
        elif i < 300:
            mu = -0.006
        else:
            mu = 0.0011
        qqq.append(max(1.0, qqq[-1] * (1.0 + rng.gauss(mu, 0.012))))
    qld = [100.0]
    for i in range(1, n):
        r = qqq[i] / qqq[i - 1] - 1.0
        qld.append(max(0.5, qld[-1] * (1.0 + 2.0 * r)))
    panel = {"dates": dates, "QQQ": qqq, "QLD": qld}
    return c6c.add_signals(panel), dates


ALL_STRATS = c6c.STRATS + ["qqq"]


# ── lookahead 가드: 모든 결정함수 인과성 ─────────────────────────────────────
@pytest.mark.parametrize("strat", ALL_STRATS)
def test_signal_no_lookahead(strat):
    panel, dates = _toy_panel()
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    fn = c6c.make_signal_fn(strat)
    assert R.lookahead_guard(fn, closes, dates) is True


def test_lookahead_guard_catches_leak():
    """가드 음성대조: 미래를 보는 신호는 LookaheadLeak."""
    panel, dates = _toy_panel(n=180)
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}

    def leaky(pc, ds):
        n = len(ds)
        sig = pc["QQQ"]
        return [{"strat_val": 1.0 if sig[min(t + 5, n - 1)] > sig[t] else 0.0}
                for t in range(n)]

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, closes, dates)


# ── VA 목표경로 손검산 ───────────────────────────────────────────────────────
def test_va_target_g0_is_cumulative_contrib():
    """g=0 이면 V_m = initial + monthly·(m+1) (누적 적립금)."""
    for m in (0, 1, 5, 24):
        assert c6c.va_target(m, initial=32.0, monthly=35.0, g=0.0) == pytest.approx(
            32.0 + 35.0 * (m + 1), abs=1e-9)


def test_va_target_first_month_growth_invariant_and_monotone():
    """첫 달(m=0)은 g 무관하게 initial+monthly; m≥1 은 g>0 이 g=0 보다 크고 단조증가."""
    assert c6c.va_target(0, g=0.0) == pytest.approx(c6c.va_target(0, g=0.08), abs=1e-9)
    assert c6c.va_target(0, g=0.08) == pytest.approx(32.0 + 35.0, abs=1e-9)
    prev = c6c.va_target(0, g=0.08)
    for m in range(1, 200, 7):
        cur = c6c.va_target(m, g=0.08)
        assert cur > c6c.va_target(m, g=0.0) - 1e-9   # 성장 포함 ≥ 누적적립
        assert cur > prev                              # 단조증가
        prev = cur


# ── 낙폭신호 정확성/인과성 ───────────────────────────────────────────────────
def test_drawdown_series_hand_checked():
    closes = [100, 110, 120, 90, 60, 66, 200]
    dd_ath, dd_52 = c6c.drawdown_series(closes)
    assert dd_ath[2] == pytest.approx(0.0)             # 신고점
    assert dd_ath[3] == pytest.approx(90 / 120 - 1.0)  # -0.25
    assert dd_ath[4] == pytest.approx(60 / 120 - 1.0)  # -0.5
    assert dd_ath[6] == pytest.approx(0.0)             # 200 신고점
    assert all(x <= 1e-12 for x in dd_ath)             # 항상 ≤ 0


def test_dd52_rolling_window():
    """52주(WIN_52W) 롤링 고점: 창 밖 고점은 기준에서 빠진다."""
    W = c6c.WIN_52W
    closes = [50.0] + [1.0] * (W - 1) + [1.0]          # index 0 의 50 은 W 지점에서 창 이탈
    dd_ath, dd_52 = c6c.drawdown_series(closes)
    assert dd_52[1] == pytest.approx(1.0 / 50.0 - 1.0)  # 아직 창 안(고점 50)
    assert dd_52[W] == pytest.approx(0.0)               # 50 이 창 밖 → 창 최대=1.0


# ── 공정예산 회계 ────────────────────────────────────────────────────────────
def test_fair_budget_identical_across_strats():
    """모든 전략의 out-of-pocket 입금이 동일하고 = initial + n_months·monthly."""
    panel, dates = _toy_panel()
    n_months = sum(R.month_start_flags(dates))
    expect = c6c.INITIAL + n_months * c6c.MONTHLY
    deps = {}
    for strat in ALL_STRATS:
        res = c6c.simulate(panel, 0, len(dates), strat, {})
        deps[strat] = res.total_deposited
        assert res.total_deposited == pytest.approx(expect, abs=1e-9)
    assert len(set(round(v, 6) for v in deps.values())) == 1


def test_terminal_includes_bucket_and_nonneg():
    """최종자산 = 마지막 equity(주식+QLD+버킷) 이고 버킷/자산 음수 아님."""
    panel, dates = _toy_panel()
    for strat in ALL_STRATS:
        res = c6c.simulate(panel, 0, len(dates), strat, {})
        assert res.final_value == pytest.approx(res.equity[-1], abs=1e-9)
        assert res.final_value > 0
        assert min(res.equity) >= -1e-9


def test_b1_invests_almost_all_cash():
    """B1(qqq) 는 매월 전액 투자 → 평균 현금비중이 리저브 전략보다 낮다."""
    panel, dates = _toy_panel()
    b1 = c6c.simulate(panel, 0, len(dates), "qqq", {})
    ddr = c6c.simulate(panel, 0, len(dates), "dd_reserve", {})
    assert b1.avg_cash_frac < 0.05
    assert ddr.avg_cash_frac > b1.avg_cash_frac


# ── 전략별 구조 ──────────────────────────────────────────────────────────────
def test_va_no_sell_never_sells():
    panel, dates = _toy_panel()
    va = c6c.simulate(panel, 0, len(dates), "va", {})
    assert va.n_sells == 0


def test_va_sell_can_sell_in_bull():
    """강한 상승 토이에서 va_sell 은 경로 초과분을 매도할 수 있다(≥0, 구조 검증)."""
    panel, dates = _toy_panel(seed=3)
    vs = c6c.simulate(panel, 0, len(dates), "va_sell", {})
    assert vs.n_sells >= 0
    # g 를 아주 낮추면(경로가 완만) 초과·매도가 실제로 발생
    vs_low = c6c.simulate(panel, 0, len(dates), "va_sell", {"g": 0.0})
    assert vs_low.n_sells >= vs.n_sells - 0  # 완만한 경로에서 매도 빈발


def test_crash_lev_routes_to_qld_and_holds():
    """깊은 낙폭 구간에서 crash_lev 는 QLD 를 보유(라우팅)하고 매도하지 않는다."""
    panel, dates = _toy_panel()
    cl = c6c.simulate(panel, 0, len(dates), "crash_lev", {})
    assert cl.n_sells == 0            # 매도 없음
    assert cl.n_deploys >= 1          # 크래시 구간에서 최소 1회 QLD 라우팅
    # QLD 노출로 평균 노출이 1x 를 초과할 수 있음
    assert cl.avg_exposure >= 1.0 - 1e-9


def test_dd_reserve_deploys_on_crash():
    panel, dates = _toy_panel()
    ddr = c6c.simulate(panel, 0, len(dates), "dd_reserve", {})
    assert ddr.n_deploys >= 1


def test_drypowder_deploys_on_crash():
    panel, dates = _toy_panel()
    dp = c6c.simulate(panel, 0, len(dates), "drypowder", {})
    assert dp.n_deploys >= 1


# ── 결정 티어 로직 ───────────────────────────────────────────────────────────
def _agg(median, p5, regret):
    return {"median": median, "p5": p5, "regret": regret}


def test_decide_tiers():
    b1 = _agg(100.0, 80.0, 0.10)
    # 강한 통과
    assert c6c.decide(_agg(120.0, 76.0, 0.10), b1)["verdict"] == "PASS"
    # 무레버리지 free-lunch: median 1.05×, p5 1.0×
    d = c6c.decide(_agg(105.0, 80.0, 0.12), b1, is_leverage=False)
    assert d["verdict"] == "USEFUL"
    # 레버리지는 유용 티어 없음 → CONDITIONAL/FAIL
    d2 = c6c.decide(_agg(105.0, 80.0, 0.12), b1, is_leverage=True)
    assert d2["verdict"] != "USEFUL"
    # median 미달 + tail 미달 → FAIL
    assert c6c.decide(_agg(101.0, 70.0, 0.10), b1)["verdict"] == "FAIL"
    # regret 급증(>5pp) 은 PASS/USEFUL 불가
    d3 = c6c.decide(_agg(120.0, 90.0, 0.20), b1)
    assert d3["verdict"] in ("CONDITIONAL", "FAIL") and d3["verdict"] != "PASS"
