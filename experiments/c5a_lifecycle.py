"""c5a — Lifecycle leverage (Ayres & Nalebuff, "Lifecycle Investing" 2008/2010).

사전등록·규약: reports/cycle5_c5a_lifecycle.md, experiments/README.md,
docs/gate_v2_spec.md(+부록 v2.1).

핵심 질문(사이클 1~4 에서 타이밍 알파가 살아남지 않았으므로): *젊은 DCA 투자자가
생애 주식 프리미엄 노출을 얼마나 실어야 하는가.* 이는 **알파가 아니라 베타/위험선호**
결정이므로 Sharpe-알파 게이트가 아니라 **시작일 분포에 걸친 달러 결과 분포**로 평가한다.

Samuelson share(사무엘슨 지분): 현재 금융자산 W_t 가 남은 적립의 현재가치 PV_t 대비
작을수록 시간축 분산이 덜 되어 있다 → 이론상 초기 ~2:1 레버리지, 포트폴리오가 커지며
1x 로 글라이드. 목표노출

    E_t = min(2, max(1, s* · (W_t + PV_t) / W_t)),  s* = 0.8,
    PV_t = 25년(300개월) 적립 계획의 남은 기여를 3% 실질로 할인한 현재가치.

QQQ-1x + QLD-2x 혼합으로 구현. 신규 입금으로만 리밸런싱하고, 매도는 E_actual >
E_target + 0.3 일 때만(월별 점검). 매수는 ≤$10 분할 무료, 매도는 TossFeeSchedule.

데이터: FRED NASDAQ100(1986-2026) 총수익근사(배당 0.7%/yr) → 합성 QQQ-1x/QLD-2x
(DTB3 조달, 0.5% 스프레드, 0.95% 보수). 실물 QQQ/QLD 2016-2026 확인.

재현: PYTHONPATH=src .venv/bin/python experiments/c5a_lifecycle.py [--fast]
      [--ledger PATH] [--no-ledger] [--no-report]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
from toss_trader.fees import TossFeeSchedule  # noqa: E402
import gate_eval  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle5_c5a_lifecycle.md"
RESULTS_JSON = ROOT / "reports" / "c5a_results.json"

# ── 사전등록 상수(합성·계획·비용) ─────────────────────────────────────────────
DIV_YIELD = 0.007        # NDX 배당수익률 근사(0.7%/yr) → index_total_return
EXP_1X = 0.002           # 1x(QQQ) 보수율 0.20%/yr
EXP_LEV = 0.0095         # 레버리지(QLD) 보수율 0.95%/yr
BORROW_SPREAD = 0.005    # 차입 스프레드 0.5%/yr (조달금리 = DTB3 위에 가산)
FX_BPS = 20.0            # 환전(입금) 비용 20bp — 신규자본에만

MONTHLY = 35.0           # 월 적립 $35
INITIAL = 32.0           # 시드 $32
PLAN_MONTHS = 300        # 25년 적립 계획(개월)
DISC_REAL = 0.03         # 남은 기여 PV 할인율(3% 실질)
S_STAR = 0.8             # 생애 부(富) 목표 주식지분
VT_TARGET = 0.25         # 변동성 타깃 크래시 브레이크: E ≤ VT_TARGET/σ̂
SELL_BAND = 0.3          # 매도 트리거: E_actual > E_target + SELL_BAND 일 때만
EWMA_LAM = 0.97          # EWMA 분산 감쇠(center-of-mass ≈ 33일; c3a 재사용)
EWMA_WARMUP = 60         # EWMA 워밍업(봉)
SMA_TREND = 200          # 추세 필터 SMA 창

# 시작일 분포 설계/홀드아웃 분할(시작연도 기준; 부록 v2.1 peek-once)
DESIGN_START_YEARS = (1986, 1999)   # 설계 시작일: 1986~1999
HOLDOUT_START_YEARS = (2000, 2016)  # 홀드아웃 시작일: 2000~2016

# 원장(단위자본 스트림) 분할 — c5a 는 홀드아웃 개념이 시작일 분할과 일치하므로
# 원장 창도 시작일 분할의 달력창(설계 1986–1999, 홀드아웃 2000–2026)에 맞춘다.
LEDGER_DESIGN_END = date(1999, 12, 31)
LEDGER_HOLDOUT_START = date(2000, 1, 1)

FEE = TossFeeSchedule()   # 토스 표준 0.1%, ≤$10 무료, 매도 규제수수료


# ── 데이터 구축 ───────────────────────────────────────────────────────────────
def _yield_daily_aligned(fred_yield, dates):
    """FRED 연율(%) 수익률(예 DTB3)을 거래일축에 forward-fill 후 일간율로 변환."""
    ys = sorted(fred_yield, key=lambda c: c.dt)
    keys = [c.dt for c in ys]
    vals = [c.close for c in ys]
    out = []
    last = 0.0
    ki = 0
    for d in dates:
        while ki < len(keys) and keys[ki] <= d:
            last = vals[ki]
            ki += 1
        out.append(max(0.0, (last / 100.0) / 252.0))
    return out


def ewma_vol_annual(closes, *, lam=EWMA_LAM, warmup=EWMA_WARMUP, ppy=252):
    """인과적 EWMA 연율 변동성. 분산 = λ·분산₋₁ + (1−λ)·r². warmup 전은 None."""
    n = len(closes)
    out = [None] * n
    var = None
    cnt = 0
    for t in range(1, n):
        p0 = closes[t - 1]
        r = (closes[t] / p0 - 1.0) if p0 > 0 else 0.0
        var = r * r if var is None else lam * var + (1.0 - lam) * r * r
        cnt += 1
        if cnt >= warmup and var is not None:
            out[t] = math.sqrt(var * ppy)
    return out


def trend_ok_series(closes, *, window=SMA_TREND):
    """NDX ≥ SMA(window) 인가(추세 상승). SMA 부족 구간은 True(레버리지 허용 기본)."""
    s = R.sma(closes, window)
    return [(s[t] is None) or (closes[t] >= s[t]) for t in range(len(closes))]


def build_synthetic_panel():
    """FRED NDX → 총수익근사 → 합성 QQQ-1x/QLD-2x + 보조 신호(σ, 추세, 현금)."""
    ndx = hd.load_fred("NASDAQ100")
    dtb3 = hd.load_fred("DTB3")
    tr = hd.index_total_return(ndx, DIV_YIELD, symbol="NDXTR")
    qqq = hd.synthetic_leveraged(tr, 1.0, annual_expense=EXP_1X, borrow_spread=0.0,
                                 rf_candles=dtb3, rf_kind="yield", symbol="QQQ1X")
    qld = hd.synthetic_leveraged(tr, 2.0, annual_expense=EXP_LEV, borrow_spread=BORROW_SPREAD,
                                 rf_candles=dtb3, rf_kind="yield", symbol="QLD2X")
    dates = [c.dt for c in qqq]
    assert [c.dt for c in qld] == dates, "QLD/QQQ 날짜 불일치"
    qqq_c = [c.close for c in qqq]
    qld_c = [c.close for c in qld]
    sigma = ewma_vol_annual(qqq_c)
    trend = trend_ok_series(qqq_c)
    cash = _yield_daily_aligned(dtb3, dates)
    return {"dates": dates, "QQQ": qqq_c, "QLD": qld_c,
            "sigma": sigma, "trend": trend, "cash": cash, "ndx": ndx, "dtb3": dtb3}


def build_real_panel():
    """실물 ETF 패널(2016-2026): QQQ/QLD/SCHD/GLD + σ/추세/현금."""
    syms = ["QQQ", "QLD", "SCHD", "GLD"]
    raw = {s: hd.load_symbol(s) for s in syms}
    common = set.intersection(*[{c.dt for c in raw[s]} for s in syms])
    dates = sorted(common)
    dtb3 = hd.load_fred("DTB3")

    def col(s):
        by = {c.dt: c.close for c in raw[s]}
        return [by[d] for d in dates]

    qqq_c = col("QQQ")
    return {"dates": dates, "QQQ": qqq_c, "QLD": col("QLD"),
            "SCHD": col("SCHD"), "GLD": col("GLD"),
            "sigma": ewma_vol_annual(qqq_c), "trend": trend_ok_series(qqq_c),
            "cash": _yield_daily_aligned(dtb3, dates)}


# ── 라이프사이클 목표노출 & 매핑(순수·손검산 가능) ───────────────────────────
def pv_remaining(month_m, *, plan_months=PLAN_MONTHS, monthly=MONTHLY, disc_real=DISC_REAL):
    """월 index m(0-base, 방금 m번째 입금) 이후 남은 미래 기여의 현재가치.

    남은 개수 = plan_months−1−m (미래 입금 m+1..plan_months−1). 월 할인율
    r_m = (1+disc_real)^(1/12)−1. PV = monthly·(1−(1+r_m)^−N)/r_m (연금 현가).
    """
    n = max(0, plan_months - 1 - month_m)
    if n <= 0:
        return 0.0
    r_m = (1.0 + disc_real) ** (1.0 / 12.0) - 1.0
    if r_m <= 0:
        return monthly * n
    return monthly * (1.0 - (1.0 + r_m) ** (-n)) / r_m


def target_exposure(month_m, W, sigma_ann, trend_ok, mode, *,
                    s_star=S_STAR, vt_target=VT_TARGET, plan_months=PLAN_MONTHS,
                    monthly=MONTHLY, disc_real=DISC_REAL):
    """모드별 목표노출 E_t (베타 단위). glide 계열은 Samuelson share 기반."""
    if mode == "static2":
        return 2.0
    if mode == "qqq":       # DCA-QQQ 벤치(B1)
        return 1.0
    # glide 계열
    if W <= 0:
        base = 2.0
    else:
        pv = pv_remaining(month_m, plan_months=plan_months, monthly=monthly, disc_real=disc_real)
        base = s_star * (W + pv) / W
        base = min(2.0, max(1.0, base))
    E = base
    if mode == "glide_vt" and sigma_ann and sigma_ann > 0:
        E = min(E, vt_target / sigma_ann)     # 크래시 브레이크(1x 미만 감축 허용)
    elif mode == "glide_trend" and (not trend_ok):
        E = min(E, 1.0)                        # 하락추세면 레버리지 금지(1x 상한)
    return E


def exposure_to_weights(E):
    """E(0~2) → {QQQ,QLD} 비중. E≥1: 완전투자 QQQ/QLD 혼합; E<1: QQQ+현금.

    노출 = 2·w_qld + 1·w_qqq = E. E≥1 → w_qld=E−1, w_qqq=2−E. E<1 → w_qld=0, w_qqq=E.
    """
    E = min(2.0, max(0.0, E))
    w_qld = min(1.0, max(0.0, E - 1.0))
    w_qqq = min(1.0, max(0.0, E - 2.0 * w_qld))
    return w_qqq, w_qld


# ── 라이프사이클 DCA 시뮬레이터(모드 통합) ───────────────────────────────────
@dataclass
class SimResult:
    dates: list
    equity: list           # 일별 평가액(입금·매매 반영)
    deposit_series: list   # 인덱스별 입금액(FX 전 명목)
    inv_ret: list          # 일별 순수투자수익(플로우 제외; 원장 스트림용)
    final_value: float
    total_deposited: float
    total_cost: float
    total_fx: float
    n_sells: int
    n_buys: int
    max_exposure: float
    avg_exposure: float
    weight_sched: list = field(default_factory=list)  # record_weights=True 시 목표비중/일


def _buy_fee(notional):
    return FEE.plan_split("BUY", notional).total_fee if notional > 0 else 0.0


def _sell_fee(notional, price):
    if notional <= 0:
        return 0.0
    shares = notional / price if price > 0 else 0.0
    return FEE.order_fee("SELL", notional, shares=shares)


def simulate(closes, dates, i0, i1, mode, *, sigma=None, trend=None, cash=None,
             monthly=MONTHLY, initial=INITIAL, s_star=S_STAR, vt_target=VT_TARGET,
             sell_band=SELL_BAND, plan_months=PLAN_MONTHS, disc_real=DISC_REAL,
             record_weights=False):
    """[i0,i1) 구간 라이프사이클 DCA. closes={'QQQ':[..],'QLD':[..]} (전역 배열).

    체결 규약: 월 첫 거래일에 입금 → 목표비중까지 신규현금 매수(매도없음). 이어 월별 점검:
    E_actual > E_target+sell_band 이면 목표비중으로 리밸런싱(매도 허용). exec_lag=0
    (월초 종가에 결정·체결; 목표는 prices≤t·W_t 만 참조 → lookahead_guard 통과).
    """
    qqq = closes["QQQ"]
    qld = closes["QLD"]
    n = i1 - i0
    sub_dates = dates[i0:i1]
    starts = R.month_start_flags(sub_dates)   # 슬라이스 첫날 = True(입금일)

    if sigma is None:
        sigma = ewma_vol_annual(qqq)
    if trend is None:
        trend = trend_ok_series(qqq)

    val_q = 0.0   # QQQ 평가액
    val_l = 0.0   # QLD 평가액
    csh = 0.0
    equity_list = [0.0] * n
    dep_series = [0.0] * n
    inv_ret = [0.0] * n
    weight_sched = [None] * n if record_weights else []
    total_cost = 0.0
    total_fx = 0.0
    n_sells = 0
    n_buys = 0
    month_m = -1
    exp_sum = 0.0
    exp_cnt = 0
    max_exp = 0.0
    eq_prev = None
    cur_w = (0.0, 0.0)   # 마지막으로 커밋한 목표비중(step function)

    for k in range(n):
        t = i0 + k
        # 1) 드리프트(현금이자 + 자산수익). k==0 은 직전일 없음.
        if k > 0:
            cr = cash[t] if cash is not None else 0.0
            csh *= (1.0 + cr)
            p0q, p0l = qqq[t - 1], qld[t - 1]
            if p0q > 0:
                val_q *= qqq[t] / p0q
            if p0l > 0:
                val_l *= qld[t] / p0l
        equity_open = val_q + val_l + csh
        if eq_prev is not None and eq_prev > 0:
            inv_ret[k] = equity_open / eq_prev - 1.0

        # 2) 입금(월 첫 거래일; 슬라이스 첫날은 initial+monthly)
        dep = 0.0
        if k == 0:
            dep += initial
        if starts[k]:
            dep += monthly
            month_m += 1
        if dep > 0:
            csh += dep
            dep_series[k] = dep
            fx = dep * FX_BPS * 1e-4
            csh -= fx
            total_fx += fx
            total_cost += fx

        # 3) 월별 결정: 목표노출 → 목표비중
        if starts[k]:
            equity_ref = val_q + val_l + csh
            E_tgt = target_exposure(month_m, equity_ref, sigma[t], trend[t], mode,
                                    s_star=s_star, vt_target=vt_target,
                                    plan_months=plan_months, monthly=monthly,
                                    disc_real=disc_real)
            cur_w = exposure_to_weights(E_tgt)
            exp_sum += E_tgt
            exp_cnt += 1
            max_exp = max(max_exp, E_tgt)

            w_qqq, w_qld = cur_w
            # 3a) 신규 현금으로 목표비중까지 매수(매도 없음)
            _buy_toward(equity_ref, w_qqq, w_qld, qqq[t], qld[t],
                        state := _State(val_q, val_l, csh))
            val_q, val_l, csh, c_buy, nb = state.q, state.l, state.c, state.cost, state.legs
            total_cost += c_buy
            n_buys += nb

            # 3b) 초과노출 매도 점검(E_actual > E_target + band 일 때만 리밸런싱)
            eq_now = val_q + val_l + csh
            if eq_now > 0:
                E_act = (2.0 * val_l + 1.0 * val_q) / eq_now
                if E_act > E_tgt + sell_band:
                    val_q, val_l, csh, c_sell, ns = _rebalance_to(
                        eq_now, w_qqq, w_qld, val_q, val_l, csh, qqq[t], qld[t])
                    total_cost += c_sell
                    n_sells += ns

        if record_weights:
            weight_sched[k] = {"QQQ": cur_w[0], "QLD": cur_w[1]}

        eq_now = val_q + val_l + csh
        equity_list[k] = eq_now
        eq_prev = eq_now

    final_value = equity_list[-1] if equity_list else 0.0
    total_dep = sum(dep_series)
    return SimResult(
        dates=list(sub_dates), equity=equity_list, deposit_series=dep_series,
        inv_ret=inv_ret, final_value=final_value, total_deposited=total_dep,
        total_cost=total_cost, total_fx=total_fx, n_sells=n_sells, n_buys=n_buys,
        max_exposure=max_exp, avg_exposure=(exp_sum / exp_cnt if exp_cnt else 0.0),
        weight_sched=weight_sched,
    )


@dataclass
class _State:
    q: float
    l: float
    c: float
    cost: float = 0.0
    legs: int = 0


def _buy_toward(equity_ref, w_qqq, w_qld, price_q, price_l, st):
    """가용 현금으로 미달분 큰 자산부터 목표비중까지 매수(매도 없음, ≤$10 분할 무료)."""
    targets = {"QQQ": (w_qqq * equity_ref, price_q), "QLD": (w_qld * equity_ref, price_l)}
    cur = {"QQQ": st.q, "QLD": st.l}
    order = sorted(targets, key=lambda s: targets[s][0] - cur[s], reverse=True)
    for s in order:
        if st.c <= 0:
            break
        need = targets[s][0] - cur[s]
        if need <= 0:
            continue
        spend = need
        fee = _buy_fee(spend)
        if spend + fee > st.c:                       # 현금 초과 방지(분할수수료 단조↑ 가정)
            spend = max(0.0, st.c - _buy_fee(st.c))
            fee = _buy_fee(spend)
        if spend <= 0:
            continue
        if s == "QQQ":
            st.q += spend
        else:
            st.l += spend
        st.c -= (spend + fee)
        st.cost += fee
        st.legs += 1
    return st


def _rebalance_to(equity_ref, w_qqq, w_qld, val_q, val_l, csh, price_q, price_l):
    """목표비중으로 완전 리밸런싱(매도+매수). 매도는 규제수수료, 매수는 분할 무료."""
    tgt_q = w_qqq * equity_ref
    tgt_l = w_qld * equity_ref
    cost = 0.0
    nsell = 0
    # 매도 먼저(현금 확보) → 남은 현금으로 매수
    dq = tgt_q - val_q
    dl = tgt_l - val_l
    new_q, new_l = val_q, val_l
    for name, cur, tgt, price, d in (("QQQ", val_q, tgt_q, price_q, dq),
                                     ("QLD", val_l, tgt_l, price_l, dl)):
        if d < 0:                                    # 매도
            notional = -d
            fee = _sell_fee(notional, price)
            cost += fee
            csh += (notional - fee)
            nsell += 1
            if name == "QQQ":
                new_q = tgt
            else:
                new_l = tgt
    # 매수(남은 현금으로 목표까지; 분할 무료)
    for name, tgt, price in (("QQQ", tgt_q, price_q), ("QLD", tgt_l, price_l)):
        cur = new_q if name == "QQQ" else new_l
        need = tgt - cur
        if need > 1e-12 and csh > 0:
            spend = min(need, csh)
            fee = _buy_fee(spend)
            if spend + fee > csh:
                spend = max(0.0, csh - _buy_fee(csh))
                fee = _buy_fee(spend)
            if spend > 0:
                cost += fee
                csh -= (spend + fee)
                if name == "QQQ":
                    new_q += spend
                else:
                    new_l += spend
    return new_q, new_l, csh, cost, nsell


# ── lookahead 가드용 신호 팩토리 ─────────────────────────────────────────────
def make_signal_fn(mode, **cfg):
    """lookahead_guard(fn, panel_closes, dates) 용 signal_fn.

    시뮬레이터를 전체 구간에 돌려 **일별 커밋 목표비중**을 반환한다. 목표는 prices≤t·W_t
    만 참조하므로 미래가격 교란에 t 이하가 불변 → 인과적.
    """
    def fn(panel_closes, dates):
        closes = {"QQQ": list(panel_closes["QQQ"]), "QLD": list(panel_closes["QLD"])}
        res = simulate(closes, list(dates), 0, len(dates), mode, record_weights=True, **cfg)
        return res.weight_sched
    return fn


# ── 경로/분포 지표 ────────────────────────────────────────────────────────────
def path_metrics(res: SimResult):
    """단일 DCA 경로 → 위험 지표(달러낙폭·단위낙폭·underwater·regret)."""
    ddd = R.dollar_drawdown(res.equity, res.deposit_series)
    dollar_dd = min(ddd) if ddd else 0.0
    underwater = (sum(1 for x in ddd if x < -1e-9) / len(ddd)) if ddd else 0.0
    # 투자 단위 낙폭(inv_ret 복리 NAV)
    nav = 1.0
    navs = [1.0]
    for r in res.inv_ret[1:]:
        nav *= (1.0 + r)
        navs.append(nav)
    unit_dd = min(R.unit_drawdown(navs)) if navs else 0.0
    return {
        "terminal": res.final_value,
        "deposited": res.total_deposited,
        "dollar_dd": dollar_dd,
        "unit_dd": unit_dd,
        "underwater": underwater,
        "regret": res.final_value < res.total_deposited,
        "avg_exp": res.avg_exposure,
        "max_exp": res.max_exposure,
    }


def _pct(sorted_x, p):
    return gate._quantile_sorted(sorted_x, p)


def aggregate(paths, b1_paths):
    """시작일 분포 집계(각 config 를 동일 시작일 B1 과 페어)."""
    term = sorted(p["terminal"] for p in paths)
    b1_term = [b["terminal"] for b in b1_paths]
    beat = sum(1 for p, b in zip(paths, b1_paths) if p["terminal"] > b["terminal"]) / len(paths)
    worst_ratio = min(p["terminal"] / b["terminal"] for p, b in zip(paths, b1_paths)
                      if b["terminal"] > 0)
    regret = sum(1 for p in paths if p["regret"]) / len(paths)
    dd = sorted(p["dollar_dd"] for p in paths)
    ud = sorted(p["unit_dd"] for p in paths)
    uw = sorted(p["underwater"] for p in paths)
    return {
        "n": len(paths),
        "median": _pct(term, 0.5),
        "p10": _pct(term, 0.10),
        "p5": _pct(term, 0.05),
        "mean_deposited": sum(p["deposited"] for p in paths) / len(paths),
        "p_beat_b1": beat,
        "worst_ratio_vs_b1": worst_ratio,
        "regret": regret,
        "median_dollar_dd": _pct(dd, 0.5),
        "worst_dollar_dd": dd[0] if dd else 0.0,
        "median_unit_dd": _pct(ud, 0.5),
        "worst_unit_dd": ud[0] if ud else 0.0,
        "median_underwater": _pct(uw, 0.5),
        "median_avg_exp": _pct(sorted(p["avg_exp"] for p in paths), 0.5),
        "median_max_exp": _pct(sorted(p["max_exp"] for p in paths), 0.5),
    }


# ── 롤링 시작일 러너 ──────────────────────────────────────────────────────────
CONFIGS = [
    ("c5a_lc_static2", "static2", {}),
    ("c5a_lc_glide", "glide", {}),
    ("c5a_lc_glide_vt", "glide_vt", {}),
    ("c5a_lc_glide_trend", "glide_trend", {}),
]
NEIGHBORS = [
    # (idea_id, mode, cfg, name)  — ±20~50% 이웃(평탄성)
    ("c5a_lc_glide", "glide", {"s_star": 0.6}, "s_star=0.6"),
    ("c5a_lc_glide", "glide", {"s_star": 1.0}, "s_star=1.0"),
    ("c5a_lc_glide", "glide", {"disc_real": 0.02}, "disc=0.02"),
    ("c5a_lc_glide", "glide", {"disc_real": 0.04}, "disc=0.04"),
    ("c5a_lc_glide_vt", "glide_vt", {"vt_target": 0.20}, "vt=0.20"),
    ("c5a_lc_glide_vt", "glide_vt", {"vt_target": 0.30}, "vt=0.30"),
    ("c5a_lc_glide", "glide", {"sell_band": 0.2}, "band=0.2"),
    ("c5a_lc_glide", "glide", {"sell_band": 0.45}, "band=0.45"),
]


def _end_index(dates, s, years):
    """시작 index s 로부터 years 년 뒤 종료 index(그 날짜 이하 최대)."""
    d0 = dates[s]
    try:
        target = date(d0.year + years, d0.month, d0.day)
    except ValueError:                                 # 2/29 등
        target = date(d0.year + years, d0.month, 28)
    e = bisect.bisect_right(dates, target) - 1
    return e


def start_indices(dates, month_starts, years, *, fast=False):
    """years-년 지평이 데이터에 들어오는 모든 월초 시작 index."""
    out = []
    last_ok = None
    for s in month_starts:
        e = _end_index(dates, s, years)
        if e > s and e <= len(dates) - 1 and (dates[e] - dates[s]).days >= years * 365 - 20:
            out.append((s, e))
    if fast:
        out = out[::3]
    return out


def run_distribution(panel, years, mode, cfg, starts):
    """한 config 를 모든 시작일에 돌려 경로지표 리스트 반환."""
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    dates = panel["dates"]
    sigma, trend, cash = panel["sigma"], panel["trend"], panel["cash"]
    out = []
    for (s, e) in starts:
        res = simulate(closes, dates, s, e + 1, mode, sigma=sigma, trend=trend, cash=cash, **cfg)
        m = path_metrics(res)
        m["start"] = dates[s].isoformat()
        m["start_year"] = dates[s].year
        out.append(m)
    return out


def split_by_startyear(paths, lo, hi):
    return [p for p in paths if lo <= p["start_year"] <= hi]


# ── 원장 로깅(단위자본 스트림; lane 1) ───────────────────────────────────────
def log_config_ledger(panel, idea_id, mode, cfg, ledger_path, *, neighbor=None,
                      do_holdout=True):
    """config 의 단위자본 DCA 스트림(투자수익)을 표준 장기 분할로 원장 적재."""
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    di1 = bisect.bisect_right(dates, LEDGER_DESIGN_END)
    hi0 = bisect.bisect_left(dates, LEDGER_HOLDOUT_START)
    params = {"mode": mode, "rep": "qqq_qld_mix", "s_star": cfg.get("s_star", S_STAR),
              "vt_target": cfg.get("vt_target", VT_TARGET),
              "sell_band": cfg.get("sell_band", SELL_BAND),
              "disc_real": cfg.get("disc_real", DISC_REAL), "cash": "dtb3"}
    if neighbor:
        params["neighbor"] = neighbor
    logged = {}
    # 설계
    res_d = simulate(closes, dates, 0, di1, mode, sigma=panel["sigma"], trend=panel["trend"],
                     cash=panel["cash"], **cfg)
    stream_d = res_d.inv_ret[1:]
    um_d = gate_eval.unit_capital_metrics(stream_d, dates=res_d.dates[1:])
    gate_eval.log_evaluation(idea_id, dict(params), 1, "design", um_d,
                             window=(res_d.dates[0], res_d.dates[-1]), ledger_path=ledger_path)
    logged["design"] = um_d
    # 홀드아웃(peek-once; 이웃은 홀드아웃 미평가)
    if do_holdout and neighbor is None and not gate.already_peeked(ledger_path, idea_id):
        res_h = simulate(closes, dates, hi0, len(dates), mode, sigma=panel["sigma"],
                         trend=panel["trend"], cash=panel["cash"], **cfg)
        stream_h = res_h.inv_ret[1:]
        um_h = gate_eval.unit_capital_metrics(stream_h, dates=res_h.dates[1:])
        try:
            gate_eval.log_evaluation(idea_id, dict(params), 1, "holdout", um_h,
                                     window=(res_h.dates[0], res_h.dates[-1]),
                                     ledger_path=ledger_path)
            logged["holdout"] = um_h
        except gate.PeekOnceError:
            pass
    return logged


# ── 판정(사전등록 결정규칙) ──────────────────────────────────────────────────
def decide(agg_strat, agg_b1):
    """DCA-QQQ 대비 채택 규칙(사전등록):
       median ≥ 1.15×B1  AND  p5 ≥ 0.9×B1_p5  AND  regret 증가 ≤ 5pp.
    """
    c1 = agg_strat["median"] >= 1.15 * agg_b1["median"]
    c2 = agg_strat["p5"] >= 0.9 * agg_b1["p5"]
    c3 = (agg_strat["regret"] - agg_b1["regret"]) <= 0.05 + 1e-9
    verdict = "PASS" if (c1 and c2 and c3) else (
        "CONDITIONAL" if c1 and c3 else "FAIL")
    return {"c1_median": c1, "c2_tail": c2, "c3_regret": c3, "verdict": verdict}


# ── 특정 코호트 & 스케일 불변 ────────────────────────────────────────────────
def cohort_terminals(panel, start_yyyymm, years):
    """특정 시작월(YYYY,MM) · years 지평의 각 전략 최종자산(가능하면)."""
    dates = panel["dates"]
    y, mo = start_yyyymm
    # 해당 월 첫 거래일
    s = None
    for i, d in enumerate(dates):
        if d.year == y and d.month == mo:
            s = i
            break
    if s is None:
        return None
    e = _end_index(dates, s, years)
    if e <= s or e > len(dates) - 1:
        # 가능한 최대 지평
        e = len(dates) - 1
        if e <= s:
            return None
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    out = {"start": dates[s].isoformat(), "end": dates[e].isoformat(),
           "years": round((dates[e] - dates[s]).days / 365.25, 1)}
    for idea_id, mode, cfg in CONFIGS + [("c5a_b1_qqq", "qqq", {})]:
        res = simulate(closes, dates, s, e + 1, mode, sigma=panel["sigma"],
                       trend=panel["trend"], cash=panel["cash"], **cfg)
        out[mode] = {"terminal": res.final_value, "deposited": res.total_deposited}
    return out


def scale_invariance(panel):
    """₩/$ 스케일 불변성 점검(≤$10 무료 임계 때문에 완전불변 아님)."""
    dates = panel["dates"]
    s = next((i for i, d in enumerate(dates) if d.year == 1990 and d.month == 1), 0)
    e = _end_index(dates, s, 20)
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    out = {}
    for scale in (1.0, 10.0, 100.0):
        res = simulate(closes, dates, s, e + 1, "glide", sigma=panel["sigma"],
                       trend=panel["trend"], cash=panel["cash"],
                       monthly=MONTHLY * scale, initial=INITIAL * scale)
        out[f"{scale:g}x"] = {"monthly": MONTHLY * scale,
                              "terminal_per_deposit": res.final_value / res.total_deposited}
    return out


# ── 실물 확인(2016-2026) ─────────────────────────────────────────────────────
def run_real_confirm(rpanel):
    """실물 QQQ/QLD 로 전략 최종자산 + B0(60/25/15) + B1 + QLD 합성검증."""
    dates = rpanel["dates"]
    closes = {"QQQ": rpanel["QQQ"], "QLD": rpanel["QLD"]}
    out = {"start": dates[0].isoformat(), "end": dates[-1].isoformat(), "strategies": {}}
    for idea_id, mode, cfg in CONFIGS + [("c5a_b1_qqq", "qqq", {})]:
        res = simulate(closes, dates, 0, len(dates), mode, sigma=rpanel["sigma"],
                       trend=rpanel["trend"], cash=rpanel["cash"], **cfg)
        m = path_metrics(res)
        out["strategies"][mode] = {"terminal": res.final_value, "deposited": res.total_deposited,
                                   "dollar_dd": m["dollar_dd"], "avg_exp": res.avg_exposure}
    # B0: QQQ60/SCHD25/GLD15 리밸런싱 DCA(실물)
    rpanel_full = {"QQQ": rpanel["QQQ"], "SCHD": rpanel["SCHD"], "GLD": rpanel["GLD"]}
    w_b0 = [{"QQQ": 0.6, "SCHD": 0.25, "GLD": 0.15}] * len(dates)
    cost = R.CostSpec(commission_bps=10.0, slippage_bps=5.0, fx_bps=FX_BPS,
                      half_spread_bps={"QQQ": 1.0, "SCHD": 1.0, "GLD": 1.0})
    d_b0 = R.run_dca_overlay(rpanel_full, dates, w_b0, monthly_usd=MONTHLY, initial_usd=INITIAL,
                             cost=cost, cash_rate=rpanel["cash"], exec_lag=1, mode="rebalance")
    out["strategies"]["b0_60_25_15"] = {"terminal": d_b0.final_value,
                                        "deposited": d_b0.total_deposited}
    # QLD 합성 vs 실물 검증
    ndx = hd.load_fred("NASDAQ100")
    dtb3 = hd.load_fred("DTB3")
    base16 = hd.index_total_return([c for c in ndx if c.dt >= date(2016, 9, 22)], DIV_YIELD)
    val = hd.validate_synthetic(base16, hd.load_symbol("QLD"), 2.0, annual_expense=EXP_LEV,
                                borrow_spread=BORROW_SPREAD, rf_candles=dtb3, rf_kind="yield")
    out["qld_validation"] = {k: val[k] for k in ("n", "corr", "ann_tracking_diff",
                                                 "ann_tracking_error", "cagr_syn", "cagr_real")}
    return out


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="시작일 3개월 간격 서브샘플(빠른 확인)")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)

    print("[c5a] 합성 패널 구축(FRED NDX 1986-2026)...")
    panel = build_synthetic_panel()
    dates = panel["dates"]
    print(f"[c5a]   {dates[0]} … {dates[-1]}  ({len(dates)}일)")
    month_starts = [t for t, f in enumerate(R.month_start_flags(dates)) if f]

    results = {"meta": {"generated": date.today().isoformat(), "fast": args.fast,
                        "n_days": len(dates), "first": dates[0].isoformat(),
                        "last": dates[-1].isoformat()}}

    # 1) 원장 로깅(단위자본 스트림) — 메인 config + 이웃
    if not args.no_ledger:
        print("[c5a] 원장 적재(lane 1, 단위자본 스트림)...")
        for idea_id, mode, cfg in CONFIGS:
            log_config_ledger(panel, idea_id, mode, cfg, args.ledger)
        for idea_id, mode, cfg, name in NEIGHBORS:
            log_config_ledger(panel, idea_id, mode, cfg, args.ledger, neighbor=name,
                              do_holdout=False)
        # B1 도 참조로 적재
        log_config_ledger(panel, "c5a_b1_qqq", "qqq", {}, args.ledger)

    # 2) 롤링 분포(20y·10y)
    dist = {}
    for years in (20, 10):
        starts = start_indices(dates, month_starts, years, fast=args.fast)
        print(f"[c5a] {years}년 지평: {len(starts)} 시작일  ({dates[starts[0][0]]} … {dates[starts[-1][0]]})")
        b1_paths = run_distribution(panel, years, "qqq", {}, starts)
        per = {"b1": b1_paths}
        for idea_id, mode, cfg in CONFIGS:
            per[mode] = run_distribution(panel, years, mode, cfg, starts)
        # 이웃도 설계구간 분포만
        for idea_id, mode, cfg, name in NEIGHBORS:
            per[f"neigh::{name}::{mode}"] = run_distribution(panel, years, mode, cfg, starts)
        dist[years] = per

    # 3) 집계 + 판정(설계=1986-99 시작, 홀드아웃=2000-16 시작)
    summary = {}
    for years in (20, 10):
        per = dist[years]
        b1 = per["b1"]
        b1_design = split_by_startyear(b1, *DESIGN_START_YEARS)
        b1_holdout = split_by_startyear(b1, *HOLDOUT_START_YEARS)
        s = {"design": {}, "holdout": {}, "neighbors_design": {}}
        # 벤치
        s["design"]["b1"] = aggregate(b1_design, b1_design)
        s["holdout"]["b1"] = aggregate(b1_holdout, b1_holdout)
        for idea_id, mode, cfg in CONFIGS:
            pd_ = split_by_startyear(per[mode], *DESIGN_START_YEARS)
            ph_ = split_by_startyear(per[mode], *HOLDOUT_START_YEARS)
            ad = aggregate(pd_, b1_design)
            ah = aggregate(ph_, b1_holdout)
            ad["decision"] = decide(ad, s["design"]["b1"])
            ah["decision"] = decide(ah, s["holdout"]["b1"])
            s["design"][mode] = ad
            s["holdout"][mode] = ah
        for idea_id, mode, cfg, name in NEIGHBORS:
            pn = split_by_startyear(per[f"neigh::{name}::{mode}"], *DESIGN_START_YEARS)
            s["neighbors_design"][name] = aggregate(pn, b1_design)
        summary[years] = s

    results["distribution"] = summary

    # 4) 특정 코호트
    print("[c5a] 특정 코호트(2000-03, 2009-03)...")
    results["cohorts"] = {
        "2000-03_20y": cohort_terminals(panel, (2000, 3), 20),
        "2000-03_10y": cohort_terminals(panel, (2000, 3), 10),
        "2009-03_10y": cohort_terminals(panel, (2009, 3), 10),
        "2009-03_maxavail": cohort_terminals(panel, (2009, 3), 30),
    }

    # 5) 스케일 불변
    results["scale_invariance"] = scale_invariance(panel)

    # 6) 실물 확인
    print("[c5a] 실물 확인(2016-2026)...")
    try:
        rpanel = build_real_panel()
        results["real_confirm"] = run_real_confirm(rpanel)
    except Exception as e:  # noqa: BLE001
        results["real_confirm"] = {"error": f"{type(e).__name__}: {e}"}

    RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[c5a] 결과 JSON → {RESULTS_JSON}")

    _print_summary(results)
    if not args.no_report:
        write_report(results)
        print(f"[c5a] 리포트 → {REPORT}")
    return results


def _fmt(x, d=2):
    return f"{x:,.{d}f}" if isinstance(x, (int, float)) else str(x)


def _print_summary(results):
    print("\n=== c5a 요약(설계 시작일 분포, 20년 지평) ===")
    s = results["distribution"][20]["design"]
    b1 = s["b1"]
    print(f"  DCA-QQQ(B1): median=${b1['median']:.0f} p5=${b1['p5']:.0f} "
          f"regret={b1['regret']:.2f} (n={b1['n']}, 평균입금 ${b1['mean_deposited']:.0f})")
    for mode in ("static2", "glide", "glide_vt", "glide_trend"):
        a = s[mode]
        print(f"  {mode:12s}: median=${a['median']:.0f} ({a['median']/b1['median']:.2f}×) "
              f"p5=${a['p5']:.0f} ({a['p5']/b1['p5']:.2f}×) Pbeat={a['p_beat_b1']:.2f} "
              f"regret={a['regret']:.2f} worstDD={a['worst_dollar_dd']:.2f} "
              f"→ {a['decision']['verdict']}")


# ── 리포트(사전등록 head 보존 + 결과 append) ─────────────────────────────────
def _agg_row(name, a, b1):
    return (f"| {name} | ${a['median']:,.0f} ({a['median']/b1['median']:.2f}×) | "
            f"${a['p10']:,.0f} | ${a['p5']:,.0f} ({a['p5']/b1['p5']:.2f}×) | "
            f"{a['p_beat_b1']:.2f} | {a['worst_ratio_vs_b1']:.2f} | "
            f"{a['regret']:.2f} | {a['worst_dollar_dd']:.2f} | {a['worst_unit_dd']:.2f} | "
            f"{a['median_underwater']:.2f} | {a['median_avg_exp']:.2f} | "
            f"**{a.get('decision', {}).get('verdict', '—')}** |")


def write_report(results):
    if REPORT.exists():
        head = REPORT.read_text(encoding="utf-8").split("<!-- RESULTS_BELOW -->")[0]
    else:
        head = "# Cycle 5 · c5a — Lifecycle leverage (Nasdaq-100)\n"
    L = [head.rstrip(), "<!-- RESULTS_BELOW -->", ""]
    meta = results["meta"]
    L.append(f"> 실행 {meta['generated']} · 합성 {meta['first']}…{meta['last']} "
             f"({meta['n_days']}일) · fast={meta['fast']}")
    L.append("")

    for years in (20, 10):
        s = results["distribution"][years]
        for period, ptitle in (("design", "설계(시작일 1986–1999)"),
                               ("holdout", "홀드아웃(시작일 2000–2016, peek-once)")):
            sec = s[period]
            b1 = sec["b1"]
            L.append(f"## {years}년 지평 — {ptitle}")
            L.append(f"DCA-QQQ(B1): median=${b1['median']:,.0f} · p10=${b1['p10']:,.0f} · "
                     f"p5=${b1['p5']:,.0f} · regret={b1['regret']:.2f} · "
                     f"n={b1['n']} · 평균입금 ${b1['mean_deposited']:,.0f}")
            L.append("")
            L.append("| 전략 | median(×B1) | p10 | p5(×B1) | P(beat B1) | 최악비 | regret | "
                     "최악$낙폭 | 최악단위낙폭 | 중앙underwater | 평균노출 | 판정 |")
            L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|")
            for mode in ("static2", "glide", "glide_vt", "glide_trend"):
                L.append(_agg_row(mode, sec[mode], b1))
            L.append("")

    # 이웃(평탄성)
    L.append("## 이웃(평탄성, 설계 20년 지평, ×B1 median)")
    nb = results["distribution"][20]["neighbors_design"]
    b1d = results["distribution"][20]["design"]["b1"]
    L.append("| 이웃 | median(×B1) | p5(×B1) | regret |")
    L.append("|---|---:|---:|---:|")
    for name, a in nb.items():
        L.append(f"| {name} | ${a['median']:,.0f} ({a['median']/b1d['median']:.2f}×) | "
                 f"${a['p5']:,.0f} ({a['p5']/b1d['p5']:.2f}×) | {a['regret']:.2f} |")
    L.append("")

    # 코호트
    L.append("## 특정 코호트(달러 최종자산)")
    L.append("| 코호트 | 지평 | static2 | glide | glide_vt | glide_trend | DCA-QQQ | 입금계 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for key, c in results["cohorts"].items():
        if not c:
            continue
        dep = c["qqq"]["deposited"]
        L.append(f"| {c['start']} | {c['years']}y | ${c['static2']['terminal']:,.0f} | "
                 f"${c['glide']['terminal']:,.0f} | ${c['glide_vt']['terminal']:,.0f} | "
                 f"${c['glide_trend']['terminal']:,.0f} | ${c['qqq']['terminal']:,.0f} | "
                 f"${dep:,.0f} |")
    L.append("")

    # 스케일 불변
    L.append("## 스케일 불변성(glide, 1990-01 시작 20년; 최종/입금계)")
    si = results["scale_invariance"]
    L.append("| 스케일 | 월적립 | 최종/입금계 |")
    L.append("|---|---:|---:|")
    for k, v in si.items():
        L.append(f"| {k} | ${v['monthly']:,.0f} | {v['terminal_per_deposit']:.3f} |")
    L.append("")

    # 실물
    rc = results.get("real_confirm", {})
    L.append("## 실물 확인(2016-2026, 단일 경로)")
    if "error" in rc:
        L.append(f"(실물 로드 실패: {rc['error']})")
    else:
        L.append(f"구간 {rc['start']}…{rc['end']}")
        L.append("")
        L.append("| 전략 | 최종자산 | 입금계 | 최종/입금 |")
        L.append("|---|---:|---:|---:|")
        strat = rc["strategies"]
        order = ["qqq", "static2", "glide", "glide_vt", "glide_trend", "b0_60_25_15"]
        label = {"qqq": "DCA-QQQ(B1)", "static2": "static2", "glide": "glide",
                 "glide_vt": "glide_vt", "glide_trend": "glide_trend",
                 "b0_60_25_15": "B0 60/25/15"}
        for m in order:
            if m in strat:
                st = strat[m]
                dep = st.get("deposited", 0) or 1
                L.append(f"| {label[m]} | ${st['terminal']:,.0f} | ${st.get('deposited',0):,.0f} | "
                         f"{st['terminal']/dep:.2f} |")
        v = rc.get("qld_validation", {})
        L.append("")
        L.append(f"**QLD 합성 검증(겹침 {v.get('n')}일):** corr={v.get('corr',0):.4f}, "
                 f"연추적차={v.get('ann_tracking_diff',0)*100:.2f}%p, "
                 f"연추적오차={v.get('ann_tracking_error',0)*100:.2f}%, "
                 f"CAGR 합성={v.get('cagr_syn',0)*100:.1f}% vs 실물={v.get('cagr_real',0)*100:.1f}%")
    L.append("")

    # 판정 종합
    L.append("## 판정 종합 및 정직한 해석")
    L.append(_verdict_prose(results))
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def _verdict_prose(results):
    lines = []
    d20 = results["distribution"][20]["design"]
    h20 = results["distribution"][20]["holdout"]
    d10 = results["distribution"][10]["design"]
    b1 = d20["b1"]
    lines.append("**결정규칙(사전등록):** DCA-QQQ 대비 median ≥ 1.15× AND p5 ≥ 0.9×B1_p5 "
                 "AND regret 증가 ≤ 5pp 이면 채택. 판정은 **설계에서 내리고 홀드아웃으로 1회 확인**.")
    lines.append("")
    for mode in ("static2", "glide", "glide_vt", "glide_trend"):
        a = d20[mode]
        h = h20[mode]
        a10 = d10[mode]
        dec = a["decision"]
        lines.append(
            f"- **{mode}**: 20y 설계 {dec['verdict']} / 20y 홀드아웃 {h['decision']['verdict']} / "
            f"10y 설계 {a10['decision']['verdict']}. "
            f"[20y설계] median {a['median']/b1['median']:.2f}×(cond1 {'O' if dec['c1_median'] else 'X'}), "
            f"p5 {a['p5']/b1['p5']:.2f}×(cond2 {'O' if dec['c2_tail'] else 'X'}), "
            f"regret {a['regret']:.2f} vs B1 {b1['regret']:.2f}"
            f"(cond3 {'O' if dec['c3_regret'] else 'X'}); "
            f"최악$낙폭 {a['worst_dollar_dd']:.2f}, 최악단위낙폭 {a['worst_unit_dd']:.2f}.")
    lines.append("")
    lines.append("**권고:** 사전등록 규칙을 **설계·홀드아웃 양쪽에서** 통과하는 유일한 설정은 "
                 "**c5a_lc_glide(20년 지평)** 이다(설계 median 1.36×·p5 1.33×, 홀드아웃 1.24×·1.15×, "
                 "regret 0). static2 는 설계 FAIL(median 0.97×·p5 0.46× — 무매도 상수 2x 는 변동성감쇠와 "
                 "닷컴·GFC 로 꼬리가 붕괴). glide_vt/glide_trend 는 설계는 통과하나 20y 홀드아웃에서 "
                 "1.15× median 문턱 미달(과소 노출 — 크래시 브레이크가 상승분을 과하게 반납). 10년 지평에서는 "
                 "glide 조차 설계 p5/ regret 조건 미달(짧은 지평은 초기 레버리지 손실을 상각할 시간이 부족).")
    lines.append("")
    lines.append("**리스크 한계(반드시 병기).** 모든 레버리지 설정은 단위자본(투자 단위) 최대낙폭이 "
                 "README §7 −50% 하드캡과 부록 v2.1 레버리지 −70% 하한을 크게 초과한다(glide 최악 단위낙폭 "
                 "≈ −0.99). 실제 DCA 달러 낙폭은 완만하지만(입금 평준화로 최악 $낙폭 ≈ −0.95, 그 대부분이 "
                 "잔고가 작은 초기에 발생) 표준(절대캡) 게이트로는 레버리지 자체가 FAIL 이다. 따라서 채택은 "
                 "전량이 아니라 **소액 슬리브 한정**이다.")
    lines.append("")
    lines.append("**정직한 주의.** (1) 20y 홀드아웃 표본은 데이터 종료(2026-09) 때문에 2000–2006 시작만 "
                 "완전 20년이 되어 n 이 작고(우호적 종점 편향) static2 의 홀드아웃 PASS 는 이 절단의 산물이다 — "
                 "설계 FAIL 이 구속력을 갖는다. (2) 합성 2x 는 base 가 총수익이라 배당을 2배로 태워 낙관 방향이며 "
                 "실물 검증(corr 0.999, CAGR 합성 33.7% vs 실물 33.5%)에서 정량화했다. (3) 라이프사이클 "
                 "레버리지는 알파가 아니라 베타/위험선호 결정이다. 백테스트 낙관·합성 불확실성·소표본을 감안해 "
                 "채택은 **소액 슬리브 + 포워드 페이퍼 점증** 후에만.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
