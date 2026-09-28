"""c8b 테스트 — 한국 세제 DCA 시뮬레이터의 세금·배당·환율·하베스팅 손검산.

네트워크 불필요: 전부 inline 토이 패널 + 손계산 기대값. 각 테스트 주석에 손검산을 남긴다.
검증 대상: (1) 양도세 공식, (2) 배당 원천징수 15%·순액 재투자, (3) 무세 시 gross TR 일치,
(4) 환차익 과세, (5) 이익 하베스팅이 원가를 스텝업해 최종 CGT 를 정확히 줄임, (6) FX 스프레드,
(7) 단위자본 KRW 순수익 스트림.
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

from toss_trader import tax as T  # noqa: E402
from toss_trader.fees import TossFeeSchedule  # noqa: E402
import c8b_allocation_tax as c8b  # noqa: E402

# 무수수료 스케줄(순수 세금 손검산용) — 수수료는 별도 테스트/실험에서 검증.
ZERO_FEE = TossFeeSchedule(commission_rate=0.0, sec_fee_rate=0.0, taf_per_share=0.0,
                           apply_regulatory_min=False)


# ── (1) 양도세 공식 손검산 ────────────────────────────────────────────────────
def test_annual_tax_handcheck():
    # 순실현손익 ₩5,000,000 → 과세표준 = 5,000,000 − 2,500,000 = 2,500,000
    #                        → 세액 = 2,500,000 × 0.22 = 550,000
    assert T.annual_tax(5_000_000) == pytest.approx(550_000.0)
    # 공제 이하 → 0
    assert T.annual_tax(2_000_000) == 0.0
    assert T.annual_tax(-1_000_000) == 0.0


def _run(dates, px_ret, fx, weights, closes, **kw):
    return c8b.simulate_dca_tax(dates, px_ret, fx, weights, 0, len(dates) - 1,
                                closes=closes, **kw)


# ── (3) 무세·무배당 → gross TR 정확 일치 ─────────────────────────────────────
def test_no_tax_matches_gross_tr():
    # 같은 달 3거래일, X 가격 +10%/일 2회. 시드 ₩1,000 @ fx1000 → $1 매수(≤$10 무료).
    # 종가: $1 → 1.1 → 1.21. 세금·배당·환율변동·스프레드 0 → 세후 = ₩1,210.
    dates = [date(2020, 1, 6), date(2020, 1, 7), date(2020, 1, 8)]
    px = {"X": [0.0, 0.10, 0.10]}
    fx = [1000.0, 1000.0, 1000.0]
    closes = {"X": [100.0, 110.0, 121.0]}
    res = _run(dates, px, fx, {"X": 1.0}, closes, wht=0.0, cgt_rate=0.0,
               fx_spread=0.0, harvest=False, monthly_krw=0.0, initial_krw=1000.0,
               div_yield={"X": 0.0})
    assert res.aftertax_terminal_krw == pytest.approx(1210.0, rel=1e-9)
    assert res.pretax_terminal_krw == pytest.approx(1210.0, rel=1e-9)
    assert res.cgt_krw == 0.0
    assert res.div_tax_krw == 0.0
    assert res.fee_usd == 0.0                          # $1 매수는 ≤$10 무료


# ── (2) 배당 원천징수 15% + 순액 재투자 손검산 ───────────────────────────────
def test_dividend_withholding_and_reinvest():
    # X 가격 flat, 배당수익률 25.2%/yr → 일 dd = 0.252/252 = 0.001 (=0.1%/일).
    # start 이후 2일 배당 accrue. 시드 ₩1,000 @ fx1000 → $1.0.
    # Day1: gross=1.0*0.001=0.001; div_tax += 0.001*0.15*1000 = 0.15
    #        net=0.001*0.85=0.00085 → v=1.00085, basis += 0.85
    # Day2: gross=1.00085*0.001=0.00100085; div_tax += 0.150128 → 총 ≈ 0.300128
    # 배당은 재투자로 원가에 편입 → 양도차익 ≈ 0 → CGT ≈ 0. 세후 ≈ 평가액.
    dates = [date(2020, 1, 6), date(2020, 1, 7), date(2020, 1, 8)]
    px = {"X": [0.0, 0.0, 0.0]}
    fx = [1000.0, 1000.0, 1000.0]
    closes = {"X": [100.0, 100.0, 100.0]}
    res = _run(dates, px, fx, {"X": 1.0}, closes, wht=0.15, cgt_rate=0.22,
               deduction=0.0, fx_spread=0.0, harvest=False, monthly_krw=0.0,
               initial_krw=1000.0, div_yield={"X": 0.252})
    assert res.div_tax_krw == pytest.approx(0.300128, rel=1e-4)
    # 평가액 = 1000 * (1 + 0.85*0.001)^2  (환율 1000, 순배당 복리)
    expect_v_usd = 1.0 * (1 + 0.85 * 0.001) ** 2
    assert res.pretax_terminal_krw == pytest.approx(expect_v_usd * 1000.0, rel=1e-6)
    # 배당 재투자가 원가를 키워 양도차익≈0 → CGT≈0
    assert res.cgt_krw == pytest.approx(0.0, abs=1e-3)


# ── (4) 환차익 과세 손검산 ────────────────────────────────────────────────────
def test_fx_gain_is_taxed():
    # X 가격 flat, 배당 0, 환율 1000 → 2000(달러가치 2배). 시드 ₩1,000 → $1 @ fx1000, basis ₩1,000.
    # 최종 평가 = $1 * 2000 = ₩2,000. 양도차익 = 2,000 − 1,000 = ₩1,000 (순수 환차익).
    # 공제 0 → CGT = 1,000 * 0.22 = 220. 세후 = 2,000 − 220 = ₩1,780.
    dates = [date(2020, 1, 6), date(2020, 1, 7), date(2020, 1, 8)]
    px = {"X": [0.0, 0.0, 0.0]}
    fx = [1000.0, 1000.0, 2000.0]
    closes = {"X": [100.0, 100.0, 100.0]}
    res = _run(dates, px, fx, {"X": 1.0}, closes, wht=0.0, cgt_rate=0.22,
               deduction=0.0, fx_spread=0.0, harvest=False, monthly_krw=0.0,
               initial_krw=1000.0, div_yield={"X": 0.0})
    assert res.pretax_terminal_krw == pytest.approx(2000.0, rel=1e-9)
    assert res.cgt_krw == pytest.approx(220.0, rel=1e-9)
    assert res.aftertax_terminal_krw == pytest.approx(1780.0, rel=1e-9)


# ── (6) FX 스프레드 = 환전 손실 ───────────────────────────────────────────────
def test_fx_spread_cost():
    # 스프레드 0.5% → $ 수령 = 1000 / (1000*1.005) = 0.995024...; 환전손실(원화, mid 대비)
    #  = 1000 − 0.995024*1000 = 4.975...  가격/배당/환율변동 0.
    dates = [date(2020, 1, 6), date(2020, 1, 7)]
    px = {"X": [0.0, 0.0]}
    fx = [1000.0, 1000.0]
    closes = {"X": [100.0, 100.0]}
    res = _run(dates, px, fx, {"X": 1.0}, closes, wht=0.0, cgt_rate=0.0,
               fx_spread=0.005, harvest=False, monthly_krw=0.0, initial_krw=1000.0,
               div_yield={"X": 0.0})
    usd = 1000.0 / (1000.0 * 1.005)
    assert res.fx_cost_krw == pytest.approx(1000.0 - usd * 1000.0, rel=1e-9)
    assert res.pretax_terminal_krw == pytest.approx(usd * 1000.0, rel=1e-9)


# ── (5) 이익 하베스팅이 원가 스텝업 → 최종 CGT 를 정확히 감소 ──────────────────
def test_harvest_steps_up_basis_and_reduces_cgt():
    # 1종목, 배당·환율변동 0, fx=1000. 시드 ₩10,000,000 → $10,000 (price $100 → 100주).
    # 가격: 2020말까지 +100%(2배) → 평가 $20,000 = ₩20,000,000, 미실현이익 ₩10,000,000.
    # 연말(2020, 2021) 하베스팅: 각 공제 ₩2,500,000 만큼 실현(비과세)·원가 스텝업.
    #   → 원가 10,000,000 → 12,500,000 → 15,000,000 (하베스팅 총 ₩5,000,000).
    # 최종(2022) 청산: 이익 = 20,000,000 − 15,000,000 = ₩5,000,000
    #   → CGT = 0.22*(5,000,000 − 2,500,000) = ₩550,000.
    # 하베스팅 미적용: 원가 10,000,000 → 이익 10,000,000
    #   → CGT = 0.22*(10,000,000 − 2,500,000) = ₩1,650,000.
    # 차이(하베스팅 효익) = 1,650,000 − 550,000 = ₩1,100,000 (= 0.22 * 5,000,000). 무수수료로 정확 검증.
    dates = [date(2020, 6, 1), date(2020, 12, 31), date(2021, 12, 31), date(2022, 6, 1)]
    px = {"X": [0.0, 1.0, 0.0, 0.0]}
    fx = [1000.0, 1000.0, 1000.0, 1000.0]
    closes = {"X": [100.0, 200.0, 200.0, 200.0]}
    common = dict(wht=0.0, cgt_rate=0.22, deduction=2_500_000.0, fx_spread=0.0,
                  monthly_krw=0.0, initial_krw=10_000_000.0, div_yield={"X": 0.0},
                  fee_sched=ZERO_FEE)
    hv = _run(dates, px, fx, {"X": 1.0}, closes, harvest=True, **common)
    nh = _run(dates, px, fx, {"X": 1.0}, closes, harvest=False, **common)

    assert nh.cgt_krw == pytest.approx(1_650_000.0, rel=1e-9)
    assert hv.harvested_krw == pytest.approx(5_000_000.0, rel=1e-9)
    assert hv.cgt_krw == pytest.approx(550_000.0, rel=1e-9)
    assert hv.fee_usd == 0.0
    # 하베스팅 효익 = 정확히 ₩1,100,000 (= 0.22 * 사전 실현 5,000,000)
    diff = hv.aftertax_terminal_krw - nh.aftertax_terminal_krw
    assert diff == pytest.approx(1_100_000.0, rel=1e-9)


# ── (7) 단위자본 KRW 순수익 스트림 ────────────────────────────────────────────
def test_unit_capital_krw_returns():
    # 가격수익 0, 배당 25.2%/yr(일 0.001), wht 0.15 → 순배당수익 = 0.85*0.001 = 0.00085/일.
    # 환율 불변 → 단위자본 일수익 = 0.00085.
    px = {"X": [0.0, 0.0, 0.0]}
    fx = [1000.0, 1000.0, 1000.0]
    r = c8b.unit_capital_krw_returns(px, fx, {"X": 1.0}, 0, 2, wht=0.15,
                                     div_yield={"X": 0.252})
    assert r == pytest.approx([0.00085, 0.00085], rel=1e-9)
    # 환율 +1%/일 추가 시: (1+0.00085)*(1.01) − 1
    fx2 = [1000.0, 1010.0, 1020.1]
    r2 = c8b.unit_capital_krw_returns(px, fx2, {"X": 1.0}, 0, 2, wht=0.15,
                                      div_yield={"X": 0.252})
    exp = (1 + 0.00085) * 1.01 - 1
    assert r2 == pytest.approx([exp, exp], rel=1e-9)


# ── 데이터 정직성: 사전등록 배당수익률·후보 합이 1.0 ─────────────────────────
def test_candidate_weights_sum_to_one():
    for name, w in c8b.CANDIDATES.items():
        assert sum(w.values()) == pytest.approx(1.0), name
        for s in w:
            assert s in c8b.DIV_YIELD, f"{name}:{s} 배당수익률 미정의"
