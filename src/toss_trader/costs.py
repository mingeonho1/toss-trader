"""비용 모델 — 소액 매매의 최대 적. 모든 시그널은 비용 차감 후로 평가해야 한다.

요율 확정 근거 (v1.2.17 정합화, 2026-09-23):
- ⚠️ **단위 변경**: v1.2.17 `/commissions.commissionRate`는 **퍼센트가 아니라 소수 비율(ratio)**.
  스펙 예시: US "0.001"=0.1%(프로모, endDate 2026-06-30), US "0.0025"=0.25%(무기한), KR "0.00015"=0.015%.
  → bps 환산은 `ratio × 10000` (구버전은 percent를 가정해 ×100 했음; 100배 오차 유발했음).
- 각 행은 [startDate, endDate] 유효기간을 가진다. **오늘을 포함하는 행**을 선택하고,
  없으면 보수적 기본요율(미국 표준 0.25% = 25bps)로 폴백 + 경고 로그(0.1% 프로모는 만료).
- 환전 스프레드: exchange-rate의 rate vs midRate 표시 스프레드는 ~3bps였으나, 명세상
  "실제 거래 환율은 표시 환율과 다를 수 있음". 실 체결의 KRW 차감액으로 측정 전까지는
  **보수적으로 높게** 잡아 백테스트 낙관을 방지(기본 20bps 편도 유지).
보수적(다소 높게) 원칙: 비용을 과소평가해 좋은 전략처럼 보이는 함정을 피한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger("toss_trader.costs")

BPS = 1e-4  # 1 basis point = 0.01%

# 미국 표준(비프로모) 수수료 = 0.25%. 오늘을 포함하는 수수료 행이 없을 때의 보수적 폴백.
CONSERVATIVE_US_COMMISSION_BPS = 25.0


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 10.0     # 백테스트 기본 가정(편도). 실측 프로모 0.1%=10bps.
    #   ⚠️ 라이브 실요율은 프로모 만료(2026-06-30) 후 0.25%(=25bps). 실계좌 비용은
    #   from_commissions()가 /commissions에서 오늘 유효요율을 골라 반영한다(폴백 25bps).
    fx_spread_bps: float = 20.0      # 원↔달러 환전 스프레드 (편도, 보수적 가정 0.20%)
    slippage_bps: float = 5.0        # 체결 슬리피지 (대형주 가정 0.05%)
    min_commission_usd: float = 0.0  # 최소 수수료(있다면)

    @classmethod
    def from_commissions(cls, commissions: Any, *, market: str = "US",
                         fx_spread_bps: float = 20.0, slippage_bps: float = 5.0,
                         min_commission_usd: float = 0.0,
                         today: date | None = None) -> "CostModel":
        """TossClient.get_commissions() 응답에서 해당 시장 수수료율로 모델을 만든다.

        v1.2.17: commissionRate는 **소수 비율**(예: "0.0025"=0.25%)이므로 ×10000 하여 bps로 환산.
        여러 행 중 [startDate, endDate]가 오늘을 포함하는 행을 고르고(startDate/endDate=null은
        각각 -∞/+∞), 오늘을 포함하는 행이 없으면 보수적 기본요율(0.25%=25bps)로 폴백 + 경고.
        환전 스프레드/슬리피지는 명세에 없으므로 인자(보수적 기본값)로 받는다.
        """
        today = today or date.today()
        market_u = market.upper()
        rate_ratio: float | None = None
        matched_row: Any = None
        for row in (commissions or []):
            if str(row.get("marketCountry", "")).upper() != market_u:
                continue
            if not _covers(row, today):
                continue
            try:
                rate_ratio = float(row.get("commissionRate", ""))
                matched_row = row
            except (TypeError, ValueError):
                rate_ratio = None
            break

        if rate_ratio is not None:
            commission_bps = rate_ratio * 10_000.0  # ratio → bps
            logger.info("수수료 적용 %s: %.4f%% (%.1fbps) [%s~%s]", market_u,
                        commission_bps / 100.0, commission_bps,
                        matched_row.get("startDate"), matched_row.get("endDate"))
        else:
            commission_bps = CONSERVATIVE_US_COMMISSION_BPS
            logger.warning(
                "오늘(%s)을 포함하는 %s 수수료 행이 없음 → 보수적 폴백 %.2f%%(%.1fbps) 사용. "
                "(0.1%% 프로모 endDate 2026-06-30 만료 가능; 실주문 전 /commissions 재확인)",
                today, market_u, CONSERVATIVE_US_COMMISSION_BPS / 100.0,
                CONSERVATIVE_US_COMMISSION_BPS)
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


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _covers(row: Any, today: date) -> bool:
    """수수료 행의 [startDate, endDate]가 today를 포함하는가. null은 무한대(각각 -∞/+∞)."""
    start = _parse_date(row.get("startDate"))
    end = _parse_date(row.get("endDate"))
    if start is not None and today < start:
        return False
    if end is not None and today > end:
        return False
    return True
