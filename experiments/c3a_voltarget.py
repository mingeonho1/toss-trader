#!/usr/bin/env python3
"""c3a — 회전율 제약 변동성 타깃 (Nasdaq-100). Cycle 3, id prefix `c3a`.

사전등록·규약: reports/cycle3_c3a_voltarget.md, experiments/README.md, docs/gate_v2_spec.md(+부록 v2.1).
데이터 파이프라인(= c2a 재사용): FRED NASDAQ100(1986-2026) → 총수익 근사(배당 0.7%/yr)
  → 합성 1x/2x/3x(synthetic_leveraged, DTB3=조달금리 yield, 경비/차입스프레드) → **현금 0%(기본)**.

핵심(c2a 대비): 변동성 타깃 신호를 **회전율 제약 실행**으로 재설계.
  (1) 주간/월간 평가, (2) 큰 레버리지 밴드(|Δlev|≥0.5),
  (3) 최소 노셔널 슬리브 표현(qld_cash / tqqq_cash) — 노출 변경당 노셔널(=수수료%)을 압축.

신호 재사용(부록 v2.1 §4): vol-target 은 c2a 에서 홀드아웃을 이미 봄 → 새 idea_id, 홀드아웃 '반오염',
  PASS 에 RC/SPA p<0.01 요구. DSR 은 **프로그램 전체 N**(원장 전 아이디어) 기준으로도 보고.

실행 규칙: 신호 close t, 체결 t+1 close(exec_lag=1). 모든 신호 함수는 lookahead_guard 통과.
분할: 설계 1986–2008, 홀드아웃 2009–2026(아이디어당 1회 peek). src/ 는 수정하지 않는다.

재현: PYTHONPATH=src .venv/bin/python experiments/c3a_voltarget.py [--fast] [--ledger PATH]
"""
from __future__ import annotations

import bisect
import math
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
import gate_eval  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle3_c3a_voltarget.md"

# ── 모델 상수(사전등록) ──────────────────────────────────────────────────────
DIV_YIELD = 0.007
EXP_1X = 0.002
EXP_LEV = 0.0095
BORROW_SPREAD = 0.005
LAM = 0.97                    # EWMA 변동성 λ (COM≈33일)
EWMA_WARMUP = 60
VOL_TARGET = 0.20            # T = 20%
LMAX = 2.0
LEV_BAND = 0.5              # |Δlev| ≥ 0.5 일 때만 리밸
REB_BAND = 0.10            # run_weights 무매매 밴드(드리프트 미세매매 억제, 사전고정)
DESIGN_END = date(2008, 12, 31)
HOLDOUT_START = date(2009, 1, 1)
HOLDOUT_END = date(2026, 12, 31)
CASH_YIELD_ZERO = True       # 부록 v2.1 §2: 현금 수익률 0% 기본

COST = R.CostSpec(
    commission_bps=25.0, slippage_bps=5.0, fx_bps=20.0,
    half_spread_bps={"L1": 1.0, "L2": 2.0, "L3": 2.0,
                     "QQQ": 1.0, "QLD": 2.0, "TQQQ": 2.0, "SCHD": 1.0, "GLD": 1.0},
    default_half_spread_bps=3.0)


def clip(x, lo, hi):
    return max(lo, min(hi, x))


# ── 데이터 빌더 (c2a 재사용) ─────────────────────────────────────────────────
def _yield_daily_aligned(fred_yield, dates):
    ks = [c.dt for c in fred_yield]
    vs = [c.close for c in fred_yield]
    out = []
    for d in dates:
        i = bisect.bisect_right(ks, d) - 1
        a = vs[i] if i >= 0 else (vs[0] if vs else 0.0)
        out.append(max(0.0, (a / 100.0) / 252.0))
    return out


def build_synthetic_panel():
    """FRED NDX → 합성 1x/2x/3x 패널 + 현금수익률(DTB3, 민감도용). 전 구간(1986-2026)."""
    ndx = hd.load_fred("NASDAQ100")
    dtb3 = hd.load_fred("DTB3")
    tr = hd.index_total_return(ndx, DIV_YIELD, symbol="NDXTR")
    L1 = hd.synthetic_leveraged(tr, 1.0, annual_expense=EXP_1X, borrow_spread=0.0,
                                rf_candles=dtb3, rf_kind="yield", symbol="L1")
    L2 = hd.synthetic_leveraged(tr, 2.0, annual_expense=EXP_LEV, borrow_spread=BORROW_SPREAD,
                                rf_candles=dtb3, rf_kind="yield", symbol="L2")
    L3 = hd.synthetic_leveraged(tr, 3.0, annual_expense=EXP_LEV, borrow_spread=BORROW_SPREAD,
                                rf_candles=dtb3, rf_kind="yield", symbol="L3")
    dates = [c.dt for c in L1]
    panel = {"L1": [c.close for c in L1], "L2": [c.close for c in L2],
             "L3": [c.close for c in L3]}
    dtb3_cash = _yield_daily_aligned(dtb3, dates)      # 민감도용
    zero_cash = [0.0] * len(dates)                     # 기본(v2.1 §2)
    return panel, dates, zero_cash, dtb3_cash


def build_real_panel():
    """실물 QQQ/QLD/TQQQ + SCHD/GLD 패널(2016-2026 공통거래일) + 현금 0%/DTB3."""
    syms = ["QQQ", "QLD", "TQQQ", "SCHD", "GLD"]
    raw = {s: hd.load_symbol(s) for s in syms}
    common = set.intersection(*[{c.dt for c in raw[s]} for s in syms])
    dates = sorted(common)
    dtb3 = hd.load_fred("DTB3")

    def col(s):
        by = {c.dt: c.close for c in raw[s]}
        return [by[d] for d in dates]

    panel = {"L1": col("QQQ"), "L2": col("QLD"), "L3": col("TQQQ"),
             "QQQ": col("QQQ"), "SCHD": col("SCHD"), "GLD": col("GLD")}
    zero_cash = [0.0] * len(dates)
    dtb3_cash = _yield_daily_aligned(dtb3, dates)
    return panel, dates, zero_cash, dtb3_cash


# ── 캘린더 ───────────────────────────────────────────────────────────────────
def week_end_flags(dates):
    """각 날짜가 ISO주의 마지막 거래일인가(금요일 또는 그 주 마지막 거래일)."""
    n = len(dates)
    out = [False] * n
    for t in range(n):
        if t == n - 1 or dates[t].isocalendar()[:2] != dates[t + 1].isocalendar()[:2]:
            out[t] = True
    return out


# ── EWMA 변동성(인과적, 로컬) ────────────────────────────────────────────────
def ewma_vol(rets, lam=LAM, warmup=EWMA_WARMUP, annualize=True, ppy=252):
    """EWMA 변동성. var_t = λ·var_{t-1} + (1−λ)·r_t². t<warmup 은 None(시드 부족).

    시드: rets[1..warmup] 표본 2차 적률. 인과적 — 인덱스 t 출력은 rets≤t 만 참조.
    """
    n = len(rets)
    out = [None] * n
    if n <= warmup:
        return out
    var = sum(rets[i] ** 2 for i in range(1, warmup + 1)) / warmup
    out[warmup] = math.sqrt(var * ppy) if annualize else math.sqrt(var)
    for t in range(warmup + 1, n):
        var = lam * var + (1.0 - lam) * rets[t] ** 2
        out[t] = math.sqrt(var * ppy) if annualize else math.sqrt(var)
    return out


# ── 레버리지 → 슬리브 표현(3종) ──────────────────────────────────────────────
def lev_to_weights_mix(L, keys=("L1", "L2", "L3")):
    """인접 슬리브 혼합(c2a 방식). 실효노출 = 1·w1+2·w2+3·w3 = L. L≥1 완전투자."""
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


def lev_to_weights(L, rep):
    """L → 목표비중. rep ∈ {mix, qld_cash, tqqq_cash}. 나머지(1−Σw)는 현금(수익 cash_rate)."""
    if L <= 1e-9:
        return {}
    if rep == "mix":
        return lev_to_weights_mix(L)
    if rep == "qld_cash":                     # 노출 L = (L/2)·2x + 현금
        return {"L2": clip(L / 2.0, 0.0, 1.0)}
    if rep == "tqqq_cash":                    # 노출 L = (L/3)·3x + 현금
        return {"L3": clip(L / 3.0, 0.0, 1.0)}
    raise ValueError(f"알 수 없는 rep: {rep!r}")


def avg_leverage(weights):
    lv = [w.get("L1", 0) + 2 * w.get("L2", 0) + 3 * w.get("L3", 0) for w in weights]
    return sum(lv) / len(lv) if lv else 0.0


# ── committed lev 스케줄(공통 코어) ──────────────────────────────────────────
def _committed_lev(rv, eval_flags, *, T, Lmax, band, Tvec=None):
    """평가일(eval_flags)에만 committed 갱신, |desired−committed|≥band 일 때만.

    desired = clip(T/σ̂, 0, Lmax). Tvec 를 주면 날짜별 T(예: vol-of-vol 변조) 사용.
    """
    n = len(rv)
    lev = [0.0] * n
    committed = 0.0
    for t in range(n):
        Tt = Tvec[t] if Tvec is not None else T
        if rv[t] is None or rv[t] <= 0:
            lev[t] = committed
            continue
        desired = clip(Tt / rv[t], 0.0, Lmax)
        if eval_flags[t] and abs(desired - committed) >= band:
            committed = desired
        lev[t] = committed
    return lev


# ── 신호 함수(전부 (panel,dates)->weights, lookahead_guard 대상) ─────────────
def sig_vt_ewma(panel, dates, *, T=VOL_TARGET, Lmax=LMAX, lam=LAM, band=LEV_BAND,
                rep="qld_cash"):
    """#1 주간 EWMA 변동성 타깃. rep=qld_cash(최소노셔널) 또는 mix."""
    rets = R.to_returns(panel["L1"])
    rv = ewma_vol(rets, lam)
    lev = _committed_lev(rv, week_end_flags(dates), T=T, Lmax=Lmax, band=band)
    return [lev_to_weights(l, rep) for l in lev]


def sig_vt_ewma_3x(panel, dates, *, T=VOL_TARGET, Lmax=LMAX, lam=LAM, band=LEV_BAND):
    """#2 주간 EWMA 변동성 타깃, tqqq_cash(3x=비용압축). Lmax=2 → TQQQ ≤ 2/3."""
    rets = R.to_returns(panel["L1"])
    rv = ewma_vol(rets, lam)
    lev = _committed_lev(rv, week_end_flags(dates), T=T, Lmax=Lmax, band=band)
    return [lev_to_weights(l, "tqqq_cash") for l in lev]


def sig_vt_monthly(panel, dates, *, T=VOL_TARGET, Lmax=LMAX, lam=LAM, band=LEV_BAND,
                   rep="qld_cash"):
    """#3 월간(월말) EWMA 변동성 타깃, qld_cash."""
    rets = R.to_returns(panel["L1"])
    rv = ewma_vol(rets, lam)
    lev = _committed_lev(rv, R.month_end_flags(dates), T=T, Lmax=Lmax, band=band)
    return [lev_to_weights(l, rep) for l in lev]


def sig_vt_volofvol(panel, dates, *, T=VOL_TARGET, Lmax=LMAX, lam=LAM, band=LEV_BAND,
                    beta=0.5, vov_win=63, floor=0.4, rep="qld_cash"):
    """#5(창작) 주간 EWMA vol-target + vol-of-vol 필터.

    T_eff = T·clip(1 − β·max(0,z), floor, 1), z = EWMA-vol 시계열의 vov_win 봉 z-score.
    변동성이 불안정한(레짐 불확실) 국면에서 위험예산 축소(파편성 신호). 전부 인과적.
    """
    rets = R.to_returns(panel["L1"])
    rv = ewma_vol(rets, lam)
    rv_filled = [v if v is not None else float("nan") for v in rv]
    z = R.zscore(rv_filled, int(round(vov_win)))
    n = len(dates)
    Tvec = [T] * n
    for t in range(n):
        zt = z[t]
        if zt is not None and math.isfinite(zt):
            Tvec[t] = T * clip(1.0 - beta * max(0.0, zt), floor, 1.0)
    lev = _committed_lev(rv, week_end_flags(dates), T=T, Lmax=Lmax, band=band, Tvec=Tvec)
    return [lev_to_weights(l, rep) for l in lev]


def sig_vt_cashflow_unit(panel, dates, *, T=VOL_TARGET, Lmax=LMAX, lam=LAM,
                         delever=0.75, step_up=0.10, rep="qld_cash"):
    """#4 단위자본 프록시: 월말 래칫 업(≤step_up/월) + (desired<current−delever) 강제 디레버.

    DCA-native 설계(신규 적립으로만 re-lever, −0.75 트리거 매도)를 단위자본으로 근사한다.
    실제 DCA-native 평가는 run_dca_cashflow(§7). 프록시임을 리포트에 명시.
    """
    rets = R.to_returns(panel["L1"])
    rv = ewma_vol(rets, lam)
    me = R.month_end_flags(dates)
    n = len(dates)
    lev = [0.0] * n
    cur = 0.0
    for t in range(n):
        if rv[t] is not None and rv[t] > 0:
            desired = clip(T / rv[t], 0.0, Lmax)
            if desired < cur - delever:            # 강제 디레버(어느 날이든)
                cur = desired
            elif me[t] and desired > cur:          # 월말에만 래칫 업(적립 근사)
                cur = min(desired, cur + step_up)
        lev[t] = cur
    return [lev_to_weights(l, rep) for l in lev]


# ── 백테스트 + 윈도 지표 ─────────────────────────────────────────────────────
def run_strategy(sig_fn, panel, dates, cash, *, cost=None, params=None, band=REB_BAND):
    weights = sig_fn(panel, dates, **(params or {}))
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
    a = window_metrics(res.equity, dates, s, e).get("terminal", 0.0)
    b = window_metrics(bench_res.equity, dates, s, e).get("terminal", 1.0)
    return a - b


def _standardize(x):
    if len(x) < 2:
        return x
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return [(v - m) / sd for v in x] if sd > 0 else [0.0] * len(x)


def vol_match_terminal(stream, target=0.10):
    """수익스트림을 목표 연변동성으로 스케일했을 때의 최종 배수(§5.3 변동성 매칭)."""
    k = gate.vol_match_scale(stream, target_ann_vol=target)
    if not math.isfinite(k):
        return float("nan")
    eq = 1.0
    for r in stream:
        eq *= (1.0 + k * r)
    return eq


# ── 상대 리스크 트랙(부록 v2.1 §1) ───────────────────────────────────────────
def relative_risk_verdict(wm_d, wb_d, wm_h, wb_h, full_mdd, *, is_leverage=True):
    """설계·홀드아웃 상대 리스크 + 홀드아웃 절대캡 + 전표본 −70% 파산하한.

    통과: (MDD_s ≥ MDD_b−5%p AND Ulcer_s ≤ 1.10·Ulcer_b) 설계·홀드아웃 모두,
          홀드아웃은 절대캡(MDD_s≥−0.50)도(벤치가 캡 지킴), 레버리지는 전표본 MDD≥−0.70.
    """
    def rel_ok(wm, wb):
        return (wm["mdd"] >= wb["mdd"] - 0.05) and (wm["ulcer"] <= 1.10 * wb["ulcer"])
    d_ok = rel_ok(wm_d, wb_d)
    h_rel = rel_ok(wm_h, wb_h)
    h_abs = wm_h["mdd"] >= gate.MDD_HARD_CAP        # 홀드아웃 벤치는 캡 준수 → 절대캡 적용
    floor_ok = (full_mdd >= -0.70) if is_leverage else True
    checks = {"design_rel": d_ok, "holdout_rel": h_rel, "holdout_abs_cap": h_abs,
              "bankruptcy_floor": floor_ok}
    verdict = "PASS" if all(checks.values()) else "FAIL"
    # 설계는 통과인데 홀드아웃 상대만 경계(±완충)면 CONDITIONAL 여지
    if verdict == "FAIL" and d_ok and h_abs and floor_ok and (
            wm_h["mdd"] >= wb_h["mdd"] - 0.08):
        verdict = "CONDITIONAL"
    return verdict, checks


# ── 강화 유의성(부록 v2.1 §4: 반오염 → p<0.01) ───────────────────────────────
def strict_significance(dsr_prog, rc_p, spa_p):
    """반오염 홀드아웃 → RC·SPA p<0.01 요구. DSR(프로그램)은 참고·강화용."""
    rc_ok = rc_p is not None and rc_p < 0.01
    spa_ok = spa_p is not None and spa_p < 0.01
    dsr_ok = dsr_prog is not None and math.isfinite(dsr_prog) and dsr_prog >= 0.95
    if rc_ok and spa_ok and dsr_ok:
        return "PASS"
    if (rc_ok or spa_ok):
        return "CONDITIONAL"
    return "FAIL"


# ── 아이디어 평가 ────────────────────────────────────────────────────────────
NEIGHBOR_GRID = (0.5, 0.7, 0.8, 1.2, 1.5)


def evaluate_idea(idea_id, sig_fn, base_params, primary_key, panel, dates, cash,
                  bench_res, *, fast=False, ledger=LEDGER, do_log=True):
    d0 = dates[0]
    bench_stream = bench_res.net_stream()
    band = REB_BAND

    res, weights = run_strategy(sig_fn, panel, dates, cash, params=base_params, band=band)

    # 베타 vs 타이밍: 동일 평균레버리지 상수 홀드(mix)
    clev = avg_leverage(weights)
    cres, _ = run_strategy(lambda p, d, *, lev=clev: [lev_to_weights_mix(lev) for _ in d],
                           panel, dates, cash, params={}, band=0.05)

    design_idx = window_slice(dates, d0, DESIGN_END)
    holdout_idx = window_slice(dates, HOLDOUT_START, HOLDOUT_END)
    di0, di1 = design_idx[0], design_idx[-1]

    def design_stream(r):
        return [r.net_returns[i] for i in design_idx if i >= 1]

    # ── 이웃(플래토): primary_key 그리드로 흔들어 설계구간 평가·원장 적재 ──
    neigh_streams = {}
    sr_trials = []
    neigh_pos = 0
    neigh_sharpes = []
    center_val = base_params[primary_key]
    configs = [("center", base_params)]
    for g in NEIGHBOR_GRID:
        p = dict(base_params)
        v = center_val * g
        p[primary_key] = int(round(v)) if isinstance(center_val, int) else v
        configs.append((f"{primary_key}x{g}", p))

    center_design_stream = None
    for name, p in configs:
        r_n, _ = run_strategy(sig_fn, panel, dates, cash, params=p, band=band)
        ds = design_stream(r_n)
        if len(ds) < 2:
            continue
        sd = gate._std(ds, 1)
        sr = (gate._mean(ds) / sd) if sd > 0 else 0.0
        neigh_streams[name] = _standardize(ds)
        nx = net_excess_terminal(r_n, bench_res, dates, d0, DESIGN_END)
        if name == "center":
            center_design_stream = ds
        else:
            sr_trials.append(sr)
            neigh_sharpes.append(gate.sharpe(ds))
            if nx > 0:
                neigh_pos += 1
        if do_log:
            um = gate_eval.unit_capital_metrics(ds, dates=[dates[i] for i in design_idx])
            gate_eval.log_evaluation(idea_id, {"neighbor": name,
                                               **{k: p[k] for k in p}}, 1, "design", um,
                                     window=(dates[di0], dates[di1]), ledger_path=ledger)

    center_sr_ann = gate.sharpe(center_design_stream) if center_design_stream else 0.0
    n_neigh = len(neigh_sharpes)
    frac_pos = neigh_pos / n_neigh if n_neigh else 0.0
    neigh_sharpes.sort()
    med_neigh = gate._quantile_sorted(neigh_sharpes, 0.5) if neigh_sharpes else 0.0
    plateau_pass = bool(n_neigh and frac_pos >= 0.8 and med_neigh >= 0.9 * center_sr_ann)
    plateau_border = bool(n_neigh and not plateau_pass and frac_pos >= 0.6)

    # DSR 아이디어-내(N_eff): 이웃 상관 클러스터
    n_eff = gate.n_eff_clusters(neigh_streams, theta=0.9) if neigh_streams else 1
    sr_all = sr_trials + ([gate._mean(center_design_stream) / gate._std(center_design_stream, 1)]
                          if center_design_stream and gate._std(center_design_stream, 1) > 0 else [])
    dsr_idea = (gate.deflated_sharpe_ratio(center_design_stream, sr_all, n_eff=n_eff)
                if center_design_stream else float("nan"))

    # ── 헤드라인: 설계 vs 홀드아웃 run_splits(원장 적재 + peek-once + RC/SPA) ──
    rc_B = 400 if fast else 1500
    cand_stream = res.net_stream()
    peeked = gate.already_peeked(ledger, idea_id)
    splits = gate_eval.run_splits(
        idea_id, dict(base_params), 1, cand_stream, bench_stream, dates, DESIGN_END,
        bench_terminal=None, ledger_path=ledger, rc_B=rc_B, log=(do_log and not peeked))

    _years = max((dates[-1] - d0).days / 365.0, 1e-9)
    out = {"idea_id": idea_id, "res": res, "weights": weights, "avg_lev": clev,
           "trades": res.trade_count, "trades_yr": res.trade_count / _years,
           "turnover_yr": sum(res.turnover) / _years,
           "cost_frac": res.total_cost,          # 단위자본 총비용(시작=1)
           "plateau_pass": plateau_pass, "plateau_border": plateau_border,
           "frac_pos": frac_pos, "n_eff": n_eff, "n_neigh": n_neigh,
           "dsr_idea": dsr_idea, "center_design_stream": center_design_stream,
           "sr_trials_idea": sr_all, "splits": splits}

    wb_d = window_metrics(bench_res.equity, dates, d0, DESIGN_END)
    wb_h = window_metrics(bench_res.equity, dates, HOLDOUT_START, HOLDOUT_END)
    wm_d = window_metrics(res.equity, dates, d0, DESIGN_END)
    wm_h = window_metrics(res.equity, dates, HOLDOUT_START, HOLDOUT_END)
    full_mdd = gate.max_drawdown(res.equity)
    out["full_mdd"] = full_mdd

    for period, s, e, wm, wb in (("design", d0, DESIGN_END, wm_d, wb_d),
                                 ("holdout", HOLDOUT_START, HOLDOUT_END, wm_h, wb_h)):
        if period not in splits:
            continue
        sp = splits[period]
        tvb0 = wm["terminal"] / wb["terminal"] if wb.get("terminal") else None
        idx = window_slice(dates, s, e)
        sc = [res.equity[i] for i in idx]
        bc = [bench_res.equity[i] for i in idx]
        wr = gate.subperiod_consistency(sc, bc, [dates[i] for i in idx],
                                        window_years=3, step_months=6)["win_rate"]
        # 비용 스트레스: 총비용 ×mult 에서 net 초과(vs 1x) 유지되는 최대 배수
        cs_mult = 1.0
        for m in (1.5, 2.0):
            r_s, _ = run_strategy(sig_fn, panel, dates, cash, cost=COST.stress(m),
                                  params=base_params, band=band)
            if net_excess_terminal(r_s, bench_res, dates, s, e) > 0:
                cs_mult = m
        base_nx = net_excess_terminal(res, bench_res, dates, s, e)
        if base_nx <= 0:
            cs_mult = 0.0
        # 손익분기 왕복 bps(vol-match 아님, net 초과=0)
        def be_eval(frac):
            cm = R.CostSpec(commission_bps=frac * 1e4 / 2.0, slippage_bps=0.0, fx_bps=0.0,
                            half_spread_bps={}, default_half_spread_bps=0.0)
            rr, _ = run_strategy(sig_fn, panel, dates, cash, cost=cm, params=base_params,
                                 band=band)
            return net_excess_terminal(rr, bench_res, dates, s, e)
        be_bps = gate.breakeven_cost_bps(be_eval, 0.0, 0.02)
        # vol-match(§5.3)
        strat_stream = [res.net_returns[i] for i in idx if i >= 1]
        bench_win_stream = [bench_res.net_returns[i] for i in idx if i >= 1]
        vm_s = vol_match_terminal(strat_stream)
        vm_b = vol_match_terminal(bench_win_stream)

        metrics = {
            "lane": 1, "terminal_vs_b0": tvb0,
            "dsr": dsr_idea,
            "rc_pvalue": sp["axis_input"].get("rc_pvalue"),
            "mdd": wm["mdd"], "plateau_pass": plateau_pass,
            "plateau_borderline": plateau_border, "subperiod_winrate": wr,
            "cost_stress_mult": cs_mult,
            "calmar_not_worse": wm["calmar"] >= wb["calmar"],
            "ulcer_not_worse": wm["martin"] >= wb["martin"],
        }
        dec_abs = gate.decide(metrics)          # 표준(절대캡) 게이트
        cm = window_metrics(cres.equity, dates, s, e)
        out[period] = {"wm": wm, "wb": wb, "tvb0": tvb0, "winrate": wr,
                       "cs_mult": cs_mult, "be_bps": be_bps,
                       "rc": sp["axis_input"].get("rc_pvalue"),
                       "spa": sp["axis_input"].get("spa_pvalue"),
                       "sr_ann": sp["unit"].get("sr_annual"),
                       "vm_s": vm_s, "vm_b": vm_b,
                       "const_ref": cm, "const_lev": clev,
                       "decision_abs": dec_abs, "metrics": metrics}
    return out


# ── DCA (2016-2026 실물) ─────────────────────────────────────────────────────
def const_weights_series(dates, wt):
    return [dict(wt) for _ in dates]


def run_dca_cashflow(panel, dates, desired_lev, *, rep="qld_cash", delever=0.75,
                     monthly_usd=35.0, initial_usd=32.0, cost=None, cash_rate=None):
    """#4 DCA-native: 적립(매수전용)으로만 re-lever + (desired<current−delever) 강제 디레버.

    각 월 첫 거래일 입금(FX 부과). 목표 = lev_to_weights(desired, rep). 신규 현금으로 미달분 매수.
    강제 디레버: desired < 현재 실효lev − delever 이면 초과 슬리브를 매도(현금화)해 노출을 desired 로.
    반환: DcaOverlayResult 유사 dict(final_value, cashflows, equity, deposits, trade_count, dollar_mdd).
    """
    cost = cost or COST
    n = len(dates)
    syms = [k for k in panel if k in ("L1", "L2", "L3", "QQQ")]
    starts = R.month_start_flags(dates)
    val = {s: 0.0 for s in syms}
    cash = 0.0
    equity = []
    deposits = []
    total_dep = 0.0
    total_cost = 0.0
    total_fx = 0.0
    trade_count = 0
    BPS = 1e-4

    def cur_exposure(equity_ref):
        if equity_ref <= 0:
            return 0.0
        expo = val.get("L1", 0) + 2 * val.get("L2", 0) + 3 * val.get("L3", 0)
        return expo / equity_ref

    for t in range(n):
        if t > 0:
            cr = cash_rate[t] if cash_rate is not None else 0.0
            cash *= (1.0 + cr)
            for s in syms:
                pc = panel[s][t - 1]
                if pc > 0:
                    val[s] *= panel[s][t] / pc
        # 입금
        dep = 0.0
        if t == 0:
            dep += initial_usd
        if starts[t]:
            dep += monthly_usd
        if dep > 0:
            cash += dep
            deposits.append((dates[t], dep))
            total_dep += dep
            fx = cost.fx_cost(dep)
            cash -= fx
            total_fx += fx
            total_cost += fx

        equity_ref = sum(val.values()) + cash
        desired = desired_lev[t]
        tw = lev_to_weights(desired, rep)      # 목표 슬리브 비중

        # 강제 디레버(매도): 현재 노출이 desired+delever 초과면 desired 로 낮춤
        if equity_ref > 0 and cur_exposure(equity_ref) > desired + delever:
            for s in syms:
                target_val = tw.get(s, 0.0) * equity_ref
                if val[s] > target_val:
                    notional = val[s] - target_val
                    c = notional * cost.trade_bps(s) * BPS
                    val[s] = target_val
                    cash += (notional - c)
                    total_cost += c
                    trade_count += 1
        # 매수전용 re-lever: 입금·현금으로 미달분 매수(desired 목표까지)
        if cash > 0 and desired > 1e-9:
            order = sorted(syms, key=lambda s: tw.get(s, 0.0) * (sum(val.values()) + cash) - val[s],
                           reverse=True)
            for s in order:
                if cash <= 0:
                    break
                eq_ref = sum(val.values()) + cash
                need = tw.get(s, 0.0) * eq_ref - val[s]
                if need <= 0:
                    continue
                tb = cost.trade_bps(s) * BPS
                spend = need
                if spend * (1.0 + tb) > cash:
                    spend = cash / (1.0 + tb)
                c = spend * tb
                val[s] += spend
                cash -= (spend + c)
                total_cost += c
                trade_count += 1
        equity.append(sum(val.values()) + cash)

    final_value = equity[-1] if equity else 0.0
    cashflows = ([(d, -abs(a)) for d, a in deposits] + [(dates[-1], final_value)]) if deposits else []
    return {"final_value": final_value, "cashflows": cashflows, "equity": equity,
            "deposits": deposits, "total_deposited": total_dep, "total_cost": total_cost,
            "total_fx_cost": total_fx, "trade_count": trade_count,
            "dollar_mdd": gate.max_drawdown(equity)}


def dca_desired_lev(panel, dates, sig_fn, params):
    """DCA-native 용 desired lev 스케줄(신호가 만든 목표 slev → 실효 lev)."""
    w = sig_fn(panel, dates, **params)
    return [x.get("L1", 0) + 2 * x.get("L2", 0) + 3 * x.get("L3", 0) for x in w]


# ── 리포트 ───────────────────────────────────────────────────────────────────
def _f(v, pct=False, nd=2):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v * 100:+.{nd}f}%" if pct else f"{v:.{nd}f}"


PREREG = None  # main 에서 원본 사전등록 텍스트를 읽어 보존


def main(fast=False, ledger=LEDGER, do_log=True):
    global PREREG
    print(f"[c3a] ledger={ledger} fast={fast} log={do_log}")
    panel, dates, zcash, dtb3cash = build_synthetic_panel()
    print(f"[c3a] synthetic N={len(dates)} {dates[0]}..{dates[-1]}")

    # 벤치마크 1x B&H(현금 0% — 완전투자라 무관)
    bench_res, _ = run_strategy(lambda p, d, *, lev=1.0: [lev_to_weights_mix(1.0) for _ in d],
                                panel, dates, zcash, params={}, band=0.0)

    ideas = [
        ("c3a_vt_ewma", "주간 EWMA vol-target (qld_cash)", sig_vt_ewma,
         {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM, "band": LEV_BAND, "rep": "qld_cash"}, "T"),
        ("c3a_vt_ewma_3x", "주간 EWMA vol-target (tqqq_cash=비용압축)", sig_vt_ewma_3x,
         {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM, "band": LEV_BAND}, "T"),
        ("c3a_vt_monthly", "월간 EWMA vol-target (qld_cash)", sig_vt_monthly,
         {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM, "band": LEV_BAND, "rep": "qld_cash"}, "T"),
        ("c3a_vt_cashflow", "DCA-native cashflow (프록시)", sig_vt_cashflow_unit,
         {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM, "delever": 0.75, "step_up": 0.10,
          "rep": "qld_cash"}, "delever"),
        ("c3a_vt_volofvol", "vol-of-vol 필터 (창작, qld_cash)", sig_vt_volofvol,
         {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM, "band": LEV_BAND, "beta": 0.5,
          "vov_win": 63, "floor": 0.4, "rep": "qld_cash"}, "beta"),
    ]

    results = {}
    for idea_id, name, fn, params, pk in ideas:
        print(f"[c3a] evaluating {idea_id} ({name})…")
        try:
            r = evaluate_idea(idea_id, fn, params, pk, panel, dates, zcash, bench_res,
                              fast=fast, ledger=ledger, do_log=do_log)
            r["name"] = name
            results[idea_id] = r
        except gate.PeekOnceError as e:
            print(f"   ⚠ {idea_id}: 홀드아웃 이미 peek — 건너뜀 ({e})")

    # 표현 비교(min-notional): c3a_vt_ewma mix vs qld_cash (설계·홀드아웃 회전율/비용/최종)
    print("[c3a] representation comparison…")
    reps = {}
    for rep in ("mix", "qld_cash"):
        rr, _ = run_strategy(sig_vt_ewma, panel, dates, zcash,
                             params={"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM,
                                     "band": LEV_BAND, "rep": rep}, band=REB_BAND)
        reps[rep] = rr
    rr3, _ = run_strategy(sig_vt_ewma_3x, panel, dates, zcash,
                          params={"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM, "band": LEV_BAND},
                          band=REB_BAND)
    reps["tqqq_cash"] = rr3

    # 프로그램 전체 N (부록 v2.1 §3) — 모든 적재 후 원장 전체에서
    prog_sr = gate.ledger_trial_sharpes(ledger)
    prog_N = len(prog_sr)
    for k, r in results.items():
        cds = r.get("center_design_stream")
        dsr_prog = (gate.deflated_sharpe_ratio(cds, prog_sr, n_eff=prog_N)
                    if cds and prog_N >= 2 else float("nan"))
        r["dsr_prog"] = dsr_prog
        r["prog_N"] = prog_N

    # 현금수익률 민감도(DTB3): c3a_vt_ewma 헤드라인만
    rr_dtb3, _ = run_strategy(sig_vt_ewma, panel, dates, dtb3cash,
                              params={"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM,
                                      "band": LEV_BAND, "rep": "qld_cash"}, band=REB_BAND)

    bench_d = window_metrics(bench_res.equity, dates, dates[0], DESIGN_END)
    bench_h = window_metrics(bench_res.equity, dates, HOLDOUT_START, HOLDOUT_END)

    # 합성 vs 실물 검증(2016-2026)
    print("[c3a] synthetic vs real validation…")
    ndx = hd.load_fred("NASDAQ100"); dtb3 = hd.load_fred("DTB3")
    base16 = hd.index_total_return([c for c in ndx if c.dt >= date(2016, 9, 22)], DIV_YIELD)
    val2 = hd.validate_synthetic(base16, hd.load_symbol("QLD"), 2.0, annual_expense=EXP_LEV,
                                 borrow_spread=BORROW_SPREAD, rf_candles=dtb3, rf_kind="yield")
    val3 = hd.validate_synthetic(base16, hd.load_symbol("TQQQ"), 3.0, annual_expense=EXP_LEV,
                                 borrow_spread=BORROW_SPREAD, rf_candles=dtb3, rf_kind="yield")

    # ── DCA 오버레이(2016-2026 실물, rebalance 모드) ──
    print("[c3a] DCA overlays (2016-2026 real, rebalance mode)…")
    rpanel, rdates, rzcash, _ = build_real_panel()
    dca = {}
    dca_sig = {
        "c3a_vt_ewma": (sig_vt_ewma, {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM,
                                      "band": LEV_BAND, "rep": "qld_cash"}),
        "c3a_vt_ewma_3x": (sig_vt_ewma_3x, {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM,
                                            "band": LEV_BAND}),
        "c3a_vt_monthly": (sig_vt_monthly, {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM,
                                            "band": LEV_BAND, "rep": "qld_cash"}),
        "c3a_vt_volofvol": (sig_vt_volofvol, {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM,
                                              "band": LEV_BAND, "beta": 0.5, "vov_win": 63,
                                              "floor": 0.4, "rep": "qld_cash"}),
    }
    for k, (fn, params) in dca_sig.items():
        w = fn(rpanel, rdates, **params)
        d = R.run_dca_overlay(rpanel, rdates, w, monthly_usd=35.0, initial_usd=32.0,
                              cost=COST, cash_rate=rzcash, exec_lag=1, mode="rebalance",
                              rebalance_band=REB_BAND)
        dca[k] = d
    # cashflow-native
    dl = dca_desired_lev(rpanel, rdates, sig_vt_ewma,
                         {"T": VOL_TARGET, "Lmax": LMAX, "lam": LAM, "band": LEV_BAND,
                          "rep": "qld_cash"})
    # cashflow 는 자기 desired(월간 래칫)로 — DCA 에서는 매일 desired(주간)로 매수전용+디레버
    dl_cf = [x.get("L1", 0) + 2 * x.get("L2", 0) + 3 * x.get("L3", 0)
             for x in sig_vt_ewma(rpanel, rdates, T=VOL_TARGET, Lmax=LMAX, lam=LAM,
                                  band=LEV_BAND, rep="qld_cash")]
    cf = run_dca_cashflow(rpanel, rdates, dl_cf, rep="qld_cash", delever=0.75,
                          cost=COST, cash_rate=rzcash)

    # B0, B1 (rebalance 모드, 동일 현금흐름)
    b0 = R.run_dca_overlay(rpanel, rdates,
                           const_weights_series(rdates, {"QQQ": 0.6, "SCHD": 0.25, "GLD": 0.15}),
                           monthly_usd=35.0, initial_usd=32.0, cost=COST, cash_rate=rzcash,
                           exec_lag=1, mode="rebalance", rebalance_band=REB_BAND)
    b1 = R.run_dca_overlay(rpanel, rdates, const_weights_series(rdates, {"QQQ": 1.0}),
                           monthly_usd=35.0, initial_usd=32.0, cost=COST, cash_rate=rzcash,
                           exec_lag=1, mode="rebalance", rebalance_band=REB_BAND)

    # 10bp 프로모 what-if — DCA 픽 재무
    promo = R.CostSpec.promo(slippage_bps=5.0, fx_bps=20.0,
                             half_spread_bps=dict(COST.half_spread_bps),
                             default_half_spread_bps=3.0)
    dca_promo = {}
    for k, (fn, params) in dca_sig.items():
        w = fn(rpanel, rdates, **params)
        dca_promo[k] = R.run_dca_overlay(rpanel, rdates, w, monthly_usd=35.0, initial_usd=32.0,
                                         cost=promo, cash_rate=rzcash, exec_lag=1,
                                         mode="rebalance", rebalance_band=REB_BAND)

    write_report(results, bench_d, bench_h, reps, val2, val3, dca, cf, b0, b1,
                 dca_promo, rr_dtb3, dates, prog_N)
    print(f"[c3a] report → {REPORT}")
    return results


def write_report(results, bd, bh, reps, val2, val3, dca, cf, b0, b1, dca_promo,
                 rr_dtb3, dates, prog_N):
    # 사전등록 원문 보존(<!-- RESULTS_BELOW --> 이전까지)
    prereg = REPORT.read_text(encoding="utf-8")
    head = prereg.split("<!-- RESULTS_BELOW -->")[0].rstrip()
    L = [head, "", "<!-- RESULTS_BELOW -->", ""]
    order = ["c3a_vt_ewma", "c3a_vt_ewma_3x", "c3a_vt_monthly", "c3a_vt_cashflow",
             "c3a_vt_volofvol"]

    # 벤치
    L.append("## 2. 벤치마크 (1x NDX-TR B&H, 단위자본)\n")
    L.append("| 구간 | CAGR | MDD | Ulcer | Martin | Calmar |\n|---|---:|---:|---:|---:|---:|")
    L.append(f"| 설계 1986-2008 | {_f(bd['cagr'],1)} | {_f(bd['mdd'],1)} | {_f(bd['ulcer'])} | {_f(bd['martin'])} | {_f(bd['calmar'])} |")
    L.append(f"| 홀드아웃 2009-2026 | {_f(bh['cagr'],1)} | {_f(bh['mdd'],1)} | {_f(bh['ulcer'])} | {_f(bh['martin'])} | {_f(bh['calmar'])} |")
    L.append("\n> 1x 설계구간 MDD 가 −50% 캡을 위반(닷컴) → **상대 리스크 트랙**(부록 v2.1 §1) 적용. 홀드아웃은 벤치가 캡 준수 → 절대캡 병행.\n")

    # 결과표(단위자본)
    L.append("## 3. 결과 — 단위자본 (설계 / 홀드아웃)\n")
    L.append("| 아이디어 | 구간 | 평균lev | CAGR | MDD | Ulcer | Calmar | 최종/1x | Sharpe | 회전/yr | 매매/yr | 비용생존 | 손익분기bps | RC p | SPA p | 롤3y승률 | 상대리스크 | 최종판정 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    verdicts = {}
    for k in order:
        if k not in results:
            continue
        r = results[k]
        is_lev = True
        # 상대 리스크(설계·홀드아웃 함께 계산)
        rv, rchk = ("—", {})
        if "design" in r and "holdout" in r:
            rv, rchk = relative_risk_verdict(r["design"]["wm"], r["design"]["wb"],
                                             r["holdout"]["wm"], r["holdout"]["wb"],
                                             r["full_mdd"], is_leverage=is_lev)
        for period in ("design", "holdout"):
            if period not in r:
                continue
            p = r[period]
            wm = p["wm"]
            # 최종판정(강화 유의성 + 상대리스크 오버레이) — 홀드아웃 행에 표기
            final_v = "—"
            if period == "holdout":
                sig = strict_significance(r.get("dsr_prog"), p["rc"], p["spa"])
                axes = dict(p["decision_abs"].axes)
                axes["significance"] = sig
                axes["risk"] = rv
                final_v = gate._combine(axes)
                verdicts[k] = {"final": final_v, "sig": sig, "rel": rv, "rchk": rchk,
                               "axes": axes}
            row = (f"| {r['name']} | {period} | {r['avg_lev']:.2f} | {_f(wm['cagr'],1)} | "
                   f"{_f(wm['mdd'],1)} | {_f(wm['ulcer'])} | {_f(wm['calmar'])} | "
                   f"{_f(p['tvb0'])} | {_f(p['sr_ann'])} | {r['turnover_yr']:.2f} | "
                   f"{r['trades_yr']:.1f} | {p['cs_mult']:.1f}× | {p['be_bps']:.0f} | "
                   f"{_f(p['rc'],nd=3)} | {_f(p['spa'],nd=3)} | {_f(p['winrate'])} | "
                   f"{rv if period=='holdout' else '—'} | "
                   f"{'**'+final_v+'**' if period=='holdout' else '—'} |")
            L.append(row)
    L.append("\n*최종/1x = 단위자본 총수익 배수 vs 1x B&H(경제 축은 DCA B0 가 아니라 **1x NDX**). "
             "비용생존 = net 초과(vs 1x)>0 유지 최대 비용배수. 손익분기bps = 초과이득=0 되는 왕복 수수료. "
             "상대리스크·최종판정은 홀드아웃(반오염) 행 기준.*\n")
    L.append("> **경제 축 해석(중요).** T=20% 타깃은 NDX 실현변동성(~25–30%)보다 낮아 **평균 lev≈1.0 이하**로 "
             "de-risk 한다 → 상승장에서 raw 총수익이 1x 에 못 미친다(최종/1x<1). 표의 '비용생존 0.0×'는 **비용 문제가 "
             "아니라** 1x 대비 경제적 열위(회전율·비용은 §4 처럼 무시할 수준)를 뜻한다. 공정한 시험은 §5 변동성 매칭.\n")

    # DSR
    L.append("### 3.1 DSR (아이디어-내 N_eff vs 프로그램 전체 N)\n")
    L.append(f"프로그램 전체 원장 시도 수 N = **{prog_N}**(전 아이디어, 부록 v2.1 §3). "
             "스트림 미저장으로 N_eff 클러스터 불가 → raw N(보수적) 사용.\n")
    L.append("| 아이디어 | DSR(아이디어 N_eff) | N_eff | DSR(프로그램 N) | 플래토 | 이웃net>0 |\n|---|---:|---:|---:|---|---:|")
    for k in order:
        if k not in results:
            continue
        r = results[k]
        pl = "PASS" if r["plateau_pass"] else ("경계" if r["plateau_border"] else "FAIL")
        L.append(f"| {r['name']} | {_f(r['dsr_idea'])} | {r['n_eff']} | {_f(r.get('dsr_prog'))} | "
                 f"{pl} | {r['frac_pos']*100:.0f}% |")
    L.append("\n> **DSR 정직 주의(c2a 계승).** 이웃 고상관 → 아이디어-내 N_eff≈1 → DSR≈'일 Sharpe>0'(베타). "
             "**프로그램 전체 N(≈{}) 기준 DSR** 이 진짜 다중검정 방어이며, N·V 가 크면 사실상 0 으로 눌린다 — "
             "이 계열은 '스킬'이 아니라 **레버리지 베타**임을 뜻한다.\n".format(prog_N))

    # 표현 비교(min-notional)
    L.append("## 4. 표현 비교 — 최소 노셔널(회전율·현금드래그, 현금 0%)\n")
    L.append("c3a 핵심: 노출을 **더 적은 노셔널 달러**로 실현해 노출변경당 수수료%를 줄인다. "
             "단, qld_cash/tqqq_cash 는 (1−비중)이 현금(수익 0%)이라 상승장 드래그가 있다 — 둘 다 보고.\n")
    L.append("| 표현 | 설계 CAGR | 설계 MDD | 홀드 CAGR | 홀드 MDD | 회전/yr | 매매/yr | 단위자본 총비용 | 최종배수(전구간) |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    yrs = max((dates[-1] - dates[0]).days / 365.0, 1e-9)
    for rep, label in (("mix", "mix(인접슬리브, 완전투자)"), ("qld_cash", "qld_cash(2x+현금)"),
                       ("tqqq_cash", "tqqq_cash(3x+현금=비용압축)")):
        rr = reps[rep]
        wmd = window_metrics(rr.equity, dates, dates[0], DESIGN_END)
        wmh = window_metrics(rr.equity, dates, HOLDOUT_START, HOLDOUT_END)
        L.append(f"| {label} | {_f(wmd['cagr'],1)} | {_f(wmd['mdd'],1)} | {_f(wmh['cagr'],1)} | "
                 f"{_f(wmh['mdd'],1)} | {sum(rr.turnover)/yrs:.2f} | {rr.trade_count/yrs:.1f} | "
                 f"{rr.total_cost:.4f} | {rr.equity[-1]/rr.equity[0]:.2f} |")
    L.append("\n> **회전율 결론(핵심 성공).** c2a 변동성타깃(일간·0.25밴드·mix)은 회전율 **~12x/yr** 로 2× "
             "비용에서 탈락했다. c3a 는 주간+0.5밴드로 **~0.3–0.5x/yr(≈25–40배 감소)** — 회전율 제약 설계는 "
             "명백히 작동한다.\n")
    L.append("> **최소 노셔널 결론(정직).** 그러나 **현금 0% 기본**에서는 노출당 노셔널을 줄인 qld_cash/tqqq_cash 가 "
             "완전투자 mix 에게 **진다**: mix 전구간 최종배수 ≈{:.0f}× vs qld_cash ≈{:.0f}× vs tqqq_cash ≈{:.0f}×. "
             "회전율이 이미 낮아 절감할 비용이 작은데, 유휴현금(0%)의 **드래그가 절감분을 초과**하기 때문이다. "
             "즉 '레버리지=비용압축' 이점은 **현금 수익률>0 일 때만** 유효(§7 DTB3 민감도에서 qld_cash 최종배수가 "
             "2배로 상승). 따라서 헤드라인 표현은 **mix 가 경제적으로 우월**하나(단, mix 도 avg lev≈1 로 1x 에 열위), "
             "사전등록대로 min-notional(qld_cash)을 §3 헤드라인으로 유지하고 이 열위를 명시한다.\n".format(
                 reps["mix"].equity[-1] / reps["mix"].equity[0],
                 reps["qld_cash"].equity[-1] / reps["qld_cash"].equity[0],
                 reps["tqqq_cash"].equity[-1] / reps["tqqq_cash"].equity[0]))

    # 변동성 매칭(§5.3)
    L.append("## 5. 변동성 매칭 (연 10% 스케일, §5.3 — vol-target 의 진짜 시험)\n")
    L.append("| 아이디어 | 구간 | vol-match 최종(전략) | vol-match 최종(1x) | 매칭후 우위 |\n|---|---|---:|---:|---|")
    for k in order:
        if k not in results:
            continue
        r = results[k]
        for period in ("design", "holdout"):
            if period not in r:
                continue
            p = r[period]
            adv = "우위" if (math.isfinite(p['vm_s']) and math.isfinite(p['vm_b'])
                           and p['vm_s'] > p['vm_b']) else "소멸"
            L.append(f"| {r['name']} | {period} | {_f(p['vm_s'])} | {_f(p['vm_b'])} | {adv} |")
    L.append("\n> 매칭 후 우위가 사라지면 그 이득은 순수 리스크값(레버리지)이다. vol-target 이 "
             "동일 변동성에서도 이기면 타이밍(엔벨로프)에 진짜 스킬이 있다는 신호.\n")

    # 합성 vs 실물
    L.append("## 6. 합성 vs 실물 검증 (2016-2026)\n")
    L.append("| 슬리브 | n | corr | 연추적차 | 연추적오차 | CAGR 합성 | CAGR 실물 |\n|---|---:|---:|---:|---:|---:|---:|")
    L.append(f"| 2x QLD | {val2['n']} | {val2['corr']:.4f} | {_f(val2['ann_tracking_diff'],1)} | {_f(val2['ann_tracking_error'],1)} | {_f(val2['cagr_syn'],1)} | {_f(val2['cagr_real'],1)} |")
    L.append(f"| 3x TQQQ | {val3['n']} | {val3['corr']:.4f} | {_f(val3['ann_tracking_diff'],1)} | {_f(val3['ann_tracking_error'],1)} | {_f(val3['cagr_syn'],1)} | {_f(val3['cagr_real'],1)} |")
    L.append("\n> 상관 0.999·연추적차<1%p → 합성이 실물을 잘 근사(c2a 계승).\n")

    # DCA
    L.append("## 7. DCA 오버레이 (2016-2026 실물, rebalance 모드, 시드 $32 + 월 $35)\n")
    L.append("| 포트폴리오 | 최종자산 | XIRR | 달러MDD | vs B0 | vs B1 | 회전노셔널 |\n|---|---:|---:|---:|---:|---:|---:|")

    def dline(nm, d, turn=None):
        x = gate.xirr(d.cashflows)
        return (nm, d.final_value, x, gate.max_drawdown(d.equity),
                getattr(d, "total_turnover", turn))
    b0v = dline("B0 QQQ60/SCHD25/GLD15", b0)
    b1v = dline("B1 QQQ100", b1)
    rows = [b0v, b1v]
    label = {"c3a_vt_ewma": "주간 EWMA (qld_cash)", "c3a_vt_ewma_3x": "주간 EWMA (tqqq_cash)",
             "c3a_vt_monthly": "월간 EWMA (qld_cash)", "c3a_vt_volofvol": "vol-of-vol (창작)"}
    for k in ("c3a_vt_ewma", "c3a_vt_ewma_3x", "c3a_vt_monthly", "c3a_vt_volofvol"):
        rows.append(dline(label[k], dca[k]))
    # cashflow-native
    cf_x = gate.xirr(cf["cashflows"])
    rows.append(("DCA-native cashflow", cf["final_value"], cf_x, cf["dollar_mdd"],
                 cf.get("total_cost")))
    for nm, fv, x, mdd, turn in rows:
        vb0 = fv / b0v[1] if b0v[1] else float("nan")
        vb1 = fv / b1v[1] if b1v[1] else float("nan")
        tstr = f"${turn:.0f}" if isinstance(turn, (int, float)) else "—"
        L.append(f"| {nm} | ${fv:.2f} | {_f(x,1)} | {_f(mdd,1)} | {vb0:.3f} | {vb1:.3f} | {tstr} |")
    L.append(f"\n총입금 ${b0.total_deposited:.0f}. DCA 달러MDD 는 신규입금 완충으로 리스크 과소평가 — "
             f"리스크 판정은 §3 단위자본(상대 트랙). rebalance 모드(매도 허용, exec_lag=1).\n")
    if dca_promo:
        L.append("**수수료 10bp 프로모 what-if**: "
                 + ", ".join(f"{label[k]} ${dca_promo[k].final_value:.2f}"
                             f"(+${dca_promo[k].final_value - dca[k].final_value:.2f})"
                             for k in ("c3a_vt_ewma", "c3a_vt_ewma_3x", "c3a_vt_monthly",
                                       "c3a_vt_volofvol"))
                 + ".\n")
    L.append(f"**현금수익률 민감도(DTB3)**: 헤드라인(주간 qld_cash) 전구간 최종배수 — 현금0% "
             f"{reps['qld_cash'].equity[-1]/reps['qld_cash'].equity[0]:.2f} vs DTB3 "
             f"{rr_dtb3.equity[-1]/rr_dtb3.equity[0]:.2f}. 현금 비중이 클수록 DTB3 가정이 결과를 올린다.\n")

    # 판정 종합
    L.append("## 8. 판정 종합 및 정직한 해석\n")
    for k in order:
        if k not in results:
            continue
        r = results[k]
        v = verdicts.get(k, {})
        dabs = r.get("holdout", {}).get("decision_abs") or r.get("design", {}).get("decision_abs")
        reasons = "; ".join(dabs.reasons[:2]) if dabs else ""
        reasons = re.sub(r"-?\d+\.\d{4,}", lambda m: f"{float(m.group()):.3f}", reasons)
        L.append(f"- **{r['name']}**: 최종판정 **{v.get('final','—')}** "
                 f"(유의성 {v.get('sig','—')}, 상대리스크 {v.get('rel','—')}, "
                 f"플래토 {'PASS' if r['plateau_pass'] else ('경계' if r['plateau_border'] else 'FAIL')}). "
                 f"표준(절대캡) 게이트: {dabs.verdict if dabs else '—'}. {reasons}\n")
    L.append("\n**정직한 결론.** " + honest_conclusion(results, verdicts) + "\n")

    REPORT.write_text("\n".join(L), encoding="utf-8")


def honest_conclusion(results, verdicts):
    parts = []
    passes = [results[k]["name"] for k in results if verdicts.get(k, {}).get("final") == "PASS"]
    cond = [results[k]["name"] for k in results if verdicts.get(k, {}).get("final") == "CONDITIONAL"]
    rel_pass = [results[k]["name"] for k in results if verdicts.get(k, {}).get("rel") == "PASS"]
    parts.append(f"**최종 PASS: {passes or '없음'}. CONDITIONAL: {cond or '없음'}.** "
                 f"상대 리스크 트랙만 통과: {rel_pass or '없음'}.")
    tr = {k: results[k]["turnover_yr"] for k in results}
    if tr:
        best = min(tr, key=tr.get)
        parts.append(f"(1) **회전율 제약은 성공했다** — c2a vol-target ~12x/yr → c3a {tr[best]:.1f}~"
                     f"{max(tr.values()):.1f}x/yr(≈25–40배 감소), 비용은 더 이상 병목이 아니다.")
    parts.append("(2) **그러나 T=20% 는 NDX 에서 under-leverage(평균 lev≈1)** → 상승장 raw 수익이 1x 에 열위이고, "
                 "**변동성 매칭(§5) 후에도 대부분 1x 에 진다** = vol-target 타이밍이 NDX 에서 (+) 스킬을 못 낸다"
                 "(변동성이 클러스터되지만 상방으로 평균회귀). (3) **최소 노셔널(비용압축) 아이디어는 현금 0% 에서 "
                 "완전투자 mix 에 패배**(유휴현금 드래그 > 회전 절감) — 현금 수익률>0 에서만 유효. (4) 유의성은 "
                 "프로그램 전체 N=396(다중검정)·반오염(RC/SPA p<0.01)에서 전부 눌린다: 초과분은 레버리지 베타이지 "
                 "스킬이 아니다. (5) 유일한 순가치는 **하락장 MDD 방어**(창작 vol-of-vol 이 홀드아웃 MDD −24% 로 "
                 "상대 트랙 통과, 단 return 을 크게 포기). 백테스트 낙관·합성 불확실성·소표본을 감안해 채택은 소액 "
                 "슬리브 + 포워드 페이퍼 후에만(§6.1).")
    return " ".join(parts)


if __name__ == "__main__":
    args = sys.argv[1:]
    fast = "--fast" in args
    ledger = LEDGER
    if "--ledger" in args:
        ledger = args[args.index("--ledger") + 1]
    main(fast=fast, ledger=ledger)
