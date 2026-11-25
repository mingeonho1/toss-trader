#!/usr/bin/env python3
"""R4 진단(2026-11-26) — hot_rvol_swing 진입 조건 재생 스캔, 2026-09-28..캐시 최신.

현재 캐시로 PaperLab 과 같은 가시 히스토리(그 날짜에 봉이 있는 경우만 append)를 만들고, 매 거래일
전략 decide() 와 동일한 조건 순서로 조건별 탈락 수와 최종 신호 수를 센다. 신호 ≥1 → BUG(장부 신호 0과 불일치),
0 → OK. 별도로 '최신성 필터 부재' 위험: 마지막 봉 날짜 < 스캔일인 심볼이 조건을 통과했는지(stale_pass) 집계.
읽기 전용: 캐시만 읽음, 장부 미접근·미기록.

재현: PYTHONPATH=src .venv/bin/python experiments/2026-11-26_hot_rvol_scan.py [--data DIR] [--out ...md]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import histdata  # noqa: E402
from toss_trader.paperlab_strategies import hot_rvol_swing as H  # noqa: E402
import paperlab_run as PR  # noqa: E402

DEFAULT_DATA = Path("/Users/mingh/github/toss-trader/data")
START = date(2026, 9, 28)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "2026-11-26_hot_rvol_scan.md")
    args = ap.parse_args()
    cache = args.data / "_hist_cache"
    histdata.CACHE_DIR = cache
    PR.HIST_CACHE = cache

    strat = H.HotRvolSwing(PR._cached_universe())
    uni = strat.universe()
    panel = PR._load_panel(sorted(set(uni) | {"QQQ"}), offline=True)
    by_date = PR._by_date(panel)
    master = PR._master_dates(panel)

    visible = {s: [] for s in uni}
    drops, per_day, signals, stale_pass = Counter(), [], [], []
    near_miss = []   # gain/near-high 통과, RVOL 만 탈락 상위
    for d in master:
        for s in uni:
            c = by_date.get(s, {}).get(d)
            if c is not None:
                visible[s].append(c)
        if d < START:
            continue
        day = Counter()
        for s in uni:
            h = visible[s]
            day["scanned"] += 1
            if not h or len(h) < H.VOL_WINDOW + 2:
                day["short_hist"] += 1; continue
            c, prev = h[-1], h[-2]
            stale = c.dt < d
            if stale:
                day["stale_bar"] += 1      # 전략은 거르지 않음(정보용) — 계속 평가
            if prev.close <= 0 or c.high <= 0 or c.high <= c.low:
                day["bad_bar"] += 1; continue
            gain = c.close / prev.close - 1.0
            if gain <= H.GAIN_MIN:
                day["gain"] += 1; continue
            if c.close < H.NEAR_HIGH * c.high:
                day["near_high"] += 1; continue
            pv = [x.volume for x in h[-(H.VOL_WINDOW + 1):-1]]
            avg = sum(pv) / len(pv) if pv else 0.0
            if avg <= 0:
                day["zero_vol"] += 1; continue
            rvol = c.volume / avg
            if rvol <= H.RVOL_MIN:
                day["rvol"] += 1
                near_miss.append((rvol, d, s, gain))
                continue
            day["signal"] += 1
            signals.append((d, s, gain, rvol, c.dt))
            if stale:
                stale_pass.append((d, s, c.dt))
        drops.update(day)
        per_day.append((d, day))

    ndays = len(per_day)
    verdict = "OK (신호 0 — 장부 신호 0과 일치)" if not signals else f"BUG ({len(signals)}건 신호 — 장부 신호 0과 불일치)"
    L = ["# 2026-11-26 R4 hot_rvol_swing 진입 조건 재생 스캔", "",
         f"- 캐시 `{cache}`(읽기 전용), 유니버스 {len(uni)}종(ETF 제외), 스캔 {START}..{per_day[-1][0] if per_day else '–'} ({ndays}거래일).",
         f"- 조건(동결): gain>{H.GAIN_MIN:.0%}, 종가≥고가×{H.NEAR_HIGH}, RVOL>{H.RVOL_MIN}(직전 {H.VOL_WINDOW}일).",
         "- 주의: 최대 3포지션·보유 중 스킵은 무시(신호 0 판정에는 영향 없음; 신호가 있으면 상한).",
         f"- **판정: {verdict}**", "",
         "| 단계 | 누적 탈락/건수(종목·일) |", "|---|---:|"]
    for k in ["scanned", "short_hist", "bad_bar", "gain", "near_high", "zero_vol", "rvol", "signal", "stale_bar"]:
        L.append(f"| {k} | {drops.get(k, 0)} |")
    L += ["", f"stale_bar = 스캔일에 봉이 없는 종목·일(최신성 필터 부재 위험 노출). stale 상태로 조건 통과: {len(stale_pass)}건.", ""]
    if signals:
        L += ["## 신호 목록", "", "| 날짜 | 종목 | gain | RVOL | 봉 날짜 |", "|---|---|---:|---:|---|"]
        L += [f"| {d} | {s} | {g:+.1%} | {r:.2f} | {cd} |" for d, s, g, r, cd in signals]
        L.append("")
    near_miss.sort(reverse=True)
    L += ["## RVOL 근접 탈락 상위 10(gain·near-high 통과)", "", "| 날짜 | 종목 | gain | RVOL |", "|---|---|---:|---:|"]
    L += [f"| {d} | {s} | {g:+.1%} | {r:.2f} |" for r, d, s, g in near_miss[:10]]
    L.append("")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
