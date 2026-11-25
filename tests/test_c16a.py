"""c16a(GEM) + c16 공용 엔진 테스트 — 순수 stdlib, 네트워크 불요(합성 패널).

- lookahead_guard: sig_gem 무누출 + 누출 신호는 검출(양방향).
- GEM 규칙: 절대모멘텀 음수 → AGG, 상대모멘텀 → SPY/ACWX, 월말에만 갱신.
- 시뮬레이터: 목표 불변이면 무매매, t+1 체결, 토스 수수료(≤$10 매수 무료·매도 최소), DCA 입금 FX.
- 게이트 배선: 임시 원장으로 evaluate_idea/run_card 전체 경로 + peek-once.
"""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

import c16_alloc_engine as E  # noqa: E402
import c16a_gem as G  # noqa: E402
from toss_trader import gate, research as R  # noqa: E402


def _bdays(n, start=date(2016, 1, 4)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _path(n, drift, amp=0.0, period=300, seed=0.0):
    out, p = [], 100.0
    for i in range(n):
        p *= 1.0 + drift + amp * math.sin(2 * math.pi * (i + seed) / period)
        out.append(p)
    return out


def _panel(n=2800):
    dates = _bdays(n)
    panel = {
        "SPY": _path(n, 0.0004, 0.004, 500),
        "ACWX": _path(n, 0.0003, 0.004, 430, 90),
        "AGG": _path(n, 0.0001),
        "BIL": _path(n, 0.00008),
        "QQQ": _path(n, 0.0005, 0.005, 520),
        "SCHD": _path(n, 0.0003, 0.002, 400),
        "GLD": _path(n, 0.0002, 0.003, 350, 40),
        "EFA": _path(n, 0.0002, 0.004, 450, 10),
        "IEF": _path(n, 0.00012, 0.001, 600),
        "VNQ": _path(n, 0.0002, 0.005, 380, 70),
    }
    return dates, panel


# ── 룩어헤드 ────────────────────────────────────────────────────────────────
def test_lookahead_guard_gem_clean():
    dates, panel = _panel(900)
    assert R.lookahead_guard(lambda p, d: G.sig_gem(p, d, lookback=252), panel, dates)


def test_lookahead_guard_detects_leak():
    dates, panel = _panel(900)

    def leaky(p, d):
        def decide_at(t):
            if t + 5 >= len(d):
                return None
            return {"SPY": min(1.0, 0.5 * p["SPY"][t + 5] / p["SPY"][t])}   # 미래가격 참조
        return E.carry_month_end(d, decide_at)

    with pytest.raises(R.LookaheadLeak):
        R.lookahead_guard(leaky, panel, dates)


# ── GEM 규칙 ────────────────────────────────────────────────────────────────
def test_gem_rules_and_month_end_only():
    n = 400
    dates = _bdays(n)
    up, flat, down = _path(n, 0.001), _path(n, 0.0001), _path(n, -0.001)
    me = R.month_end_flags(dates)
    w = G.sig_gem({"SPY": up, "ACWX": flat, "AGG": flat, "BIL": flat}, dates, lookback=252)
    assert all(x == {} for x in w[:252])
    t = next(i for i in range(260, n) if me[i])
    assert w[t] == {"SPY": 1.0}
    w2 = G.sig_gem({"SPY": flat, "ACWX": up, "AGG": flat, "BIL": _path(n, 0.00005)}, dates, lookback=252)
    assert w2[t] == {"ACWX": 1.0}
    w3 = G.sig_gem({"SPY": down, "ACWX": up, "AGG": flat, "BIL": flat}, dates, lookback=252)
    assert w3[t] == {"AGG": 1.0}                    # 절대모멘텀 음수 → ACWX 가 강해도 채권
    # 비월말에는 갱신 없음
    for i in range(t + 1, n):
        if me[i]:
            break
        assert w[i] == w[t]


# ── 시뮬레이터 ──────────────────────────────────────────────────────────────
def test_simulate_no_trade_when_target_unchanged_and_t_plus_1():
    dates, panel = _panel(300)
    tg = E.const_targets(dates, {"SPY": 1.0})
    s = E.simulate(panel, dates, tg, s0=10)
    assert s.n_switch == 0
    # 최초 매수 후 무매매 → 자본 = 보유가치(가격 비례)
    k = s.equity[-1] / s.equity[0]
    assert k == pytest.approx(panel["SPY"][-1] / panel["SPY"][10], rel=1e-4)   # 잔여현금 ≈ 0
    # 종가 100 에서 바뀐 목표는 다음 거래일(101) 종가에 체결
    tg2 = [{"SPY": 1.0}] * 100 + [{"AGG": 1.0}] * 200
    s2 = E.simulate(panel, dates, tg2, s0=10)
    assert s2.n_switch == 1 and s2.switch_dates[0] == dates[101]


def test_toss_fees_small_buy_free_and_sell_min():
    c = E.COST_BASE
    assert c.fee("BUY", 9.0, 100.0) == pytest.approx(9.0 * 6 * 1e-4)        # 수수료 0 + 반호가/슬리피지
    sell = c.fee("SELL", 9.0, 100.0)
    assert sell >= 0.02                                                       # SEC+TAF 최소 $0.01씩
    assert E.cost_mult(2.0).fee("BUY", 9.0, 100.0) > 0.009                    # 스트레스: 무료구간 제거
    assert E.COST_STRESS_25BP.sched.commission_rate == pytest.approx(0.0025)


def test_dca_deposits_and_fx():
    dates, panel = _panel(300)
    tg = E.const_targets(dates, {"QQQ": 1.0})
    s = E.simulate(panel, dates, tg, s0=5, start_usd=32.0, monthly_usd=35.0)
    n_months = sum(1 for i, f in enumerate(R.month_start_flags(dates)) if f and i > 5)
    assert len(s.deposits) == 1 + n_months
    assert s.total_cost >= sum(a for _, a in s.deposits) * 20e-4 - 1e-9
    assert gate.xirr(s.cashflows()) == gate.xirr(s.cashflows())               # 유한값


def test_bil_from_dtb3_and_const_yield():
    dates = _bdays(253)
    b = E.bil_from_dtb3(dates, [(dates[0], 5.0)])
    assert b[-1] / b[0] - 1 == pytest.approx((1 + (0.05 - E.BIL_ER) / 252) ** 252 - 1, rel=1e-9)
    tr = E.const_yield_tr([100.0] * 253, 0.0252)
    assert tr[-1] / tr[0] - 1 == pytest.approx((1 + 0.0001) ** 252 - 1, rel=1e-9)


# ── 게이트 배선(임시 원장) ──────────────────────────────────────────────────
def test_evaluate_idea_logs_and_peek_once(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(E, "LEDGER", str(ledger))
    dates, panel = _panel()
    ev = E.evaluate_idea("t_gem", G.sig_gem, {"lookback": 252}, "lookback", dates, panel,
                         universe=G.NAMES, rc_B=50)
    lines = ledger.read_text().splitlines()
    assert len(lines) == 7                                   # 이웃5 + 설계center + 홀드아웃1
    assert gate.already_peeked(str(ledger), "t_gem")
    assert ev["holdout"]["decision"]["verdict"] in ("PASS", "CONDITIONAL", "FAIL")
    # 재실행 시 원장 추가 없음(peek-once)
    E.evaluate_idea("t_gem", G.sig_gem, {"lookback": 252}, "lookback", dates, panel,
                    universe=G.NAMES, rc_B=50)
    assert len(ledger.read_text().splitlines()) == 7


def test_run_card_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "LEDGER", str(tmp_path / "l.jsonl"))
    dates, panel = _panel()
    monkeypatch.setattr(E, "load_panel", lambda names, sens=False: (dates, {k: panel[k] for k in names}, {}))
    out = E.run_card("t_gem2", G.sig_gem, {"lookback": 252}, "lookback", G.NAMES,
                     control_weights=G.avg_weights, rc_B=50, with_paperlab=False)
    res = out.pop("_res")
    assert out["verdict"] in ("PASS", "CONDITIONAL", "FAIL")
    assert set(out["dca"]) == {"strategy", "B0_QQQ60_SCHD25_GLD15", "B1_QQQ100"}
    md = E.render_results(out, [])
    assert "판정" in md and "코어-위성" in md
    assert res.dates[0] >= E.EVAL_START
    import json
    json.dumps(E.jsonable(out))
