"""experiments/c3b_lcc.py 결정론적 테스트 (순수 stdlib, 네트워크 없음).

검증 포인트(사양 §2.3 / README §4):
- **모든 c3b 신호에 lookahead_guard**: rsi2 / events(union) / prefomc / tom 신호가 미래 '가격'
  교란에 대해 t 이하 목표비중 불변(인과성).
- **캘린더 인과성(prefix invariance)**: events/prefomc/tom 은 순수 date 함수(next_trading_day 합성)
  라 배열을 뒤에서 잘라도 각 인덱스 목표비중 불변. 고의 누수 신호는 잡힌다.
- 메커니즘 항등식: 3x·1/3노셔널의 노출당 왕복비용이 1x 의 ≈1/3, 경로감쇠는 음(-).
- 거래 인덱스 추출(진입 i+lag, 청산 (j+1)+lag)·슬리브/코어위성 손계산.
- 합성 스택 end-to-end: gate_eval_config 원장 적재 + 홀드아웃 peek-once.
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

from toss_trader import research as R  # noqa: E402
from toss_trader.research import LookaheadLeak, lookahead_guard  # noqa: E402
import c2c_calendar as C  # noqa: E402
import c3b_lcc as c3b  # noqa: E402


# ── 합성 시세 ─────────────────────────────────────────────────────────────────
def _sched_dates(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if C.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _closes(n: int) -> list[float]:
    return [100.0 + 5.0 * math.sin(i / 4.0) + 0.05 * i for i in range(n)]


def _panel(dates):
    c = _closes(len(dates))
    # 신호(SIG=가격지수), 실행(TR 1x, X3 3x)까지 lookahead_guard 가 교란할 심볼들
    x3 = [c[0]]
    for i in range(1, len(c)):
        r = c[i] / c[i - 1] - 1.0
        x3.append(x3[-1] * (1 + 3 * r))
    return {"SIG": list(c), "NDXTR": list(c), "TQQQ3X": x3}


BASE_RSI = {"rsi_entry": 10.0, "sma_trend": 100, "sma_exit": 5, "max_hold": 10}


# ── lookahead_guard: 모든 신호 ────────────────────────────────────────────────
@pytest.mark.parametrize("kind,cfg", [
    ("rsi2", BASE_RSI),
    ("events", BASE_RSI),
    ("prefomc", {}),
    ("tom", {"tom": (1, 3)}),
])
def test_all_signals_pass_price_lookahead_guard(kind, cfg):
    dates = _sched_dates(date(2015, 1, 2), 620)
    panel = _panel(dates)
    fn = c3b.make_signal_fn(kind, cfg)
    assert lookahead_guard(fn, panel, dates) is True


def test_lookahead_guard_catches_leak():
    """가드가 실제로 미래참조를 잡는지 — 고의 누수 신호는 LookaheadLeak."""
    dates = _sched_dates(date(2015, 1, 2), 300)
    panel = _panel(dates)

    def leaky(pan, ds):
        # 다음 봉이 25%↑면 보유 — 가드의 미래교란(≥1.7×)이 분할점에서 확실히 0→1 로 뒤집는다.
        c = pan["SIG"]
        n = len(c)
        return [{"POS": 1.0 if (i + 1 < n and c[i + 1] > 1.25 * c[i]) else 0.0}
                for i in range(n)]

    with pytest.raises(LookaheadLeak):
        lookahead_guard(leaky, panel, dates)


# ── 캘린더 인과성: prefix invariance ─────────────────────────────────────────
def _assert_prefix_invariant(fn, panel, dates, k):
    full = fn(panel, dates)
    pre = fn({s: v[:k] for s, v in panel.items()}, dates[:k])
    for i in range(k):
        assert (full[i] or {}) == (pre[i] or {}), (
            f"prefix invariance 위반 @i={i} date={dates[i]}: {full[i]} != {pre[i]}")


@pytest.mark.parametrize("kind,cfg", [
    ("events", BASE_RSI), ("prefomc", {}), ("tom", {"tom": (1, 3)}),
])
def test_calendar_signals_prefix_invariant(kind, cfg):
    dates = _sched_dates(date(2015, 1, 2), 620)
    panel = _panel(dates)
    fn = c3b.make_signal_fn(kind, cfg)
    # 월말 절단점을 포함해 캘린더 누수가 드러나도록
    for k in (400, 500, 611):
        _assert_prefix_invariant(fn, panel, dates, k)


def test_next_next_trading_day_pure():
    # _nn 은 순수 date 함수(배열 무관): 이틀 앞 거래일.
    assert c3b._nn(date(2021, 1, 4)) == C.next_trading_day(C.next_trading_day(date(2021, 1, 4)))
    # 금요일 → (주말 건너) 화요일 근방
    d = date(2021, 5, 27)   # 목
    assert c3b._nn(d) == C.next_trading_day(C.next_trading_day(d))


# ── 메커니즘 항등식 ───────────────────────────────────────────────────────────
def test_cost_compression_identity():
    """3x·1/3노셔널의 노출당 왕복비용 ≈ 1x 왕복비용의 1/3(반호가 티어 차이 제외)."""
    # 이 항등식은 25bp 표준 가정의 손계산(62/64)에 고정 — 명시 25bp로 검증(표준요율 정정과 무관).
    rt_1x = c3b.cost_for(["NDXTR"], commission_bps=25.0).roundtrip_bps("NDXTR")     # 2*(25+1+5)=62
    rt_3x_full = c3b.cost_for(["TQQQ3X"], commission_bps=25.0).roundtrip_bps("TQQQ3X")  # 2*(25+2+5)=64
    per_exposure_3x = rt_3x_full * c3b.NOTIONAL_FRAC          # 노셔널 1/3 → 노출당
    assert abs(rt_1x - 62.0) < 1e-6
    assert per_exposure_3x < rt_1x / 2.0                     # 압축 실재(≈21.3 < 31)


def test_path_decay_negative_on_synthetic_3x():
    """합성 3x 는 1x 대비 경로감쇠(경비+차입+분산드래그)로 실현 3x < 3·기초 가 평균적으로 성립."""
    dates = _sched_dates(date(2000, 1, 3), 800)
    c = _closes(len(dates))
    x3 = [c[0]]
    for i in range(1, len(c)):
        r = c[i] / c[i - 1] - 1.0
        x3.append(x3[-1] * (1 + 3 * r - 0.0095 / 252 - 2 * 0.005 / 252))  # 경비+차입
    tw = [1.0] * len(dates)
    m = c3b.per_trade_mechanism(tw, c, x3, dates)
    assert m["n_trades"] >= 1
    assert m["path_decay_3x"] < 0.0


# ── 거래 인덱스 추출 ─────────────────────────────────────────────────────────
def test_trade_index_pairs():
    tw = [0, 0, 1, 1, 0, 0, 0]
    pairs = c3b.trade_index_pairs(tw, len(tw), exec_lag=1)
    # 진입 i=2 → ef=3; 청산 j+1=4 → xf=5
    assert pairs == [(3, 5)]


# ── 슬리브/코어위성 손계산 ────────────────────────────────────────────────────
def test_sleeve_and_core_satellite_shapes():
    dates = _sched_dates(date(2010, 1, 4), 400)
    c = _closes(len(dates))
    x3 = [c[0]]
    for i in range(1, len(c)):
        x3.append(x3[-1] * (1 + 3 * (c[i] / c[i - 1] - 1.0)))
    cash = [0.0] * len(dates)
    tw = c3b.union_events_tw(dates, c, BASE_RSI)
    s = c3b.sleeve_stream(tw, c, x3, dates, cash)
    assert len(s.net_returns) == len(dates)
    cs = c3b.core_satellite(tw, c, x3, dates, cash, base_frac=0.85, swap_frac=0.15,
                            off_to_base=False)
    assert len(cs["net"]) == len(dates)
    # 코어 무매매: 첫날 이후 코어 자본은 순수 가격 드리프트(비율 = tr[t]/tr[0])
    ratio = cs["core_eq"][-1] / cs["core_eq"][0]
    assert abs(ratio - c[-1] / c[0]) < 1e-9


# ── end-to-end: 원장 적재 + peek-once ────────────────────────────────────────
def test_gate_eval_config_end_to_end(tmp_path):
    from toss_trader import gate
    ledger = str(tmp_path / "l.jsonl")
    dates = _sched_dates(date(2000, 1, 3), 1600)
    c = _closes(len(dates))
    x3 = [c[0]]
    for i in range(1, len(c)):
        x3.append(x3[-1] * (1 + 3 * (c[i] / c[i - 1] - 1.0)))
    cash = [0.0] * len(dates)
    tw = c3b.rsi2_tw(c, BASE_RSI)
    s = c3b.sleeve_stream(tw, c, x3, dates, cash)
    bench = R.to_returns(c)
    design_end = dates[900]
    g = c3b.gate_eval_config("c3b_test", {"exec": "tqqq_1_3"}, s.net_returns, bench,
                             dates, design_end, is_leveraged=True,
                             semi_contaminated=True, ledger=ledger, rc_b=50,
                             program_sharpes=[0.01, 0.02, -0.01])
    assert g["verdict"] in ("PASS", "CONDITIONAL", "FAIL")
    assert g["rc_threshold"] == 0.01           # 반오염 → p<0.01
    assert "relative_risk" in g and "holdout" in g
    assert Path(ledger).exists()
    lines = Path(ledger).read_text().strip().splitlines()
    holdout = [l for l in lines if '"period": "holdout"' in l]
    assert len(holdout) == 1                    # peek-once
    # 두 번째 홀드아웃 시도는 거부
    with pytest.raises(gate.PeekOnceError):
        c3b.gate_eval_config("c3b_test", {"exec": "tqqq_1_3"}, s.net_returns, bench,
                             dates, design_end, is_leveraged=True,
                             semi_contaminated=True, ledger=ledger, rc_b=50,
                             program_sharpes=[0.01])
