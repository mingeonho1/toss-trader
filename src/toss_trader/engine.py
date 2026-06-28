"""무인 매매 엔진 — 하루 1회(스윙) 실행 단위.

run_once 흐름:
  1) 시세 수집 → 당일 시작자본 기록(start_day)
  2) 트레일링 고점 갱신 → 보호적 청산(손절/익절/트레일링) 강제 매도
  3) 일일 최대손실 차단 체크
  4) 전략 목표비중 → 리스크 조정(캡/조각거래 금지/차단 반영)
  5) 리밸런싱(시장가, 소수점 amount 매수) → 모든 행위를 일지/로그에 기록
각 단계는 예외를 잡아 기록하고 가능한 한 계속한다(한 종목 실패가 전체를 멈추지 않게).
"""
from __future__ import annotations

from datetime import date

from .broker import Broker
from .journal import DailyReport, JournalWriter
from .marketdata import MarketDataSource
from .models import Fill
from .risk import RiskManager
from .strategy.base import Strategy, StrategyContext


class TradingEngine:
    def __init__(self, *, data: MarketDataSource, broker: Broker, strategy: Strategy,
                 risk: RiskManager, journal: JournalWriter, universe: list[str],
                 rebalance_threshold: float = 0.05) -> None:
        self.data = data
        self.broker = broker
        self.strategy = strategy
        self.risk = risk
        self.journal = journal
        self.universe = universe
        self.rebalance_threshold = rebalance_threshold

    def run_once(self, today: date) -> DailyReport:
        report = DailyReport(day=today.isoformat())
        fills: list[Fill] = []
        try:
            prices = self._collect_prices()
            equity0 = self.broker.equity(prices)
            report.equity_start = equity0
            self.risk.start_day(equity0)
            positions = getattr(self.broker, "positions", {})
            self.risk.observe(positions, prices)

            # 2) 보호적 청산
            exits = self.risk.evaluate_exits(positions, prices)
            exited: set[str] = set()
            for sym, reason in exits:
                px = prices.get(sym)
                if px is None:
                    continue
                fl = self.broker.submit_market_order(
                    sym, "SELL", quantity=positions[sym].quantity, ref_price=px, dt=today)
                if fl:
                    fills.append(fl)
                    exited.add(sym)
            report.exits = exits

            # 3) 일일 손실 차단
            equity_now = self.broker.equity(prices)
            self.risk.check_daily_halt(equity_now)
            report.halted = self.risk.halted
            report.halt_reason = self.risk.halt_reason

            # 4) 전략 → 리스크 조정
            ctx = StrategyContext(today=today, history={
                s: self.data.history(s, self.strategy.warmup + 5) for s in self.universe
            })
            raw_weights = self.strategy.target_weights(ctx)
            weights = self.risk.adjust_weights(raw_weights, equity_now, exclude=exited)
            report.weights = weights

            # 5) 리밸런싱 — 일일손실 차단(회로차단기)이 걸리면 거래를 동결한다.
            #    차단은 '신규/추가 진입 중단'이지 '보유 투매'가 아니다. halt 시 adjust_weights가
            #    빈 dict를 주는데, 이를 _rebalance에 넘기면 '전 종목 목표 0%'로 해석돼 보유 전량을
            #    최악의 타이밍(당일 급락)에 시장가 청산해버린다 → 동결로 막는다.
            #    보호청산(손절/익절/트레일링)은 위 2)단계에서 이미 처리됐다.
            if not self.risk.halted:
                fills += self._rebalance(weights, prices, today, exited)

            report.equity_end = self.broker.equity(self._collect_prices())
        except Exception as e:  # noqa: BLE001 — 무인 운용: 죽지 않고 기록
            msg = f"{type(e).__name__}: {e}"
            report.errors.append(msg)
            self.journal.write_error(report.day, "run_once", msg)
            if report.equity_end == 0.0:
                report.equity_end = report.equity_start

        # 다음 날 일일손실 차단의 기준선 갱신(전일 종가 자본)
        self.risk.end_day(report.equity_end or report.equity_start)
        report.fills = [self._fill_dict(f) for f in fills]
        self.journal.write_run(report)
        return report

    # --- helpers ---
    def _collect_prices(self) -> dict[str, float]:
        held = [s for s, p in getattr(self.broker, "positions", {}).items() if p.quantity > 0]
        prices: dict[str, float] = {}
        for sym in set(self.universe) | set(held):
            try:
                px = self.data.price(sym)
            except Exception as e:  # noqa: BLE001
                self.journal.write_error(date.today().isoformat(), f"price:{sym}", str(e))
                px = None
            if px is not None and px > 0:
                prices[sym] = px
        return prices

    def _rebalance(self, weights: dict[str, float], prices: dict[str, float],
                   today: date, exclude: set[str]) -> list[Fill]:
        fills: list[Fill] = []
        positions = getattr(self.broker, "positions", {})
        equity = self.broker.equity(prices)
        if equity <= 0:
            return fills
        targets = {s: weights.get(s, 0.0) * equity for s in prices}
        for sym, pos in positions.items():
            if pos.quantity > 0 and sym not in targets:
                targets[sym] = 0.0

        # 매도 먼저 (차단 시에도 비중 0 종목은 정리)
        for sym, tv in targets.items():
            if sym in exclude:
                continue
            px = prices.get(sym)
            if px is None:
                continue
            diff = tv - self.broker.position(sym).market_value(px)
            if diff < -self.rebalance_threshold * equity:
                qty = min(self.broker.position(sym).quantity, (-diff) / px)
                if qty > 0:
                    fl = self.broker.submit_market_order(sym, "SELL", quantity=qty,
                                                         ref_price=px, dt=today)
                    if fl:
                        fills.append(fl)
        # 매수
        for sym, tv in targets.items():
            if sym in exclude:
                continue
            px = prices.get(sym)
            if px is None:
                continue
            diff = tv - self.broker.position(sym).market_value(px)
            if diff > self.rebalance_threshold * equity:
                amount = min(diff, self.broker.cash)
                if amount > 0:
                    fl = self.broker.submit_market_order(sym, "BUY", amount=amount,
                                                         ref_price=px, dt=today)
                    if fl:
                        fills.append(fl)
        return fills

    @staticmethod
    def _fill_dict(f: Fill) -> dict:
        return {"symbol": f.symbol, "side": f.side, "quantity": f.quantity,
                "price": f.price, "cost": f.cost, "realized_pnl": f.realized_pnl}
