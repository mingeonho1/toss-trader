#!/usr/bin/env python3
"""실 데이터 백테스트 — 토스 OpenAPI 실 일봉으로 SMA 전략을 검증하고 리포트를 생성한다.

검증 두 갈래:
  A) 전략 단독 (run_backtest): 전략+비용만. 순수 전략 엣지.
  B) 전체 엔진 리플레이: 전략+리스크(손절/익절/트레일링/일일차단=동결)+비용. 실제 배포 시스템.
또한 파라미터 그리드 민감도 + 벤치마크(QQQ/동일비중 바이앤홀드)를 측정한다.

실 시드 = 매수가능 KRW ÷ 환율 (기본 50,000원 / 1541.6 ≈ $32.43).
캔들은 scratchpad에 캐시해 재실행 시 API를 다시 치지 않는다.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import metrics                       # noqa: E402
from toss_trader.backtest import run_backtest         # noqa: E402
from toss_trader.broker import PaperBroker            # noqa: E402
from toss_trader.client import TossClient             # noqa: E402
from toss_trader.costs import CostModel               # noqa: E402
from toss_trader.engine import TradingEngine          # noqa: E402
from toss_trader.marketdata import ReplayMarketData, TossMarketData  # noqa: E402
from toss_trader.metrics import Performance           # noqa: E402
from toss_trader.models import Candle                 # noqa: E402
from toss_trader.risk import RiskConfig, RiskManager  # noqa: E402
from toss_trader.strategy import SmaCrossStrategy     # noqa: E402

logging.disable(logging.INFO)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "_candle_cache"
REPORTS = ROOT / "reports"

UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "COST", "QQQ", "SCHD"]
DEPTH = 1250  # 영업일 (~5년)


class _SilentJournal:
    """백테스트용 무동작 저널(파일 I/O 없음)."""
    def write_run(self, report) -> None: ...
    def write_error(self, day, context, message) -> None: ...


def load_candles(symbols: list[str], depth: int) -> dict[str, list[Candle]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    client = None
    panel: dict[str, list[Candle]] = {}
    for sym in symbols:
        f = CACHE / f"{sym}_{depth}.json"
        if f.exists():
            rows = json.loads(f.read_text())
            panel[sym] = [Candle(sym, date.fromisoformat(r["d"]), r["o"], r["h"],
                                 r["l"], r["c"], r["v"]) for r in rows]
            continue
        if client is None:
            client = TossClient()
        md = TossMarketData(client)
        candles = md.history(sym, depth)
        f.write_text(json.dumps([{"d": c.dt.isoformat(), "o": c.open, "h": c.high,
                                  "l": c.low, "c": c.close, "v": c.volume}
                                 for c in candles]))
        panel[sym] = candles
        print(f"  fetched {sym}: {len(candles)} candles "
              f"({candles[0].dt} ~ {candles[-1].dt})")
    return panel


def align_panel(panel: dict[str, list[Candle]]) -> dict[str, list[Candle]]:
    """모든 종목이 거래된 공통 시작일 이후로 정렬(데이터 길이 불균형 방지)."""
    starts = [c[0].dt for c in panel.values() if c]
    common_start = max(starts)
    return {s: [c for c in cs if c.dt >= common_start] for s, cs in panel.items()}


def run_engine_replay(panel, strategy, risk_cfg, seed, cost, rebalance_threshold):
    data = ReplayMarketData(panel)
    broker = PaperBroker(cash=seed, cost_model=cost)
    risk = RiskManager(risk_cfg)
    engine = TradingEngine(data=data, broker=broker, strategy=strategy,
                           risk=risk, journal=_SilentJournal(), universe=list(panel),
                           rebalance_threshold=rebalance_threshold)
    dates = sorted({c.dt for cs in panel.values() for c in cs})
    by_sym = {s: {c.dt: c for c in cs} for s, cs in panel.items()}
    equity_curve: list[float] = []
    halts = 0
    for d in dates:
        data.set_cursor(d)
        rep = engine.run_once(d)
        halts += 1 if rep.halted else 0
        equity_curve.append(rep.equity_end or rep.equity_start)
    days = (dates[-1] - dates[0]).days if len(dates) >= 2 else 1
    perf = metrics.compute(equity_curve, broker.fills, days)
    return perf, broker, halts


def buy_and_hold(panel, symbols, seed, cost) -> Performance:
    """동일비중 바이앤홀드 벤치마크(시작일 진입, 종료일까지 보유)."""
    dates = sorted({c.dt for s in symbols for c in panel[s]})
    by_sym = {s: {c.dt: c.close for c in panel[s]} for s in symbols}
    broker = PaperBroker(cash=seed, cost_model=cost)
    start = dates[0]
    per = seed / len(symbols)
    for s in symbols:
        px = by_sym[s].get(start)
        if px:
            broker.submit_market_order(s, "BUY", amount=per, ref_price=px, dt=start)
    curve = []
    for d in dates:
        prices = {s: by_sym[s].get(d) for s in symbols if by_sym[s].get(d)}
        # 마지막 알려진 가격으로 채움
        for s in symbols:
            if s not in prices:
                prior = [by_sym[s][k] for k in by_sym[s] if k <= d]
                if prior:
                    prices[s] = prior[-1]
        curve.append(broker.equity(prices))
    days = (dates[-1] - dates[0]).days
    return metrics.compute(curve, broker.fills, days)


@dataclass
class Config:
    name: str
    fast: int
    slow: int
    max_positions: int
    rebalance_threshold: float


CONFIGS = [
    Config("baseline 20/60 x3 rb5%", 20, 60, 3, 0.05),
    Config("lowfreq 20/60 x3 rb20%", 20, 60, 3, 0.20),
    Config("slow 50/150 x2 rb20%", 50, 150, 2, 0.20),
    Config("fast 10/30 x3 rb5%", 10, 30, 3, 0.05),
    Config("concentrated 50/200 x1 rb25%", 50, 200, 1, 0.25),
    Config("midslow 30/100 x2 rb15%", 30, 100, 2, 0.15),
]

RISK = RiskConfig(stop_loss_pct=0.08, take_profit_pct=0.25, trailing_stop_pct=0.12,
                  daily_max_loss_pct=0.05, max_position_weight=0.5, min_trade_usd=5.0)


def perf_row(p: Performance, extra: dict | None = None) -> dict:
    d = {"total_return": p.total_return, "cagr": p.cagr, "mdd": p.max_drawdown,
         "sharpe": p.sharpe, "trades": p.num_trades, "win_rate": p.win_rate,
         "payoff": p.payoff, "expectancy": p.expectancy, "total_cost": p.total_cost,
         "end_equity": p.end_equity, "days": p.days}
    if extra:
        d.update(extra)
    return d


def main() -> int:
    seed_krw = 50_000.0
    fx = 1541.6
    seed = seed_krw / fx
    cost = CostModel()  # 실요율: 수수료 10bps + 환전 20bps + 슬리피지 5bps
    print(f"시드 ₩{seed_krw:,.0f} / {fx} = ${seed:.2f} | 왕복비용 {cost.roundtrip_bps:.0f}bps")
    print(f"유니버스 {len(UNIVERSE)}종목, 깊이 {DEPTH}봉 로딩...")
    panel = align_panel(load_candles(UNIVERSE, DEPTH))
    dates = sorted({c.dt for cs in panel.values() for c in cs})
    print(f"공통기간 {dates[0]} ~ {dates[-1]} ({len(dates)}봉, {(dates[-1]-dates[0]).days}일)\n")

    results: dict = {"meta": {"seed_usd": seed, "seed_krw": seed_krw, "fx": fx,
                              "universe": UNIVERSE, "period": [dates[0].isoformat(), dates[-1].isoformat()],
                              "n_bars": len(dates), "cost_roundtrip_bps": cost.roundtrip_bps,
                              "cost_model": {"commission_bps": cost.commission_bps,
                                             "fx_spread_bps": cost.fx_spread_bps,
                                             "slippage_bps": cost.slippage_bps}},
                     "configs": [], "benchmarks": {}, "cost_drag": {}}

    # 벤치마크
    bh_uni = buy_and_hold(panel, UNIVERSE, seed, cost)
    bh_qqq = buy_and_hold(panel, ["QQQ"], seed, cost)
    results["benchmarks"]["equal_weight_bh"] = perf_row(bh_uni)
    results["benchmarks"]["qqq_bh"] = perf_row(bh_qqq)
    print("=== 벤치마크 (바이앤홀드) ===")
    print(f"  동일비중   : {bh_uni.summary().splitlines()[0]}")
    print(f"  QQQ 단독   : {bh_qqq.summary().splitlines()[0]}")

    # 설정별 전체 엔진 리플레이
    print("\n=== 전체 엔진 리플레이 (전략+리스크+비용) ===")
    for cfg in CONFIGS:
        strat = SmaCrossStrategy(universe=list(panel), fast=cfg.fast, slow=cfg.slow,
                                 max_positions=cfg.max_positions)
        perf, broker, halts = run_engine_replay(panel, strat, RISK, seed, cost,
                                                cfg.rebalance_threshold)
        row = perf_row(perf, {"name": cfg.name, "halt_days": halts,
                              "cost_pct_of_seed": perf.total_cost / seed,
                              "params": {"fast": cfg.fast, "slow": cfg.slow,
                                         "max_positions": cfg.max_positions,
                                         "rebalance_threshold": cfg.rebalance_threshold}})
        results["configs"].append(row)
        print(f"  [{cfg.name}]")
        print(f"     수익률 {perf.total_return*100:+6.1f}% | CAGR {perf.cagr*100:+5.1f}% | "
              f"MDD {perf.max_drawdown*100:5.1f}% | Sharpe {perf.sharpe:5.2f} | "
              f"매매 {perf.num_trades:3d} | 승률 {perf.win_rate*100:4.0f}% | "
              f"비용 ${perf.total_cost:5.2f}({perf.total_cost/seed*100:4.1f}% of seed) | "
              f"차단 {halts}일 | 최종 ${perf.end_equity:.2f}")

    # 비용 드래그 (baseline 전략단독: 무비용 vs 비용반영)
    print("\n=== 비용 드래그 (baseline 전략단독 run_backtest) ===")
    strat = SmaCrossStrategy(universe=list(panel), fast=20, slow=60, max_positions=3)
    no_cost = CostModel(commission_bps=0, fx_spread_bps=0, slippage_bps=0)
    r_free = run_backtest(panel, strat, start_cash=seed, cost_model=no_cost,
                          rebalance_threshold=0.05)
    strat2 = SmaCrossStrategy(universe=list(panel), fast=20, slow=60, max_positions=3)
    r_cost = run_backtest(panel, strat2, start_cash=seed, cost_model=cost,
                          rebalance_threshold=0.05)
    drag = r_free.performance.total_return - r_cost.performance.total_return
    results["cost_drag"] = {"no_cost_return": r_free.performance.total_return,
                            "with_cost_return": r_cost.performance.total_return,
                            "drag_pp": drag,
                            "with_cost_trades": r_cost.performance.num_trades,
                            "with_cost_total_cost": r_cost.performance.total_cost}
    print(f"  무비용 {r_free.performance.total_return*100:+.1f}% → "
          f"비용반영 {r_cost.performance.total_return*100:+.1f}% "
          f"(드래그 {drag*100:.1f}%p, 매매 {r_cost.performance.num_trades}회, "
          f"총비용 ${r_cost.performance.total_cost:.2f})")

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / "backtest_results_2026-06-28.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n결과 JSON 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
