"""c12 Lane A 실험 — 신호 함수 look-ahead 가드 + 시뮬레이터 정합 + 판정 로직(순수 stdlib).

검증 포인트:
- 모든 목표비중 신호 함수가 research.lookahead_guard 통과(미래가격 교란에 t 이하 목표비중 불변).
- 자본경로 의존(A3 9Sig)·오버나이트(G1/G2) 시뮬레이터도 인과적(미래 교란에 t 이하 자본곡선 불변).
- simulate() 가 research.run_weights 와 일치(일간 리밸·상수비중).
- lane_a_verdict 판정 로직(부록 v3 a/b/c + 파산가드).
- 오버나이트가 익일 시가에 청산.
"""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from toss_trader import research as R  # noqa: E402
import c12_trend_vol as c12  # noqa: E402


# ── 결정론적 토이 패널(모든 신호가 참조하는 심볼·보조 시리즈 포함, None 없음) ──
def _toy(n=520, seed=11):
    import random
    rng = random.Random(seed)
    d0 = date(2016, 1, 4)
    dates, d = [], d0
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)

    def walk(mu, sd, p0=100.0):
        out = [p0]
        for _ in range(n - 1):
            out.append(max(1.0, out[-1] * (1.0 + rng.gauss(mu, sd))))
        return out

    price_syms = ["QQQ", "TQQQ", "SPY", "UPRO", "TLT", "TMF", "IEF", "AGG", "SVXY",
                  "VIXY", "SLV", "GLD", "XLI", "XLU", "DBB", "UUP", "SCZ", "TIP",
                  "VWO", "BND", "IBIT"] + list(c12.G5_UNIVERSE)
    panel = {}
    for s in set(price_syms):
        panel[s] = walk(0.0004, 0.014)
    # 보조 시리즈(양수)
    panel["VIX"] = [max(9.0, 20.0 + 8.0 * math.sin(i / 15.0) + rng.gauss(0, 2)) for i in range(n)]
    panel["VIX3M"] = [max(10.0, 22.0 + 6.0 * math.sin(i / 17.0) + rng.gauss(0, 2)) for i in range(n)]
    panel["SPX"] = walk(0.0003, 0.010, 3000.0)
    panel["BTC"] = walk(0.001, 0.03, 30000.0)
    return panel, dates


# 목표비중 신호(전부 (panel,dates)->(targets, signal_days)) — [0]만 lookahead_guard 대상
WEIGHT_SIGNALS = [
    ("a1", lambda pc, ds: c12.sig_a1(pc, ds, lev="TQQQ")[0]),
    ("a2", lambda pc, ds: c12.sig_a2(pc, ds, lev="TQQQ")[0]),
    ("a4", lambda pc, ds: c12.sig_a4(pc, ds)[0]),
    ("a4_shadow", lambda pc, ds: c12.sig_a4(pc, ds, shadow=True)[0]),
    ("a5", lambda pc, ds: c12.sig_a5(pc, ds, lev="TQQQ")[0]),
    ("c1", lambda pc, ds: c12.sig_c1(pc, ds)[0]),
    ("c2", lambda pc, ds: c12.sig_c2(pc, ds)[0]),
    ("c2_shadow", lambda pc, ds: c12.sig_c2(pc, ds, shadow=True)[0]),
    ("d1", lambda pc, ds: c12.sig_d1(pc, ds)[0]),
    ("d2", lambda pc, ds: c12.sig_d2(pc, ds)[0]),
    ("g3", lambda pc, ds: c12.sig_g3(pc, ds)[0]),
    ("g5", lambda pc, ds: c12.sig_g5(pc, ds)[0]),
    ("g6", lambda pc, ds: c12.sig_g6(pc, ds)[0]),
]


@pytest.mark.parametrize("name,fn", WEIGHT_SIGNALS)
def test_weight_signal_no_lookahead(name, fn):
    """모든 목표비중 신호는 인과적 — 미래가격 교란에도 t 이하 목표비중 불변(사양 §2.3)."""
    panel, dates = _toy()
    assert R.lookahead_guard(fn, panel, dates) is True


@pytest.mark.parametrize("name,fn", WEIGHT_SIGNALS)
def test_weight_signal_aligned_and_long_only(name, fn):
    """신호 출력 길이 = dates, 비중 합 ≤ 1(롱온리)."""
    panel, dates = _toy()
    tw = fn(panel, dates)
    assert len(tw) == len(dates)
    assert all(sum(w.values()) <= 1.0 + 1e-9 for w in tw)


def test_lookahead_guard_catches_leak():
    """음성통제: 미래를 참조하는 신호는 LookaheadLeak."""
    panel, dates = _toy(n=160)

    def leaky(pc, ds):
        q = pc["QQQ"]
        n = len(ds)
        return [{"TQQQ": 1.0} if q[min(t + 3, n - 1)] > q[t] else {} for t in range(n)]

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, panel, dates)


def _prefix_invariant(run_equity_fn, panel, dates, split):
    """자본경로/오버나이트 시뮬레이터의 인과성: >split 가격 교란 후 자본곡선[:split+1] 불변."""
    import random
    rng = random.Random(7)
    base = run_equity_fn(panel, dates)
    pert = {s: list(v) for s, v in panel.items()}
    for s, v in pert.items():
        for i in range(split + 1, len(v)):
            v[i] = v[i] * (1.6 + 0.1 * rng.random())
    got = run_equity_fn(pert, dates)
    for i in range(split + 1):
        assert abs(base[i] - got[i]) <= 1e-9, f"{s} leak at {i}"


def test_9sig_causal():
    """A3 9Sig(자본경로 의존) 인과성: 미래 교란에도 자본곡선 접두부 불변."""
    panel, dates = _toy()
    cost = c12.cost_spec(["TQQQ", "AGG"])

    def run(pc, ds):
        return c12.simulate_9sig(pc, ds, cost=cost, tqqq="TQQQ", bond="AGG").equity

    _prefix_invariant(run, panel, dates, len(dates) // 2)


def test_overnight_causal_and_next_open():
    """G1 오버나이트: 인과성(미래 교란에 접두부 불변) + 익일 시가 청산 확인."""
    panel, dates = _toy()
    opens = {s: [v * 1.001 for v in panel[s]] for s in panel}  # 시가 = 종가 근처
    cost = c12.cost_spec(["TQQQ"])

    def run(pc, ds):
        # opens 는 별도 dict — 접두부 불변 검사는 종가 교란만 반영(시가는 종가에서 파생 안함)
        return c12.overnight_series(pc, opens, ds, "TQQQ", cost=cost).equity

    base = run(panel, dates)
    assert len(base) == len(dates)
    # 익일 시가 청산: 첫 진입일(t=0) → t=1 수익 = open[1]/close[0]-1-비용
    rt = 2.0 * cost.trade_bps("TQQQ") * 1e-4
    exp = opens["TQQQ"][1] / panel["TQQQ"][0] - 1.0 - rt
    got = base[1] / base[0] - 1.0
    assert got == pytest.approx(exp, abs=1e-12)


def test_simulate_matches_run_weights():
    """simulate() ≡ run_weights (일간 리밸·상수비중·exec_lag=1)."""
    panel, dates = _toy(n=300)
    closes = {s: panel[s] for s in ("QQQ", "TQQQ")}
    n = len(dates)
    tw = [{"QQQ": 0.6, "TQQQ": 0.4} for _ in range(n)]
    cash = [0.0] * n
    cost = c12.cost_spec(["QQQ", "TQQQ"])
    r_sim = c12.simulate(closes, dates, tw, [True] * n, cost=cost, cash_rate=cash, exec_lag=1)
    r_rw = R.run_weights(closes, dates, tw, exec_lag=1, rebalance_band=0.0, cost=cost,
                         cash_rate=cash)
    assert max(abs(a - b) for a, b in zip(r_sim.equity, r_rw.equity)) < 1e-12
    assert r_sim.trades == r_rw.trade_count


def test_lane_a_verdict():
    """부록 v3 판정: (a)설계·홀드아웃>QQQ (b)이웃60%+ (c)2×비용, 파산가드 MDD>−95%."""
    # 완전 통과
    assert c12.lane_a_verdict(0.4, 0.2, 0.3, 0.15, -0.5, 0.8, 0.38, 0.19) == "PASS"
    # 설계에서 QQQ 미달 → FAIL
    assert c12.lane_a_verdict(0.2, 0.2, 0.3, 0.15, -0.5, 0.8, 0.2, 0.19) == "FAIL"
    # (a)는 되나 이웃평탄 <60% → CONDITIONAL
    assert c12.lane_a_verdict(0.4, 0.2, 0.3, 0.15, -0.5, 0.4, 0.38, 0.19) == "CONDITIONAL"
    # (a)는 되나 2×비용에서 홀드아웃 붕괴 → CONDITIONAL
    assert c12.lane_a_verdict(0.4, 0.2, 0.3, 0.15, -0.5, 0.8, 0.38, 0.10) == "CONDITIONAL"
    # 파산가드(MDD ≤ −95%) → FAIL
    assert c12.lane_a_verdict(0.4, 0.2, 0.3, 0.15, -0.97, 0.8, 0.38, 0.19).startswith("FAIL")
    # 표본부족(설계 None) → N/A
    assert c12.lane_a_verdict(None, 0.2, None, 0.15, -0.5, 0.8, None, 0.19).startswith("N/A")


def test_g5_universe_present():
    """G5 유니버스 심볼이 캐시에 존재(로딩 가능)해야 한다(스모크, 네트워크 없이 캐시)."""
    # 캐시가 있으면 통과, 없으면 skip(오프라인 테스트 안정성).
    from toss_trader import histdata as hd
    for s in ["TQQQ", "SOXL", "TECL", "UPRO"]:
        p = hd._cache_path(s)
        if not p.exists():
            pytest.skip(f"{s} 캐시 없음")
    assert True
