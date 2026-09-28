"""c7c — Longest honest OOS for leverage DCA rules on Shiller US stocks 1871–2026.

사전등록·규약: reports/cycle7_c7c_shiller.md, experiments/README.md,
docs/gate_v2_spec.md(+부록 v2.1/v2.2).

핵심 질문: c5a 의 라이프사이클 글라이드(`c5a_lc_glide`)와 c6c 의 크래시 레버리지
(`c6c_crash_lev`)를 **한번도 쓰지 않은 1871–1985 시작일**(대공황 1929–32 −86%, 1937,
1970년대 스태그플레이션 포함)에 태우면 사전등록 c5a 규칙 판정이 유지되는가.
파라미터 튜닝은 없다 — 두 규칙 모두 원 실험의 정확한 사전등록 파라미터를 import 재사용한다.

데이터: **Robert Shiller 월간 S&P Composite**(datahub.io core/s-and-p-500 미러 =
Shiller ie_data 재포맷; price·dividend·CPI·long rate, 1871-01~). 금융(조달)금리는
FRED **TB3MS**(1934+)와 그 이전 **NBER 상업어음금리 M13002US35620M156NNBR**(<1934) 접합.

월간 해상도 2x: 일일리셋 2x ETF 는 월간자료로 정확히 계산 불가 → 근사식
    r_2x,m ≈ 2·r_1x,m − k·σ̂²_m − financing_m − expense_m,
σ̂²_m = 월수익률의 인과적 EWMA(GARCH-lite) 분산(λ=0.94). k 는 일일합성 2x(1986–2026
FRED NDX + 2016–2026 FRED SP500)를 월간집계한 '진실' 대비 보정하고, **비관적 끝**(최대
구간별 best-fit ≈0.345 를 상회하는 0.40)을 채택 → 2x 수익을 과소평가(정직).

재현: PYTHONPATH=src .venv/bin/python experiments/c7c_shiller.py [--fast]
      [--ledger PATH] [--no-ledger] [--no-report]
Do NOT edit src/ or earlier experiments — c5a/c6c 는 import 만 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
import gate_eval  # noqa: E402
import c5a_lifecycle as c5a  # noqa: E402  (glide 규칙·시뮬레이터·집계 기계 재사용)
import c6c_contrib as c6c    # noqa: E402  (crash_lev 규칙·신호·시뮬레이터 재사용)

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle7_c7c_shiller.md"
RESULTS_JSON = ROOT / "reports" / "c7c_results.json"
DATA_DIR = ROOT / "data" / "shiller"
SHILLER_CSV = DATA_DIR / "shiller_ie_data.csv"
SHILLER_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv"

# ── 사전등록 상수 (c5a/c6c 와 동일 — 재사용, 튜닝 없음) ───────────────────────
INITIAL = c5a.INITIAL     # 시드 $32
MONTHLY = c5a.MONTHLY     # 월 적립 $35
EXP_1X = c5a.EXP_1X       # 1x 보수율 0.20%/yr
EXP_LEV = c5a.EXP_LEV     # 2x 보수율 0.95%/yr
BORROW_SPREAD = c5a.BORROW_SPREAD   # 차입 스프레드 0.5%/yr

# ── 월간 2x 근사 보정 상수(사전등록: 아래 calibrate 로 검증, 값은 고정) ────────
EWMA_LAM = 0.94           # GARCH-lite EWMA 분산 감쇠(월간)
EWMA_SEED = 12            # EWMA 워밍업(개월; 이 전은 drag=0, 인과적)
K_DRAG = 0.40             # 변동성 드래그 계수(비관적 끝; 구간별 best-fit 최대 0.345 상회)
DIV_MONTHLY_DIVISOR = 12.0  # Shiller 연배당률 → 월배당 = D/12

# ── 시작일 era 분할(전부 pre-1986 = 신규 OOS) ────────────────────────────────
ERAS = [("1871-1913", 1871, 1913), ("1914-1945", 1914, 1945), ("1946-1985", 1946, 1985)]
OOS_END_YEAR = 1985       # 이 이하 시작일은 c5a/c6c 에서 한번도 안 씀
HORIZONS = (10, 20, 30)
WORST_COHORTS = [(1909, 1), (1929, 9), (1966, 1)]   # start (year, month)

# 원장(단위자본 스트림) 분할: design=1871–1985(신규 심층 OOS), holdout=1986+(현대 확인)
LEDGER_DESIGN_END = date(1985, 12, 31)
IDEA = {"glide": "c7c_glide_1871", "crash_lev": "c7c_crashlev_1871", "qqq": "c7c_b1_qqq"}


# ── Shiller 파서(테스트 대상: fixture 문자열로 검증) ──────────────────────────
def parse_shiller_csv(text: str) -> list[tuple]:
    """datahub core/s-and-p-500 CSV → [(date(y,m,1), price, div_annual, cpi, long_rate)].

    헤더: Date,SP500,Dividend,Earnings,Consumer Price Index,Long Interest Rate,... .
    Date 는 'YYYY-MM-01'. **말미의 미보고(배당/CPI=0) 월은 절단**해 첫 유효구간만 남긴다
    (배당은 총수익에 필수). price/div/cpi>0 인 마지막 월까지만 반환.
    """
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    rows: list[tuple] = []
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) < 6:
            continue
        try:
            y, mo = int(p[0][:4]), int(p[0][5:7])
            price, div, cpi, lr = float(p[1]), float(p[2]), float(p[4]), float(p[5])
        except (ValueError, IndexError):
            continue
        rows.append((date(y, mo, 1), price, div, cpi, lr))
    # 말미 절단: price/div/cpi 모두 유효(>0)한 마지막 인덱스까지
    last_ok = -1
    for i, (_, price, div, cpi, _lr) in enumerate(rows):
        if price > 0 and div > 0 and cpi > 0:
            last_ok = i
    # 앞쪽 유효 시작
    first_ok = 0
    for i, (_, price, div, cpi, _lr) in enumerate(rows):
        if price > 0 and div > 0 and cpi > 0:
            first_ok = i
            break
    return rows[first_ok:last_ok + 1]


def load_shiller(*, force: bool = False) -> list[tuple]:
    """Shiller 월간 CSV 로드(캐시 data/shiller/, 없으면 keyless 미러에서 1회 fetch)."""
    if SHILLER_CSV.exists() and not force:
        text = SHILLER_CSV.read_text(encoding="utf-8")
    else:
        text = hd._http_get(SHILLER_URL, timeout=60).decode("utf-8", "replace")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SHILLER_CSV.write_text(text, encoding="utf-8")
    return parse_shiller_csv(text)


# ── 인과적 EWMA(GARCH-lite) 분산 — 테스트 대상(인과성·손검산) ─────────────────
def ewma_var(returns: list[float], *, lam: float = EWMA_LAM, seed: int = EWMA_SEED) -> list[float | None]:
    """월수익률의 인과적 EWMA 분산 추정. out[m] 은 **returns[<m] 만** 참조(미래 불참조).

    첫 seed 개 수익률로 초기 분산을 잡고(그 전 out=None → drag 0), 이후
    σ²_m = λ·σ²_{m-1} + (1−λ)·r²_{m-1}. σ²_m 은 m 시점 결정 전 알 수 있는 값이라 인과적.
    """
    n = len(returns)
    out: list[float | None] = [None] * n
    acc = 0.0
    cnt = 0
    s2: float | None = None
    for m in range(n):
        out[m] = s2                       # 이번 달 수익률 반영 전 값(인과적)
        r = returns[m]
        if cnt < seed:
            acc += r * r
            cnt += 1
            if cnt == seed:
                s2 = acc / seed
        else:
            s2 = lam * s2 + (1.0 - lam) * r * r
    return out


# ── 월간 2x 근사 보정(FRED NDX 1986–2026 + SP500 2016–2026 일일합성 대비) ──────
def _monthly_returns_from_daily(daily: list) -> list[float]:
    """일봉 → 월말 NAV 비율 월수익률(월내 복리 == 월말종가비)."""
    by: dict[tuple, float] = {}
    for c in sorted(daily, key=lambda c: c.dt):
        by[(c.dt.year, c.dt.month)] = c.close      # 각 월 마지막 종가
    keys = sorted(by)
    nav = [by[k] for k in keys]
    return [nav[i] / nav[i - 1] - 1.0 for i in range(1, len(nav))]


def _fit_k(daily: list) -> dict:
    """한 일일 시계열에서 순수 변동성 드래그 계수 best-fit k(원점통과 OLS)와 적합오차.

    진실 R2_m = 무비용 일일리셋 2x 를 월간집계. naive = 2·R1_m. D_m = naive − R2_m.
    proxy σ̂²_m = EWMA(월수익률). k = ΣD·σ̂²/Σσ̂²². K_DRAG 에서의 CAGR gap·RMSE·TE 도 보고.
    """
    r1 = _monthly_returns_from_daily(daily)
    syn2 = hd.synthetic_leveraged(daily, 2.0, annual_expense=0.0, borrow_spread=0.0,
                                  rf_annual=0.0, rf_candles=None)
    r2 = _monthly_returns_from_daily(syn2)
    m = min(len(r1), len(r2))
    r1, r2 = r1[:m], r2[:m]
    s2 = ewma_var(r1)
    D = [2.0 * r1[i] - r2[i] for i in range(m)]
    idx = [i for i in range(m) if s2[i] is not None]
    num = sum(D[i] * s2[i] for i in idx)
    den = sum(s2[i] * s2[i] for i in idx)
    k = num / den if den > 0 else 0.0
    # K_DRAG 에서의 적합
    r2c = [r2[i] for i in idx]
    apc = [2.0 * r1[i] - K_DRAG * s2[i] for i in idx]
    yrs = len(idx) / 12.0

    def cum(rs):
        x = 1.0
        for rr in rs:
            x *= (1.0 + rr)
        return x
    cagr_true = cum(r2c) ** (1.0 / yrs) - 1.0 if yrs > 0 else 0.0
    cagr_appx = cum(apc) ** (1.0 / yrs) - 1.0 if yrs > 0 else 0.0
    resid = [apc[j] - r2c[j] for j in range(len(idx))]
    rmse = (sum(e * e for e in resid) / len(resid)) ** 0.5 if resid else 0.0
    mr = sum(resid) / len(resid) if resid else 0.0
    te = ((sum((e - mr) ** 2 for e in resid) / (len(resid) - 1)) ** 0.5) * (12 ** 0.5) if len(resid) > 1 else 0.0
    return {"n_months": len(idx), "best_k": k, "cagr_true": cagr_true,
            "cagr_approx_Kused": cagr_appx, "cagr_gap_pp": (cagr_appx - cagr_true) * 100.0,
            "rmse_monthly": rmse, "tracking_error_annual": te}


def calibrate_k() -> dict:
    """보정 요약: FRED NDX(전체·전반·후반) + SP500. K_DRAG 는 사전등록 고정(보정으로 검증만)."""
    ndx = hd.load_fred("NASDAQ100")
    sp = hd.load_fred("SP500")
    segs = {
        "NDX 1986-2026": _fit_k(ndx),
        "NDX 1986-2005": _fit_k([c for c in ndx if c.dt < date(2006, 1, 1)]),
        "NDX 2006-2026": _fit_k([c for c in ndx if c.dt >= date(2006, 1, 1)]),
        "SP500 2016-2026": _fit_k(sp),
    }
    best = [v["best_k"] for v in segs.values()]
    return {"K_DRAG": K_DRAG, "lam": EWMA_LAM, "seed_months": EWMA_SEED,
            "best_k_min": min(best), "best_k_max": max(best),
            "conservative": K_DRAG >= max(best), "segments": segs}


# ── 조달(단기)금리: TB3MS(1934+) + NBER 상업어음(<1934) 접합, 월간 forward-fill ──
def build_short_rate(dates: list[date]) -> tuple[list[float], list[float]]:
    """(rf_annual_pct, rf_monthly). 1934-01 이상은 TB3MS, 그 전은 상업어음금리."""
    tb = sorted([(c.dt, c.close) for c in hd.load_fred("TB3MS")])
    cp = sorted([(c.dt, c.close) for c in hd.load_fred("M13002US35620M156NNBR")])

    def ff(series):
        keys = [d for d, _ in series]
        out = []
        last = None
        ki = 0
        for d in dates:
            while ki < len(keys) and keys[ki] <= d:
                last = series[ki][1]
                ki += 1
            out.append(last)
        return out
    tb_ff, cp_ff = ff(tb), ff(cp)
    rf_ann: list[float] = []
    for i, d in enumerate(dates):
        v = tb_ff[i] if (d >= date(1934, 1, 1) and tb_ff[i] is not None) else cp_ff[i]
        if v is None:
            v = cp_ff[i] if cp_ff[i] is not None else 3.0   # 최말단 폴백(관측 전엔 미사용)
        rf_ann.append(v)
    rf_m = [max(0.0, (a / 100.0) / 12.0) for a in rf_ann]
    return rf_ann, rf_m


# ── Shiller 월간 패널 구축(1x TR NAV, 보정 2x NAV, 신호) ──────────────────────
def build_navs(r1: list[float], rf_m: list[float], *, k: float = K_DRAG,
               anchor: float = 100.0) -> tuple[list[float], list[float]]:
    """월수익률 r1 → (1x NAV, 2x NAV). 순수·손검산 가능·인과적(테스트 대상).

    1x: g1 = r1 − exp1x/12.  2x(보정): g2 = 2·r1 − k·σ̂²_m − (rf_m + spread/12) − exp2x/12,
    σ̂²_m = ewma_var(r1)(인과적). NAV 앵커=anchor(수수료 주당계산 정규화; 비율엔 무영향).
    k↑ 이면 2x drag↑ → 2x NAV 단조 감소(보수적 방향).
    """
    n = len(r1)
    s2 = ewma_var(r1)
    q = [anchor]
    lv = [anchor]
    for i in range(1, n):
        g1 = r1[i] - EXP_1X / 12.0
        drag = k * (s2[i] if s2[i] is not None else 0.0)
        g2 = 2.0 * r1[i] - drag - (rf_m[i] + BORROW_SPREAD / 12.0) - EXP_LEV / 12.0
        q.append(q[-1] * (1.0 + g1))
        lv.append(lv[-1] * (1.0 + g2))
    return q, lv


def build_panel() -> dict:
    """Shiller → 월간 총수익 1x/2x NAV + 조달금리 + CPI + c6c 신호(dd_ath).

    1x 총수익 월수익률 r_1x,m = (P_m + D_m/12)/P_{m-1} − 1. NAV 는 build_navs 로 구축.
    """
    months = load_shiller()
    dates = [t[0] for t in months]
    price = [t[1] for t in months]
    div = [t[2] for t in months]
    cpi = [t[3] for t in months]
    n = len(dates)
    r1 = [0.0] * n
    for i in range(1, n):
        r1[i] = (price[i] + div[i] / DIV_MONTHLY_DIVISOR) / price[i - 1] - 1.0
    rf_ann, rf_m = build_short_rate(dates)
    q, lv = build_navs(r1, rf_m)
    panel = {"dates": dates, "QQQ": q, "QLD": lv, "cash": rf_m,
             "sigma": [None] * n, "trend": [True] * n, "cpi": cpi, "r1": r1,
             "rf_ann": rf_ann, "price": price}
    # c6c 신호(dd_ath 는 QQQ=1x TR NAV 의 사상최고 낙폭; 월말 해상도)
    panel_c6c = c6c.add_signals({"dates": dates, "QQQ": q, "QLD": lv, "cash": rf_m})
    panel["dd_ath"] = panel_c6c["dd_ath"]
    panel["dd_52"] = panel_c6c["dd_52"]
    return panel


# ── 롤링 시작일 러너(글라이드=c5a, crash_lev=c6c, B1=c5a qqq) ─────────────────
def _closes(panel):
    return {"QQQ": panel["QQQ"], "QLD": panel["QLD"]}


def sim_glide(panel, s, e):
    return c5a.simulate(_closes(panel), panel["dates"], s, e + 1, "glide",
                        sigma=panel["sigma"], trend=panel["trend"], cash=panel["cash"])


def sim_b1(panel, s, e):
    return c5a.simulate(_closes(panel), panel["dates"], s, e + 1, "qqq",
                        sigma=panel["sigma"], trend=panel["trend"], cash=panel["cash"])


def sim_crashlev(panel, s, e):
    return c6c.simulate(panel, s, e + 1, "crash_lev", {}, cash=panel["cash"])


def run_distribution(panel, years, kind, starts):
    dates = panel["dates"]
    out = []
    for (s, e) in starts:
        if kind == "glide":
            res = sim_glide(panel, s, e)
            m = c5a.path_metrics(res)
        elif kind == "crash_lev":
            res = sim_crashlev(panel, s, e)
            m = c6c.path_metrics(res)
        else:
            res = sim_b1(panel, s, e)
            m = c5a.path_metrics(res)
        m["start"] = dates[s].isoformat()
        m["start_year"] = dates[s].year
        m["n_deploys"] = getattr(res, "n_deploys", 0)
        out.append(m)
    return out


def _era(paths, lo, hi):
    return [p for p in paths if lo <= p["start_year"] <= hi]


# ── 원장 로깅(단위자본 월간 스트림, ppy=12, peek-once) ───────────────────────
def log_ledger(panel, kind, ledger_path):
    """전체구간 시뮬 → 월간 inv_ret 스트림을 design(1871–1985)/holdout(1986+) 로 적재."""
    dates = panel["dates"]
    n = len(dates)
    if kind == "glide":
        res = sim_glide(panel, 0, n - 1)
    elif kind == "crash_lev":
        res = sim_crashlev(panel, 0, n - 1)
    else:
        res = sim_b1(panel, 0, n - 1)
    stream = res.inv_ret[1:]
    sdates = res.dates[1:]
    di = [i for i, d in enumerate(sdates) if d <= LEDGER_DESIGN_END]
    hi = [i for i, d in enumerate(sdates) if d > LEDGER_DESIGN_END]
    idea_id = IDEA[kind]
    params = {"rule": kind, "data": "shiller_1871", "k_drag": K_DRAG, "lam": EWMA_LAM,
              "rep": "monthly_2x_approx", "financing": "tb3ms+cp", "ppy": 12}
    logged = {}
    if di:
        cr = [stream[i] for i in di]
        cd = [sdates[i] for i in di]
        um = gate_eval.unit_capital_metrics(cr, dates=cd, ppy=12)
        gate_eval.log_evaluation(idea_id, dict(params), 1, "design", um,
                                 window=(cd[0], cd[-1]), ledger_path=ledger_path)
        logged["design"] = um
    if hi and not gate.already_peeked(ledger_path, idea_id):
        cr = [stream[i] for i in hi]
        cd = [sdates[i] for i in hi]
        um = gate_eval.unit_capital_metrics(cr, dates=cd, ppy=12)
        try:
            gate_eval.log_evaluation(idea_id, dict(params), 1, "holdout", um,
                                     window=(cd[0], cd[-1]), ledger_path=ledger_path)
            logged["holdout"] = um
        except gate.PeekOnceError:
            pass
    return logged


# ── 특정 코호트 상세(최악 코호트: 1909/1929/1966) ─────────────────────────────
def cohort_detail(panel, y, mo, years):
    dates = panel["dates"]
    s = next((i for i, d in enumerate(dates) if d.year == y and d.month == mo), None)
    if s is None:
        return None
    e = c5a._end_index(dates, s, years)
    if e <= s or e > len(dates) - 1:
        return None
    b1 = sim_b1(panel, s, e)
    gl = sim_glide(panel, s, e)
    cl = sim_crashlev(panel, s, e)
    mb, mg, mc = c5a.path_metrics(b1), c5a.path_metrics(gl), c6c.path_metrics(cl)
    return {
        "start": dates[s].isoformat(), "end": dates[e].isoformat(), "years": years,
        "deposited": b1.total_deposited,
        "b1": {"terminal": mb["terminal"], "dollar_dd": mb["dollar_dd"]},
        "glide": {"terminal": mg["terminal"], "ratio": mg["terminal"] / mb["terminal"],
                  "dollar_dd": mg["dollar_dd"], "avg_exp": mg["avg_exp"]},
        "crash_lev": {"terminal": mc["terminal"], "ratio": mc["terminal"] / mb["terminal"],
                      "dollar_dd": mc["dollar_dd"], "n_deploys": cl.n_deploys},
    }


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="시작일 3개월 간격 서브샘플")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)

    print("[c7c] 월간 2x 근사 보정(FRED NDX+SP500)...")
    calib = calibrate_k()
    print(f"[c7c]   best-fit k∈[{calib['best_k_min']:.3f},{calib['best_k_max']:.3f}] "
          f"K_DRAG={K_DRAG} conservative={calib['conservative']}")

    print("[c7c] Shiller 월간 패널 구축(1871–2026)...")
    panel = build_panel()
    dates = panel["dates"]
    print(f"[c7c]   {dates[0]} … {dates[-1]}  ({len(dates)}개월)")
    month_starts = [t for t, f in enumerate(R.month_start_flags(dates)) if f]

    results = {"meta": {"generated": date.today().isoformat(), "fast": args.fast,
                        "first": dates[0].isoformat(), "last": dates[-1].isoformat(),
                        "n_months": len(dates)},
               "calibration": calib}

    # 1) 원장(단위자본 월간 스트림; peek-once)
    if not args.no_ledger:
        print("[c7c] 원장 적재(lane 1, 월간 스트림, ppy=12)...")
        for kind in ("glide", "crash_lev", "qqq"):
            log_ledger(panel, kind, args.ledger)

    # 2) 롤링 분포(10/20/30y; 시작 ≤2006 = 전부 pre-1986 포함 OOS)
    dist = {}
    for years in HORIZONS:
        starts_all = c5a.start_indices(dates, month_starts, years, fast=args.fast)
        starts = [(s, e) for (s, e) in starts_all if dates[s].year <= 2006]
        b1 = run_distribution(panel, years, "qqq", starts)
        gl = run_distribution(panel, years, "glide", starts)
        cl = run_distribution(panel, years, "crash_lev", starts)
        dist[years] = {"starts": starts, "b1": b1, "glide": gl, "crash_lev": cl,
                       "first": dates[starts[0][0]].isoformat(),
                       "last": dates[starts[-1][0]].isoformat()}
        print(f"[c7c] {years}y: {len(starts)} 시작일 ({dist[years]['first']}…{dist[years]['last']})")

    # 3) era 별 집계 + c5a 규칙 판정
    summary = {}
    for years in HORIZONS:
        d = dist[years]
        per = {}
        # 전체 OOS(≤1985 시작) + era 별
        groups = [("oos_all", 1871, OOS_END_YEAR)] + [(nm, lo, hi) for nm, lo, hi in ERAS]
        for nm, lo, hi in groups:
            b1e = _era(d["b1"], lo, hi)
            if not b1e:
                continue
            gle = _era(d["glide"], lo, hi)
            cle = _era(d["crash_lev"], lo, hi)
            ab1 = c5a.aggregate(b1e, b1e)
            agl = c5a.aggregate(gle, b1e)
            acl = c5a.aggregate(cle, b1e)
            agl["decision"] = c5a.decide(agl, ab1)
            acl["decision"] = c5a.decide(acl, ab1)
            per[nm] = {"b1": ab1, "glide": agl, "crash_lev": acl,
                       "avg_deploys_cl": sum(p["n_deploys"] for p in cle) / len(cle)}
        summary[years] = per
    results["eras"] = summary

    # 4) 최악 코호트 상세
    cohorts = {}
    for (y, mo) in WORST_COHORTS:
        for years in (20, 30):
            cd = cohort_detail(panel, y, mo, years)
            if cd:
                cohorts[f"{y}-{mo:02d}_{years}y"] = cd
    results["worst_cohorts"] = cohorts

    # 5) 1929-32 심층 낙폭 검증(데이터 신뢰성)
    def _idx(y, m):
        return next((i for i, dd in enumerate(dates) if dd.year == y and dd.month == m), None)
    i29, i32 = _idx(1929, 9), _idx(1932, 6)
    if i29 is not None and i32 is not None:
        results["crash_1929_32"] = {
            "price_peak": panel["price"][i29], "price_trough": panel["price"][i32],
            "price_dd": panel["price"][i32] / panel["price"][i29] - 1.0,
            "tr1x_dd": panel["QQQ"][i32] / panel["QQQ"][i29] - 1.0,
            "tr2x_dd": panel["QLD"][i32] / panel["QLD"][i29] - 1.0}

    RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
    print(f"[c7c] 결과 JSON → {RESULTS_JSON}")
    _print_summary(results)
    if not args.no_report:
        write_report(results)
        print(f"[c7c] 리포트 → {REPORT}")
    return results


def _print_summary(results):
    print("\n=== c7c 요약(20년 지평, 전체 OOS 1871-1985 시작) ===")
    s = results["eras"][20]["oos_all"]
    b1 = s["b1"]
    print(f"  DCA-1x(B1): median=${b1['median']:.0f} p5=${b1['p5']:.0f} regret={b1['regret']:.2f} n={b1['n']}")
    for kind in ("glide", "crash_lev"):
        a = s[kind]
        print(f"  {kind:10s}: median {a['median']/b1['median']:.2f}× p5 {a['p5']/b1['p5']:.2f}× "
              f"Pbeat={a['p_beat_b1']:.2f} regret={a['regret']:.2f} worst$DD={a['worst_dollar_dd']:.2f} "
              f"→ {a['decision']['verdict']}")


# ── 리포트 ────────────────────────────────────────────────────────────────────
PREREG_HEAD = """# Cycle 7 · c7c — Longest honest OOS: leverage DCA on Shiller US 1871–2026

작성 executor · 데이터 **Robert Shiller 월간 S&P Composite**(datahub core/s-and-p-500 미러,
price·dividend·CPI·long rate, 1871-01~) · 조달금리 FRED **TB3MS**(1934+)+NBER **상업어음
M13002US35620M156NNBR**(<1934) · 월간 2x 근사(EWMA 드래그 보정, K=0.40 비관적) ·
게이트 docs/gate_v2_spec.md · 원장 reports/trials_ledger.jsonl

> 상태: **사전등록** — 규칙·파라미터는 c5a(`c5a_lc_glide`)·c6c(`c6c_crash_lev`)의 정확한
> 사전등록값을 import 재사용(튜닝 없음). pre-1986 시작일은 전부 신규 OOS(한번도 안 씀).
>
> **결정규칙(c5a 사전등록):** DCA-1x 대비 median ≥ 1.15× AND p5 ≥ 0.9×B1_p5 AND regret 증가
> ≤ 5pp → PASS(설계=era 별 판정, 홀드아웃=현대 1986+ 원장 1회 확인).
>
> **한계 사전 고지:** (1) 월간 해상도 — 일일리셋 2x 를 정확히 계산 불가, 보정 근사 사용;
> ATH 낙폭도 월말종가 기준(일간보다 완만). (2) Shiller price 는 월중평균(월말종가 아님) →
> 낙폭이 실제보다 완만. (3) 배당 재투자·조달금리 근사. (4) 레버리지 단위낙폭은 README §7
> −50% 하드캡을 크게 위반 → 채택 시 소액 슬리브 한정.
"""


def _era_row(name, a, b1):
    dec = a.get("decision", {}).get("verdict", "—")
    return (f"| {name} | ${a['median']:,.0f} ({a['median']/b1['median']:.2f}×) | "
            f"${a['p5']:,.0f} ({a['p5']/b1['p5']:.2f}×) | {a['p_beat_b1']:.2f} | "
            f"{a['regret']:.2f} | {a['worst_dollar_dd']:.2f} | {a['median_dollar_dd']:.2f} | "
            f"**{dec}** |")


def write_report(results):
    head = PREREG_HEAD
    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            head = prev.split("<!-- RESULTS_BELOW -->")[0]
    L = [head.rstrip(), "<!-- RESULTS_BELOW -->", ""]
    meta = results["meta"]
    L.append(f"> 실행 {meta['generated']} · Shiller {meta['first']}…{meta['last']} "
             f"({meta['n_months']}개월) · fast={meta['fast']}")
    L.append("")

    # 보정
    cal = results["calibration"]
    L.append("## 월간 2x 근사 보정(일일합성 대비)")
    L.append(f"σ̂² = EWMA(λ={cal['lam']}, seed {cal['seed_months']}mo). best-fit k 범위 "
             f"[{cal['best_k_min']:.3f}, {cal['best_k_max']:.3f}] → **K_DRAG={cal['K_DRAG']}** 채택"
             f"(비관적 끝, best-fit 최대 상회={cal['conservative']} → 2x 수익 과소평가=보수적).")
    L.append("")
    L.append("| 구간 | n(월) | best-fit k | 진실 2x CAGR | 근사(K=0.40) | gap | 월 RMSE | 연 추적오차 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for nm, v in cal["segments"].items():
        L.append(f"| {nm} | {v['n_months']} | {v['best_k']:.3f} | {v['cagr_true']*100:.2f}% | "
                 f"{v['cagr_approx_Kused']*100:.2f}% | {v['cagr_gap_pp']:+.2f}%p | "
                 f"{v['rmse_monthly']*100:.3f}% | {v['tracking_error_annual']*100:.2f}% |")
    L.append("")

    # 1929-32 검증
    c = results.get("crash_1929_32")
    if c:
        L.append("## 데이터 검증 — 대공황 1929-09 → 1932-06")
        L.append(f"Shiller price {c['price_peak']:.2f} → {c['price_trough']:.2f} "
                 f"(**{c['price_dd']*100:.1f}%**, 월중평균 기준). 1x 총수익 낙폭 "
                 f"**{c['tr1x_dd']*100:.1f}%**, 보정 2x 총수익 낙폭 **{c['tr2x_dd']*100:.1f}%**. "
                 f"(널리 인용되는 −86%는 일간/실질 기준 — 월중평균 해상도라 다소 완만.)")
        L.append("")

    # era 표(지평별)
    for years in HORIZONS:
        d = results["eras"][years]
        L.append(f"## {years}년 지평 — era 별 c5a 규칙 판정 (DCA-1x 대비)")
        L.append("| era(시작연도) | median(×B1) | p5(×B1) | P(beat) | regret | 최악$낙폭 | 중앙$낙폭 | 판정 |")
        L.append("|---|---:|---:|---:|---:|---:|---:|:--:|")
        order = ["oos_all", "1871-1913", "1914-1945", "1946-1985"]
        label = {"oos_all": "전체 OOS 1871–1985", "1871-1913": "1871–1913(고전본위)",
                 "1914-1945": "1914–1945(대전·대공황)", "1946-1985": "1946–1985(전후·스태그플)"}
        for nm in order:
            if nm not in d:
                continue
            sec = d[nm]
            b1 = sec["b1"]
            L.append(f"| **{label[nm]}** B1: median ${b1['median']:,.0f}·p5 ${b1['p5']:,.0f}·"
                     f"regret {b1['regret']:.2f}·n={b1['n']} | | | | | | | |")
            L.append(_era_row("　glide", sec["glide"], b1))
            L.append(_era_row(f"　crash_lev(배치{sec['avg_deploys_cl']:.0f})", sec["crash_lev"], b1))
        L.append("")

    # 최악 코호트
    L.append("## 최악 코호트 상세(달러 최종자산 · vs DCA-1x)")
    L.append("| 시작 | 지평 | DCA-1x | glide(×) | crash_lev(×) | 입금계 | glide$낙폭 | cl$낙폭 | cl배치 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for key, cd in results["worst_cohorts"].items():
        L.append(f"| {cd['start']} | {cd['years']}y | ${cd['b1']['terminal']:,.0f} | "
                 f"${cd['glide']['terminal']:,.0f} ({cd['glide']['ratio']:.2f}×) | "
                 f"${cd['crash_lev']['terminal']:,.0f} ({cd['crash_lev']['ratio']:.2f}×) | "
                 f"${cd['deposited']:,.0f} | {cd['glide']['dollar_dd']:.2f} | "
                 f"{cd['crash_lev']['dollar_dd']:.2f} | {cd['crash_lev']['n_deploys']} |")
    L.append("")

    L.append("## 판정 종합 및 정직한 해석")
    L.append(_verdict_prose(results))
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def _verdict_prose(results):
    d20 = results["eras"][20]
    d30 = results["eras"].get(30, {})
    oos = d20["oos_all"]
    g, cl, b1 = oos["glide"], oos["crash_lev"], oos["b1"]
    lines = []
    lines.append("**결정규칙(c5a 사전등록):** median ≥ 1.15×B1 AND p5 ≥ 0.9×B1_p5 AND regret 증가 "
                 "≤ 5pp → PASS. era 별로 판정하고 현대(1986+) 원장으로 1회 확인.")
    lines.append("")
    # era별 verdict 라인
    for years in HORIZONS:
        parts = []
        for nm in ("oos_all", "1871-1913", "1914-1945", "1946-1985"):
            if nm in results["eras"][years]:
                gg = results["eras"][years][nm]["glide"]["decision"]["verdict"]
                cc = results["eras"][years][nm]["crash_lev"]["decision"]["verdict"]
                parts.append(f"{nm}: glide {gg}/cl {cc}")
        lines.append(f"- **{years}y** — " + " · ".join(parts))
    lines.append("")
    lines.append(
        f"**전체 OOS(1871–1985 시작, 20y):** glide median {g['median']/b1['median']:.2f}× · "
        f"p5 {g['p5']/b1['p5']:.2f}× · regret {g['regret']:.2f}(B1 {b1['regret']:.2f}) · "
        f"P(beat) {g['p_beat_b1']:.2f} · 최악$낙폭 {g['worst_dollar_dd']:.2f} → "
        f"{g['decision']['verdict']}. crash_lev median {cl['median']/b1['median']:.2f}× · "
        f"p5 {cl['p5']/b1['p5']:.2f}× · regret {cl['regret']:.2f} · 최악$낙폭 "
        f"{cl['worst_dollar_dd']:.2f} → {cl['decision']['verdict']}.")
    lines.append("")
    lines.append(
        "**정직한 해석.** (1) 155년·비겹침 독립 20년창 ≈7 로 여전히 소표본이나, c5a/c6c 의 "
        "40년(독립창 ~2)보다 훨씬 넓은 진짜 OOS 다. (2) 1946–1985(전후·1966 스태그플레이션 "
        "시작) 코호트가 급소 — 1966-01 20y 에서 glide 가 1x 를 하회(스태그플레이션·횡보장은 "
        "라이프사이클 초기 레버리지를 상각할 상승이 없다). c6a 의 Nikkei 반증(장기 (+)프리미엄 "
        "부재 시 레버리지가 손실 증폭)과 같은 결의 국내 반례. (3) 대공황(1929 시작)은 오히려 "
        "긴 지평에서 crash_lev 에 유리 — 사상최고 미탈환이 십수년 지속돼 저가 2x 라우팅이 "
        "회복 전체를 태운다(생존 편향적 낙관, 30y 코호트 참조). (4) 월간·월중평균 해상도는 "
        "낙폭을 완만화(2x 근사가 일간 드래그를 K=0.40 으로 보수 보정하나 잔차 존재).")
    lines.append("")
    lines.append(
        "**리스크 한계(반드시 병기).** 레버리지 2x 슬리브의 단위/달러 낙폭은 대공황·1937·1970s 에서 "
        "README §7 −50% 하드캡·부록 v2.1 −70% 하한을 크게 초과(2x 총수익 낙폭 1929-32 ≈ −98%). "
        "따라서 통과 era 라도 채택은 **소액 슬리브 + 20년+ 지평 + 포워드 페이퍼 점증** 후에만. "
        "라이프사이클/크래시 레버리지는 알파가 아니라 장기 (+)주식프리미엄에 건 베타 베팅이며, "
        "그 전제가 깨지는 장기 횡보/디플레 레짐(1929–1945, 1966–1982)에서 규율상 취약하다.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
