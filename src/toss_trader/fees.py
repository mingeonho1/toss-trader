"""토스 미국주식 **주문 단위** 실수수료 모델 — 소액·소수점·분할 매수 최적화용.

이 모듈은 백테스터의 bps 근사(`costs.CostModel` / `research.CostSpec`)와 달리, **1건의 주문에
실제로 붙는 달러 수수료**를 규정대로(무료 구간·절사·규제 최소금액·상한) 정확히 계산한다.
분할 매수/매도 시 "몇 건으로 쪼개면 총 수수료가 최소인가"를 계획한다.

요율 근거 (2025-12-01 이후, 공식 공지 https://corp.tossinvest.com/ko/post?id=17106 기준):
- **미국 표준 수수료 = 거래대금의 0.1%**(프로모가 아니라 표준). (구 자료의 0.25%는 스펙 '예시'였을 뿐.)
- **건당 체결금액 ≤ $10 이면 수수료 무료**(a). 소수점(fractional) 주문도 0.1% 동일(c).
- **$0.01 미만 수수료는 절사**(d): 0.1% 계산액을 센트 단위로 내림.  예) $10.01×0.1%=$0.01001 → $0.01.
- '주식모으기'(앱 정기매수)는 무료(b) — 이 봇의 API 주문과는 별개.

규제 수수료(매도에만, 근거는 뉴스/FAQ라 **덜 확실** → `apply_regulatory_min`으로 토글 가능):
- SEC fee(매도 대금 기준): $20.60/$1M = 0.00206% = 0.0000206, 최소 $0.01 (2026-04-04~).
- FINRA TAF(매도 주식수 기준): ≈ $0.000166/주, 최소 $0.01, 최대 $8.30.
- 이 최소금액들 때문에 **매도는 잘게 쪼개면 오히려 비싸다**(건마다 최소 $0.01+$0.01) → 보통 1건.

원칙: 표준 수수료는 규정대로 절사(ROUND_DOWN). 규제 수수료는 apply_regulatory_min=True면
**보수적**으로 센트 올림+최소금액, False면 절사(소액 0). 실측(체결 내역)으로 대체 전까지 근사.
정확한 라이브 요율은 계좌별 `/commissions` 엔드포인트가 진실이다(costs.CostModel.from_commissions).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Sequence

__all__ = [
    "TossFeeSchedule", "SplitPlan", "FeeBreakdown", "BpsPoint",
]

_CENT = Decimal("0.01")


def _d(x: float | int | str | Decimal) -> Decimal:
    """float를 안전하게 Decimal로 (str 경유로 이진부동소수 오차 회피)."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


@dataclass(frozen=True)
class FeeBreakdown:
    """주문 1건 수수료 내역(USD). total = commission + sec_fee + taf."""
    commission: float
    sec_fee: float
    taf: float

    @property
    def total(self) -> float:
        return self.commission + self.sec_fee + self.taf


@dataclass(frozen=True)
class SplitPlan:
    """분할 계획: 주문별 노셔널 리스트 + 총 수수료 + 실효 bps."""
    side: str
    notionals: list[float]
    total_fee: float
    effective_bps: float

    @property
    def n_orders(self) -> int:
        return len(self.notionals)


@dataclass(frozen=True)
class BpsPoint:
    """수수료 곡선의 한 점(주문 크기별 실효 bps)."""
    notional: float
    buy_fee: float
    buy_bps: float
    sell_fee: float
    sell_bps: float


@dataclass(frozen=True)
class TossFeeSchedule:
    """토스 미국주식 주문 단위 수수료 규정(2025-12-01~). 모든 필드는 설정 가능(불확실성 대비).

    - commission_rate: 표준 수수료율(거래대금 대비 소수 비율). 0.001 = 0.1%.
    - free_threshold_usd: 건당 체결금액이 이 값 **이하**면 수수료 무료($10).
    - truncate_cents: 수수료를 센트 단위로 절사(True=규정대로 내림).
    - sec_fee_rate/sec_min: SEC fee(매도 대금 기준)율과 최소금액.
    - taf_per_share/taf_min/taf_max: FINRA TAF(매도 주식수 기준) 단가·최소·최대.
    - apply_regulatory_min: SEC/TAF 최소금액($0.01) 적용 여부(불확실 → 토글).
    """
    commission_rate: float = 0.001          # 0.1% 표준(2025-12-01~; 구 자료 0.25%는 예시였음)
    free_threshold_usd: float = 10.0        # 건당 체결금액 ≤ $10 → 무료
    truncate_cents: bool = True             # $0.01 미만 수수료 절사(센트 내림)
    sec_fee_rate: float = 0.0000206         # SEC fee 0.00206%($20.60/$1M), 매도 대금 기준
    sec_min: float = 0.01
    taf_per_share: float = 0.000166         # FINRA TAF, 매도 주식수 기준
    taf_min: float = 0.01
    taf_max: float = 8.30
    apply_regulatory_min: bool = True       # 규제 수수료 최소금액 적용(불확실 → 설정 가능)

    # ── 성분 계산 ────────────────────────────────────────────────────────────
    def _commission(self, notional: Decimal) -> Decimal:
        """0.1% 표준 수수료(센트 절사). 체결금액 ≤ 무료 임계면 0."""
        if notional <= _d(self.free_threshold_usd):
            return Decimal("0")
        raw = notional * _d(self.commission_rate)
        rounding = ROUND_DOWN if self.truncate_cents else ROUND_HALF_UP
        return raw.quantize(_CENT, rounding=rounding)

    def _sec_fee(self, notional: Decimal) -> Decimal:
        """SEC fee(매도 대금 기준).

        apply_regulatory_min=True: 센트 올림(보수적) + 최소금액 하한.
        False(불확실 → 규제수수료 미적용 모델): 센트 절사(소액은 0).
        """
        raw = notional * _d(self.sec_fee_rate)
        if self.apply_regulatory_min:
            return max(raw.quantize(_CENT, rounding=ROUND_CEILING), _d(self.sec_min))
        return raw.quantize(_CENT, rounding=ROUND_DOWN)

    def _taf(self, shares: Decimal) -> Decimal:
        """FINRA TAF(매도 주식수 기준). 상한($8.30) 적용. 최소금액은 apply_regulatory_min으로 토글."""
        raw = shares * _d(self.taf_per_share)
        cap = _d(self.taf_max)
        if raw > cap:
            raw = cap
        if self.apply_regulatory_min:
            fee = max(raw.quantize(_CENT, rounding=ROUND_CEILING), _d(self.taf_min))
        else:
            fee = raw.quantize(_CENT, rounding=ROUND_DOWN)
        return cap if fee > cap else fee

    # ── 공개 API ─────────────────────────────────────────────────────────────
    def order_fee_breakdown(self, side: str, notional: float,
                            shares: float = 0.0) -> FeeBreakdown:
        """주문 1건의 수수료 내역(USD). 매수는 수수료만, 매도는 +SEC+TAF."""
        n = _d(abs(notional))
        commission = self._commission(n)
        sec = Decimal("0")
        taf = Decimal("0")
        if side.upper() == "SELL" and n > 0:
            sec = self._sec_fee(n)
            taf = self._taf(_d(abs(shares)))
        return FeeBreakdown(commission=float(commission), sec_fee=float(sec), taf=float(taf))

    def order_fee(self, side: str, notional: float, shares: float = 0.0) -> float:
        """주문 1건의 총 수수료(USD). 손계산 앵커: fees 모듈 테스트 참고."""
        return self.order_fee_breakdown(side, notional, shares).total

    def plan_split(self, side: str, notional: float, price: float | None = None, *,
                   max_orders: int = 20, min_chunk: float = 1.0) -> SplitPlan:
        """수수료 최소화 분할 계획.

        - 매수: 각 청크를 무료 임계(≤$10) 이하로 쪼개면 전부 무료 → 가능한 만큼 ≤$10 청크로 분할.
          max_orders에 걸리면 (max_orders−1)개를 $10로 채우고 나머지 1건만 수수료를 문다(과세 노셔널 최소화).
          min_chunk 미만의 잔여 청크는 직전 청크에 합쳐 소액 조각 주문을 피한다.
        - 매도: SEC/TAF 최소금액 때문에 쪼갤수록 비싸다 → **1건**이 최소.  price로 주식수(→TAF) 산정.
        """
        s = side.upper()
        total = float(abs(notional))
        if s == "BUY":
            chunks = self._buy_chunks(total, max_orders=max_orders, min_chunk=min_chunk)
            shares_per = [(c / price if (price and price > 0) else 0.0) for c in chunks]
        elif s == "SELL":
            chunks = [total] if total > 0 else []
            if price and price > 0:
                shares_per = [total / price for _ in chunks]
            else:
                shares_per = [0.0 for _ in chunks]
        else:
            raise ValueError(f"side는 BUY 또는 SELL: {side!r}")
        total_fee = sum(self.order_fee(s, c, sh) for c, sh in zip(chunks, shares_per))
        eff_bps = (total_fee / total * 1e4) if total > 0 else 0.0
        return SplitPlan(side=s, notionals=chunks, total_fee=total_fee, effective_bps=eff_bps)

    def _buy_chunks(self, amount: float, *, max_orders: int, min_chunk: float) -> list[float]:
        """매수 노셔널을 무료 임계(≤$10) 청크로 분할(센트 정수 연산). max_orders/min_chunk 반영."""
        cents = int(round(amount * 100))
        if cents <= 0:
            return []
        free = int(round(self.free_threshold_usd * 100))
        if free <= 0 or max_orders <= 1:
            return [cents / 100.0]
        chunks: list[int] = []
        remaining = cents
        while remaining > 0 and len(chunks) < max_orders - 1:
            take = min(free, remaining)
            chunks.append(take)
            remaining -= take
        if remaining > 0:                      # max_orders 초과분은 마지막 1건(≥$10일 수 있음)에 몰아 과세 최소화
            chunks.append(remaining)
        min_c = int(round(min_chunk * 100))
        if len(chunks) >= 2 and chunks[-1] < min_c:   # 소액 잔여 조각은 직전 청크에 합침
            chunks[-2] += chunks[-1]
            chunks.pop()
        return [c / 100.0 for c in chunks]

    def as_fee_fn(self, *, split: bool = False, price: float | None = None,
                  max_orders: int = 20, min_chunk: float = 1.0
                  ) -> Callable[[str, float], float]:
        """`research.run_weights/run_trades/run_dca_overlay(fee_fn=...)` 훅용 (side, notional)→USD 콜백.

        equity/capital을 **달러**로 돌릴 때만 정확하다(노셔널이 달러여야 order_fee와 단위 일치).
        split=True면 매수를 분할 최소수수료(plan_split, ≤$10 무료 활용)로 계산. price는 매도 TAF 산정용.
        """
        if split:
            def fn(side: str, notional: float) -> float:
                return self.plan_split(side, notional, price,
                                       max_orders=max_orders, min_chunk=min_chunk).total_fee
        else:
            def fn(side: str, notional: float) -> float:
                shares = (abs(notional) / price) if (side.upper() == "SELL" and price and price > 0) else 0.0
                return self.order_fee(side, notional, shares=shares)
        return fn

    def effective_bps_curve(self, sizes: Sequence[float] | None = None, *,
                            price: float = 100.0) -> list[BpsPoint]:
        """주문 크기별 실효 수수료 bps 곡선(매수·매도). 소액 매도 최소금액의 급증을 시각화용.

        매도 bps는 주식수 산정을 위해 price가 필요(기본 $100 → 주식수=노셔널/price).
        """
        if sizes is None:
            sizes = [1.0, 2.0, 5.0, 9.99, 10.0, 10.01, 15.0, 20.0, 35.0,
                     50.0, 100.0, 250.0, 500.0, 1000.0, 5000.0]
        rows: list[BpsPoint] = []
        for s in sizes:
            bf = self.order_fee("BUY", s)
            sh = (s / price) if (price and price > 0) else 0.0
            sf = self.order_fee("SELL", s, shares=sh)
            rows.append(BpsPoint(notional=float(s), buy_fee=bf, buy_bps=_bps(bf, s),
                                 sell_fee=sf, sell_bps=_bps(sf, s)))
        return rows


def _bps(fee: float, notional: float) -> float:
    return (fee / notional * 1e4) if notional > 0 else 0.0
