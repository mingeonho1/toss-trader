"""c14a 테스트 — 소액 실행 최적화(순수 stdlib + 합성 패널, 네트워크 불요).

검증 포인트(task):
- **수수료 분해 정확성**: order_fee_breakdown 재계산 = 장부 실현 수수료. ~$3.6 매도는 규제 최소금액
  (SEC $0.01 + TAF $0.01 = $0.02) 을 문다. 매수 ≤$10 은 커미션 0.
- **바스켓 크기 변형(a)**: BasketN(topk=k) 은 select_basket(topk=k) 와 동일 선택(≤k 종목).
- **min-trade 변형(b)**: min_trade=$5 는 $36 장부에서 소액 트림을 건너뛴다(체결 감소).
- **buy-only 변형(c)**: 매도가 전혀 없다 → SEC/TAF(규제수수료) 0.
- **무수수료 반사실**: ZERO_FEES 는 어떤 주문도 수수료 0.
- **분해 재현**: BasketN(10) PaperLab 실행 = 원 HibetaBasket 실행(변형 서브클래스 무발산).
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

from toss_trader.fees import TossFeeSchedule  # noqa: E402
from toss_trader.models import Candle  # noqa: E402
from toss_trader.paperlab import PaperLab  # noqa: E402
from toss_trader.paperlab_strategies.hibeta_basket import HibetaBasket, select_basket  # noqa: E402
import c14a_micro_exec as C14  # noqa: E402


# ── 합성 패널(결정론적, 네트워크 불요) ───────────────────────────────────────
def _dates(n, start=date(2016, 1, 4)):
    out, d = [], start
    for _ in range(n):
        out.append(d)
        d += timedelta(days=1)
        while d.weekday() >= 5:      # 주말 스킵(거래일 근사)
            d += timedelta(days=1)
    return out


def _candles(sym, dates, base, drift):
    cs = []
    px = base
    for i, d in enumerate(dates):
        px = max(1.0, px * (1.0 + drift + 0.01 * ((i * 7 + hash(sym) % 5) % 3 - 1)))
        cs.append(Candle(symbol=sym, dt=d, open=px, high=px * 1.01, low=px * 0.99,
                         close=px, volume=1e6))
    return cs


def _panel(n=400):
    dates = _dates(n)
    names = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ",
             "KKK", "LLL"]
    panel = {"QQQ": _candles("QQQ", dates, 100.0, 0.0004)}
    for i, s in enumerate(names):
        panel[s] = _candles(s, dates, 20.0 + i, 0.0003 + 0.00015 * (i % 4))
    by_date = {s: {c.dt: c for c in cs} for s, cs in panel.items()}
    return panel, by_date, dates, names


# ── 1) 수수료 분해 정확성 ─────────────────────────────────────────────────────
def test_regmin_on_micro_sell():
    """~$3.6 매도: 커미션 0(≤$10) + SEC 최소 $0.01 + TAF 최소 $0.01 = $0.02."""
    f = TossFeeSchedule()
    bd = f.order_fee_breakdown("SELL", 3.6, shares=3.6 / 20.0)
    assert bd.commission == 0.0                 # ≤$10 무료
    assert bd.sec_fee == pytest.approx(0.01)    # 규제 최소
    assert bd.taf == pytest.approx(0.01)
    assert bd.total == pytest.approx(0.02)


def test_micro_buy_free():
    f = TossFeeSchedule()
    assert f.order_fee_breakdown("BUY", 3.6).total == 0.0   # ≤$10 매수 무료


def test_decompose_matches_book_fee():
    """decompose_fills 재계산 수수료 = 장부 실현 수수료(합성 체결)."""
    fills = [
        {"side": "BUY", "symbol": "AAA", "qty": 0.18, "price": 20.0, "fee": 0.0},
        {"side": "SELL", "symbol": "AAA", "qty": 0.18, "price": 20.0, "fee": 0.02},
        {"side": "SELL", "symbol": "QQQ", "qty": 0.036, "price": 100.0, "fee": 0.02},
    ]
    agg = C14.decompose_fills(fills)
    assert agg["recomputed_fee"] == pytest.approx(agg["book_fee"])
    assert agg["n_sells"] == 2 and agg["n_buys"] == 1
    assert agg["reg_min_hits"] == 2             # 둘 다 최소금액에 걸림
    assert agg["reg_min_total"] == pytest.approx(0.04)


def test_zero_fees_are_zero():
    assert C14.ZERO_FEES.order_fee_breakdown("SELL", 3.6, shares=0.18).total == 0.0
    assert C14.ZERO_FEES.order_fee_breakdown("BUY", 500.0).total == 0.0


# ── 2) 바스켓 크기 변형(a) ────────────────────────────────────────────────────
def test_basketN_matches_select_basket_topk():
    panel, by_date, dates, names = _panel()
    hist = {s: cs for s, cs in panel.items()}
    for k in (3, 5, 10):
        w_direct = select_basket(hist, names, topk=k)
        v = C14.BasketN(names, k)
        w_variant = v.decide(hist, {})
        assert set(w_variant) == set(w_direct), k
        assert len(w_variant) <= k
        if w_variant:
            assert abs(sum(w_variant.values()) - 1.0) < 1e-9


def test_basketN10_reproduces_hibeta_basket():
    """변형 서브클래스 N=10 = 원 HibetaBasket(무발산) — PaperLab 최종자산 동일."""
    panel, by_date, dates, names = _panel()
    fees = TossFeeSchedule()
    base = PaperLab(HibetaBasket(names), fees=fees)
    s0 = base.run(base.fresh_state(dates[350]), dates, by_date, contribute=False)
    var = PaperLab(C14.BasketN(names, 10), fees=fees)
    s1 = var.run(var.fresh_state(dates[350]), dates, by_date, contribute=False)
    assert base.summarize(s0)["real"].equity == pytest.approx(
        var.summarize(s1)["real"].equity, rel=1e-9)


# ── 3) min-trade 변형(b) ──────────────────────────────────────────────────────
def test_min_trade_reduces_trades():
    panel, by_date, dates, names = _panel()
    r_tight = C14.run_lab(C14.BasketN(names, 10, name="t"), dates, by_date, dates[350],
                          contribute=True, min_trade=0.01)
    r_batch = C14.run_lab(C14.BasketN(names, 10, name="b"), dates, by_date, dates[350],
                          contribute=True, min_trade=5.0)
    assert r_batch["real_trades"] <= r_tight["real_trades"]


# ── 4) buy-only 변형(c) ───────────────────────────────────────────────────────
def test_buy_only_never_sells():
    panel, by_date, dates, names = _panel()
    r = C14.run_buy_only(C14.BasketN(names, 10, name="bo"), dates, by_date, dates[350])
    assert r["_real_fills"], "체결이 있어야 함"
    assert all(f["side"] == "BUY" for f in r["_real_fills"])   # 매도 없음
    # 매도가 없으니 규제수수료(SEC/TAF) 0.
    agg = C14.decompose_fills(r["_real_fills"])
    assert agg["reg_min_total"] == 0.0
    assert agg["n_sells"] == 0


# ── 5) 크로스오버 단조성(규모↑ → N=10/N=3 비율 안정 또는 개선) ────────────────
def test_crossover_runs_and_reports():
    panel, by_date, dates, names = _panel()
    rows = C14.crossover_sweep(names, dates, by_date, dates[350], [36, 1000])
    assert len(rows) == 2
    for r in rows:
        assert r["n3_final"] > 0 and r["n10_final"] > 0
        assert isinstance(r["n10_beats_n3"], bool)
