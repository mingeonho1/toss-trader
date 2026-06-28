"""비용 모델 — 소액 매매의 최대 적. 모든 시그널은 비용 차감 후로 평가해야 한다.

요율 확정 근거 (2026-06-28, 실 /commissions·exchange-rate 응답):
- 미국주식 수수료: **0.1% 편도** (commissionRate "0.1"; 단 endDate 2026-06-29 — 프로모 가능성,
  실거래 전 재확인). → commission_bps 기본값 10.0.
- 환전 스프레드: exchange-rate의 rate vs midRate 표시 스프레드는 ~3bps였으나, 명세상
  "실제 거래 환율은 표시 환율과 다를 수 있음". 실 체결의 KRW 차감액으로 측정 전까지는
  **보수적으로 높게** 잡아 백테스트 낙관을 방지(기본 20bps 편도 유지).
보수적(다소 높게) 원칙: 비용을 과소평가해 좋은 전략처럼 보이는 함정을 피한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

BPS = 1e-4  # 1 basis point = 0.01%


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 10.0     # 매매 수수료 (편도). 실측: 미국 0.1% (2026-06-28)
    fx_spread_bps: float = 20.0      # 원↔달러 환전 스프레드 (편도, 보수적 가정 0.20%)
    slippage_bps: float = 5.0        # 체결 슬리피지 (대형주 가정 0.05%)
    min_commission_usd: float = 0.0  # 최소 수수료(있다면)

    @classmethod
    def from_commissions(cls, commissions: Any, *, market: str = "US",
                         fx_spread_bps: float = 20.0, slippage_bps: float = 5.0,
                         min_commission_usd: float = 0.0) -> "CostModel":
        """TossClient.get_commissions() 응답에서 해당 시장 수수료율(%)로 모델을 만든다.

        commissionRate는 퍼센트(예: "0.1" = 0.1%)이므로 ×100 하여 bps로 환산한다.
        환전 스프레드/슬리피지는 명세에 없으므로 인자(보수적 기본값)로 받는다.
        """
        rate_pct = None
        for row in (commissions or []):
            if str(row.get("marketCountry", "")).upper() == market.upper():
                try:
                    rate_pct = float(row.get("commissionRate", ""))
                except (TypeError, ValueError):
                    rate_pct = None
                break
        commission_bps = rate_pct * 100.0 if rate_pct is not None else cls.commission_bps
        return cls(commission_bps=commission_bps, fx_spread_bps=fx_spread_bps,
                   slippage_bps=slippage_bps, min_commission_usd=min_commission_usd)

    def fill_price(self, ref_price: float, side: str) -> float:
        """슬리피지 반영 체결가. 매수는 불리하게 위로, 매도는 아래로."""
        slip = ref_price * self.slippage_bps * BPS
        return ref_price + slip if side.upper() == "BUY" else ref_price - slip

    def trade_cost(self, notional: float) -> float:
        """체결 금액(절대값)에 대한 부대비용 = 수수료 + 환전 스프레드."""
        notional = abs(notional)
        commission = max(self.min_commission_usd, notional * self.commission_bps * BPS)
        fx = notional * self.fx_spread_bps * BPS
        return commission + fx

    @property
    def roundtrip_bps(self) -> float:
        """왕복 총비용(bps). 손익분기 임계치 = 이 값보다 더 벌어야 본전."""
        return 2 * (self.commission_bps + self.fx_spread_bps + self.slippage_bps)
