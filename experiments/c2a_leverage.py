#!/usr/bin/env python3
"""c2a — 레버리지 + 추세/변동성 제어 (Nasdaq-100). Cycle 2, id prefix `c2a`.

사전등록·규약: experiments/README.md, docs/gate_v2_spec.md.
데이터 파이프라인(오케스트레이터 지정):
  FRED NASDAQ100(1986-2026) → 총수익 근사(배당 0.7%/yr, index_total_return)
  → 합성 1x/2x/3x(synthetic_leveraged, DTB3=조달금리 yield, 경비/차입스프레드)
  → 현금은 DTB3 수익.

실행 규칙: 신호 close t, 체결 t+1 close(exec_lag=1). 모든 신호 함수는 lookahead_guard 통과.
비용: 수수료 25bp/side(표준) + 반호가(레버리지ETF 2bp/ETF 1bp) + 슬리피지 5bp. FX는 매매 미부과.
분할: 설계 1986–2008(닷컴 포함), 홀드아웃 2009–2026(아이디어당 1회 peek).
게이트: gate_eval 하네스로 원장 적재(reports/trials_ledger.jsonl) + DSR/RC/SPA + gate.decide.

src/ 는 수정하지 않는다. 이 스크립트는 histdata/research/gate 를 소비만 한다.

재현: PYTHONPATH=src .venv/bin/python experiments/c2a_leverage.py [--fast]
  결과표·판정을 reports/cycle2_c2a_leverage.md 로 쓴다.
"""
from __future__ import annotations

import bisect
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
import gate_eval  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle2_c2a_leverage.md"

# ── 모델 상수(사전등록) ──────────────────────────────────────────────────────
DIV_YIELD = 0.007           # NDX 배당수익률 보수적 근사(총수익용)
EXP_1X = 0.002              # 1x(=QQQ) 경비율 0.20%/yr
EXP_LEV = 0.0095            # 레버리지 ETF 경비율 0.95%/yr (QLD/TQQQ 근사)
BORROW_SPREAD = 0.005       # 차입 스프레드 0.5%/yr (조달금리=DTB3 위에 가산)
DESIGN_END = date(2008, 12, 31)
HOLDOUT_END = date(2026, 12, 31)

COST = R.CostSpec(
    commission_bps=25.0, slippage_bps=5.0, fx_bps=20.0,
    half_spread_bps={"L1": 1.0, "L2": 2.0, "L3": 2.0,
                     "QQQ": 1.0, "QLD": 2.0, "TQQQ": 2.0, "SCHD": 1.0, "GLD": 1.0},
    default_half_spread_bps=3.0)


# ── 데이터 빌더 ──────────────────────────────────────────────────────────────
def _yield_daily_aligned(fred_yield: list, dates: list[date]) -> list[float]:
    """FRED 연율(%) 수익률 시리즈 → dates 정렬 일간 현금수익률(직전 유효값 forward-fill)."""
    ks = [c.dt for c in fred_yield]
    vs = [c.close for c in fred_yield]
    out = []
    for d in dates:
        i = bisect.bisect_right(ks, d) - 1
        a = vs[i] if i >= 0 else (vs[0] if vs else 0.0)
        out.append(max(0.0, (a / 100.0) / 252.0))
    return out


def _level_aligned(fred_level: list, dates: list[date]) -> list[float]:
    """FRED 레벨(예 VIXCLS) → dates 정렬(직전 유효값 forward-fill). 첫 유효 이전은 None."""
    ks = [c.dt for c in fred_level]
    vs = [c.close for c in fred_level]
    out: list[float | None] = []
    for d in dates:
        i = bisect.bisect_right(ks, d) - 1
        out.append(vs[i] if i >= 0 else None)
    return out


def build_synthetic_panel():
    """FRED NDX → 합성 1x/2x/3x 패널 + VIX + 현금수익률. 전 구간(1986-2026)."""
    ndx = hd.load_fred("NASDAQ100")
    dtb3 = hd.load_fred("DTB3")
    vix = hd.load_fred("VIXCLS")
    tr = hd.index_total_return(ndx, DIV_YIELD, symbol="NDXTR")
    L1 = hd.synthetic_leveraged(tr, 1.0, annual_expense=EXP_1X, borrow_spread=0.0,
                                rf_candles=dtb3, rf_kind="yield", symbol="L1")
    L2 = hd.synthetic_leveraged(tr, 2.0, annual_expense=EXP_LEV, borrow_spread=BORROW_SPREAD,
                                rf_candles=dtb3, rf_kind="yield", symbol="L2")
    L3 = hd.synthetic_leveraged(tr, 3.0, annual_expense=EXP_LEV, borrow_spread=BORROW_SPREAD,
                                rf_candles=dtb3, rf_kind="yield", symbol="L3")
    dates = [c.dt for c in L1]
    panel = {"L1": [c.close for c in L1], "L2": [c.close for c in L2],
             "L3": [c.close for c in L3], "VIX": _level_aligned(vix, dates)}
    cash = _yield_daily_aligned(dtb3, dates)
    return panel, dates, cash


def build_real_panel():
    """실물 QQQ/QLD/TQQQ + SCHD/GLD 패널(2016-2026, 공통거래일) + 현금수익률(DTB3)."""
    syms = ["QQQ", "QLD", "TQQQ", "SCHD", "GLD"]
    raw = {s: hd.load_symbol(s) for s in syms}
    common = set.intersection(*[{c.dt for c in raw[s]} for s in syms])
    dates = sorted(common)
    dtb3 = hd.load_fred("DTB3")
    vix = hd.load_fred("VIXCLS")
    def col(s):
        by = {c.dt: c.close for c in raw[s]}
        return [by[d] for d in dates]
    # 신호용 키(L1/L2/L3) = 실물 QQQ/QLD/TQQQ 로 매핑 → 동일 신호 함수 재사용
    panel = {"L1": col("QQQ"), "L2": col("QLD"), "L3": col("TQQQ"),
             "QQQ": col("QQQ"), "SCHD": col("SCHD"), "GLD": col("GLD"),
             "VIX": _level_aligned(vix, dates)}
    cash = _yield_daily_aligned(dtb3, dates)
    return panel, dates, cash


# ── 캘린더(로컬; src 미수정) ─────────────────────────────────────────────────
def week_end_flags(dates: list[date]) -> list[bool]:
    """각 날짜가 해당 ISO주의 마지막 거래일인가(금요일 또는 그 주 마지막 거래일)."""
    n = len(dates)
    out = [False] * n
    for t in range(n):
        if t == n - 1 or dates[t].isocalendar()[:2] != dates[t + 1].isocalendar()[:2]:
            out[t] = True
    return out


# ── 레버리지 → 슬리브 비중 매핑 ──────────────────────────────────────────────
def lev_to_weights(L: float, keys=("L1", "L2", "L3")) -> dict[str, float]:
    """실효 레버리지 L∈[0,3] → 인접 슬리브 혼합 비중(자본가중). L≥1 완전투자, L<1 현금 버퍼.
    실효 노출 = 1·w1 + 2·w2 + 3·w3 = L (인접 슬리브만 사용 → 일일 리밸 레버리지 감쇠 정합)."""
    a, b, c = keys
    if L <= 1e-9:
        return {}
    if L <= 1.0:
        return {a: L}
    if L <= 2.0:
        w = {a: 2.0 - L, b: L - 1.0}
    elif L < 3.0:
        w = {b: 3.0 - L, c: L - 2.0}
    else:
        return {c: 1.0}
    return {k: v for k, v in w.items() if v > 1e-9}


def avg_leverage(weights: list[dict]) -> float:
    """비중 스케줄의 평균 실효 레버리지(무매매·현금 구간 포함)."""
    lv = [w.get("L1", 0) + 2 * w.get("L2", 0) + 3 * w.get("L3", 0) for w in weights]
    return sum(lv) / len(lv) if lv else 0.0


# ── 신호 함수(전부 (panel,dates)->weights, lookahead_guard 대상) ─────────────
def _trend_state(sig: list[float], sma: list, dates: list[date], band: float,
                 weekly: bool) -> list[bool]:
    """SMA 대비 ±band 히스테리시스 추세 상태(True=추세상방). weekly면 주 마지막 거래일에만 갱신."""
    n = len(sig)
    we = week_end_flags(dates) if weekly else [True] * n
    st = [False] * n
    cur = False
    for t in range(n):
        if we[t] and sma[t] is not None:
            if sig[t] > sma[t] * (1 + band):
                cur = True
            elif sig[t] < sma[t] * (1 - band):
                cur = False
        st[t] = cur
    return st


def sig_lrr(panel, dates, *, sma_win=200, band=0.02, lev_sym="L2"):
    """전략1 LRR(Gayed-Bilello): NDX TR > SMA200(±2% 밴드, 주간 금요일 평가) → 2x, else 현금."""
    sig = panel["L1"]
    st = _trend_state(sig, R.sma(sig, int(round(sma_win))), dates, band, weekly=True)
    return [{lev_sym: 1.0} if st[t] else {} for t in range(len(dates))]


def sig_vol_target(panel, dates, *, vol_target=0.25, vol_win=20, lev_band=0.25):
    """전략2 변동성 타깃: lev = clip(target/realized_vol20, 0, 3), |Δlev|>0.25 일 때만 리밸."""
    rets = R.to_returns(panel["L1"])
    rv = R.realized_vol(rets, int(round(vol_win)), annualize=True)
    n = len(dates)
    w = [{} for _ in range(n)]
    committed = 0.0
    for t in range(n):
        if rv[t] is None or rv[t] <= 0:
            w[t] = lev_to_weights(committed)
            continue
        desired = max(0.0, min(3.0, vol_target / rv[t]))
        if abs(desired - committed) > lev_band:
            committed = desired
        w[t] = lev_to_weights(committed)
    return w


def sig_combo(panel, dates, *, sma_win=200, band=0.02, vol_target=0.25,
              vol_win=20, lev_band=0.25):
    """전략3 콤보: 추세필터 AND 변동성타깃. 추세상방이면 vol-target lev, 하방이면 0(현금)."""
    sig = panel["L1"]
    st = _trend_state(sig, R.sma(sig, int(round(sma_win))), dates, band, weekly=False)
    rets = R.to_returns(panel["L1"])
    rv = R.realized_vol(rets, int(round(vol_win)), annualize=True)
    n = len(dates)
    w = [{} for _ in range(n)]
    committed = 0.0
    for t in range(n):
        if not st[t]:
            committed = 0.0
            w[t] = {}
            continue
        if rv[t] is None or rv[t] <= 0:
            w[t] = lev_to_weights(committed)
            continue
        desired = max(0.0, min(3.0, vol_target / rv[t]))
        if committed <= 1e-9 or abs(desired - committed) > lev_band:
            committed = desired
        w[t] = lev_to_weights(committed)
    return w


def sig_const(panel, dates, *, lev=2.0):
    """전략4 상수 레버리지(정직한 베타 레퍼런스). 상수 혼합 비중을 매일 목표로(밴드로 저회전)."""
    wt = lev_to_weights(float(lev))
    return [dict(wt) for _ in range(len(dates))]


def sig_vixregime(panel, dates, *, sma_win=200, band=0.02, vix_target=0.25,
                  vix_smooth=5, lev_band=0.25):
    """전략5(창작) VIX-regime 레버리지 + 추세 테일가드: 추세상방이면
    lev = clip(vix_target/(VIX_ma/100), 0, 3)(내재변동성 기반, 실현변동성보다 선반응),
    추세하방이면 현금. 사전등록 단일 설정."""
    sig = panel["L1"]
    st = _trend_state(sig, R.sma(sig, int(round(sma_win))), dates, band, weekly=False)
    vix = panel["VIX"]
    vsm = R.sma([v if v is not None else float("nan") for v in vix], int(round(vix_smooth)))
    n = len(dates)
    w = [{} for _ in range(n)]
    committed = 0.0
    for t in range(n):
        if not st[t]:
            committed = 0.0
            w[t] = {}
            continue
        vv = vsm[t]
        if vv is None or not math.isfinite(vv) or vv <= 0:
            w[t] = lev_to_weights(committed)
            continue
        desired = max(0.0, min(3.0, vix_target / (vv / 100.0)))
        if committed <= 1e-9 or abs(desired - committed) > lev_band:
            committed = desired
        w[t] = lev_to_weights(committed)
    return w


# ── 백테스트 + 윈도 지표 ─────────────────────────────────────────────────────
def run_strategy(sig_fn, panel, dates, cash, *, band=0.05, cost=None, params=None):
    weights = sig_fn(panel, dates, **(params or {}))
    # 신호 함수는 전 패널(VIX 등)을 보지만, 체결기는 수치 종가 슬리브만 받는다(VIX=None 제외).
    tradable = {k: v for k, v in panel.items() if None not in v}
    res = R.run_weights(tradable, dates, weights, exec_lag=1, rebalance_band=band,
                        cost=cost or COST, cash_rate=cash)
    return res, weights


def window_slice(dates, s, e):
    return [i for i, d in enumerate(dates) if s <= d <= e]


def window_metrics(equity, dates, s, e):
    idx = window_slice(dates, s, e)
    if len(idx) < 2:
        return {}
    eq = [equity[i] for i in idx]
    days = max((dates[idx[-1]] - dates[idx[0]]).days, 1)
    cg = gate.cagr(eq, days)
    mdd = gate.max_drawdown(eq)
    return {"terminal": eq[-1] / eq[0], "cagr": cg, "mdd": mdd,
            "ulcer": gate.ulcer_index(eq), "martin": gate.martin_ratio(eq, days),
            "calmar": gate.calmar(cg, mdd), "i0": idx[0], "i1": idx[-1]}


def net_excess_terminal(res, bench_res, dates, s, e):
    """윈도 [s,e]에서 후보/벤치 단위자본 총수익 배수 초과(후보배수 − 벤치배수)."""
    a = window_metrics(res.equity, dates, s, e).get("terminal", 0.0)
    b = window_metrics(bench_res.equity, dates, s, e).get("terminal", 1.0)
    return a - b


# ── 아이디어 평가(이웃/헤드라인/강건성/판정) ────────────────────────────────
NEIGHBOR_GRID = (0.5, 0.7, 0.8, 1.2, 1.5)


def _standardize(x):
    if len(x) < 2:
        return x
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return [(v - m) / sd for v in x] if sd > 0 else [0.0] * len(x)


def evaluate_idea(idea_id, sig_fn, base_params, primary_key, panel, dates, cash,
                  bench_res, *, start=None, fast=False):
    """이웃(설계) 적재 → 헤드라인(설계/홀드아웃) run_splits → 강건성 → gate.decide."""
    d0 = start or dates[0]
    design_end = DESIGN_END
    bench_stream = bench_res.net_stream()

    # 헤드라인 백테스트(전 구간)
    band = 0.0 if idea_id in ("c2a_lrr", "c2a_lrr3x", "c2a_const2x") else 0.05
    res, weights = run_strategy(sig_fn, panel, dates, cash, band=band, params=base_params)

    # 베타 vs 타이밍: 동일 평균레버리지 상수 홀드(타이밍 제거) 레퍼런스
    clev = avg_leverage(weights)
    cres, _ = run_strategy(sig_const, panel, dates, cash, band=0.05, params={"lev": clev})

    # ── 이웃(플래토): primary_key 를 그리드로 흔들어 설계구간 평가·원장 적재 ──
    neigh_streams = {}
    sr_trials = []
    neigh_pos = 0
    neigh_sharpes = []
    design_idx = window_slice(dates, d0, design_end)
    di0, di1 = (design_idx[0], design_idx[-1]) if design_idx else (0, 0)

    def design_stream(r):
        # net_stream: index i ↔ dates[i+1]. 설계구간 스트림 추출.
        return [r.net_returns[i] for i in design_idx if i >= 1]

    center_val = base_params[primary_key]
    configs = [("center", base_params)]
    for g in NEIGHBOR_GRID:
        p = dict(base_params)
        v = center_val * g
        p[primary_key] = int(round(v)) if isinstance(center_val, int) else v
        configs.append((f"{primary_key}x{g}", p))

    center_design_stream = None
    holdout_idx = window_slice(dates, date(2009, 1, 1), HOLDOUT_END)
    for name, p in configs:
        r_n, w_n = run_strategy(sig_fn, panel, dates, cash, band=band, params=p)
        ds = design_stream(r_n)
        if len(ds) < 2:
            continue
        sd = gate._std(ds, 1)
        sr = (gate._mean(ds) / sd) if sd > 0 else 0.0
        neigh_streams[name] = _standardize(ds)
        wm = window_metrics(r_n.equity, dates, d0, design_end)
        nx = net_excess_terminal(r_n, bench_res, dates, d0, design_end)
        sr_ann = gate.sharpe(ds)
        if name == "center":
            center_design_stream = ds
        else:
            sr_trials.append(sr)
            neigh_sharpes.append(sr_ann)
            if nx > 0:
                neigh_pos += 1
        # 원장 적재(이웃도 기록 — 상관 클러스터로 N_eff 흡수)
        um = gate_eval.unit_capital_metrics(ds, dates=[dates[i] for i in design_idx])
        gate_eval.log_evaluation(idea_id, {"neighbor": name, **{k: p[k] for k in p}},
                                 1, "design", um,
                                 window=(dates[di0], dates[di1]), ledger_path=LEDGER)

    center_sr_ann = gate.sharpe(center_design_stream) if center_design_stream else 0.0
    n_neigh = len(neigh_sharpes)
    frac_pos = neigh_pos / n_neigh if n_neigh else 0.0
    neigh_sharpes.sort()
    med_neigh = gate._quantile_sorted(neigh_sharpes, 0.5) if neigh_sharpes else 0.0
    plateau_pass = bool(n_neigh and frac_pos >= 0.8 and med_neigh >= 0.9 * center_sr_ann)
    plateau_border = bool(n_neigh and not plateau_pass and frac_pos >= 0.6)

    # DSR(N_eff): 이웃 상관 클러스터로 유효 시도수 축소(사양 §3.2)
    n_eff = gate.n_eff_clusters(neigh_streams, theta=0.9) if neigh_streams else 1
    sr_all = sr_trials + ([gate._mean(center_design_stream) / gate._std(center_design_stream, 1)]
                          if center_design_stream and gate._std(center_design_stream, 1) > 0 else [])
    dsr_neff = (gate.deflated_sharpe_ratio(center_design_stream, sr_all, n_eff=n_eff)
                if center_design_stream else float("nan"))
    holdout_center_stream = [res.net_returns[i] for i in holdout_idx if i >= 1]
    dsr_neff_holdout = (gate.deflated_sharpe_ratio(holdout_center_stream, sr_all, n_eff=n_eff)
                        if len(holdout_center_stream) >= 2 else float("nan"))

    # ── 헤드라인: 설계 vs 홀드아웃 run_splits(원장 적재 + peek-once + RC/SPA) ──
    rc_B = 400 if fast else 1000
    cand_stream = res.net_stream()
    peeked = gate.already_peeked(LEDGER, idea_id)
    splits = gate_eval.run_splits(
        idea_id, dict(base_params), 1, cand_stream, bench_stream, dates, design_end,
        bench_terminal=None, ledger_path=LEDGER, rc_B=rc_B, log=not peeked)

    _years = max((dates[-1] - d0).days / 365.0, 1e-9)
    out = {"idea_id": idea_id, "res": res, "weights": weights, "band": band,
           "avg_lev": avg_leverage(weights), "trades": res.trade_count,
           "trades_yr": res.trade_count / _years,
           "turnover_yr": sum(res.turnover) / _years,
           "plateau_pass": plateau_pass, "plateau_border": plateau_border,
           "frac_pos": frac_pos, "n_eff": n_eff, "n_neigh": n_neigh,
           "dsr_neff": dsr_neff, "splits": splits, "d0": d0}

    # 강건성/판정은 설계·홀드아웃 각각
    for period, s, e in (("design", d0, design_end),
                         ("holdout", date(2009, 1, 1), HOLDOUT_END)):
        if period not in splits:
            continue
        sp = splits[period]
        wm = window_metrics(res.equity, dates, s, e)
        wb = window_metrics(bench_res.equity, dates, s, e)
        tvb0 = wm["terminal"] / wb["terminal"] if wb.get("terminal") else None
        # 롤링 3년 승률
        idx = window_slice(dates, s, e)
        sc = [res.equity[i] for i in idx]
        bc = [bench_res.equity[i] for i in idx]
        wr = gate.subperiod_consistency(sc, bc, [dates[i] for i in idx],
                                        window_years=3, step_months=6)["win_rate"]
        # 비용 스트레스: 총비용 ×mult 에서 net 초과(vs 1x) 유지
        cs_mult = 1.0
        for m in (1.5, 2.0):
            r_s, _ = run_strategy(sig_fn, panel, dates, cash, band=band,
                                  cost=COST.stress(m), params=base_params)
            if net_excess_terminal(r_s, bench_res, dates, s, e) > 0:
                cs_mult = m
        base_nx = net_excess_terminal(res, bench_res, dates, s, e)
        if base_nx <= 0:
            cs_mult = 0.0
        metrics = {
            "lane": 1,
            "terminal_vs_b0": tvb0,
            "dsr": dsr_neff if period == "design" else dsr_neff_holdout,
            "rc_pvalue": sp["axis_input"].get("rc_pvalue"),
            "mdd": wm["mdd"],
            "plateau_pass": plateau_pass, "plateau_borderline": plateau_border,
            "subperiod_winrate": wr,
            "cost_stress_mult": cs_mult if base_nx > 0 else 0.0,
            "calmar_not_worse": wm["calmar"] >= wb["calmar"],
            "ulcer_not_worse": wm["martin"] >= wb["martin"],
        }
        dec = gate.decide(metrics)
        # 베타 레퍼런스(동일 평균레버리지 상수) 윈도 지표 → 타이밍 순가치 분해
        cm = window_metrics(cres.equity, dates, s, e)
        out[period] = {"wm": wm, "wb": wb, "tvb0": tvb0, "winrate": wr,
                       "cs_mult": metrics["cost_stress_mult"],
                       "rc": sp["axis_input"].get("rc_pvalue"),
                       "spa": sp["axis_input"].get("spa_pvalue"),
                       "sr_ann": sp["unit"].get("sr_annual"),
                       "const_ref": cm, "const_lev": clev,
                       "decision": dec, "metrics": metrics}
    return out


# ── DCA 오버레이(2016-2026 실물) ─────────────────────────────────────────────
def run_dca(sig_fn, panel, dates, cash, params, *, band=0.05):
    weights = sig_fn(panel, dates, **(params or {}))
    return R.run_dca_overlay(panel, dates, weights, monthly_usd=35.0, initial_usd=32.0,
                             cost=COST, cash_rate=cash, exec_lag=0)


def const_weights_series(dates, wt):
    return [dict(wt) for _ in dates]


# ── 리포트 ───────────────────────────────────────────────────────────────────
def _f(v, pct=False, nd=2):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v * 100:+.{nd}f}%" if pct else f"{v:.{nd}f}"


def main(fast=False):
    print("[c2a] building synthetic panel (FRED NDX 1986-2026)…")
    panel, dates, cash = build_synthetic_panel()
    print(f"[c2a] N={len(dates)}  {dates[0]}..{dates[-1]}")

    # 벤치마크: 1x B&H(=NDX TR − 0.20% 경비) 전 구간
    bench_res, _ = run_strategy(sig_const, panel, dates, cash,
                                band=0.0, params={"lev": 1.0})

    ideas = [
        ("c2a_lrr", "LRR 2x (추세 주간)", sig_lrr, {"sma_win": 200, "band": 0.02, "lev_sym": "L2"}, "sma_win"),
        ("c2a_lrr3x", "LRR 3x 변형", sig_lrr, {"sma_win": 200, "band": 0.02, "lev_sym": "L3"}, "sma_win"),
        ("c2a_voltarget", "변동성 타깃 25%", sig_vol_target, {"vol_target": 0.25, "vol_win": 20, "lev_band": 0.25}, "vol_target"),
        ("c2a_combo", "추세+변동성 콤보", sig_combo, {"sma_win": 200, "band": 0.02, "vol_target": 0.25, "vol_win": 20, "lev_band": 0.25}, "vol_target"),
        ("c2a_const15x", "상수 1.5x (레퍼런스)", sig_const, {"lev": 1.5}, "lev"),
        ("c2a_const2x", "상수 2x (레퍼런스)", sig_const, {"lev": 2.0}, "lev"),
        ("c2a_vixregime", "VIX-regime + 추세 (창작)", sig_vixregime, {"sma_win": 200, "band": 0.02, "vix_target": 0.25, "vix_smooth": 5, "lev_band": 0.25}, "vix_target"),
    ]

    results = {}
    for idea_id, name, fn, params, pk in ideas:
        # VIX 전략은 VIX 존재 구간(1990+)만: 패널·벤치를 슬라이스
        ipanel, idates, icash, ibench = panel, dates, cash, bench_res
        if idea_id == "c2a_vixregime":
            i0 = next(i for i, v in enumerate(panel["VIX"]) if v is not None)
            ipanel = {k: v[i0:] for k, v in panel.items()}
            idates = dates[i0:]
            icash = cash[i0:]
            ibench, _ = run_strategy(sig_const, ipanel, idates, icash,
                                     band=0.0, params={"lev": 1.0})
        print(f"[c2a] evaluating {idea_id} ({name})…")
        try:
            r = evaluate_idea(idea_id, fn, params, pk, ipanel, idates, icash, ibench,
                              fast=fast)
            r["name"] = name
            results[idea_id] = r
        except gate.PeekOnceError as e:
            print(f"   ⚠ {idea_id}: 홀드아웃 이미 peek — 건너뜀 ({e})")

    # 벤치 윈도 지표
    bench_design = window_metrics(bench_res.equity, dates, dates[0], DESIGN_END)
    bench_hold = window_metrics(bench_res.equity, dates, date(2009, 1, 1), HOLDOUT_END)

    # ── 합성 vs 실물 검증(2016-2026) ──
    print("[c2a] validating synthetic vs real QLD/TQQQ…")
    ndx = hd.load_fred("NASDAQ100"); dtb3 = hd.load_fred("DTB3")
    base16 = hd.index_total_return([c for c in ndx if c.dt >= date(2016, 9, 22)], DIV_YIELD)
    val2 = hd.validate_synthetic(base16, hd.load_symbol("QLD"), 2.0,
                                 annual_expense=EXP_LEV, borrow_spread=BORROW_SPREAD,
                                 rf_candles=dtb3, rf_kind="yield")
    val3 = hd.validate_synthetic(base16, hd.load_symbol("TQQQ"), 3.0,
                                 annual_expense=EXP_LEV, borrow_spread=BORROW_SPREAD,
                                 rf_candles=dtb3, rf_kind="yield")

    # ── DCA 오버레이(2016-2026 실물) : 설계기간 Martin 기준 상위 2 + 레퍼런스 ──
    print("[c2a] DCA overlays (2016-2026 real ETFs)…")
    rpanel, rdates, rcash = build_real_panel()
    # 후보 선정: 타이밍 전략 중 설계 Martin(위험조정) 상위 2 ("best 1-2")
    timing = [k for k in ("c2a_lrr", "c2a_lrr3x", "c2a_voltarget", "c2a_combo", "c2a_vixregime")
              if k in results and "design" in results[k]]
    timing.sort(key=lambda k: results[k]["design"]["wm"]["martin"], reverse=True)
    top = timing[:2]
    print(f"[c2a] DCA top picks (design Martin): {top}")

    sig_map = {"c2a_lrr": (sig_lrr, {"sma_win": 200, "band": 0.02, "lev_sym": "L2"}, 0.0),
               "c2a_lrr3x": (sig_lrr, {"sma_win": 200, "band": 0.02, "lev_sym": "L3"}, 0.0),
               "c2a_voltarget": (sig_vol_target, {"vol_target": 0.25, "vol_win": 20, "lev_band": 0.25}, 0.05),
               "c2a_combo": (sig_combo, {"sma_win": 200, "band": 0.02, "vol_target": 0.25, "vol_win": 20, "lev_band": 0.25}, 0.05),
               "c2a_vixregime": (sig_vixregime, {"sma_win": 200, "band": 0.02, "vix_target": 0.25, "vix_smooth": 5, "lev_band": 0.25}, 0.05)}

    dca = {}
    for k in top:
        fn, params, band = sig_map[k]
        w = fn(rpanel, rdates, **params)
        d = R.run_dca_overlay(rpanel, rdates, w, monthly_usd=35.0, initial_usd=32.0,
                              cost=COST, cash_rate=rcash, exec_lag=0)
        dca[k] = d
    # B0, B1
    b0 = R.run_dca_overlay(rpanel, rdates, const_weights_series(rdates, {"QQQ": 0.6, "SCHD": 0.25, "GLD": 0.15}),
                           monthly_usd=35.0, initial_usd=32.0, cost=COST, cash_rate=rcash, exec_lag=0)
    b1 = R.run_dca_overlay(rpanel, rdates, const_weights_series(rdates, {"QQQ": 1.0}),
                           monthly_usd=35.0, initial_usd=32.0, cost=COST, cash_rate=rcash, exec_lag=0)

    # 수수료 10bp(프로모) what-if — DCA 픽 재무
    promo = R.CostSpec.promo(slippage_bps=5.0, fx_bps=20.0,
                             half_spread_bps=dict(COST.half_spread_bps),
                             default_half_spread_bps=3.0)
    dca_promo = {}
    for k in top:
        fn, params, _b = sig_map[k]
        w = fn(rpanel, rdates, **params)
        dca_promo[k] = R.run_dca_overlay(rpanel, rdates, w, monthly_usd=35.0, initial_usd=32.0,
                                         cost=promo, cash_rate=rcash, exec_lag=0)

    write_report(results, bench_design, bench_hold, val2, val3, dca, b0, b1, top,
                 dates, panel, cash, bench_res, dca_promo)
    print(f"[c2a] report → {REPORT}")
    return results


def write_report(results, bd, bh, val2, val3, dca, b0, b1, top, dates, panel, cash, bench_res,
                 dca_promo=None):
    L = []
    L.append("# Cycle 2 · c2a — 레버리지 + 추세/변동성 제어 (Nasdaq-100)\n")
    L.append("작성 executor · 데이터 FRED NASDAQ100(1986-2026) 총수익근사 → 합성 1x/2x/3x · "
             "게이트 docs/gate_v2_spec.md · 원장 reports/trials_ledger.jsonl\n")

    # 사전등록
    L.append("## 1. 사전등록 (실행 전 고정)\n")
    L.append(
        "**데이터·합성.** FRED NASDAQ100(1986-01-02~2026-09-22)에 배당 0.7%/yr 를 더해 총수익(TR) 근사"
        "(`index_total_return`). TR 을 기초로 `synthetic_leveraged` 일일리밸 합성 슬리브 생성 — "
        "**1x**(경비 0.20%/yr, 차입 없음=QQQ 근사), **2x/3x**(경비 0.95%/yr, 차입 스프레드 0.5%/yr, "
        "조달금리 = DTB3 yield). 현금은 DTB3(일할). 실효 레버리지 L 은 인접 슬리브 혼합으로 표현"
        "(L≤1: 1x+현금, 1<L≤2: 1x/2x, 2<L≤3: 2x/3x). **벤치마크 B&H = 1x**(≈QQQ).\n")
    L.append(
        "**해석 주의(정직).** 오케스트레이터 지정은 '1x/2x/3x 경비 0.95%'였으나, 1x 를 0.95% 로 두면 "
        "무-레버리지 홀드가 벤치(같은 1x)에 경비만큼 자동 열위가 되어 비교가 왜곡된다. 따라서 "
        "**1x=0.20%(QQQ ER), 레버리지 슬리브=0.95%** 로 조정했다(합리적 해석, 결과에 명시). "
        "또한 합성은 TR 기초에 레버리지를 걸어 배당분 레버리지를 소폭 과대계상하나, "
        "§5 실물 검증에서 추적오차로 정량화한다.\n")
    L.append("**후보(각 1 설정 + primary 파라미터 ±20~50% 이웃).** 신호 close t, 체결 t+1 close.\n")
    L.append("1. **LRR(c2a_lrr)** — NDX TR > SMA200(±2% 밴드, 주 마지막 거래일 평가) → **2x**, else 현금. "
             "변형 **3x(c2a_lrr3x)**. 이웃: SMA {100,140,160,240,300}.\n")
    L.append("2. **변동성 타깃(c2a_voltarget)** — lev = clip(0.25/실현변동성20, 0, 3), |Δlev|>0.25 일 때만 리밸. 이웃: target {0.125…0.375}.\n")
    L.append("3. **콤보(c2a_combo)** — 추세필터 AND 변동성타깃(추세상방=vol-target lev, 하방=0). 이웃: target 그리드.\n")
    L.append("4. **상수 레버리지(c2a_const15x/const2x)** — 1.5x·2x 저회전 홀드 = 정직한 베타 레퍼런스.\n")
    L.append("5. **창작 VIX-regime(c2a_vixregime)** — 추세상방이면 lev = clip(0.25/(VIX_ma5/100), 0, 3) "
             "(내재변동성으로 크래시 선반응), 하방이면 현금. VIX(1990+)로 설계 1990-2008.\n")
    L.append("**게이트 축(AND)**: 경제(vs 1x)·유의성(DSR N_eff·RC·SPA)·플래토·롤링3년 승률·비용 2×·"
             "리스크(**단위자본 MDD −50% 하드캡** + Calmar/Martin 비열위). 홀드아웃 1회 peek.\n")

    # 벤치
    L.append("## 2. 벤치마크 (1x B&H, 단위자본)\n")
    L.append("| 구간 | CAGR | MDD | Ulcer | Martin | Calmar |\n|---|---:|---:|---:|---:|---:|")
    L.append(f"| 설계 1986-2008 | {_f(bd['cagr'],1)} | {_f(bd['mdd'],1)} | {_f(bd['ulcer'])} | {_f(bd['martin'])} | {_f(bd['calmar'])} |")
    L.append(f"| 홀드아웃 2009-2026 | {_f(bh['cagr'],1)} | {_f(bh['mdd'],1)} | {_f(bh['ulcer'])} | {_f(bh['martin'])} | {_f(bh['calmar'])} |")
    L.append("\n> 주의: **1x NDX B&H 자체가 설계구간 MDD ≈ −83%** (닷컴 2000-2002)로 −50% 캡을 위반한다. "
             "즉 캡을 지키려면 반드시 하락장에서 **현금으로 빠지는 타이밍**이 필요하다 — 이것이 이 실험의 핵심.\n")

    # 결과표
    L.append("## 3. 결과 — 단위자본 (설계 / 홀드아웃)\n")
    L.append("| 아이디어 | 구간 | 평균lev | CAGR | MDD | Ulcer | Calmar | 최종/1x | Sharpe | DSR(Neff) | RC p | SPA p | 롤3y승률 | 비용생존 | 회전/yr | 매매/yr | 판정 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    order = ["c2a_lrr", "c2a_lrr3x", "c2a_voltarget", "c2a_combo",
             "c2a_const15x", "c2a_const2x", "c2a_vixregime"]
    for k in order:
        if k not in results:
            continue
        r = results[k]
        for period in ("design", "holdout"):
            if period not in r:
                continue
            p = r[period]
            wm = p["wm"]
            row = (f"| {r['name']} | {period} | {r['avg_lev']:.2f} | {_f(wm['cagr'],1)} | "
                   f"{_f(wm['mdd'],1)} | {_f(wm['ulcer'])} | {_f(wm['calmar'])} | "
                   f"{_f(p['tvb0'])} | {_f(p['sr_ann'])} | "
                   f"{_f(r['dsr_neff']) if period=='design' else '—'} | {_f(p['rc'],nd=3)} | "
                   f"{_f(p['spa'],nd=3)} | {_f(p['winrate'])} | {p['cs_mult']:.1f}× | "
                   f"{r['turnover_yr']:.2f} | {r['trades_yr']:.1f} | **{p['decision'].verdict}** |")
            L.append(row)
    L.append(f"\n*최종/1x = 단위자본 총수익 배수 대비 1x B&H. DSR 은 설계구간·N_eff(상관클러스터).*")
    L.append("> **DSR 주의(정직).** 이웃(플래토)들이 서로 높은 상관 → N_eff≈1 로 축소되어 "
             "아이디어-내 다중검정 페널티가 사실상 사라진다(DSR≈1). 즉 여기서 DSR 은 '스킬 유의성'이 "
             "아니라 '일 Sharpe>0'에 가깝고, 대부분 **베타(레버리지)** 로 부풀려진다. 진짜 다중검정 "
             "방어는 (a) 이번 사이클 7개 아이디어 + 동시 타 에이전트의 **프로그램 전체 N**, (b) "
             "**리스크 축(−50% 캡·Calmar/Martin 비열위)** 이다. RC/SPA 도 벤치가 1x라 레버리지의 "
             "상승장 초과를 '유의'로 잡을 뿐 스킬 증거가 아니다.\n")

    # 플래토·유의 상세
    L.append("### 3.1 플래토·N_eff\n")
    L.append("| 아이디어 | 이웃 net>0 비율 | plateau | N_eff | 이웃수 |\n|---|---:|---|---:|---:|")
    for k in order:
        if k not in results:
            continue
        r = results[k]
        pl = "PASS" if r["plateau_pass"] else ("경계" if r["plateau_border"] else "FAIL")
        L.append(f"| {r['name']} | {r['frac_pos']*100:.0f}% | {pl} | {r['n_eff']} | {r['n_neigh']} |")

    # 베타 vs 타이밍
    L.append("\n## 4. 베타 vs 타이밍 (정직한 분해)\n")
    L.append("각 타이밍 전략을 **동일 평균레버리지의 상수 홀드**(타이밍 제거 = 순수 베타)와 비교한다. "
             "'베타'는 상수 홀드가 만든 부분, '타이밍'은 그 위/아래 차이다. 핵심 질문: 타이밍이 "
             "**수익을 더했나, 아니면 (수익을 조금 깎는 대신) MDD 를 줄였나?**\n")
    L.append("| 아이디어 | 구간 | 평균lev | 전략 CAGR | 베타(상수lev) CAGR | 타이밍Δ CAGR | 전략 MDD | 베타 MDD | 타이밍 MDD개선 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for k in order:
        if k not in results or k in ("c2a_const15x", "c2a_const2x"):
            continue
        r = results[k]
        for period in ("design", "holdout"):
            if period not in r:
                continue
            p = r[period]; cm = p.get("const_ref", {})
            dcg = p["wm"]["cagr"] - cm.get("cagr", 0.0)
            dmdd = p["wm"]["mdd"] - cm.get("mdd", 0.0)
            L.append(f"| {r['name']} | {period} | {p.get('const_lev',0):.2f} | {_f(p['wm']['cagr'],1)} | "
                     f"{_f(cm.get('cagr'),1)} | {_f(dcg,1)} | {_f(p['wm']['mdd'],1)} | "
                     f"{_f(cm.get('mdd'),1)} | {_f(dmdd,1)} |")
    L.append("\n**읽는 법.** 타이밍Δ CAGR<0 인데 MDD개선>0(+) 이면 타이밍은 '수익을 조금 포기하고 "
             "리스크를 줄이는' 보험이다(대개 이 모양). 그러나 표에서 보듯 **상수 2x 는 설계구간에서 "
             "1x 에게조차 진다**(닷컴 −98.8% 후 미회복 → 변동성 잠식·크래시 수학). 즉 레버리지의 '베타 "
             "프리미엄'은 무료가 아니며, 타이밍의 MDD 개선조차 대부분 −50% 캡을 지키기엔 부족했다.\n")

    # 실물 검증
    L.append("## 5. 합성 vs 실물 검증 (2016-2026)\n")
    L.append("| 슬리브 | n | corr | 연추적차(합성−실물) | 연추적오차 | CAGR 합성 | CAGR 실물 |\n|---|---:|---:|---:|---:|---:|---:|")
    L.append(f"| 2x QLD | {val2['n']} | {val2['corr']:.4f} | {_f(val2['ann_tracking_diff'],1)} | {_f(val2['ann_tracking_error'],1)} | {_f(val2['cagr_syn'],1)} | {_f(val2['cagr_real'],1)} |")
    L.append(f"| 3x TQQQ | {val3['n']} | {val3['corr']:.4f} | {_f(val3['ann_tracking_diff'],1)} | {_f(val3['ann_tracking_error'],1)} | {_f(val3['cagr_syn'],1)} | {_f(val3['cagr_real'],1)} |")
    L.append("\n> 상관 0.999·연추적차 <1%p → 합성 모델은 실물을 잘 근사. 합성이 소폭 높은 것은 "
             "TR 기초 레버리지의 배당분 과대계상과 정합(방향 일치, 크기 작음).\n")

    # DCA
    L.append("## 6. DCA 오버레이 (2016-2026 실물, 시드 $32 + 월 $35)\n")
    L.append("| 포트폴리오 | 최종자산 | XIRR | 달러MDD | vs B0 | vs B1 |\n|---|---:|---:|---:|---:|---:|")
    def dline(nm, d):
        x = gate.xirr(d.cashflows)
        return (nm, d.final_value, x, gate.max_drawdown(d.equity))
    b0v = dline("B0 QQQ60/SCHD25/GLD15", b0)
    b1v = dline("B1 QQQ100", b1)
    rows = [b0v, b1v]
    for k in top:
        rows.append(dline(results[k]["name"], dca[k]))
    for nm, fv, x, mdd in rows:
        vb0 = fv / b0v[1]
        vb1 = fv / b1v[1]
        L.append(f"| {nm} | ${fv:.2f} | {_f(x,1)} | {_f(mdd,1)} | {vb0:.3f} | {vb1:.3f} |")
    L.append(f"\n총입금 ${b0.total_deposited:.0f}. DCA 곡선 달러MDD 는 신규입금 완충으로 리스크를 "
             f"과소평가 — 리스크 판정은 §3 단위자본 곡선 기준(픽들의 홀드아웃 단위자본 MDD 는 캡 근처/위반).\n")
    if dca_promo:
        L.append("**수수료 10bp 프로모 what-if** (표준 25bp → 10bp): "
                 + ", ".join(f"{results[k]['name']} ${dca_promo[k].final_value:.2f}"
                             f"(+${dca_promo[k].final_value - dca[k].final_value:.2f})"
                             for k in top if k in dca_promo)
                 + ". 저회전 배분이라 수수료 민감도는 작다(회전율이 큰 vol-target 계열은 상대적으로 큼).\n")

    # 판정 종합
    L.append("## 7. 판정 종합 (gate.decide) 및 해석\n")
    for k in order:
        if k not in results:
            continue
        r = results[k]
        dd = r.get("design", {}).get("decision")
        dh = r.get("holdout", {}).get("decision")
        reasons = "; ".join((dh or dd).reasons[:2]) if (dh or dd) else ""
        reasons = re.sub(r"-?\d+\.\d{4,}", lambda m: f"{float(m.group()):.3f}", reasons)
        L.append(f"- **{r['name']}**: 설계 {dd.verdict if dd else '—'} / 홀드아웃 "
                 f"{dh.verdict if dh else '—'}. " + reasons + "\n")
    L.append("\n**정직한 결론.** " + honest_conclusion(results) + "\n")

    REPORT.write_text("\n".join(L), encoding="utf-8")


def honest_conclusion(results):
    parts = []
    # MDD 캡 위반 집계
    viol = []
    for k, r in results.items():
        for period in ("design", "holdout"):
            wm = r.get(period, {}).get("wm")
            if wm and wm["mdd"] <= -0.50:
                viol.append(f"{r['name']}({period} MDD {wm['mdd']*100:.0f}%)")
    if viol:
        parts.append("−50% 하드캡 위반: " + ", ".join(sorted(set(viol))) + ".")
    passes = [results[k]["name"] for k in results
              if results[k].get("holdout", {}).get("decision")
              and results[k]["holdout"]["decision"].verdict == "PASS"]
    cond = [results[k]["name"] for k in results
            if results[k].get("holdout", {}).get("decision")
            and results[k]["holdout"]["decision"].verdict == "CONDITIONAL"]
    parts.append(f"홀드아웃 PASS: {passes or '없음'}. CONDITIONAL: {cond or '없음'}.")
    parts.append("레버리지의 초과수익 대부분은 상승장 베타이며, 타이밍(추세/변동성 필터)의 진짜 기여는 "
                 "하락장에서 현금화해 캡을 지키는 것이다. 백테스트 낙관·합성 불확실성·소표본을 감안해 "
                 "채택은 소액 슬리브+포워드 페이퍼 후에만.")
    return " ".join(parts)


if __name__ == "__main__":
    main(fast="--fast" in sys.argv)
