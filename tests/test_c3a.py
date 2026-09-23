"""c3a 회전율 제약 변동성 타깃 — 신호 look-ahead 가드 + 표현/EWMA sanity (순수 stdlib).

검증 포인트:
- 모든 신호 함수(sig_vt_ewma/ewma_3x/monthly/cashflow_unit/volofvol)가 research.lookahead_guard 통과.
- lev_to_weights 의 실효 레버리지: mix·qld_cash·tqqq_cash 각 표현에서 1·w1+2·w2+3·w3 = L.
- 최소 노셔널 성질: 노출 ΔL 변경 시 qld_cash(|ΔL|/2 한다리) < mix(양다리).
- ewma_vol 인과성(미래수익 교란에도 과거 출력 불변) + warmup None.
- week_end_flags ISO주 마지막 거래일.
"""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))

from toss_trader import research as R  # noqa: E402
import c3a_voltarget as c3a  # noqa: E402


def _toy_panel(n=420, seed=11):
    import random
    rng = random.Random(seed)
    d0 = date(2000, 1, 3)
    dates = []
    d = d0
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    l1 = [100.0]
    for _ in range(n - 1):
        l1.append(max(1.0, l1[-1] * (1.0 + rng.gauss(0.0004, 0.013))))

    def lev(series, k):
        out = [series[0]]
        for i in range(1, len(series)):
            r = series[i] / series[i - 1] - 1.0
            out.append(max(0.5, out[-1] * (1.0 + k * r)))
        return out

    return {"L1": l1, "L2": lev(l1, 2), "L3": lev(l1, 3)}, dates


SIGNALS = [
    ("sig_vt_ewma", c3a.sig_vt_ewma),
    ("sig_vt_ewma_3x", c3a.sig_vt_ewma_3x),
    ("sig_vt_monthly", c3a.sig_vt_monthly),
    ("sig_vt_cashflow_unit", c3a.sig_vt_cashflow_unit),
    ("sig_vt_volofvol", c3a.sig_vt_volofvol),
]


@pytest.mark.parametrize("name,fn", SIGNALS)
def test_signal_no_lookahead(name, fn):
    """모든 신호는 인과적 — 미래가격 교란에도 t 이하 목표비중 불변(사양 §2.3)."""
    panel, dates = _toy_panel()
    assert R.lookahead_guard(fn, panel, dates) is True


def test_lookahead_guard_catches_leak():
    """가드 음성통제: 미래 참조 신호는 LookaheadLeak."""
    panel, dates = _toy_panel(n=140)

    def leaky(pc, ds):
        n = len(ds)
        sig = pc["L1"]
        return [{"L2": 1.0} if sig[min(t + 5, n - 1)] > sig[t] else {} for t in range(n)]

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, panel, dates)


@pytest.mark.parametrize("rep,L", [
    ("mix", 0.0), ("mix", 0.5), ("mix", 1.0), ("mix", 1.5), ("mix", 2.0),
    ("qld_cash", 0.0), ("qld_cash", 0.6), ("qld_cash", 1.0), ("qld_cash", 1.7), ("qld_cash", 2.0),
    ("tqqq_cash", 0.0), ("tqqq_cash", 0.9), ("tqqq_cash", 1.5), ("tqqq_cash", 2.0),
])
def test_lev_to_weights_effective_leverage(rep, L):
    """실효 레버리지 = 1·w1 + 2·w2 + 3·w3 = L (모든 표현). 롱온리·비중합 ≤ 1."""
    w = c3a.lev_to_weights(L, rep)
    eff = w.get("L1", 0.0) + 2 * w.get("L2", 0.0) + 3 * w.get("L3", 0.0)
    assert eff == pytest.approx(L, abs=1e-9)
    assert sum(w.values()) <= 1.0 + 1e-9


def test_min_notional_property():
    """노출 1.0→1.5 변경 시 qld_cash(한 다리 0.25) < mix(양 다리 1.0) 노셔널."""
    def notional(rep, a, b):
        wa = c3a.lev_to_weights(a, rep)
        wb = c3a.lev_to_weights(b, rep)
        keys = set(wa) | set(wb)
        return sum(abs(wb.get(k, 0.0) - wa.get(k, 0.0)) for k in keys)
    n_qld = notional("qld_cash", 1.0, 1.5)
    n_mix = notional("mix", 1.0, 1.5)
    n_tqqq = notional("tqqq_cash", 1.0, 1.5)
    assert n_qld == pytest.approx(0.25, abs=1e-9)     # |ΔL|/2
    assert n_tqqq == pytest.approx(0.5 / 3.0, abs=1e-9)  # |ΔL|/3
    assert n_mix == pytest.approx(1.0, abs=1e-9)      # 두 다리(팔고 사고)
    assert n_qld < n_mix and n_tqqq < n_qld


def test_ewma_vol_causal_and_warmup():
    """EWMA 변동성: warmup 이전 None, 이후 finite, 미래 교란에 과거 출력 불변(인과)."""
    panel, dates = _toy_panel(n=300)
    rets = R.to_returns(panel["L1"])
    rv = c3a.ewma_vol(rets, warmup=60)
    assert all(v is None for v in rv[:60])
    assert rv[60] is not None and math.isfinite(rv[60])
    # 미래(>150) 교란 → t≤150 출력 불변
    rets2 = list(rets)
    for i in range(151, len(rets2)):
        rets2[i] = rets2[i] * 3.0 + 0.05
    rv2 = c3a.ewma_vol(rets2, warmup=60)
    for t in range(0, 151):
        a, b = rv[t], rv2[t]
        if a is None:
            assert b is None
        else:
            assert a == pytest.approx(b, abs=1e-12)


def test_week_end_flags():
    dates = [date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6), date(2021, 1, 7),
             date(2021, 1, 8), date(2021, 1, 11), date(2021, 1, 15)]
    assert c3a.week_end_flags(dates) == [False, False, False, False, True, False, True]


def test_signals_aligned_and_longonly():
    """신호 출력 길이 = dates, 롱온리(비중합 ≤ 1)."""
    panel, dates = _toy_panel(n=260)
    for _, fn in SIGNALS:
        w = fn(panel, dates)
        assert len(w) == len(dates)
        assert all(sum(x.values()) <= 1.0 + 1e-9 for x in w)


def test_cashflow_delever_only_drops_fast():
    """cashflow 프록시: 래칫 업은 월≤step_up, 강제 디레버는 즉시(하락 스텝이 상승 스텝보다 큼 가능)."""
    panel, dates = _toy_panel(n=300, seed=5)
    w = c3a.sig_vt_cashflow_unit(panel, dates, step_up=0.10, delever=0.75)
    lev = [x.get("L1", 0) + 2 * x.get("L2", 0) + 3 * x.get("L3", 0) for x in w]
    ups = [lev[t] - lev[t - 1] for t in range(1, len(lev)) if lev[t] > lev[t - 1]]
    # 상승 스텝은 step_up 근방 이하(월간 래칫), 큰 점프는 없음
    assert all(u <= 0.10 + 1e-9 for u in ups)
