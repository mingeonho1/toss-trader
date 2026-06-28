"""백테스터 — 전략의 목표비중을 PaperBroker로 리밸런싱하고 성과를 측정한다.

lookahead 방지: 각 시점 ctx에는 '당일 종가까지'만 넣고, 체결도 당일 종가 기준(슬리피지 반영).
실전과의 차이(당일 종가 체결 가정 등)는 보수적 비용/슬리피지로 일부 상쇄한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import metrics
from .broker import PaperBroker
from .costs import CostModel
from .metrics import Performance
from .models import Candle
from .strategy.base import Strategy, StrategyContext


@dataclass
class DcaResult:
    deposited: float          # 누적 입금액 (USD)
    final_value: float        # 최종 평가액 (USD)
    profit: float             # final - deposited
    profit_pct: float         # final/deposited - 1 (단순 수익률)
    max_drawdown: float
    total_cost: float
    n_buys: int
    days: int
    equity_curve: list[tuple[date, float]]

    def summary(self) -> str:
        return (f"입금누계 ${self.deposited:,.2f} → 평가액 ${self.final_value:,.2f} "
                f"(순익 ${self.profit:+,.2f}, {self.profit_pct*100:+.1f}%) | "
                f"MDD {self.max_drawdown*100:.1f}% | 매수 {self.n_buys}회 | "
                f"총비용 ${self.total_cost:.2f}")


def run_dca(
    panel: dict[str, list[Candle]],
    weights: dict[str, float],
    *,
    monthly_usd: float,
    cost_model: CostModel | None = None,
    initial_usd: float = 0.0,
) -> DcaResult:
    """적립식(DCA) + 분산 바이앤홀드 백테스트.

    매월 첫 거래일에 monthly_usd를 입금하고 target weights의 '미달분(underweight)'을
    신규 현금으로 매수만 한다(매도 없음 → 저회전·세금/회전비용 최소, 리밸런싱 프리미엄은
    적립 매수로 자연 확보). target weights 합은 1.0 권장.
    """
    cost_model = cost_model or CostModel()
    broker = PaperBroker(cash=initial_usd, cost_model=cost_model)
    syms = list(weights)
    by_sym = {s: {c.dt: c.close for c in panel[s]} for s in syms}
    dates = sorted({c.dt for s in syms for c in panel[s]})
    last_px = {s: None for s in syms}
    deposited = initial_usd
    last_month = None
    equity_curve: list[tuple[date, float]] = []

    for d in dates:
        for s in syms:
            if by_sym[s].get(d):
                last_px[s] = by_sym[s][d]
        prices = {s: last_px[s] for s in syms if last_px[s]}
        if (d.year, d.month) != last_month and prices:
            last_month = (d.year, d.month)
            broker.cash += monthly_usd
            deposited += monthly_usd
            equity = broker.equity(prices)
            # 미달분이 큰 종목부터 신규 현금으로 매수(매도 없음)
            order = sorted(syms, key=lambda s: (weights[s] * equity)
                           - broker.position(s).market_value(prices.get(s, 0.0)),
                           reverse=True)
            for s in order:
                px = prices.get(s)
                if not px or broker.cash <= 0:
                    continue
                need = weights[s] * equity - broker.position(s).market_value(px)
                if need > 0:
                    broker.submit_market_order(s, "BUY", amount=min(need, broker.cash),
                                               ref_price=px, dt=d)
        equity_curve.append((d, broker.equity(prices)))

    eq = [e for _, e in equity_curve]
    peak, mdd = -1e18, 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    final = eq[-1] if eq else 0.0
    days = (dates[-1] - dates[0]).days if len(dates) >= 2 else 1
    return DcaResult(
        deposited=deposited, final_value=final, profit=final - deposited,
        profit_pct=(final / deposited - 1.0) if deposited > 0 else 0.0,
        max_drawdown=mdd, total_cost=sum(f.cost for f in broker.fills),
        n_buys=len(broker.fills), days=days, equity_curve=equity_curve,
    )


@dataclass
class BacktestResult:
    performance: Performance
    equity_curve: list[tuple[date, float]]
    broker: PaperBroker


def _all_dates(panel: dict[str, list[Candle]]) -> list[date]:
    ds: set[date] = set()
    for candles in panel.values():
        ds.update(c.dt for c in candles)
    return sorted(ds)


def run_backtest(
    panel: dict[str, list[Candle]],
    strategy: Strategy,
    *,
    start_cash: float,
    cost_model: CostModel | None = None,
    rebalance_threshold: float = 0.02,   # 목표와의 괴리가 이 비율 미만이면 매매 생략(비용 절약)
) -> BacktestResult:
    cost_model = cost_model or CostModel()
    broker = PaperBroker(cash=start_cash, cost_model=cost_model)
    # symbol -> {date: candle} 인덱스
    by_sym = {sym: {c.dt: c for c in candles} for sym, candles in panel.items()}
    dates = _all_dates(panel)

    equity_curve: list[tuple[date, float]] = []

    for i, today in enumerate(dates):
        # 당일 종가까지의 히스토리 구성 (lookahead 방지)
        history = {
            sym: [c for c in panel[sym] if c.dt <= today]
            for sym in panel
        }
        prices = {sym: by_sym[sym][today].close for sym in panel if today in by_sym[sym]}
        if not prices:
            continue

        if i >= strategy.warmup:
            ctx = StrategyContext(today=today, history=history)
            weights = strategy.target_weights(ctx)
            _rebalance(broker, weights, prices, today, rebalance_threshold)

        equity_curve.append((today, broker.equity(prices)))

    days = (dates[-1] - dates[0]).days if len(dates) >= 2 else 1
    perf = metrics.compute([e for _, e in equity_curve], broker.fills, days)
    return BacktestResult(performance=perf, equity_curve=equity_curve, broker=broker)


def _rebalance(broker: PaperBroker, weights: dict[str, float],
               prices: dict[str, float], today: date, threshold: float) -> None:
    equity = broker.equity(prices)
    if equity <= 0:
        return
    # 목표 가치 (가격 있는 종목만 대상)
    targets = {sym: weights.get(sym, 0.0) * equity for sym in prices}
    # 목표비중 0 또는 빠진 보유 종목도 청산 대상에 포함
    for sym, pos in broker.positions.items():
        if pos.quantity > 0 and sym not in targets:
            targets[sym] = 0.0

    # 1) 매도(현금 확보) 먼저
    for sym, target_val in targets.items():
        price = prices.get(sym)
        if price is None:
            continue
        cur_val = broker.position(sym).market_value(price)
        diff = target_val - cur_val
        if diff < -threshold * equity:
            qty = min(broker.position(sym).quantity, (-diff) / price)
            if qty > 0:
                broker.submit_market_order(sym, "SELL", quantity=qty,
                                           ref_price=price, dt=today)
    # 2) 매수 (소수점: amount 단위)
    for sym, target_val in targets.items():
        price = prices.get(sym)
        if price is None:
            continue
        cur_val = broker.position(sym).market_value(price)
        diff = target_val - cur_val
        if diff > threshold * equity:
            amount = min(diff, broker.cash)
            if amount > 0:
                broker.submit_market_order(sym, "BUY", amount=amount,
                                           ref_price=price, dt=today)
