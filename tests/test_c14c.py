"""c14c 실적발표 프리미엄/프리-어닝 런업 테스트 (순수 stdlib + 합성데이터, 네트워크 불요).

검증 포인트(task):
- **lookahead_guard(가격축, 사양 §2.3)**: 이벤트 포트폴리오 신호가 미래가격 교란에 불변(인과적).
  이벤트 인덱스는 발표일(외생)에서 오지만 hibeta 선별·live·베타는 종가≤진입 만 참조함을 강제.
- **날짜축 look-ahead(task 핵심)**:
    · actual 스케줄 = 발표일을 **미리 안다고 가정**(진입 E−5 < 발표 known) → look-ahead 임을 **명시적으로 강제**.
    · expected 스케줄(직전+91일) = 진입 시 **직전 분기 발표만 참조**(known ≤ 진입) → look-ahead 없음.
    · 두 스케줄의 성과 갭을 정량화(대안 검증).
- 효과 거래일 산정(장마감 후 → 익일), 종가-종가 체결 PnL, 동시 최대 포지션, PIT as-of 게이팅.
- EDGAR 캐시가 있으면 NVDA/AAPL 실적일 케이던스·효과일 검증(없으면 skip).
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

from toss_trader import research  # noqa: E402
from toss_trader.research import CostSpec, LookaheadLeak  # noqa: E402
import c2d_stocks as c2d  # noqa: E402
import c14c_earnings as c14c  # noqa: E402


# ── 합성 PIT 패널(베타-분산) + c3c 인터페이스 ────────────────────────────────
def _weekdays(start: date, count: int) -> list[date]:
    out, d = [], start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


_SYMS = {"A": 2.2, "B": 1.6, "C": 1.1, "D": 0.7}


def _synth_panel(n: int = 200, seed: int = 7, late_sym: str | None = None,
                 late_at: int = 90) -> c2d.Panel:
    rng = random.Random(seed)
    dates = _weekdays(date(2019, 1, 1), n)
    p = c2d.Panel(dates=dates)

    def to_close(rets, p0):
        cl = [p0]
        for r in rets[1:]:
            cl.append(max(cl[-1] * (1.0 + r), 1e-6))
        return cl

    qret = [0.0] + [0.0005 + 0.010 * rng.gauss(0, 1) for _ in range(n - 1)]
    p.close["QQQ"] = to_close(qret, 100.0)
    for fld, mul in (("open", 0.999), ("high", 1.004), ("low", 0.996)):
        getattr(p, fld)["QQQ"] = [c * mul for c in p.close["QQQ"]]
    p.vol["QQQ"] = [5e6] * n
    p.first_idx["QQQ"] = 0
    p.tier["QQQ"] = research.TIER_ETF

    for k, (s, beta) in enumerate(_SYMS.items()):
        rets = [0.0] + [beta * qret[i] + 0.004 * rng.gauss(0, 1) for i in range(1, n)]
        cl = to_close(rets, 50.0 + 5 * k)
        p.close[s] = cl
        p.open[s] = [c * 0.999 for c in cl]
        p.high[s] = [c * 1.01 for c in cl]
        p.low[s] = [c * 0.99 for c in cl]
        p.vol[s] = [2e6] * n
        p.first_idx[s] = 0
        p.tier[s] = research.TIER_LARGE_CAP
    p.universe = [s for s in p.close if s != "QQQ"]

    # c3c 인터페이스
    p.rawclose = {s: list(p.close[s]) for s in p.close}   # type: ignore[attr-defined]
    p.last_idx = {s: n - 1 for s in p.close}              # type: ignore[attr-defined]
    base_members = set(_SYMS)
    if late_sym:
        base_members.discard(late_sym)
    elig = []
    for t in range(n):
        m = set(base_members)
        if late_sym and t >= late_at:
            m.add(late_sym)
        elig.append(frozenset(m))
    p.pit_elig = elig                                     # type: ignore[attr-defined]
    p.added_idx = {late_sym: late_at} if late_sym else {}  # type: ignore[attr-defined]
    p.missing_members = []                                # type: ignore[attr-defined]
    p.pit_records = [(dates[0], frozenset(base_members))]  # type: ignore[attr-defined]
    p._beta_cache = {}                                    # type: ignore[attr-defined]
    p._hibeta_cache = {}                                  # type: ignore[attr-defined]
    return p


def _earn_provider(mapping: dict[str, list[tuple[date, bool]]]):
    return lambda s: mapping.get(s, [])


# ── 효과 거래일 산정 ─────────────────────────────────────────────────────────
def test_effective_day_after_close_next_trading_day():
    dates = _weekdays(date(2020, 1, 2), 10)          # Thu 시작
    D = dates[4]                                     # 거래일
    assert c14c._effective_idx(dates, D, after_close=True) == 5   # 익일로
    assert c14c._effective_idx(dates, D, after_close=False) == 4  # 당일


def test_effective_day_weekend_maps_to_next_trading_day():
    dates = _weekdays(date(2020, 1, 2), 10)
    sat = date(2020, 1, 4)                           # 토요일(비거래)
    # 첫 거래일 >= 토요일 = 월요일(index 2), after_close 여도 D!=거래일이라 미이동
    assert c14c._effective_idx(dates, sat, after_close=True) == 2
    assert c14c._effective_idx(dates, sat, after_close=False) == 2


# ── build_events: 진입=E−lead, 청산=E−exit_offset ───────────────────────────
def test_build_events_indices_pre_earn():
    p = _synth_panel(n=200)
    prov = _earn_provider({"A": [(p.dates[20], False), (p.dates[60], False)]})
    ev = c14c.build_events(p, lead=5, exit_offset=0, mode="actual", events_provider=prov)
    a = sorted([(e["entry"], e["exit"], e["eff"]) for e in ev if e["sym"] == "A"])
    assert a == [(15, 20, 20), (55, 60, 60)]
    ev1 = c14c.build_events(p, lead=5, exit_offset=1, mode="actual", events_provider=prov)
    a1 = sorted([(e["entry"], e["exit"]) for e in ev1 if e["sym"] == "A"])
    assert a1 == [(15, 19), (55, 59)]               # 발표 전일 청산


# ── 날짜축 look-ahead: actual 은 미래정보, expected 는 인과적 ────────────────
def test_actual_schedule_has_lookahead():
    """actual: 진입(E−5) 이 발표 known(=발표일 거래인덱스)보다 앞 → 발표일 완전예지 = look-ahead."""
    p = _synth_panel(n=200)
    prov = _earn_provider({"A": [(p.dates[20], False), (p.dates[60], False)],
                           "B": [(p.dates[30], True), (p.dates[80], True)]})
    ev = c14c.build_events(p, lead=5, exit_offset=0, mode="actual", events_provider=prov)
    assert ev
    # 모든 actual 이벤트는 발표를 알기 전에 진입한다(=look-ahead) — 이 성질을 명시적으로 강제.
    assert all(e["entry"] < e["known"] for e in ev), \
        "actual 스케줄이 look-ahead 가 아니게 됨(기대와 다름)"


def test_expected_schedule_has_no_lookahead():
    """expected: 진입 시 직전 분기 발표(known)만 참조 → known ≤ 진입(인과적, 미래정보 없음)."""
    p = _synth_panel(n=200)
    prov = _earn_provider({"A": [(p.dates[20], False), (p.dates[60], False)]})
    ev = c14c.build_events(p, lead=5, exit_offset=0, mode="expected", events_provider=prov)
    assert ev, "expected 이벤트가 비어 있음(직전+91일이 범위 밖?)"
    # 진입 시점에 known(직전 분기 발표)이 이미 알려져 있어야 한다.
    assert all(e["known"] <= e["entry"] for e in ev), \
        "expected 스케줄에 look-ahead 잔존(직전 발표를 진입 후에 앎)"
    # 예상 발표일은 직전(dates[20]) + ~91일 → 진입은 그로부터 훨씬 뒤.
    assert all(e["entry"] >= 20 for e in ev)


def test_lookahead_gap_quantified():
    """두 스케줄이 모두 거래를 만들고, 갭 계량이 동작(대안 검증 존재)."""
    p = _synth_panel(n=200)
    prov = _earn_provider({s: [(p.dates[20 + 30 * k], False) for k in range(5)]
                           for s in ("A", "B", "C")})
    ea = c14c.build_events(p, lead=5, mode="actual", events_provider=prov)
    ee = c14c.build_events(p, lead=5, mode="expected", events_provider=prov)
    assert ea and ee
    assert len(ee) < len(ea)                         # expected 는 1분기차 이벤트 생략 → 더 적음


# ── lookahead_guard(가격축) ─────────────────────────────────────────────────
def test_lookahead_guard_event_portfolio_non_hibeta():
    p = _synth_panel(n=200)
    prov = _earn_provider({s: [(p.dates[20 + 25 * k], False) for k in range(6)]
                           for s in ("A", "B", "C", "D")})
    fn = c14c.make_signal_fn(p, lead=5, hibeta=False, mode="actual",
                             max_pos=3, events_provider=prov)
    assert research.lookahead_guard(fn, p.close, p.dates) is True


def test_lookahead_guard_event_portfolio_hibeta():
    p = _synth_panel(n=200)
    prov = _earn_provider({s: [(p.dates[20 + 25 * k], False) for k in range(6)]
                           for s in ("A", "B", "C", "D")})
    # beta_top(50) > 유니버스라 전부 포함되지만, 베타 계산이 종가≤진입 만 참조함을 강제.
    fn = c14c.make_signal_fn(p, lead=5, hibeta=True, mode="actual",
                             max_pos=3, events_provider=prov)
    assert research.lookahead_guard(fn, p.close, p.dates) is True


def test_lookahead_guard_catches_leak_control():
    """대조군: 미래 종가를 참조하는 신호는 guard 가 반드시 잡는다(가드 자체 유효성)."""
    p = _synth_panel(n=120)

    def leaky(panel_closes, dates):
        n = len(dates)
        out = [{} for _ in range(n)]
        for t in range(n - 1):
            if panel_closes["A"][t + 1] > panel_closes["A"][t]:
                out[t] = {"A": 1.0}
        return out

    with pytest.raises(LookaheadLeak):
        research.lookahead_guard(leaky, p.close, p.dates)


# ── 종가-종가 체결 PnL / 동시 포지션 / as-of ────────────────────────────────
def test_simulate_c2c_gross_matches_close_ratio():
    """단일 이벤트, 무비용: 포트폴리오 최종 자본 = close[exit]/close[entry](정확)."""
    p = _synth_panel(n=200)
    prov = _earn_provider({"A": [(p.dates[40], False)]})   # E=40, entry=35, exit=40
    ev = c14c.build_events(p, lead=5, exit_offset=0, mode="actual", events_provider=prov)
    dec, taken = c14c.event_weight_schedule(ev, len(p.dates), max_pos=10)
    assert len(taken) == 1 and taken[0] == (35, 40, "A")
    zcost = CostSpec(commission_bps=0.0, slippage_bps=0.0, fx_bps=0.0, default_half_spread_bps=0.0)
    sim = c14c.simulate_c2c(p, dec, zcost, cash_rate=None)
    expected = p.close["A"][40] / p.close["A"][35]
    assert sim.equity[-1] == pytest.approx(expected, rel=1e-9)


def test_simulate_c2c_costs_reduce_return():
    p = _synth_panel(n=200)
    prov = _earn_provider({"A": [(p.dates[40], False)]})
    ev = c14c.build_events(p, lead=5, mode="actual", events_provider=prov)
    dec, _ = c14c.event_weight_schedule(ev, len(p.dates), max_pos=10)
    gross = c14c.simulate_c2c(p, dec, CostSpec(commission_bps=0.0, slippage_bps=0.0,
                                               fx_bps=0.0, default_half_spread_bps=0.0)).equity[-1]
    net = c14c.simulate_c2c(p, dec, c14c.cost_for(p)).equity[-1]
    assert net < gross                               # 왕복 비용만큼 순수익 감소


def test_max_pos_respected():
    p = _synth_panel(n=200)
    # 네 종목 모두 같은 효과일 → 동시 진입 시도, max_pos=2 로 제한
    prov = _earn_provider({s: [(p.dates[40], False)] for s in ("A", "B", "C", "D")})
    ev = c14c.build_events(p, lead=5, mode="actual", events_provider=prov)
    dec, taken = c14c.event_weight_schedule(ev, len(p.dates), max_pos=2)
    for t, w in dec.items():
        assert len(w) <= 2, f"t={t} 동시 포지션 {len(w)} > 2"
    assert len(taken) <= 2 * 1 + 0                   # 한 진입일 슬롯 2 → 최대 2건 체결


def test_asof_membership_gates_entry():
    """late 종목은 편입(late_at) 전 효과일 이벤트가 만들어지면 안 되고, 편입 후엔 만들어져야 한다."""
    p = _synth_panel(n=200, late_sym="A", late_at=100)
    prov = _earn_provider({"A": [(p.dates[50], False), (p.dates[150], False)]})  # E=50(편입전),150(편입후)
    ev = c14c.build_events(p, lead=5, mode="actual", events_provider=prov)
    a_entries = sorted(e["entry"] for e in ev if e["sym"] == "A")
    assert 45 not in a_entries                       # E=50 → entry45 (편입 전) 제외
    assert 145 in a_entries                          # E=150 → entry145 (편입 후) 포함


def test_cost_model_uniform_3bp_half_spread():
    p = _synth_panel(n=60)
    c14c.set_uniform_large_cap_tier(p)
    c = c14c.cost_for(p)
    assert c.trade_bps("A") == pytest.approx(10 + 3 + 5)         # 대형 유동주: 18bp
    assert c.trade_bps("QQQ") == pytest.approx(10 + 1 + 5)       # ETF: 16bp
    assert c14c.cost_for(p, micro=True).trade_bps("A") == pytest.approx(3 + 5)   # ≤$10 무료
    assert c14c.cost_for(p, mult=2.0).trade_bps("A") == pytest.approx(2 * 18)


# ── EDGAR 캐시 검증(있을 때만) ───────────────────────────────────────────────
def _has_cache(sym: str) -> bool:
    return (c14c.EARN_DIR / f"{sym.replace('.', '_')}.json").exists()


@pytest.mark.skipif(not _has_cache("NVDA"), reason="EDGAR 캐시 없음(--fetch 선행 필요)")
def test_edgar_nvda_quarterly_cadence():
    evs = c14c.load_symbol_events("NVDA")
    yrs = [d for d, _ in evs if d >= date(2017, 1, 1) and d <= date(2024, 12, 31)]
    assert len(yrs) >= 28, f"NVDA 분기 실적일이 너무 적음: {len(yrs)}"
    # 대부분 장마감 후 발표(after_close True)
    ac = sum(1 for _, a in evs if a)
    assert ac >= 0.7 * len(evs), "NVDA 실적 대부분이 장마감 후여야 함"
