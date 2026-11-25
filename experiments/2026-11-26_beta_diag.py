#!/usr/bin/env python3
"""R3 진단(2026-11-26) — DEMOTED 군 + 벤치의 라이브 β(일간 OLS vs qqq_bh), β조정 잔차 z, 라이브 vol 재척도 z,
hibeta 라이브 β vs 백테스트 β=1.6 이동블록 부트스트랩(블록 5, 1000회, seed 0).

진단 전용 — 상태 판정 불사용. 읽기 전용: toss-trader/data/paperlab/*/state.json 의 단위장부 equity 만 읽는다.
날짜 정렬: 전략·qqq_bh equity 의 공통 날짜 inner join 후 수익률 계산(결측일은 양쪽 모두 2일 수익으로 합쳐짐).

정의(n = 공통 일수익 수):
- beta, alpha: r_s = a + b·r_q + e (OLS, 단순수익).
- resid_z = mean(e) / (sd(e)/sqrt(n))   — β 조정 후 잔차 평균의 t 통계.
- raw_z   = mean(x) / (sd(x)/sqrt(n)), x = log(1+r_s) - log(1+r_q)  — 라이브 TE 로 척도한 초과 z.
- volscaled_z = raw_z × (vol_bt_qqq / vol_live_qqq) 가 아니라, 결정 메모의 원장 z 를 라이브/백테스트 vol 비(--vol-ratio,
  기본 1.5)로 나눈 값으로 별도 열 표기(원장 z 는 state.json 에 없으므로 --ledger-z JSON 으로 주입, 없으면 공란).

재현: .venv/bin/python experiments/2026-11-26_beta_diag.py [--data DIR] [--out reports/2026-11-26_beta_diag.md]
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = Path("/Users/mingh/github/toss-trader/data")
HIBETA = ["buffer_hibeta", "ftlt_hibeta", "ftlt_hibeta_psq", "hibeta_basket", "simple_hibeta"]
OTHERS = ["holygrail_hibeta", "lrs200_tqqq", "simple_rsi_uvxy", "tqqq_bh", "qqq_bh"]
BT_BETA = 1.6
BLOCK, NBOOT, SEED = 5, 1000, 0
# 결정 메모 2026-11-25 원장 z(세션 2026-11-24, n=42). 표기용 입력값(계산값 아님).
LEDGER_Z = {"buffer_hibeta": -2.03, "ftlt_hibeta": -2.44, "simple_hibeta": -2.34, "ftlt_hibeta_psq": -2.24,
            "hibeta_basket": -2.96, "lrs200_tqqq": -2.71, "simple_rsi_uvxy": -2.14, "tqqq_bh": -2.13,
            "holygrail_hibeta": -1.41}


def load_eq(data: Path, name: str) -> dict[str, float]:
    p = data / "paperlab" / name / "state.json"
    st = json.loads(p.read_text(encoding="utf-8"))
    return {r[0]: float(r[1]) for r in st.get("equity") or []}


def rets(eq_s: dict[str, float], eq_q: dict[str, float]) -> tuple[list[float], list[float], list[str]]:
    ds = sorted(set(eq_s) & set(eq_q))
    rs, rq, out = [], [], []
    for a, b in zip(ds, ds[1:]):
        if eq_s[a] > 0 and eq_q[a] > 0:
            rs.append(eq_s[b] / eq_s[a] - 1.0)
            rq.append(eq_q[b] / eq_q[a] - 1.0)
            out.append(b)
    return rs, rq, out


def mean(x): return sum(x) / len(x)


def sd(x):
    m = mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1)) if len(x) > 1 else float("nan")


def ols(y, x):
    mx, my = mean(x), mean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sxx if sxx > 0 else float("nan")
    a = my - b * mx
    return a, b, [yi - a - b * xi for xi, yi in zip(x, y)]


def tstat(x):
    s = sd(x)
    return mean(x) / (s / math.sqrt(len(x))) if s and s > 0 else float("nan")


def block_boot_beta(rs, rq, rng):
    n = len(rs)
    starts = list(range(0, n - BLOCK + 1))
    out = []
    for _ in range(NBOOT):
        ys, xs = [], []
        while len(ys) < n:
            i = rng.choice(starts)
            ys.extend(rs[i:i + BLOCK]); xs.extend(rq[i:i + BLOCK])
        out.append(ols(ys[:n], xs[:n])[1])
    out.sort()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "2026-11-26_beta_diag.md")
    ap.add_argument("--vol-ratio", type=float, default=None,
                    help="라이브/백테스트 QQQ vol 비. 미지정 시 라이브 QQQ 연율 vol / 0.235(NDX 1986- 장기 근사)")
    args = ap.parse_args()

    eq_q = load_eq(args.data, "qqq_bh")
    rng = random.Random(SEED)
    rows, boots = [], {}
    q_vol = None
    for name in HIBETA + OTHERS:
        try:
            eq_s = load_eq(args.data, name)
        except FileNotFoundError:
            rows.append((name, None)); continue
        rs, rq, ds = rets(eq_s, eq_q)
        if len(rs) < 10:
            rows.append((name, None)); continue
        a, b, e = ols(rs, rq)
        x = [math.log1p(s) - math.log1p(q) for s, q in zip(rs, rq)]
        q_vol = sd(rq) * math.sqrt(252)
        rows.append((name, dict(n=len(rs), first=ds[0], last=ds[-1], beta=b, alpha_ann=a * 252,
                                resid_z=tstat(e), raw_z=tstat(x), vol=sd(rs) * math.sqrt(252),
                                cum_excess=sum(x))))
        if name in HIBETA:
            boots[name] = block_boot_beta(rs, rq, rng)

    vr = args.vol_ratio if args.vol_ratio else (q_vol / 0.235 if q_vol else float("nan"))
    L = ["# 2026-11-26 R3 β 진단 — DEMOTED 군 + 벤치 (진단 전용, 상태 판정 불사용)", "",
         f"- 원천: `{args.data}/paperlab/*/state.json` 단위장부 equity(읽기 전용), 기준 qqq_bh 공통일 inner join.",
         f"- 라이브 QQQ 연율 vol = {q_vol:.1%}, vol 비(라이브/백테스트) = {vr:.2f}. 재척도 z = 원장 z / vol 비.",
         "",
         "| 전략 | n | 기간 | β | α(연율, 단순) | 잔차 z(β조정) | 라이브TE z | 원장 z | vol재척도 z | 연율 vol | 누적 초과로그 |",
         "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, r in rows:
        if r is None:
            L.append(f"| {name} | – | 장부 없음/짧음 | | | | | | | | |"); continue
        lz = LEDGER_Z.get(name)
        lzs = f"{lz:+.2f}" if lz is not None else "–"
        vzs = f"{lz / vr:+.2f}" if lz is not None and vr == vr else "–"
        L.append(f"| {name} | {r['n']} | {r['first']}..{r['last']} | {r['beta']:.2f} | {r['alpha_ann']:+.1%} | "
                 f"{r['resid_z']:+.2f} | {r['raw_z']:+.2f} | {lzs} | {vzs} | {r['vol']:.1%} | {r['cum_excess']:+.2%} |")
    L += ["", f"## hibeta β vs 백테스트 {BT_BETA} — 이동블록 부트스트랩(블록 {BLOCK}, {NBOOT}회, seed {SEED})", "",
          "| 전략 | β | 95% CI | P(β*≤1.6) | 판정 |", "|---|---:|---|---:|---|"]
    for name in HIBETA:
        bb = boots.get(name)
        r = dict(rows)[name]
        if not bb or r is None:
            L.append(f"| {name} | – | – | – | 데이터 없음 |"); continue
        lo, hi = bb[int(0.025 * NBOOT)], bb[int(0.975 * NBOOT) - 1]
        p = sum(1 for v in bb if v <= BT_BETA) / NBOOT
        verdict = "라이브 β > 1.6 (CI 하한 > 1.6)" if lo > BT_BETA else "1.6 과 구분 불가"
        L.append(f"| {name} | {r['beta']:.2f} | [{lo:.2f}, {hi:.2f}] | {p:.3f} | {verdict} |")
    L += ["", "주의: n≈40 일간 관측, 블록 5 → 유효 블록 ≈8. CI 는 넓게 해석. 고베타 5종은 동일 수익흐름 A(독립표본 아님).", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
