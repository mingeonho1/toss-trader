"""c6a — Adversarial audit of c5a_lc_glide (lifecycle leverage).

목적: 사전등록을 통과한 유일 후보 `c5a_lc_glide` 를 **정직하게 깨보는** 감사.
확증이 아니라 반증을 시도한다. src/ 와 c5a 파일은 수정하지 않고 import 만 한다.

감사 항목(reports/cycle6_c6a_lifecycle_audit.md 에 기록):
 1. 헤드라인 재현 + 코드리뷰(look-ahead / 체결 / 수수료 / 합성 조달·보수 / 배당 이중계상).
 2. 통계적 정직성: 겹침 시작일 → 유효표본 ~2. 정상 부트스트랩 MC(≥2000 경로, 평균블록~250일).
 3. NEW OOS 1971–1985: FRED NASDAQCOM 가격 + 가정배당(1.5%, 1.0/2.0 민감도), DTB3 조달,
    사전등록 glide 설정 그대로. 1973–74·1987 포함. 원장 idea_id=c6a_lc_glide_oos1971(peek-once).
 4. 스트레스: (a) Nikkei225 OOS, (b) 조달 +2%, (c) 2x 보수 1.5%, (d) 5년 후 적립중단.
 5. 행동재무: 달러낙폭·언더워터 분포, P(달러낙폭>50%), 최악 낙폭의 시점(적립 대비).

배당 이중계상 정정(사전 지정): 합성 2x 는 총수익 base 에 L 배 → 배당 L 배. 실물은 가격 L 배
+ 1× 배당. 정정 = 현재 − (L−1)·div = 현재 − 0.7%/yr(2x 슬리브 한정, 1x 는 불변).

재현: PYTHONPATH=src .venv/bin/python experiments/c6a_lifecycle_audit.py [--fast] [--no-ledger]
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

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle6_c6a_lifecycle_audit.md"
RESULTS_JSON = ROOT / "reports" / "c6a_audit_results.json"

# 사전등록 상수(c5a 와 동일 — 재튜닝 금지)
DIV_NDX = c5a.DIV_YIELD          # 0.7%/yr (NDX 총수익 근사)
EXP_1X = c5a.EXP_1X              # 0.20%/yr
EXP_LEV = c5a.EXP_LEV            # 0.95%/yr
BORROW_SPREAD = c5a.BORROW_SPREAD  # 0.50%/yr
DIV_COMP_1971 = 0.015            # 1971–1985 Nasdaq Composite 배당수익률 가정(문서화; 1.0/2.0 민감도)


# ── 배당 이중계상 정정 합성 ────────────────────────────────────────────────
def corrected_qld_closes(price_candles, dtb3, *, div_yield, expense=EXP_LEV,
                         spread=BORROW_SPREAD):
    """정정 2x: 가격수익률 2배 + 1× 배당 − 조달 − 보수.  (c5a 는 2× 배당 → 0.7%/yr 과대.)

    syn2 = synthetic_leveraged(가격, 2x) = 2·r_price − (rf+spread) − expense.  여기에 1× 배당
    (div_daily) 를 더해 실물 2x ETF(가격지수 2배 추종 + 배당 통과)를 근사한다.
    """
    syn2 = hd.synthetic_leveraged(price_candles, 2.0, annual_expense=expense,
                                  borrow_spread=spread, rf_candles=dtb3, rf_kind="yield")
    cl = [c.close for c in syn2]
    div_d = div_yield / 252.0
    out = [cl[0]]
    for i in range(1, len(cl)):
        r = (cl[i] / cl[i - 1] - 1.0) + div_d
        out.append(out[-1] * (1.0 + r))
    return out


def build_corrected_panel():
    """c5a 합성 패널을 그대로 만들되 QLD 만 배당 이중계상 정정으로 교체(그 외 전부 동일)."""
    panel = c5a.build_synthetic_panel()
    qld_corr = corrected_qld_closes(panel["ndx"], panel["dtb3"], div_yield=DIV_NDX)
    assert len(qld_corr) == len(panel["dates"]), "정정 QLD 길이 불일치"
    p = dict(panel)
    p["QLD"] = qld_corr
    return p


def build_price_panel(price_candles, dtb3, *, div_yield, lev_expense=EXP_LEV,
                      spread=BORROW_SPREAD, one_x_expense=EXP_1X):
    """가격지수(FRED) → 정정 합성 QQQ-1x / QLD-2x + σ/추세/현금.  OOS·스트레스 공용.

    QQQ-1x: 총수익(가격+배당) base 에 1x → 가격+배당−보수(실물 1x 와 일치, 정정 불필요).
    QLD-2x: 가격 2배 + 1× 배당 − 조달 − 보수(정정).
    """
    price = sorted(price_candles, key=lambda c: c.dt)
    tr = hd.index_total_return(price, div_yield)
    qqq = hd.synthetic_leveraged(tr, 1.0, annual_expense=one_x_expense, borrow_spread=0.0,
                                 rf_candles=dtb3, rf_kind="yield")
    dates = [c.dt for c in qqq]
    qqq_c = [c.close for c in qqq]
    qld_c = corrected_qld_closes(price, dtb3, div_yield=div_yield, expense=lev_expense,
                                 spread=spread)
    assert len(qld_c) == len(dates)
    return {"dates": dates, "QQQ": qqq_c, "QLD": qld_c,
            "sigma": c5a.ewma_vol_annual(qqq_c), "trend": c5a.trend_ok_series(qqq_c),
            "cash": c5a._yield_daily_aligned(dtb3, dates)}


# ── 시작일 분포 표(설계/홀드아웃 판정) ────────────────────────────────────────
def dist_table(panel, years, design_years, holdout_years, *, fast=False,
               modes=("glide", "static2")):
    """panel 에서 modes + B1 를 모든 시작일에 돌려 설계·홀드아웃 집계·판정 반환."""
    dates = panel["dates"]
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, years, fast=fast)
    if not starts:
        return None
    b1 = c5a.run_distribution(panel, years, "qqq", {}, starts)
    out = {"n_starts": len(starts),
           "first": dates[starts[0][0]].isoformat(), "last": dates[starts[-1][0]].isoformat()}
    for period, (lo, hi) in (("design", design_years), ("holdout", holdout_years)):
        b1p = c5a.split_by_startyear(b1, lo, hi)
        if not b1p:
            out[period] = None
            continue
        ab = c5a.aggregate(b1p, b1p)
        sec = {"b1": ab}
        for mode in modes:
            paths = c5a.run_distribution(panel, years, mode, {}, starts)
            pp = c5a.split_by_startyear(paths, lo, hi)
            a = c5a.aggregate(pp, b1p)
            a["decision"] = c5a.decide(a, ab)
            sec[mode] = a
        out[period] = sec
    return out


# ── 2) 정상(stationary) 부트스트랩 몬테카를로 ────────────────────────────────
def stationary_bootstrap_indices(n, length, mean_block, rng):
    """Politis–Romano 정상 부트스트랩: 길이 length 의 인덱스열(0..n-1, 랩어라운드)."""
    p = 1.0 / max(1.0, mean_block)
    idx = []
    i = rng.randrange(n)
    for _ in range(length):
        idx.append(i)
        if rng.random() < p:
            i = rng.randrange(n)
        else:
            i = (i + 1) % n
    return idx


def _synth_dates(n, start=date(1990, 1, 2)):
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bootstrap_mc(panel, *, years=20, n_paths=2000, mean_block=250, seed=20260928,
                 div_yield=DIV_NDX):
    """NDX 일 가격수익률(+정렬 현금)을 정상 부트스트랩 → 합성 20y 경로마다 glide/B1 DCA.

    각 경로: 정정 합성(가격 2배+1×배당) QQQ/QLD 를 부트스트랩 수익률로 재구성해 c5a.simulate.
    반환: glide/B1 최종자산 비 분포(median, P(glide<B1), P(glide<0.5B1), CVaR5, 문턱).
    """
    dates0 = panel["dates"]
    ndx = panel["ndx"]
    by = {c.dt: c.close for c in sorted(ndx, key=lambda c: c.dt)}
    seq = [by[d] for d in dates0]
    r_price = [seq[i] / seq[i - 1] - 1.0 for i in range(1, len(seq))]
    cash_full = panel["cash"]                    # 일 현금율(연율/252), dates0 정렬
    cash_daily = cash_full[1:]                   # r_price 와 정렬(둘 다 t≥1)
    m = min(len(r_price), len(cash_daily))
    r_price, cash_daily = r_price[:m], cash_daily[:m]
    rng = random.Random(seed)
    L = 252 * years
    dts = _synth_dates(L + 1)
    div_d = div_yield / 252.0
    exp1_d = EXP_1X / 252.0
    expL_d = EXP_LEV / 252.0
    spr_d = BORROW_SPREAD / 252.0
    ratios = []
    gl_term, b1_term = [], []
    for _ in range(n_paths):
        idx = stationary_bootstrap_indices(m, L, mean_block, rng)
        qqq = [100.0]
        qld = [100.0]
        cashp = [0.0]
        for j in idx:
            rp = r_price[j]
            rf = cash_daily[j]
            qqq.append(qqq[-1] * (1.0 + rp + div_d - exp1_d))
            r_l = 2.0 * rp + div_d - (rf + spr_d) - expL_d
            qld.append(qld[-1] * (1.0 + r_l))
            cashp.append(rf)
        closes = {"QQQ": qqq, "QLD": qld}
        sig = c5a.ewma_vol_annual(qqq)
        trd = c5a.trend_ok_series(qqq)
        rg = c5a.simulate(closes, dts, 0, len(dts), "glide", sigma=sig, trend=trd, cash=cashp)
        rb = c5a.simulate(closes, dts, 0, len(dts), "qqq", sigma=sig, trend=trd, cash=cashp)
        if rb.final_value > 0:
            ratios.append(rg.final_value / rb.final_value)
            gl_term.append(rg.final_value)
            b1_term.append(rb.final_value)
    ratios.sort()
    n = len(ratios)
    k5 = max(1, int(math.ceil(0.05 * n)))
    cvar5 = sum(ratios[:k5]) / k5
    return {
        "n_paths": n, "years": years, "mean_block": mean_block,
        "ratio_median": c5a._pct(ratios, 0.5),
        "ratio_p05": c5a._pct(ratios, 0.05),
        "ratio_p25": c5a._pct(ratios, 0.25),
        "ratio_p75": c5a._pct(ratios, 0.75),
        "ratio_mean": sum(ratios) / n,
        "p_glide_lt_b1": sum(1 for x in ratios if x < 1.0) / n,
        "p_glide_lt_half_b1": sum(1 for x in ratios if x < 0.5) / n,
        "p_ratio_lt_115": sum(1 for x in ratios if x < 1.15) / n,
        "cvar5_ratio": cvar5,
        "glide_median_term": c5a._pct(sorted(gl_term), 0.5),
        "b1_median_term": c5a._pct(sorted(b1_term), 0.5),
    }


def effective_sample(design_years, holdout_years, horizon, data_span_years):
    """겹침 시작일의 유효 독립표본(비겹침 창 수 근사)."""
    return {
        "design_span_years": design_years[1] - design_years[0] + 1,
        "holdout_span_years": holdout_years[1] - holdout_years[0] + 1,
        "horizon": horizon,
        "eff_nonoverlap_total": round(data_span_years / horizon, 2),
    }


# ── 5) 행동재무: 달러낙폭 시점/규모 ──────────────────────────────────────────
def behavioral_paths(panel, years, mode, *, fast=False,
                     design_years=(1986, 1999), holdout_years=(2000, 2016)):
    """각 시작일 경로의 최악 달러낙폭·시점(적립 대비)·언더워터.  P(달러낙폭>50%)."""
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, years, fast=fast)
    rows = []
    for (s, e) in starts:
        res = c5a.simulate(closes, dates, s, e + 1, mode, sigma=panel["sigma"],
                           trend=panel["trend"], cash=panel["cash"])
        ddd = R.dollar_drawdown(res.equity, res.deposit_series)
        if not ddd:
            continue
        trough = min(range(len(ddd)), key=lambda i: ddd[i])
        dep_cum = [0.0] * len(res.deposit_series)
        run = 0.0
        for i, dv in enumerate(res.deposit_series):
            run += dv
            dep_cum[i] = run
        uw = sum(1 for x in ddd if x < -1e-9) / len(ddd)
        rows.append({
            "start_year": dates[s].year,
            "worst_dd": min(ddd),
            "trough_frac_of_horizon": trough / max(1, len(ddd) - 1),
            "deposits_at_trough": dep_cum[trough],
            "deposits_total": dep_cum[-1],
            "dep_frac_at_trough": dep_cum[trough] / max(1e-9, dep_cum[-1]),
            "underwater": uw,
        })
    out = {}
    for period, (lo, hi) in (("design", design_years), ("holdout", holdout_years)):
        sub = [r for r in rows if lo <= r["start_year"] <= hi]
        if not sub:
            out[period] = None
            continue
        dds = sorted(r["worst_dd"] for r in sub)
        out[period] = {
            "n": len(sub),
            "worst_dd": dds[0],
            "median_worst_dd": c5a._pct(dds, 0.5),
            "p_dd_gt_50": sum(1 for r in sub if r["worst_dd"] < -0.50) / len(sub),
            "p_dd_gt_70": sum(1 for r in sub if r["worst_dd"] < -0.70) / len(sub),
            "median_underwater": c5a._pct(sorted(r["underwater"] for r in sub), 0.5),
            "median_trough_frac": c5a._pct(sorted(r["trough_frac_of_horizon"] for r in sub), 0.5),
            "median_dep_frac_at_trough": c5a._pct(
                sorted(r["dep_frac_at_trough"] for r in sub), 0.5),
        }
    return out


# ── 실물 QLD 재검증(정정 vs 현행) ────────────────────────────────────────────
def revalidate_qld(panel):
    """정정 합성 QLD 를 실물 QLD 와 재비교(현행 이중계상 대비 얼마나 달라지나)."""
    ndx = panel["ndx"]
    dtb3 = panel["dtb3"]
    try:
        real = hd.load_symbol("QLD")
    except Exception as ex:  # noqa: BLE001
        return {"error": f"{type(ex).__name__}: {ex}"}
    base16 = [c for c in ndx if c.dt >= date(2016, 9, 22)]
    # 현행(c5a): 2×TR
    tr16 = hd.index_total_return(base16, DIV_NDX)
    cur = hd.validate_synthetic(tr16, real, 2.0, annual_expense=EXP_LEV,
                                borrow_spread=BORROW_SPREAD, rf_candles=dtb3, rf_kind="yield")
    # 정정: 2×가격 + 1×배당 → 실물 대비 CAGR
    corr = corrected_qld_closes(base16, dtb3, div_yield=DIV_NDX)
    dts = [c.dt for c in sorted(base16, key=lambda c: c.dt)]
    real_by = {c.dt: c.close for c in real}
    common = [d for d in dts if d in real_by]
    corr_by = {d: v for d, v in zip(dts, corr)}
    days = (common[-1] - common[0]).days if len(common) > 1 else 1
    cagr_corr = (corr_by[common[-1]] / corr_by[common[0]]) ** (365.25 / max(days, 1)) - 1.0
    cagr_real = (real_by[common[-1]] / real_by[common[0]]) ** (365.25 / max(days, 1)) - 1.0
    return {"current_corr": cur.get("corr"), "current_cagr_syn": cur.get("cagr_syn"),
            "cagr_real": cur.get("cagr_real"),
            "corrected_cagr_syn": cagr_corr, "corrected_cagr_real_check": cagr_real,
            "current_minus_real_pp": (cur.get("cagr_syn", 0) - cur.get("cagr_real", 0)) * 100,
            "corrected_minus_real_pp": (cagr_corr - cagr_real) * 100}


# ── OOS 원장 로깅(peek-once) ──────────────────────────────────────────────────
def log_oos_ledger(panel, ledger_path):
    """glide OOS(1971–1985 가격) 단위자본 스트림을 원장에 적재.  design=1971–1990, holdout=1991+."""
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    idea = "c6a_lc_glide_oos1971"
    params = {"mode": "glide", "rep": "qqq_qld_mix_corrected", "s_star": c5a.S_STAR,
              "disc_real": c5a.DISC_REAL, "div_assumed": DIV_COMP_1971, "cash": "dtb3",
              "source": "FRED_NASDAQCOM", "div_dbl_count": "corrected"}
    di1 = bisect.bisect_right(dates, date(1990, 12, 31))
    res_d = c5a.simulate(closes, dates, 0, di1, "glide", sigma=panel["sigma"],
                         trend=panel["trend"], cash=panel["cash"])
    um_d = gate_eval.unit_capital_metrics(res_d.inv_ret[1:], dates=res_d.dates[1:])
    gate_eval.log_evaluation(idea, dict(params), 1, "design", um_d,
                             window=(res_d.dates[0], res_d.dates[-1]), ledger_path=ledger_path)
    logged = {"design": um_d}
    if not gate.already_peeked(ledger_path, idea):
        hi0 = bisect.bisect_left(dates, date(1991, 1, 1))
        res_h = c5a.simulate(closes, dates, hi0, len(dates), "glide", sigma=panel["sigma"],
                             trend=panel["trend"], cash=panel["cash"])
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

    res = {"meta": {"generated": date.today().isoformat(), "fast": args.fast}}

    # 0) 헤드라인 재현
    print("[c6a] 합성 패널(c5a) + 배당정정 패널 구축...")
    panel = c5a.build_synthetic_panel()
    cpanel = build_corrected_panel()

    print("[c6a] (1) 헤드라인 재현(현행 이중계상)...")
    rep = dist_table(panel, 20, c5a.DESIGN_START_YEARS, c5a.HOLDOUT_START_YEARS, fast=args.fast)
    res["reproduce_20y_current"] = rep

    print("[c6a] (1b) 배당 이중계상 정정 후 재판정...")
    res["corrected_20y"] = dist_table(cpanel, 20, c5a.DESIGN_START_YEARS,
                                      c5a.HOLDOUT_START_YEARS, fast=args.fast)
    res["corrected_10y"] = dist_table(cpanel, 10, c5a.DESIGN_START_YEARS,
                                      c5a.HOLDOUT_START_YEARS, fast=args.fast)
    res["qld_revalidation"] = revalidate_qld(panel)

    # 2) 유효표본 + 부트스트랩 MC
    print(f"[c6a] (2) 정상 부트스트랩 MC({args.mc_paths} 경로, 정정합성)...")
    res["effective_sample_20y"] = effective_sample(c5a.DESIGN_START_YEARS,
                                                   c5a.HOLDOUT_START_YEARS, 20, 40)
    res["mc_20y"] = bootstrap_mc(panel, years=20, n_paths=args.mc_paths)
    res["mc_10y"] = bootstrap_mc(panel, years=10, n_paths=args.mc_paths)

    # 3) OOS 1971–1985
    print("[c6a] (3) OOS NASDAQCOM 1971–1985...")
    ndxcom = hd.load_fred("NASDAQCOM")
    dtb3 = hd.load_fred("DTB3")
    oos = {}
    for tag, dv in (("div1.5", 0.015), ("div1.0", 0.010), ("div2.0", 0.020)):
        cp = build_price_panel(ndxcom, dtb3, div_yield=dv)
        oos[tag] = {
            "20y": dist_table(cp, 20, (1971, 1978), (1979, 1985), fast=args.fast),
            "10y": dist_table(cp, 10, (1971, 1980), (1981, 1985), fast=args.fast),
        }
    res["oos_1971"] = oos
    oos_panel = build_price_panel(ndxcom, dtb3, div_yield=DIV_COMP_1971)
    if not args.no_ledger:
        print("[c6a] (3b) OOS 원장 적재(peek-once)...")
        res["oos_ledger"] = log_oos_ledger(oos_panel, args.ledger)

    # 4) 스트레스
    print("[c6a] (4) 스트레스 시나리오...")
    stress = {}
    # (a) Nikkei225 OOS (index path only; rf≈0.5%)
    try:
        nik = hd.load_fred("NIKKEI225")
        nik_dtb3 = [hd.Candle("RF", c.dt, 0.5, 0.5, 0.5, 0.5, 0.0) for c in nik]  # rf≈0.5%
        npan = build_price_panel(nik, nik_dtb3, div_yield=DIV_COMP_1971)
        stress["nikkei_20y"] = dist_table(npan, 20, (1970, 1990), (1991, 2006), fast=args.fast)
        stress["nikkei_first"] = npan["dates"][0].isoformat()
        stress["nikkei_last"] = npan["dates"][-1].isoformat()
    except Exception as ex:  # noqa: BLE001
        stress["nikkei_error"] = f"{type(ex).__name__}: {ex}"
    # (b) 조달 +2% (정정합성)
    hp = build_price_panel(panel["ndx"], panel["dtb3"], div_yield=DIV_NDX,
                           spread=BORROW_SPREAD + 0.02)
    stress["financing_plus2pct_20y"] = dist_table(hp, 20, c5a.DESIGN_START_YEARS,
                                                  c5a.HOLDOUT_START_YEARS, fast=args.fast)
    # (c) 2x 보수 1.5%
    ep = build_price_panel(panel["ndx"], panel["dtb3"], div_yield=DIV_NDX, lev_expense=0.015)
    stress["expense_1p5_20y"] = dist_table(ep, 20, c5a.DESIGN_START_YEARS,
                                           c5a.HOLDOUT_START_YEARS, fast=args.fast)
    # (d) 5년 후 적립중단 — 정정패널에서 glide vs B1 최종 비교
    stress["contrib_stop_5y"] = contrib_stop(cpanel, fast=args.fast)
    res["stress"] = stress

    # 5) 행동재무(정정패널)
    print("[c6a] (5) 행동재무(달러낙폭 분포·시점)...")
    res["behavioral_glide_20y"] = behavioral_paths(cpanel, 20, "glide", fast=args.fast)
    res["behavioral_static2_20y"] = behavioral_paths(cpanel, 20, "static2", fast=args.fast)

    RESULTS_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
    print(f"[c6a] 결과 JSON → {RESULTS_JSON}")
    write_report(res)
    print(f"[c6a] 리포트 → {REPORT}")
    return res


def contrib_stop(panel, *, fast=False, stop_year=5):
    """5년 적립 후 중단(실직).  glide vs B1 각 시작일 최종·최종비 — glide 가 더 나빠지나?"""
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, 20, fast=fast)
    stop_months = stop_year * 12
    ratios = []
    gl_final, b1_final = [], []
    for (s, e) in starts:
        if dates[s].year < 1986 or dates[s].year > 1999:
            continue
        gf = simulate_contrib_stop(closes, dates, s, e + 1, "glide", panel, stop_months)
        bf = simulate_contrib_stop(closes, dates, s, e + 1, "qqq", panel, stop_months)
        if bf > 0:
            ratios.append(gf / bf)
            gl_final.append(gf)
            b1_final.append(bf)
    if not ratios:
        return None
    ratios.sort()
    return {"n": len(ratios), "ratio_median": c5a._pct(ratios, 0.5),
            "ratio_p05": c5a._pct(ratios, 0.05),
            "p_glide_lt_b1": sum(1 for x in ratios if x < 1.0) / len(ratios),
            "glide_median_final": c5a._pct(sorted(gl_final), 0.5),
            "b1_median_final": c5a._pct(sorted(b1_final), 0.5)}


def simulate_contrib_stop(closes, dates, i0, i1, mode, panel, stop_months):
    """c5a.simulate 를 그대로 반영하되, 월 index >= stop_months 부터 적립을 0 으로.

    c5a.simulate 의 드리프트/입금/월결정/매수·매도 규약을 동일하게 미러링한다(감사 헬퍼;
    c5a 미수정).  중단 후에도 잔여 자산은 시장·목표비중대로 계속 운용된다.
    """
    qqq = closes["QQQ"]
    qld = closes["QLD"]
    sigma = panel["sigma"]
    trend = panel["trend"]
    cash = panel["cash"]
    sub_dates = dates[i0:i1]
    starts = R.month_start_flags(sub_dates)
    val_q = val_l = csh = 0.0
    month_m = -1
    eq = 0.0
    for k in range(i1 - i0):
        t = i0 + k
        if k > 0:
            csh *= (1.0 + (cash[t] if cash is not None else 0.0))
            if qqq[t - 1] > 0:
                val_q *= qqq[t] / qqq[t - 1]
            if qld[t - 1] > 0:
                val_l *= qld[t] / qld[t - 1]
        dep = 0.0
        if k == 0:
            dep += c5a.INITIAL
        if starts[k]:
            month_m += 1
            if month_m < stop_months:
                dep += c5a.MONTHLY
        if dep > 0:
            csh += dep
            csh -= dep * c5a.FX_BPS * 1e-4
        if starts[k]:
            equity_ref = val_q + val_l + csh
            E = c5a.target_exposure(month_m, equity_ref, sigma[t], trend[t], mode)
            w_qqq, w_qld = c5a.exposure_to_weights(E)
            st = c5a._State(val_q, val_l, csh)
            c5a._buy_toward(equity_ref, w_qqq, w_qld, qqq[t], qld[t], st)
            val_q, val_l, csh = st.q, st.l, st.c
            eq_now = val_q + val_l + csh
            if eq_now > 0:
                E_act = (2.0 * val_l + val_q) / eq_now
                if E_act > E + c5a.SELL_BAND:
                    val_q, val_l, csh, _c, _n = c5a._rebalance_to(
                        eq_now, w_qqq, w_qld, val_q, val_l, csh, qqq[t], qld[t])
        eq = val_q + val_l + csh
    return eq


# ── 리포트 ────────────────────────────────────────────────────────────────────
def _dec(a):
    return a.get("decision", {}).get("verdict", "—") if a else "—"


def _mrow(name, a, b1):
    if not a or not b1:
        return f"| {name} | — | — | — | — |"
    return (f"| {name} | ${a['median']:,.0f} ({a['median']/b1['median']:.2f}×) | "
            f"${a['p5']:,.0f} ({a['p5']/max(1e-9,b1['p5']):.2f}×) | {a['regret']:.2f} | "
            f"**{_dec(a)}** |")


def _sec_table(title, tab, modes=("glide", "static2")):
    L = [f"### {title}"]
    if not tab:
        L.append("(데이터 부족/없음)")
        return L
    L.append(f"시작일 {tab.get('first')}…{tab.get('last')} · n_starts={tab.get('n_starts')}")
    for period in ("design", "holdout"):
        sec = tab.get(period)
        if not sec:
            L.append(f"- {period}: (표본 없음)")
            continue
        b1 = sec["b1"]
        L.append(f"**{period}** — B1 median ${b1['median']:,.0f} · p5 ${b1['p5']:,.0f} · "
                 f"regret {b1['regret']:.2f} · n={b1['n']}")
        L.append("| 전략 | median(×B1) | p5(×B1) | regret | 판정 |")
        L.append("|---|---:|---:|---:|:--:|")
        for m in modes:
            if m in sec:
                L.append(_mrow(m, sec[m], b1))
    return L


def write_report(res):
    m = res["meta"]
    L = ["# Cycle 6 · c6a — Adversarial audit of c5a_lc_glide (lifecycle leverage)", ""]
    L.append(f"> 실행 {m['generated']} · fast={m['fast']} · 감사자(executor) · "
             "src/·c5a 무수정, import 만.")
    L.append("")
    L.append("본 문서는 c6a 스크립트가 결과를 자동 append 한다. 상단 서술/판정은 수기.")
    L.append("<!-- RESULTS_BELOW -->")
    L.append("")

    L.append("## 1. 재현 + 배당 이중계상 정정")
    L += _sec_table("1a. 헤드라인 재현(현행, 20y)", res.get("reproduce_20y_current"))
    L.append("")
    L += _sec_table("1b. 배당정정 후(20y)", res.get("corrected_20y"))
    L += _sec_table("1c. 배당정정 후(10y)", res.get("corrected_10y"))
    rv = res.get("qld_revalidation", {})
    if rv and "error" not in rv:
        L.append("")
        L.append(f"**실물 QLD 재검증(2016–2026):** 현행 CAGR합성 {rv['current_cagr_syn']*100:.1f}% "
                 f"vs 실물 {rv['cagr_real']*100:.1f}% (차 {rv['current_minus_real_pp']:+.2f}%p) · "
                 f"정정 CAGR합성 {rv['corrected_cagr_syn']*100:.1f}% "
                 f"(차 {rv['corrected_minus_real_pp']:+.2f}%p). "
                 "→ 현행 이중계상은 실물과 우연히 잘 맞고, 정정은 보수적으로 언더슈트.")
    L.append("")

    L.append("## 2. 통계적 정직성(부트스트랩 MC)")
    es = res.get("effective_sample_20y", {})
    L.append(f"겹침 시작일 유효표본: 20y 지평·40y 데이터 → **비겹침 독립창 ≈ "
             f"{es.get('eff_nonoverlap_total')}**. n=168/82 는 착시.")
    for key, lab in (("mc_20y", "20년"), ("mc_10y", "10년")):
        mc = res.get(key)
        if not mc:
            continue
        L.append("")
        L.append(f"**{lab} 지평 · 정상 부트스트랩({mc['n_paths']} 경로, 평균블록 {mc['mean_block']}일, "
                 "배당정정):**")
        L.append("| 지표 | 값 |")
        L.append("|---|---:|")
        L.append(f"| glide/B1 비 median | {mc['ratio_median']:.3f} |")
        L.append(f"| 비 p05 / p25 / p75 | {mc['ratio_p05']:.3f} / {mc['ratio_p25']:.3f} / "
                 f"{mc['ratio_p75']:.3f} |")
        L.append(f"| P(glide < B1) | {mc['p_glide_lt_b1']:.3f} |")
        L.append(f"| P(glide < 0.5×B1) | {mc['p_glide_lt_half_b1']:.3f} |")
        L.append(f"| P(비 < 1.15) | {mc['p_ratio_lt_115']:.3f} |")
        L.append(f"| CVaR5(비) | {mc['cvar5_ratio']:.3f} |")
    L.append("")

    L.append("## 3. NEW OOS — NASDAQCOM 1971–1985(한번도 안 쓴 시작일)")
    oos = res.get("oos_1971", {})
    for tag, lab in (("div1.5", "배당 1.5%(기준)"), ("div1.0", "배당 1.0%"), ("div2.0", "배당 2.0%")):
        blk = oos.get(tag, {})
        L.append(f"### 배당 가정 {lab}")
        L += _sec_table("20y", blk.get("20y"))
        L += _sec_table("10y", blk.get("10y"))
        L.append("")
    lg = res.get("oos_ledger")
    if lg:
        d = lg.get("design", {})
        L.append(f"**원장 c6a_lc_glide_oos1971:** design CAGR {d.get('cagr',0)*100:.1f}% · "
                 f"MDD {d.get('max_drawdown',0)*100:.0f}% · Sharpe {d.get('sr_annual',0):.2f} · "
                 f"holdout={'적재' if isinstance(lg.get('holdout'), dict) else lg.get('holdout')}.")
    L.append("")

    L.append("## 4. 스트레스 시나리오")
    st = res.get("stress", {})
    if "nikkei_error" not in st:
        L.append(f"### (a) Nikkei225 국제 OOS ({st.get('nikkei_first')}…{st.get('nikkei_last')})")
        L += _sec_table("Nikkei 20y", st.get("nikkei_20y"))
    else:
        L.append(f"### (a) Nikkei225 — {st['nikkei_error']}")
    L.append("")
    L += _sec_table("(b) 조달 +2% (20y)", st.get("financing_plus2pct_20y"))
    L += _sec_table("(c) 2x 보수 1.5% (20y)", st.get("expense_1p5_20y"))
    cs = st.get("contrib_stop_5y")
    if cs:
        L.append("")
        L.append(f"### (d) 5년 후 적립중단(실직)")
        L.append(f"glide/B1 최종비 median {cs['ratio_median']:.2f} · p05 {cs['ratio_p05']:.2f} · "
                 f"P(glide<B1) {cs['p_glide_lt_b1']:.2f} (n={cs['n']}). "
                 "→ 적립중단해도 glide 가 B1 을 하회하지 않으면 '더 나빠지지 않음'.")
    L.append("")

    L.append("## 5. 행동재무(달러낙폭 시점·규모)")
    for key, lab in (("behavioral_glide_20y", "glide"), ("behavioral_static2_20y", "static2")):
        bh = res.get(key, {})
        for period in ("design", "holdout"):
            b = bh.get(period)
            if not b:
                continue
            L.append(f"- **{lab}/{period}**(n={b['n']}): 최악 달러낙폭 {b['worst_dd']:.2f} · "
                     f"중앙 최악낙폭 {b['median_worst_dd']:.2f} · P(낙폭>50%) {b['p_dd_gt_50']:.2f} · "
                     f"P(낙폭>70%) {b['p_dd_gt_70']:.2f} · 중앙 언더워터 {b['median_underwater']:.2f} · "
                     f"최악낙폭 시점(지평비) {b['median_trough_frac']:.2f} · "
                     f"그 시점 적립비율 {b['median_dep_frac_at_trough']:.2f}.")
    L.append("")
    L.append("## 6. 판정")
    L.append("(수기 판정은 리포트 상단/하단 서술 참조 — CONFIRMED/WEAKENED/REFUTED.)")

    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            head = prev.split("<!-- RESULTS_BELOW -->")[0].rstrip()
            REPORT.write_text(head + "\n\n<!-- RESULTS_BELOW -->\n\n" +
                              "\n".join(L[L.index("<!-- RESULTS_BELOW -->") + 2:]) + "\n",
                              encoding="utf-8")
            return
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
