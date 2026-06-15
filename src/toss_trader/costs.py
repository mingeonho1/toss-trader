"""비용 모델 — 소액 매매의 최대 적. 모든 시그널은 비용 차감 후로 평가해야 한다.

요율은 **가정값**이며 실제 값은 /commissions 응답과 환율 스프레드 실측으로 확정한다.
보수적으로(다소 높게) 잡아 백테스트가 낙관에 빠지지 않게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass

BPS = 1e-4  # 1 basis point = 0.01%


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 8.0      # 매매 수수료 (편도, 가정 0.08%)
    fx_spread_bps: float = 20.0      # 원↔달러 환전 스프레드 (편도, 가정 0.20%)
    slippage_bps: float = 5.0        # 체결 슬리피지 (대형주 가정 0.05%)
    min_commission_usd: float = 0.0  # 최소 수수료(있다면)

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
