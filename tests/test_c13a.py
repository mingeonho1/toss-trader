"""c13a 테스트 — 승리신호 × 고베타 바스켓(순수 stdlib + 합성 패널, 네트워크 불요).

검증 포인트(task):
- **lookahead_guard**(사양 §2.3): c13a 파이프라인(c12 신호 빌더 → c13a 레그 remap)이 미래가격
  교란에 불변(인과적). 5개 승리 신호(FTLT·HolyGrail·Simple·A1·A2) 전부 + 누수 대조군.
- 레그 remap 정확성: 위험선호 3x→HIBETA, UVXY→현금, 인버스→현금/PSQ, 채권→SHY/BIL.
- 승자 심볼이 전부 매핑됨(미매핑=현금 드롭 방지).
- 비용 티어(HIBETA 3bp / 1x ETF 1bp / 10bp 수수료 / 5bp 슬립) + micro(매수무료) 산식.
"""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import research as R  # noqa: E402
from toss_trader.research import LookaheadLeak  # noqa: E402
import c12_composer as CC  # noqa: E402
import c12_trend_vol as CT  # noqa: E402
import c13a_signal_hibeta as C13  # noqa: E402


# ── 합성 패널(네트워크 불요) ─────────────────────────────────────────────────
def _dates(n):
    return [date(2016, 1, 4) + timedelta(days=i) for i in range(n)]


def _walk(rng, n, start=100.0, drift=0.0, vol=0.02):
    px = [start]
    for _ in range(n - 1):
        px.append(max(1e-3, px[-1] * (1.0 + drift + rng.gauss(0.0, vol))))
    return px


def _panel(symbols, n=420, seed=7):
    rng = random.Random(seed + (hash(tuple(symbols)) % 1000))
    return {s: _walk(rng, n, start=50.0 + 10 * (i % 5), drift=0.0002 * ((i % 3) - 1))
            for i, s in enumerate(symbols)}, _dates(n)


# ── 1) lookahead_guard: c12 신호 + c13a remap 전체 파이프라인이 인과적 ────────
def _composer_guarded(strat, inverse_to="cash"):
    def fn(pc, d, b=strat.build, p=strat.base_p, inv=inverse_to):
        W = b(pc, d, p)
        return [C13.remap_leg(w, inverse_to=inv)[0] for w in W]
    return fn


def _trendvol_guarded(sig, params):
    def fn(pc, d, s=sig, pp=params):
        tw, _ = s(pc, d, **pp)
        return [C13.remap_leg(w)[0] for w in tw]
    return fn


@pytest.mark.parametrize("sid", ["c12_b1_ftlt", "c12_b2_holygrail", "c12_b5_simple"])
def test_lookahead_guard_composer_pipeline(sid):
    reg = {s.sid: s for s in CC.registry()}
    strat = reg[sid]
    S, dates = _panel(list(dict.fromkeys(strat.symbols)))
    assert R.lookahead_guard(_composer_guarded(strat), S, dates) is True
    # 인버스 있는 설정은 PSQ 변형도 인과적
    assert R.lookahead_guard(_composer_guarded(strat, "PSQ"), S, dates) is True


def test_lookahead_guard_trendvol_pipeline():
    S, dates = _panel(["QQQ"])
    assert R.lookahead_guard(_trendvol_guarded(CT.sig_a1, {"sma_win": 200, "lev": "TQQQ"}),
                             S, dates) is True
    assert R.lookahead_guard(_trendvol_guarded(CT.sig_a2, {"entry": 0.05, "exit": 0.03, "lev": "TQQQ"}),
                             S, dates) is True


def test_lookahead_guard_catches_leak_control():
    """대조군: 내일 종가를 참조해 오늘 HIBETA 매수 → 누수를 guard 가 반드시 잡는다."""
    S, dates = _panel(["QQQ", "TQQQ"])

    def leaky(pc, d):
        n = len(d)
        out = [{} for _ in range(n)]
        for t in range(n - 1):
            if pc["TQQQ"][t + 1] > pc["TQQQ"][t]:
                out[t], _ = {"HIBETA": 1.0}, None
        return out

    with pytest.raises(LookaheadLeak):
        R.lookahead_guard(leaky, S, dates)


# ── 2) 레그 remap 정확성 ─────────────────────────────────────────────────────
def test_remap_risk_on_to_hibeta():
    for s in ("TQQQ", "SPXL", "TECL", "SOXL", "UPRO", "FAS"):
        rw, unk = C13.remap_leg({s: 1.0})
        assert rw == {"HIBETA": 1.0} and not unk, s


def test_remap_vol_and_inverse_to_cash():
    assert C13.remap_leg({"UVXY": 1.0})[0] == {}          # 변동성 → 현금
    assert C13.remap_leg({"SQQQ": 1.0})[0] == {}          # 인버스 1차 → 현금
    assert C13.remap_leg({"SQQQ": 1.0}, inverse_to="PSQ")[0] == {"PSQ": 1.0}
    assert C13.remap_leg({"SOXS": 1.0}, inverse_to="PSQ")[0] == {"PSQ": 1.0}


def test_remap_bonds_and_1x():
    assert C13.remap_leg({"BSV": 1.0})[0] == {"SHY": 1.0}
    assert C13.remap_leg({"BIL": 1.0})[0] == {"BIL": 1.0}
    assert C13.remap_leg({"QQQ": 1.0})[0] == {"QQQ": 1.0}
    assert C13.remap_leg({"SPY": 1.0})[0] == {"QQQ": 1.0}   # 1x 주식 ETF → QQQ 대용


def test_remap_mixed_leg_aggregates():
    rw, unk = C13.remap_leg({"SOXL": 0.5, "TECL": 0.5})     # 둘 다 3x → HIBETA 합산
    assert rw == {"HIBETA": 1.0} and not unk


def test_all_winner_symbols_are_mapped():
    """5개 승리 신호가 참조하는 모든 심볼이 매핑됨(미지=현금 드롭 방지)."""
    reg = {s.sid: s for s in CC.registry()}
    syms = set()
    for sid in ("c12_b1_ftlt", "c12_b2_holygrail", "c12_b5_simple"):
        syms |= set(reg[sid].symbols)
    syms |= {"QQQ", "TQQQ", "BIL"}                          # A1/A2
    for s in syms:
        _, unk = C13.remap_leg({s: 1.0})
        assert not unk, f"미매핑 심볼(현금 드롭 위험): {s}"


# ── 3) 비용 모델 ─────────────────────────────────────────────────────────────
def test_cost_tiers():
    c = C13.c13a_cost(1.0)
    assert c.commission_bps == 10.0 and c.slippage_bps == 5.0
    assert c.half_spread_for("HIBETA") == 3.0              # 주식 바스켓
    assert c.half_spread_for("QQQ") == 1.0                 # 1x ETF
    assert c.trade_bps("HIBETA") == pytest.approx(18.0)    # 10+3+5
    assert c.trade_bps("QQQ") == pytest.approx(16.0)       # 10+1+5
    c2 = C13.c13a_cost(2.0)
    assert c2.trade_bps("HIBETA") == pytest.approx(36.0)


def test_micro_fee_buys_free():
    fn = C13.micro_fee_fn()
    assert fn("BUY", 1000.0) == pytest.approx((3.0 + 5.0) * 1e-4 * 1000.0)   # 수수료 0
    assert fn("SELL", 1000.0) == pytest.approx((10.0 + 3.0 + 5.0) * 1e-4 * 1000.0)
    assert fn("BUY", 1000.0) < fn("SELL", 1000.0)
