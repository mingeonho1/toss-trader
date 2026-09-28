"""공격형 포워드 페이퍼 랩 — 액티브 전략이 '실시세 전진'으로 스스로 증명하는 곳(실주문 절대 없음).

설계(부록 v3 Lane A · 사용자 목표: 자동매매 수익 최우선, 공격적 허용, 페이퍼로 먼저 검증):

- **전략** = 객체: ``name`` · ``universe()``(심볼) · ``decide(history, state) -> target_weights``
  (종가 t 기준 하루 1회 산출) · ``fill_on``("close"|"open") · 선택적 ``intraday`` 훅.
- **무 look-ahead**: 종가 t 에서 산출한 목표비중은 **다음 거래일 t+1 에 체결**한다(기본 t+1 종가,
  ``fill_on="open"`` 이고 시가가 있으면 t+1 시가). 엔진은 어제 정한 ``pending`` 목표를 오늘 체결하고,
  오늘 종가까지의 데이터로만 새 목표를 정한다 → 미래참조 불가능.
- **전략별 페이퍼 브로커 2권**: $1,000 **단위 장부**(깨끗한 성과) + $36 **실스케일 장부**(소액 수수료
  실측). 둘 다 같은 목표비중을 따르되 자본만 다르다.
- **토스 수수료 정확 반영**(:class:`~toss_trader.fees.TossFeeSchedule`): 매수 건당 ≤$10 무료,
  매도 0.1% + SEC/TAF 최소금액. **소수점 체결**. **현금 이자 0%**.
- **멱등 영속화**: ``data/paperlab/{name}/`` 에 상태/체결/에쿼티를 **날짜 키로 멱등** 저장.

이 모듈은 순수 계산(브로커·엔진·지표·리포트)만 담고, 네트워크/캐시 로딩은 ``scripts/paperlab_run.py``
가 담당한다. 표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .fees import TossFeeSchedule
from .models import Candle

__all__ = [
    "Strategy", "PaperBook", "PaperLab", "BookMetrics",
    "cagr_from_curve", "max_drawdown", "buy_hold_return",
    "render_leaderboard", "render_backfill",
]

_EPS = 1e-12

# 레버리지/인버스/변동성 ETP(한국 리테일 첫 거래 시 예탁금 규제 대상). 유니버스에 하나라도 있으면
# '레버리지 ETP(예탁금 필요)' 그룹으로 분류한다.
_LEVERAGED_ETP = frozenset({
    "TQQQ", "SQQQ", "QLD", "SOXL", "SOXS", "SOXX", "TECL", "TECS", "SPXL", "SPXU",
    "UPRO", "SDOW", "UDOW", "UVXY", "VIXY", "VXX", "SVXY", "SVIX", "TMF", "TMV",
    "FAS", "FAZ", "HIBL", "HIBS", "FNGU", "FNGD", "TNA", "TZA", "LABU", "LABD",
    "NAIL", "DPST", "BOIL", "KOLD", "UCO", "SCO", "YINN", "YANG", "CONL", "MSTU",
})

# 그룹 라벨(task: 실계좌 가능/레버리지 ETP/탐색용).
GROUP_LABELS = {
    "retail": "실계좌 가능(비레버리지)",
    "leverage": "레버리지 ETP(예탁금 필요)",
    "explore": "탐색용",
}
_GROUP_ORDER = ["retail", "leverage", "explore"]


def classify_group(uni: Sequence[str], declared: str | None = None) -> str:
    """전략 그룹 판정. declared 우선(예 'explore'), 없으면 유니버스에 레버리지 ETP 포함 여부로."""
    if declared in GROUP_LABELS:
        return declared
    return "leverage" if any(s in _LEVERAGED_ETP for s in uni) else "retail"


# ─────────────────────────────────────────────────────────── 전략 베이스(프로토콜)
class Strategy:
    """페이퍼 랩 전략 베이스. 하위 모듈은 이걸 상속해 ``name``/``universe``/``decide`` 를 채운다.

    - ``name``: 고유 식별자(폴더/리포트 키). 소문자 스네이크.
    - ``fill_on``: "close"(기본) 또는 "open" — 신호(종가 t) 대비 체결 시점(t+1 종가/시가).
    - ``decide(history, state)``: ``history[sym]`` 은 **오늘(t) 종가까지의** Candle 오름차순 리스트.
      ``state`` 는 전략별 영속 dict(엔진이 저장/복원). 반환은 목표비중 dict(합≤1, 나머지는 현금).
      빈 dict/0합이면 **100% 현금**을 뜻한다.
    - ``intraday``: 선택적 장중 훅(현재는 미사용, None). 장중 신호가 필요한 전략용 확장점.
    """

    name: str = "base"
    fill_on: str = "close"
    intraday: Callable[..., Any] | None = None
    overnight: bool = False    # True면 엔진이 종가매수→익일시가매도 전용 경로로 실행(G1)
    exec_lag: int = 1          # 1(기본)=신호(종가 t)→익일 체결(t+1, 보수적). 0=당일 종가 체결(MOC, 낙관적).
    real_monthly_contribution: float = 0.0   # >0이면 실($36)장부에 매월 첫 거래일 적립(포워드 전용; 단위장부 미적용)
    group: str | None = None   # 리더보드 분류. None이면 유니버스로 자동판정(레버리지 ETP↔비레버리지). "explore"=탐색용.

    def universe(self) -> list[str]:  # pragma: no cover - 추상
        raise NotImplementedError

    def decide(self, history: Mapping[str, Sequence[Candle]],
               state: dict[str, Any]) -> dict[str, float]:  # pragma: no cover - 추상
        raise NotImplementedError


# ─────────────────────────────────────────────────────────── 페이퍼 브로커(1권)
class PaperBook:
    """단일 자본 장부. 소수점 체결, 토스 수수료 정확 반영, 현금 이자 0%.

    ``positions[sym] = {"qty": float, "avg": float}`` (avg = 수수료 제외 평균 매입가).
    매수 예산(budget)은 **현금에서 나갈 총액**(노셔널+수수료)이라 절대 현금이 음수가 되지 않는다.
    """

    def __init__(self, cash: float, fees: TossFeeSchedule, *, min_trade_usd: float = 0.01,
                 start: float | None = None) -> None:
        self.cash = float(cash)
        self.start = float(start if start is not None else cash)
        self.fees = fees
        self.min_trade = float(min_trade_usd)
        self.positions: dict[str, dict[str, float]] = {}
        self.fills: list[dict[str, Any]] = []

    # ── 마크투마켓 ────────────────────────────────────────────────────────────
    def market_value(self, prices: Mapping[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            if px and pos["qty"] > _EPS:
                total += pos["qty"] * px
        return total

    def equity(self, prices: Mapping[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def qty(self, sym: str) -> float:
        return self.positions.get(sym, {}).get("qty", 0.0)

    # ── 주문(소수점·수수료 정확) ──────────────────────────────────────────────
    def buy(self, sym: str, budget: float, price: float, dt: date) -> dict[str, Any] | None:
        """``budget`` = 현금에서 지출할 **총액**(노셔널+수수료). 노셔널 = budget − 예상수수료."""
        if budget <= 0 or price <= 0:
            return None
        budget = min(budget, self.cash)
        est_fee = self.fees.order_fee("BUY", budget)
        notional = budget - est_fee
        if notional < self.min_trade or notional <= 0:
            return None
        fee = self.fees.order_fee("BUY", notional)
        shares = notional / price
        self.cash -= (notional + fee)
        pos = self.positions.setdefault(sym, {"qty": 0.0, "avg": 0.0})
        new_qty = pos["qty"] + shares
        pos["avg"] = (pos["avg"] * pos["qty"] + notional) / new_qty if new_qty > 0 else price
        pos["qty"] = new_qty
        rec = {"dt": dt.isoformat(), "symbol": sym, "side": "BUY", "qty": shares,
               "price": price, "fee": fee, "realized": 0.0}
        self.fills.append(rec)
        return rec

    def sell(self, sym: str, shares: float, price: float, dt: date) -> dict[str, Any] | None:
        pos = self.positions.get(sym)
        if not pos or shares <= 0 or price <= 0:
            return None
        shares = min(shares, pos["qty"])
        notional = shares * price
        if notional <= 0:
            return None
        fee = self.fees.order_fee("SELL", notional, shares)
        realized = (price - pos["avg"]) * shares
        self.cash += (notional - fee)
        pos["qty"] -= shares
        if pos["qty"] <= _EPS:
            self.positions.pop(sym, None)
        rec = {"dt": dt.isoformat(), "symbol": sym, "side": "SELL", "qty": shares,
               "price": price, "fee": fee, "realized": realized}
        self.fills.append(rec)
        return rec

    def rebalance(self, target: Mapping[str, float], prices: Mapping[str, float],
                  dt: date) -> list[dict[str, Any]]:
        """목표비중으로 리밸런스(매도 먼저 현금확보 → 매수). 가격 없는 심볼은 건드리지 않는다."""
        before = len(self.fills)
        eq = self.equity(prices)
        if eq <= 0:
            return []
        targets: dict[str, float] = {}
        for sym, w in target.items():
            px = prices.get(sym)
            if w > 0 and px and px > 0:
                targets[sym] = w * eq
        for sym in list(self.positions):            # 목표에 없는 보유분은 청산 대상
            if sym not in targets and prices.get(sym):
                targets[sym] = 0.0

        # 1) 매도(초과분) — 현금 확보
        for sym, tval in targets.items():
            px = prices.get(sym)
            if not px:
                continue
            cur = self.qty(sym) * px
            diff = tval - cur
            if diff < -self.min_trade:
                self.sell(sym, min(self.qty(sym), (-diff) / px), px, dt)
        # 2) 매수(부족분) — 큰 것부터, 현금 한도 내
        for sym, tval in sorted(targets.items(), key=lambda kv: kv[1], reverse=True):
            px = prices.get(sym)
            if not px or self.cash <= 0:
                continue
            cur = self.qty(sym) * px
            diff = tval - cur
            if diff > self.min_trade:
                self.buy(sym, min(diff, self.cash), px, dt)
        return self.fills[before:]

    # ── 직렬화 ────────────────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        if abs(self.cash) < 1e-10:
            self.cash = 0.0
        return {"cash": self.cash, "start": self.start,
                "positions": {s: dict(p) for s, p in self.positions.items() if p["qty"] > _EPS},
                "fills": self.fills}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], fees: TossFeeSchedule, *,
                  min_trade_usd: float = 0.01) -> "PaperBook":
        book = cls(float(d.get("cash", 0.0)), fees, min_trade_usd=min_trade_usd,
                   start=float(d.get("start", d.get("cash", 0.0))))
        for sym, p in (d.get("positions") or {}).items():
            book.positions[sym] = {"qty": float(p["qty"]), "avg": float(p["avg"])}
        book.fills = list(d.get("fills") or [])
        return book

    @property
    def total_fees(self) -> float:
        return sum(float(f.get("fee", 0.0)) for f in self.fills)


# ─────────────────────────────────────────────────────────── 지표
def max_drawdown(values: Sequence[float]) -> float:
    """에쿼티 곡선의 최대낙폭(음수). 2점 미만이면 0."""
    peak = -math.inf
    mdd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def cagr_from_curve(curve: Sequence[tuple[date, float]]) -> float | None:
    """(date, equity) 곡선의 연복리성장률. 캘린더일수 기준. 데이터 부족/음수면 None."""
    pts = [(d, v) for d, v in curve if v is not None]
    if len(pts) < 2:
        return None
    d0, v0 = pts[0]
    d1, v1 = pts[-1]
    days = (d1 - d0).days
    if v0 <= 0 or v1 <= 0 or days <= 0:
        return None
    years = days / 365.25
    if years < 1e-9:
        return None
    return (v1 / v0) ** (1.0 / years) - 1.0


def buy_hold_return(closes: Mapping[date, float], start: date, end: date) -> float | None:
    """[start, end] 구간 바이앤홀드 총수익(조정종가 기준). start/end 이전·이후 첫/마지막 유효값 사용."""
    if not closes:
        return None
    dates = sorted(closes)
    s = next((d for d in dates if d >= start), None)
    e = next((d for d in reversed(dates) if d <= end), None)
    if s is None or e is None or s > e:
        return None
    p0, p1 = closes[s], closes[e]
    if p0 <= 0:
        return None
    return p1 / p0 - 1.0


@dataclass(frozen=True)
class BookMetrics:
    """한 장부(단위 또는 실스케일)의 요약 지표."""
    start_equity: float
    equity: float
    total_return: float | None
    today_return: float | None
    cagr: float | None
    max_dd: float
    trades: int
    fees: float


def _book_metrics(start_equity: float, curve: Sequence[tuple[date, float]],
                  trades: int, fees: float) -> BookMetrics:
    vals = [v for _, v in curve]
    equity = vals[-1] if vals else start_equity
    total = (equity / vals[0] - 1.0) if vals and vals[0] > 0 else None
    today = (vals[-1] / vals[-2] - 1.0) if len(vals) >= 2 and vals[-2] > 0 else None
    return BookMetrics(start_equity=start_equity, equity=equity, total_return=total,
                       today_return=today, cagr=cagr_from_curve(curve),
                       max_dd=max_drawdown(vals), trades=trades, fees=fees)


# ─────────────────────────────────────────────────────────── 엔진(1 전략)
class PaperLab:
    """한 전략의 페이퍼 장부(단위+실스케일)를 히스토리 위에서 결정론적으로 전진시킨다."""

    def __init__(self, strategy: Strategy, *, unit_usd: float = 1000.0,
                 real_usd: float = 36.0, fees: TossFeeSchedule | None = None,
                 min_trade_usd: float = 0.01) -> None:
        self.strategy = strategy
        self.unit_usd = float(unit_usd)
        self.real_usd = float(real_usd)
        self.fees = fees or TossFeeSchedule()
        self.min_trade_usd = float(min_trade_usd)

    # ── 상태 ----------------------------------------------------------------
    def fresh_state(self, start_date: date) -> dict[str, Any]:
        return {
            "version": 1,
            "name": self.strategy.name,
            "fill_on": self.strategy.fill_on,
            "start_date": start_date.isoformat(),
            "last_date": None,
            "pending": None,
            "last_applied": None,      # 마지막으로 실제 체결한 목표비중(변화 감지용)
            "real_contributed": 0.0,   # 실장부 누적 적립액(적립형 전략만; 자금가중 수익 해석용)
            "strategy_state": {},
            "unit": PaperBook(self.unit_usd, self.fees, min_trade_usd=self.min_trade_usd).to_dict(),
            "real": PaperBook(self.real_usd, self.fees, min_trade_usd=self.min_trade_usd).to_dict(),
            "equity": [],          # [[iso_date, unit_eq, real_eq], ...]
        }

    # ── 전진 실행(멱등) ------------------------------------------------------
    def run(self, state: dict[str, Any], master_dates: Sequence[date],
            by_date: Mapping[str, Mapping[date, Candle]], *,
            contribute: bool = True) -> dict[str, Any]:
        """``master_dates`` 거래일 순서로 전진. ``by_date[sym][d]`` = 그 날 Candle.

        멱등: ``state['last_date']`` 이하 날짜는 건너뛴다. 같은 캐시로 재실행하면 결과 동일.

        ``exec_lag``(전략 속성): 1=신호(종가 t)→익일 체결(t+1, 보수적 기본), 0=당일 종가 체결(MOC, 낙관적).
        ``contribute``: True(포워드 기본)이고 전략의 ``real_monthly_contribution>0`` 이면 매월 첫 거래일에
        실($36)장부에 그 금액을 현금 적립한다(단위장부는 미적용). 백필(BACKTEST)은 비교 왜곡을 피해 False.
        """
        if getattr(self.strategy, "overnight", False):
            return self._run_overnight(state, master_dates, by_date)
        strat = self.strategy
        uni = strat.universe()
        fill_on = state.get("fill_on", strat.fill_on)
        exec_lag = int(getattr(strat, "exec_lag", 1) or 0)
        contrib = float(getattr(strat, "real_monthly_contribution", 0.0) or 0.0)
        do_contribute = bool(contribute) and contrib > 0.0
        start_date = date.fromisoformat(state["start_date"])
        last = date.fromisoformat(state["last_date"]) if state.get("last_date") else None

        unit = PaperBook.from_dict(state["unit"], self.fees, min_trade_usd=self.min_trade_usd)
        real = PaperBook.from_dict(state["real"], self.fees, min_trade_usd=self.min_trade_usd)
        pending = state.get("pending")
        last_applied = state.get("last_applied")
        real_contributed = float(state.get("real_contributed", 0.0) or 0.0)
        sstate = state.get("strategy_state") or {}
        equity: list[list[Any]] = list(state.get("equity") or [])

        # 가시 히스토리(오늘까지) — 하루 1개씩 append 해 O(N) 유지, 미래참조 불가.
        visible: dict[str, list[Candle]] = {s: [] for s in uni}
        last_price: dict[str, float] = {}
        # 재개(resume): 이미 처리한 날들의 캔들을 visible/last_price 에 미리 채운다(신호 연속성).
        for d in master_dates:
            if last is not None and d <= last:
                for s in uni:
                    c = by_date.get(s, {}).get(d)
                    if c is not None:
                        visible[s].append(c)
                        if c.close > 0:
                            last_price[s] = c.close
            else:
                break

        prev_d: date | None = last        # 직전 처리 거래일(월초 적립 판정용)
        for d in master_dates:
            if last is not None and d <= last:
                continue
            # 오늘 캔들 반영(가시 히스토리 · 체결/마크 가격). 워밍업 구간(d<start)에도 히스토리는
            # 채워야 첫 거래일에 SMA200/RSI2 등 룩백 신호가 눈 뜬 채로 계산된다.
            close_px: dict[str, float] = {}
            fill_px: dict[str, float] = {}
            for s in uni:
                c = by_date.get(s, {}).get(d)
                if c is None:
                    continue
                visible[s].append(c)
                if c.close > 0:
                    close_px[s] = c.close
                    last_price[s] = c.close
                    fill_px[s] = c.open if (fill_on == "open" and c.open > 0) else c.close

            if d < start_date:
                continue          # 워밍업: 신호용 히스토리만 채우고 매매/에쿼티/결정은 하지 않음

            # 0) 월초 적립(실장부 전용, 선택). 최초 세션엔 적립하지 않음(초기자본만). 결정/체결 전에 넣어
            #    당일이 리밸런스일이면 그대로 배분되고, 아니면 다음 리밸런스까지 현금(이자 0%)으로 대기.
            if (do_contribute and prev_d is not None
                    and (prev_d.year, prev_d.month) != (d.year, d.month)):
                real.cash += contrib
                real_contributed += contrib

            if exec_lag == 0:
                # (MOC, exec_lag=0) 낙관적: 오늘 종가까지의 데이터로 결정 → **오늘 종가**에 체결.
                #   Composer 식 same-close 체결(관대). 목표가 바뀐 날만 리밸런스(변화 없으면 홀드).
                tw = strat.decide(visible, sstate) or {}
                target = {s: float(w) for s, w in tw.items() if float(w) > 0}
                if not _same_target(target, last_applied):
                    need = [s for s, w in target.items() if w > 1e-9]
                    if all(s in close_px for s in need):
                        unit.rebalance(target, close_px, d)
                        real.rebalance(target, close_px, d)
                        last_applied = dict(target)
                pending = None
            else:
                # (t+1, 기본) 어제 정한 목표를 오늘 체결. **목표가 바뀐 날만** 리밸런스한다(변화 없으면
                #   홀드·드리프트 = 신호 사이엔 바이앤홀드). 모든 목표심볼 가격이 있어야 실행(부분체결 금지);
                #   없으면 pending 유지 후 다음날 재시도.
                if pending is not None:
                    if _same_target(pending, last_applied):
                        pending = None                # 목표 동일 → 매매 없이 보유(드리프트)
                    else:
                        need = [s for s, w in pending.items() if w > 1e-9]
                        if all(s in fill_px for s in need):
                            unit.rebalance(pending, fill_px, d)
                            real.rebalance(pending, fill_px, d)
                            last_applied = dict(pending)
                            pending = None

            # 2) 마크투마켓(오늘 종가; 결측 심볼은 직전가로 캐리포워드). MOC 당일 체결도 여기서 반영.
            mark = dict(last_price)
            mark.update(close_px)
            equity.append([d.isoformat(), unit.equity(mark), real.equity(mark)])

            # 3) t+1 모드는 오늘 종가까지의 데이터로 새 목표 산출 → 내일 체결(pending). MOC는 위에서 처리됨.
            if exec_lag != 0:
                tw = strat.decide(visible, sstate) or {}
                pending = {s: float(w) for s, w in tw.items() if float(w) > 0}
            prev_d = d
            last = d

        state["last_date"] = last.isoformat() if last else None
        state["pending"] = pending
        state["last_applied"] = last_applied
        state["real_contributed"] = real_contributed
        state["strategy_state"] = sstate
        state["unit"] = unit.to_dict()
        state["real"] = real.to_dict()
        state["equity"] = equity
        return state

    # ── 오버나이트 전진(G1: 종가 매수 → 익일 시가 매도) ----------------------
    def _run_overnight(self, state: dict[str, Any], master_dates: Sequence[date],
                       by_date: Mapping[str, Mapping[date, Candle]]) -> dict[str, Any]:
        """close(t) 매수 → open(t+1) 매도만 보유(장중 현금). 매수는 ≤$10 분할 무료.

        보유 포지션은 각 종가 시점에 장부에 남아 있고(그날 밤 보유분), 다음 거래일 **시가**에
        매도한다. 시가 없으면 종가로 폴백. 멱등: ``last_date`` 이하는 건너뛴다.
        """
        strat = self.strategy
        uni = strat.universe()
        start_date = date.fromisoformat(state["start_date"])
        last = date.fromisoformat(state["last_date"]) if state.get("last_date") else None
        unit = PaperBook.from_dict(state["unit"], self.fees, min_trade_usd=self.min_trade_usd)
        real = PaperBook.from_dict(state["real"], self.fees, min_trade_usd=self.min_trade_usd)
        sstate = state.get("strategy_state") or {}
        equity: list[list[Any]] = list(state.get("equity") or [])

        visible: dict[str, list[Candle]] = {s: [] for s in uni}
        last_price: dict[str, float] = {}
        for d in master_dates:                      # 재개: 처리한 날 히스토리 복원
            if last is not None and d <= last:
                for s in uni:
                    c = by_date.get(s, {}).get(d)
                    if c is not None:
                        visible[s].append(c)
                        if c.close > 0:
                            last_price[s] = c.close
            else:
                break

        for d in master_dates:
            if last is not None and d <= last:
                continue
            cand: dict[str, Candle] = {}
            for s in uni:
                c = by_date.get(s, {}).get(d)
                if c is not None:
                    visible[s].append(c)
                    cand[s] = c
                    if c.close > 0:
                        last_price[s] = c.close
            if d < start_date:
                continue

            # 1) 어젯밤 보유분을 오늘 **시가**에 매도(익일 시가 청산).
            for book in (unit, real):
                for sym in list(book.positions):
                    c = cand.get(sym)
                    if c is None or book.positions[sym]["qty"] <= _EPS:
                        continue
                    px = c.open if c.open > 0 else c.close
                    if px > 0:
                        book.sell(sym, book.positions[sym]["qty"], px, d)

            # 2) 오늘 종가까지의 데이터로 오늘 밤 보유 목표 결정.
            tw = strat.decide(visible, sstate) or {}
            target = {s: float(w) for s, w in tw.items()
                      if float(w) > 0 and s in cand and cand[s].close > 0}

            # 3) 오늘 **종가**에 매수(≤$10 분할 무료 반영).
            for book in (unit, real):
                for sym, w in target.items():
                    self._overnight_buy(book, sym, w * book.cash, cand[sym].close, d)

            # 4) 오늘 종가 마크(매수 직후 → 야간 수익은 다음날 시가 매도에 반영).
            mark = dict(last_price)
            mark.update({s: c.close for s, c in cand.items() if c.close > 0})
            equity.append([d.isoformat(), unit.equity(mark), real.equity(mark)])
            last = d

        state["last_date"] = last.isoformat() if last else None
        state["pending"] = None
        state["last_applied"] = None
        state["strategy_state"] = sstate
        state["unit"] = unit.to_dict()
        state["real"] = real.to_dict()
        state["equity"] = equity
        return state

    def _overnight_buy(self, book: PaperBook, sym: str, budget: float, price: float,
                       dt: date) -> None:
        """종가 매수(분할 최소수수료: ≤$10 청크 무료). budget=현금에서 나갈 총액."""
        if budget <= 0 or price <= 0:
            return
        budget = min(budget, book.cash)
        est_fee = book.fees.plan_split("BUY", budget, price).total_fee
        notional = budget - est_fee
        if notional < book.min_trade or notional <= 0:
            return
        fee = book.fees.plan_split("BUY", notional, price).total_fee
        shares = notional / price
        book.cash -= (notional + fee)
        pos = book.positions.setdefault(sym, {"qty": 0.0, "avg": 0.0})
        new_qty = pos["qty"] + shares
        pos["avg"] = (pos["avg"] * pos["qty"] + notional) / new_qty if new_qty > 0 else price
        pos["qty"] = new_qty
        book.fills.append({"dt": dt.isoformat(), "symbol": sym, "side": "BUY",
                           "qty": shares, "price": price, "fee": fee, "realized": 0.0})

    # ── 요약 ----------------------------------------------------------------
    def summarize(self, state: dict[str, Any]) -> dict[str, Any]:
        curve = [(date.fromisoformat(r[0]), float(r[1])) for r in state.get("equity", [])]
        real_curve = [(date.fromisoformat(r[0]), float(r[2])) for r in state.get("equity", [])]
        unit = PaperBook.from_dict(state["unit"], self.fees)
        real = PaperBook.from_dict(state["real"], self.fees)
        um = _book_metrics(self.unit_usd, curve, len(unit.fills), unit.total_fees)
        rm = _book_metrics(self.real_usd, real_curve, len(real.fills), real.total_fees)
        start = date.fromisoformat(state["start_date"])
        first_d = curve[0][0] if curve else None
        last_d = curve[-1][0] if curve else None
        return {
            "name": state["name"], "fill_on": state.get("fill_on"),
            "start_date": start.isoformat(),
            "first_session": first_d.isoformat() if first_d else None,
            "last_session": last_d.isoformat() if last_d else None,
            "days_live": len(curve),
            "calendar_days": (last_d - first_d).days if (first_d and last_d) else 0,
            "unit": um, "real": rm,
            "curve": curve, "real_curve": real_curve,
            "position_text": _position_text(unit, dict()),
            "pending": state.get("pending"),
            "real_contributed": float(state.get("real_contributed", 0.0) or 0.0),
            "group": classify_group(self.strategy.universe(),
                                    getattr(self.strategy, "group", None)),
        }


def _same_target(a: Mapping[str, float] | None, b: Mapping[str, float] | None,
                 tol: float = 1e-9) -> bool:
    """두 목표비중이 (부동소수 허용오차 내에서) 동일한가. 둘 다 현금(빈/None)도 동일로 본다."""
    a = a or {}
    b = b or {}
    keys = set(a) | set(b)
    return all(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) <= tol for k in keys)


def _position_text(book: PaperBook, prices: Mapping[str, float]) -> str:
    parts = [f"{s} {p['qty']:.4f}" for s, p in sorted(book.positions.items()) if p["qty"] > _EPS]
    return ", ".join(parts) if parts else "cash"


# ─────────────────────────────────────────────────────────── 영속화(멱등)
def save_state(base_dir: Path, name: str, state: dict[str, Any]) -> None:
    """``data/paperlab/{name}/`` 에 state.json + trades.jsonl + equity.csv 를 원자적·멱등 기록."""
    d = base_dir / name
    d.mkdir(parents=True, exist_ok=True)
    _atomic_write(d / "state.json", json.dumps(state, ensure_ascii=False, indent=2))
    # trades.jsonl / equity.csv 는 state 로부터 매번 재생성 → 멱등(중복 없음).
    trades: list[str] = []
    for book_key in ("unit", "real"):
        for f in (state.get(book_key, {}).get("fills") or []):
            trades.append(json.dumps({"book": book_key, **f}, ensure_ascii=False))
    _atomic_write(d / "trades.jsonl", ("\n".join(trades) + "\n") if trades else "")
    eq_lines = ["date,unit_equity,real_equity"]
    for row in state.get("equity", []):
        eq_lines.append(f"{row[0]},{float(row[1]):.6f},{float(row[2]):.6f}")
    _atomic_write(d / "equity.csv", "\n".join(eq_lines) + "\n")


def load_state(base_dir: Path, name: str) -> dict[str, Any] | None:
    path = base_dir / name / "state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ─────────────────────────────────────────────────────────── 리포트(순수 렌더)
def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def _num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def render_leaderboard(summaries: Sequence[dict[str, Any]], *,
                       bench: Mapping[str, Mapping[date, float]], generated: str,
                       start_default: date) -> str:
    """포워드 페이퍼 리더보드. total return(단위장부) 내림차순. 최근 10일 일간수익표 포함."""
    rows = sorted(summaries, key=_sort_key, reverse=True)
    L: list[str] = [
        "# Aggressive Forward Paper Lab — Leaderboard",
        "",
        f"- Generated: `{generated}`",
        "- **PAPER (forward, live prices going forward). Real orders: none, ever.** "
        "Signal at close t → fill at t+1 (close, or open where noted). Cash earns 0%.",
        "- Two books per strategy: **unit $1,000** (clean performance) and **real $36** "
        "(true small-scale Toss fees). Leaderboard ranks by the unit book's total return.",
        "- Fees: Toss exact — buys ≤$10 free, sells 0.1% + SEC/TAF minimums.",
        f"- Lane A objective (docs/gate_v2_spec.md 부록 v3): cost-adjusted CAGR; bankruptcy "
        f"guard only (unit MDD > −95% ⇒ FAIL). Live conversion needs cumulative return > QQQ "
        f"over ≥3 months + 0 ops errors + user approval.",
        "",
        "## Strategies — grouped by real-account executability, sorted by unit-book total return",
        "",
    ]
    _hdr = ("| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | "
            "Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |")
    _sep = "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    if not rows:
        L += [_hdr, _sep, "| (no strategies) | | | | | | | | | | | | | |"]
    for gkey in _GROUP_ORDER:
        grp = [s for s in rows if s.get("group", "leverage") == gkey]
        if not grp:
            continue
        L += [f"### {GROUP_LABELS[gkey]} ({len(grp)})", "", _hdr, _sep]
        for s in grp:
            L.append(_leaderboard_row(s, bench=bench, start_default=start_default))
        L.append("")

    L += _daily_returns_table(rows, last_n=10)
    L += [
        "", "## Notes", "",
        "- `qqq_bh` / `tqqq_bh` are benchmark strategies run through the same broker "
        "(so their small fee drag is visible); the *vs QQQ/TQQQ B&H* columns are the raw "
        "adjusted-close buy&hold over each strategy's own live window.",
        "- Dividends are reflected via adjusted-close cache for single names/ETFs where the "
        "source provides them; leveraged ETFs (TQQQ/SQQQ) use their own listed history.",
        "- MaxDD/CAGR are on the unit book. Sharpe/MDD are reported but are NOT a fail reason "
        "in Lane A — only the −95% bankruptcy guard is.",
        "- New strategies plug in via `src/toss_trader/paperlab_strategies/` "
        "(hook for docs/aggressive_strategy_catalog.md candidates).",
        "",
    ]
    contrib_rows = [s for s in rows if s.get("real_contributed", 0.0) > 0]
    if contrib_rows:
        L += ["- **Contributions**: " + ", ".join(
            f"`{s['name']}` +${s['real_contributed']:.0f}" for s in contrib_rows)
            + " added to the **$36 book only** (monthly, first session of each month; the $1k "
            "book stays contribution-free). The $36 *Total* is therefore money-weighted "
            "(deposits inflate it) and is not directly comparable to the $1k *Total*.", ""]
    if rows and all(s["days_live"] == 0 for s in rows):
        banner = (f"> ⏳ **Awaiting first forward session.** All books initialized at "
                  f"`{start_default.isoformat()}`; no keyless daily close ≥ start date has "
                  f"arrived yet (the cache ends earlier). Numbers populate once "
                  f"`scripts/paperlab_run.py` sees a session on/after the start date. For a "
                  f"historical sanity check, see `reports/paperlab_backfill.md` (BACKTEST).")
        idx = next(i for i, ln in enumerate(L) if ln.startswith("## Strategies"))
        L[idx:idx] = [banner, ""]
    return "\n".join(L)


def _leaderboard_row(s: Mapping[str, Any], *, bench: Mapping[str, Mapping[date, float]],
                     start_default: date) -> str:
    um: BookMetrics = s["unit"]
    rm: BookMetrics = s["real"]
    first = date.fromisoformat(s["first_session"]) if s["first_session"] else start_default
    last = date.fromisoformat(s["last_session"]) if s["last_session"] else start_default
    qqq = buy_hold_return(bench.get("QQQ", {}), first, last)
    tqqq = buy_hold_return(bench.get("TQQQ", {}), first, last)
    return (
        f"| `{s['name']}` | {s['fill_on']} | {s['start_date']} | {s['days_live']} | "
        f"{_pct(um.total_return)} | {_pct(um.today_return)} | {_pct(um.cagr)} | "
        f"{_pct(um.max_dd)} | {um.trades} | ${um.fees:.3f} | {_pct(rm.total_return)} | "
        f"${rm.fees:.3f} | {_pct(qqq)} | {_pct(tqqq)} |")


def _sort_key(s: Mapping[str, Any]) -> float:
    tr = s["unit"].total_return
    return tr if tr is not None else -math.inf


def _daily_returns_table(rows: Sequence[dict[str, Any]], *, last_n: int) -> list[str]:
    # 최근 last_n 거래일에 등장한 날짜 합집합.
    all_dates: set[date] = set()
    per: dict[str, dict[date, float]] = {}
    for s in rows:
        curve = s["curve"]
        rets: dict[date, float] = {}
        for i in range(1, len(curve)):
            d0, v0 = curve[i - 1]
            d1, v1 = curve[i]
            if v0 > 0:
                rets[d1] = v1 / v0 - 1.0
        per[s["name"]] = rets
        all_dates.update(rets)
    if not all_dates:
        return ["", "## Daily returns (last 10 sessions)", "", "- (no forward sessions yet.)"]
    dates = sorted(all_dates)[-last_n:]
    header = "| Date | " + " | ".join(f"`{s['name']}`" for s in rows) + " |"
    sep = "|---|" + "".join("---:|" for _ in rows)
    out = ["", f"## Daily returns (last {last_n} sessions)", "", header, sep]
    for d in dates:
        cells = " | ".join(_pct(per[s["name"]].get(d)) for s in rows)
        out.append(f"| {d.isoformat()} | {cells} |")
    return out


def render_backfill(summaries: Sequence[dict[str, Any]], *,
                    bench: Mapping[str, Mapping[date, float]], generated: str,
                    backfill_from: date) -> str:
    """백필(BACKTEST) 리포트. 포워드 페이퍼가 아니라 과거 재현임을 명확히 라벨."""
    rows = sorted(summaries, key=lambda s: (s["unit"].cagr if s["unit"].cagr is not None else -math.inf),
                  reverse=True)
    L: list[str] = [
        "# Aggressive Forward Paper Lab — BACKFILL REPLAY (BACKTEST, NOT PAPER)",
        "",
        f"- Generated: `{generated}`",
        f"- **⚠️ BACKTEST**: historical replay from `{backfill_from.isoformat()}` on keyless "
        f"daily closes, for sanity only. This is NOT forward paper evidence and NOT a Lane A "
        f"PASS. Same engine, same Toss fees, same no-look-ahead (signal t → fill t+1).",
        "- Books: unit $1,000 and real $36. Cash 0%. Leveraged ETFs use listed history "
        "(no synthetic pre-inception here).",
        "",
        "| Strategy | Grp | Fill | Sessions | Years | CAGR (u$1k) | Total | MaxDD | Trades | "
        "Fees(u) | Final(u) | Final($36) | QQQ B&H CAGR | TQQQ B&H CAGR |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    _gtag = {"retail": "실계좌", "leverage": "레버ETP", "explore": "탐색"}
    for s in rows:
        um: BookMetrics = s["unit"]
        rm: BookMetrics = s["real"]
        first = date.fromisoformat(s["first_session"]) if s["first_session"] else backfill_from
        last = date.fromisoformat(s["last_session"]) if s["last_session"] else backfill_from
        years = s["calendar_days"] / 365.25 if s["calendar_days"] else 0.0
        qqq_c = _bh_cagr(bench.get("QQQ", {}), first, last)
        tqqq_c = _bh_cagr(bench.get("TQQQ", {}), first, last)
        L.append(
            f"| `{s['name']}` | {_gtag.get(s.get('group', 'leverage'), '?')} | {s['fill_on']} | "
            f"{s['days_live']} | {years:.1f} | "
            f"{_pct(um.cagr)} | {_pct(um.total_return)} | {_pct(um.max_dd)} | {um.trades} | "
            f"${um.fees:.2f} | ${um.equity:.2f} | ${rm.equity:.2f} | "
            f"{_pct(qqq_c)} | {_pct(tqqq_c)} |")
    L += [
        "", "## Reading this", "",
        "- Lane A gate (design+holdout CAGR > QQQ, neighbor robustness, 2× cost) is evaluated "
        "elsewhere; this replay is a smoke test that the strategies compute and trade sanely.",
        "- A leveraged/inverse strategy can post a large CAGR here and still be rejected — "
        "forward paper (≥3 months, live prices) is the real judge.",
        "",
    ]
    return "\n".join(L)


def _bh_cagr(closes: Mapping[date, float], start: date, end: date) -> float | None:
    r = buy_hold_return(closes, start, end)
    if r is None:
        return None
    days = (end - start).days
    if days <= 0:
        return None
    return (1.0 + r) ** (365.25 / days) - 1.0
