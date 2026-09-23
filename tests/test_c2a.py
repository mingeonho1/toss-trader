"""c2a 레버리지 실험 — 신호 함수 look-ahead 가드 + 소형 sanity (순수 stdlib).

검증 포인트(요구사항):
- 모든 신호 함수(sig_lrr/sig_vol_target/sig_combo/sig_const/sig_vixregime)가
  research.lookahead_guard 를 통과한다(미래가격 교란에 t 이하 목표비중 불변).
- lev_to_weights 의 실효 레버리지 매핑 정확성(1·w1+2·w2+3·w3 = L).
- week_end_flags 가 ISO주 마지막 거래일을 정확히 표시.
- run_strategy 스모크: 상수 1x 는 L1 매수홀드와 사실상 동일(비용 차감분만 차이).
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
import c2a_leverage as c2a  # noqa: E402


# ── 결정론적 토이 패널(수치 VIX 포함, None 없음) ─────────────────────────────
def _toy_panel(n=420, seed=7):
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
        l1.append(max(1.0, l1[-1] * (1.0 + rng.gauss(0.0004, 0.012))))
    # 합성 2x/3x 근사(일수익 배수) + 양수 VIX
    def lev(series, k):
        out = [series[0]]
        for i in range(1, len(series)):
            r = series[i] / series[i - 1] - 1.0
            out.append(max(0.5, out[-1] * (1.0 + k * r)))
        return out
    vix = [max(9.0, 20.0 + 8.0 * math.sin(i / 15.0) + rng.gauss(0, 2)) for i in range(n)]
    return {"L1": l1, "L2": lev(l1, 2), "L3": lev(l1, 3), "VIX": vix}, dates


SIGNALS = [
    ("sig_lrr", c2a.sig_lrr),
    ("sig_vol_target", c2a.sig_vol_target),
    ("sig_combo", c2a.sig_combo),
    ("sig_const", c2a.sig_const),
    ("sig_vixregime", c2a.sig_vixregime),
]


@pytest.mark.parametrize("name,fn", SIGNALS)
def test_signal_no_lookahead(name, fn):
    """모든 신호 함수는 인과적 — 미래가격 교란에도 t 이하 목표비중 불변(사양 §2.3)."""
    panel, dates = _toy_panel()
    assert R.lookahead_guard(fn, panel, dates) is True


def test_lookahead_guard_catches_leak():
    """가드가 실제로 누수를 잡는지(음성통제): 미래를 참조하는 신호는 LookaheadLeak."""
    panel, dates = _toy_panel(n=120)

    def leaky(pc, ds):
        n = len(ds)
        sig = pc["L1"]
        # t 에서 t+5 미래 가격을 참조(명백한 누수)
        out = []
        for t in range(n):
            fut = sig[min(t + 5, n - 1)]
            out.append({"L2": 1.0} if fut > sig[t] else {})
        return out

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, panel, dates)


@pytest.mark.parametrize("L", [0.0, 0.4, 1.0, 1.3, 2.0, 2.6, 3.0])
def test_lev_to_weights_effective_leverage(L):
    """실효 레버리지 = 1·w1 + 2·w2 + 3·w3 = L (인접 슬리브 혼합)."""
    w = c2a.lev_to_weights(L)
    eff = w.get("L1", 0.0) + 2 * w.get("L2", 0.0) + 3 * w.get("L3", 0.0)
    assert eff == pytest.approx(L, abs=1e-9)
    # 비중 합 ≤ 1(롱온리), L≥1 이면 완전투자
    assert sum(w.values()) <= 1.0 + 1e-9
    if L >= 1.0:
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-9)


def test_week_end_flags():
    """ISO주 마지막 거래일 플래그(금요일 또는 그 주 마지막 거래일). 연속 주 경계 확인."""
    dates = [date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6), date(2021, 1, 7),
             date(2021, 1, 8),                       # 금(주말 전) → True
             date(2021, 1, 11), date(2021, 1, 15)]   # 다음 주 월…금 → 15일 True
    fl = c2a.week_end_flags(dates)
    assert fl == [False, False, False, False, True, False, True]


def test_const_1x_matches_buyhold():
    """상수 1x 전략 ≈ L1 매수홀드(초기 진입 비용만 차감). 스모크."""
    panel, dates = _toy_panel(n=300)
    cash = [0.0] * len(dates)
    res, _ = c2a.run_strategy(c2a.sig_const, panel, dates, cash,
                              band=0.0, cost=R.CostSpec(), params={"lev": 1.0})
    # 진입 후 단일 슬리브 홀드: 워밍업(진입 비용에 따른 미세 잔조정) 이후엔 무매매.
    assert res.trade_count <= 10
    assert sum(res.trades[20:]) == 0
    bh = panel["L1"][-1] / panel["L1"][0]
    got = res.equity[-1] / res.equity[0]
    # 25bp+반호가+슬리피지 편도 진입비용(≈0.3%) 만큼만 낮아야(레버리지·현금흐름 없음)
    assert got == pytest.approx(bh, rel=0.01)
    assert got < bh


def test_signals_return_aligned_weights():
    """신호 함수 출력 길이 = dates, 비중 합 ≤ 1(롱온리)."""
    panel, dates = _toy_panel(n=260)
    for _, fn in SIGNALS:
        w = fn(panel, dates)
        assert len(w) == len(dates)
        assert all(sum(x.values()) <= 1.0 + 1e-9 for x in w)
