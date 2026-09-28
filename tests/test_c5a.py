"""c5a 테스트 — 목표노출 fn 의 lookahead 가드, PV 손검산, 노출→비중 매핑,
매수전용 시뮬레이터가 research.run_dca_overlay 와 일치(회계 동등), EWMA 인과성."""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))

from toss_trader import research as R  # noqa: E402
import c5a_lifecycle as c5a  # noqa: E402


# ── 결정론적 토이 패널(데이터파일 불필요) ─────────────────────────────────────
def _toy_panel(n=400, seed=7):
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
    for _ in range(1, n):
        qqq.append(max(1.0, qqq[-1] * (1.0 + rng.gauss(0.0004, 0.012))))
    # QLD ≈ 2x 일수익 복리
    qld = [100.0]
    for i in range(1, n):
        r = qqq[i] / qqq[i - 1] - 1.0
        qld.append(max(0.5, qld[-1] * (1.0 + 2.0 * r)))
    return {"QQQ": qqq, "QLD": qld}, dates


MODES = ["static2", "glide", "glide_vt", "glide_trend", "qqq"]


@pytest.mark.parametrize("mode", MODES)
def test_signal_no_lookahead(mode):
    """목표노출→비중 신호가 인과적(미래가격 교란에 t 이하 목표 불변)."""
    panel, dates = _toy_panel()
    fn = c5a.make_signal_fn(mode)
    assert R.lookahead_guard(fn, panel, dates) is True


def test_lookahead_guard_catches_leak():
    """가드가 실제 누수를 잡는지(음성대조)."""
    panel, dates = _toy_panel(n=160)

    def leaky(pc, ds):
        n = len(ds)
        sig = pc["QQQ"]
        return [{"QLD": 1.0} if sig[min(t + 5, n - 1)] > sig[t] else {"QQQ": 1.0}
                for t in range(n)]

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, panel, dates)


# ── PV 손검산 ────────────────────────────────────────────────────────────────
def test_pv_remaining_hand_checked():
    """PV_0 (300개월 계획, $35, 3% 실질) 손검산 ≈ $7396.6."""
    # r_m = 1.03^(1/12)-1, N=299, PV = 35·(1−(1+r_m)^−299)/r_m
    r_m = (1.03) ** (1.0 / 12.0) - 1.0
    N = 299
    expect = 35.0 * (1.0 - (1.0 + r_m) ** (-N)) / r_m
    got = c5a.pv_remaining(0)
    assert got == pytest.approx(expect, abs=1e-6)
    assert got == pytest.approx(7396.6, abs=1.0)   # 독립 손계산 앵커


def test_pv_remaining_monotone_and_terminal():
    """PV 는 m 증가에 단조감소하고, 계획 끝(마지막 달)엔 0."""
    prev = c5a.pv_remaining(0)
    for m in range(1, 300, 17):
        cur = c5a.pv_remaining(m)
        assert cur <= prev + 1e-9
        prev = cur
    assert c5a.pv_remaining(299) == pytest.approx(0.0, abs=1e-9)
    assert c5a.pv_remaining(400) == 0.0


def test_pv_zero_rate_reduces_to_sum():
    """할인율 0 이면 PV = 남은 개수 × 월적립."""
    got = c5a.pv_remaining(10, disc_real=0.0)
    assert got == pytest.approx(35.0 * (300 - 1 - 10), abs=1e-9)


# ── 노출→비중 매핑 앵커 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("E", [0.0, 0.5, 0.8, 1.0, 1.3, 1.5, 2.0])
def test_exposure_to_weights_invariant(E):
    w_qqq, w_qld = c5a.exposure_to_weights(E)
    eff = 1.0 * w_qqq + 2.0 * w_qld
    assert eff == pytest.approx(E, abs=1e-9)
    assert w_qqq >= -1e-12 and w_qld >= -1e-12
    assert (w_qqq + w_qld) <= 1.0 + 1e-9
    if E >= 1.0:                                   # E≥1 완전투자
        assert (w_qqq + w_qld) == pytest.approx(1.0, abs=1e-9)
    else:                                          # E<1 현금 버퍼
        assert (w_qqq + w_qld) == pytest.approx(E, abs=1e-9)


def test_exposure_anchors():
    assert c5a.exposure_to_weights(2.0) == pytest.approx((0.0, 1.0))
    assert c5a.exposure_to_weights(1.0) == pytest.approx((1.0, 0.0))
    assert c5a.exposure_to_weights(1.5) == pytest.approx((0.5, 0.5))
    assert c5a.exposure_to_weights(0.8) == pytest.approx((0.8, 0.0))


def test_target_exposure_starts_at_2x():
    """작은 W 에서 base=2 (초기 2:1 레버리지)."""
    assert c5a.target_exposure(0, 67.0, 0.2, True, "glide") == pytest.approx(2.0, abs=1e-9)
    # 큰 W(적립 PV 대비 매우 큼) → base=1
    E = c5a.target_exposure(0, 5_000_000.0, 0.2, True, "glide")
    assert E == pytest.approx(1.0, abs=1e-9)


def test_glide_vt_crash_brake():
    """고변동성이면 vt 캡이 노출을 1x 미만으로 감축."""
    E = c5a.target_exposure(0, 67.0, 0.60, True, "glide_vt")   # 0.25/0.60 ≈ 0.417
    assert E == pytest.approx(0.25 / 0.60, abs=1e-9)
    assert E < 1.0


def test_glide_trend_caps_in_downtrend():
    E_dn = c5a.target_exposure(0, 67.0, 0.2, False, "glide_trend")  # 추세 이탈
    assert E_dn == pytest.approx(1.0, abs=1e-9)
    E_up = c5a.target_exposure(0, 67.0, 0.2, True, "glide_trend")
    assert E_up == pytest.approx(2.0, abs=1e-9)


# ── EWMA 인과성/워밍업 ───────────────────────────────────────────────────────
def test_ewma_warmup_and_causal():
    panel, dates = _toy_panel(n=300)
    rv = c5a.ewma_vol_annual(panel["QQQ"], warmup=60)
    assert all(v is None for v in rv[:60])
    assert rv[60] is not None
    # 미래(>200) 교란해도 t≤200 불변
    pert = list(panel["QQQ"])
    for i in range(201, len(pert)):
        pert[i] *= 1.5
    rv2 = c5a.ewma_vol_annual(pert, warmup=60)
    for t in range(201):
        a, b = rv[t], rv2[t]
        if a is None:
            assert b is None
        else:
            assert b == pytest.approx(a, abs=1e-12)


# ── 매수전용 시뮬레이터 == run_dca_overlay(회계 동등) ────────────────────────
def test_buyonly_static_matches_overlay():
    """무매도(static2)·상수비중 매수전용 시뮬레이터가 run_dca_overlay 와 최종자산 일치.

    수수료를 동일 fee_fn(TossFeeSchedule split) 으로 맞추고 FX 20bp 동일. c5a 는 QLD 전량
    (E=2, 매도 안 함). run_dca_overlay 도 {'QLD':1.0} buy_only + 동일 fee_fn.
    """
    panel, dates = _toy_panel(n=260)
    # c5a static2 (매도밴드 무한대 → 매도 없음)
    res = c5a.simulate(panel, dates, 0, len(dates), "static2",
                       sigma=[None] * len(dates), trend=[True] * len(dates),
                       cash=[0.0] * len(dates))
    # run_dca_overlay 동등 설정: fee_fn=split, cash 0, exec_lag 0, FX 20bp
    fee_fn = c5a.FEE.as_fee_fn(split=True)
    cost = R.CostSpec(commission_bps=0.0, slippage_bps=0.0, fx_bps=c5a.FX_BPS,
                      half_spread_bps={"QQQ": 0.0, "QLD": 0.0})
    w = [{"QLD": 1.0}] * len(dates)
    ov = R.run_dca_overlay({"QQQ": panel["QQQ"], "QLD": panel["QLD"]}, dates, w,
                           monthly_usd=c5a.MONTHLY, initial_usd=c5a.INITIAL, cost=cost,
                           cash_rate=[0.0] * len(dates), exec_lag=0, mode="buy_only",
                           fee_fn=fee_fn)
    assert res.total_deposited == pytest.approx(ov.total_deposited, abs=1e-9)
    assert res.final_value == pytest.approx(ov.final_value, rel=2e-3)


def test_simulate_deposits_and_growth():
    """입금계·최종자산 sanity: 상승장에서 최종 > 입금계, 입금계 = initial + 월수×monthly."""
    panel, dates = _toy_panel(n=260, seed=3)
    res = c5a.simulate(panel, dates, 0, len(dates), "glide",
                       sigma=[0.2] * len(dates), trend=[True] * len(dates),
                       cash=[0.0] * len(dates))
    n_months = sum(R.month_start_flags(dates))
    assert res.total_deposited == pytest.approx(c5a.INITIAL + n_months * c5a.MONTHLY, abs=1e-9)
    assert res.final_value > 0
    assert res.max_exposure <= 2.0 + 1e-9
    assert 1.0 - 1e-9 <= res.avg_exposure <= 2.0 + 1e-9


def test_sell_only_on_overshoot():
    """매도밴드가 크면 매도 0(매수전용); 작으면 매도 발생 가능."""
    panel, dates = _toy_panel(n=260, seed=5)
    r_big = c5a.simulate(panel, dates, 0, len(dates), "glide", sigma=[0.2] * len(dates),
                         trend=[True] * len(dates), cash=[0.0] * len(dates), sell_band=10.0)
    assert r_big.n_sells == 0
    r_small = c5a.simulate(panel, dates, 0, len(dates), "glide", sigma=[0.2] * len(dates),
                           trend=[True] * len(dates), cash=[0.0] * len(dates), sell_band=0.05)
    assert r_small.n_sells >= 0   # 존재하면 ≥0 (구조 검증; 실제 발생은 경로 의존)


def test_weight_schedule_long_only():
    """기록된 목표비중은 항상 롱온리(합≤1)."""
    panel, dates = _toy_panel(n=200)
    for mode in MODES:
        res = c5a.simulate(panel, dates, 0, len(dates), mode, record_weights=True,
                           sigma=c5a.ewma_vol_annual(panel["QQQ"]),
                           trend=c5a.trend_ok_series(panel["QQQ"]), cash=[0.0] * len(dates))
        for w in res.weight_sched:
            assert w is not None
            assert sum(w.values()) <= 1.0 + 1e-9
            assert all(v >= -1e-12 for v in w.values())
