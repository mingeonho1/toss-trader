"""Gate v2 결정론적 테스트 (순수 stdlib, 고정 시드).

검증 포인트(사양 docs/gate_v2_spec.md):
- XIRR: 손으로 푼 케이스(정확히 10%)와 다중 현금흐름의 NPV≈0 자기일치.
- DSR/PSR/E[maxSR]: 손으로 유도한 수치 예제(주석에 유도 과정).
- 정상 부트스트랩 평균 CI가 단순 합성 케이스에서 참 평균을 덮는다.
- White Reality Check / Hansen SPA: 심은 엣지는 기각, 노이즈는 기각 실패.
- 원장 peek-once: 홀드아웃 2회 적재 시 PeekOnceError.
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist

import pytest

# 저장소 관례상 소스는 src/ 아래에 있고 별도 설치가 없다(test_forward_paper.py와 동일 패턴).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import gate  # noqa: E402

_Z = NormalDist()


# ── XIRR ─────────────────────────────────────────────────────────────────────
def test_xirr_hand_computed_ten_percent():
    # 손 계산: -1000 투자(2021-01-01), +1100 회수(2022-01-01). Δt = 365일(비윤년).
    # NPV(r) = -1000 + 1100·(1+r)^(-365/365) = 0  →  (1+r) = 1100/1000 = 1.1  →  r = 0.10.
    r = gate.xirr([(date(2021, 1, 1), -1000.0), (date(2022, 1, 1), 1100.0)])
    assert abs(r - 0.10) < 1e-6


def test_xirr_multiflow_npv_zero():
    # 다중 입금 → 단조감소 NPV. 해석해가 지저분하므로 반환 r에서 NPV≈0 자기일치로 검증.
    flows = [(date(2020, 1, 1), -100.0), (date(2020, 7, 1), -100.0),
             (date(2021, 1, 1), 220.0)]
    r = gate.xirr(flows)
    t0 = flows[0][0]
    npv = sum(cf * (1 + r) ** (-((d - t0).days / 365.0)) for d, cf in flows)
    assert abs(npv) < 1e-7


def test_xirr_nan_when_no_sign_change():
    # 부호변화 없음(전손실 등) → 해 없음 → nan. 호출부는 최종자산으로 폴백(사양 §1.3).
    r = gate.xirr([(date(2020, 1, 1), -100.0), (date(2021, 1, 1), -50.0)])
    assert math.isnan(r)


# ── PSR / E[max SR] / DSR ────────────────────────────────────────────────────
def test_probabilistic_sharpe_ratio_hand_anchor():
    # 손 유도: SR_hat=0.1, SR*=0, T=101, skew=0, kurt=3(정규).
    # denom = sqrt(1 - 0·SR + ((3-1)/4)·SR²) = sqrt(1 + 0.5·0.01) = sqrt(1.005).
    # z = (0.1 - 0)·sqrt(100) / sqrt(1.005) = 1.0 / 1.0024969 = 0.9975093.
    # PSR = Φ(0.9975093) ≈ 0.84074.
    psr = gate.probabilistic_sharpe_ratio(0.1, 0.0, 101, 0.0, 3.0)
    z_hand = (0.1 * math.sqrt(100)) / math.sqrt(1.005)
    assert abs(psr - _Z.cdf(z_hand)) < 1e-12
    assert abs(psr - 0.8407413) < 1e-6


def test_expected_max_sharpe_hand_anchor():
    # 손 유도: N=10, V=0.01(→ sqrt(V)=0.1), γ=0.5772156649.
    # SR*₀ = 0.1·[(1-γ)·Φ⁻¹(0.9) + γ·Φ⁻¹(1 - 1/(10e))]
    #      = 0.1·[0.4227843·1.2815516 + 0.5772157·1.7900174] ≈ 0.157460.
    ems = gate.expected_max_sharpe(10, 0.01)
    a = _Z.inv_cdf(1 - 1 / 10)
    b = _Z.inv_cdf(1 - 1 / (10 * math.e))
    hand = 0.1 * ((1 - gate._EULER) * a + gate._EULER * b)
    assert abs(ems - hand) < 1e-12
    assert abs(ems - 0.1574598) < 1e-6


def test_expected_max_sharpe_monotone_in_N():
    # N이 커질수록 우연 상한이 올라간다(다중검정 페널티↑, 사양 §3.3 직관).
    assert gate.expected_max_sharpe(5, 0.01) < gate.expected_max_sharpe(50, 0.01)
    assert gate.expected_max_sharpe(1, 0.01) == 0.0     # N<2 → 페널티 없음


def test_deflated_sharpe_ratio_wiring_matches_components():
    rng = random.Random(1)
    rets = [rng.gauss(0.0008, 0.01) for _ in range(600)]
    trials = [rng.gauss(0.02, 0.05) for _ in range(15)]
    dsr = gate.deflated_sharpe_ratio(rets, trials, n_eff=8)
    # 손 조립: sr_hat=mean/std, V=var(trials), SR*₀=E[maxSR](8,V), 그리고 PSR.
    sd = gate._std(rets, 1)
    sr_hat = gate._mean(rets) / sd
    sk, ku = gate.skew_kurt(rets)
    star = gate.expected_max_sharpe(8, gate._std(trials, 1) ** 2)
    manual = gate.probabilistic_sharpe_ratio(sr_hat, star, len(rets), sk, ku)
    assert abs(dsr - manual) < 1e-12


def test_deflated_sharpe_more_trials_lowers_dsr():
    # 같은 수익 스트림이라도 시도 수(N)가 많다고 원장에 기록되면 DSR이 떨어진다.
    rng = random.Random(5)
    rets = [rng.gauss(0.001, 0.01) for _ in range(750)]
    trials = [rng.gauss(0.0, 0.05) for _ in range(40)]
    dsr_few = gate.deflated_sharpe_ratio(rets, trials, n_eff=3)
    dsr_many = gate.deflated_sharpe_ratio(rets, trials, n_eff=40)
    assert dsr_many < dsr_few


# ── 정상 부트스트랩 CI ───────────────────────────────────────────────────────
def test_stationary_bootstrap_ci_covers_true_mean():
    rng = random.Random(2024)
    mu = 0.0005
    x = [rng.gauss(mu, 0.01) for _ in range(1500)]
    mean, lo, hi = gate.stationary_bootstrap_mean_ci(x, B=1500, rng=random.Random(3))
    assert lo <= mu <= hi
    assert lo < mean < hi


def test_block_bootstrap_ci_covers_true_mean():
    rng = random.Random(77)
    mu = 0.0003
    x = [rng.gauss(mu, 0.008) for _ in range(1200)]
    mean, lo, hi = gate.block_bootstrap_ci(x, block=21, B=1200, rng=random.Random(4))
    assert lo <= mu <= hi


# ── White Reality Check / Hansen SPA ─────────────────────────────────────────
def _family_with_edge(edge_mean: float, seed: int, k_noise: int = 6, T: int = 500):
    r = random.Random(seed)
    diffs = {"edge": [r.gauss(edge_mean, 0.01) for _ in range(T)]}
    for k in range(k_noise):
        diffs[f"n{k}"] = [r.gauss(0.0, 0.01) for _ in range(T)]
    return diffs


def _family_noise(seed: int, k: int = 7, T: int = 500):
    r = random.Random(seed)
    return {f"n{i}": [r.gauss(0.0, 0.01) for _ in range(T)] for i in range(k)}


def test_reality_check_and_spa_reject_planted_edge():
    diffs = _family_with_edge(0.002, 42)     # 심은 엣지: 일 초과평균 0.002, std 0.01 (t≈4.5)
    rc_p, stats = gate.whites_reality_check(diffs, B=1200, rng=random.Random(7))
    spa_p = gate.hansen_spa(diffs, B=1200, rng=random.Random(9))
    assert rc_p < 0.05
    assert spa_p < 0.05
    assert stats["edge"]["d_bar"] > 0


def test_reality_check_and_spa_fail_to_reject_noise():
    diffs = _family_noise(2026)              # 전부 평균 0 → 벤치 초과 없음
    rc_p, _ = gate.whites_reality_check(diffs, B=1200, rng=random.Random(7))
    spa_p = gate.hansen_spa(diffs, B=1200, rng=random.Random(9))
    assert rc_p > 0.10
    assert spa_p > 0.10


# ── N_eff 상관 클러스터 ──────────────────────────────────────────────────────
def test_n_eff_clusters_absorbs_near_duplicates():
    base = [0.01, 0.02, -0.01, 0.03, -0.02, 0.015, -0.005]
    near = [v + 0.0005 for v in base]        # base와 거의 완전 상관
    diff = [-v for v in base]                # base와 음의 상관 → 별개 클러스터
    n = gate.n_eff_clusters({"a": base, "b": near, "c": diff}, theta=0.9)
    assert n == 2


# ── 리스크·거래 지표 ─────────────────────────────────────────────────────────
def test_max_drawdown_and_ulcer():
    eq = [100.0, 120.0, 60.0, 90.0]          # peak 120 → 60 : -50%
    assert abs(gate.max_drawdown(eq) - (-0.5)) < 1e-12
    assert gate.ulcer_index(eq) > 0


def test_cvar_worst_tail():
    rets = [-0.10, -0.05, 0.0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]
    # alpha=0.2, T=10 → k=ceil(2)=2 → 최악 2개 평균 = (-0.10-0.05)/2 = -0.075.
    assert abs(gate.cvar(rets, alpha=0.2) - (-0.075)) < 1e-12


def test_profit_factor_and_tstat_and_expectancy():
    pnls = [2.0, -1.0, 3.0, -1.0, 1.0]       # Σwin=6, |Σloss|=2 → PF=3.0
    assert abs(gate.profit_factor(pnls) - 3.0) < 1e-12
    assert abs(gate.expectancy(pnls) - 0.8) < 1e-12
    assert gate.trade_tstat(pnls) == pytest.approx(
        gate._mean(pnls) / (gate._std(pnls, 1) / math.sqrt(len(pnls))))


def test_vol_match_scale():
    rng = random.Random(11)
    rets = [rng.gauss(0.0, 0.02) for _ in range(500)]
    scale = gate.vol_match_scale(rets, target_ann_vol=0.10)
    scaled = [r * scale for r in rets]
    assert abs(gate.annualized_vol(scaled) - 0.10) < 1e-9


# ── 강건성 헬퍼 ──────────────────────────────────────────────────────────────
def test_plateau_test_flat_vs_peak():
    # 평탄: net_excess/sharpe가 파라미터에 둔감 → pass.
    def flat(params):
        return {"net_excess": 0.05, "sharpe": 1.0}
    res = gate.plateau_test(flat, {"lookback": 200, "vt": 0.10})
    assert res["pass"] and res["frac_positive"] == 1.0

    # 뾰족: 중심에서만 좋고 이웃은 음수 → fail.
    def peak(params):
        ok = params["lookback"] == 200
        return {"net_excess": 0.05 if ok else -0.02, "sharpe": 1.0 if ok else 0.1}
    res2 = gate.plateau_test(peak, {"lookback": 200})
    assert not res2["pass"]


def test_subperiod_consistency_winrate():
    start = date(2015, 1, 1)
    dates = [start + timedelta(days=i) for i in range(365 * 6)]
    strat = gate.returns_to_equity([0.0004] * (len(dates) - 1))
    bench = gate.returns_to_equity([0.0003] * (len(dates) - 1))
    strat = strat[:len(dates)]
    bench = bench[:len(dates)]
    res = gate.subperiod_consistency(strat, bench, dates, window_years=3, step_months=6)
    assert res["n_windows"] > 0
    assert res["win_rate"] == 1.0            # 매 창에서 strat이 bench 초과


def test_cost_stress_and_breakeven():
    # net_excess(cost) = 0.02 - cost. cost 인자는 (레인1) 비용배수를 fraction로 해석하는 예.
    def eval_stress(mult):
        return {"net_excess": 0.02 - 0.005 * mult}   # 1×→0.015, 2×→0.010
    cs = gate.cost_stress(eval_stress, mult=2.0)
    assert cs["pass"] and cs["net_excess_stressed"] == pytest.approx(0.010)

    # breakeven: net = 0.01 - 100·frac  → 0이 되는 frac = 1e-4 → 1 bps.
    def eval_bps(frac):
        return 0.01 - 100.0 * frac
    be = gate.breakeven_cost_bps(eval_bps, lo=0.0, hi=0.02)
    assert abs(be - 1.0) < 1e-2                # 약 1 bps


def test_start_date_randomization():
    res = gate.start_date_randomization(
        dca_fn_strat=lambda s: 100.0 + s,     # 전략이 항상 벤치보다 1 큼
        dca_fn_bench=lambda s: 99.0 + s,
        offsets=list(range(12)))
    assert res["win_rate"] == 1.0
    assert res["median_margin"] > 0


# ── 워크포워드 분할 ──────────────────────────────────────────────────────────
def test_anchored_holdout_split():
    dates = [date(2000, 1, 1) + timedelta(days=90 * i) for i in range(60)]
    design, holdout = gate.anchored_holdout(dates, date(2012, 12, 31))
    assert all(dates[i] <= date(2012, 12, 31) for i in design)
    assert all(dates[i] > date(2012, 12, 31) for i in holdout)
    assert len(design) + len(holdout) == len(dates)


def test_walk_forward_splits_have_embargo_gap():
    dates = [date(2000, 1, 1) + timedelta(days=i) for i in range(2000)]
    splits = gate.walk_forward_splits(dates, train_days=365, test_days=252,
                                      step_days=252, embargo_days=30)
    assert splits
    for train_idx, test_idx in splits:
        # purge/embargo: train의 마지막 날짜와 test 첫 날짜 사이에 gap이 있어야 한다.
        assert dates[test_idx[0]] - dates[train_idx[-1]] >= timedelta(days=1)


# ── 시도 원장 · peek-once ────────────────────────────────────────────────────
def test_ledger_append_count_and_sharpes(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    gate.ledger_append(path, {"idea_id": "a", "sr_daily": 0.05, "period": "design"})
    gate.ledger_append(path, {"idea_id": "a", "sr_daily": 0.04, "period": "design"})
    gate.ledger_append(path, {"idea_id": "b", "sr_daily": 0.02, "period": "design"})
    assert gate.ledger_count(path) == 3
    assert gate.ledger_count(path, "a") == 2
    assert gate.ledger_trial_sharpes(path, "a") == [0.05, 0.04]
    assert gate.ledger_count("/nonexistent/ledger.jsonl") == 0


def test_ledger_peek_once_raises(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    idea = "tsmom_v1"
    assert not gate.already_peeked(path, idea)
    gate.append_holdout_peek(path, idea, {"sr_daily": 0.05, "mdd": -0.3})
    assert gate.already_peeked(path, idea)
    with pytest.raises(gate.PeekOnceError):
        gate.append_holdout_peek(path, idea, {"sr_daily": 0.05})
    # 새 idea_id(새 아이디어, N++)는 허용된다.
    gate.append_holdout_peek(path, "tsmom_v2", {"sr_daily": 0.05})
    assert gate.already_peeked(path, "tsmom_v2")


def test_config_hash_stable_and_order_independent():
    h1 = gate.config_hash({"lookback": 252, "vt": 0.1})
    h2 = gate.config_hash({"vt": 0.1, "lookback": 252})
    assert h1 == h2
    assert h1 != gate.config_hash({"lookback": 200, "vt": 0.1})


# ── 결정표 ───────────────────────────────────────────────────────────────────
def _pass_metrics():
    return dict(lane=1, terminal_vs_b0=1.10, terminal_vs_b1=1.05, dsr=0.97,
                rc_pvalue=0.02, plateau_pass=True, subperiod_winrate=0.75,
                cost_stress_mult=2.5, start_date_winrate=0.80, mdd=-0.35,
                calmar_not_worse=True, ulcer_not_worse=True)


def test_decide_pass():
    d = gate.decide(_pass_metrics())
    assert str(d) == "PASS"
    assert all(v in ("PASS", "SKIP") for v in d.axes.values())


def test_decide_fail_on_mdd_hardcap():
    m = _pass_metrics()
    m["mdd"] = -0.55                          # 하드캡 −50% 위반
    d = gate.decide(m)
    assert d.verdict == "FAIL"
    assert d.axes["risk"] == "FAIL"


def test_decide_fail_on_economic_below_b0():
    m = _pass_metrics()
    m["terminal_vs_b0"] = 0.9
    assert gate.decide(m).verdict == "FAIL"


def test_decide_conditional_on_dsr_border():
    m = _pass_metrics()
    m["dsr"] = 0.92                           # 0.90–0.95 경계
    d = gate.decide(m)
    assert d.verdict == "CONDITIONAL"
    assert d.axes["significance"] == "CONDITIONAL"


def test_decide_lane3_never_pass_but_conditional_when_necessary_met():
    good = dict(lane=3, n_trades=250, trade_tstat=3.5, trade_ci_lo=0.5,
                slippage_stress_pass=True)
    assert gate.decide(good).verdict == "CONDITIONAL"
    bad = dict(lane=3, n_trades=120, trade_tstat=2.0, trade_ci_lo=-0.1,
               slippage_stress_pass=False)
    assert gate.decide(bad).verdict == "FAIL"
