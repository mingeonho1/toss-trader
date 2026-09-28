"""c11b 테스트 — FX 환전 타이밍 DCA 의 인과성(lookahead)·릴리스 타이밍(t−1 FX)·강제환전·
공정성(동일 out-of-pocket)·환전비/파킹이자 손검산·판정 티어.

네트워크 불필요: 전부 결정론적 토이 시계열 + 손계산. 각 테스트 주석에 손검산.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import research as R  # noqa: E402
import c11b_fx_timing as c11b  # noqa: E402


# ── 토이 유틸 ─────────────────────────────────────────────────────────────────
def _weekdays(n, d0=date(2020, 1, 6)):
    out, d = [], d0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _flat_sig(n):
    """신호가 절대 발화하지 않는 sig(강제·즉시만 동작)."""
    return {"below_ma": [False] * n, "z_below": [False] * n, "spike_below": [False] * n}


def _mixed_fx_panel(n=340, seed=11):
    """평균회귀 있는 USDKRW 토이(값>0). lookahead 가드 입력용."""
    import random
    rng = random.Random(seed)
    fx = [1200.0]
    for _ in range(1, n):
        pull = (1200.0 - fx[-1]) * 0.02          # 평균회귀
        fx.append(max(800.0, fx[-1] + pull + rng.gauss(0.0, 6.0)))
    return fx


# ── (A) lookahead 가드: 모든 환전 규칙이 인과적(신호는 fx[≤t−1] 만) ────────────
@pytest.mark.parametrize("rule", ["ma", "z", "split", "spike"])
def test_convert_signal_no_lookahead(rule):
    n = 340
    fx = _mixed_fx_panel(n)
    dates = _weekdays(n)
    fn = c11b.make_convert_signal_fn(rule)
    assert R.lookahead_guard(fn, {"FX": fx}, dates) is True


def test_lookahead_guard_catches_future_fx_leak():
    """음성대조: 미래 FX(fx[t+2])를 참조하는 환전신호는 가드가 잡는다."""
    n = 160
    fx = _mixed_fx_panel(n)
    dates = _weekdays(n)

    def leaky(panel_closes, ds):
        f = list(panel_closes["FX"])
        m = len(f)
        # 이틀 뒤 환율이 더 낮으면(원화 강세 예상) 오늘 환전 — 미래참조(누수).
        return [{"conv": 1.0} if f[min(t + 2, m - 1)] < f[t] else {} for t in range(m)]

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, {"FX": fx}, dates)


# ── (B) FRED 릴리스 타이밍: 결정은 t−1 FX(당일 fx[t] 미참조) ────────────────────
def test_decision_uses_lagged_signal_not_today():
    """sig[below_ma] 가 index 2 에서 True 여도, 환전은 그 신호를 '다음날'(t=3) 읽어 발생한다.

    같은 달 6 거래일(입금은 day0 만). sig True@2.
      · 코호트 [0,2]: 결정일 0,1,2 는 sig[-],sig[0],sig[1]=F → 미환전(t 당일 fx 미사용 증명).
      · 코호트 [0,3]: day3 는 sig[2]=True → 환전. 지연 = (day3−day0)=3일.
    """
    dates = _weekdays(6)                          # Jan 6,7,8,9,10,13
    n = len(dates)
    fx = [1000.0] * n
    parking = [0.0] * n
    ms = [True] + [False] * (n - 1)              # day0 만 월초(모두 같은 달)
    sig = _flat_sig(n)
    sig["below_ma"][2] = True                    # index 2 에서만 MA 하회

    res2 = c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, "ma", 0, 2)
    assert res2.n_conversions == 0               # 당일(fx[t]) 미사용 → index2 True 여도 미환전
    assert res2.leftover_krw == pytest.approx(c11b.MONTHLY_KRW, rel=1e-12)

    res3 = c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, "ma", 0, 3)
    assert res3.n_conversions == 1               # t−1(=index2) 신호 → day3 환전
    assert res3.leftover_krw == pytest.approx(0.0, abs=1e-9)
    # 지연 = (Jan9 − Jan6) = 3일 / 30.44
    assert res3.avg_delay_months == pytest.approx(3.0 / 30.44, rel=1e-9)


# ── (C) 강제 환전: 3개월 대기 상한 ────────────────────────────────────────────
def test_force_conversion_after_3_months():
    """신호가 영구 미발화여도 3개월 후 강제 환전(지연 ≤ ~3개월)."""
    dates = _weekdays(150)                        # ~7 개월
    n = len(dates)
    fx = [1000.0] * n
    parking = [0.0] * n
    ms = R.month_start_flags(dates)
    sig = _flat_sig(n)                            # 절대 발화 안 함
    res = c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, "ma", 0, n - 1)
    assert res.n_conversions >= 1                 # 강제로라도 환전 발생
    # 강제 상한 3개월 → 어떤 환전도 지연 3개월 크게 초과 불가(가중평균 ≤ ~3.2m)
    assert res.avg_delay_months <= 3.0 + 0.3
    assert res.avg_delay_months > 0.0


# ── (D) 공정성: 무비용·무성장·flat FX 면 전 규칙 terminal == deposited ─────────
@pytest.mark.parametrize("rule", c11b.RULES)
def test_fairness_flat_fx_no_cost_terminal_equals_deposited(rule):
    """flat FX·FX비용0·파킹0·주식수익0 이면 환전 타이밍과 무관하게 원화보존(비율 1.0×)."""
    dates = _weekdays(120)                        # ~5.7 개월(다개월 → 타이밍 실동작)
    n = len(dates)
    fx = [1300.0] * n
    parking = [0.0] * n
    ms = R.month_start_flags(dates)
    sig = _flat_sig(n)                            # ma/z/spike 미발화(강제만) — 순수 회계검증
    res = c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, rule, 0, n - 1,
                                  fx_cost=0.0)
    assert res.terminal_krw == pytest.approx(res.deposited_krw, rel=1e-9)


def test_all_rules_same_out_of_pocket():
    """동일 창·동일 스케줄 → 모든 규칙의 입금총액(원화 out-of-pocket) 동일."""
    dates = _weekdays(120)
    n = len(dates)
    fx = [1300.0] * n
    parking = [0.0] * n
    ms = R.month_start_flags(dates)
    sig = _flat_sig(n)
    deps = {r: c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, r, 0, n - 1)
            .deposited_krw for r in c11b.RULES}
    base = deps["immediate"]
    for r, d in deps.items():
        assert d == pytest.approx(base, rel=1e-12), r


# ── (E) FX 환전비 손검산(immediate: 전액 환전 → deposited/(1+cost)) ──────────────
def test_fx_cost_handcheck_immediate():
    # flat fx=1000, cost=0.0005, 성장0. 환전 USD = krw/(fx·(1+c)); 가치 = USD·fx = krw/(1+c).
    # 전액 즉시 환전 → terminal = deposited/(1+c).
    dates = _weekdays(90)
    n = len(dates)
    fx = [1000.0] * n
    parking = [0.0] * n
    ms = R.month_start_flags(dates)
    sig = _flat_sig(n)
    res = c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, "immediate", 0, n - 1,
                                  fx_cost=0.0005)
    assert res.terminal_krw == pytest.approx(res.deposited_krw / 1.0005, rel=1e-9)
    assert res.avg_delay_months == pytest.approx(0.0, abs=1e-12)
    assert res.leftover_krw == pytest.approx(0.0, abs=1e-9)


# ── (F) 파킹이자: 대기 원화가 이자를 벌어 terminal = deposited + parking_gain ────
def test_parking_interest_accrues_while_waiting():
    """flat fx·비용0·주식0·파킹>0. ma(미발화→강제)로 대기 → 이자만큼 terminal 증가."""
    dates = _weekdays(150)
    n = len(dates)
    fx = [1000.0] * n
    parking = [0.03] * n                          # 연 3% 파킹
    ms = R.month_start_flags(dates)
    sig = _flat_sig(n)
    res = c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, "ma", 0, n - 1,
                                  fx_cost=0.0)
    assert res.parking_gain_krw > 0.0
    # flat fx·무비용·무성장 → terminal = 원금 + 파킹이자(정확)
    assert res.terminal_krw == pytest.approx(res.deposited_krw + res.parking_gain_krw, rel=1e-9)
    # immediate(대기 0)는 이자 0
    imm = c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, "immediate", 0, n - 1,
                                  fx_cost=0.0)
    assert imm.parking_gain_krw == pytest.approx(0.0, abs=1e-9)


# ── (G) 판정 티어(사전등록 결정규칙) ─────────────────────────────────────────
def test_decide_fx_tiers():
    assert c11b.decide_fx({"median_ratio": 1.02, "p5_ratio": 1.00, "p_beat": 0.8}) == "FREE_LUNCH"
    assert c11b.decide_fx({"median_ratio": 1.02, "p5_ratio": 0.995, "p_beat": 0.6}) == "PASS"
    assert c11b.decide_fx({"median_ratio": 1.005, "p5_ratio": 0.97, "p_beat": 0.5}) == "CONDITIONAL"
    assert c11b.decide_fx({"median_ratio": 0.99, "p5_ratio": 0.95, "p_beat": 0.3}) == "FAIL"
    # PASS 는 p_beat<0.55 면 CONDITIONAL 로 강등
    assert c11b.decide_fx({"median_ratio": 1.02, "p5_ratio": 0.995, "p_beat": 0.5}) == "CONDITIONAL"


# ── (H) split 은 항상 절반을 즉시 환전(지연 회계에 0 포함) ─────────────────────
def test_split_converts_half_immediately():
    """split: 매 입금 50% 즉시(지연0) + 50% 는 MA 관리. flat fx·비용0 → 원화보존."""
    dates = _weekdays(120)
    n = len(dates)
    fx = [1000.0] * n
    parking = [0.0] * n
    ms = R.month_start_flags(dates)
    sig = _flat_sig(n)                            # 관리분은 강제까지 대기
    res = c11b.simulate_fx_timing(dates, [0.0] * n, fx, parking, ms, sig, "split", 0, n - 1,
                                  fx_cost=0.0)
    n_months = sum(ms)
    # 즉시 절반 환전(월수 회) + 관리분 강제 환전 이벤트 → 최소 월수 회 환전
    assert res.n_conversions >= n_months
    # 즉시분이 delay 0 으로 섞여 평균지연이 immediate(0)와 ma(≈강제) 사이
    assert 0.0 < res.avg_delay_months < 3.0
    assert res.terminal_krw == pytest.approx(res.deposited_krw, rel=1e-9)
