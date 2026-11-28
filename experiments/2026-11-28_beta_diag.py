#!/usr/bin/env python3
"""R3 β조정 z 진단 (2026-11-28) — DEMOTED 묶음의 음의 z가 공통 베타/레짐으로 설명되는가.

진단 전용: 상태기계·원장·src 불변. 장부(toss-trader/data/paperlab)·캐시(_hist_cache)는 읽기만 한다.

사전등록(1차 실행 전 고정)
- 대상: buffer_hibeta, simple_hibeta, ftlt_hibeta, ftlt_hibeta_psq, hibeta_basket, lrs200_tqqq,
  simple_rsi_uvxy, holygrail_hibeta + 대조군 qqq_bh, tqqq_bh.
- 백테스트 모형: 2021-01-01 백필(현재 캐시 + 수정 후 코드, contribute=False) 중 라이브 시작(2026-09-28) 이전,
  일간 로그수익 OLS. r_q = qqq_bh 단위장부 일간 로그수익. 동결 기대치(exp, tvol) 재현도도 보고.
- z_model = Σ_t (r_s,t − pred_t) / (σe·√m), m = 라이브 정렬 스텝 수. pred = 백테스트 모형의 예측
  (= 실현 QQQ 경로를 넣었을 때 기대 수익). Σ(r_s − pred) 는 "엔진 excess_qqq − 모형 기대 excess"와 같다.
- 판정: z ≤ −2 GENUINE / |z| < 2 BETA/REGIME / z ≥ 2 OVER.

사후 수정(1차 실행 후, 결과를 보고 바꾼 것 — 리포트에 명시)
- (A1) 1차 주 지표였던 선형 모형 M1(r_s = a + b·r_q)이 **대조군 tqqq_bh 를 GENUINE(z −3.98)으로 오판**했다.
  원인은 레버리지 변동성 끌림(log(1+3R) ≈ 3·log(1+R) − 3R²)이 실현 분산에 비례하는데 M1 은 이를 평균 α 로만
  흡수하기 때문이다. 그래서 주 지표를 M2(r_s = a + b·r_q + c·r_q²)로 바꾼다. M1 도 함께 보고한다.
- (A2) 1차 레짐 모형은 약세장 계수를 라이브 전 구간에 적용했다. 실제 라이브 43스텝 중 약세장은 13스텝이다.
  그래서 M3 는 날짜별로 레짐(QQQ 전일 종가 < SMA200)에 맞는 계수를 적용하는 구간별 M2 로 바꾼다.
- (A3) 장부 일간 경로에는 정체 봉 표시(11-09 TQQQ 등)가 섞여 있다. 누적값 X 는 그대로지만 β_live 와
  σe_live 를 왜곡한다. 그래서 전 전략에서 **재생 곡선(09-28 시작)**을 주 곡선으로 쓴다. 재생과 장부의
  누적 일치 여부는 R2 결과를 따른다. 오염 4종은 장부 값을 별도 행으로 병기한다.
- (A4) 견고성 점검으로 jackknife 를 추가한다. 하루씩 빼고 다시 계산한 M2 z 의 최댓값이 −2 를 넘으면 "단일일 의존"으로 표시한다.

실행: .venv/bin/python experiments/2026-11-28_beta_diag.py [--data DIR] [--out reports/2026-11-28_beta_diag_tables.md]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "loop"))

from toss_trader import histdata  # noqa: E402
from toss_trader.paperlab import PaperLab  # noqa: E402
from toss_trader.paperlab_strategies._universes import NDX_100  # noqa: E402
import paperlab_run as PR  # noqa: E402
import daily_decision as DD  # noqa: E402

DEFAULT_DATA = Path("/Users/mingh/github/toss-trader/data")
LIVE_START = date(2026, 9, 28)
BT_START = date(2021, 1, 1)
TARGETS = ["buffer_hibeta", "simple_hibeta", "ftlt_hibeta", "ftlt_hibeta_psq", "hibeta_basket",
           "holygrail_hibeta", "lrs200_tqqq", "simple_rsi_uvxy", "qqq_bh", "tqqq_bh"]
CONTAMINATED = {"ftlt_hibeta", "ftlt_hibeta_psq", "simple_hibeta", "holygrail_hibeta"}
HIBETA = {"buffer_hibeta", "simple_hibeta", "ftlt_hibeta", "ftlt_hibeta_psq", "hibeta_basket",
          "holygrail_hibeta"}


def unit_curve(equity: list) -> list[tuple[str, float]]:
    return [(str(r[0]), float(r[1])) for r in equity if len(r) >= 2 and float(r[1]) > 0]


def aligned(curve, bench: dict[str, float]):
    """(date, r_s, r_q) 일간 로그수익. 엔진 _cum_log_excess 와 같은 정렬 규칙을 쓴다."""
    out = []
    for i in range(1, len(curve)):
        d0, v0 = curve[i - 1]
        d1, v1 = curve[i]
        b0, b1 = bench.get(d0), bench.get(d1)
        if v0 > 0 and v1 > 0 and b0 and b1:
            out.append((d1, math.log(v1 / v0), math.log(b1 / b0)))
    return out


def _solve(A, y):
    n = len(y)
    M = [row[:] + [y[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-18:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                k = M[r][c] / M[c][c]
                M[r] = [a - k * b for a, b in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def feats(rq: float, quad: bool) -> list[float]:
    return [1.0, rq, rq * rq] if quad else [1.0, rq]


def fit(rows, quad: bool):
    """OLS r_s ~ feats(r_q). 반환: coef, sigma_e(일간), se_b, r2, n."""
    n = len(rows)
    k = 3 if quad else 2
    if n < k + 3:
        return None
    X = [feats(rq, quad) for _, _, rq in rows]
    y = [rs for _, rs, _ in rows]
    XtX = [[sum(x[i] * x[j] for x in X) for j in range(k)] for i in range(k)]
    Xty = [sum(x[i] * yy for x, yy in zip(X, y)) for i in range(k)]
    coef = _solve(XtX, Xty)
    if coef is None:
        return None
    res = [yy - sum(c * xi for c, xi in zip(coef, x)) for x, yy in zip(X, y)]
    sse = sum(e * e for e in res)
    sig = math.sqrt(sse / (n - k))
    my = statistics.fmean(y)
    sst = sum((v - my) ** 2 for v in y)
    # se(b): (XtX)^-1[1,1] * sig^2
    e1 = [1.0 if i == 1 else 0.0 for i in range(k)]
    inv_col = _solve(XtX, e1)
    se_b = math.sqrt(max(inv_col[1], 0.0)) * sig if inv_col else float("nan")
    return {"coef": coef, "sig": sig, "se_b": se_b, "r2": 1 - sse / sst if sst > 0 else float("nan"),
            "n": n, "quad": quad, "res": res}


def pred(m, rq):
    return sum(c * x for c, x in zip(m["coef"], feats(rq, m["quad"])))


def z_model(rows, pick, sig_override: float | None = None) -> float | None:
    """pick(date) → 그날 적용할 모형. z = Σ(r_s − pred) / sqrt(Σσ²)."""
    if not rows:
        return None
    num = 0.0
    var = 0.0
    for d, rs, rq in rows:
        m = pick(d)
        if m is None:
            return None
        num += rs - pred(m, rq)
        s = sig_override if sig_override is not None else m["sig"]
        var += s * s
    return num / math.sqrt(var) if var > 0 else None


def regime_days(qqq_closes: list[tuple[str, float]]) -> set[str]:
    """QQQ 전일 종가 < 전일까지 SMA200 인 날(t+1 체결 관점에서 관측 가능한 레짐)."""
    vals = [v for _, v in qqq_closes]
    out = set()
    for i in range(201, len(qqq_closes)):
        if vals[i - 1] < sum(vals[i - 201:i - 1]) / 200:
            out.add(qqq_closes[i][0])
    return out


def verdict(z: float | None) -> str:
    if z is None:
        return "n/a"
    return "GENUINE" if z <= -2 else ("BETA/REGIME" if z < 2 else "OVER")


def f(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:+.{nd}%}" if pct else f"{x:+.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "2026-11-28_beta_diag_tables.md")
    args = ap.parse_args()

    cache = args.data / "_hist_cache"
    histdata.CACHE_DIR = cache
    PR.HIST_CACHE = cache
    expect_all = json.loads((ROOT / "docs/loop/backtest_expectations.json").read_text())["strategies"]

    swing_uni = PR._cached_universe()
    mom_uni = [s for s in NDX_100 if PR._cache_file(s).exists()]
    roster = {s.name: s for s in PR._build(swing_uni, mom_uni, PR._hibeta_cached())}
    needed = {"QQQ", "TQQQ"}
    for nm in TARGETS:
        needed.update(roster[nm].universe())
    panel = PR._load_panel(sorted(needed), offline=True)
    by_date = PR._by_date(panel)
    master = PR._master_dates(panel)

    ledger, replay, bt = {}, {}, {}
    for nm in TARGETS:
        p = args.data / "paperlab" / nm / "state.json"
        ledger[nm] = unit_curve(json.loads(p.read_text(encoding="utf-8"))["equity"])
        lab = PaperLab(roster[nm])
        replay[nm] = unit_curve(lab.run(lab.fresh_state(LIVE_START), master, by_date)["equity"])
        lab = PaperLab(roster[nm])
        st = lab.run(lab.fresh_state(BT_START), master, by_date, contribute=False)
        bt[nm] = [(d, v) for d, v in unit_curve(st["equity"]) if d < LIVE_START.isoformat()]
        print(f"{nm}: ledger n={len(ledger[nm])} replay n={len(replay[nm])} bt n={len(bt[nm])}", flush=True)

    q_led, t_led = dict(ledger["qqq_bh"]), dict(ledger["tqqq_bh"])
    q_rep, t_rep = dict(replay["qqq_bh"]), dict(replay["tqqq_bh"])
    q_bt = dict(bt["qqq_bh"])
    bear = regime_days([(c.dt.isoformat(), c.close) for c in panel["QQQ"]])
    qrows = aligned(replay["qqq_bh"], q_rep)
    qsum = sum(rq for _, _, rq in qrows)
    qvar_live = statistics.pstdev([rq for _, _, rq in qrows]) * math.sqrt(252)
    qbt_rows = aligned(bt["qqq_bh"], q_bt)
    qvar_bt = statistics.pstdev([rq for _, _, rq in qbt_rows]) * math.sqrt(252)
    live_bear = sum(1 for d, _, _ in qrows if d in bear)

    out_rows, res_live = [], {}
    for nm in [t for t in TARGETS if t != "qqq_bh"]:  # qqq_bh = 항등 대조(X≡0, z 정의 불가)
        exp = expect_all[nm]
        bt_rows = aligned(bt[nm], q_bt)
        m1 = fit(bt_rows, quad=False)
        m2 = fit(bt_rows, quad=True)
        m2_bear = fit([r for r in bt_rows if r[0] in bear], quad=True)
        m2_bull = fit([r for r in bt_rows if r[0] not in bear], quad=True)
        ex_d = [rs - rq for _, rs, rq in bt_rows]
        exp_rep = statistics.fmean(ex_d) * 252
        tvol_rep = statistics.pstdev(ex_d) * math.sqrt(252)
        variants = [("replay", replay[nm], q_rep, t_rep)]
        if nm in CONTAMINATED:
            variants.append(("ledger", ledger[nm], q_led, t_led))
        for src, curve, qb, tb in variants:
            rows = aligned(curve, qb)
            met = DD.compute_metrics(curve, qb, tb, exp)
            fl = fit(rows, quad=False)
            te_live = statistics.pstdev([rs - rq for _, rs, rq in rows])
            z_vol = ((met.excess_qqq - exp["exp_annual_log_excess_vs_qqq"] * met.n / 252)
                     / (te_live * math.sqrt(met.n)))
            z1 = z_model(rows, lambda d: m1)
            z2 = z_model(rows, lambda d: m2)
            z3 = z_model(rows, lambda d: m2_bear if d in bear else m2_bull)
            sig2_live = statistics.stdev([rs - pred(m2, rq) for _, rs, rq in rows])
            z2l = z_model(rows, lambda d: m2, sig_override=max(sig2_live, m2["sig"]))
            jk = [z_model(rows[:i] + rows[i + 1:], lambda d: m2) for i in range(len(rows))]
            jk_max = max(jk)
            jk_day = rows[jk.index(jk_max)][0]
            exp_x2 = sum(pred(m2, rq) - rq for _, _, rq in rows)
            if src == "replay":
                res_live[nm] = [rs - pred(m2, rq) for _, rs, rq in rows]
            led_met = DD.compute_metrics(ledger[nm], q_led, t_led, exp)
            out_rows.append(dict(
                name=nm, src=src, n=met.n, cum=met.cum, x=met.excess_qqq, z=met.z, z_led=led_met.z,
                x_led=led_met.excess_qqq, b_live=fl["coef"][1], se_live=fl["se_b"],
                r2_live=fl["r2"], b1=m1["coef"][1], a1=m1["coef"][0] * 252, b2=m2["coef"][1], c2=m2["coef"][2],
                a2=m2["coef"][0] * 252, sig1=m1["sig"] * math.sqrt(252), sig2=m2["sig"] * math.sqrt(252),
                sig2_live=sig2_live * math.sqrt(252), b_bear=m2_bear["coef"][1], n_bear=m2_bear["n"],
                exp_x2=exp_x2, z1=z1, z2=z2, z3=z3, z2l=z2l, zvol=z_vol, jk_max=jk_max, jk_day=jk_day,
                te_live=te_live * math.sqrt(252), exp=exp["exp_annual_log_excess_vs_qqq"],
                tvol=exp["tracking_vol_annual"], exp_rep=exp_rep, tvol_rep=tvol_rep))

    # 엔진 상태(장부 곡선) → RETIRED 남은 세션
    retire = {}
    for nm in TARGETS:
        if nm in DD.BENCHMARKS:
            continue
        hist = DD.replay_strategy(ledger[nm], q_led, t_led, expect_all[nm])
        streak = 0
        for h in hist:
            streak = streak + 1 if h.state in ("DEMOTED", "RETIRED") else 0
        first = next((h.date for h in hist if h.state == "DEMOTED"), None)
        retire[nm] = (hist[-1].state, streak, max(DD.RETIRE_SESSIONS - streak, 0), first)

    hb = [nm for nm in TARGETS if nm in HIBETA]
    cors = [statistics.correlation(res_live[a], res_live[b])
            for i, a in enumerate(hb) for b in hb[i + 1:]]
    rbar = statistics.fmean(cors)
    n_eff = len(hb) / (1 + (len(hb) - 1) * rbar)

    L = [
        "<!-- generated by experiments/2026-11-28_beta_diag.py -->",
        f"- 라이브: {qrows[0][0]}~{qrows[-1][0]} m={len(qrows)} 스텝. QQQ Σr_q = {qsum:+.4f} ({math.exp(qsum) - 1:+.2%}), "
        f"QQQ 실현 vol {qvar_live:.1%}/yr (백테스트 {qvar_bt:.1%}). QQQ<SMA200 레짐 스텝 {live_bear}/{len(qrows)}.",
        "",
        "### 표 1. z 비교 (주 곡선 = 재생; `(ledger)` = 오염 장부 병기)",
        "",
        "| 전략 | 곡선 | 누적 | X | plain z | β_live ±SE | β_bt(M1) | β_bt(M2) | c(r²) | M2 기대 X | z_M1 | **z_M2** | z_M3 레짐 | z_M2 liveσ | z_vol | JK max (뺀 날) | 판정(M2) |",
        "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for r in out_rows:
        jk = f"{f(r['jk_max'])} ({r['jk_day'][5:]})"
        vd = verdict(r["z2"])
        if vd == "GENUINE" and r["jk_max"] > -2:
            vd += " · 단일일 의존"
        if (r["z2"] <= -2) != (r["z3"] <= -2):
            vd += " · 레짐 민감"
        L.append(
            f"| {r['name']} | {r['src']} | {f(r['cum'], 1, True)} | {f(r['x'], 3)} | {f(r['z'])} | "
            f"{r['b_live']:.2f}±{r['se_live']:.2f} | {f(r['b1'])} | {f(r['b2'])} | {f(r['c2'], 1)} | "
            f"{f(r['exp_x2'], 3)} | {f(r['z1'])} | **{f(r['z2'])}** | {f(r['z3'])} | {f(r['z2l'])} | "
            f"{f(r['zvol'])} | {jk} | {vd} |")
    L += [
        "",
        "### 표 2. 분모·재현도",
        "",
        "| 전략 | σe M1 | σe M2 | σe M2 라이브 | TE 동결 | TE 재현 | TE 라이브 | exp 동결 | exp 재현 | α M2/yr | R²_live | 약세 β(M2) n | 장부 X / 재생 X | 장부 z |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|",
    ]
    for r in out_rows:
        if r["src"] != "replay":
            continue
        L.append(
            f"| {r['name']} | {f(r['sig1'], 1, True)} | {f(r['sig2'], 1, True)} | {f(r['sig2_live'], 1, True)} | "
            f"{f(r['tvol'], 1, True)} | {f(r['tvol_rep'], 1, True)} | {f(r['te_live'], 1, True)} | "
            f"{f(r['exp'], 1, True)} | {f(r['exp_rep'], 1, True)} | {f(r['a2'], 1, True)} | {f(r['r2_live'])} | "
            f"{r['b_bear']:+.2f} ({r['n_bear']}) | {f(r['x_led'], 3)} / {f(r['x'], 3)} | {f(r['z_led'])} |")
    L += ["", "### 표 3. 엔진 상태(장부 곡선 리플레이)", "",
          "| 전략 | 상태 | 첫 DEMOTED | DEMOTED 연속 | RETIRED 까지 세션 |", "|---|---|---|---:|---:|"]
    for nm, (s, k, left, first) in retire.items():
        L.append(f"| {nm} | {s} | {first or '–'} | {k} | {left} |")
    L += ["", f"### hibeta 6종 라이브 M2 잔차 평균 쌍상관 = {rbar:+.2f} → 유효 독립 흐름 ≈ {n_eff:.2f}/{len(hb)}", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
