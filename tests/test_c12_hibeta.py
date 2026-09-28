"""c12s 고베타 = '레버리지 ETF 없는 레버리지' 테스트 (순수 stdlib + 합성데이터, 네트워크 불요).

검증 포인트(task):
- **모든 신호에 lookahead_guard**(사양 §2.3): 고베타 바스켓/트렌드/모멘텀/드로다운가드 결정 함수가
  미래가격 교란에 불변(인과적). 베타·SMA·모멘텀 전부 종가≤t 만 참조함을 강제.
- **PIT 멤버십은 리밸런스일마다 as-of**: 선택 종목이 항상 그 시점 as-of 멤버(pit_elig[t]) 안에 있고,
  미래 편입 종목은 편입 전 절대 선택되지 않음.
- 베타 선별이 실제로 고베타 종목을 고름(랭킹 정상성).
- 비용 모델(10bp/side + 티어 반호가 + 5bp, micro=수수료0, 2x 스트레스) 산식.
- EP 스캔 인과성(진입=다음시가, as-of 멤버·live 만), BTC 추세 신호 인과성, Lane A 판정 로직.
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
from toss_trader.research import LookaheadLeak  # noqa: E402
import c2d_stocks as c2d  # noqa: E402
import c12_hibeta as c12  # noqa: E402


# ── 합성 베타-분산 PIT 패널 ──────────────────────────────────────────────────
def _weekdays(start: date, count: int) -> list[date]:
    out, d = [], start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# 심볼→(진짜 베타, 편입 인덱스). LATE 는 t>=late_at 에만 as-of 멤버.
_BETAS = {"HB1": 2.8, "HB2": 2.4, "HB3": 2.0, "MB1": 1.4, "MB2": 1.1, "LB1": 0.6, "LB2": 0.4}


def _synth_panel(n: int = 340, seed: int = 11, late_at: int = 170) -> c2d.Panel:
    rng = random.Random(seed)
    dates = _weekdays(date(2019, 1, 1), n)
    p = c2d.Panel(dates=dates)

    def to_close(rets, p0):
        cl = [p0]
        for r in rets[1:]:
            cl.append(max(cl[-1] * (1.0 + r), 1e-6))
        return cl

    qret = [0.0] + [0.0005 + 0.011 * rng.gauss(0, 1) for _ in range(n - 1)]
    p.close["QQQ"] = to_close(qret, 100.0)
    p.open["QQQ"] = [c * 0.999 for c in p.close["QQQ"]]
    p.high["QQQ"] = [c * 1.004 for c in p.close["QQQ"]]
    p.low["QQQ"] = [c * 0.996 for c in p.close["QQQ"]]
    p.vol["QQQ"] = [5e6] * n
    p.first_idx["QQQ"] = 0
    p.tier["QQQ"] = research.TIER_ETF

    syms = dict(_BETAS)
    syms["LATE"] = 3.5                                     # 최고 베타지만 late_at 전엔 비멤버
    for k, (s, beta) in enumerate(syms.items()):
        rets = [0.0] + [beta * qret[i] + 0.004 * rng.gauss(0, 1) for i in range(1, n)]
        cl = to_close(rets, 50.0 + k)
        p.close[s] = cl
        p.open[s] = [c * (0.999 if i % 2 else 1.001) for i, c in enumerate(cl)]
        p.high[s] = [max(cl[i], p.open[s][i]) * 1.01 for i in range(n)]
        p.low[s] = [min(cl[i], p.open[s][i]) * 0.99 for i in range(n)]
        p.vol[s] = [1e6 * (k + 2)] * n
        p.first_idx[s] = 0
        p.tier[s] = c2d._tier_for(s)                      # 대부분 small_hot(15bp)
    p.universe = [s for s in p.close if s != "QQQ"]

    # aux(c3c 패널 인터페이스)
    p.rawclose = {s: list(p.close[s]) for s in p.close}   # type: ignore[attr-defined]
    p.last_idx = {s: n - 1 for s in p.close}              # type: ignore[attr-defined]
    elig = []
    base_members = {s for s in _BETAS}
    for t in range(n):
        m = set(base_members)
        if t >= late_at:
            m.add("LATE")
        elig.append(frozenset(m))
    p.pit_elig = elig                                     # type: ignore[attr-defined]
    p.added_idx = {"LATE": late_at}                       # type: ignore[attr-defined]
    p.missing_members = []                                # type: ignore[attr-defined]
    p.pit_records = [(dates[0], frozenset(base_members)),
                     (dates[min(late_at, n - 1)], frozenset(base_members | {"LATE"}))]  # type: ignore[attr-defined]
    p._beta_cache = {}                                    # type: ignore[attr-defined]
    return p


# ── lookahead_guard: 모든 결정 함수가 인과적 ────────────────────────────────
def test_lookahead_guard_hibeta_basket():
    p = _synth_panel()
    fn = c12.make_signal_fn(p, c12.decide_hibeta_basket, topk=3, beta_win=60,
                            dv_top=5, min_price=1.0, use_filter=False)
    assert research.lookahead_guard(fn, p.close, p.dates) is True


def test_lookahead_guard_hibeta_trend_and_guard():
    p = _synth_panel()
    fn_tr = c12.make_signal_fn(p, c12.decide_hibeta_basket, topk=3, beta_win=60,
                               dv_top=5, min_price=1.0, use_filter=True)
    assert research.lookahead_guard(fn_tr, p.close, p.dates) is True
    fn_dd = c12.make_signal_fn(p, c12.decide_hibeta_basket, topk=3, beta_win=60,
                               dv_top=5, min_price=1.0, use_filter=False,
                               guard=True, dd_win=20, dd_thresh=0.10)
    assert research.lookahead_guard(fn_dd, p.close, p.dates) is True


def test_lookahead_guard_mom_hibeta():
    p = _synth_panel()
    fn = c12.make_signal_fn(p, c12.decide_mom_hibeta, topk=3, beta_top=5, beta_win=60,
                            lookback=60, skip=5, dv_top=7, min_price=1.0, use_filter=True)
    assert research.lookahead_guard(fn, p.close, p.dates) is True


def test_lookahead_guard_catches_leak_control():
    """대조군: 미래 종가를 참조하는 신호는 guard 가 반드시 잡는다(가드 자체 유효성)."""
    p = _synth_panel()

    def leaky(panel_closes, dates):
        n = len(dates)
        out = [{} for _ in range(n)]
        for t in range(n - 1):
            # 내일 종가가 오늘보다 높으면 오늘 매수 → 명백한 look-ahead
            if panel_closes["HB1"][t + 1] > panel_closes["HB1"][t]:
                out[t] = {"HB1": 1.0}
        return out

    with pytest.raises(LookaheadLeak):
        research.lookahead_guard(leaky, p.close, p.dates)


# ── PIT as-of: 선택 종목은 항상 그 시점 as-of 멤버, 미래편입은 편입 전 미선택 ──
def test_selection_within_asof_membership():
    p = _synth_panel(late_at=170)
    dec = c12.decide_hibeta_basket(p, topk=4, beta_win=60, dv_top=8, min_price=1.0)
    assert dec, "결정이 비어 있음"
    for t, w in dec.items():
        for s in w:
            assert s in p.pit_elig[t], f"t={t} 선택 {s} 가 as-of 멤버가 아님(미래정보 누수)"


def test_future_entrant_not_selected_before_addition():
    """LATE 는 최고 베타지만 편입(late_at) 전에는 절대 선택되면 안 된다."""
    late_at = 170
    p = _synth_panel(late_at=late_at)
    dec = c12.decide_hibeta_basket(p, topk=4, beta_win=60, dv_top=8, min_price=1.0)
    for t, w in dec.items():
        if t < late_at:
            assert "LATE" not in w, f"편입 전 t={t} 에 LATE 선택됨(as-of 위반)"
    # 편입 후에는 최고 베타이므로 실제로 선택되어야(게이팅이 과도차단 아님)
    after = [w for t, w in dec.items() if t >= late_at]
    assert any("LATE" in w for w in after), "편입 후에도 LATE 가 한 번도 선택되지 않음"


def test_beta_selection_picks_highest_beta():
    """베타 상위 선별이 실제 고베타(HB*) 종목을 고른다(랭킹 정상성)."""
    p = _synth_panel(late_at=10_000)            # LATE 비활성
    dec = c12.decide_hibeta_basket(p, topk=3, beta_win=120, dv_top=8, min_price=1.0)
    # 후반부 결정에서 상위 3 은 대체로 HB1/HB2/HB3 중심이어야 한다.
    last_w = dec[max(dec)]
    hb = {"HB1", "HB2", "HB3"}
    assert len(set(last_w) & hb) >= 2, f"고베타 선별 실패: {set(last_w)}"


def test_beta_causal_uses_only_past():
    """beta_to_bench 는 종가≤t 만 사용 → 미래 종가를 바꿔도 t 시점 베타 불변."""
    p = _synth_panel()
    t, win = 200, 60
    b0 = c12.beta_to_bench(p, "HB1", t, win)
    p2 = _synth_panel()
    for i in range(t + 1, len(p2.dates)):        # 미래만 교란
        p2.close["HB1"][i] *= 3.0
        p2.close["QQQ"][i] *= 0.5
    p2._beta_cache = {}                          # type: ignore[attr-defined]
    b1 = c12.beta_to_bench(p2, "HB1", t, win)
    assert b0 is not None and abs(b0 - b1) < 1e-12


# ── 비용 모델 ────────────────────────────────────────────────────────────────
def test_cost_model_tiers():
    p = _synth_panel()
    p.tier["MEGA"] = research.TIER_LARGE_CAP
    p.tier["HOT"] = research.TIER_SMALL_HOT
    c = c12.cost_for(p)
    assert c.trade_bps("MEGA") == pytest.approx(10 + 3 + 5)      # 대형: 18bp
    assert c.trade_bps("HOT") == pytest.approx(10 + 15 + 5)      # 핫: 30bp
    cm = c12.cost_for(p, micro=True)                            # ≤$10 무료 → 수수료 0
    assert cm.trade_bps("MEGA") == pytest.approx(0 + 3 + 5)
    c2x = c12.cost_for(p, mult=2.0)
    assert c2x.trade_bps("MEGA") == pytest.approx(2 * (10 + 3 + 5))


# ── EP 스캔 인과성 ───────────────────────────────────────────────────────────
def test_ep_scan_is_causal_and_asof():
    p = _synth_panel()
    sigs = c12.ep_gap_scan(p, gap=0.0, rvol_k=0.0, rng_min=0.0, avg_win=20,
                           sma_exit=5, max_hold=20, min_price=1.0)
    assert sigs, "신호가 전혀 없음(임계 완화했는데도)"
    for t, s, xt in sigs:
        assert s in p.pit_elig[t], f"EP 신호 {s}@{t} 가 as-of 멤버 아님"
        assert xt >= t + 1, "청산 트리거가 진입(다음시가=t+1)보다 앞설 수 없음"
        assert c2d._eligible(p, s, t, 1)


def test_ep_build_respects_max_pos():
    p = _synth_panel()
    decisions, taken, n_sig = c12.ep_build(p, gap=0.0, rvol_k=0.0, rng_min=0.0,
                                           avg_win=20, sma_exit=5, max_hold=20,
                                           min_price=1.0, max_pos=3)
    for t, w in decisions.items():
        assert len(w) <= 3, f"t={t} 동시 포지션 {len(w)} > 3"
    assert n_sig >= len(taken)                                  # 슬롯 제한으로 일부 스킵


# ── BTC 추세 신호 인과성 ─────────────────────────────────────────────────────
def _btc_panel(n: int = 200, seed: int = 3):
    p = c2d.Panel(dates=_weekdays(date(2020, 1, 1), n))
    for s in ("QQQ", "MSTR", "COIN"):
        p.close[s] = [50.0 + i * 0.1 for i in range(n)]
        p.open[s] = list(p.close[s]); p.high[s] = list(p.close[s]); p.low[s] = list(p.close[s])
        p.vol[s] = [1e6] * n
        p.first_idx[s] = 0 if s != "COIN" else n // 2          # COIN 은 늦게 상장
        p.tier[s] = c2d._tier_for(s)
    p.universe = ["MSTR", "COIN"]
    return p


def test_btc_signal_causal():
    p = _btc_panel()
    rng = random.Random(5)
    btc = [1000.0]
    for _ in range(len(p.dates) - 1):
        btc.append(max(btc[-1] * (1.0 + 0.01 * rng.gauss(0, 1)), 1.0))
    split = 120
    d0 = c12.decide_btc_proxy(p, btc, sma_win=30)
    daily0 = c2d.decisions_to_daily(d0, len(p.dates))
    btc2 = list(btc)
    for i in range(split + 1, len(btc2)):                      # 미래만 교란
        btc2[i] *= 5.0
    d1 = c12.decide_btc_proxy(p, btc2, sma_win=30)
    daily1 = c2d.decisions_to_daily(d1, len(p.dates))
    for i in range(split + 1):
        assert daily0[i] == daily1[i], f"i={i} BTC 신호가 미래 정보에 반응(look-ahead)"


def test_btc_only_live_names():
    """COIN 상장(첫 실측) 전에는 바스켓에 COIN 이 들어가면 안 된다."""
    p = _btc_panel()
    btc = [1000.0 + i for i in range(len(p.dates))]             # 상시 상승 → 항상 on
    dec = c12.decide_btc_proxy(p, btc, sma_win=20)
    daily = c2d.decisions_to_daily(dec, len(p.dates))
    coin_start = p.first_idx["COIN"]
    for i in range(coin_start):
        assert "COIN" not in daily[i], f"i={i} COIN 상장 전 편입"


# ── Lane A 판정 로직 ─────────────────────────────────────────────────────────
def test_lane_a_verdict_logic():
    assert c12.lane_a_verdict(a_pass=True, b_pass=True, c_pass=True, bankruptcy=False)[0] == "PASS"
    assert c12.lane_a_verdict(a_pass=True, b_pass=False, c_pass=True, bankruptcy=False)[0] == "CONDITIONAL"
    assert c12.lane_a_verdict(a_pass=True, b_pass=True, c_pass=False, bankruptcy=False)[0] == "CONDITIONAL"
    assert c12.lane_a_verdict(a_pass=False, b_pass=True, c_pass=True, bankruptcy=False)[0] == "FAIL"
    assert c12.lane_a_verdict(a_pass=True, b_pass=True, c_pass=True, bankruptcy=True)[0] == "FAIL"


def test_realized_beta_matches_construction():
    """합성: 심볼 수익 = beta*QQQ + 노이즈 → realized_beta 가 설계 베타 근사."""
    p = _synth_panel(n=400, seed=9, late_at=10_000)
    q = research.to_returns(p.close["QQQ"])[1:]
    for s, tb in (("HB2", 2.4), ("MB1", 1.4), ("LB1", 0.6)):
        r = research.to_returns(p.close[s])[1:]
        rb = c12.realized_beta(r, q)
        assert abs(rb - tb) < 0.5, f"{s}: realized {rb:.2f} vs 설계 {tb}"
