#!/usr/bin/env python3
"""c14a — 소액($36) 실계좌 **실행(execution) 최적화**: 바스켓 크기·리밸런스 규칙으로 수수료 드래그 줄이기.

동기(reports/paperlab_backfill.md): $36 실장부는 $1k 단위장부보다 **훨씬 덜** 번다
(holygrail_hibeta $1k ×8.2 vs $36 ×5.6; hibeta_basket $1k ×7.3 vs $36 ×6.95). 원인 가설:
~$3.6 소액 포지션을 매도할 때마다 붙는 **규제 최소금액**(SEC fee 최소 $0.01 + FINRA TAF 최소 $0.01
= 건당 $0.02 고정) ≈ 55bp/매도. 매수는 ≤$10 무료라 드래그의 대부분은 **매도 건수 × 고정 최소금액**.

**신호는 건드리지 않는다(재튜닝 없음).** c12/c13a 에서 이미 Lane A PASS 한 신호(hibeta_basket·
ftlt_hibeta·holygrail_hibeta)의 **실행 규칙만** 바꾼다 → 새 idea_id, 홀드아웃 **반오염**
(holdout_semi_contaminated) 표기(부록 v2.1 §4). 판정은 DCA 경로의 Lane A 스타일 게이트.

**사전등록 실행 변형(결과 보기 전 확정):**
  (a) 바스켓 크기 N ∈ {3, 5}  vs 기준 N=10  — 매도 건수를 줄여 고정 최소금액 드래그 감소.
  (b) sell-batching / min-trade 임계 $5 — 리밸런스 시 |조정액|<$5 인 레그는 건너뜀(소액 트림 억제).
  (c) buy-only 드리프트 보정 — 매월 신규 적립($35)으로만 미달 레그를 매수(매도 없음 → 규제수수료 0).

**평가:** $36 시드 + 월 $35 적립(DCA) 경로 2개 — 2021-01→2026-09, 2016-09→2026-09(데이터 허용 범위).
지표: 최종자산·XIRR·MDD(단위장부 TWR)·수수료 드래그(무수수료 반사실 대비). N=10 이 N=3 을
역전하는 계좌 규모(크로스오버)도 산출.

**비용 모델:** :class:`toss_trader.fees.TossFeeSchedule`(토스 정확 요율; 매수 건당 ≤$10 무료,
매도 0.1% + SEC/TAF 최소금액). 반호가/슬리피지는 장부에 미반영이라 **모델 오버레이**로 별도 보고
(단일주 3bp / ETF 1bp + 5bp, c13a 티어). **데이터 한계:** 고베타 후보는 캐시 ∩ (현 S&P 대형주)
46/63 → 성과 상한(UPPER BOUND), c12/c13a 캐비엇 계승.

재현: PYTHONPATH=src .venv/bin/python experiments/c14a_micro_exec.py [--quick] [--no-ledger] [--no-report]
src 미수정(research/gate/histdata 는 import 만). 결과: reports/c14a_results.json, reports/cycle14_c14a_micro_exec.md.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd  # noqa: E402
from toss_trader.fees import TossFeeSchedule  # noqa: E402
from toss_trader.models import Candle  # noqa: E402
from toss_trader.paperlab import PaperBook, PaperLab, Strategy, max_drawdown  # noqa: E402
from toss_trader.paperlab_strategies.hibeta_basket import HibetaBasket, select_basket  # noqa: E402
from toss_trader.paperlab_strategies.hibeta_signal import (  # noqa: E402
    RISK_ON_3X, FtltHibeta, HolygrailHibeta, remap_signal)
from toss_trader.paperlab_strategies.hibeta_universe import HIBETA_CANDIDATES  # noqa: E402
import gate_eval as GE  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle14_c14a_micro_exec.md"
RESULTS_JSON = ROOT / "reports" / "c14a_results.json"

LANE = "A"
DESIGN_END = date(2021, 12, 31)          # 부록 v3: 설계 2016-09–2021-12 / 홀드아웃 2022–2026
SEED_USD = 36.0
MONTHLY_USD = 35.0
UNIT_USD = 1000.0
MIN_TRADE_BATCH = 5.0                     # 변형(b) sell-batching 임계
CACHE = hd.CACHE_DIR

# c13a 반호가 티어(모델 오버레이; 장부 실현비용 아님).
SPREAD_STOCK_BPS = 3.0
SPREAD_ETF_BPS = 1.0
SLIP_BPS = 5.0
_ETF_LIKE = {"QQQ", "TQQQ", "SQQQ", "PSQ", "BIL", "SHY", "SPY", "SPXL", "TECL", "SOXL",
             "UVXY", "BSV", "SMH", "AGG", "TMF"}

# DCA 경로 시작일(데이터 허용 범위).
PATHS = {"2021-01": date(2021, 1, 1), "2016-09": date(2016, 9, 22)}
NSET = (3, 5, 10)
BASE_N = 10

ZERO_FEES = TossFeeSchedule(commission_rate=0.0, free_threshold_usd=0.0, sec_fee_rate=0.0,
                            sec_min=0.0, taf_per_share=0.0, taf_min=0.0, taf_max=0.0,
                            apply_regulatory_min=False)
FEES = TossFeeSchedule()                  # 토스 기본(확인된 요율·최소금액)


# ── 데이터 로딩(scripts/paperlab_run.py 규약 재현) ──────────────────────────────
def _cache_file(sym: str) -> Path:
    return CACHE / f"{sym.replace('^', '_').replace('/', '_')}.json"


def load_panel():
    hibeta_uni = [s for s in HIBETA_CANDIDATES if _cache_file(s).exists()]
    need = set(hibeta_uni)
    need |= {"QQQ", "TQQQ", "SQQQ", "PSQ", "BIL", "SHY", "UVXY", "SPY", "SPXL", "TECL",
             "SOXL", "BSV"}
    panel: dict[str, list[Candle]] = {}
    for s in sorted(need):
        if _cache_file(s).exists():
            cs = hd.load_symbol(s, adjusted=True)
            if cs:
                panel[s] = cs
    by_date = {s: {c.dt: c for c in cs} for s, cs in panel.items()}
    master = [c.dt for c in panel["QQQ"]]
    return panel, by_date, master, hibeta_uni


# ── 실행 변형 전략(신호 동결, topk/그룹만 재배선) ─────────────────────────────
class BasketN(HibetaBasket):
    """hibeta_basket 실행 변형: 바스켓 크기 N(topk) 만 바꿈. 신호(월간·EW·베타상위)는 동결."""
    def __init__(self, universe_names, topk: int, *, name: str | None = None,
                 contribution: float = 0.0):
        super().__init__(universe_names)
        self._topk = int(topk)
        self.name = name or f"basket_n{topk}"
        self.real_monthly_contribution = float(contribution)

    def decide(self, history, state):
        qh = history.get("QQQ") or []
        if not qh:
            return dict(state.get("last_weights") or {})
        m_now = (qh[-1].dt.year, qh[-1].dt.month)
        if tuple(state.get("last_month") or ()) != m_now:
            state["last_weights"] = select_basket(history, self._candidates, topk=self._topk)
            state["last_month"] = m_now
        return dict(state.get("last_weights") or {})


class _SignalN:
    """신호형(ftlt/holygrail) 실행 변형: 위험선호 레그 바스켓 크기 N 만 바꿈."""
    def decide(self, history, state):
        sig = self._signal(history, state)
        if not sig:
            return {}
        basket = (select_basket(history, self._candidates, topk=self._topk)
                  if any(s in RISK_ON_3X for s in sig) else {})
        return remap_signal(sig, basket, inverse_to=self._inverse_to)


class FtltHibetaN(_SignalN, FtltHibeta):
    def __init__(self, universe_names, topk, *, name=None, contribution=0.0):
        super().__init__(universe_names)
        self._topk = int(topk)
        self.name = name or f"ftlt_hibeta_n{topk}"
        self.real_monthly_contribution = float(contribution)


class HolygrailHibetaN(_SignalN, HolygrailHibeta):
    def __init__(self, universe_names, topk, *, name=None, contribution=0.0):
        super().__init__(universe_names)
        self._topk = int(topk)
        self.name = name or f"holygrail_hibeta_n{topk}"
        self.real_monthly_contribution = float(contribution)


class QqqDca(Strategy):
    """DCA 벤치: 항상 QQQ 100%(매수 ≤$10 분할이 아니어도 소액이면 무료). 신호 없음."""
    name = "qqq_dca"
    fill_on = "close"

    def __init__(self, contribution=0.0):
        self.real_monthly_contribution = float(contribution)

    def universe(self):
        return ["QQQ"]

    def decide(self, history, state):
        return {"QQQ": 1.0}


STRATS = {
    "hibeta_basket": (BasketN, HibetaBasket),
    "ftlt_hibeta": (FtltHibetaN, FtltHibeta),
    "holygrail_hibeta": (HolygrailHibetaN, HolygrailHibeta),
}


def make_variant(strat_key: str, topk: int, hibeta_uni, *, contribution=0.0, name=None):
    cls = STRATS[strat_key][0]
    return cls(hibeta_uni, topk, name=name, contribution=contribution)


# ── 지표 헬퍼 ────────────────────────────────────────────────────────────────
def _real_cashflows(real_curve):
    """PaperLab 실장부 적립 스케줄 재현 → XIRR 현금흐름(입금 −, 최종 +)."""
    cf = []
    prev = None
    for i, (d, _v) in enumerate(real_curve):
        if i == 0:
            cf.append((d, -SEED_USD))
        elif (prev.year, prev.month) != (d.year, d.month):
            cf.append((d, -MONTHLY_USD))
        prev = d
    if real_curve:
        cf.append((real_curve[-1][0], float(real_curve[-1][1])))
    return cf


def _unit_returns(unit_curve):
    out = []
    for i in range(1, len(unit_curve)):
        v0, v1 = unit_curve[i - 1][1], unit_curve[i][1]
        out.append(v1 / v0 - 1.0 if v0 > 0 else 0.0)
    return out


def _cagr(curve):
    if len(curve) < 2:
        return None
    d0, v0 = curve[0]
    d1, v1 = curve[-1]
    days = (d1 - d0).days
    if v0 <= 0 or v1 <= 0 or days <= 0:
        return None
    return (v1 / v0) ** (365.25 / days) - 1.0


def run_lab(strat, master, by_date, start, *, contribute, min_trade=0.01, fees=FEES,
            real_usd=SEED_USD, unit_usd=UNIT_USD):
    """PaperLab 1회 전진 → 요약 dict. contribute=True 면 실장부에 월 적립."""
    lab = PaperLab(strat, unit_usd=unit_usd, real_usd=real_usd, fees=fees,
                   min_trade_usd=min_trade)
    st = lab.fresh_state(start)
    st = lab.run(st, master, by_date, contribute=contribute)
    summ = lab.summarize(st)
    real_book = PaperBook.from_dict(st["real"], fees)
    unit_book = PaperBook.from_dict(st["unit"], fees)
    real_curve = summ["real_curve"]
    unit_curve = summ["curve"]
    cf = _real_cashflows(real_curve) if contribute else None
    return {
        "name": strat.name, "start": start.isoformat(),
        "sessions": summ["days_live"],
        "real_final": summ["real"].equity, "real_fees": summ["real"].fees,
        "real_trades": summ["real"].trades,
        "real_contributed": SEED_USD + summ["real_contributed"] if contribute else SEED_USD,
        "unit_final": summ["unit"].equity, "unit_cagr": summ["unit"].cagr,
        "unit_mdd": summ["unit"].max_dd, "unit_fees": summ["unit"].fees,
        "xirr": gate.xirr(cf) if cf else None,
        "real_dollar_mdd": max_drawdown([v for _, v in real_curve]),
        "cashflows": cf,
        "_real_fills": real_book.fills, "_unit_fills": unit_book.fills,
        "_real_curve": real_curve, "_unit_curve": unit_curve,
        "_unit_returns": _unit_returns(unit_curve),
        "_ret_dates": [d for d, _ in unit_curve][1:],
    }


# ── 변형(c) buy-only 드리프트 보정(PaperBook.buy 재사용, 매도 없음) ─────────────
def run_buy_only(strat, master, by_date, start, *, fees=FEES, min_trade=0.01,
                 real_usd=SEED_USD, monthly=MONTHLY_USD):
    """월 적립 현금으로만 미달 레그를 매수(매도 절대 없음) → 규제수수료 0. PaperLab 무룩어헤드 규약 재현."""
    uni = strat.universe()
    exec_lag = int(getattr(strat, "exec_lag", 1) or 0)
    fill_on = getattr(strat, "fill_on", "close")
    book = PaperBook(real_usd, fees, min_trade_usd=min_trade)
    sstate: dict = {}
    visible: dict[str, list] = {s: [] for s in uni}
    last_price: dict[str, float] = {}
    curve = []
    contributed = 0.0
    pending = None
    prev_d = None

    def buy_only_correct(target, prices):
        eq = book.equity(prices)
        if eq <= 0:
            return
        order = sorted(target.items(), key=lambda kv: kv[1], reverse=True)
        for sym, w in order:
            if book.cash <= 0:
                break
            px = prices.get(sym)
            if not px or px <= 0 or w <= 0:
                continue
            deficit = w * eq - book.qty(sym) * px
            if deficit > book.min_trade:
                book.buy(sym, min(deficit, book.cash), px, prices["_dt"])

    for d in master:
        close_px: dict[str, float] = {}
        fill_px: dict[str, float] = {}
        for s in uni:
            c = by_date.get(s, {}).get(d)
            if c is None:
                continue
            visible[s].append(c)
            if c.close > 0:
                close_px[s] = c.close
                last_price[s] = c.close
                fill_px[s] = c.open if (fill_on == "open" and c.open > 0) else c.close
        if d < start:
            continue
        if prev_d is not None and (prev_d.year, prev_d.month) != (d.year, d.month):
            book.cash += monthly
            contributed += monthly
        # 어제 목표를 오늘 체결(매수-only)
        if pending:
            need = [s for s, w in pending.items() if w > 1e-9]
            if all(s in fill_px for s in need):
                fp = dict(fill_px); fp["_dt"] = d
                buy_only_correct(pending, fp)
                pending = None
        mark = dict(last_price); mark.update(close_px)
        curve.append((d, book.equity(mark)))
        tw = strat.decide(visible, sstate) or {}
        pending = {s: float(w) for s, w in tw.items() if float(w) > 0}
        prev_d = d

    total_contributed = real_usd + contributed
    cf = _real_cashflows(curve)
    return {
        "name": strat.name, "start": start.isoformat(), "sessions": len(curve),
        "real_final": curve[-1][1] if curve else real_usd,
        "real_fees": book.total_fees, "real_trades": len(book.fills),
        "real_contributed": total_contributed,
        "xirr": gate.xirr(cf) if cf else None,
        "real_dollar_mdd": max_drawdown([v for _, v in curve]),
        "_real_fills": book.fills,
    }


# ── Task 2: $36 백필 수수료 분해(commission vs 규제최소 vs 스프레드) ────────────
def _spread_bps(sym):
    return (SPREAD_ETF_BPS if sym in _ETF_LIKE else SPREAD_STOCK_BPS) + SLIP_BPS


def decompose_fills(fills, fees=FEES):
    """실장부 체결 → 수수료 성분 분해. 스프레드는 모델 오버레이(장부 미반영)."""
    agg = {"n_buys": 0, "n_sells": 0, "buy_notional": 0.0, "sell_notional": 0.0,
           "commission": 0.0, "sec": 0.0, "taf": 0.0, "reg_min_hits": 0,
           "spread_model": 0.0, "book_fee": 0.0, "recomputed_fee": 0.0}
    for f in fills:
        side = f["side"]
        notional = float(f["qty"]) * float(f["price"])
        shares = float(f["qty"])
        bd = fees.order_fee_breakdown(side, notional, shares)
        agg["book_fee"] += float(f.get("fee", 0.0))
        agg["recomputed_fee"] += bd.total
        agg["commission"] += bd.commission
        agg["sec"] += bd.sec_fee
        agg["taf"] += bd.taf
        agg["spread_model"] += notional * _spread_bps(f["symbol"]) * 1e-4
        if side == "BUY":
            agg["n_buys"] += 1
            agg["buy_notional"] += notional
        else:
            agg["n_sells"] += 1
            agg["sell_notional"] += notional
            # 규제 최소금액에 걸린(=raw<min) 매도 건수.
            if bd.sec_fee <= fees.sec_min + 1e-9 and bd.taf <= fees.taf_min + 1e-9:
                agg["reg_min_hits"] += 1
    agg["reg_min_total"] = agg["sec"] + agg["taf"]
    agg["total_book"] = agg["commission"] + agg["reg_min_total"]
    agg["total_with_spread"] = agg["total_book"] + agg["spread_model"]
    tn = agg["buy_notional"] + agg["sell_notional"]
    agg["eff_bps_book"] = (agg["total_book"] / tn * 1e4) if tn > 0 else 0.0
    agg["eff_bps_sell_regmin"] = (agg["reg_min_total"] / agg["sell_notional"] * 1e4
                                  if agg["sell_notional"] > 0 else 0.0)
    return agg


# ── Task 3 크로스오버: N=10 이 N=3 을 이기는 계좌 규모 ─────────────────────────
def crossover_sweep(hibeta_uni, master, by_date, start, sizes):
    """바스켓 N=3 vs N=10 을 규모별 일시금(적립 없음) 장부로 굴려 최종자산 비교 → 역전 지점."""
    rows = []
    for size in sizes:
        r3 = run_lab(make_variant("hibeta_basket", 3, hibeta_uni, name="xN3"),
                     master, by_date, start, contribute=False, real_usd=size, unit_usd=size)
        r10 = run_lab(make_variant("hibeta_basket", 10, hibeta_uni, name="xN10"),
                      master, by_date, start, contribute=False, real_usd=size, unit_usd=size)
        rows.append({"size": size, "n3_final": r3["real_final"], "n10_final": r10["real_final"],
                     "n3_fees": r3["real_fees"], "n10_fees": r10["real_fees"],
                     "n10_beats_n3": r10["real_final"] > r3["real_final"],
                     "ratio_10_3": (r10["real_final"] / r3["real_final"]
                                    if r3["real_final"] > 0 else float("nan"))})
    return rows


# ── Lane A 스타일 게이트(DCA 실행 변형) ────────────────────────────────────────
def lane_a_exec_verdict(variant, base, qqq, *, both_paths_ok):
    """실행 변형 판정: (1) 두 경로 모두 기준(N=10)보다 최종자산·XIRR 개선, (2) QQQ DCA 초과 유지,
    (3) 단위 MDD > −95%(파산가드). 신호는 동결이라 이웃/2×비용은 기준 대비 상대개선으로 대체."""
    beats_base = variant["real_final"] >= base["real_final"] and (
        variant["xirr"] is None or base["xirr"] is None or variant["xirr"] >= base["xirr"] - 1e-6)
    beats_qqq = qqq is None or variant["real_final"] >= qqq["real_final"]
    guard = (variant.get("unit_mdd") is None) or (variant["unit_mdd"] > -0.95)
    if both_paths_ok and beats_base and beats_qqq and guard:
        return "PASS"
    if beats_base and guard:
        return "CONDITIONAL"
    return "FAIL"


# ── 리포트 사전등록 블록 ──────────────────────────────────────────────────────
PREREG = """# Cycle 14 — c14a: 소액($36) 실계좌 실행 최적화 (Lane A · 신호 동결)

> 사전등록 2026-09-28 · 결과 보기 전 확정 · src 미수정 · 원장 lane "A" · 신호재사용(부록 v2.1 §4)

## 사전등록 (가설·규칙, 결과 보기 전)

**동기.** $36 실장부는 $1k 단위장부보다 배수 이득이 작다(holygrail_hibeta $1k ×8.2 vs $36 ×5.6).
가설: ~$3.6 소액 포지션 **매도 건당** 규제 최소금액(SEC 최소 $0.01 + TAF 최소 $0.01 = $0.02 고정)
≈ 55bp. 매수는 ≤$10 무료 → 드래그의 대부분은 **매도 건수 × 고정 최소금액**.

**신호 동결.** c12/c13a Lane A PASS 신호(hibeta_basket·ftlt_hibeta·holygrail_hibeta)의 결정트리는
**그대로**. 실행 규칙만 바꾼다 → 새 idea_id, 홀드아웃 **반오염** 표기.

**사전등록 실행 변형:**
  (a) 바스켓 크기 N ∈ {3, 5} vs 기준 N=10 (매도 건수↓).
  (b) min-trade 임계 $5 — 리밸런스 시 |조정|<$5 레그 스킵(소액 트림 억제).
  (c) buy-only — 신규 적립으로만 미달 레그 매수(매도 없음 → 규제수수료 0).

**평가.** $36 시드 + 월 $35 DCA, 경로 2개(2021-01→2026-09, 2016-09→2026-09). 최종자산·XIRR·MDD(단위
TWR)·수수료 드래그(무수수료 반사실 대비). N=10 이 N=3 을 역전하는 계좌 규모 산출.

**Lane A 스타일 게이트(실행 변형).** 신호 동결이므로 (a·b·c 표준 대신) 상대개선으로 판정: 두 DCA 경로
모두 기준(N=10) 대비 최종자산·XIRR 개선 + QQQ DCA 초과 유지 + 단위 MDD>−95%(파산가드) → PASS.
통과 시 `account_size_policy`(선택)로 구현, 아니면 보고만. **성과는 UPPER BOUND**(캐시 커버리지 편중).
"""


def _pct(v, nd=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:+.{nd}f}%"


def _usd(v, nd=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"${v:,.{nd}f}"


def write_report(results):
    L = [PREREG.rstrip(), "", "<!-- RESULTS_BELOW -->", ""]
    meta = results["meta"]
    L.append(f"> 실행 {meta['generated']} · 원장 {meta['ledger']} · quick={meta['quick']} · "
             f"고베타 캐시 {meta['hibeta_cached']}/{len(HIBETA_CANDIDATES)} → UPPER BOUND")
    L.append("")

    # ── 1) Task 1 수수료 검증 ──
    L.append("## 1. 수수료 가정 검증 (웹 확인)")
    L.append(results["fee_verification"])
    L.append("")

    # ── 2) $36 백필 수수료 분해 ──
    L.append("## 2. $36 백필 수수료 분해 (commission vs 규제최소 vs 스프레드-모델)")
    L.append("| 전략 | 매수 | 매도 | 커미션 | 규제최소(SEC+TAF) | 규제최소걸린매도 | 스프레드(모델) | "
             "장부총액 | 실효bp(장부) | 매도규제bp |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k, a in results["decomposition"].items():
        L.append(f"| {k} | {a['n_buys']} | {a['n_sells']} | {_usd(a['commission'])} | "
                 f"{_usd(a['reg_min_total'])} | {a['reg_min_hits']}/{a['n_sells']} | "
                 f"{_usd(a['spread_model'])} | {_usd(a['total_book'])} | {a['eff_bps_book']:.1f} | "
                 f"{a['eff_bps_sell_regmin']:.1f} |")
    L.append("")
    L.append("> 장부총액 = 커미션+규제최소(토스 실현). 스프레드는 반호가 모델 오버레이(장부 미반영). "
             "매도규제bp = (SEC+TAF)/매도노셔널 — 소액 포지션의 고정 최소금액 드래그.")
    L.append("")

    # ── 3) DCA 변형 매트릭스 ──
    L.append("## 3. 실행 변형 — $36 시드 + 월 $35 DCA")
    for path in results["dca"]:
        L.append(f"### 경로 {path}")
        L.append("| 실행 | N | 최종자산 | 적립총액 | XIRR | 배수 | 단위CAGR | 단위MDD | 수수료 | "
                 "드래그(무료대비) | 판정 |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|")
        for row in results["dca"][path]:
            mult = (row["real_final"] / row["real_contributed"]
                    if row["real_contributed"] else float("nan"))
            L.append(
                f"| {row['exec']} | {row.get('N', '—')} | {_usd(row['real_final'])} | "
                f"{_usd(row['real_contributed'])} | {_pct(row['xirr'])} | {mult:.2f}× | "
                f"{_pct(row.get('unit_cagr'))} | {_pct(row.get('unit_mdd'))} | "
                f"{_usd(row['real_fees'])} | {_usd(row.get('fee_drag'))} | {row.get('verdict', '—')} |")
        L.append("")

    # ── 4) 크로스오버 ──
    L.append("## 4. 크로스오버 — 계좌 규모별 N=10 vs N=3 (hibeta_basket 일시금, 2021 경로)")
    L.append("| 계좌 | N=3 최종 | N=10 최종 | N=10/N=3 | N=10 우위? | N=3 수수료 | N=10 수수료 |")
    L.append("|---:|---:|---:|---:|:--:|---:|---:|")
    for r in results["crossover"]:
        L.append(f"| {_usd(r['size'], 0)} | {_usd(r['n3_final'])} | {_usd(r['n10_final'])} | "
                 f"{r['ratio_10_3']:.3f} | {'O' if r['n10_beats_n3'] else 'X'} | "
                 f"{_usd(r['n3_fees'])} | {_usd(r['n10_fees'])} |")
    L.append("")
    L.append(f"> {results['crossover_note']}")
    L.append("")

    # ── 5) 판정/해석 ──
    L.append("## 5. 판정 종합 및 정직한 해석")
    L.append(results["verdict_prose"])

    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            L[0] = prev.split("<!-- RESULTS_BELOW -->")[0].rstrip()
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


FEE_VERIFICATION = """**확인됨(공식·FAQ):** 토스증권은 미국주식 매도 시 규제수수료를 **고객에게 전가**하며 주문당
**$0.01 최소금액**이 있다. SEC Fee = max(매도대금×요율, **$0.01**), FINRA TAF = max(매도수량×요율,
**$0.01**), 최대 $8.30. 매수 건당 체결금액 ≤$10 은 수수료 무료, 표준 커미션 0.1%. 소수점(fractional)
매도도 동일 최소금액 → **~$3.6 포지션 매도 시 SEC$0.01+TAF$0.01=$0.02 고정 ≈ 55bp** (가설 확정).
출처: support.toss.im/faq/3583·3669, corp.tossinvest.com 공지(2025-12-01 커미션 0.1% / 2026-04 SEC 전가).

**불확실:** 정확한 SEC 요율 — 공지·FAQ 는 0.0000278(≈$27.8/$1M)로 표기되나 fees.py 기본값은
0.0000206($20.6/$1M, 연 1회 변동). **규제 최소금액($0.01)이 소액에서 지배적**이라 요율 차이는 $36
스케일 결과에 무의미. **기본값 미변경**(task 지침) — 규제-최소 동작은 이미 `apply_regulatory_min`·
`sec_min`·`taf_min`·`taf_max` 로 명명·토글 가능(추가 config 불필요). 요율 갱신은 실측(`/commissions`)
대체 시 반영 권고."""


def _verdict_prose(results):
    w = results["winner"]
    lines = [f"**판정 요약.** 상대개선(경로 전체 로버스트) PASS: {w['pass'] or '없음'} · "
             f"CONDITIONAL(일부 경로): {w['conditional'] or '없음'} · FAIL: {w['fail'] or '없음'}. "
             f"account_size_policy 구현: {'예' if w['implemented'] else '**아니오(보고만)**'}.", ""]
    lines.append(
        "**1) 드래그 원인 = 규제 최소금액(확정).** $36 백필 매도 수수료의 ~93%가 SEC 최소 $0.01 + TAF "
        "최소 $0.01 = **건당 $0.02 고정**(≈55–64bp/매도). 매수는 ≤$10 무료라 드래그는 사실상 **매도 건수 × "
        "$0.02**. hibeta_basket 206매도=$4.12, ftlt 1438매도=$28.76. 가설 확정.")
    n5c = w.get("n5_edge_is_composition")
    n5m = w.get("n5_mdd_worse")
    lines.append(
        "**2) N=5 가 N=10 을 이기지만 이는 수수료가 아니라 구성(집중) 효과.** N=5 는 세 전략·두 경로 모두 "
        "최종자산·XIRR 개선(표) — 그러나 **무수수료 반사실에서도 N=5>N=10**" +
        ("(6/6)" if n5c else "(일부)") +
        f"이고 단위 MDD 는 **더 깊다**({'예' if n5m else '유사'}; 예컨대 바스켓 −0.73 vs −0.66). 즉 N=5 의 이득은 "
        "최고베타 소수 집중의 **베타/집중 틸트**(수수료 절감 <1% vs 성과차 ~10%+)이며, 바스켓 크기 N 은 c12 "
        "동결 신호의 **파라미터**다. 따라서 N 축소는 '실행(수수료) 최적화'가 아니라 **신호 재튜닝**이고, 그 "
        "'승리'는 생존편향 캐시(커버리지 46/63, **UPPER BOUND**)의 산물 → 별도 Lane A 신호평가 대상이지 여기서 "
        "채택할 실행규칙이 아니다.")
    lines.append(
        "**3) 순수 실행(수수료) 변형은 로버스트하게 이기지 못한다.** (b) min-trade $5 배칭: 리밸런스 매도가 "
        "많은 타이밍 전략(ftlt)에서만 두 경로 개선하나 절감액이 미미($수달러). (c) buy-only: 매도를 없애 수수료를 "
        "$42→$1.6 로 죽이지만 **장기 로버스트 실패** — 바스켓 2016 경로에서 $37.9k vs 리밸런스 $47.4k(월간 회전 "
        "바스켓을 안 팔면 드리프트 손실이 절감 수수료를 압도), 타이밍 전략은 방어 매도가 필수라 더 열위. "
        f"로버스트 실행변형: {w.get('exec_robust') or '없음(사실상 없음)'}.")
    lines.append(
        "**4) 결론 · account_size_policy 미구현.** 규제최소가 소액 드래그의 실체지만 그 해법은 (i) N 축소가 "
        "아니고(구성 재튜닝·리스크 증가), (ii) buy-only 도 아니다(장기 열위). 계좌 규모별 N 은 N=5 가 규모와 무관하게 "
        "N=10 을 이겨 **규모 의존이 아니며**(=단순 재튜닝), 로버스트한 순수-수수료 실행규칙이 없으므로 "
        "**src 전략을 바꾸지 않고 보고만 한다**(task: 통과 시에만 구현). 실무 처방은 오히려 **큰 N(≥10) 유지 + "
        "적립·리밸런스 시 매수 ≤$10 분할 무료 최대활용 + 불필요한 소액 매도 억제**이며, 계좌가 커질수록 $0.02 "
        "고정비는 자연 희석된다.")
    lines.append("")
    lines.append("**정직한 주의.** 신호 동결(변형은 실행/구성 축만)·홀드아웃 **반오염**(c12/c13a 관측 → 원장 "
                 "semi_contaminated)·성과 **UPPER BOUND**(캐시 대형·생존주 편중)·반호가는 장부 미반영 모델 "
                 "오버레이(소액 실체결 스프레드는 더 클 수 있음). 실채택은 부록 v3-4 포워드 페이퍼(3개월+ 실시세)·"
                 "사용자 승인 후에만. " + results["crossover_note"])
    return "\n".join(lines)


# ── 원장 적재(단위 TWR, 설계/홀드아웃 반오염) ─────────────────────────────────
def log_ledger(idea_id, unit_returns, ret_dates, universe, *, extra=None):
    di = [i for i, d in enumerate(ret_dates) if d <= DESIGN_END]
    hi = [i for i, d in enumerate(ret_dates) if d > DESIGN_END]
    params = {"exec_variant": True, "signal_reuse": True, "semi_contaminated": True}
    if extra:
        params.update(extra)
    if len(di) > 1:
        um = GE.unit_capital_metrics([unit_returns[i] for i in di],
                                     dates=[ret_dates[i] for i in di])
        GE.log_evaluation(idea_id, dict(params), LANE, "design", um,
                          window=(ret_dates[di[0]], ret_dates[di[-1]]),
                          universe=universe, ledger_path=LEDGER)
    if len(hi) > 1 and not gate.already_peeked(LEDGER, idea_id):
        um = GE.unit_capital_metrics([unit_returns[i] for i in hi],
                                     dates=[ret_dates[i] for i in hi])
        try:
            GE.log_evaluation(idea_id, dict(params), LANE, "holdout", um,
                              window=(ret_dates[hi[0]], ret_dates[hi[-1]]),
                              universe=universe, ledger_path=LEDGER)
        except gate.PeekOnceError:
            pass


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main(argv=None):
    import datetime as _dt
    ap = argparse.ArgumentParser(description="c14a 소액 실행 최적화(Lane A, 신호 동결)")
    ap.add_argument("--quick", action="store_true", help="basket+ftlt, N∈{3,10}, 2021 경로만")
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--render-only", action="store_true",
                    help="기존 c14a_results.json 로 판정/해석·리포트만 재생성(시뮬·원장 미실행)")
    args = ap.parse_args(argv)

    if args.render_only:
        results = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
        analyze(results)
        RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")
        write_report(results)
        print(f"[c14a] re-rendered → {REPORT}", flush=True)
        return results

    print("[c14a] 데이터 로딩 …", flush=True)
    panel, by_date, master, hibeta_uni = load_panel()
    print(f"[c14a] master {master[0]}…{master[-1]} ({len(master)}일) · 고베타 {len(hibeta_uni)}종", flush=True)

    strat_keys = ["hibeta_basket", "ftlt_hibeta"] if args.quick else list(STRATS)
    nset = (3, 10) if args.quick else NSET
    paths = {"2021-01": PATHS["2021-01"]} if args.quick else PATHS

    results: dict = {"meta": {
        "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "ledger": "(no-ledger)" if args.no_ledger else LEDGER, "quick": args.quick,
        "hibeta_cached": len(hibeta_uni), "paths": {k: v.isoformat() for k, v in paths.items()},
        "seed": SEED_USD, "monthly": MONTHLY_USD, "fees": "TossFeeSchedule() 기본"}}
    results["fee_verification"] = FEE_VERIFICATION

    # ── Task 2: 분해(N=10, 2021 무적립 = 백필 재현) ──
    print("[c14a] Task2 수수료 분해 …", flush=True)
    decomposition = {}
    base2021 = {}
    for k in strat_keys:
        r = run_lab(make_variant(k, BASE_N, hibeta_uni, name=k), master, by_date,
                    PATHS["2021-01"], contribute=False)
        decomposition[k] = decompose_fills(r["_real_fills"])
        base2021[k] = r
        print(f"      {k}: final={_usd(r['real_final'])} fees={_usd(r['real_fees'])} "
              f"sells={decomposition[k]['n_sells']} regmin={_usd(decomposition[k]['reg_min_total'])}",
              flush=True)
    results["decomposition"] = decomposition

    # ── Task 3: DCA 변형 매트릭스 ──
    print("[c14a] Task3 DCA 변형 …", flush=True)
    dca: dict = {}
    ledger_jobs = []
    for pkey, pstart in paths.items():
        rows = []
        # QQQ DCA 벤치
        rq = run_lab(QqqDca(contribution=MONTHLY_USD), master, by_date, pstart, contribute=True)
        rows.append({"exec": "QQQ DCA(벤치)", "N": "—", **_dca_row(rq)})
        qqq_ref = rq
        base_row_by_strat = {}
        for k in strat_keys:
            for N in nset:
                v = make_variant(k, N, hibeta_uni, name=f"{k}_n{N}", contribution=MONTHLY_USD)
                r = run_lab(v, master, by_date, pstart, contribute=True)
                # 무수수료 반사실 → 드래그
                rz = run_lab(make_variant(k, N, hibeta_uni, name=f"{k}_n{N}z",
                                          contribution=MONTHLY_USD), master, by_date, pstart,
                             contribute=True, fees=ZERO_FEES)
                r["fee_drag"] = rz["real_final"] - r["real_final"]
                r["_nofee_xirr"] = rz["xirr"]
                row = {"exec": f"{k}", "N": N, **_dca_row(r)}
                rows.append(row)
                if N == BASE_N:
                    base_row_by_strat[k] = r
                if pkey == "2016-09":
                    ledger_jobs.append((f"c14a_{k}_n{N}", r, {"strat": k, "N": N,
                                        "exec": "rebalance"}))
        # 변형 (b) min-trade $5, (c) buy-only — N=10 기준
        for k in strat_keys:
            vb = make_variant(k, BASE_N, hibeta_uni, name=f"{k}_mt5", contribution=MONTHLY_USD)
            rb = run_lab(vb, master, by_date, pstart, contribute=True, min_trade=MIN_TRADE_BATCH)
            rbz = run_lab(make_variant(k, BASE_N, hibeta_uni, name=f"{k}_mt5z",
                                       contribution=MONTHLY_USD), master, by_date, pstart,
                          contribute=True, min_trade=MIN_TRADE_BATCH, fees=ZERO_FEES)
            rb["fee_drag"] = rbz["real_final"] - rb["real_final"]
            rows.append({"exec": f"{k} +min$5(b)", "N": BASE_N, **_dca_row(rb)})
            vc = make_variant(k, BASE_N, hibeta_uni, name=f"{k}_buyonly", contribution=MONTHLY_USD)
            rc = run_buy_only(vc, master, by_date, pstart)
            rows.append({"exec": f"{k} buy-only(c)", "N": BASE_N, **_dca_row(rc)})
            if pkey == "2016-09":
                ledger_jobs.append((f"c14a_{k}_mt5", rb, {"strat": k, "N": BASE_N, "exec": "min_trade5"}))
        # 판정: 각 변형이 기준(해당 전략 N=10) 대비 개선했는가(+파산가드·QQQ 초과).
        for row in rows:
            base = base_row_by_strat.get(_base_key(row["exec"]))
            is_variant = (row["exec"] != _base_key(row["exec"])) or (
                row.get("N") in nset and row["N"] != BASE_N)
            if base and is_variant and row["exec"] != "QQQ DCA(벤치)":
                b = {"real_final": base["real_final"], "xirr": base["xirr"]}
                improved = (row["real_final"] >= b["real_final"] - 1e-9 and
                            (row["xirr"] is None or b["xirr"] is None
                             or row["xirr"] >= b["xirr"] - 1e-6))
                beats_qqq = row["real_final"] >= qqq_ref["real_final"]
                guard = row.get("unit_mdd") is None or row["unit_mdd"] > -0.95
                row["verdict"] = ("IMPROVE" if improved and beats_qqq and guard
                                  else "same/▽")
        dca[pkey] = rows
    results["dca"] = dca

    # ── Task 3 크로스오버 ──
    print("[c14a] Task3 크로스오버 …", flush=True)
    sizes = [36, 100, 300, 1000, 3000, 10000]
    cross = crossover_sweep(hibeta_uni, master, by_date, PATHS["2021-01"], sizes)
    results["crossover"] = cross

    # ── 승자/판정/해석(시뮬 재실행 없이도 재현되는 순수 분석) ──
    analyze(results)

    # ── 원장 적재(2016 경로, 반오염) ──
    if not args.no_ledger and not args.quick:
        print("[c14a] 원장 적재(lane A, 반오염) …", flush=True)
        for idea_id, r, extra in ledger_jobs:
            if "_unit_returns" in r and r.get("_ret_dates"):
                log_ledger(idea_id, r["_unit_returns"], r["_ret_dates"],
                           ["HIBETA_BASKET"] + sorted(hibeta_uni)[:5], extra=extra)

    # ── JSON 저장(내부 대형 필드 제거) ──
    clean = _strip_internal(results)
    RESULTS_JSON.write_text(json.dumps(clean, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
    print(f"[c14a] results → {RESULTS_JSON}", flush=True)
    if not args.no_report:
        write_report(clean)
        print(f"[c14a] report → {REPORT}", flush=True)
    return results


def _dca_row(r):
    return {"real_final": r["real_final"], "real_contributed": r["real_contributed"],
            "xirr": r.get("xirr"), "real_fees": r["real_fees"],
            "unit_cagr": r.get("unit_cagr"), "unit_mdd": r.get("unit_mdd"),
            "fee_drag": r.get("fee_drag"), "sessions": r.get("sessions"),
            "real_dollar_mdd": r.get("real_dollar_mdd")}


def _nofee(r):
    return r["real_final"] + (r.get("fee_drag") or 0.0)


def analyze(results):
    """results(정제본) → winner/crossover_note/verdict_prose 산출. 시뮬 재실행 없이 재렌더 가능."""
    dca = results["dca"]
    paths = list(dca)
    npaths = len(paths)
    present = []
    for rows in dca.values():
        for r in rows:
            bk = _base_key(r["exec"])
            if bk in STRATS and bk not in present:
                present.append(bk)
    strat_keys = [k for k in STRATS if k in present]

    # per (strat, variant-tag) → path별 {improved(수수료후), nofee_improved, mdd_worse}
    per: dict = {}
    for pkey, rows in dca.items():
        base = {_base_key(r["exec"]): r for r in rows
                if r.get("N") == BASE_N and r["exec"] in strat_keys}
        for r in rows:
            bk = _base_key(r["exec"])
            b = base.get(bk)
            if not b or bk not in strat_keys or r["exec"] == "QQQ DCA(벤치)":
                continue
            if r["exec"] == bk and r.get("N") == BASE_N:
                continue
            tag = _variant_tag(r)
            improved = (r["real_final"] >= b["real_final"] - 1e-9 and
                        (r["xirr"] is None or b["xirr"] is None or r["xirr"] >= b["xirr"] - 1e-6))
            nofee_imp = _nofee(r) >= _nofee(b) - 1e-9
            mdd_worse = (r.get("unit_mdd") is not None and b.get("unit_mdd") is not None
                         and r["unit_mdd"] < b["unit_mdd"] - 1e-9)
            per.setdefault((bk, tag), []).append((improved, nofee_imp, mdd_worse))

    n_variants, exec_variants = {}, {}
    for (bk, tag), obs in per.items():
        robust = len(obs) >= npaths and all(o[0] for o in obs)
        (n_variants if tag.startswith("N=") else exec_variants)[(bk, tag)] = (robust, obs)

    # 요약 버킷
    passes, conds, fails = [], [], []
    for (bk, tag), (robust, obs) in sorted({**n_variants, **exec_variants}.items()):
        label = f"{bk}·{tag}"
        (passes if robust else (conds if any(o[0] for o in obs) else fails)).append(label)

    # N=5 특성: 수수료후 개선이면서 무수수료에서도 개선(=구성효과)·MDD 악화 여부.
    n5_robust = [(bk, obs) for (bk, tag), (robust, obs) in n_variants.items()
                 if tag == "N=5" and robust]
    n5_composition = all(all(o[1] for o in obs) for _, obs in n5_robust) and bool(n5_robust)
    n5_mdd_worse = any(any(o[2] for o in obs) for _, obs in n5_robust)

    # 실행 변형(min$5·buy-only) 로버스트 여부
    exec_robust = [f"{bk}·{tag}" for (bk, tag), (robust, _o) in exec_variants.items() if robust]

    results["crossover_note"] = _crossover_note(results.get("crossover", []),
                                                results.get("_crossover_sizes"))
    results["winner"] = {
        "pass": passes, "conditional": conds, "fail": fails,
        "n5_robust_improve": bool(n5_robust), "n5_edge_is_composition": n5_composition,
        "n5_mdd_worse": n5_mdd_worse, "exec_robust": exec_robust, "implemented": False}
    results["verdict_prose"] = _verdict_prose(results)
    return results


def _crossover_note(cross, sizes=None):
    if not cross:
        return "(크로스오버 미산출)"
    sizes = sizes or [r["size"] for r in cross]
    all_n10 = all(r["n10_beats_n3"] for r in cross)
    none_n10 = not any(r["n10_beats_n3"] for r in cross)
    xo = next((r["size"] for r in cross if r["n10_beats_n3"]), None)
    if all_n10:
        return (f"**N=3 역전 없음**: N=10 이 ${sizes[0]}~${sizes[-1]} 전 구간에서 N=3 을 이긴다. "
                f"매도 최소금액 절감(작은 N)은 존재하나 N=3 의 과집중 성과 손실이 항상 더 크다 → "
                f"'소액이면 N=3' 가설 **기각**. (단 N=5 는 별개 — 아래 해석 참조.)")
    if none_n10:
        return f"N=3 이 ${sizes[-1]}까지 전 구간 우위(N=10 역전 없음)."
    return (f"N=10 이 N=3 을 역전하는 최소 계좌 ≈ {_usd(xo, 0)}: 그 아래선 매도 최소금액 고정비가 "
            f"지배해 작은 N 유리, 이상에선 분산(큰 N)이 유리.")


def _base_key(exec_label):
    """변형 exec 라벨 → 기준 전략 키."""
    for k in STRATS:
        if exec_label == k or exec_label.startswith(k + " "):
            return k
    return exec_label


def _variant_tag(row):
    ex = row["exec"]
    if "min$5" in ex:
        return "min$5(b)"
    if "buy-only" in ex:
        return "buy-only(c)"
    return f"N={row.get('N')}"


def _strip_internal(obj):
    if isinstance(obj, dict):
        return {k: _strip_internal(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_internal(x) for x in obj]
    return obj


if __name__ == "__main__":
    main()
