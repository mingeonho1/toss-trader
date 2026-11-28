#!/usr/bin/env python3
"""R4 진단 2026-11-28 — hot_rvol_swing 0거래(44세션)가 정상(필터 미충족)인가 버그인가.

읽기 전용: 라이브 캐시(toss-trader/data/_hist_cache)·장부(toss-trader/data/paperlab)는 읽기만 한다.
1) 포워드 창(2026-09-28..2026-11-27) 매 세션마다 decide() 조건을 심볼별로 재생 → 조건별 탈락 집계.
   decide() 는 h[-1] 의 날짜를 검사하지 않으므로, '오늘 봉이 없는(정체) 심볼'도 별도 집계한다.
2) 같은 규칙으로 포워드 직전 1439세션(=backtest_expectations 창) 신호 빈도 → 44세션 0거래 확률.
   (a) 전체 캐시 유니버스, (b) 포워드에서 실제로 갱신된 심볼만(유효 유니버스) 두 가지.
3) PaperLab 으로 hot_rvol_swing 을 현재 캐시로 재생(메모리) → 장부와 대조.
4) 합성 주입 테스트: 정체 심볼의 마지막 봉이 조건을 만족한다고 가정 → decide()/엔진 동작(잠복 버그 실증).

재현: .venv/bin/python experiments/2026-11-28_hot_rvol_scan.py [--data DIR]
"""
from __future__ import annotations

import argparse
import bisect
import dataclasses
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import histdata  # noqa: E402
from toss_trader.paperlab import _LEVERAGED_ETP, PaperLab  # noqa: E402
from toss_trader.paperlab_strategies import hot_rvol_swing as H  # noqa: E402
import paperlab_run as PR  # noqa: E402

DEFAULT_DATA = Path("/Users/mingh/github/toss-trader/data")
START = date(2026, 9, 28)
END = date(2026, 11, 27)
BT_SESSIONS = 1439
FWD_SESSIONS = 44
W = H.VOL_WINDOW + 2
STAGES = ["no_hist", "short_hist", "bad_bar", "gain<=5%", "not_near_high", "rvol<=3", "PASS"]
# 단일종목이 아닌데 ETF_LIKE 에 없어 스윙 유니버스로 새는 심볼(수동 목록; 보고용)
NON_STOCK_HINT = {"BSV", "PSQ", "SPXL", "TECL", "TECS", "SGOV", "BOXX", "SH", "SCZ", "TIP", "UUP", "VWO",
                  "VIXM", "VIXY", "SVIX", "DBB", "CURE", "DRN", "FAS", "HIBL", "LABU", "TNA", "CONL",
                  "MSTU", "BITX"}


def classify(h, d):
    """decide() 조건 순서 그대로 첫 탈락 단계 + (h[-1] 이 오늘 봉인지) + 지표."""
    if not h:
        return "no_hist", False, None
    fresh = h[-1].dt == d
    if len(h) < W:
        return "short_hist", fresh, None
    c, prev = h[-1], h[-2]
    if prev.close <= 0 or c.high <= 0 or c.high <= c.low:
        return "bad_bar", fresh, None
    g = c.close / prev.close - 1.0
    pv = [x.volume for x in h[-(H.VOL_WINDOW + 1):-1]]
    avg = sum(pv) / len(pv)
    rv = c.volume / avg if avg > 0 else 0.0
    m = (g, c.close / c.high, rv)
    if g <= H.GAIN_MIN:
        return "gain<=5%", fresh, m
    if c.close < H.NEAR_HIGH * c.high:
        return "not_near_high", fresh, m
    if avg <= 0 or rv <= H.RVOL_MIN:
        return "rvol<=3", fresh, m
    return "PASS", fresh, m


def scan(uni, bars, dts, dates):
    out = []
    for d in dates:
        cnt, cnt_f, passes, near = Counter(), Counter(), [], []
        stale = 0
        for s in uni:
            i = bisect.bisect_right(dts[s], d)
            h = bars[s][max(0, i - W):i]
            st, fresh, m = classify(h, d)
            cnt[st] += 1
            if h and not fresh:
                stale += 1
            if fresh:
                cnt_f[st] += 1
                if m and m[0] > H.GAIN_MIN:
                    near.append((s, st, m))
            if st == "PASS":
                passes.append((s, fresh, h[-1].dt.isoformat()))
        out.append({"d": d, "cnt": cnt, "fresh": cnt_f, "stale": stale, "passes": passes, "near": near})
    return out


def simulate_entries(days):
    """decide() 의 보유/슬롯 규칙(hold=5, max=3)으로 '신선한' 신호만 진입 수 계산."""
    holds: dict[str, int] = {}
    entries = 0
    for r in days:
        for s in list(holds):
            holds[s] += 1
            if holds[s] >= H.HOLD_DAYS:
                del holds[s]
        slots = H.MAX_POS - len(holds)
        c = [s for s, fresh, _ in r["passes"] if fresh and s not in holds]
        for s in c[:max(slots, 0)]:
            holds[s] = 0
            entries += 1
    return entries


def bt_stats(label, uni, bars, dts, pre):
    pdays = scan(uni, bars, dts, pre)
    flags = [1 if any(f for _, f, _ in r["passes"]) else 0 for r in pdays]
    entries = simulate_entries(pdays)
    lam = entries / len(pdays) * FWD_SESSIONS
    wins = [sum(flags[i:i + FWD_SESSIONS]) for i in range(0, len(flags) - FWD_SESSIONS + 1)]
    non = [sum(flags[i:i + FWD_SESSIONS]) for i in range(0, len(flags) - FWD_SESSIONS + 1, FWD_SESSIONS)]
    tot_p = Counter()
    for r in pdays:
        tot_p.update(r["fresh"])
    by_year = Counter(r["d"].year for r, f in zip(pdays, flags) if f)
    cov = sorted(sum(r["fresh"].values()) for r in pdays)
    print(f"\n[backtest {label}: {pre[0]}..{pre[-1]}, {len(pdays)} sessions, universe={len(uni)}]")
    print("  fresh first-failing:", {k: tot_p[k] for k in STAGES})
    print(f"  signal days={sum(flags)} ({sum(flags) / len(pdays):.2%}) total PASS="
          f"{sum(sum(1 for _, f, _ in r['passes'] if f) for r in pdays)} entries={entries} "
          f"stale-PASS days={sum(1 for r in pdays if any(not f for _, f, _ in r['passes']))}")
    print(f"  expected entries per {FWD_SESSIONS} sessions = {lam:.2f}; Poisson P(0)={math.exp(-lam):.2e}")
    print(f"  rolling {FWD_SESSIONS}-session windows with 0 signal days: {sum(1 for w in wins if w == 0)}/{len(wins)} "
          f"({sum(1 for w in wins if w == 0) / len(wins):.1%}); non-overlapping: "
          f"{sum(1 for w in non if w == 0)}/{len(non)}; min signal days in any window={min(wins)}")
    print("  signal days by year:", dict(sorted(by_year.items())))
    print(f"  fresh-evaluated symbols per session: min={cov[0]} median={cov[len(cov) // 2]} max={cov[-1]}")


def injection_test(strat, master, by_date, stale_sym, fresh_sym, fresh_day):
    """정체 심볼의 마지막 봉을 '조건 충족'으로 바꾸고 + 신선 심볼에 진짜 신호 1개 주입 → 엔진 재생."""
    bd = {s: dict(v) for s, v in by_date.items()}
    for sym, d_target in ((stale_sym, None), (fresh_sym, fresh_day)):
        days = sorted(bd[sym])
        d = d_target or days[-1]
        i = days.index(d)
        prev = bd[sym][days[i - 1]]
        avg = sum(bd[sym][x].volume for x in days[i - 20:i]) / 20
        px = prev.close * 1.08
        bd[sym][d] = dataclasses.replace(bd[sym][d], open=prev.close * 1.01, high=px, low=prev.close,
                                         close=px, volume=avg * 5)
    lab = PaperLab(strat)
    st = lab.run(lab.fresh_state(START), master, bd)
    fills = st["unit"]["fills"]
    print(f"\n[injection] stale {stale_sym}@{max(bd[stale_sym])} forced PASS + genuine {fresh_sym}@{fresh_day} PASS")
    print(f"  fills={len(fills)} {[(f['dt'], f['symbol'], f['side']) for f in fills][:6]}")
    print(f"  final pending={st['pending']} holds={st['strategy_state'].get('holds')}")
    # 대조: 정체 심볼 주입 없이 신선 신호만
    bd2 = {s: dict(v) for s, v in by_date.items()}
    bd2[fresh_sym] = bd[fresh_sym]
    lab2 = PaperLab(strat)
    st2 = lab2.run(lab2.fresh_state(START), master, bd2)
    f2 = st2["unit"]["fills"]
    print(f"  control (fresh signal only): fills={len(f2)} {[(f['dt'], f['symbol'], f['side']) for f in f2]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    args = ap.parse_args()
    cache = args.data / "_hist_cache"
    histdata.CACHE_DIR = cache
    PR.HIST_CACHE = cache

    strat = H.HotRvolSwing(PR._cached_universe())
    uni = strat.universe()
    panel = PR._load_panel(sorted(set(uni) | {"QQQ"}), offline=True)
    by_date = PR._by_date(panel)
    master = PR._master_dates(panel)
    mset = set(master)
    # 엔진의 visible 은 master(QQQ) 날짜의 봉만 담는다 → 동일하게 맞춤.
    bars = {s: sorted((c for c in by_date.get(s, {}).values() if c.dt in mset), key=lambda c: c.dt) for s in uni}
    dts = {s: [c.dt for c in bars[s]] for s in uni}
    fwd = [d for d in master if START <= d <= END]
    pre = [d for d in master if d < START][-BT_SESSIONS:]

    last_bar = {s: dts[s][-1] for s in uni if dts[s]}
    missing = [s for s in uni if s not in last_bar]
    fresh_end = sorted(s for s, lb in last_bar.items() if lb >= END)
    lb_hist = Counter(lb.isoformat() for lb in last_bar.values())
    print(f"universe={len(uni)} loaded={len(last_bar)} missing={len(missing)} "
          f"fwd_sessions={len(fwd)} ({fwd[0]}..{fwd[-1]}) pre_sessions={len(pre)} ({pre[0]}..{pre[-1]})")
    print(f"symbols with bar on {END}: {len(fresh_end)} -> {fresh_end}")
    print("last-bar date histogram:", lb_hist.most_common(10))
    leak = sorted(s for s in uni if s in _LEVERAGED_ETP or s in NON_STOCK_HINT)
    print(f"non-single-stock symbols leaking into swing universe ({len(leak)}): {leak}")
    print(f"  of which in effective (fresh) universe: {sorted(set(leak) & set(fresh_end))}")

    days = scan(uni, bars, dts, fwd)
    tot, tot_f = Counter(), Counter()
    for r in days:
        tot.update(r["cnt"])
        tot_f.update(r["fresh"])
    print("\n[forward] symbol-session first-failing condition (all = as decide() sees; fresh = bar dated today):")
    for k in STAGES:
        print(f"  {k:14s} all={tot[k]:6d}  fresh_only={tot_f[k]:6d}")
    print(f"  mean stale symbols per session = {sum(r['stale'] for r in days) / len(days):.1f} / {len(uni)}")
    print("\n[forward] per-session: date fresh_evaluated stale PASS(fresh) PASS(stale)")
    stale_pass_days = 0
    for r in days:
        nf = sum(r["fresh"].values())
        pf = [s for s, f, _ in r["passes"] if f]
        ps = [f"{s}@{dt}" for s, f, dt in r["passes"] if not f]
        stale_pass_days += bool(ps)
        print(f"  {r['d']} {nf:4d} {r['stale']:4d} {pf} {ps}")
    print(f"forward fresh-signal entries (hold/slot rules) = {simulate_entries(days)}; "
          f"days with stale PASS = {stale_pass_days}")
    print("\n[forward] near-misses (fresh bar, gain>5%): date sym stage gain close/high rvol")
    for r in days:
        for s, st, (g, ch, rv) in r["near"]:
            print(f"  {r['d']} {s:6s} {st:14s} {g:+.3f} {ch:.4f} {rv:.2f}")

    bt_stats("FULL cached universe", uni, bars, dts, pre)
    bt_stats("EFFECTIVE forward universe (refreshed symbols only)", fresh_end, bars, dts, pre)
    stocks_eff = [s for s in fresh_end if s not in leak]
    bt_stats("EFFECTIVE single stocks only", stocks_eff, bars, dts, pre)

    lab = PaperLab(strat)
    st = lab.run(lab.fresh_state(START), master, by_date)
    print(f"\n[engine replay] last_date={st['last_date']} sessions={len(st['equity'])} "
          f"fills={len(st['unit']['fills'])} pending={st['pending']} holds={st['strategy_state'].get('holds')}")
    p = args.data / "paperlab" / "hot_rvol_swing" / "state.json"
    if p.exists():
        a = json.loads(p.read_text(encoding="utf-8"))
        print(f"[ledger] last_date={a.get('last_date')} sessions={len(a.get('equity') or [])} "
              f"fills={len(a['unit']['fills'])} pending={a.get('pending')} "
              f"holds={(a.get('strategy_state') or {}).get('holds')}")

    stale_sym = next(s for s in uni if last_bar.get(s) and last_bar[s] < START and s not in leak)
    injection_test(strat, master, by_date, stale_sym, "AAPL", date(2026, 10, 15))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
