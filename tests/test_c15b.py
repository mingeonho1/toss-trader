"""c15b 테스트 — Lane A 승자 앙상블(순수 stdlib, 네트워크 불요).

검증 포인트(task):
- 앙상블 시뮬레이터: 동일 스트림 앙상블 = 패스스루(무비용), 반상관 스트림 = 분산이득(변동성 축소).
- 리밸런스 비용: 월말·회전 발생시에만, micro(매수무료)·2× 스트레스 산식.
- 역변동성 가중: 저변동 스트림에 더 큰 비중.
- 신호 투표 오버레이: ≥3 위험선호 → ew 보유, 아니면 현금. 토글시에만 스위치비용.
- 상관/지표 헬퍼, Lane A 판정(파산가드·a·c·유의성) 논리.
"""
from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

import c15b_ensemble as C15  # noqa: E402


# ── 합성 스트림 헬퍼 ──────────────────────────────────────────────────────────
def _dates(n, start=date(2019, 1, 1)):
    return [start + timedelta(days=i) for i in range(n)]


def _stream(name, dates, rets, *, group="legal", spread=3.0, risk_on=None):
    by = {dates[i]: rets[i] for i in range(len(dates))}
    return C15.Stream(name, group, spread, list(dates), list(rets), by, risk_on)


def _std(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


# ── 1) 앙상블 시뮬레이터: 동일 스트림 = 패스스루(무비용) ──────────────────────
def test_identical_streams_passthrough():
    n = 300
    d = _dates(n)
    rng = [0.001 * ((i % 7) - 3) for i in range(n)]      # 결정론적
    a = _stream("a", d, rng)
    b = _stream("b", d, rng)
    rd, port, cd = C15.simulate_ensemble([a, b], "equal")
    # 두 스트림이 동일하면 드리프트 대칭 → 월말 회전 0 → 비용 0 → 앙상블=구성원.
    for i, day in enumerate(rd):
        assert port[i] == pytest.approx(a.by_date[day], abs=1e-12)


# ── 2) 반상관 스트림 = 분산이득(변동성 축소) ──────────────────────────────────
def test_anticorrelated_streams_reduce_vol():
    n = 300
    d = _dates(n)
    drift = 0.0003
    e = [0.01 * ((i % 5) - 2) for i in range(n)]
    a = _stream("a", d, [drift + e[i] for i in range(n)])
    b = _stream("b", d, [drift - e[i] for i in range(n)])
    rd, port, _ = C15.simulate_ensemble([a, b], "equal")
    va = _std(a.rets)
    vp = _std(port)
    assert vp < 0.5 * va                                  # 반상관 EW → 변동성 대폭 축소


# ── 3) 역변동성 가중: 저변동 스트림에 더 큰 비중 ──────────────────────────────
def test_ivol_weights_favor_low_vol():
    # k 시점 트레일링 수익으로 vol 산출. lo-vol vs hi-vol 두 구성원.
    n = 120
    lo = _stream("lo", _dates(n), [0.001 * ((i % 3) - 1) for i in range(n)])   # 작은 변동
    hi = _stream("hi", _dates(n), [0.02 * ((i % 3) - 1) for i in range(n)])    # 큰 변동
    R_mat = [[lo.rets[k], hi.rets[k]] for k in range(n)]
    w = C15._target_weights([lo, hi], R_mat, 100, "ivol", lookback=63)
    assert w[0] > w[1]                                    # 저변동에 더 큰 비중
    assert w[0] + w[1] == pytest.approx(1.0)


def test_equal_weights():
    w = C15._target_weights([1, 2, 3, 4], None, 5, "equal")
    assert w == [0.25, 0.25, 0.25, 0.25]


# ── 4) 리밸런스 비용: micro(매수무료) < 일반 < 2× ─────────────────────────────
def test_side_cost_micro_and_stress():
    normal_buy = C15._side_cost(3.0, "BUY", mult=1.0, micro=False)
    micro_buy = C15._side_cost(3.0, "BUY", mult=1.0, micro=True)
    normal_sell = C15._side_cost(3.0, "SELL", mult=1.0, micro=False)
    stress_buy = C15._side_cost(3.0, "BUY", mult=2.0, micro=False)
    assert normal_buy == pytest.approx((10 + 3 + 5) * 1e-4)
    assert micro_buy == pytest.approx((0 + 3 + 5) * 1e-4)   # 매수 수수료 0
    assert micro_buy < normal_buy
    assert normal_sell == pytest.approx((10 + 3 + 5) * 1e-4)  # 매도는 수수료 유지
    assert stress_buy == pytest.approx(2.0 * normal_buy)


def test_rebalance_cost_only_on_turnover():
    # 드리프트로 월말 회전 발생 → 앙상블 CAGR < 무비용 근사(비용 차감 확인).
    n = 400
    d = _dates(n)
    a = _stream("a", d, [0.003 for _ in range(n)], spread=3.0)       # 상승 드리프트
    b = _stream("b", d, [-0.001 for _ in range(n)], spread=3.0)      # 하락 드리프트
    rd, port, _ = C15.simulate_ensemble([a, b], "equal", mult=1.0)
    _, port2, _ = C15.simulate_ensemble([a, b], "equal", mult=2.0)
    # 2× 비용이면 리밸런스 비용이 커져 누적수익이 더 낮다.
    eq1 = 1.0
    for r in port:
        eq1 *= (1 + r)
    eq2 = 1.0
    for r in port2:
        eq2 *= (1 + r)
    assert eq2 < eq1                                     # 회전 비용이 실제로 부과됨


# ── 5) 신호 투표 오버레이 ─────────────────────────────────────────────────────
def _ew_and_legal(n=200, all_on=True):
    d = _dates(n)
    ew = _stream("ew_legal", d, [0.002 * ((i % 4) - 1) for i in range(n)], group="legal")
    ron = {day: all_on for day in d}
    members = [_stream(f"m{j}", d, [0.0] * n, risk_on=dict(ron)) for j in range(5)]
    cash = {day: 0.0001 for day in d}
    return ew, members, cash, d


def test_vote_all_on_holds_ew():
    ew, members, cash, d = _ew_and_legal(all_on=True)
    rd, port = C15.simulate_vote(ew, members, cash, need=3)
    # 항상 위험선호(5≥3) → ew 보유. 첫날만 진입 스위치비용, 이후엔 ew 그대로.
    for i in range(1, len(rd)):
        assert port[i] == pytest.approx(ew.by_date[rd[i]], abs=1e-12)


def test_vote_below_threshold_goes_cash():
    ew, members, cash, d = _ew_and_legal(all_on=False)
    rd, port = C15.simulate_vote(ew, members, cash, need=3)
    for i in range(len(rd)):
        assert port[i] == pytest.approx(cash[rd[i]], abs=1e-12)   # 위험선호 없음 → 현금


def test_vote_switch_cost_on_toggle():
    n = 120
    d = _dates(n)
    ew = _stream("ew_legal", d, [0.0] * n)
    # 앞 절반 위험선호 3개, 뒤 절반 0개 → 중간에 한 번 토글.
    members = []
    for j in range(5):
        ron = {}
        for i, day in enumerate(d):
            ron[day] = (j < 3 and i < n // 2)
        members.append(_stream(f"m{j}", d, [0.0] * n, risk_on=ron))
    cash = {day: 0.0 for day in d}
    rd, port = C15.simulate_vote(ew, members, cash, need=3)
    # ew·cash 수익이 모두 0이므로, 음수 수익은 오직 스위치 비용에서만 나온다.
    neg = [p for p in port if p < 0]
    assert len(neg) >= 1                                 # 최소 한 번(진입) 스위치 비용
    assert all(p <= 0 for p in port)


# ── 6) 상관/지표 헬퍼 ─────────────────────────────────────────────────────────
def test_pearson_bounds():
    a = [0.01, -0.02, 0.03, -0.01, 0.02]
    assert C15._pearson(a, a) == pytest.approx(1.0)
    assert C15._pearson(a, [-x for x in a]) == pytest.approx(-1.0)


def test_corr_matrix_diag_one():
    n = 120
    d = _dates(n)
    m1 = _stream("m1", d, [0.001 * ((i % 3) - 1) for i in range(n)])
    m2 = _stream("m2", d, [0.002 * ((i % 5) - 2) for i in range(n)])
    cm = C15.corr_matrix([m1, m2], d, upto=True)          # 2019 합성일 → 설계창
    assert cm["matrix"]["m1"]["m1"] == pytest.approx(1.0)
    assert cm["matrix"]["m2"]["m2"] == pytest.approx(1.0)
    assert cm["matrix"]["m1"]["m2"] == pytest.approx(cm["matrix"]["m2"]["m1"])


def test_split_metrics_design_holdout():
    # 설계(2019~2021)와 홀드아웃(2022+)이 분리되는지.
    d = [date(2020, 1, 1) + timedelta(days=30 * i) for i in range(48)]  # 4년치 월간
    rets = [0.01] * len(d)
    sp = C15._split(rets, d)
    assert sp["design"]["n"] > 1 and sp["holdout"]["n"] > 1
    assert sp["design"]["cagr"] > 0 and sp["holdout"]["cagr"] > 0


# ── 7) Lane A 판정 논리 ───────────────────────────────────────────────────────
def test_verdict_bankruptcy_guard():
    v, _ = C15.lane_a_verdict(0.3, 0.3, 0.3, 0.3, 0.2, 0.15, -0.96, rc_p=0.001, spa_p=0.001)
    assert v == "FAIL"


def test_verdict_a_fail():
    # 홀드아웃이 QQQ 미달 → (a) 실패 → FAIL.
    v, _ = C15.lane_a_verdict(0.3, 0.10, 0.28, 0.10, 0.2, 0.15, -0.5, rc_p=0.001, spa_p=0.001)
    assert v == "FAIL"


def test_verdict_pass_requires_sig():
    v_pass, _ = C15.lane_a_verdict(0.3, 0.3, 0.3, 0.3, 0.2, 0.15, -0.5, rc_p=0.001, spa_p=0.001)
    assert v_pass == "PASS"
    v_cond, _ = C15.lane_a_verdict(0.3, 0.3, 0.3, 0.3, 0.2, 0.15, -0.5, rc_p=0.20, spa_p=0.20)
    assert v_cond == "CONDITIONAL"                        # a·c 충족하나 유의성 미달


def test_verdict_cost2x_breakdown():
    # (a) 충족, 2×비용에서 홀드 QQQ 미달 → CONDITIONAL.
    v, _ = C15.lane_a_verdict(0.3, 0.3, 0.28, 0.10, 0.2, 0.15, -0.5, rc_p=0.001, spa_p=0.001)
    assert v == "CONDITIONAL"


# ── 8) 공통달력 교집합 ────────────────────────────────────────────────────────
def test_common_dates_intersection():
    d1 = _dates(10, start=date(2019, 1, 1))
    d2 = _dates(10, start=date(2019, 1, 5))              # 4일 오프셋
    a = _stream("a", d1, [0.0] * 10)
    b = _stream("b", d2, [0.0] * 10)
    cd = C15.common_dates([a, b])
    assert cd == sorted(set(d1) & set(d2))
    assert cd[0] == date(2019, 1, 5) and cd[-1] == date(2019, 1, 10)
