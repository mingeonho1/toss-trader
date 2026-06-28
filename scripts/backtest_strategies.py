#!/usr/bin/env python3
"""저회전 전략 게이트 검증 — 듀얼모멘텀 / 200일 레짐필터 vs 바이앤홀드.

로드맵 E4(저회전 신규전략) + E5(B&H 순증분 통계 유의성)를 한 번에 돌린다.
- 실 시드 $32.43(₩50,000), 실 비용(왕복 70bps).
- 워밍업(최대 lookback) 이후 공통 구간으로 모든 곡선을 정렬·리베이스해 공정 비교.
- 채택 게이트: (a) 비용반영 수익이 QQQ B&H 이상  또는  (b) Sharpe ≥ 0.9×B&H 이면서 MDD 개선,
  그리고 (c) 일별 초과수익(vs QQQ B&H)의 블록부트스트랩 95% CI 하한 > 0(진짜 엣지).
"""
from __future__ import annotations

import json
import logging
import math
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.backtest import run_backtest          # noqa: E402
from toss_trader.broker import PaperBroker             # noqa: E402
from toss_trader.client import TossClient              # noqa: E402
from toss_trader.costs import CostModel                # noqa: E402
from toss_trader.marketdata import TossMarketData      # noqa: E402
from toss_trader.models import Candle                  # noqa: E402
from toss_trader.strategy import (BuyAndHoldStrategy, DualMomentumStrategy,  # noqa: E402
                                  RegimeFilterStrategy)

logging.disable(logging.INFO)
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "_candle_cache"
REPORTS = ROOT / "reports"
DEPTH = 2500  # ~10년: 2018 Q4 급락·2020 COVID·2022 약세장·강세장 멀티레짐 포함

UNIVERSE = ["QQQ", "SPY", "EFA", "IWM", "GLD", "IEF", "BIL", "SCHD"]
RISK = ["QQQ", "SPY", "EFA", "IWM", "GLD"]


def load_candles(symbols, depth=DEPTH):
    CACHE.mkdir(parents=True, exist_ok=True)
    client = None
    panel = {}
    for sym in symbols:
        f = CACHE / f"{sym}_{depth}.json"
        if f.exists():
            rows = json.loads(f.read_text())
            panel[sym] = [Candle(sym, date.fromisoformat(r["d"]), r["o"], r["h"],
                                 r["l"], r["c"], r["v"]) for r in rows]
            continue
        client = client or TossClient()
        md = TossMarketData(client)
        cs = md.history(sym, depth)
        f.write_text(json.dumps([{"d": c.dt.isoformat(), "o": c.open, "h": c.high,
                                  "l": c.low, "c": c.close, "v": c.volume} for c in cs]))
        panel[sym] = cs
        print(f"  fetched {sym}: {len(cs)} ({cs[0].dt}~{cs[-1].dt})")
    return panel


def align(panel):
    start = max(c[0].dt for c in panel.values())
    return {s: [c for c in cs if c.dt >= start] for s, cs in panel.items()}


def hold_curve(panel, weights, seed, cost):
    """바이앤홀드 곡선: 첫날 weights로 매수 후 보유. {date: equity} 반환."""
    syms = list(weights)
    dates = sorted({c.dt for s in syms for c in panel[s]})
    px = {s: {c.dt: c.close for c in panel[s]} for s in syms}
    broker = PaperBroker(cash=seed, cost_model=cost)
    d0 = dates[0]
    for s in syms:
        if px[s].get(d0):
            broker.submit_market_order(s, "BUY", amount=seed * weights[s],
                                       ref_price=px[s][d0], dt=d0)
    curve = {}
    last = {s: None for s in syms}
    for d in dates:
        for s in syms:
            if px[s].get(d):
                last[s] = px[s][d]
        curve[d] = broker.equity({s: last[s] for s in syms if last[s]})
    return curve, broker


def strat_curve(panel, strategy, seed, cost, threshold=0.05):
    res = run_backtest(panel, strategy, start_cash=seed, cost_model=cost,
                       rebalance_threshold=threshold)
    return {d: e for d, e in res.equity_curve}, res.broker


def metrics_from(dates, eq):
    start, end = eq[0], eq[-1]
    total = end / start - 1.0 if start > 0 else 0.0
    days = max((dates[-1] - dates[0]).days, 1)
    years = max(days / 365.0, 1e-9)
    cagr = (end / start) ** (1 / years) - 1.0 if start > 0 and end > 0 else 0.0
    peak, mdd = -1e18, 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1] > 0]
    if len(rets) > 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
    else:
        sharpe = 0.0
    return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe}


def daily_rets(dates, curve, seed):
    eq = [curve[d] for d in dates]
    base = eq[0] or seed
    eq = [seed * (v / base) for v in eq]   # 리베이스
    return eq, [eq[i] / eq[i - 1] - 1.0 if eq[i - 1] > 0 else 0.0 for i in range(1, len(eq))]


def block_bootstrap(excess, block=21, n=4000, seed=7):
    T = len(excess)
    if T < block + 1:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    nb = max(1, T // block)
    means = []
    for _ in range(n):
        samp = []
        for _ in range(nb):
            st = rng.randint(0, T - block)
            samp.extend(excess[st:st + block])
        means.append(sum(samp) / len(samp))
    means.sort()
    return (sum(excess) / T, means[int(0.025 * n)], means[int(0.975 * n)])


def main() -> int:
    seed = 50_000.0 / 1541.6
    cost = CostModel()
    print(f"시드 ${seed:.2f} (₩50,000) | 왕복비용 {cost.roundtrip_bps:.0f}bps | 유니버스 {UNIVERSE}")
    panel = align(load_candles(UNIVERSE))
    all_dates = sorted({c.dt for cs in panel.values() for c in cs})
    print(f"데이터 {all_dates[0]}~{all_dates[-1]} ({len(all_dates)}봉)")

    # 후보 정의
    candidates = {
        "BH_QQQ (벤치)": ("bench", BuyAndHoldStrategy(["QQQ"])),
        "BH_동일비중위험 (벤치)": ("bench", BuyAndHoldStrategy(RISK)),
        "BH_60/40 (벤치)": ("bench", BuyAndHoldStrategy(["SPY", "IEF"], {"SPY": 0.6, "IEF": 0.4})),
        "DualMom 5자산→IEF top1": ("active", DualMomentumStrategy(RISK, "IEF", 252, 1, "month")),
        "DualMom 5자산→IEF top2": ("active", DualMomentumStrategy(RISK, "IEF", 252, 2, "month")),
        "DualMom QQQ/EFA→BIL(GEM)": ("active", DualMomentumStrategy(["QQQ", "EFA"], "BIL", 252, 1, "month")),
        "Regime QQQ>200d else IEF": ("active", RegimeFilterStrategy(BuyAndHoldStrategy(["QQQ"]), "QQQ", 200, "IEF", "month")),
        "Regime QQQ>200d else 현금": ("active", RegimeFilterStrategy(BuyAndHoldStrategy(["QQQ"]), "QQQ", 200, None, "month")),
        "Regime(SPY200)+DualMom": ("active", RegimeFilterStrategy(DualMomentumStrategy(RISK, "IEF", 252, 1, "month"), "SPY", 200, "IEF", "month")),
    }

    # 워밍업 정렬: 최대 lookback(252) 이후로 평가
    max_warm = max(s.warmup for _, s in candidates.values())
    eval_start = all_dates[max_warm + 5]
    eval_dates = [d for d in all_dates if d >= eval_start]
    print(f"평가창(워밍업 정렬): {eval_dates[0]}~{eval_dates[-1]} ({len(eval_dates)}봉, "
          f"{(eval_dates[-1]-eval_dates[0]).days/365.25:.1f}년)\n")

    # 위기 구간 (멀티레짐 방어력 비교)
    CRISES = {
        "2018Q4": (date(2018, 10, 1), date(2018, 12, 31)),
        "COVID20": (date(2020, 2, 1), date(2020, 4, 30)),
        "Bear22": (date(2022, 1, 1), date(2022, 12, 31)),
    }
    crisis_dates = {k: [d for d in eval_dates if a <= d <= b] for k, (a, b) in CRISES.items()}

    curves = {}
    brokers = {}
    for name, (_, strat) in candidates.items():
        if isinstance(strat, BuyAndHoldStrategy):
            c, b = hold_curve(panel, strat.weights, seed, cost)
        else:
            c, b = strat_curve(panel, strat, seed, cost)
        curves[name] = c
        brokers[name] = b

    # 벤치: QQQ B&H 일별수익(초과수익 기준)
    bench_eq, bench_r = daily_rets(eval_dates, curves["BH_QQQ (벤치)"], seed)
    bench_m = metrics_from(eval_dates, bench_eq)

    results = {"meta": {"seed": seed, "cost_bps": cost.roundtrip_bps,
                        "eval": [eval_dates[0].isoformat(), eval_dates[-1].isoformat()],
                        "universe": UNIVERSE}, "rows": []}

    print(f"{'전략':30} {'수익률':>8} {'CAGR':>7} {'MDD':>7} {'Sharpe':>7} "
          f"{'매매':>4} {'비용%':>6} {'초과(연)':>8} {'CI하한':>8} 게이트")
    print("-" * 104)
    crisis_table = {}
    for name, (kind, strat) in candidates.items():
        eq, r = daily_rets(eval_dates, curves[name], seed)
        m = metrics_from(eval_dates, eq)
        # 위기구간별 낙폭
        cr = {}
        for k, ds in crisis_dates.items():
            if len(ds) > 2:
                ceq, _ = daily_rets(ds, curves[name], seed)
                cr[k] = metrics_from(ds, ceq)["mdd"]
            else:
                cr[k] = 0.0
        crisis_table[name] = cr
        # in-window 매매/비용
        fills = [f for f in brokers[name].fills if f.dt >= eval_start]
        ntr = len(fills)
        cst = sum(f.cost for f in fills)
        # 초과수익 vs QQQ B&H
        excess = [a - b for a, b in zip(r, bench_r)]
        ex_mean, ci_lo, ci_hi = block_bootstrap(excess)
        ex_ann, ci_lo_ann = ex_mean * 252, ci_lo * 252
        gate = ""
        if kind == "active":
            beats_ret = m["total"] >= bench_m["total"]
            beats_risk = m["sharpe"] >= 0.9 * bench_m["sharpe"] and m["mdd"] > bench_m["mdd"]
            sig = ci_lo > 0
            gate = ("✅채택" if (beats_ret or beats_risk) and sig
                    else ("△리스크" if beats_risk else "❌"))
        print(f"{name:30} {m['total']*100:+7.1f}% {m['cagr']*100:+6.1f}% {m['mdd']*100:6.1f}% "
              f"{m['sharpe']:7.2f} {ntr:4d} {cst/seed*100:5.1f}% "
              f"{ex_ann*100:+7.1f}% {ci_lo_ann*100:+7.1f}% {gate}")
        results["rows"].append({"name": name, "kind": kind, **m, "trades": ntr,
                                "cost_pct": cst / seed, "crisis_mdd": cr,
                                "excess_ann": ex_ann, "ci_lo_ann": ci_lo_ann,
                                "ci_hi_ann": ci_hi * 252})

    print(f"\n=== 위기구간 낙폭(MDD) 비교 ===")
    print(f"{'전략':30} " + "".join(f"{k:>10}" for k in CRISES))
    for name in candidates:
        print(f"{name:30} " + "".join(f"{crisis_table[name][k]*100:9.1f}%" for k in CRISES))

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "strategy_results_2026-06-28.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2))
    print("\n게이트: ✅채택=수익 or 위험조정 우위 AND 초과수익 CI하한>0 | △리스크=위험조정만 우위 | ❌=미달")
    print(f"기준 QQQ B&H: 수익 {bench_m['total']*100:+.1f}% / Sharpe {bench_m['sharpe']:.2f} / MDD {bench_m['mdd']*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
