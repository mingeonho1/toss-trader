"""c3c PIT 단일종목 모멘텀 테스트 (순수 stdlib + 합성데이터, 네트워크 불요).

검증 포인트(task):
- **모든 신호에 lookahead_guard**(사양 §2.3): PIT 결정 함수가 미래가격 교란에 불변(인과적).
- **PIT 멤버십은 리밸런스일마다 as-of**: pit_asof 가 미래 편입/편출을 참조하지 않음.
- 비멤버는 모멘텀/거래대금이 아무리 커도 후보에서 제외(멤버십 게이팅).
- 상장폐지(last_idx 이후) 종목 미선택; additions_map 인과성; 신규편입 바스켓 규칙.
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

from toss_trader import research  # noqa: E402
from toss_trader.research import LookaheadLeak  # noqa: E402
import c2d_stocks as c2d  # noqa: E402
import c3c_pit as c3c  # noqa: E402


# ── 합성 PIT 패널 ────────────────────────────────────────────────────────────
def _weekdays(start: date, count: int) -> list[date]:
    out, d = [], start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _synth_pit_panel(n: int = 160, seed: int = 7) -> c3c.c2d.Panel:
    """QQQ + 6 종목. 각기 다른 추세로 랭킹 분산. PIT 멤버십/신규편입/거래대금 aux 포함."""
    dates = _weekdays(date(2020, 1, 6), n)
    p = c2d.Panel(dates=dates)
    syms = ["QQQ", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    for k, s in enumerate(syms):
        drift = 0.0003 * (k + 1)
        cl, op, hi, lo, vo, rc = [], [], [], [], [], []
        px = 100.0 + 5 * k
        for i in range(n):
            px *= (1.0 + drift + 0.004 * math.sin((i + k) / 4.0))
            c = px
            o = c * (1.0 - 0.001 * math.cos((i + k) / 3.0))
            cl.append(c); op.append(o); hi.append(max(c, o) * 1.005); lo.append(min(c, o) * 0.995)
            vo.append(1_000_000.0 * (k + 1))
            rc.append(c)                       # 원시종가 = 조정종가(합성)
        p.close[s] = cl; p.open[s] = op; p.high[s] = hi; p.low[s] = lo; p.vol[s] = vo
        p.first_idx[s] = 0; p.tier[s] = research.TIER_LARGE_CAP
    p.tier["QQQ"] = research.TIER_ETF
    p.universe = [s for s in syms if s != "QQQ"]
    # aux: 원시종가, last_idx, 멤버십(as-of), 신규편입 인덱스
    p.rawclose = {s: list(p.close[s]) for s in p.close}
    p.last_idx = {s: n - 1 for s in p.close}
    # PIT 멤버십: 초기 {AAA,BBB,CCC}. t>=80 에 DDD,EEE 편입, FFF 는 계속 비멤버.
    elig = []
    for t in range(n):
        base = {"AAA", "BBB", "CCC"}
        if t >= 80:
            base |= {"DDD", "EEE"}
        elig.append(frozenset(base))
    p.pit_elig = elig
    p.added_idx = {"DDD": 80, "EEE": 80}       # t=80 편입
    p.missing_members = []
    # pit_records: as-of 함수용(실제 c3c.pit_asof 은 recs 를 쓰지만 여기선 aux 로 충분)
    p.pit_records = [(dates[0], frozenset({"AAA", "BBB", "CCC"})),
                     (dates[min(80, n - 1)], frozenset({"AAA", "BBB", "CCC", "DDD", "EEE"}))]
    return p


def _pit_signal_fn(base: c3c.c2d.Panel, decide_fn, **kwargs):
    """guard 어댑터: panel_closes(미래 교란)만 갈아끼우고 나머지 aux 는 고정."""
    def fn(panel_closes, dates):
        p = c2d.Panel(dates=list(dates))
        p.close = panel_closes
        p.open, p.high, p.low, p.vol = base.open, base.high, base.low, base.vol
        p.first_idx, p.tier, p.universe = base.first_idx, base.tier, base.universe
        p.rawclose, p.last_idx = base.rawclose, base.last_idx
        p.pit_elig, p.added_idx = base.pit_elig, base.added_idx
        p.missing_members, p.pit_records = base.missing_members, base.pit_records
        dec = decide_fn(p, **kwargs)
        return c2d.decisions_to_daily(dec, len(dates))
    return fn


# ── lookahead_guard: 모든 PIT 신호가 인과적 ──────────────────────────────────
def test_lookahead_guard_pit_momentum():
    p = _synth_pit_panel()
    fn = _pit_signal_fn(p, c3c.pit_momentum, topk=2, lookback=30, skip=3,
                        use_filter=False, dv_top=5)
    assert research.lookahead_guard(fn, p.close, p.dates) is True
    fn2 = _pit_signal_fn(p, c3c.pit_momentum, topk=2, lookback=30, skip=3,
                         use_filter=True, dv_top=5)
    assert research.lookahead_guard(fn2, p.close, p.dates) is True


def test_lookahead_guard_pit_resid_momentum():
    p = _synth_pit_panel()
    fn = _pit_signal_fn(p, c3c.pit_resid_momentum, topk=2, lookback=70, skip=3, dv_top=5)
    assert research.lookahead_guard(fn, p.close, p.dates) is True


def test_lookahead_guard_pit_new_entrant():
    p = _synth_pit_panel()
    fn = _pit_signal_fn(p, c3c.pit_new_entrant, hold_m=3, min_hist=5, topk=0)
    assert research.lookahead_guard(fn, p.close, p.dates) is True
    # 신규편입 바스켓이 실제로 잡혀야 유의미한 검증
    dec = c3c.pit_new_entrant(p, hold_m=3, min_hist=5, topk=0)
    assert any(set(w) & {"DDD", "EEE"} for w in dec.values()), "신규편입이 신호로 잡히지 않음"


def test_lookahead_guard_catches_leak_control():
    """대조군: 미래 종가에 의존하는 누수 신호는 guard 가 잡아야 한다."""
    p = _synth_pit_panel(n=60)

    def leaky(panel_closes, dates):
        last = panel_closes["AAA"][-1]
        return [{"AAA": min(1.0, panel_closes["AAA"][t] / last)} for t in range(len(dates))]

    with pytest.raises(LookaheadLeak):
        research.lookahead_guard(leaky, p.close, p.dates)


# ── PIT 멤버십 as-of 정확성 ──────────────────────────────────────────────────
def test_pit_asof_is_causal():
    d0, d1, d2 = date(2016, 9, 1), date(2020, 12, 21), date(2024, 9, 23)
    recs = [(d0, frozenset({"AAA", "BBB"})),
            (d1, frozenset({"AAA", "BBB", "TSLA"})),
            (d2, frozenset({"AAA", "TSLA", "PLTR"}))]
    # 편입일 이전엔 미포함
    assert "TSLA" not in c3c.pit_asof(recs, date(2020, 12, 18))
    assert "TSLA" in c3c.pit_asof(recs, date(2020, 12, 21))
    assert "PLTR" not in c3c.pit_asof(recs, date(2024, 9, 22))
    assert "PLTR" in c3c.pit_asof(recs, date(2024, 9, 23))
    # 편출(BBB 는 d2 에서 빠짐)
    assert "BBB" in c3c.pit_asof(recs, date(2024, 9, 22))
    assert "BBB" not in c3c.pit_asof(recs, date(2024, 9, 23))
    # 최초 레코드 이전 날짜는 공집합
    assert c3c.pit_asof(recs, date(2016, 1, 1)) == frozenset()


def test_additions_map_causal():
    d0, d1, d2 = date(2016, 9, 1), date(2020, 12, 21), date(2024, 9, 23)
    recs = [(d0, frozenset({"AAA", "BBB"})),
            (d1, frozenset({"AAA", "BBB", "TSLA"})),
            (d2, frozenset({"AAA", "TSLA", "PLTR"}))]
    add = c3c.additions_map(recs, date(2016, 9, 1), date(2026, 9, 30))
    # 시작 시점 기존 멤버(AAA,BBB)는 신규편입 아님
    assert "AAA" not in add and "BBB" not in add
    assert add["TSLA"] == d1 and add["PLTR"] == d2


# ── 멤버십 게이팅: 비멤버는 후보에서 제외 ────────────────────────────────────
def test_non_member_excluded_from_candidates():
    p = _synth_pit_panel()
    # FFF 는 절대 멤버가 아니므로 어떤 t 에서도 후보에 없어야 한다(거래대금·모멘텀 무관).
    for t in (79, 100, 150):
        cands = c3c.pit_candidates(p, t, need=30, dv_top=6)
        assert "FFF" not in cands
    # DDD/EEE 는 편입(t>=80) 후 이력 충족 시 후보 가능
    cands_before = c3c.pit_candidates(p, 79, need=30, dv_top=6)
    assert "DDD" not in cands_before and "EEE" not in cands_before
    cands_after = c3c.pit_candidates(p, 130, need=30, dv_top=6)
    assert "DDD" in cands_after and "EEE" in cands_after


def test_momentum_only_picks_pit_members():
    p = _synth_pit_panel()
    dec = c3c.pit_momentum(p, topk=3, lookback=30, skip=3, use_filter=False, dv_top=6)
    for t, w in dec.items():
        for s in w:
            if s == "QQQ":
                continue
            assert s in p.pit_elig[t], f"t={t} 비멤버 {s} 선택됨"


# ── 상장폐지(last_idx) 이후 미선택 ───────────────────────────────────────────
def test_delisted_after_last_idx_not_selected():
    p = _synth_pit_panel()
    p.last_idx["CCC"] = 90                    # CCC 는 t=90 이후 상장폐지(마지막 실측 90)
    assert c3c._pit_live(p, "CCC", 85, 30) is True
    assert c3c._pit_live(p, "CCC", 120, 30) is False
    cands = c3c.pit_candidates(p, 120, need=30, dv_top=6)
    assert "CCC" not in cands


# ── 신규편입 바스켓: 보유창·멤버십 제약 ──────────────────────────────────────
def test_new_entrant_holds_only_recent_adds():
    p = _synth_pit_panel(n=220)               # 편입 t=80 이후 보유창(63일) 종료까지 관측
    dec = c3c.pit_new_entrant(p, hold_m=3, min_hist=5, topk=0)  # 보유창 ~63거래일
    # 편입 직전 월말엔 QQQ(신규 없음)
    for t, w in dec.items():
        if t < 80:
            assert w == {"QQQ": 1.0}
    # 편입 직후 창 안(age∈[5,63])엔 DDD/EEE 포함
    near = [t for t in dec if 85 <= t <= 143]
    assert near and any(set(dec[t]) & {"DDD", "EEE"} for t in near)
    # 보유창을 지난 월말(age>63)엔 다시 신규 없음 → QQQ
    late = [t for t in dec if t - 80 > 63]
    assert late and all(dec[t] == {"QQQ": 1.0} for t in late)
