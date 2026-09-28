"""tests/test_c4b.py — Cycle 4 · c4b 수수료 재평가 검증.

두 축:
1) **look-ahead 가드** — c4b 가 재사용하는 신호 생성기(RSI2 / events(RSI2∪TOM∪preFOMC) /
   TOM)가 인과적(미래가격 교란에 t 이하 목표비중 불변)임을 research.lookahead_guard 로 강제.
   (c4b 는 신호를 새로 만들지 않고 import 재사용하지만, 실행 래퍼가 t 이하 인과성을 깨지
   않는지 이 하네스로 확인한다.)
2) **R2 수수료 산술 손검산** — 국소 구현한 마이크로 계좌 수수료 공식이 정정 공지의
   숫자(≤$10 무료·10bp·$0.01 SEC·$0.01 TAF, 소액 fee=0.001·N+0.02)와 정확히 일치하는지,
   그리고 R1/R3 CostSpec 이 명시 인자대로(기본값 변화와 무관) 구성되는지.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in ("src", "scripts", "experiments"):
    sys.path.insert(0, str(ROOT / _p))

from toss_trader import research as R  # noqa: E402
import c2b_meanrev as c2b  # noqa: E402
import c2c_calendar as c2c  # noqa: E402
import c3b_lcc as c3b  # noqa: E402
import c4b_fee_reeval as c4b  # noqa: E402

BPS = 1e-4


# ── 공용: 결정적 합성 가격 패널 ──────────────────────────────────────────────
def _synthetic_prices(n: int = 420, seed: int = 12345) -> list[float]:
    import random
    rng = random.Random(seed)
    px = [100.0]
    for _ in range(1, n):
        px.append(max(1.0, px[-1] * (1.0 + rng.uniform(-0.03, 0.032))))
    return px


def _dates(n: int):
    from datetime import date, timedelta
    d0 = date(1990, 1, 2)
    out = []
    d = d0
    while len(out) < n:
        if d.weekday() < 5:            # 평일만(거래일 근사)
            out.append(d)
        d = d + timedelta(days=1)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 1) look-ahead 가드 — 재사용 신호 생성기
# ══════════════════════════════════════════════════════════════════════════════
def test_lookahead_rsi2_generator():
    """c2b RSI(2) 신호(=c2b_rsi2_1x·c3b_lcc_rsi2 가 쓰는 생성기)의 인과성."""
    n = 420
    prices, dates = _synthetic_prices(n), _dates(n)
    cfg = {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10}
    sig_fn = c2b.make_signal_fn("rsi2", cfg)           # panel["C"] → [{"POS": 0/1}]
    assert R.lookahead_guard(sig_fn, {"C": prices}, dates) is True


def test_lookahead_events_generator():
    """c3b events(RSI2 ∪ TOM ∪ pre-FOMC) 신호(=c3b_lcc_events·core_boost 생성기)의 인과성."""
    n = 420
    prices, dates = _synthetic_prices(n, seed=999), _dates(n)
    cfg = {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10}
    sig_fn = c3b.make_signal_fn("events", cfg)          # panel["SIG"] → [{"POS": 0/1}]
    assert R.lookahead_guard(sig_fn, {"SIG": prices}, dates) is True


def test_lookahead_tom_generator():
    """c2c Turn-of-month 캘린더 신호(=c2c_tom_1x 생성기)의 인과성(순수 날짜함수)."""
    n = 420
    prices, dates = _synthetic_prices(n, seed=7), _dates(n)
    sig = c2c.make_calendar_signal(c2c.hold_tom(1, 3), asset="NDX")
    assert R.lookahead_guard(sig, {"NDX": prices}, dates) is True


# ══════════════════════════════════════════════════════════════════════════════
# 2) R2 수수료 산술 — 손검산
# ══════════════════════════════════════════════════════════════════════════════
def test_r2_sell_reg_small_n_formula():
    """소액 N(>$10, TAF·SEC 모두 최소): fee = 0.001·N + 0.02 정확히."""
    for N in (50.0, 100.0, 200.0, 400.0):
        expected = 0.001 * N + 0.02          # 10bp 수수료 + $0.01 SEC + $0.01 TAF
        got = c4b.r2_sell_reg_usd(N, sell_price=400.0, commission_bps=10.0)
        assert got == pytest.approx(expected, abs=1e-9), (N, got, expected)


def test_r2_sell_reg_components_handchecked():
    # N=$50 → 수수료 0.05, SEC max(0.01, 0.0000206·50=0.00103)=0.01, TAF max(0.01, ...)=0.01
    assert c4b.r2_sell_reg_usd(50.0, sell_price=400.0) == pytest.approx(0.07, abs=1e-9)
    # N=$1000 → 수수료 1.00, SEC max(0.01, 0.0206)=0.0206, TAF 0.01 → 1.0306
    assert c4b.r2_sell_reg_usd(1000.0, sell_price=400.0) == pytest.approx(1.0306, abs=1e-9)
    # N=$5 (≤$10) → 수수료 0, SEC 0.01(min), TAF 0.01(min) → 0.02
    assert c4b.r2_sell_reg_usd(5.0, sell_price=400.0) == pytest.approx(0.02, abs=1e-9)


def test_r2_sec_taf_scale_above_min():
    # SEC 비례항이 min 을 넘는 지점(N > $485.4): 0.0000206·N
    N = 2000.0
    reg = c4b.r2_sell_reg_usd(N, sell_price=50.0)   # 저가(TQQQ 유사) → TAF 비례항도 커짐
    comm = 0.001 * N
    sec = max(0.01, 0.0000206 * N)                  # = 0.0412
    taf = max(0.01, 0.000166 * (N / 50.0))          # 주식수 40 → 0.00664 → min 0.01
    assert reg == pytest.approx(comm + sec + taf, abs=1e-9)
    assert sec > 0.01                                # 비례항이 min 초과 확인


def test_r2_buy_is_commission_free():
    # 매수: ≤$10 청크분할 → 수수료 0. 미시구조(반호가+슬리피지)만.
    for N, hs in ((100.0, 1.0), (37.0, 2.0)):
        micro = N * (hs + c4b.SLIP) * BPS
        assert c4b.r2_buy_fee_usd(N, hs) == pytest.approx(micro, abs=1e-12)
    # 매수엔 SEC/TAF 없음 → buy fee 는 절대 규제최소($0.02)를 포함하지 않는다
    assert c4b.r2_buy_fee_usd(5.0, 1.0) == pytest.approx(5.0 * 6 * BPS, abs=1e-12)


def test_r2_roundtrip_bps_handchecked_etf():
    # ETF(hs=1): 매수 미시 6bp, 매도 미시 6bp + reg(N=$50 → $0.07 = 14bp) → 왕복 26bp
    got = c4b.r2_roundtrip_bps(50.0, tier="ETF", sell_price=400.0)
    # buy=50*6e-4=0.03, sell=0.03+0.07=0.10, 합 0.13 → /50*1e4 = 26.0
    assert got == pytest.approx(26.0, abs=1e-6)


def test_r1_r3_roundtrip_bps():
    # R1 ETF 왕복 = 2*(10+1+5)=32; 레버리지 = 2*(10+2+5)=34; R3 = ×2
    assert c4b.r1_roundtrip_bps(tier="ETF", commission_bps=10.0) == pytest.approx(32.0)
    assert c4b.r1_roundtrip_bps(tier="LEV", commission_bps=10.0) == pytest.approx(34.0)
    assert c4b.r1_roundtrip_bps(tier="ETF", commission_bps=10.0, mult=2.0) == pytest.approx(64.0)


def test_r2_breakeven_notional_is_20():
    # break-even: 200/N = 절감된 매수수수료(10bp) → N = $20 (티어 무관)
    assert c4b.r2_breakeven_notional(tier="ETF") == pytest.approx(20.0, abs=0.2)
    assert c4b.r2_breakeven_notional(tier="LEV") == pytest.approx(20.0, abs=0.2)
    # break-even 아래(>$10 구간)는 R2 가 R1 보다 비싸고, 위는 싸다
    r1 = c4b.r1_roundtrip_bps(tier="ETF")
    assert c4b.r2_roundtrip_bps(15.0, tier="ETF") > r1     # $10<15<$20
    assert c4b.r2_roundtrip_bps(40.0, tier="ETF") < r1     # >$20


def test_r2_roundtrip_monotone_decreasing():
    prev = math.inf
    for N in (20.0, 50.0, 200.0, 1000.0, 5000.0):
        cur = c4b.r2_roundtrip_bps(N, tier="ETF", sell_price=400.0)
        assert cur < prev
        prev = cur
    # 대형 N 극한 → 매수측 수수료 절감분(≈10bp)만큼 R1 아래로 수렴
    assert c4b.r2_roundtrip_bps(1e6, tier="ETF", sell_price=400.0) == pytest.approx(
        c4b.r1_roundtrip_bps(tier="ETF") - 10.0, abs=0.5)


# ── R1/R3 CostSpec 이 명시 인자대로(기본값 변화와 무관) 구성되는지 ────────────
def test_cost_spec_explicit_commission():
    spec = c4b.cost_spec(10.0, tiers={"QQQ": "ETF", "TQQQ": "LEV"})
    assert spec.commission_bps == 10.0
    assert spec.slippage_bps == 5.0
    assert spec.trade_bps("QQQ") == pytest.approx(10.0 + 1.0 + 5.0)   # ETF hs 1
    assert spec.trade_bps("TQQQ") == pytest.approx(10.0 + 2.0 + 5.0)  # LEV hs 2
    assert spec.trade_bps("UNKNOWN") == pytest.approx(10.0 + 3.0 + 5.0)  # 기본 hs 3


def test_cost_spec_stress_doubles_all():
    r3 = c4b.cost_spec(10.0, tiers={"QQQ": "ETF"}, mult=2.0)
    assert r3.commission_bps == pytest.approx(20.0)
    assert r3.slippage_bps == pytest.approx(10.0)
    assert r3.trade_bps("QQQ") == pytest.approx(2.0 * (10.0 + 1.0 + 5.0))


def test_r2_fee_fn_buy_free_sell_charged():
    # run_dca_overlay/run_trades 훅으로 넘길 fee_fn 이 매수 무료·매도 유료를 지키는지
    pan = {"TQQQ": [60.0] * 10}
    fn = c4b.r2_fee_fn_for(pan, rep_price=60.0)
    assert fn("BUY", 100.0) == pytest.approx(100.0 * (2.0 + 5.0) * BPS)   # LEV hs 2, 수수료 0
    sell = fn("SELL", 100.0)
    assert sell > fn("BUY", 100.0)                    # 매도가 더 비쌈(수수료+규제)
    # 매도 = 미시(100*7bp) + reg(0.001*100 + 0.02) = 0.07 + 0.12 = 0.19
    assert sell == pytest.approx(100.0 * 7 * BPS + (0.001 * 100.0 + 0.02), abs=1e-9)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
