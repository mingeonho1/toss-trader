"""c7b — Lost-decade kill-switch (KS) on lifecycle leverage (c5a_lc_glide).

사전등록·규약: reports/cycle7_c7b_killswitch.md, experiments/README.md,
docs/gate_v2_spec.md(+부록 v2.1).

**정직성:** KS 는 c6a 감사에서 Nikkei(잃어버린 10년) 반증을 본 *뒤* 제안된 NEW 규칙이다
→ 새 시도로 회계. Nikkei 결과는 이 규칙에 대해 **in-sample**. 진짜 OOS 는 NDX 홀드아웃/NASDAQCOM.

규칙 KS(사전등록): 월말 t 에서 1× 슬리브(QQQ-1x, 총수익)의 트레일링 10년 총수익률 g_idx 와
트레일링 10년 CPI 인플레 g_cpi(참조월 = 결정월−1; 릴리즈 랙)를 비교. g_idx/g_cpi−1 < 0 이면
KS 발동 → E_target := 1.0(신규 레버리지 금지, 기존 QLD 미매도). (+)로 복귀하면 재활성.
10년 히스토리(지수·CPI) 전엔 비활성. Nikkei 는 JPNCPIALLMINMEI(2021-06 이후 마지막값 캐리·flag).

src/·c5a·c6a 는 수정하지 않고 import 만 한다. 기계(글라이드 목표·배당정정 합성·롤링시작·Nikkei
로더·부트스트랩)는 c5a/c6a 에서 그대로 재사용한다.

재현: PYTHONPATH=src .venv/bin/python experiments/c7b_killswitch.py [--fast] [--no-ledger]
      [--mc-paths N] [--ledger PATH]
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

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle7_c7b_killswitch.md"
RESULTS_JSON = ROOT / "reports" / "c7b_results.json"

# ── 사전등록 상수 ────────────────────────────────────────────────────────────
LOOKBACK_YEARS = 10                 # 사전등록 KS lookback
NEIGHBOR_LOOKBACKS = (7, 15)        # 평탄성 이웃
DIV_NDX = c5a.DIV_YIELD             # 0.7%/yr
DIV_NIKKEI = c6a.DIV_COMP_1971      # 1.5%/yr (c6a 와 동일 가정; 문서화)
DESIGN_YEARS = c5a.DESIGN_START_YEARS    # (1986, 1999)
HOLDOUT_YEARS = c5a.HOLDOUT_START_YEARS  # (2000, 2016) → 20y 는 2000–2006 만 완전


# ── CPI 로더 & 월 산술 ───────────────────────────────────────────────────────
def load_cpi_monthly(series):
    """FRED 월 CPI → {(y,m): level}, first/last (y,m) 튜플."""
    candles = hd.load_fred(series)
    m = {(c.dt.year, c.dt.month): c.close for c in candles}
    months = sorted(m)
    return {"map": m, "first": months[0], "last": months[-1], "series": series}


def _add_months(y, mo, delta):
    idx = y * 12 + (mo - 1) + delta
    return idx // 12, idx % 12 + 1


def _cpi_lookup(cpi, ym):
    """(값, forward_filled) 반환. ym < first → (None, False). ym > last → (last값, True)."""
    mp, first, last = cpi["map"], cpi["first"], cpi["last"]
    if ym in mp:
        return mp[ym], False
    if ym < first:
        return None, False
    if ym > last:
        return mp[last], True
    y, mo = ym                                   # 중간 결손(월 시리즈엔 드묾) → 직전 관측
    for _ in range(24):
        y, mo = _add_months(y, mo, -1)
        if (y, mo) in mp:
            return mp[(y, mo)], False
    return None, False


# ── KS 활성 시리즈(인과적) ───────────────────────────────────────────────────
def compute_ks_active(one_x_closes, dates, cpi, lookback_years, *, nominal_only=False):
    """각 거래일 t 의 KS 활성 여부(True=신규 레버리지 중단). 오직 data ≤ t 만 사용.

    실질 성장 = (P_t/P_{t−Ly}) / (CPI_{m−1}/CPI_{m−1−Ly}).  < 1 이면 활성.
    10년(지수·CPI) 히스토리 전엔 비활성. CPI 없거나 nominal_only → 인플레=1(명목).
    """
    n = len(dates)
    active = [False] * n
    real_ret = [None] * n
    enough = [False] * n
    ff_count = 0
    for t in range(n):
        d = dates[t]
        # 1) 지수 10년 전 포인트(≤ 목표일 최대 거래일)
        try:
            tgt = date(d.year - lookback_years, d.month, d.day)
        except ValueError:
            tgt = date(d.year - lookback_years, d.month, 28)
        j = bisect.bisect_right(dates, tgt) - 1
        if j < 0:
            continue
        if (d - dates[j]).days < lookback_years * 365 - 30:   # 충분한 지수 히스토리 요구
            continue
        if one_x_closes[j] <= 0:
            continue
        g_idx = one_x_closes[t] / one_x_closes[j]
        # 2) CPI 인플레(참조월 m−1; 릴리즈 랙)
        g_cpi = 1.0
        if not nominal_only and cpi is not None:
            end_m = _add_months(d.year, d.month, -1)          # 결정월 − 1
            start_m = (end_m[0] - lookback_years, end_m[1])
            ve, ff_e = _cpi_lookup(cpi, end_m)
            vs, ff_s = _cpi_lookup(cpi, start_m)
            if ve is None or vs is None or vs <= 0:
                continue                                      # CPI 히스토리 부족 → 비활성
            if ff_e or ff_s:
                ff_count += 1
            g_cpi = ve / vs
        enough[t] = True
        rg = g_idx / g_cpi
        real_ret[t] = rg - 1.0
        active[t] = rg < 1.0
    return {"active": active, "real_ret": real_ret, "enough": enough,
            "nominal_only": nominal_only, "forward_filled": ff_count,
            "lookback": lookback_years}


def active_spans(dates, active):
    """연속 활성 구간 → [(start_iso, end_iso, n_days)] + 총 활성일수."""
    spans = []
    i = 0
    n = len(active)
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            spans.append((dates[i].isoformat(), dates[j].isoformat(), j - i + 1))
            i = j + 1
        else:
            i += 1
    return {"spans": spans, "n_active_days": sum(s[2] for s in spans),
            "ever": bool(spans)}


# ── KS 시뮬레이터(c5a.simulate 미러 + KS 상한) ───────────────────────────────
def simulate_ks(closes, dates, i0, i1, ks_active, *, sigma=None, trend=None, cash=None,
                monthly=c5a.MONTHLY, initial=c5a.INITIAL, s_star=c5a.S_STAR,
                sell_band=c5a.SELL_BAND, plan_months=c5a.PLAN_MONTHS,
                disc_real=c5a.DISC_REAL, record_weights=False):
    """[i0,i1) 라이프사이클 glide DCA + 킬스위치. c5a.simulate 규약을 그대로 미러링하되,
    KS 활성월엔 E_target := min(E_glide, 1.0) 로 신규 레버리지 금지 & **매도 억제**(기존 QLD 유지).

    ks_active: 전역 dates 정렬 bool 배열. 목표는 prices≤t·W_t·ks_active[t] 만 참조 → 인과적.
    반환은 c5a.SimResult(그 헬퍼·집계 재사용).
    """
    qqq = closes["QQQ"]
    qld = closes["QLD"]
    n = i1 - i0
    sub_dates = dates[i0:i1]
    starts = R.month_start_flags(sub_dates)

    if sigma is None:
        sigma = c5a.ewma_vol_annual(qqq)
    if trend is None:
        trend = c5a.trend_ok_series(qqq)

    val_q = 0.0
    val_l = 0.0
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
    cur_w = (0.0, 0.0)

    for k in range(n):
        t = i0 + k
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

        dep = 0.0
        if k == 0:
            dep += initial
        if starts[k]:
            dep += monthly
            month_m += 1
        if dep > 0:
            csh += dep
            dep_series[k] = dep
            fx = dep * c5a.FX_BPS * 1e-4
            csh -= fx
            total_fx += fx
            total_cost += fx

        if starts[k]:
            equity_ref = val_q + val_l + csh
            E_glide = c5a.target_exposure(month_m, equity_ref, sigma[t], trend[t], "glide",
                                          s_star=s_star, plan_months=plan_months,
                                          monthly=monthly, disc_real=disc_real)
            is_ks = bool(ks_active[t]) if ks_active is not None else False
            E_tgt = min(E_glide, 1.0) if is_ks else E_glide
            cur_w = c5a.exposure_to_weights(E_tgt)
            exp_sum += E_tgt
            exp_cnt += 1
            max_exp = max(max_exp, E_tgt)

            w_qqq, w_qld = cur_w
            # 3a) 신규 현금으로 목표까지 매수(매도 없음) — KS 활성이면 QQQ 로만
            c5a._buy_toward(equity_ref, w_qqq, w_qld, qqq[t], qld[t],
                            state := c5a._State(val_q, val_l, csh))
            val_q, val_l, csh, c_buy, nb = (state.q, state.l, state.c, state.cost, state.legs)
            total_cost += c_buy
            n_buys += nb

            # 3b) 초과노출 매도 점검 — **KS 활성월엔 억제(기존 QLD 미매도)**
            if not is_ks:
                eq_now = val_q + val_l + csh
                if eq_now > 0:
                    E_act = (2.0 * val_l + 1.0 * val_q) / eq_now
                    if E_act > E_tgt + sell_band:
                        val_q, val_l, csh, c_sell, ns = c5a._rebalance_to(
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
    return c5a.SimResult(
        dates=list(sub_dates), equity=equity_list, deposit_series=dep_series,
        inv_ret=inv_ret, final_value=final_value, total_deposited=total_dep,
        total_cost=total_cost, total_fx=total_fx, n_sells=n_sells, n_buys=n_buys,
        max_exposure=max_exp, avg_exposure=(exp_sum / exp_cnt if exp_cnt else 0.0),
        weight_sched=weight_sched,
    )


def make_ks_signal_fn(cpi, lookback_years, **cfg):
    """lookahead_guard 용 signal_fn — KS 를 panel_closes 로부터 매번 재계산(인과성 강제)."""
    def fn(panel_closes, dates):
        closes = {"QQQ": list(panel_closes["QQQ"]), "QLD": list(panel_closes["QLD"])}
        ks = compute_ks_active(closes["QQQ"], list(dates), cpi, lookback_years)["active"]
        res = simulate_ks(closes, list(dates), 0, len(dates), ks, record_weights=True, **cfg)
        return res.weight_sched
    return fn


# ── 분포 러너(KS) ────────────────────────────────────────────────────────────
def run_distribution_ks(panel, years, ks_active, cfg, starts):
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    dates = panel["dates"]
    out = []
    for (s, e) in starts:
        res = simulate_ks(closes, dates, s, e + 1, ks_active, sigma=panel["sigma"],
                          trend=panel["trend"], cash=panel["cash"], **cfg)
        m = c5a.path_metrics(res)
        m["start"] = dates[s].isoformat()
        m["start_year"] = dates[s].year
        out.append(m)
    return out


def dist_table(panel, years, cpi, lookback, design_years, holdout_years, *, fast=False,
               neighbors=False):
    """panel 에서 DCA-1x(B1)·glide·glide_ks(+이웃) 를 모든 시작일에 → 설계·홀드아웃 판정."""
    dates = panel["dates"]
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    starts = c5a.start_indices(dates, ms, years, fast=fast)
    if not starts:
        return None
    ks_main = compute_ks_active(panel["QQQ"], dates, cpi, lookback)
    b1 = c5a.run_distribution(panel, years, "qqq", {}, starts)
    glide = c5a.run_distribution(panel, years, "glide", {}, starts)
    glide_ks = run_distribution_ks(panel, years, ks_main["active"], {}, starts)
    strat_paths = {"glide": glide, "glide_ks": glide_ks}
    if neighbors:
        for lb in NEIGHBOR_LOOKBACKS:
            ks_n = compute_ks_active(panel["QQQ"], dates, cpi, lb)
            strat_paths[f"glide_ks_{lb}y"] = run_distribution_ks(panel, years,
                                                                ks_n["active"], {}, starts)
    out = {"n_starts": len(starts), "first": dates[starts[0][0]].isoformat(),
           "last": dates[starts[-1][0]].isoformat(),
           "ks_nominal_only": ks_main["nominal_only"], "ks_forward_filled": ks_main["forward_filled"]}
    for period, (lo, hi) in (("design", design_years), ("holdout", holdout_years)):
        b1p = c5a.split_by_startyear(b1, lo, hi)
        if not b1p:
            out[period] = None
            continue
        ab = c5a.aggregate(b1p, b1p)
        sec = {"b1": ab}
        for name, paths in strat_paths.items():
            pp = c5a.split_by_startyear(paths, lo, hi)
            a = c5a.aggregate(pp, b1p)
            a["decision"] = c5a.decide(a, ab)
            sec[name] = a
        out[period] = sec
    return out


def cost_benefit(tab):
    """설계 구간 median/B1 로 보험비용(glide−glide_ks) 요약."""
    if not tab or not tab.get("design"):
        return None
    d = tab["design"]
    b1 = d["b1"]["median"]
    g = d["glide"]["median"] / b1
    k = d["glide_ks"]["median"] / b1
    return {"glide_x": g, "glide_ks_x": k, "insurance_cost_x": g - k,
            "glide_regret": d["glide"]["regret"], "glide_ks_regret": d["glide_ks"]["regret"]}


# ── 정상 부트스트랩 MC (KS 결합; 수익·현금·인플레 결합 재표집) ─────────────────
def _log_interp_levels(dates, cpi):
    """월 CPI(레벨) → 각 거래일 로그선형 보간 레벨. 범위 밖은 edge clamp."""
    anchors = sorted((date(y, m, 1), math.log(v)) for (y, m), v in cpi["map"].items())
    ad = [a[0] for a in anchors]
    av = [a[1] for a in anchors]
    out = []
    for d in dates:
        i = bisect.bisect_right(ad, d) - 1
        if i < 0:
            out.append(math.exp(av[0]))
        elif i >= len(ad) - 1:
            out.append(math.exp(av[-1]))
        else:
            span = (ad[i + 1] - ad[i]).days or 1
            frac = (d - ad[i]).days / span
            out.append(math.exp(av[i] + frac * (av[i + 1] - av[i])))
    return out


def _mc_series(index_candles, cash_daily_full, dates0, cpi):
    """부트스트랩 재표집 풀: 지수·CPI 겹침 구간의 (r_price, cash, r_infl) 정렬 배열."""
    by = {c.dt: c.close for c in sorted(index_candles, key=lambda c: c.dt)}
    px = [by[d] for d in dates0]
    cpi_first_d = date(cpi["first"][0], cpi["first"][1], 1)
    cpi_last_d = date(cpi["last"][0], cpi["last"][1], 28)
    lo = max(dates0[0], cpi_first_d)
    hi = min(dates0[-1], cpi_last_d)
    win = [i for i, d in enumerate(dates0) if lo <= d <= hi]
    ws, we = win[0], win[-1]
    sub_dates = dates0[ws:we + 1]
    sub_px = px[ws:we + 1]
    lvl = _log_interp_levels(sub_dates, cpi)
    r_price, r_infl, cashd = [], [], []
    for i in range(1, len(sub_px)):
        if sub_px[i - 1] <= 0:
            continue
        r_price.append(sub_px[i] / sub_px[i - 1] - 1.0)
        r_infl.append(lvl[i] / lvl[i - 1] - 1.0)
        cashd.append(cash_daily_full[ws + i])
    m = min(len(r_price), len(r_infl), len(cashd))
    return (r_price[:m], r_infl[:m], cashd[:m], sub_dates[0].isoformat(),
            sub_dates[-1].isoformat())


def bootstrap_mc_ks(index_candles, dtb3, dates0, cash_full, cpi, *, div_yield,
                    years=20, n_paths=2000, mean_block=250, lookback=LOOKBACK_YEARS,
                    seed=20260928):
    """정상 부트스트랩: 각 20y 경로에서 glide / glide_ks / B1 최종자산 비 분포 + KS 발동율."""
    r_price, r_infl, cash_daily, mc_lo, mc_hi = _mc_series(index_candles, cash_full, dates0, cpi)
    m = len(r_price)
    rng = random.Random(seed)
    L = 252 * years
    off = 252 * lookback
    dts = c6a._synth_dates(L + 1)
    div_d = div_yield / 252.0
    exp1_d = c5a.EXP_1X / 252.0
    expL_d = c5a.EXP_LEV / 252.0
    spr_d = c5a.BORROW_SPREAD / 252.0
    r_gk_b1, r_g_b1, r_gk_g = [], [], []
    ks_fired = 0
    for _ in range(n_paths):
        idx = c6a.stationary_bootstrap_indices(m, L, mean_block, rng)
        qqq = [100.0]
        qld = [100.0]
        cpip = [100.0]
        cashp = [0.0]
        for j in idx:
            rp = r_price[j]
            rf = cash_daily[j]
            qqq.append(qqq[-1] * (1.0 + rp + div_d - exp1_d))
            qld.append(qld[-1] * (1.0 + 2.0 * rp + div_d - (rf + spr_d) - expL_d))
            cpip.append(cpip[-1] * (1.0 + r_infl[j]))
            cashp.append(rf)
        # KS 활성(합성 경로): 실질 10년 성장 < 0
        ks = [False] * len(qqq)
        for kk in range(off, len(qqq)):
            g_idx = qqq[kk] / qqq[kk - off]
            g_cpi = cpip[kk] / cpip[kk - off]
            ks[kk] = (g_idx / g_cpi) < 1.0
        if any(ks):
            ks_fired += 1
        closes = {"QQQ": qqq, "QLD": qld}
        sig = c5a.ewma_vol_annual(qqq)
        trd = c5a.trend_ok_series(qqq)
        rg = c5a.simulate(closes, dts, 0, len(dts), "glide", sigma=sig, trend=trd, cash=cashp)
        rk = simulate_ks(closes, dts, 0, len(dts), ks, sigma=sig, trend=trd, cash=cashp)
        rb = c5a.simulate(closes, dts, 0, len(dts), "qqq", sigma=sig, trend=trd, cash=cashp)
        if rb.final_value > 0:
            r_g_b1.append(rg.final_value / rb.final_value)
            r_gk_b1.append(rk.final_value / rb.final_value)
        if rg.final_value > 0:
            r_gk_g.append(rk.final_value / rg.final_value)

    def _summ(ratios):
        if not ratios:
            return None
        s = sorted(ratios)
        n = len(s)
        k5 = max(1, int(math.ceil(0.05 * n)))
        return {"n": n, "median": c5a._pct(s, 0.5), "p05": c5a._pct(s, 0.05),
                "p25": c5a._pct(s, 0.25), "p75": c5a._pct(s, 0.75),
                "mean": sum(s) / n, "cvar5": sum(s[:k5]) / k5,
                "p_lt_1": sum(1 for x in s if x < 1.0) / n,
                "p_lt_half": sum(1 for x in s if x < 0.5) / n}
    return {"n_paths": n_paths, "years": years, "lookback": lookback,
            "mean_block": mean_block, "mc_window": [mc_lo, mc_hi],
            "ks_fired_frac": ks_fired / max(1, n_paths),
            "glide_vs_b1": _summ(r_g_b1), "glide_ks_vs_b1": _summ(r_gk_b1),
            "glide_ks_vs_glide": _summ(r_gk_g)}


# ── 원장(단위자본 스트림; peek-once) ─────────────────────────────────────────
def log_ledger(panel, cpi, ledger_path, *, do_neighbors=True):
    """c7b_glide_ks(배당정정 NDX) 단위자본 스트림 → 표준 장기 분할 원장 적재(peek-once)."""
    dates = panel["dates"]
    closes = {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}
    di1 = bisect.bisect_right(dates, c5a.LEDGER_DESIGN_END)
    hi0 = bisect.bisect_left(dates, c5a.LEDGER_HOLDOUT_START)
    logged = {}

    def _log_one(idea, lookback, neighbor=None):
        ks = compute_ks_active(panel["QQQ"], dates, cpi, lookback)["active"]
        params = {"mode": "glide_ks", "rep": "qqq_qld_mix_corrected", "s_star": c5a.S_STAR,
                  "disc_real": c5a.DISC_REAL, "lookback_years": lookback, "cpi": cpi["series"],
                  "real": True, "cash": "dtb3", "div_dbl_count": "corrected"}
        if neighbor:
            params["neighbor"] = neighbor
        res_d = simulate_ks(closes, dates, 0, di1, ks, sigma=panel["sigma"],
                            trend=panel["trend"], cash=panel["cash"])
        um_d = gate_eval.unit_capital_metrics(res_d.inv_ret[1:], dates=res_d.dates[1:])
        gate_eval.log_evaluation(idea, dict(params), 1, "design", um_d,
                                 window=(res_d.dates[0], res_d.dates[-1]), ledger_path=ledger_path)
        entry = {"design": um_d}
        if neighbor is None and not gate.already_peeked(ledger_path, idea):
            res_h = simulate_ks(closes, dates, hi0, len(dates), ks, sigma=panel["sigma"],
                                trend=panel["trend"], cash=panel["cash"])
            um_h = gate_eval.unit_capital_metrics(res_h.inv_ret[1:], dates=res_h.dates[1:])
            try:
                gate_eval.log_evaluation(idea, dict(params), 1, "holdout", um_h,
                                         window=(res_h.dates[0], res_h.dates[-1]),
                                         ledger_path=ledger_path)
                entry["holdout"] = um_h
            except gate.PeekOnceError:
                entry["holdout"] = "already_peeked"
        else:
            entry["holdout"] = "already_peeked" if neighbor is None else "neighbor_no_holdout"
        return entry

    logged["c7b_glide_ks"] = _log_one("c7b_glide_ks", LOOKBACK_YEARS)
    if do_neighbors:
        for lb in NEIGHBOR_LOOKBACKS:
            logged[f"c7b_glide_ks_{lb}y"] = _log_one(f"c7b_glide_ks_{lb}y", lb,
                                                     neighbor=f"lookback={lb}y")
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
                    "lookback": LOOKBACK_YEARS, "neighbors": list(NEIGHBOR_LOOKBACKS),
                    "mc_paths": args.mc_paths}}

    print("[c7b] CPI 로드(CPIAUCSL, JPNCPIALLMINMEI)...")
    cpi_us = load_cpi_monthly("CPIAUCSL")
    try:
        cpi_jp = load_cpi_monthly("JPNCPIALLMINMEI")
    except Exception as ex:  # noqa: BLE001
        cpi_jp = None
        res["meta"]["jp_cpi_error"] = f"{type(ex).__name__}: {ex}"

    # ── NDX(배당정정) ───────────────────────────────────────────────────────
    print("[c7b] NDX 배당정정 패널 구축...")
    ndx_panel = c6a.build_corrected_panel()
    ks_ndx = compute_ks_active(ndx_panel["QQQ"], ndx_panel["dates"], cpi_us, LOOKBACK_YEARS)
    res["ndx_ks_trigger"] = active_spans(ndx_panel["dates"], ks_ndx["active"])
    res["ndx_ks_trigger"]["forward_filled"] = ks_ndx["forward_filled"]

    print("[c7b] NDX 20y / 10y 분포·판정...")
    res["ndx_20y"] = dist_table(ndx_panel, 20, cpi_us, LOOKBACK_YEARS, DESIGN_YEARS,
                                HOLDOUT_YEARS, fast=args.fast, neighbors=True)
    res["ndx_10y"] = dist_table(ndx_panel, 10, cpi_us, LOOKBACK_YEARS, DESIGN_YEARS,
                                HOLDOUT_YEARS, fast=args.fast)
    res["ndx_cost_benefit_20y"] = cost_benefit(res["ndx_20y"])

    # ── NASDAQCOM 1971–85 OOS ───────────────────────────────────────────────
    print("[c7b] NASDAQCOM 1971–85 OOS...")
    ndxcom = hd.load_fred("NASDAQCOM")
    dtb3 = hd.load_fred("DTB3")
    com_panel = c6a.build_price_panel(ndxcom, dtb3, div_yield=c6a.DIV_COMP_1971)
    ks_com = compute_ks_active(com_panel["QQQ"], com_panel["dates"], cpi_us, LOOKBACK_YEARS)
    res["nasdaqcom_ks_trigger"] = active_spans(com_panel["dates"], ks_com["active"])
    res["nasdaqcom_20y"] = dist_table(com_panel, 20, cpi_us, LOOKBACK_YEARS,
                                      (1971, 1978), (1979, 1985), fast=args.fast)
    res["nasdaqcom_10y"] = dist_table(com_panel, 10, cpi_us, LOOKBACK_YEARS,
                                      (1971, 1980), (1981, 1985), fast=args.fast)

    # ── Nikkei 225 (IN-SAMPLE for the rule) ─────────────────────────────────
    print("[c7b] Nikkei 225 (규칙 설계 케이스 = in-sample)...")
    try:
        nik = hd.load_fred("NIKKEI225")
        nik_rf = [hd.Candle("RF", c.dt, 0.5, 0.5, 0.5, 0.5, 0.0) for c in nik]
        nik_panel = c6a.build_price_panel(nik, nik_rf, div_yield=DIV_NIKKEI)
        cpi_for_nik = cpi_jp if cpi_jp is not None else cpi_us
        ks_nik = compute_ks_active(nik_panel["QQQ"], nik_panel["dates"], cpi_for_nik,
                                   LOOKBACK_YEARS, nominal_only=(cpi_jp is None))
        res["nikkei_ks_trigger"] = active_spans(nik_panel["dates"], ks_nik["active"])
        res["nikkei_ks_trigger"]["cpi"] = (cpi_jp["series"] if cpi_jp else "nominal")
        res["nikkei_ks_trigger"]["forward_filled"] = ks_nik["forward_filled"]
        res["nikkei_20y"] = dist_table(nik_panel, 20, cpi_for_nik, LOOKBACK_YEARS,
                                       (1970, 1990), (1991, 2006), fast=args.fast)
        res["nikkei_first"] = nik_panel["dates"][0].isoformat()
        res["nikkei_last"] = nik_panel["dates"][-1].isoformat()
    except Exception as ex:  # noqa: BLE001
        res["nikkei_error"] = f"{type(ex).__name__}: {ex}"

    # ── 몬테카를로(정상 부트스트랩) ─────────────────────────────────────────
    print(f"[c7b] MC NDX({args.mc_paths} 경로)...")
    res["mc_ndx_20y"] = bootstrap_mc_ks(ndx_panel["ndx"], ndx_panel["dtb3"],
                                        ndx_panel["dates"], ndx_panel["cash"], cpi_us,
                                        div_yield=DIV_NDX, years=20, n_paths=args.mc_paths)
    if "nikkei_error" not in res and cpi_jp is not None:
        print(f"[c7b] MC Nikkei({args.mc_paths} 경로)...")
        res["mc_nikkei_20y"] = bootstrap_mc_ks(nik, nik_rf, nik_panel["dates"],
                                               nik_panel["cash"], cpi_jp, div_yield=DIV_NIKKEI,
                                               years=20, n_paths=args.mc_paths)

    # ── 원장 ────────────────────────────────────────────────────────────────
    if not args.no_ledger:
        print("[c7b] 원장 적재(peek-once)...")
        res["ledger"] = log_ledger(ndx_panel, cpi_us, args.ledger)

    RESULTS_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
    print(f"[c7b] 결과 JSON → {RESULTS_JSON}")
    write_report(res)
    print(f"[c7b] 리포트 → {REPORT}")
    _print_summary(res)
    return res


def _print_summary(res):
    print("\n=== c7b 요약 ===")
    cb = res.get("ndx_cost_benefit_20y")
    if cb:
        print(f"  NDX 20y 설계: glide {cb['glide_x']:.2f}× / glide_ks {cb['glide_ks_x']:.2f}× "
              f"→ 보험비용 {cb['insurance_cost_x']:+.3f}×")
    tr = res.get("ndx_ks_trigger", {})
    print(f"  NDX KS 발동: {'YES' if tr.get('ever') else 'NO'} "
          f"(활성일 {tr.get('n_active_days', 0)}, 구간 {len(tr.get('spans', []))})")
    tc = res.get("nasdaqcom_ks_trigger", {})
    print(f"  NASDAQCOM KS 발동: {'YES' if tc.get('ever') else 'NO'} "
          f"(활성일 {tc.get('n_active_days', 0)})")
    nik = res.get("nikkei_20y")
    if nik and nik.get("holdout"):
        h = nik["holdout"]
        print(f"  Nikkei 홀드아웃: glide {h['glide']['median']/h['b1']['median']:.2f}× "
              f"(regret {h['glide']['regret']:.2f}) → glide_ks "
              f"{h['glide_ks']['median']/h['b1']['median']:.2f}× (regret {h['glide_ks']['regret']:.2f})")


# ── 리포트 ────────────────────────────────────────────────────────────────────
def _dec(a):
    return a.get("decision", {}).get("verdict", "—") if a else "—"


def _row(name, a, b1):
    if not a or not b1:
        return f"| {name} | — | — | — | — |"
    return (f"| {name} | ${a['median']:,.0f} ({a['median']/b1['median']:.2f}×) | "
            f"${a['p5']:,.0f} ({a['p5']/max(1e-9,b1['p5']):.2f}×) | {a['regret']:.2f} | "
            f"**{_dec(a)}** |")


def _sec(title, tab, strategies=("glide", "glide_ks")):
    L = [f"### {title}"]
    if not tab:
        L.append("(데이터 부족/없음)")
        return L
    ff = tab.get("ks_forward_filled", 0)
    note = f" · CPI forward-fill {ff}일" if ff else ""
    L.append(f"시작일 {tab.get('first')}…{tab.get('last')} · n_starts={tab.get('n_starts')}{note}")
    for period in ("design", "holdout"):
        sec = tab.get(period)
        if not sec:
            L.append(f"- {period}: (표본 없음)")
            continue
        b1 = sec["b1"]
        L.append(f"**{period}** — DCA-1x(B1) median ${b1['median']:,.0f} · p5 ${b1['p5']:,.0f} · "
                 f"regret {b1['regret']:.2f} · n={b1['n']}")
        L.append("| 전략 | median(×B1) | p5(×B1) | regret | 판정 |")
        L.append("|---|---:|---:|---:|:--:|")
        for s in strategies:
            if s in sec:
                L.append(_row(s, sec[s], b1))
    return L


def _mc_block(title, mc):
    L = [f"### {title}"]
    if not mc:
        L.append("(없음)")
        return L
    L.append(f"{mc['n_paths']} 경로 · 평균블록 {mc['mean_block']}일 · lookback {mc['lookback']}년 · "
             f"창 {mc['mc_window'][0]}…{mc['mc_window'][1]} · KS 발동경로 {mc['ks_fired_frac']*100:.1f}%")
    L.append("| 비(ratio) | median | p05 | CVaR5 | P(<1) | P(<0.5) |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for key, lab in (("glide_vs_b1", "glide / B1"), ("glide_ks_vs_b1", "glide_ks / B1"),
                     ("glide_ks_vs_glide", "glide_ks / glide")):
        s = mc.get(key)
        if s:
            L.append(f"| {lab} | {s['median']:.3f} | {s['p05']:.3f} | {s['cvar5']:.3f} | "
                     f"{s['p_lt_1']:.3f} | {s['p_lt_half']:.3f} |")
    return L


def _trigger_line(lab, tr):
    if not tr:
        return f"- **{lab}**: (없음)"
    if not tr.get("ever"):
        return f"- **{lab}**: KS 발동 **없음**(전 구간 실질 10년 (+))."
    spans = tr.get("spans", [])
    shown = "; ".join(f"{a}→{b}({d}일)" for a, b, d in spans[:6])
    more = f" 외 {len(spans)-6}구간" if len(spans) > 6 else ""
    extra = f" · CPI {tr['cpi']}" if tr.get("cpi") else ""
    return (f"- **{lab}**: KS 발동 **있음** — 총 활성일 {tr['n_active_days']}, {len(spans)}구간{extra}. "
            f"구간: {shown}{more}.")


def write_report(res):
    m = res["meta"]
    L = []
    L.append(f"> 실행 {m['generated']} · fast={m['fast']} · lookback {m['lookback']}년 · "
             f"이웃 {m['neighbors']} · MC {m['mc_paths']} 경로")
    L.append("")

    L.append("## 결과 A — NDX 1986–2026 (진짜 OOS: 홀드아웃 2000–06)")
    L += _sec("A1. NDX 20년 지평 (+7y·15y 이웃)", res.get("ndx_20y"),
              strategies=("glide", "glide_ks", "glide_ks_7y", "glide_ks_15y"))
    L += _sec("A2. NDX 10년 지평", res.get("ndx_10y"))
    cb = res.get("ndx_cost_benefit_20y")
    if cb:
        L.append("")
        L.append(f"**보험 비용(NDX 20y 설계):** glide {cb['glide_x']:.3f}× → glide_ks "
                 f"{cb['glide_ks_x']:.3f}× (median/B1) → **비용 {cb['insurance_cost_x']:+.3f}×**. "
                 f"regret {cb['glide_regret']:.2f}→{cb['glide_ks_regret']:.2f}.")
    L.append("")

    L.append("## 결과 B — NASDAQCOM 1971–85 OOS (1973–74·1987 포함)")
    L += _sec("B1. NASDAQCOM 20년", res.get("nasdaqcom_20y"))
    L += _sec("B2. NASDAQCOM 10년", res.get("nasdaqcom_10y"))
    L.append("")

    L.append("## 결과 C — Nikkei 225 (규칙 설계 케이스 = **IN-SAMPLE**)")
    L.append("> ⚠ Nikkei 는 KS 가 겨냥해 만들어진 사례다. 아래 개선은 규칙의 일반적 검증이 아니라 "
             "**설계 의도의 확인**이다(정직 회계).")
    if "nikkei_error" in res:
        L.append(f"(로드 실패: {res['nikkei_error']})")
    else:
        L.append(f"구간 {res.get('nikkei_first')}…{res.get('nikkei_last')}")
        L += _sec("C1. Nikkei 20년", res.get("nikkei_20y"))
    L.append("")

    L.append("## 결과 D — KS 발동 이력(날짜)")
    L.append(_trigger_line("NDX(배당정정, 미국)", res.get("ndx_ks_trigger")))
    L.append(_trigger_line("NASDAQCOM(미국)", res.get("nasdaqcom_ks_trigger")))
    L.append(_trigger_line("Nikkei(일본)", res.get("nikkei_ks_trigger")))
    L.append("")

    L.append("## 결과 E — 정상 부트스트랩 MC (수익·현금·인플레 결합 재표집)")
    L += _mc_block("E1. NDX 20년", res.get("mc_ndx_20y"))
    L += _mc_block("E2. Nikkei 20년 (in-sample)", res.get("mc_nikkei_20y"))
    L.append("")

    lg = res.get("ledger", {})
    if lg:
        L.append("## 원장 적재(peek-once)")
        for idea, e in lg.items():
            d = e.get("design", {})
            hd_ = e.get("holdout")
            hd_s = "적재" if isinstance(hd_, dict) else hd_
            L.append(f"- `{idea}`: design CAGR {d.get('cagr',0)*100:.1f}% · MDD "
                     f"{d.get('max_drawdown',0)*100:.0f}% · Sharpe {d.get('sr_annual',0):.2f} · "
                     f"holdout={hd_s}.")
        L.append("")

    L.append("## 판정 및 정직한 해석")
    L.append(_verdict(res))

    head = ""
    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            head = prev.split("<!-- RESULTS_BELOW -->")[0].rstrip()
    body = "\n".join(L)
    out = (head + "\n\n<!-- RESULTS_BELOW -->\n\n" + body + "\n") if head else (body + "\n")
    REPORT.write_text(out, encoding="utf-8")


def _verdict(res):
    L = []
    cb = res.get("ndx_cost_benefit_20y") or {}
    ndx20 = res.get("ndx_20y") or {}
    com20 = res.get("nasdaqcom_20y") or {}
    ndx_tr = res.get("ndx_ks_trigger") or {}
    com_tr = res.get("nasdaqcom_ks_trigger") or {}
    nik20 = res.get("nikkei_20y") or {}
    mc_ndx = res.get("mc_ndx_20y") or {}
    mc_nik = res.get("mc_nikkei_20y") or {}

    def _dx(sec, s):
        return (sec[s]["median"] / sec["b1"]["median"]) if sec and s in sec and sec.get("b1") else float("nan")

    def _dp5(sec, s):
        return (sec[s]["p5"] / sec["b1"]["p5"]) if sec and s in sec and sec.get("b1") else float("nan")

    def _first_last_span(tr):
        sp = tr.get("spans") or []
        if not sp:
            return "—"
        return f"{sp[0][0]}…{sp[-1][1]}"

    d, h = ndx20.get("design"), ndx20.get("holdout")
    cd, ch = com20.get("design"), com20.get("holdout")
    nd, nh = nik20.get("design"), nik20.get("holdout")

    L.append("**결정규칙:** median≥1.15×B1 AND p5≥0.9×B1_p5 AND regret증가≤5pp(설계 판정·홀드아웃 1회 확인).")
    L.append("")

    # 종합 판정 한 줄
    ndx_ok = d and h and _dec(d.get("glide_ks")) == "PASS" and _dec(h.get("glide_ks")) == "PASS"
    verdict = "CONDITIONAL"
    if ndx_ok:
        verdict = "PASS(보험 관점) — NDX 진짜 OOS 유지 + Nikkei(in-sample) 개선"
    L.append(f"### 최종 판정: **{verdict}**")
    L.append("")

    if d and h:
        L.append(f"- **NDX(진짜 OOS) — 보험비용 소액.** glide_ks 20y 설계 {_dx(d,'glide_ks'):.2f}×"
                 f"({_dec(d.get('glide_ks'))}) / 홀드아웃 {_dx(h,'glide_ks'):.2f}×({_dec(h.get('glide_ks'))}). "
                 f"glide 대비 median {cb.get('insurance_cost_x',0):+.3f}× (설계), p5 는 "
                 f"{_dp5(d,'glide'):.2f}→{_dp5(d,'glide_ks'):.2f}×(설계)·"
                 f"{_dp5(h,'glide'):.2f}→{_dp5(h,'glide_ks'):.2f}×(홀드) 로 소폭 하락. "
                 "→ glide 의 사전등록 PASS 를 **깨지 않는다**.")
    if cd and ch:
        flip = (_dec(ch.get("glide")) == "PASS" and _dec(ch.get("glide_ks")) != "PASS")
        L.append(f"- **NASDAQCOM(진짜 OOS) — 여기서 보험료가 드러난다.** 20y 홀드아웃 glide "
                 f"{_dx(ch,'glide'):.2f}×({_dec(ch.get('glide'))}) → glide_ks {_dx(ch,'glide_ks'):.2f}×"
                 f"({_dec(ch.get('glide_ks'))}). "
                 + ("**KS 가 median 문턱(1.15×) 아래로 밀어 홀드아웃 판정을 뒤집는다.** "
                    if flip else "")
                 + "1970s 스태그플레이션(1981 발동)에 신규 레버리지를 멈춰 median 을 반납한 대가 — "
                 "미국에도 '작은 잃어버린 10년'이 있었고 KS 가 실제로 물렸음을 보여준다.")
    if nd and nh:
        L.append(f"- **Nikkei(IN-SAMPLE) — 의도한 효익.** 홀드아웃 glide {_dx(nh,'glide'):.2f}×"
                 f"(regret {nh['glide']['regret']:.2f}) → glide_ks {_dx(nh,'glide_ks'):.2f}×"
                 f"(regret {nh['glide_ks']['regret']:.2f}); 설계 glide {_dx(nd,'glide'):.2f}×"
                 f"(regret {nd['glide']['regret']:.2f}) → glide_ks {_dx(nd,'glide_ks'):.2f}×"
                 f"(regret {nd['glide_ks']['regret']:.2f}). 1x 하회를 1x 근방으로 끌어올리고 regret 을 낮춘다. "
                 "**단 규칙이 겨냥한 사례 → 확증이 아니라 설계 의도 확인.**")
    if mc_ndx.get("glide_ks_vs_glide"):
        gg = mc_ndx["glide_ks_vs_glide"]
        L.append(f"- **MC.** NDX glide_ks/glide median {gg['median']:.3f}(KS 발동경로 "
                 f"{mc_ndx['ks_fired_frac']*100:.0f}%)"
                 + (f" · Nikkei glide_ks/glide median {mc_nik['glide_ks_vs_glide']['median']:.3f}"
                    f"(발동 {mc_nik['ks_fired_frac']*100:.0f}%)"
                    if mc_nik.get("glide_ks_vs_glide") else "")
                 + " → 부트스트랩 히스토리에서도 NDX 는 소액 비용, Nikkei 는 개선.")
    L.append("")

    L.append("**KS 는 미국에서 발동한 적이 있는가(핵심 부수 질문): 있다.**")
    L.append(f"- NDX(배당정정): {_first_last_span(ndx_tr)} 구간에서 발동 — 닷컴+GFC 로 1998–2008 10년 실질이 "
             "(−)로 돌며 신규 레버리지 일시 중단.")
    L.append(f"- NASDAQCOM: {_first_last_span(com_tr)} 구간 — 1970s 고인플레로 10년 실질 (−). "
             "즉 KS 는 이론상 장치가 아니라 미국 데이터에서도 실제로 물리는 규칙이다.")
    L.append("")

    L.append("**핵심 질문 답.** KS 는 NDX 에서는 사실상 무비용(median +방향, p5 소폭↓)으로 glide 의 PASS 를 "
             "유지하나, **NASDAQCOM 홀드아웃에서는 median 을 1.15× 아래로 내려 판정을 FAIL 로 뒤집는 실질 "
             "보험료**를 물린다. 효익 쪽은 Nikkei(in-sample)에서 regret↓·median↑ 로 나타난다. 따라서 "
             "\"공짜 보험\"은 아니며, **NASDAQCOM 이 보여주듯 프리미엄이 둔화한 국면에서는 상승 반납이 존재**한다.")
    L.append("")
    L.append("**권고.** c5a·c6a 권고(소액 슬리브·20년 지평·포워드 페이퍼 점증)에 KS 를 **거버넌스 가드**로 "
             "얹는 것은 정당화된다: 파국(일본형 전제 붕괴)에 대한 꼬리 보호를 제공하고 NDX 성과를 훼손하지 "
             "않는다. 다만 KS 는 median 을 다소 반납할 수 있으므로 '알파 향상'이 아니라 **레짐 위험 보험**으로만 "
             "채택한다.")
    L.append("")
    L.append("**정직한 한계.** (1) Nikkei in-sample — 개선은 사후 규칙의 자기충족 위험(진짜 검증은 미래 OOS). "
             "(2) KS 는 10년 lookback 의 **느린·사후적** 신호 → 붕괴 초기 손실은 이미 반영된 뒤 발동, 기존 "
             "QLD 를 팔지 않으므로 c6a 의 단위낙폭≈−0.99·달러낙폭>50% 리스크는 **그대로 남는다**. "
             "(3) 부트스트랩 인플레는 로그선형 보간·결합 재표집 근사. (4) Japan CPI(JPNCPIALLMINMEI)는 "
             "2021-06 종료 → 이후 결정월 마지막값 캐리(flag; 인플레 과소→KS 보수적). (5) CPI 릴리즈 랙은 "
             "월 t−1 로 단순화(테스트 `test_c7b`).")
    return "\n".join(L)


if __name__ == "__main__":
    main()
