"""c7b 킬스위치 테스트 — KS look-ahead 가드(CPI 릴리즈 랙 M−1 포함), 활성 판정 항등식,
10년 히스토리 전 비활성, CPI 랙(월 t−1 사용) 인과성, '기존 QLD 미매도'·'신규 레버리지 금지',
KS 미발동 시 c5a.simulate(glide) 와 동등, CPI 헬퍼.

데이터파일 불필요(토이 Candle 패널 + 토이 월 CPI).  src/·c5a·c6a 미수정 전제.
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

from toss_trader import research as R  # noqa: E402
import c5a_lifecycle as c5a  # noqa: E402
import c7b_killswitch as c7b  # noqa: E402


# ── 토이 유틸 ────────────────────────────────────────────────────────────────
def _bdays(n, start=date(1990, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _toy_cpi(dates, annual_infl=0.02):
    """dates 전 구간을 덮는 토이 월 CPI 딕셔너리(월 복리)."""
    y0, y1 = dates[0].year, dates[-1].year
    mp = {}
    lvl = 100.0
    r_m = (1.0 + annual_infl) ** (1.0 / 12.0)
    for y in range(y0, y1 + 1):
        for mo in range(1, 13):
            mp[(y, mo)] = lvl
            lvl *= r_m
    months = sorted(mp)
    return {"map": mp, "first": months[0], "last": months[-1], "series": "TOY_CPI"}


def _toy_panel(n=3900, seed=7, drift=0.0004, vol=0.010):
    """QQQ(1x)·QLD(2x) 토이 패널(약 15년 영업일)."""
    import random
    rng = random.Random(seed)
    dates = _bdays(n)
    qqq = [100.0]
    for _ in range(1, n):
        qqq.append(max(1.0, qqq[-1] * (1.0 + rng.gauss(drift, vol))))
    qld = [100.0]
    for i in range(1, n):
        r = qqq[i] / qqq[i - 1] - 1.0
        qld.append(max(0.5, qld[-1] * (1.0 + 2.0 * r)))
    return {"QQQ": qqq, "QLD": qld, "dates": dates}


def _flat_then_infl(n=3300, nominal_drift=0.0):
    """명목 거의 정체 + CPI 상승 → 10년 실질 (−) → KS 활성 기대."""
    dates = _bdays(n)
    qqq = [100.0]
    for i in range(1, n):
        qqq.append(qqq[-1] * (1.0 + nominal_drift))
    qld = [100.0]
    for i in range(1, n):
        qld.append(qld[-1] * (1.0 + 2.0 * nominal_drift))
    return {"QQQ": qqq, "QLD": qld, "dates": dates}


# ── CPI 헬퍼 ─────────────────────────────────────────────────────────────────
def test_add_months():
    assert c7b._add_months(2000, 3, -1) == (2000, 2)
    assert c7b._add_months(2000, 1, -1) == (1999, 12)
    assert c7b._add_months(2000, 1, -12) == (1999, 1)
    assert c7b._add_months(2000, 12, 1) == (2001, 1)


def test_cpi_lookup_forward_fill_and_none():
    dates = _bdays(300)
    cpi = _toy_cpi(dates)
    last = cpi["last"]
    v_last, ff = c7b._cpi_lookup(cpi, last)
    assert ff is False
    v_beyond, ff2 = c7b._cpi_lookup(cpi, (last[0] + 5, last[1]))   # 미래 → 캐리
    assert ff2 is True and v_beyond == v_last
    v_before, ff3 = c7b._cpi_lookup(cpi, (cpi["first"][0] - 5, 1))  # 과거 → None
    assert v_before is None and ff3 is False


# ── 활성 판정: 실질<0 iff 활성 ──────────────────────────────────────────────
def test_ks_active_when_real_negative():
    panel = _flat_then_infl(nominal_drift=0.0)          # 명목 0, 인플레 +2%
    cpi = _toy_cpi(panel["dates"], annual_infl=0.02)
    ks = c7b.compute_ks_active(panel["QQQ"], panel["dates"], cpi, 10)
    # 10년 히스토리 쌓인 뒤에는 실질 (−) → 활성
    assert ks["active"][-1] is True
    assert ks["real_ret"][-1] is not None and ks["real_ret"][-1] < 0


def test_ks_inactive_when_real_positive():
    panel = _flat_then_infl(nominal_drift=0.0004)        # 명목 +≈10%/yr > 인플레 2%
    cpi = _toy_cpi(panel["dates"], annual_infl=0.02)
    ks = c7b.compute_ks_active(panel["QQQ"], panel["dates"], cpi, 10)
    assert ks["active"][-1] is False
    assert ks["real_ret"][-1] > 0


def test_ks_inactive_before_10y_history():
    panel = _flat_then_infl(nominal_drift=0.0)
    cpi = _toy_cpi(panel["dates"], annual_infl=0.02)
    ks = c7b.compute_ks_active(panel["QQQ"], panel["dates"], cpi, 10)
    dates = panel["dates"]
    for t, d in enumerate(dates):
        if (d - dates[0]).days < 10 * 365 - 30:
            assert ks["active"][t] is False
            assert ks["enough"][t] is False


# ── CPI 릴리즈 랙: 결정월 M 에서 M−1 만 사용, M 은 미사용 ───────────────────
def test_cpi_uses_month_t_minus_1_not_current():
    panel = _flat_then_infl(nominal_drift=0.00005)       # 실질 ≈ 0 근방(민감)
    dates = panel["dates"]
    base = _toy_cpi(dates, annual_infl=0.02)
    # 마지막 결정일의 월 M
    t = len(dates) - 1
    M = (dates[t].year, dates[t].month)
    Mm1 = c7b._add_months(M[0], M[1], -1)
    # (a) 현재월 M 의 CPI 를 크게 교란 → 결정 t 는 불변(현재월 미사용)
    cpi_cur = {"map": dict(base["map"]), "first": base["first"], "last": base["last"],
               "series": "T"}
    cpi_cur["map"][M] = base["map"][M] * 5.0
    r0 = c7b.compute_ks_active(panel["QQQ"], dates, base, 10)["real_ret"][t]
    r_cur = c7b.compute_ks_active(panel["QQQ"], dates, cpi_cur, 10)["real_ret"][t]
    assert r_cur == pytest.approx(r0, abs=1e-12)          # 현재월 교란 → 영향 없음
    # (b) M−1 의 CPI 를 교란 → 결정 t 가 실제로 바뀜(M−1 사용 증명)
    cpi_prev = {"map": dict(base["map"]), "first": base["first"], "last": base["last"],
                "series": "T"}
    cpi_prev["map"][Mm1] = base["map"][Mm1] * 1.10
    r_prev = c7b.compute_ks_active(panel["QQQ"], dates, cpi_prev, 10)["real_ret"][t]
    assert abs(r_prev - r0) > 1e-6


# ── look-ahead 가드(가격 인과성) + 음성대조 ─────────────────────────────────
def test_ks_signal_no_lookahead():
    panel = _toy_panel(n=3900)
    cpi = _toy_cpi(panel["dates"], annual_infl=0.02)
    fn = c7b.make_ks_signal_fn(cpi, 10)
    assert R.lookahead_guard(fn, {"QQQ": panel["QQQ"], "QLD": panel["QLD"]},
                             panel["dates"]) is True


def test_ks_signal_lookahead_negative_control():
    """미래 가격을 참조하는 KS 는 LookaheadLeak 를 던진다(가드가 실제 누수를 잡는지)."""
    panel = _toy_panel(n=800)
    dates = panel["dates"]

    def leaky(pc, ds):
        n = len(ds)
        sig = pc["QQQ"]
        # 미래(t+5) 가격을 보고 목표비중 결정 → 누수
        return [{"QLD": 1.0} if sig[min(t + 5, n - 1)] > sig[t] else {"QQQ": 1.0}
                for t in range(n)]

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}, dates)


# ── KS 미발동 시 c5a.simulate(glide) 와 완전 동등 ────────────────────────────
def test_equals_c5a_glide_when_ks_never_active():
    panel = _toy_panel(n=1200, seed=3)
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    sigma = c5a.ewma_vol_annual(panel["QQQ"])
    trend = c5a.trend_ok_series(panel["QQQ"])
    cash = [0.0] * len(dates)
    ref = c5a.simulate(closes, dates, 0, len(dates), "glide", sigma=sigma, trend=trend, cash=cash)
    ks_off = [False] * len(dates)
    got = c7b.simulate_ks(closes, dates, 0, len(dates), ks_off, sigma=sigma, trend=trend, cash=cash)
    assert got.final_value == pytest.approx(ref.final_value, rel=1e-12)
    assert got.total_deposited == pytest.approx(ref.total_deposited, rel=1e-12)
    assert got.n_sells == ref.n_sells and got.n_buys == ref.n_buys
    assert got.avg_exposure == pytest.approx(ref.avg_exposure, rel=1e-12)


# ── '신규 레버리지 금지' & '기존 QLD 미매도' ────────────────────────────────
def test_ks_active_blocks_new_leverage():
    """KS 상시 활성 → 평균노출 ≤ 1.0(신규 레버리지 없음); glide(비활성)는 > 1.0."""
    panel = _toy_panel(n=1500, seed=5, drift=0.0006)
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    sigma = [0.2] * len(dates)
    trend = [True] * len(dates)
    cash = [0.0] * len(dates)
    on = c7b.simulate_ks(closes, dates, 0, len(dates), [True] * len(dates),
                         sigma=sigma, trend=trend, cash=cash)
    off = c7b.simulate_ks(closes, dates, 0, len(dates), [False] * len(dates),
                          sigma=sigma, trend=trend, cash=cash)
    assert on.avg_exposure <= 1.0 + 1e-9
    assert on.max_exposure <= 1.0 + 1e-9
    assert off.avg_exposure > 1.0                        # glide 는 초기 레버리지


def test_ks_does_not_force_sell_existing_qld():
    """QLD 축적 후 KS 발동 → 매도 억제(작은 sell_band 로도 강제매도 0), QLD 보유 유지."""
    panel = _toy_panel(n=1600, seed=8, drift=0.0007)
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    sigma = [0.2] * len(dates)
    trend = [True] * len(dates)
    cash = [0.0] * len(dates)
    half = len(dates) // 2
    ks_toggle = [i >= half for i in range(len(dates))]   # 전반 glide(QLD 축적) → 후반 KS
    # 작은 sell_band: 억제 없으면 KS 발동시 E_target=1.0 로 대량 매도가 나야 함
    res = c7b.simulate_ks(closes, dates, 0, len(dates), ks_toggle, sigma=sigma, trend=trend,
                          cash=cash, sell_band=0.01)
    assert res.n_sells == 0                              # KS 활성월 매도 억제
    # 참조: 상시 활성이면 애초에 QLD 를 안 사므로 종가가 더 낮음(레버리지 미보유 확인)
    always_on = c7b.simulate_ks(closes, dates, 0, len(dates), [True] * len(dates),
                                sigma=sigma, trend=trend, cash=cash, sell_band=0.01)
    assert res.final_value > always_on.final_value       # 기존 QLD 를 계속 보유 → 상승분 수취


# ── active_spans sanity ──────────────────────────────────────────────────────
def test_active_spans():
    dates = _bdays(10)
    active = [False, True, True, False, False, True, False, False, False, False]
    sp = c7b.active_spans(dates, active)
    assert sp["ever"] is True
    assert sp["n_active_days"] == 3
    assert len(sp["spans"]) == 2
    assert sp["spans"][0][2] == 2 and sp["spans"][1][2] == 1
    off = c7b.active_spans(dates, [False] * 10)
    assert off["ever"] is False and off["n_active_days"] == 0
