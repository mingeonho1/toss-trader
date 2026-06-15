"""리스크 관리 — 무인 운용의 안전벨트.

순서대로 적용:
1) 보호적 청산: 손절 / 익절 / 트레일링 스탑 (보유 포지션 강제 매도)
2) 일일 최대손실 차단(circuit breaker): 당일 시작자본 대비 -X% → 신규진입 중단
3) 비중 조정: 종목당 최대비중 캡, 최소매매금액 미만 조각거래 금지
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Position


@dataclass(frozen=True)
class RiskConfig:
    max_position_weight: float = 0.5    # 종목당 최대 비중
    stop_loss_pct: float = 0.08         # -8% 손절
    take_profit_pct: float = 0.25       # +25% 익절
    trailing_stop_pct: float = 0.12     # 고점 대비 -12% (0이면 비활성)
    daily_max_loss_pct: float = 0.05    # 당일 -5%면 신규진입 중단
    min_trade_usd: float = 5.0          # 이보다 작은 매매는 비용낭비 → 금지


class RiskManager:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.cfg = config or RiskConfig()
        self.prev_equity: float | None = None   # 전일 종가 자본(일일손실 기준선)
        self.halted = False
        self.halt_reason = ""
        self._peak_price: dict[str, float] = {}   # 트레일링용 보유기간 고점

    # --- 일자 경계 ---
    def start_day(self, equity: float) -> None:
        self.halted = False
        self.halt_reason = ""
        if self.prev_equity is None:   # 최초 실행일은 자기 자신이 기준
            self.prev_equity = equity

    def end_day(self, equity: float) -> None:
        """장 마감 시 호출. 다음 날 일일손실 차단의 기준선을 갱신한다."""
        self.prev_equity = equity

    # --- 트레일링 고점 갱신 / 정리 ---
    def observe(self, positions: dict[str, Position], prices: dict[str, float]) -> None:
        held = {s for s, p in positions.items() if p.quantity > 0}
        for sym in held:
            px = prices.get(sym)
            if px is not None:
                self._peak_price[sym] = max(self._peak_price.get(sym, px), px)
        # 청산된 종목은 고점 추적 해제
        for sym in list(self._peak_price):
            if sym not in held:
                self._peak_price.pop(sym, None)

    # --- 보호적 청산 대상 산출 ---
    def evaluate_exits(self, positions: dict[str, Position],
                       prices: dict[str, float]) -> list[tuple[str, str]]:
        exits: list[tuple[str, str]] = []
        for sym, pos in positions.items():
            if pos.quantity <= 0 or pos.avg_price <= 0:
                continue
            px = prices.get(sym)
            if px is None:
                continue
            ret = px / pos.avg_price - 1.0
            if self.cfg.stop_loss_pct > 0 and ret <= -self.cfg.stop_loss_pct:
                exits.append((sym, f"손절 {ret*100:.1f}%"))
            elif self.cfg.take_profit_pct > 0 and ret >= self.cfg.take_profit_pct:
                exits.append((sym, f"익절 {ret*100:.1f}%"))
            elif self.cfg.trailing_stop_pct > 0:
                peak = self._peak_price.get(sym, px)
                if peak > 0 and px / peak - 1.0 <= -self.cfg.trailing_stop_pct:
                    exits.append((sym, f"트레일링 고점대비 {(px/peak-1)*100:.1f}%"))
        return exits

    # --- 일일 손실 차단 (전일 종가 자본 대비) ---
    def check_daily_halt(self, equity: float) -> bool:
        if self.prev_equity and self.prev_equity > 0:
            dd = equity / self.prev_equity - 1.0
            if dd <= -self.cfg.daily_max_loss_pct:
                self.halted = True
                self.halt_reason = f"일일손실 {dd*100:.1f}% (한도 -{self.cfg.daily_max_loss_pct*100:.0f}%)"
        return self.halted

    # --- 목표비중 조정 ---
    def adjust_weights(self, weights: dict[str, float], equity: float,
                       exclude: set[str] | None = None) -> dict[str, float]:
        exclude = exclude or set()
        if self.halted:
            return {}   # 차단 시 신규/추가 진입 없음
        out: dict[str, float] = {}
        for sym, w in weights.items():
            if sym in exclude:
                continue
            w = min(w, self.cfg.max_position_weight)
            if w * equity < self.cfg.min_trade_usd:
                continue   # 조각거래 금지
            out[sym] = w
        return out
