"""c7c 테스트 — Shiller 파서 fixture, 월간 2x 근사 보정 수학(EWMA 인과성·손검산·
드래그 단조성·보정 보수성), 그리고 규칙 신호의 lookahead 가드(월간 재사용 인과성).

네트워크 불필요: 파서/EWMA/NAV 는 inline fixture, lookahead 는 결정론적 토이 월간 패널.
보정 보수성 테스트만 캐시된 FRED(data/_hist_cache/fred/)를 쓰며 없으면 skip.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import research as R  # noqa: E402
from toss_trader.models import Candle  # noqa: E402
import c5a_lifecycle as c5a  # noqa: E402
import c6c_contrib as c6c  # noqa: E402
import c7c_shiller as c7c  # noqa: E402


# ── Shiller 파서 fixture ──────────────────────────────────────────────────────
_FIXTURE = (
    "Date,SP500,Dividend,Earnings,Consumer Price Index,Long Interest Rate,"
    "Real Price,Real Dividend,Real Earnings,PE10\n"
    "1871-01-01,4.44,0.26,0.4,12.46,5.32,109.05,6.39,9.82,0.0\n"
    "1871-02-01,4.50,0.26,0.4,12.84,5.32,110.0,6.3,9.8,0.0\n"
    "1929-09-01,31.30,0.94,1.6,17.3,3.39,600.0,18.0,30.0,32.0\n"
    "1932-06-01,4.77,0.66,0.5,13.6,3.53,110.0,15.0,11.0,9.0\n"
    "2023-06-01,4345.37,70.0,200.0,304.0,3.75,5000.0,80.0,230.0,30.0\n"
    "2023-07-01,4500.00,0.0,0.0,0.0,3.9,0.0,0.0,0.0,0.0\n"     # 말미 미보고(절단 대상)
    "2023-08-01,4400.00,0.0,0.0,0.0,3.9,0.0,0.0,0.0,0.0\n"
)


def test_parse_shiller_basic_shape():
    rows = c7c.parse_shiller_csv(_FIXTURE)
    # 말미 배당/CPI=0 두 행은 절단 → 5행
    assert len(rows) == 5
    d0, p0, div0, cpi0, lr0 = rows[0]
    assert d0 == date(1871, 1, 1)
    assert p0 == pytest.approx(4.44)
    assert div0 == pytest.approx(0.26)
    assert cpi0 == pytest.approx(12.46)
    assert lr0 == pytest.approx(5.32)
    assert rows[-1][0] == date(2023, 6, 1)   # 마지막 유효월


def test_parse_shiller_known_crash_values():
    """1929-09 peak 31.30, 1932-06 trough 4.77 (Shiller 알려진 값)."""
    rows = c7c.parse_shiller_csv(_FIXTURE)
    by = {d: (p, div, cpi) for d, p, div, cpi, _lr in rows}
    assert by[date(1929, 9, 1)][0] == pytest.approx(31.30)
    assert by[date(1932, 6, 1)][0] == pytest.approx(4.77)
    dd = by[date(1932, 6, 1)][0] / by[date(1929, 9, 1)][0] - 1.0
    assert dd == pytest.approx(-0.8476, abs=1e-3)   # 월중평균 명목 -84.8%


def test_parse_shiller_truncates_trailing_missing():
    rows = c7c.parse_shiller_csv(_FIXTURE)
    assert all(p > 0 and div > 0 and cpi > 0 for _d, p, div, cpi, _lr in rows)
    assert date(2023, 7, 1) not in {r[0] for r in rows}


def test_parse_shiller_empty_and_headeronly():
    assert c7c.parse_shiller_csv("") == []
    assert c7c.parse_shiller_csv("Date,SP500,Dividend,Earnings,CPI,LR\n") == []


# ── EWMA(GARCH-lite) 분산 — 인과성·손검산·워밍업 ──────────────────────────────
def test_ewma_var_causal_future_independent():
    """out[m] 은 returns[>=m] 에 불변(미래 불참조)."""
    base = [0.0] + [0.01 * ((-1) ** i) for i in range(1, 60)]
    ev = c7c.ewma_var(base, seed=12)
    for t in (13, 25, 40):
        pert = list(base)
        for i in range(t, len(pert)):
            pert[i] = pert[i] * 3.0 + 0.05      # 미래(>=t) 교란
        evp = c7c.ewma_var(pert, seed=12)
        for i in range(t):                      # 접두부 불변
            assert (ev[i] is None and evp[i] is None) or (ev[i] == pytest.approx(evp[i]))


def test_ewma_var_warmup_none_then_defined():
    r = [0.0] + [0.02] * 20
    ev = c7c.ewma_var(r, lam=0.94, seed=12)
    assert all(x is None for x in ev[:12])       # seed 전 None → drag 0
    assert ev[12] is not None
    assert all(x is not None for x in ev[12:])


def test_ewma_var_handchecked():
    """seed 후 첫 값 = 첫 seed 개 제곱수익률 평균, 이후 EWMA 갱신 공식."""
    seed = 4
    lam = 0.9
    r = [0.10, -0.10, 0.10, -0.10, 0.20, 0.0, 0.0, 0.0]
    ev = c7c.ewma_var(r, lam=lam, seed=seed)
    exp_seed = sum(x * x for x in r[:seed]) / seed   # = 0.01
    assert ev[seed] == pytest.approx(exp_seed)       # ev[4] 는 r[0..3] 기반
    # ev[5] = lam*ev[4] + (1-lam)*r[4]^2
    assert ev[5] == pytest.approx(lam * exp_seed + (1 - lam) * r[4] ** 2)


# ── 2x NAV 근사: 인과성·드래그 단조성·앵커 ────────────────────────────────────
def _toy_returns(n=80, seed=7):
    import random
    rng = random.Random(seed)
    return [0.0] + [rng.gauss(0.006, 0.04) for _ in range(1, n)]


def test_build_navs_causal():
    r1 = _toy_returns()
    rf = [0.003] * len(r1)
    q, lv = c7c.build_navs(r1, rf)
    t = 40
    r1p = list(r1)
    for i in range(t, len(r1p)):
        r1p[i] = r1p[i] + 0.05
    qp, lvp = c7c.build_navs(r1p, rf)
    for i in range(t):                           # 접두부 NAV 불변(인과적 구축)
        assert q[i] == pytest.approx(qp[i])
        assert lv[i] == pytest.approx(lvp[i])


def test_build_navs_drag_monotone():
    """k 클수록 2x NAV 종가 낮음(드래그 단조 → 보수적 방향)."""
    r1 = _toy_returns()
    rf = [0.003] * len(r1)
    _, lv_lo = c7c.build_navs(r1, rf, k=0.2)
    _, lv_hi = c7c.build_navs(r1, rf, k=0.6)
    assert lv_hi[-1] < lv_lo[-1]
    # 1x 는 k 무관
    q_lo, _ = c7c.build_navs(r1, rf, k=0.2)
    q_hi, _ = c7c.build_navs(r1, rf, k=0.6)
    assert q_lo[-1] == pytest.approx(q_hi[-1])


def test_build_navs_anchor():
    r1 = _toy_returns()
    rf = [0.003] * len(r1)
    q, lv = c7c.build_navs(r1, rf, anchor=100.0)
    assert q[0] == pytest.approx(100.0) and lv[0] == pytest.approx(100.0)


# ── 보정 수학: 드래그 부호(변동성 vs 추세) — 캐시 FRED 불필요 ─────────────────
def _daily_candles(returns, sym="X", start=date(2000, 1, 3)):
    from datetime import timedelta
    out = []
    nav = 100.0
    d = start
    for i, r in enumerate(returns):
        nav *= (1.0 + r)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Candle(sym, d, nav, nav, nav, nav, 0.0))
        d += timedelta(days=1)
    return out


def test_fit_k_choppy_has_more_drag_than_trend():
    """순변동성(무추세) 계열은 일일리셋 2x 드래그↑ → best_k > 추세계열."""
    import random
    rng = random.Random(3)
    n = 24 * 21
    choppy = [0.02 * ((-1) ** i) for i in range(n)]                 # 순변동성
    trend = [0.0007 + rng.gauss(0, 0.001) for _ in range(n)]        # 완만 추세, 저변동
    fk_choppy = c7c._fit_k(_daily_candles(choppy))
    fk_trend = c7c._fit_k(_daily_candles(trend))
    assert fk_choppy["best_k"] > fk_trend["best_k"]
    assert fk_choppy["best_k"] > 0.0                                # 드래그는 (+)


def test_calibration_is_conservative_if_cache_present():
    """캐시 FRED 있으면 K_DRAG 가 구간별 best-fit 최대 이상(비관적 끝)인지 확인."""
    try:
        cal = c7c.calibrate_k()
    except Exception as e:  # noqa: BLE001  캐시/네트워크 없으면 skip
        pytest.skip(f"FRED 캐시 없음: {e}")
    assert cal["K_DRAG"] >= cal["best_k_max"] - 1e-9   # 보수적
    assert cal["conservative"] is True
    # 각 구간에서 K_DRAG 근사 2x CAGR 이 진실을 크게 넘지 않음(≤ +0.5%p)
    for nm, v in cal["segments"].items():
        assert v["cagr_gap_pp"] <= 0.5, f"{nm} gap {v['cagr_gap_pp']}"


# ── lookahead 가드: 월간 패널에서 규칙 신호 인과성 ────────────────────────────
def _toy_monthly_panel(n=360, seed=11):
    """상승→>30%크래시→회복 포함 결정론적 월간 패널(1x/2x NAV)."""
    import random
    from datetime import timedelta
    rng = random.Random(seed)
    dates = []
    d = date(1900, 1, 1)
    for _ in range(n):
        dates.append(d)
        # 다음 달 1일
        y, m = (d.year + (d.month // 12)), (d.month % 12) + 1
        d = date(y, m, 1)
    r1 = [0.0]
    for i in range(1, n):
        if i < n * 0.5:
            mu = 0.010
        elif i < n * 0.65:
            mu = -0.05
        else:
            mu = 0.012
        r1.append(rng.gauss(mu, 0.035))
    rf = [0.0025] * n
    q, lv = c7c.build_navs(r1, rf)
    return dates, q, lv


def test_glide_signal_no_lookahead_monthly():
    dates, q, lv = _toy_monthly_panel()
    closes = {"QQQ": q, "QLD": lv}
    fn = c5a.make_signal_fn("glide", cash=[0.0025] * len(dates))
    assert R.lookahead_guard(fn, closes, dates) is True


def test_crashlev_signal_no_lookahead_monthly():
    dates, q, lv = _toy_monthly_panel()
    closes = {"QQQ": q, "QLD": lv}
    fn = c6c.make_signal_fn("crash_lev")
    assert R.lookahead_guard(fn, closes, dates) is True


def test_lookahead_guard_catches_leak():
    """음성대조: 미래를 보는 신호는 LookaheadLeak."""
    dates, q, lv = _toy_monthly_panel(n=120)
    closes = {"QQQ": q, "QLD": lv}

    def leaky(pc, ds):
        nn = len(ds)
        sig = pc["QQQ"]
        return [{"QQQ": 1.0 if sig[min(t + 3, nn - 1)] > sig[t] else 0.0, "QLD": 0.0}
                for t in range(nn)]

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, closes, dates)


# ── 회계/구조 기본 점검(월간 패널) ───────────────────────────────────────────
def test_short_rate_splice_uses_cp_pre1934_tb_post():
    """조달금리 접합: 1934 이전 상업어음, 이후 TB3MS(캐시 있으면)."""
    try:
        dates = [date(1900, 6, 1), date(1950, 6, 1), date(2000, 6, 1)]
        rf_ann, rf_m = c7c.build_short_rate(dates)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"FRED 캐시 없음: {e}")
    assert all(a is not None and a >= 0 for a in rf_ann)
    assert all(m >= 0 for m in rf_m)
    assert rf_m[0] == pytest.approx((rf_ann[0] / 100.0) / 12.0)
