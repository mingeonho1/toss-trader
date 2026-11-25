#!/usr/bin/env python3
"""감사 2026-11-25 R2 진단 — 현재 캐시로 페이퍼랩 전체를 2026-09-28부터 재생(수정 후 코드) vs 실제 장부.

읽기 전용: 캐시(toss-trader/data/_hist_cache)·장부(toss-trader/data/paperlab)는 읽기만 하고, 재생 결과는
메모리에서만 계산한다(장부 덮어쓰기 없음). 비교 = 단위장부($1,000) 누적수익, 두 곡선의 공통 마지막 날짜 기준.

재현: PYTHONPATH=src .venv/bin/python experiments/2026-11-25_ledger_replay.py [--data DIR] [--out reports/...md]
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

from toss_trader import histdata  # noqa: E402
from toss_trader.paperlab import PaperLab  # noqa: E402
from toss_trader.paperlab_strategies._universes import NDX_100  # noqa: E402
import paperlab_run as PR  # noqa: E402

DEFAULT_DATA = Path("/Users/mingh/github/toss-trader/data")
START = date(2026, 9, 28)


def _cum_at(equity: list, upto: str) -> tuple[float | None, str | None]:
    rows = [r for r in equity if r[0] <= upto]
    if not rows:
        return None, None
    return float(rows[-1][1]) / 1000.0 - 1.0, rows[-1][0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "2026-11-25_ledger_replay.md")
    args = ap.parse_args()

    cache = args.data / "_hist_cache"
    histdata.CACHE_DIR = cache          # 읽기 전용: 캐시 파일이 있는 심볼만 로드(_load_from_cache 가 존재 확인)
    PR.HIST_CACHE = cache

    swing_uni = PR._cached_universe()
    mom_uni = [s for s in NDX_100 if PR._cache_file(s).exists()]
    roster = PR._build(swing_uni, mom_uni, PR._hibeta_cached())
    needed = {"QQQ", "TQQQ"}
    for strat in roster:
        needed.update(strat.universe())
    panel = PR._load_panel(sorted(needed), offline=True)
    by_date = PR._by_date(panel)
    master = PR._master_dates(panel)

    rows = []
    for strat in roster:
        lab = PaperLab(strat)
        st = lab.run(lab.fresh_state(START), master, by_date)
        p = args.data / "paperlab" / strat.name / "state.json"
        actual = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        if not actual or not actual.get("equity") or not st.get("equity"):
            rows.append((strat.name, None, None, None, st.get("last_date"), actual and actual.get("last_date")))
            continue
        common = min(actual["equity"][-1][0], st["equity"][-1][0])
        a, ad = _cum_at(actual["equity"], common)
        r, _ = _cum_at(st["equity"], common)
        rows.append((strat.name, ad, a, r, st.get("last_date"), actual.get("last_date")))

    lines = [
        "# 2026-11-25 장부 재생(R2 진단) — 실제 장부 vs 현재 캐시 재생(수정 후 코드)",
        "",
        f"- 재생: {START} 시작, 캐시 `{cache}`(읽기 전용), 장부 덮어쓰기 없음. 단위장부($1,000) 누적수익.",
        "- 비교 시점 = 장부·재생 곡선의 공통 마지막 날짜. 재생 마지막일이 장부보다 이르면 늦은 봉 대기(수정 3) 때문.",
        "- 한계: 유니버스는 현재 캐시 기준(장부 당시와 다를 수 있음), 조정종가 재계산 차이 가능. 수정 전 코드 재생은 생략.",
        "",
        "| 전략 | 비교일 | 장부 누적 | 재생 누적 | 차이(%p) | 재생 last | 장부 last |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for name, d, a, r, rl, al in rows:
        if a is None or r is None:
            lines.append(f"| {name} | – | – | – | – | {rl} | {al} |")
            continue
        flag = " **" if abs(r - a) >= 0.02 else ""
        lines.append(f"| {name} | {d} | {a:+.2%} | {r:+.2%} | {(r - a) * 100:+.2f}{flag} | {rl} | {al} |")
    lines += ["", "`**` = |차이| ≥ 2%p(장부 아티팩트 의심).", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
