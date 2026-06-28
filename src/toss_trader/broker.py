"""브로커 인터페이스 — paper와 live가 동일 API. 전략은 어느 쪽인지 모른다.

PaperBroker는 백테스트와 포워드 페이퍼트레이딩 양쪽에서 쓴다(동일 코드 경로).
LiveBroker는 TossClient로 실주문을 내고 체결을 폴링해 같은 인터페이스를 구현한다.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import date

from .costs import CostModel
from .models import Fill, Position

logger = logging.getLogger("toss_trader.broker")

# 주문 상태 중 더 이상 체결이 진행되지 않는 종료 상태들.
_TERMINAL_STATUS = {"FILLED", "CANCELED", "REJECTED", "REPLACED",
                    "CANCEL_REJECTED", "REPLACE_REJECTED"}


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


class LiveBroker(Broker):
    """토스 OpenAPI 실주문 브로커. PaperBroker와 동일 인터페이스(전략은 구분 못 함).

    동작:
    - BUY는 미국 금액주문(orderAmount, MARKET, 정규장 전용)으로 소수점 매수,
      SELL은 수량주문(quantity, MARKET — US는 소수점 매도 허용).
    - 주문 후 get_order로 종료/체결까지 폴링해 실제 체결(평단·수량·수수료)로 Fill 생성.
    - positions/cash는 holdings·buying-power(USD) 실응답으로 동기화(엔진과 drop-in 호환).

    안전장치: require_live=True면 TRADING_MODE=live가 아닐 때 생성을 거부한다
    (PLAN §4 검증 게이트 통과 후에만 live 전환). 미국주식만 취급한다.
    """

    def __init__(self, client, *, cost_model: CostModel | None = None,
                 poll_interval: float = 1.0, poll_timeout: float = 30.0,
                 require_live: bool = True) -> None:  # client: TossClient
        self.client = client
        if require_live and not client.s.is_live:
            raise RuntimeError(
                "LiveBroker는 실주문을 냅니다. TRADING_MODE=live에서만 사용하세요 "
                "(검증 전에는 PaperBroker). 테스트는 require_live=False.")
        client.s.require_account()
        self.cost = cost_model or CostModel()
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.positions: dict[str, Position] = {}
        self.cash: float = 0.0
        self.fills: list[Fill] = []
        self.sync()

    # --- 계좌 상태 동기화 (실응답 → 내부 캐시) ---
    def sync(self) -> None:
        bp = self.client.get_buying_power("USD")
        self.cash = _f(bp.get("cashBuyingPower")) if isinstance(bp, dict) else 0.0
        hold = self.client.get_holdings()
        items = hold.get("items", []) if isinstance(hold, dict) else (hold or [])
        positions: dict[str, Position] = {}
        for it in items:
            if str(it.get("marketCountry", "")).upper() != "US":
                continue  # 이 봇은 미국주식만 운용
            sym = it.get("symbol")
            qty = _f(it.get("quantity"))
            if sym and qty > 0:
                positions[sym] = Position(sym, qty, _f(it.get("averagePurchasePrice")))
        self.positions = positions

    def position(self, symbol: str) -> Position:
        return self.positions.get(symbol, Position(symbol))

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
        try:
            if side == "BUY":
                if amount is not None:
                    # US 금액주문은 정규장 전용. 소수점 둘째 자리로 정규화.
                    resp = self.client.create_order(
                        symbol, "BUY", order_type="MARKET",
                        order_amount=f"{max(0.0, amount):.2f}")
                elif quantity is not None and quantity > 0:
                    # 수량매수는 US 정수만 허용.
                    resp = self.client.create_order(
                        symbol, "BUY", order_type="MARKET", quantity=int(quantity))
                else:
                    return None
            else:  # SELL
                qty = quantity if quantity is not None else self.position(symbol).quantity
                if not qty or qty <= 0:
                    return None
                resp = self.client.create_order(
                    symbol, "SELL", order_type="MARKET", quantity=qty)
        except Exception as e:  # noqa: BLE001 — 주문 거절/오류는 상위(엔진)가 기록
            logger.warning("주문 실패 %s %s: %s", side, symbol, e)
            raise

        order_id = resp.get("orderId") if isinstance(resp, dict) else None
        if not order_id:
            return None
        order = self._await_fill(order_id)
        fill = self._fill_from_order(symbol, side, order, dt)
        self.sync()  # 체결 후 잔고/포지션 재동기화
        if fill:
            self.fills.append(fill)
        return fill

    # --- 체결 폴링 / 변환 ---
    def _await_fill(self, order_id: str) -> dict:
        """get_order를 폴링해 종료상태 또는 타임아웃까지 대기. 마지막 스냅샷 반환."""
        deadline = time.monotonic() + self.poll_timeout
        last: dict = {}
        while True:
            o = self.client.get_order(order_id)
            if isinstance(o, dict):
                last = o
                if str(o.get("status", "")).upper() in _TERMINAL_STATUS:
                    return o
            if time.monotonic() >= deadline:
                logger.warning("주문 %s 체결 폴링 타임아웃(status=%s)",
                               order_id, last.get("status"))
                return last
            time.sleep(self.poll_interval)

    def _fill_from_order(self, symbol: str, side: str, order: dict,
                         dt: date) -> Fill | None:
        exe = (order or {}).get("execution", {}) or {}
        qty = _f(exe.get("filledQuantity"))
        if qty <= 0:
            return None
        price = _f(exe.get("averageFilledPrice"))
        # 실 체결 수수료+세금(native USD). 환전 스프레드는 주문단위로 분리 제공되지 않아
        # 포트폴리오 환전 시점에 반영된다(metrics는 broker 수수료만 집계).
        cost = _f(exe.get("commission")) + _f(exe.get("tax"))
        realized = 0.0
        if side == "SELL":
            prev = self.position(symbol)
            if prev.avg_price > 0:
                realized = (price - prev.avg_price) * qty
        return Fill(symbol, side, qty, price, cost, dt, realized_pnl=realized)


def _f(v) -> float:
    """decimal 문자열/None을 안전하게 float로."""
    if v is None:
        return 0.0
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0
