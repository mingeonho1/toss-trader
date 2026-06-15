"""브로커 인터페이스 — paper와 live가 동일 API. 전략은 어느 쪽인지 모른다.

PaperBroker는 백테스트와 포워드 페이퍼트레이딩 양쪽에서 쓴다(동일 코드 경로).
LiveBroker(Phase 5)는 TossClient.create_order로 같은 인터페이스를 구현한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from .costs import CostModel
from .models import Fill, Position


class Broker(ABC):
    @abstractmethod
    def submit_market_order(self, symbol: str, side: str, *,
                            quantity: float | None = None,
                            amount: float | None = None,
                            ref_price: float, dt: date) -> Fill | None: ...

    @abstractmethod
    def position(self, symbol: str) -> Position: ...

    @abstractmethod
    def equity(self, prices: dict[str, float]) -> float: ...


class PaperBroker(Broker):
    """현금/포지션을 직접 관리하는 시뮬레이션 브로커.

    체결가는 비용모델의 슬리피지를 반영하고, 부대비용(수수료+환전)은 현금에서 차감한다.
    미국 소수점 매수를 지원하기 위해 amount(USD) 또는 quantity 중 하나로 주문한다.
    """

    def __init__(self, cash: float, cost_model: CostModel | None = None) -> None:
        self.cash = float(cash)
        self.cost = cost_model or CostModel()
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []

    def position(self, symbol: str) -> Position:
        return self.positions.setdefault(symbol, Position(symbol))

    def equity(self, prices: dict[str, float]) -> float:
        total = self.cash
        for sym, pos in self.positions.items():
            if pos.quantity:
                total += pos.market_value(prices.get(sym, pos.avg_price))
        return total

    def submit_market_order(self, symbol: str, side: str, *,
                            quantity: float | None = None,
                            amount: float | None = None,
                            ref_price: float, dt: date) -> Fill | None:
        side = side.upper()
        fill_price = self.cost.fill_price(ref_price, side)
        if fill_price <= 0:
            return None

        if side == "BUY":
            if amount is not None:
                # amount(USD)에는 비용이 포함된다고 보고, 비용 제외분으로 수량 산정
                qty = self._qty_for_amount(amount, fill_price)
            else:
                qty = float(quantity or 0.0)
            if qty <= 0:
                return None
            notional = qty * fill_price
            cost = self.cost.trade_cost(notional)
            if notional + cost > self.cash + 1e-9:
                # 현금 부족 → 가능한 만큼으로 축소
                qty = self._qty_for_amount(self.cash, fill_price)
                if qty <= 0:
                    return None
                notional = qty * fill_price
                cost = self.cost.trade_cost(notional)
            pos = self.position(symbol)
            new_qty = pos.quantity + qty
            pos.avg_price = (pos.avg_price * pos.quantity + notional) / new_qty if new_qty else 0.0
            pos.quantity = new_qty
            self.cash -= notional + cost
            fill = Fill(symbol, "BUY", qty, fill_price, cost, dt)

        else:  # SELL
            pos = self.position(symbol)
            qty = float(quantity if quantity is not None else pos.quantity)
            qty = min(qty, pos.quantity)
            if qty <= 0:
                return None
            notional = qty * fill_price
            cost = self.cost.trade_cost(notional)
            realized = (fill_price - pos.avg_price) * qty
            pos.quantity -= qty
            if pos.quantity <= 1e-12:
                pos.quantity = 0.0
                pos.avg_price = 0.0
            self.cash += notional - cost
            fill = Fill(symbol, "SELL", qty, fill_price, cost, dt, realized_pnl=realized)

        self.fills.append(fill)
        return fill

    def _qty_for_amount(self, amount: float, fill_price: float) -> float:
        """비용을 감안해 amount(USD) 안에서 살 수 있는 수량(소수점 허용)."""
        # amount = qty*price + trade_cost(qty*price); trade_cost는 notional 비례 → 역산
        bps = (self.cost.commission_bps + self.cost.fx_spread_bps) * 1e-4
        notional = amount / (1.0 + bps)
        # 최소수수료가 있으면 약간 보수적으로(무시해도 소액에선 미미)
        return max(0.0, notional / fill_price)
