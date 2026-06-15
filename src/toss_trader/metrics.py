"""성과 지표 — 비용 차감 후 기준으로 전략을 평가한다."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Fill

TRADING_DAYS = 252


@dataclass
class Performance:
    start_equity: float
    end_equity: float
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    num_trades: int
    win_rate: float
    payoff: float          # 평균이익 / 평균손실
    expectancy: float      # 1회 매매당 기대 실현손익(비용 차감 후)
    total_cost: float
    days: int

    def summary(self) -> str:
        return (
            f"기간 {self.days}d | 수익률 {self.total_return*100:+.2f}% "
            f"(CAGR {self.cagr*100:+.2f}%) | MDD {self.max_drawdown*100:.2f}% | "
            f"Sharpe {self.sharpe:.2f}\n"
            f"매매 {self.num_trades}회 | 승률 {self.win_rate*100:.1f}% | "
            f"손익비 {self.payoff:.2f} | 기대값/매매 ${self.expectancy:+.4f} | "
            f"총비용 ${self.total_cost:.2f}"
        )


def _max_drawdown(equity: list[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def _sharpe(equity: list[float]) -> float:
    if len(equity) < 3:
        return 0.0
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(TRADING_DAYS)


def compute(equity_curve: list[float], fills: list[Fill], days: int) -> Performance:
    start = equity_curve[0] if equity_curve else 0.0
    end = equity_curve[-1] if equity_curve else 0.0
    total_return = (end / start - 1.0) if start > 0 else 0.0
    years = max(days / 365.0, 1e-9)
    cagr = (end / start) ** (1 / years) - 1.0 if start > 0 and end > 0 else 0.0

    # 매도 체결 기준 실현손익(비용 차감 후)으로 승률/손익비/기대값 계산
    pnls = [f.realized_pnl - f.cost for f in fills if f.side.upper() == "SELL"]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 0 else (math.inf if avg_win > 0 else 0.0)
    expectancy = sum(pnls) / len(pnls) if pnls else 0.0

    return Performance(
        start_equity=start,
        end_equity=end,
        total_return=total_return,
        cagr=cagr,
        max_drawdown=_max_drawdown(equity_curve),
        sharpe=_sharpe(equity_curve),
        num_trades=len(pnls),
        win_rate=win_rate,
        payoff=payoff,
        expectancy=expectancy,
        total_cost=sum(f.cost for f in fills),
        days=days,
    )
