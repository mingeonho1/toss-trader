"""experiments/c2b_meanrev.py 결정론적 테스트 (순수 stdlib, 네트워크 없음).

검증 포인트:
- **모든 신호 함수에 lookahead_guard** (사양 §2.3): rsi2 / ndown / ibs / vix 신호가
  미래가격 교란에 대해 t 이하 목표비중 불변(인과성)을 만족.
- 고의 누수 신호는 LookaheadLeak 로 잡힌다(가드 자체 정상 동작 확인).
- 포지션 상태기계(진입/청산) 손계산.
- extract_trades 체결 인덱스(진입 close[i+lag], 청산 close[j+1+lag]).
- gap-down 신호의 인과성(진입=시가 i, 청산=종가 i, 신호=시가 i vs 종가 i−1).
- 합성 스택 end-to-end: evaluate_close_idea 가 원장에 적재하고 판정 dict 를 낸다.
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import research  # noqa: E402
from toss_trader.research import LookaheadLeak, lookahead_guard  # noqa: E402
import c2b_meanrev as c2b  # noqa: E402


# ── 합성 시세 생성(결정론적) ─────────────────────────────────────────────────
def _weekdays(n: int, start=date(2005, 1, 3)) -> list[date]:
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _rw_closes(n: int, seed: int = 42, drift: float = 0.0003, vol: float = 0.012
               ) -> list[float]:
    rng = random.Random(seed)
    p = 100.0
    out = [p]
    for _ in range(n - 1):
        p *= math.exp(drift + vol * rng.gauss(0, 1))
        out.append(p)
    return out


def _ohlc(closes, seed: int = 7):
    rng = random.Random(seed)
    O, H, L = [], [], []
    prev = closes[0]
    for c in closes:
        u = abs(rng.gauss(0, 1)) * 0.008 + 0.002
        dn = abs(rng.gauss(0, 1)) * 0.008 + 0.002
        hi = max(prev, c) * (1 + u)
        lo = min(prev, c) * (1 - dn)
        op = min(max(lo, prev * (1 + rng.gauss(0, 0.004))), hi)
        O.append(op); H.append(hi); L.append(lo)
        prev = c
    return O, H, L


# ── lookahead_guard: 모든 신호 함수 ──────────────────────────────────────────
def test_lookahead_guard_rsi2():
    closes = _rw_closes(700)
    panel = {"C": closes}
    fn = c2b.make_signal_fn("rsi2", {"rsi_entry": 10.0, "sma_trend": 200,
                                     "sma_exit": 5, "max_hold": 10})
    assert lookahead_guard(fn, panel, _weekdays(700)) is True


def test_lookahead_guard_ndown():
    closes = _rw_closes(700, seed=99)
    panel = {"C": closes}
    fn = c2b.make_signal_fn("ndown", {"n_down": 3, "sma_trend": 200})
    assert lookahead_guard(fn, panel, _weekdays(700)) is True


def test_lookahead_guard_ibs():
    closes = _rw_closes(500, seed=5)
    O, H, L = _ohlc(closes)
    panel = {"O": O, "H": H, "L": L, "C": closes}
    fn = c2b.make_signal_fn("ibs", {"ibs_entry": 0.2, "ibs_exit": 0.5,
                                    "max_hold": 5, "sma_trend": 0})
    assert lookahead_guard(fn, panel, _weekdays(500)) is True


def test_lookahead_guard_vix():
    closes = _rw_closes(700, seed=11)
    vix = [15.0 + 8.0 * abs(math.sin(i / 7.0)) for i in range(700)]
    panel = {"C": closes, "VIX": vix}
    fn = c2b.make_signal_fn("vix", {"mult": 1.3, "vix_win": 20, "sma_trend": 200,
                                    "sma_exit": 5, "max_hold": 10})
    assert lookahead_guard(fn, panel, _weekdays(700)) is True


def test_lookahead_guard_catches_leak():
    """가드가 실제로 미래참조를 잡는지 — 고의 누수 신호는 LookaheadLeak."""
    closes = _rw_closes(300)

    def leaky(panel, dates):
        c = panel["C"]
        n = len(c)
        # t 의 비중이 '다음 봉' 상승 여부(미래)를 참조 → 명백한 look-ahead
        return [{"POS": 1.0 if (i + 1 < n and c[i + 1] > c[i]) else 0.0}
                for i in range(n)]

    with pytest.raises(LookaheadLeak):
        lookahead_guard(leaky, {"C": closes}, _weekdays(300))


# ── 상태기계 손계산 ──────────────────────────────────────────────────────────
def test_rsi2_entry_and_exit():
    # 강한 하락(RSI2 폭락) → 진입, 이후 SMA5 상향돌파 → 청산 을 유도.
    up = [100.0 + i for i in range(210)]          # 충분히 긴 상승(SMA200 확보, 지수>SMA200)
    down = [up[-1] * (0.99 ** k) for k in range(1, 7)]   # 6일 급락 → RSI2<10
    rebound = [down[-1] * (1.03 ** k) for k in range(1, 8)]  # 반등 → close>SMA5
    closes = up + down + rebound
    des = c2b.rsi2_positions(closes, rsi_entry=10.0, sma_trend=200, sma_exit=5,
                             max_hold=10)
    # 급락 구간 끝에서 진입(어딘가 1.0 등장), 반등 뒤 청산(마지막은 0.0)
    assert any(d > 0.5 for d in des), "급락 후 진입 신호가 있어야 함"
    assert des[-1] == 0.0, "반등 후 청산되어 마지막은 flat"
    # 인과성(가드)도 통과
    assert lookahead_guard(c2b.make_signal_fn("rsi2", {"rsi_entry": 10.0,
             "sma_trend": 200, "sma_exit": 5, "max_hold": 10}),
             {"C": closes}, _weekdays(len(closes))) is True


def test_ndown_three_down_then_up_exit():
    up = [100.0 + i for i in range(210)]
    seq = up + [up[-1] * 0.99, up[-1] * 0.98, up[-1] * 0.97,  # 3연속 하락 → 진입
                up[-1] * 0.995]                               # 상승 종가 → 청산
    des = c2b.ndown_positions(seq, n_down=3, sma_trend=200)
    # 3연속 하락의 마지막 봉에서 진입
    assert des[len(up) + 2] == 1.0
    # 다음 상승 종가에서 청산
    assert des[len(up) + 3] == 0.0


def test_ibs_low_close_enters():
    closes = [100.0] * 50
    O, H, L = _ohlc(closes, seed=3)
    # 마지막 봉을 저가 근처 종가로 강제 → IBS<0.2
    H[-1] = 101.0; L[-1] = 99.0; closes[-1] = 99.1
    des = c2b.ibs_positions(O, H, L, closes, ibs_entry=0.2, ibs_exit=0.5,
                            max_hold=5, sma_trend=0)
    ibs_last = (closes[-1] - L[-1]) / (H[-1] - L[-1])
    assert ibs_last < 0.2
    assert des[-1] == 1.0, "IBS<0.2 이면 진입"


# ── extract_trades 체결 인덱스 ───────────────────────────────────────────────
def test_extract_trades_fill_indices():
    dates = _weekdays(10)
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    # 결정: day2·3 보유희망(desired=1), 나머지 0
    desired = [0, 0, 1, 1, 0, 0, 0, 0, 0, 0]
    trades = c2b.extract_trades(desired, closes, dates, "SYM", exec_lag=1)
    assert len(trades) == 1
    t = trades[0]
    # 진입 결정 i=2 → 체결 close[3]=13; 청산 결정 j+1=4 → 체결 close[5]=15
    assert t.entry_price == 13 and t.exit_price == 15
    assert t.symbol == "SYM"


def test_extract_trades_moc_lag0():
    dates = _weekdays(6)
    closes = [10, 11, 12, 13, 14, 15]
    desired = [0, 1, 1, 0, 0, 0]
    trades = c2b.extract_trades(desired, closes, dates, "S", exec_lag=0)
    t = trades[0]
    # lag0: 진입 close[1]=11, 청산 (j+1=3) close[3]=13
    assert t.entry_price == 11 and t.exit_price == 13


# ── gap-down 신호 인과성 ─────────────────────────────────────────────────────
def test_gapdown_signal_uses_only_past_close():
    dates = _weekdays(5)
    # day2 시가가 전일(day1) 종가 대비 −1.5% → 신호
    C = [100.0, 100.0, 100.0, 100.0, 100.0]
    O = [100.0, 100.0, 98.5, 100.0, 100.0]
    gap_days = [i for i in range(1, len(C)) if O[i] <= C[i - 1] * (1 - 0.01)]
    assert gap_days == [2]
    # 진입=시가 i, 청산=종가 i (같은 날, 미래 없음)
    trades = [research.Trade(entry_dt=dates[i], entry_price=O[i], exit_dt=dates[i],
                             exit_price=C[i], symbol="S") for i in gap_days]
    assert trades[0].entry_price == 98.5 and trades[0].exit_price == 100.0
    assert trades[0].entry_dt == trades[0].exit_dt


# ── 합성 스택 end-to-end ─────────────────────────────────────────────────────
def _synth_stack(n=1400):
    dates = _weekdays(n)
    sig = _rw_closes(n, seed=1)
    ex1 = list(sig)
    # 저비용 2x/3x 근사(테스트용, 정확 금융비용은 histdata 담당)
    ex2 = [sig[0]]
    ex3 = [sig[0]]
    for i in range(1, n):
        r = sig[i] / sig[i - 1] - 1.0
        ex2.append(ex2[-1] * (1 + 2 * r))
        ex3.append(ex3[-1] * (1 + 3 * r))
    cash = [0.0] * n
    vix = [15.0 + 10.0 * abs(math.sin(i / 9.0)) for i in range(n)]
    return {"dates": dates, "sig": sig, "exec": {"1x": ex1, "2x": ex2, "3x": ex3},
            "cash": cash, "vix": vix}


def test_evaluate_close_idea_end_to_end(tmp_path):
    ledger = str(tmp_path / "ledger.jsonl")
    stack = _synth_stack(1400)
    design_end = stack["dates"][900]
    out = c2b.evaluate_close_idea(
        "c2b_test", c2b.rsi2_positions,
        {"rsi_entry": 10.0, "sma_trend": 100, "sma_exit": 5, "max_hold": 10},
        stack, design_end,
        num_params=["rsi_entry", "sma_trend", "sma_exit", "max_hold"],
        ledger=ledger, rc_b=60)
    # 구조 검증
    for lev in ("1x", "2x", "3x"):
        e = out["execs"][lev]
        assert e["verdict"] in ("PASS", "CONDITIONAL", "FAIL")
        assert "holdout" in e and "trade_stats" in e
        assert "full_mdd" in e
    assert "moc_1x" in out and "overlay" in out
    # 원장 적재 확인 + 홀드아웃 peek-once
    assert Path(ledger).exists()
    lines = Path(ledger).read_text().strip().splitlines()
    assert len(lines) > 10
    holdout_ids = [l for l in lines if '"period": "holdout"' in l]
    # 1x/2x/3x/overlay = 4 개 idea_id 각 1회
    assert len(holdout_ids) == 4


def test_holdout_peek_once_enforced(tmp_path):
    from toss_trader import gate
    ledger = str(tmp_path / "l.jsonl")
    gate.append_holdout_peek(ledger, "c2b_x", {"sr_daily": 0.01})
    with pytest.raises(gate.PeekOnceError):
        gate.append_holdout_peek(ledger, "c2b_x", {"sr_daily": 0.02})
