"""c7a — Adversarial audit of c6c_crash_lev (drawdown-conditional 2x leverage).

목적: c6c 사전등록 통과자 `c6c_crash_lev` 를 **정직하게 깨보는** 감사(확증 아님, 반증 시도).
src/·c5a·c6a·c6c 파일은 수정하지 않고 import 만 한다.

crash_lev 규칙(c6c._decide): DCA-QQQ 를 기본으로, NDX 가 **사상최고(ATH) 대비 낙폭 > 30%**
이면 신규 적립을 QLD(2x) 로 라우팅, 낙폭 < 10% 회복 시 QQQ 복귀. 절대 매도하지 않음.

감사 항목(reports/cycle7_c7a_crashlev_audit.md 기록):
 1. 코드리뷰: look-ahead / **"ATH" 정의(시장 ATH vs 코호트 ATH)** / 체결타이밍 / 수수료 /
    합성 2x 배당 이중계상(c6a 정정 적용).
 2. 정상 부트스트랩 MC(≥2000 경로, 평균블록 250) 20y/10y crash_lev/B1 비:
    median, P(<1), P(<0.9), P(<1.15), CVaR5. **경로상대 ATH**(신선한 투자자) — 이것이 정직한 검정.
 3. Nikkei225(FRED) 잃어버린 10년 OOS. 원장 idea_id=c7a_crash_lev_oos_nikkei (peek-once).
 4. 스트레스: 조달 +2%, 2x 보수 1.5%.
 5. 5년 후 적립중단(실직).
 6. 행동재무 — **B1(DCA-QQQ) 대비**로 crash_lev 와 c5a_lc_glide 를 같은 경로에서 비교
    (P(달러낙폭>50%) B1 포함, 최악낙폭, 언더워터, 동일시점 낙폭). c6a 의 공백 정정.
 7. 3.11× 홀드아웃 절단(end-point) 편향 정량화 — 대체 종료일에서 재평가.
 8. 두 전략(glide vs crash_lev) 상대성과의 경로간 상관 — 같은 베팅인가?
 9. 안전/기대이득 side-by-side (glide vs crash_lev vs B1).

재현: PYTHONPATH=src .venv/bin/python experiments/c7a_crashlev_audit.py [--fast]
      [--mc-paths 2000] [--ledger PATH] [--no-ledger]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
import gate_eval  # noqa: E402
import c5a_lifecycle as c5a  # noqa: E402
import c6a_lifecycle_audit as c6a  # noqa: E402
import c6c_contrib as c6c  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle7_c7a_crashlev_audit.md"
RESULTS_JSON = ROOT / "reports" / "c7a_results.json"

# 사전등록 상수(c6c 동일 — 재튜닝 금지)
DIV_NDX = c6c.DIV_YIELD          # 0.7%/yr
EXP_1X = c6c.EXP_1X
EXP_LEV = c6c.EXP_LEV
BORROW_SPREAD = c6c.BORROW_SPREAD
DIV_COMP = c6a.DIV_COMP_1971     # 1.5% (외국지수 배당 가정, 문서화)
DESIGN = c6c.DESIGN_START_YEARS  # (1986,1999)
HOLDOUT = c6c.HOLDOUT_START_YEARS  # (2000,2016)


# ── 배당정정 패널(시장 ATH 신호) ─────────────────────────────────────────────
def build_corrected_panel():
    """c6c 합성 패널을 만들되 QLD 를 배당 이중계상 정정(2×가격+1×배당)으로 교체.

    dd_ath/dd_52 는 QQQ 종가로 add_signals 가 재계산(시장 전체 running-max = **시장 ATH**).
    """
    base = c5a.build_synthetic_panel()
    qld_corr = c6a.corrected_qld_closes(base["ndx"], base["dtb3"], div_yield=DIV_NDX)
    assert len(qld_corr) == len(base["dates"])
    p = dict(base)
    p["QLD"] = qld_corr
    return c6c.add_signals(p)


def build_price_panel_c6c(price_candles, dtb3, *, div_yield, spread=BORROW_SPREAD,
                          lev_expense=EXP_LEV):
    """가격지수(FRED) → c6a 정정 합성(QQQ-1x/QLD-2x) + c6c 신호(dd_ath/dd_52).  OOS·스트레스 공용."""
    p = c6a.build_price_panel(price_candles, dtb3, div_yield=div_yield, spread=spread,
                              lev_expense=lev_expense)
    return c6c.add_signals(p)


# ── (1) 코드리뷰: 코호트-상대 ATH 변형 ───────────────────────────────────────
def run_crashlev_relative_ath(panel, years, starts, cfg, *, cash=None):
    """crash_lev 를 **코호트 자기 시작일 기준 running-max(코호트 ATH)** 로 재실행.

    시장 ATH(패널 전역 running-max)는 2000–2006 홀드아웃 코호트를 '이미 −60% 인 시장'에
    태우지만, 신선한 투자자는 자기 시작일이 자기 고점이다 → 시작 시 레버리지 아님.
    각 (s,e) 마다 dd 를 s 부터 재계산한 로컬 패널로 c6c.simulate(i0=0).
    """
    dates = panel["dates"]
    q = panel["QQQ"]
    lv = panel["QLD"]
    out = []
    for (s, e) in starts:
        sub_q = q[s:e + 1]
        sub_lv = lv[s:e + 1]
        dd_ath, dd_52 = c6c.drawdown_series(sub_q)
        loc = {"dates": dates[s:e + 1], "QQQ": sub_q, "QLD": sub_lv,
               "dd_ath": dd_ath, "dd_52": dd_52}
        sub_cash = cash[s:e + 1] if cash is not None else None
        res = c6c.simulate(loc, 0, len(sub_q), "crash_lev", cfg, cash=sub_cash)
        m = c6c.path_metrics(res)
        m["start"] = dates[s].isoformat()
        m["start_year"] = dates[s].year
        out.append(m)
    return out


def dist_crashlev(panel, years, starts, *, cash=None, relative_ath=False, cfg=None):
    """crash_lev vs B1 설계/홀드아웃 집계.  relative_ath=True 면 코호트 ATH 변형."""
    cfg = cfg or {}
    b1 = c6c.run_distribution(panel, years, "qqq", {}, starts, cash=cash)
    if relative_ath:
        cl = run_crashlev_relative_ath(panel, years, starts, cfg, cash=cash)
    else:
        cl = c6c.run_distribution(panel, years, "crash_lev", cfg, starts, cash=cash)
    out = {}
    for period, (lo, hi) in (("design", DESIGN), ("holdout", HOLDOUT)):
        b1p = c5a.split_by_startyear(b1, lo, hi)
        clp = c5a.split_by_startyear(cl, lo, hi)
        if not b1p or not clp:
            out[period] = None
            continue
        ab1 = c5a.aggregate(b1p, b1p)
        acl = c5a.aggregate(clp, b1p)
        acl["decision"] = c6c.decide(acl, ab1, is_leverage=True)
        out[period] = {"b1": ab1, "crash_lev": acl}
    return out


# ── (2/8) 정상 부트스트랩 MC: crash_lev & glide, 경로상대 ATH ─────────────────
def bootstrap_mc(panel, *, years=20, n_paths=2000, mean_block=250, seed=20260928,
                 div_yield=DIV_NDX):
    """NDX 일 가격수익률(+현금)을 정상 부트스트랩 → 합성 경로마다 crash_lev/B1 및 glide/B1.

    각 경로는 100 에서 시작하는 신선한 시계열 → dd_ath 는 **경로 자기 running-max**(코호트/경로
    상대). 이는 시장 ATH 백테스트의 '기존 낙폭 상속' 인공물을 제거한 정직한 검정이다.
    QLD 는 c6a 정정식(2·rp + div − rf − spread − expense).  glide 는 c5a.simulate 재사용.
    반환: crash_lev/B1·glide/B1 비 분포 + 두 비의 상관(같은 베팅 여부).
    """
    dates0 = panel["dates"]
    ndx = panel["ndx"]
    by = {c.dt: c.close for c in sorted(ndx, key=lambda c: c.dt)}
    seq = [by[d] for d in dates0]
    r_price = [seq[i] / seq[i - 1] - 1.0 for i in range(1, len(seq))]
    cash_full = panel["cash"]
    cash_daily = cash_full[1:]
    m = min(len(r_price), len(cash_daily))
    r_price, cash_daily = r_price[:m], cash_daily[:m]
    rng = random.Random(seed)
    L = 252 * years
    dts = _synth_dates(L + 1)
    div_d = div_yield / 252.0
    exp1_d = EXP_1X / 252.0
    expL_d = EXP_LEV / 252.0
    spr_d = BORROW_SPREAD / 252.0

    cl_ratios, gl_ratios = [], []
    cl_term, gl_term, b1_term = [], [], []
    for _ in range(n_paths):
        idx = c6a.stationary_bootstrap_indices(m, L, mean_block, rng)
        qqq = [100.0]
        qld = [100.0]
        cashp = [0.0]
        for j in idx:
            rp = r_price[j]
            rf = cash_daily[j]
            qqq.append(qqq[-1] * (1.0 + rp + div_d - exp1_d))
            qld.append(qld[-1] * (1.0 + 2.0 * rp + div_d - (rf + spr_d) - expL_d))
            cashp.append(rf)
        # B1 (c6c 엔진)
        dd_ath, dd_52 = c6c.drawdown_series(qqq)
        pcl = {"dates": dts, "QQQ": qqq, "QLD": qld, "dd_ath": dd_ath, "dd_52": dd_52}
        rb = c6c.simulate(pcl, 0, len(dts), "qqq", {}, cash=cashp)
        rcl = c6c.simulate(pcl, 0, len(dts), "crash_lev", {}, cash=cashp)
        # glide (c5a 엔진, 동일 경로)
        sig = c5a.ewma_vol_annual(qqq)
        trd = c5a.trend_ok_series(qqq)
        rg = c5a.simulate({"QQQ": qqq, "QLD": qld}, dts, 0, len(dts), "glide",
                          sigma=sig, trend=trd, cash=cashp)
        if rb.final_value > 0:
            cl_ratios.append(rcl.final_value / rb.final_value)
            gl_ratios.append(rg.final_value / rb.final_value)
            cl_term.append(rcl.final_value)
            gl_term.append(rg.final_value)
            b1_term.append(rb.final_value)

    def _summ(ratios):
        r = sorted(ratios)
        n = len(r)
        k5 = max(1, int(math.ceil(0.05 * n)))
        return {
            "n_paths": n,
            "ratio_median": c5a._pct(r, 0.5),
            "ratio_p05": c5a._pct(r, 0.05),
            "ratio_p25": c5a._pct(r, 0.25),
            "ratio_p75": c5a._pct(r, 0.75),
            "ratio_mean": sum(r) / n,
            "p_lt_1": sum(1 for x in r if x < 1.0) / n,
            "p_lt_090": sum(1 for x in r if x < 0.9) / n,
            "p_lt_115": sum(1 for x in r if x < 1.15) / n,
            "cvar5": sum(r[:k5]) / k5,
        }

    cl = _summ(cl_ratios)
    gl = _summ(gl_ratios)
    corr_p = _pearson(cl_ratios, gl_ratios)
    corr_s = _spearman(cl_ratios, gl_ratios)
    return {"years": years, "mean_block": mean_block, "n_paths": len(cl_ratios),
            "crash_lev": cl, "glide": gl,
            "corr_pearson": corr_p, "corr_spearman": corr_s,
            "cl_median_term": c5a._pct(sorted(cl_term), 0.5),
            "gl_median_term": c5a._pct(sorted(gl_term), 0.5),
            "b1_median_term": c5a._pct(sorted(b1_term), 0.5),
            "_cl_ratios": cl_ratios, "_gl_ratios": gl_ratios}


def _synth_dates(n, start=date(1990, 1, 2)):
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _pearson(a, b):
    n = min(len(a), len(b))
    if n < 2:
        return float("nan")
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if sa == 0 or sb == 0:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def _spearman(a, b):
    n = min(len(a), len(b))
    if n < 2:
        return float("nan")

    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(x):
            j = i
            while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    return _pearson(ranks(a[:n]), ranks(b[:n]))


# ── (5) 5년 후 적립중단(실직) — c6c.simulate 미러(crash_lev/qqq) ─────────────
def simulate_contrib_stop(panel, i0, i1, strat, cfg, cash, stop_months):
    """c6c.simulate 를 그대로 미러하되 month index >= stop_months 부터 월적립 0.

    c6c 미수정(감사 헬퍼).  중단 후에도 잔여 자산·버킷은 시장·규칙대로 계속 운용.
    """
    cfg = cfg or {}
    q = panel["QQQ"]
    lv = panel["QLD"]
    dd_ath = panel["dd_ath"]
    dd_52 = panel["dd_52"]
    dates = panel["dates"]
    monthly = cfg.get("monthly", c6c.MONTHLY)
    initial = cfg.get("initial", c6c.INITIAL)
    n = i1 - i0
    sub_dates = dates[i0:i1]
    starts = R.month_start_flags(sub_dates)
    if cash is None:
        cash = [0.0] * len(dates)
    st = {"QQQ": 0.0, "QLD": 0.0, "bucket": 0.0, "cost": 0.0, "buys": 0, "sells": 0}
    month_m = -1
    in_lev = False
    dp_floor = cfg.get("floor_months", c6c.DP_FLOOR_MONTHS) * monthly
    eq = 0.0
    for k in range(n):
        t = i0 + k
        if k > 0:
            st["bucket"] *= (1.0 + cash[t])
            if q[t - 1] > 0:
                st["QQQ"] *= q[t] / q[t - 1]
            if lv[t - 1] > 0:
                st["QLD"] *= lv[t] / lv[t - 1]
        dep = 0.0
        if k == 0:
            dep += initial
        if starts[k]:
            month_m += 1
            if month_m < stop_months:
                dep += monthly
        if dep > 0:
            fx = dep * c6c.FX_BPS * 1e-4
            st["bucket"] += (dep - fx)
        if starts[k]:
            st["cost"] = 0.0
            st["buys"] = 0
            st["sells"] = 0
            sig = c6c._decide(strat, cfg, st, month_m, q[t], lv[t], dd_ath[t], dd_52[t],
                              monthly=monthly, initial=initial, dp_floor=dp_floor,
                              in_lev=in_lev)
            in_lev = sig["in_lev"]
        eq = st["QQQ"] + st["QLD"] + st["bucket"]
    return eq


def contrib_stop(panel, *, fast=False, stop_year=5, cash=None):
    """5년 적립 후 중단.  crash_lev vs B1 각 시작일 최종·최종비 (설계 코호트 1986–1999)."""
    dates = panel["dates"]
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, 20, fast=fast)
    stop_months = stop_year * 12
    ratios, cl_final, b1_final = [], [], []
    for (s, e) in starts:
        if not (DESIGN[0] <= dates[s].year <= DESIGN[1]):
            continue
        cf = simulate_contrib_stop(panel, s, e + 1, "crash_lev", {}, cash, stop_months)
        bf = simulate_contrib_stop(panel, s, e + 1, "qqq", {}, cash, stop_months)
        if bf > 0:
            ratios.append(cf / bf)
            cl_final.append(cf)
            b1_final.append(bf)
    if not ratios:
        return None
    ratios.sort()
    return {"n": len(ratios), "ratio_median": c5a._pct(ratios, 0.5),
            "ratio_p05": c5a._pct(ratios, 0.05),
            "p_cl_lt_b1": sum(1 for x in ratios if x < 1.0) / len(ratios),
            "cl_median_final": c5a._pct(sorted(cl_final), 0.5),
            "b1_median_final": c5a._pct(sorted(b1_final), 0.5)}


# ── (6) 행동재무: B1 대비 같은 경로 비교(핵심 공백 정정) ─────────────────────
def _dd_and_trough(res):
    ddd = R.dollar_drawdown(res.equity, res.deposit_series)
    if not ddd:
        return None
    trough = min(range(len(ddd)), key=lambda i: ddd[i])
    uw = sum(1 for x in ddd if x < -1e-9) / len(ddd)
    return ddd, trough, uw


def behavioral_vs_b1_crashlev(panel, years, *, fast=False, cash=None):
    """crash_lev vs B1(둘 다 c6c 엔진, 같은 시작일).  strat 낙폭·B1 낙폭·동일시점 낙폭."""
    dates = panel["dates"]
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, years, fast=fast)
    rows = []
    for (s, e) in starts:
        rcl = c6c.simulate(panel, s, e + 1, "crash_lev", {}, cash=cash)
        rb1 = c6c.simulate(panel, s, e + 1, "qqq", {}, cash=cash)
        a = _dd_and_trough(rcl)
        b = _dd_and_trough(rb1)
        if a is None or b is None:
            continue
        cl_dd, cl_tr, cl_uw = a
        b1_dd, b1_tr, b1_uw = b
        rows.append({
            "start_year": dates[s].year,
            "cl_worst": min(cl_dd), "b1_worst": min(b1_dd),
            "cl_uw": cl_uw, "b1_uw": b1_uw,
            "b1_at_cl_trough": b1_dd[cl_tr],   # crash_lev 최악시점의 B1 낙폭
            "cl_at_b1_trough": cl_dd[b1_tr],   # B1 최악시점의 crash_lev 낙폭
        })
    return _behav_agg(rows)


def behavioral_vs_b1_glide(panel, years, *, fast=False):
    """glide vs B1(둘 다 c5a 엔진, 같은 시작일)."""
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, years, fast=fast)
    rows = []
    for (s, e) in starts:
        rg = c5a.simulate(closes, dates, s, e + 1, "glide", sigma=panel["sigma"],
                          trend=panel["trend"], cash=panel["cash"])
        rb1 = c5a.simulate(closes, dates, s, e + 1, "qqq", sigma=panel["sigma"],
                           trend=panel["trend"], cash=panel["cash"])
        a = _dd_and_trough(rg)
        b = _dd_and_trough(rb1)
        if a is None or b is None:
            continue
        g_dd, g_tr, g_uw = a
        b1_dd, b1_tr, b1_uw = b
        rows.append({
            "start_year": dates[s].year,
            "cl_worst": min(g_dd), "b1_worst": min(b1_dd),
            "cl_uw": g_uw, "b1_uw": b1_uw,
            "b1_at_cl_trough": b1_dd[g_tr],
            "cl_at_b1_trough": g_dd[b1_tr],
        })
    return _behav_agg(rows)


def _behav_agg(rows):
    out = {}
    for period, (lo, hi) in (("design", DESIGN), ("holdout", HOLDOUT)):
        sub = [r for r in rows if lo <= r["start_year"] <= hi]
        if not sub:
            out[period] = None
            continue
        def med(key):
            return c5a._pct(sorted(r[key] for r in sub), 0.5)
        out[period] = {
            "n": len(sub),
            "strat_worst_dd": min(r["cl_worst"] for r in sub),
            "strat_median_worst_dd": med("cl_worst"),
            "b1_worst_dd": min(r["b1_worst"] for r in sub),
            "b1_median_worst_dd": med("b1_worst"),
            "strat_p_dd_gt_50": sum(1 for r in sub if r["cl_worst"] < -0.50) / len(sub),
            "b1_p_dd_gt_50": sum(1 for r in sub if r["b1_worst"] < -0.50) / len(sub),
            "strat_p_dd_gt_70": sum(1 for r in sub if r["cl_worst"] < -0.70) / len(sub),
            "b1_p_dd_gt_70": sum(1 for r in sub if r["b1_worst"] < -0.70) / len(sub),
            "strat_median_uw": med("cl_uw"),
            "b1_median_uw": med("b1_uw"),
            "median_b1_at_strat_trough": med("b1_at_cl_trough"),
            "median_strat_at_b1_trough": med("cl_at_b1_trough"),
        }
    return out


# ── (7) 3.11× 홀드아웃 절단(end-point) 편향 정량화 ───────────────────────────
def truncation_bias(panel, *, cash=None):
    """20y 홀드아웃 시작(2000–2006)에서 종료일을 대체일자로 절단해 crash_lev/B1 median 비 변화.

    자연 종점(2020–2026, 시장 고점)이 3.11× 를 얼마나 만드는지: 종점을 베어/중간에 두면 붕괴하는가.
    각 종료캡에 대해 e = min(자연 20y 종점, 캡일자 index).  최소 8년 보유한 경로만 집계.
    """
    dates = panel["dates"]
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts20 = c5a.start_indices(dates, ms, 20, fast=False)
    hold = [(s, e) for (s, e) in starts20 if HOLDOUT[0] <= dates[s].year <= HOLDOUT[1]]
    caps = [
        ("natural_20y", None),
        ("2007-10-09_peak", date(2007, 10, 9)),
        ("2009-03-09_gfc_low", date(2009, 3, 9)),
        ("2015-12-31", date(2015, 12, 31)),
        ("2018-12-24_low", date(2018, 12, 24)),
        ("2020-03-23_covid_low", date(2020, 3, 23)),
        ("2022-10-12_bear_low", date(2022, 10, 12)),
    ]
    out = []
    for name, capd in caps:
        ratios = []
        min_years = None
        for (s, e0) in hold:
            e = e0 if capd is None else min(e0, bisect.bisect_right(dates, capd) - 1)
            if e <= s:
                continue
            span_y = (dates[e] - dates[s]).days / 365.25
            if capd is not None and span_y < 8.0:
                continue
            rcl = c6c.simulate(panel, s, e + 1, "crash_lev", {}, cash=cash)
            rb1 = c6c.simulate(panel, s, e + 1, "qqq", {}, cash=cash)
            if rb1.final_value > 0:
                ratios.append(rcl.final_value / rb1.final_value)
                min_years = span_y if min_years is None else min(min_years, span_y)
        if ratios:
            ratios.sort()
            out.append({"cap": name, "n": len(ratios),
                        "median_ratio": c5a._pct(ratios, 0.5),
                        "p05_ratio": c5a._pct(ratios, 0.05),
                        "min_span_years": round(min_years, 1) if min_years else None})
    # 단일 최심 코호트(2000-03) 종점 스윕
    s0 = next((i for i, d in enumerate(dates) if d.year == 2000 and d.month == 3), None)
    sweep = []
    if s0 is not None:
        for endy in range(2005, 2027):
            capd = date(endy, min(dates[s0].month, 12), 1)
            e = bisect.bisect_right(dates, capd) - 1
            if e <= s0:
                continue
            rcl = c6c.simulate(panel, s0, e + 1, "crash_lev", {}, cash=cash)
            rb1 = c6c.simulate(panel, s0, e + 1, "qqq", {}, cash=cash)
            if rb1.final_value > 0:
                sweep.append({"end_year": endy,
                              "years": round((dates[e] - dates[s0]).days / 365.25, 1),
                              "ratio": rcl.final_value / rb1.final_value})
    return {"caps": out, "cohort_2000_03_sweep": sweep}


# ── (3) Nikkei OOS 원장(peek-once) ───────────────────────────────────────────
def log_nikkei_ledger(panel, ledger_path):
    """crash_lev Nikkei 단위자본 스트림 원장 적재.  design≤1990-12-31, holdout 1991+(잃어버린 10년)."""
    dates = panel["dates"]
    idea = "c7a_crash_lev_oos_nikkei"
    params = {"strat": "crash_lev", "budget": "fair", "enter": c6c.CL_ENTER,
              "exit": c6c.CL_EXIT, "div_assumed": DIV_COMP, "cash": "dtb3_0.5pct",
              "source": "FRED_NIKKEI225", "div_dbl_count": "corrected"}
    di1 = bisect.bisect_right(dates, date(1990, 12, 31))
    res_d = c6c.simulate(panel, 0, di1, "crash_lev", {}, cash=panel["cash"])
    um_d = gate_eval.unit_capital_metrics(res_d.inv_ret[1:], dates=res_d.dates[1:])
    gate_eval.log_evaluation(idea, dict(params), 1, "design", um_d,
                             window=(res_d.dates[0], res_d.dates[-1]), ledger_path=ledger_path)
    logged = {"design": um_d}
    if not gate.already_peeked(ledger_path, idea):
        hi0 = bisect.bisect_left(dates, date(1991, 1, 1))
        res_h = c6c.simulate(panel, hi0, len(dates), "crash_lev", {}, cash=panel["cash"])
        um_h = gate_eval.unit_capital_metrics(res_h.inv_ret[1:], dates=res_h.dates[1:])
        try:
            gate_eval.log_evaluation(idea, dict(params), 1, "holdout", um_h,
                                     window=(res_h.dates[0], res_h.dates[-1]),
                                     ledger_path=ledger_path)
            logged["holdout"] = um_h
        except gate.PeekOnceError:
            logged["holdout"] = "already_peeked"
    else:
        logged["holdout"] = "already_peeked"
    return logged


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--mc-paths", type=int, default=2000)
    ap.add_argument("--ledger", default=LEDGER)
    args = ap.parse_args(argv)

    res = {"meta": {"generated": date.today().isoformat(), "fast": args.fast,
                    "mc_paths": args.mc_paths}}

    print("[c7a] 배당정정 합성 패널(시장 ATH 신호) 구축...")
    panel = build_corrected_panel()
    cash0 = [0.0] * len(panel["dates"])

    # (0/1) 헤드라인 재현(정정) + 시장 ATH vs 코호트 ATH
    print("[c7a] (1) crash_lev 정정 재현 + 시장/코호트 ATH 대조...")
    dates = panel["dates"]
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    rep = {}
    for years in (20, 10):
        starts = c5a.start_indices(dates, ms, years, fast=args.fast)
        rep[years] = {
            "market_ath": dist_crashlev(panel, years, starts, cash=cash0, relative_ath=False),
            "cohort_ath": dist_crashlev(panel, years, starts, cash=cash0, relative_ath=True),
        }
    res["reproduce"] = rep

    # (2/8) 부트스트랩 MC + 상관
    print(f"[c7a] (2) 정상 부트스트랩 MC({args.mc_paths} 경로, 경로상대 ATH)...")
    mc20 = bootstrap_mc(panel, years=20, n_paths=args.mc_paths)
    mc10 = bootstrap_mc(panel, years=10, n_paths=args.mc_paths)
    for mc in (mc20, mc10):
        mc.pop("_cl_ratios", None)
        mc.pop("_gl_ratios", None)
    res["mc_20y"] = mc20
    res["mc_10y"] = mc10

    # (7) 절단 편향
    print("[c7a] (7) 3.11× 홀드아웃 절단(end-point) 편향...")
    res["truncation"] = truncation_bias(panel, cash=cash0)

    # (6) 행동재무 B1 대비
    print("[c7a] (6) 행동재무(B1 대비, crash_lev & glide)...")
    res["behavioral_crashlev_20y"] = behavioral_vs_b1_crashlev(panel, 20, fast=args.fast,
                                                               cash=cash0)
    res["behavioral_glide_20y"] = behavioral_vs_b1_glide(panel, 20, fast=args.fast)

    # (4) 스트레스
    print("[c7a] (4) 스트레스(조달 +2%, 보수 1.5%)...")
    stress = {}
    hp = build_price_panel_c6c(panel["ndx"], panel["dtb3"], div_yield=DIV_NDX,
                               spread=BORROW_SPREAD + 0.02)
    starts20 = c5a.start_indices(dates, ms, 20, fast=args.fast)
    stress["financing_plus2pct"] = dist_crashlev(hp, 20, starts20, cash=hp["cash"])
    ep = build_price_panel_c6c(panel["ndx"], panel["dtb3"], div_yield=DIV_NDX,
                               lev_expense=0.015)
    stress["expense_1p5"] = dist_crashlev(ep, 20, starts20, cash=ep["cash"])
    # (5) 적립중단
    stress["contrib_stop_5y"] = contrib_stop(panel, fast=args.fast, cash=cash0)
    res["stress"] = stress

    # (3) Nikkei OOS
    print("[c7a] (3) Nikkei225 OOS(잃어버린 10년)...")
    try:
        nik = hd.load_fred("NIKKEI225")
        nik_dtb3 = [hd.Candle("RF", c.dt, 0.5, 0.5, 0.5, 0.5, 0.0) for c in nik]
        npan = build_price_panel_c6c(nik, nik_dtb3, div_yield=DIV_COMP)
        nd = npan["dates"]
        nms = [t for t, f in enumerate(R.month_start_flags(nd)) if f]
        nik_out = {"first": nd[0].isoformat(), "last": nd[-1].isoformat(), "per_horizon": {}}
        for years in (20, 10):
            nstarts = c5a.start_indices(nd, nms, years, fast=args.fast)
            # Nikkei 시작연도 창: 설계=고점 이전(1970–1990), 홀드아웃=고점 이후(1991–2006)
            nik_out["per_horizon"][years] = _nikkei_dist(npan, years, nstarts, nd)
        res["nikkei"] = nik_out
        if not args.no_ledger:
            print("[c7a] (3b) Nikkei 원장 적재(peek-once)...")
            res["nikkei_ledger"] = log_nikkei_ledger(npan, args.ledger)
    except Exception as ex:  # noqa: BLE001
        res["nikkei"] = {"error": f"{type(ex).__name__}: {ex}"}

    # 상관(역사 경로) — 20y 설계+홀드아웃 시작일에서 glide/B1 vs crashlev/B1
    print("[c7a] (8) 두 전략 상대성과 상관(역사 경로)...")
    res["hist_corr_20y"] = hist_correlation(panel, 20, fast=args.fast, cash=cash0)

    RESULTS_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
    print(f"[c7a] 결과 JSON → {RESULTS_JSON}")
    _print_summary(res)
    write_report(res)
    print(f"[c7a] 리포트 → {REPORT}")
    return res


def _nikkei_dist(panel, years, starts, nd):
    """Nikkei crash_lev vs B1, 설계(시작 1970–1990)/홀드아웃(1991–2006, 고점 이후)."""
    b1 = c6c.run_distribution(panel, years, "qqq", {}, starts, cash=panel["cash"])
    cl = c6c.run_distribution(panel, years, "crash_lev", {}, starts, cash=panel["cash"])
    out = {}
    for period, (lo, hi) in (("design", (1970, 1990)), ("holdout", (1991, 2006))):
        b1p = c5a.split_by_startyear(b1, lo, hi)
        clp = c5a.split_by_startyear(cl, lo, hi)
        if not b1p or not clp:
            out[period] = None
            continue
        ab1 = c5a.aggregate(b1p, b1p)
        acl = c5a.aggregate(clp, b1p)
        acl["decision"] = c6c.decide(acl, ab1, is_leverage=True)
        out[period] = {"b1": ab1, "crash_lev": acl,
                       "first": nd[starts[0][0]].isoformat(),
                       "last": nd[starts[-1][0]].isoformat()}
    return out


def hist_correlation(panel, years, *, fast=False, cash=None):
    """같은 20y 시작일에서 crash_lev/B1 과 glide/B1 비를 짝지어 상관(같은 베팅인가)."""
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, years, fast=fast)
    cl_r, gl_r, yrs = [], [], []
    for (s, e) in starts:
        rb1 = c6c.simulate(panel, s, e + 1, "qqq", {}, cash=cash)
        rcl = c6c.simulate(panel, s, e + 1, "crash_lev", {}, cash=cash)
        rg = c5a.simulate(closes, dates, s, e + 1, "glide", sigma=panel["sigma"],
                          trend=panel["trend"], cash=panel["cash"])
        rb1g = c5a.simulate(closes, dates, s, e + 1, "qqq", sigma=panel["sigma"],
                            trend=panel["trend"], cash=panel["cash"])
        if rb1.final_value > 0 and rb1g.final_value > 0:
            cl_r.append(rcl.final_value / rb1.final_value)
            gl_r.append(rg.final_value / rb1g.final_value)
            yrs.append(dates[s].year)
    out = {"n": len(cl_r),
           "corr_pearson_all": _pearson(cl_r, gl_r),
           "corr_spearman_all": _spearman(cl_r, gl_r)}
    for period, (lo, hi) in (("design", DESIGN), ("holdout", HOLDOUT)):
        idx = [i for i, y in enumerate(yrs) if lo <= y <= hi]
        if len(idx) >= 2:
            a = [cl_r[i] for i in idx]
            b = [gl_r[i] for i in idx]
            out[f"corr_pearson_{period}"] = _pearson(a, b)
            out[f"corr_spearman_{period}"] = _spearman(a, b)
    return out


def _print_summary(res):
    print("\n=== c7a 요약 ===")
    mk = res["reproduce"][20]["market_ath"]["design"]
    co = res["reproduce"][20]["cohort_ath"]["design"]
    if mk and co:
        print(f"  20y 설계 crash_lev/B1: 시장ATH {mk['crash_lev']['median']/mk['b1']['median']:.2f}× "
              f"vs 코호트ATH {co['crash_lev']['median']/co['b1']['median']:.2f}×")
    mkh = res["reproduce"][20]["market_ath"]["holdout"]
    coh = res["reproduce"][20]["cohort_ath"]["holdout"]
    if mkh and coh:
        print(f"  20y 홀드 crash_lev/B1: 시장ATH {mkh['crash_lev']['median']/mkh['b1']['median']:.2f}× "
              f"vs 코호트ATH {coh['crash_lev']['median']/coh['b1']['median']:.2f}×")
    mc = res["mc_20y"]
    print(f"  MC 20y crash_lev/B1 median {mc['crash_lev']['ratio_median']:.2f} "
          f"P(<1)={mc['crash_lev']['p_lt_1']:.2f} P(<0.9)={mc['crash_lev']['p_lt_090']:.2f} "
          f"CVaR5={mc['crash_lev']['cvar5']:.2f}")
    print(f"  MC 20y corr(crash_lev, glide) Pearson {mc['corr_pearson']:.2f} "
          f"Spearman {mc['corr_spearman']:.2f}")


# ── 리포트(head 보존 + 결과 append; c6a 패턴) ───────────────────────────────
def _clrow(a):
    """crash_lev vs B1 한 구간 표 행."""
    if not a or not a.get("crash_lev") or not a.get("b1"):
        return "| — | — | — | — | — | — |"
    cl, b1 = a["crash_lev"], a["b1"]
    return (f"| ${cl['median']:,.0f} ({cl['median']/b1['median']:.2f}×) | "
            f"${cl['p5']:,.0f} ({cl['p5']/max(1e-9,b1['p5']):.2f}×) | "
            f"{cl['p_beat_b1']:.2f} | {cl['regret']:.2f} | {cl['worst_dollar_dd']:.2f} | "
            f"**{cl['decision']['verdict']}** |")


def _mc_block(mc, key):
    m = mc[key]
    return [f"| median | {m['ratio_median']:.3f} |",
            f"| p05 / p25 / p75 | {m['ratio_p05']:.3f} / {m['ratio_p25']:.3f} / {m['ratio_p75']:.3f} |",
            f"| P(<1) | {m['p_lt_1']:.3f} |",
            f"| P(<0.9) | {m['p_lt_090']:.3f} |",
            f"| P(<1.15) | {m['p_lt_115']:.3f} |",
            f"| CVaR5 | {m['cvar5']:.3f} |"]


def write_report(res):
    L = []
    L.append("<!-- RESULTS_BELOW -->")
    L.append("")
    m = res["meta"]
    L.append(f"> 실행 {m['generated']} · fast={m['fast']} · MC {m['mc_paths']} 경로 · "
             "감사자(executor) · src/·c5a·c6a·c6c 무수정, import 만.")
    L.append("")

    # 1. 재현 + 시장 ATH vs 코호트 ATH
    L.append("## 1. 정정 재현 — 시장 ATH vs 코호트(경로상대) ATH")
    L.append("crash_lev/B1 median×.  '시장 ATH'=패널 전역 running-max(c6c 구현), "
             "'코호트 ATH'=각 시작일 기준 running-max(신선한 투자자).")
    L.append("")
    L.append("| 지평 | 구간 | 시장 ATH ×B1 | 코호트 ATH ×B1 | 시장 판정 | 코호트 판정 |")
    L.append("|---|---|---:|---:|:--:|:--:|")
    for years in (20, 10):
        for period in ("design", "holdout"):
            mk = res["reproduce"][years]["market_ath"].get(period)
            co = res["reproduce"][years]["cohort_ath"].get(period)
            def cell(a):
                return (a["crash_lev"]["median"] / a["b1"]["median"]) if a else float("nan")
            def dec(a):
                return a["crash_lev"]["decision"]["verdict"] if a else "—"
            L.append(f"| {years}y | {period} | {cell(mk):.2f}× | {cell(co):.2f}× | "
                     f"{dec(mk)} | {dec(co)} |")
    L.append("")
    L.append("crash_lev vs B1 상세(정정, 시장 ATH):")
    L.append("| 지평·구간 | median(×B1) | p5(×B1) | P(beat) | regret | 최악$낙폭 | 판정 |")
    L.append("|---|---:|---:|---:|---:|---:|:--:|")
    for years in (20, 10):
        for period in ("design", "holdout"):
            a = res["reproduce"][years]["market_ath"].get(period)
            L.append(f"| {years}y {period} " + _clrow(a))
    L.append("")

    # 2. 부트스트랩 MC
    L.append("## 2. 정상 부트스트랩 MC (경로상대 ATH — 정직한 신선-투자자 검정)")
    for key, lab in (("mc_20y", "20년"), ("mc_10y", "10년")):
        mc = res[key]
        L.append("")
        L.append(f"### {lab} 지평 ({mc['n_paths']} 경로, 평균블록 {mc['mean_block']})")
        L.append("| 지표 | crash_lev/B1 | glide/B1 |")
        L.append("|---|---:|---:|")
        clb = _mc_block(mc, "crash_lev")
        glb = _mc_block(mc, "glide")
        for cl_line, gl_line in zip(clb, glb):
            label = cl_line.split("|")[1].strip()
            clv = cl_line.split("|")[2].strip()
            glv = gl_line.split("|")[2].strip()
            L.append(f"| {label} | {clv} | {glv} |")
        L.append(f"| corr(crash_lev, glide) | Pearson {mc['corr_pearson']:.3f} | "
                 f"Spearman {mc['corr_spearman']:.3f} |")
    L.append("")

    # 3. Nikkei
    L.append("## 3. Nikkei225 OOS (잃어버린 10년)")
    nik = res.get("nikkei", {})
    if "error" in nik:
        L.append(f"(로드 실패: {nik['error']})")
    else:
        L.append(f"구간 {nik['first']}…{nik['last']}. 설계=고점 이전 시작(1970–1990), "
                 "홀드아웃=고점 이후 시작(1991–2006).")
        L.append("| 지평·구간 | median(×B1) | p5(×B1) | P(beat) | regret | 최악$낙폭 | 판정 |")
        L.append("|---|---:|---:|---:|---:|---:|:--:|")
        for years in (20, 10):
            for period in ("design", "holdout"):
                a = nik["per_horizon"].get(years, {}).get(period)
                L.append(f"| {years}y {period} " + _clrow(a))
        lg = res.get("nikkei_ledger", {})
        if lg:
            d = lg.get("design", {})
            L.append("")
            L.append(f"**원장 c7a_crash_lev_oos_nikkei:** design CAGR {d.get('cagr',0)*100:.1f}% · "
                     f"MDD {d.get('max_drawdown',0)*100:.0f}% · Sharpe {d.get('sr_annual',0):.2f} · "
                     f"holdout={'적재' if isinstance(lg.get('holdout'), dict) else lg.get('holdout')}.")
    L.append("")

    # 4. 스트레스
    L.append("## 4. 스트레스 (20년 지평)")
    L.append("| 시나리오·구간 | median(×B1) | p5(×B1) | regret | 최악$낙폭 | 판정 |")
    L.append("|---|---:|---:|---:|---:|:--:|")
    st = res["stress"]
    for skey, slab in (("financing_plus2pct", "조달+2%"), ("expense_1p5", "보수1.5%")):
        for period in ("design", "holdout"):
            a = st[skey].get(period)
            if not a:
                continue
            cl, b1 = a["crash_lev"], a["b1"]
            L.append(f"| {slab} {period} | ${cl['median']:,.0f} ({cl['median']/b1['median']:.2f}×) | "
                     f"${cl['p5']:,.0f} ({cl['p5']/max(1e-9,b1['p5']):.2f}×) | {cl['regret']:.2f} | "
                     f"{cl['worst_dollar_dd']:.2f} | **{cl['decision']['verdict']}** |")
    cs = st.get("contrib_stop_5y")
    if cs:
        L.append("")
        L.append(f"**5년 후 적립중단(실직):** crash_lev/B1 최종비 median {cs['ratio_median']:.2f} · "
                 f"p05 {cs['ratio_p05']:.2f} · P(crash_lev<B1) {cs['p_cl_lt_b1']:.2f} (n={cs['n']}).")
    L.append("")

    # 5. 절단 편향
    L.append("## 5. 3.11× 홀드아웃 절단(end-point) 편향")
    tr = res["truncation"]
    L.append("20y 홀드아웃 시작(2000–2006)의 종료일을 대체일자로 절단 → crash_lev/B1 median 비.")
    L.append("| 종료 캡 | n | median 비 | p05 비 | 최소보유(y) |")
    L.append("|---|---:|---:|---:|---:|")
    for c in tr["caps"]:
        L.append(f"| {c['cap']} | {c['n']} | {c['median_ratio']:.2f}× | {c['p05_ratio']:.2f}× | "
                 f"{c['min_span_years']} |")
    L.append("")
    sw = tr.get("cohort_2000_03_sweep", [])
    if sw:
        L.append("단일 최심 코호트(2000-03 시작) 종점 스윕 — crash_lev/B1 비:")
        L.append("| 종료연도 | 보유(y) | 비 |")
        L.append("|---|---:|---:|")
        for r in sw:
            L.append(f"| {r['end_year']} | {r['years']} | {r['ratio']:.2f}× |")
    L.append("")

    # 6. 행동재무 B1 대비
    L.append("## 6. 행동재무 — B1(DCA-QQQ) 대비 같은 경로 (20년)")
    L.append("| 전략·구간 | strat 최악$낙폭 | B1 최악$낙폭 | strat P(>50%) | B1 P(>50%) | "
             "strat 언더워터 | B1 언더워터 | strat최악시점 B1낙폭 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for key, lab in (("behavioral_crashlev_20y", "crash_lev"),
                     ("behavioral_glide_20y", "glide")):
        bh = res.get(key, {})
        for period in ("design", "holdout"):
            b = bh.get(period)
            if not b:
                continue
            L.append(f"| {lab}/{period} | {b['strat_median_worst_dd']:.2f} | "
                     f"{b['b1_median_worst_dd']:.2f} | {b['strat_p_dd_gt_50']:.2f} | "
                     f"{b['b1_p_dd_gt_50']:.2f} | {b['strat_median_uw']:.2f} | "
                     f"{b['b1_median_uw']:.2f} | {b['median_b1_at_strat_trough']:.2f} |")
    L.append("")

    # 7. 상관(역사)
    hc = res.get("hist_corr_20y", {})
    L.append("## 7. 두 전략 상대성과 상관 (역사 20y 시작일)")
    L.append(f"- 전체(n={hc.get('n')}): Pearson {hc.get('corr_pearson_all',float('nan')):.3f}, "
             f"Spearman {hc.get('corr_spearman_all',float('nan')):.3f}")
    if "corr_pearson_design" in hc:
        L.append(f"- 설계: Pearson {hc['corr_pearson_design']:.3f}, Spearman {hc['corr_spearman_design']:.3f}")
    if "corr_pearson_holdout" in hc:
        L.append(f"- 홀드아웃: Pearson {hc['corr_pearson_holdout']:.3f}, "
                 f"Spearman {hc['corr_spearman_holdout']:.3f}")
    L.append(f"- 부트스트랩(경로상대): 20y Pearson {res['mc_20y']['corr_pearson']:.3f}, "
             f"10y Pearson {res['mc_10y']['corr_pearson']:.3f}")
    L.append("")

    # 8. 안전/기대이득 side-by-side
    L.append("## 8. 안전 vs 기대이득 side-by-side (glide vs crash_lev vs B1)")
    L.append(_safety_table(res))
    L.append("")

    body = "\n".join(L) + "\n"
    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            head = prev.split("<!-- RESULTS_BELOW -->")[0].rstrip()
            REPORT.write_text(head + "\n\n" + body, encoding="utf-8")
            return
    REPORT.write_text("# Cycle 7 · c7a — Adversarial audit of c6c_crash_lev\n\n" + body,
                      encoding="utf-8")


def _safety_table(res):
    """부트스트랩 20y(경로상대) 기준 안전/이득 지표.  B1 기준선 포함."""
    mc = res["mc_20y"]
    cl, gl = mc["crash_lev"], mc["glide"]
    bh_cl = res.get("behavioral_crashlev_20y", {}).get("design", {})
    bh_gl = res.get("behavioral_glide_20y", {}).get("design", {})
    L = ["부트스트랩 20y(경로상대 ATH) median 비·downside + 설계 코호트 행동낙폭.",
         "'이득/위험'=(median비−1)/max(0.01, 1−CVaR5) — 기대초과 대비 꼬리손실 효율.",
         "",
         "| 전략 | median ×B1 | P(<1) | P(<0.9) | CVaR5 | 설계 최악$낙폭(중앙) | P(낙폭>50%) | 이득/위험 |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]

    def geff(m):
        return (m["ratio_median"] - 1.0) / max(0.01, 1.0 - m["cvar5"])
    L.append(f"| B1 (DCA-QQQ) | 1.00× | — | — | 1.000 | "
             f"{bh_cl.get('b1_median_worst_dd', float('nan')):.2f} | "
             f"{bh_cl.get('b1_p_dd_gt_50', float('nan')):.2f} | 0.00 |")
    L.append(f"| crash_lev | {cl['ratio_median']:.2f}× | {cl['p_lt_1']:.2f} | {cl['p_lt_090']:.2f} | "
             f"{cl['cvar5']:.2f} | {bh_cl.get('strat_median_worst_dd', float('nan')):.2f} | "
             f"{bh_cl.get('strat_p_dd_gt_50', float('nan')):.2f} | {geff(cl):.2f} |")
    L.append(f"| glide | {gl['ratio_median']:.2f}× | {gl['p_lt_1']:.2f} | {gl['p_lt_090']:.2f} | "
             f"{gl['cvar5']:.2f} | {bh_gl.get('strat_median_worst_dd', float('nan')):.2f} | "
             f"{bh_gl.get('strat_p_dd_gt_50', float('nan')):.2f} | {geff(gl):.2f} |")
    return "\n".join(L)


if __name__ == "__main__":
    main()
