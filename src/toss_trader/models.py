"""도메인 모델. 페이퍼/실거래/백테스트가 공유한다."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Candle:
    symbol: str
    dt: date          # 1d 캔들 기준일
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    currency: str = "USD"


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0   # 평균 매입가 (USD)

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_price) * self.quantity


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str            # BUY | SELL
    quantity: float
    price: float         # 슬리피지 반영 체결가
    cost: float          # 수수료+환전 등 총 부대비용 (USD)
    dt: date
    realized_pnl: float = 0.0   # 매도 시 실현손익(비용 차감 전)
