"""research — 빠른(순수 리스트 기반) 벡터화 지향 리서치 백테스터.

`gate.py`(전략 채택 게이트)가 **평범한 파이썬 리스트**(일수익/자본곡선/거래PnL)만 받도록
설계된 것과 짝을 이룬다. 이 모듈은 그 리스트들을 **만들어 내는** 쪽이다: 목표비중
스케줄·이벤트 거래·DCA 오버레이를 비용 티어(사양 §7)와 체결 지연(사양 §2.3)까지
반영해 돌리고, 게이트가 바로 먹을 수 있는 순수익 시계열·자본곡선·현금흐름을 낸다.

설계 원칙:
- 외부 의존성 없음(stdlib only). 넘파이 없이 리스트로 벡터화 '정신'만 취한다.
- 데이터 로딩과 분리: `panel_closes: dict[sym, list[float]]`(=`dates`에 정렬된 종가)와
  `dates: list[date]`만 받는다. `histdata.load_panel/align_panel`이 만든 공통거래일 패널을
  그대로 흘려 넣으면 된다(이 모듈은 histdata를 수정하지 않고 소비만 한다).
- **look-ahead 금지**: close t 에서 계산한 신호는 close t+exec_lag(기본 1) 또는 다음 시가에
  체결된다. 모든 롤링 헬퍼(sma/ema/rsi/zscore/percentile_rank …)는 인덱스 t 출력이
  입력 ≤ t 만 참조하도록 인과적으로 구현했다. `lookahead_guard`로 전략 신호를 검증한다.
- **비용 티어(사양 §7)**: 매매비 = 수수료 + 반호가(심볼 티어별) + 슬리피지. FX 스프레드는
  **환전(펀딩) 비용**이라 매매가 아니라 신규자본(입금)에만 부과한다(USD 상주 회전은 FX 0).

주요 API:
- `run_weights(...)`  : 저회전 배분(레인1) — 일별 gross/net 수익·자본·회전율·비용.
- `run_trades(...)`   : 이벤트/인트라데이(레인2·3) — 거래별 순PnL + 일별 수익.
- `run_dca_overlay(...)`: 월적립 DCA(가치가중) — 자본곡선 + gate.xirr용 현금흐름(FX는 입금에).
- 헬퍼: sma/ema/rsi(Wilder)/atr/realized_vol/rolling_max/min/zscore/percentile_rank,
  월말·월초·turn-of-month 플래그, "월 마지막 거래일"(거래일 리스트 기반 → 미 공휴일 자동반영).
- `lookahead_guard`   : 미래가격을 교란해 t 이하 목표비중 불변을 강제하는 테스트 유틸.
"""
from __future__ import annotations

import bisect
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

__all__ = [
    # 비용
    "CostSpec", "DEFAULT_TIER_HALF_SPREAD_BPS", "PROMO_COMMISSION_BPS",
    "STANDARD_COMMISSION_BPS",
    "TIER_ETF", "TIER_LARGE_CAP", "TIER_LEVERAGED_ETF", "TIER_SMALL_HOT",
    # 백테스터
    "WeightsResult", "run_weights",
    "Trade", "TradesResult", "run_trades",
    "DcaOverlayResult", "run_dca_overlay",
    # 헬퍼 지표
    "to_returns", "sma", "ema", "rsi", "atr", "realized_vol",
    "rolling_max", "rolling_min", "zscore", "percentile_rank",
    # 캘린더
    "month_start_flags", "month_end_flags", "turn_of_month_flags",
    "first_trading_day_of_month", "last_trading_day_of_month",
    "cash_rate_from_prices", "cash_rate_from_annual",
    # look-ahead
    "LookaheadLeak", "lookahead_guard",
]

BPS = 1e-4

# 심볼 유동성 티어(사양 §7: 반호가 half-spread 편도 bps)
TIER_ETF = "etf"                    # 메가캡 ETF (SPY/QQQ …)   1bp
TIER_LARGE_CAP = "large_cap"        # 유동 대형주               3bp
TIER_LEVERAGED_ETF = "leveraged_etf"  # 레버리지 ETF (TQQQ …)   2bp
TIER_SMALL_HOT = "small_hot"        # 중소형/변동성/핫          15bp

DEFAULT_TIER_HALF_SPREAD_BPS: dict[str, float] = {
    TIER_ETF: 1.0,
    TIER_LARGE_CAP: 3.0,
    TIER_LEVERAGED_ETF: 2.0,
    TIER_SMALL_HOT: 15.0,
}

# 수수료(편도, bps). US 표준 0.25%(무기한). 0.1% 프로모는 endDate 2026-06-30 만료(costs.py 참조).
STANDARD_COMMISSION_BPS = 25.0
PROMO_COMMISSION_BPS = 10.0


# ── 내부 통계 헬퍼 ───────────────────────────────────────────────────────────
def _mean(x: Sequence[float]) -> float:
    return sum(x) / len(x)


def _std(x: Sequence[float], ddof: int = 1) -> float:
    n = len(x)
    if n - ddof <= 0:
        return 0.0
    m = sum(x) / n
    return math.sqrt(sum((v - m) ** 2 for v in x) / (n - ddof))


def _as_date(x: date | datetime) -> date:
    return x.date() if isinstance(x, datetime) else x


# ── 비용 스펙 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CostSpec:
    """소액 매매 비용 스펙 (사양 §7). 단위는 전부 bps(편도).

    매매비(편도) = commission_bps + half_spread(심볼 티어) + slippage_bps.
      - FX는 매매비에 **포함하지 않는다**: USD 상주자본 회전은 환전을 다시 하지 않는다.
      - fx_bps는 신규자본(입금)에만 부과된다 → `fx_cost(deposit)` / `run_dca_overlay`에서만 사용.
    half_spread_bps: 심볼→반호가 bps 매핑. 없는 심볼은 default_half_spread_bps(기본 대형주 3bp).
    commission_bps 기본 25(US 표준 0.25%). 프로모(0.1%=10bps)는 `CostSpec.promo(...)` 프리셋.
    """
    commission_bps: float = STANDARD_COMMISSION_BPS
    slippage_bps: float = 5.0
    fx_bps: float = 20.0
    half_spread_bps: Mapping[str, float] = field(default_factory=dict)
    default_half_spread_bps: float = DEFAULT_TIER_HALF_SPREAD_BPS[TIER_LARGE_CAP]

    # ── 구성 프리셋 ──
    @classmethod
    def promo(cls, **kw) -> "CostSpec":
        """토스 US 수수료 프로모(0.1%=10bps) 프리셋. 나머지 인자는 그대로 전달."""
        kw.setdefault("commission_bps", PROMO_COMMISSION_BPS)
        return cls(**kw)

    @classmethod
    def from_tiers(cls, symbol_tiers: Mapping[str, str], *,
                   commission_bps: float = STANDARD_COMMISSION_BPS,
                   slippage_bps: float = 5.0, fx_bps: float = 20.0,
                   tier_half_spread: Mapping[str, float] | None = None,
                   default_half_spread_bps: float | None = None) -> "CostSpec":
        """심볼→티어 매핑에서 반호가 표를 펼쳐 CostSpec을 만든다.

        예: from_tiers({"SPY": TIER_ETF, "TQQQ": TIER_LEVERAGED_ETF, "HOTX": TIER_SMALL_HOT}).
        tier_half_spread로 티어별 bps를 덮어쓸 수 있다(기본 DEFAULT_TIER_HALF_SPREAD_BPS).
        """
        table = dict(DEFAULT_TIER_HALF_SPREAD_BPS)
        if tier_half_spread:
            table.update(tier_half_spread)
        hs: dict[str, float] = {}
        for sym, tier in symbol_tiers.items():
            if tier not in table:
                raise ValueError(f"알 수 없는 유동성 티어: {tier!r} (가능: {sorted(table)})")
            hs[sym] = table[tier]
        default = (default_half_spread_bps if default_half_spread_bps is not None
                   else DEFAULT_TIER_HALF_SPREAD_BPS[TIER_LARGE_CAP])
        return cls(commission_bps=commission_bps, slippage_bps=slippage_bps,
                   fx_bps=fx_bps, half_spread_bps=dict(hs),
                   default_half_spread_bps=default)

    # ── 조회 ──
    def half_spread_for(self, sym: str) -> float:
        return self.half_spread_bps.get(sym, self.default_half_spread_bps)

    def trade_bps(self, sym: str) -> float:
        """매매비(편도, bps) = 수수료 + 반호가(심볼) + 슬리피지. FX 제외."""
        return self.commission_bps + self.half_spread_for(sym) + self.slippage_bps

    def trade_cost(self, sym: str, notional: float) -> float:
        """매매 금액(절대값)에 대한 편도 매매비(USD). FX는 포함하지 않는다."""
        return abs(notional) * self.trade_bps(sym) * BPS

    def roundtrip_bps(self, sym: str) -> float:
        return 2.0 * self.trade_bps(sym)

    def fx_cost(self, deposit: float) -> float:
        """신규자본(입금)에 대한 환전(펀딩) 비용(USD). 매매가 아니라 입금에만 부과(사양 §7)."""
        return abs(deposit) * self.fx_bps * BPS

    def stress(self, mult: float) -> "CostSpec":
        """모든 비용 요율을 mult배 한 스트레스 스펙(사양 §4.3). 반호가/FX 포함 전 항목 스케일."""
        return CostSpec(
            commission_bps=self.commission_bps * mult,
            slippage_bps=self.slippage_bps * mult,
            fx_bps=self.fx_bps * mult,
            half_spread_bps={k: v * mult for k, v in self.half_spread_bps.items()},
            default_half_spread_bps=self.default_half_spread_bps * mult,
        )


# ── run_weights (레인1: 저회전 배분) ─────────────────────────────────────────
@dataclass
class WeightsResult:
    """목표비중 백테스트 결과. 모든 일별 시계열은 `dates`에 정렬(길이 N).

    net_returns[0]=gross_returns[0]=0.0 (시드일 — 직전일이 없음). 게이트에 넣을 때는 시드일을
    포함해도(첫날 수익 0) 무방하나, 순수 수익 스트림만 원하면 `.net_stream()`을 쓴다.
    """
    dates: list[date]
    equity: list[float]            # 종가 자본(매매 후), 길이 N
    gross_returns: list[float]     # 시장수익(비용 전), 길이 N, [0]=0
    net_returns: list[float]       # 순수익(비용 후) = eq[t]/eq[t-1]-1, 길이 N, [0]=0
    turnover: list[float]          # 매매 노셔널/매매직전자본, 길이 N
    costs: list[float]             # 일별 매매비(USD), 길이 N
    trades: list[int]              # 일별 체결 레그 수, 길이 N
    total_cost: float
    trade_count: int

    @property
    def final_equity(self) -> float:
        return self.equity[-1] if self.equity else 0.0

    @property
    def equity_curve(self) -> list[tuple[date, float]]:
        """(date, equity) 튜플 — DcaResult.equity_curve 호환(gate 소비용)."""
        return list(zip(self.dates, self.equity))

    def net_stream(self) -> list[float]:
        """시드일(0) 제외 순수익 스트림 — gate.sharpe/deflated_sharpe_ratio 등에 바로 사용."""
        return self.net_returns[1:]

    def gross_stream(self) -> list[float]:
        return self.gross_returns[1:]


def _check_panel(panel: Mapping[str, Sequence[float]], dates: Sequence[date],
                 name: str) -> None:
    n = len(dates)
    for s, series in panel.items():
        if len(series) != n:
            raise ValueError(
                f"{name}[{s!r}] 길이 {len(series)} != len(dates) {n} — dates에 정렬된 리스트여야 함")


def run_weights(panel_closes: Mapping[str, Sequence[float]], dates: Sequence[date],
                target_weights: Sequence[Mapping[str, float]], *,
                exec_lag: int = 1, rebalance_band: float = 0.0,
                cost: CostSpec | None = None,
                cash_rate: Sequence[float] | None = None,
                exec_price: str = "close",
                panel_opens: Mapping[str, Sequence[float]] | None = None,
                start_equity: float = 1.0) -> WeightsResult:
    """목표비중 스케줄을 비용·체결지연까지 반영해 돌린다(레인1 저회전 배분).

    - close t 에서 계산된 target_weights[t]는 close t+exec_lag(기본 1)에 체결된다(사양 §2.3).
      exec_price='open' 이고 panel_opens가 주어지면 그 날의 **시가**에 체결한다.
    - rebalance_band: |목표비중 − 현재비중| ≤ band 인 심볼은 매매 생략(무매매 밴드, 회전/비용 절약).
    - 비용: 매매마다 심볼별 trade_bps(수수료+반호가+슬리피지). FX는 부과하지 않는다(USD 상주).
    - 비중합 ≤ 1(롱온리) 가정. 잔여(1−Σw)는 현금이며 cash_rate가 있으면 그만큼 이자 수익.
      cash_rate는 dates에 정렬된 **일간** 현금수익률 리스트(예 BIL 일수익/DTB3 환산, None이면 0).

    반환 WeightsResult: 일별 gross/net 수익, 자본곡선, 회전율, 비용, 체결 레그 수, 총비용.
    """
    cost = cost or CostSpec()
    syms = list(panel_closes)
    n = len(dates)
    if len(target_weights) != n:
        raise ValueError(f"target_weights 길이 {len(target_weights)} != len(dates) {n}")
    _check_panel(panel_closes, dates, "panel_closes")
    if cash_rate is not None and len(cash_rate) != n:
        raise ValueError(f"cash_rate 길이 {len(cash_rate)} != len(dates) {n}")
    use_open = exec_price == "open" and panel_opens is not None
    if use_open:
        _check_panel(panel_opens, dates, "panel_opens")
    # 목표비중이 참조하는 심볼은 모두 패널에 있어야 한다(오타/버그 조기 발견).
    universe = set(syms)
    for tw in target_weights:
        extra = set(tw) - universe
        if extra:
            raise ValueError(f"target_weights가 패널에 없는 심볼 참조: {sorted(extra)}")

    equity = [0.0] * n
    gross = [0.0] * n
    net = [0.0] * n
    turnover = [0.0] * n
    costs = [0.0] * n
    legs = [0] * n

    val: dict[str, float] = {s: 0.0 for s in syms}
    cash = float(start_equity)

    def rebalance(equity_ref: float, tw: Mapping[str, float]) -> tuple[float, float, int]:
        """드리프트된 val을 목표비중으로 조정(밴드 적용). (비용, 매매노셔널, 레그수) 반환.

        매매하지 않은 심볼의 val은 유지되고, 현금이 잔여를 흡수한다. 비용은 현금에서 차감.
        """
        nonlocal cash
        if equity_ref <= 0:
            return 0.0, 0.0, 0
        c = 0.0
        traded = 0.0
        nl = 0
        for s in syms:
            target = tw.get(s, 0.0)
            cur = val[s]
            cur_w = cur / equity_ref
            if abs(target - cur_w) > rebalance_band:
                new_val = target * equity_ref
                notional = abs(new_val - cur)
                traded += notional
                c += notional * cost.trade_bps(s) * BPS
                val[s] = new_val
                nl += 1
        cash = equity_ref - sum(val.values()) - c
        return c, traded, nl

    # ── 시드일(t=0) ──
    if 0 - exec_lag >= 0:                # exec_lag==0 이면 첫날 종가에 즉시 체결
        c0, tr0, nl0 = rebalance(start_equity, target_weights[0])
        costs[0], legs[0] = c0, nl0
        turnover[0] = tr0 / start_equity if start_equity > 0 else 0.0
    equity[0] = sum(val.values()) + cash
    eq_prev = equity[0]

    for t in range(1, n):
        cr = cash_rate[t] if cash_rate is not None else 0.0
        cash *= (1.0 + cr)                # 현금 이자(하루치)
        sig_idx = t - exec_lag
        exec_day = sig_idx >= 0
        c = tr = 0.0
        nl = 0
        eq_reb = 0.0

        if use_open and exec_day:
            # seg1: 전일 종가 → 당일 시가
            for s in syms:
                pc = panel_closes[s][t - 1]
                if pc > 0:
                    val[s] *= panel_opens[s][t] / pc
            eq_reb = sum(val.values()) + cash
            c, tr, nl = rebalance(eq_reb, target_weights[sig_idx])
            # seg2: 당일 시가 → 당일 종가
            for s in syms:
                op = panel_opens[s][t]
                if op > 0:
                    val[s] *= panel_closes[s][t] / op
        else:
            for s in syms:
                pc = panel_closes[s][t - 1]
                if pc > 0:
                    val[s] *= panel_closes[s][t] / pc
            eq_reb = sum(val.values()) + cash
            if exec_day:
                c, tr, nl = rebalance(eq_reb, target_weights[sig_idx])

        eq_now = sum(val.values()) + cash
        equity[t] = eq_now
        costs[t], legs[t] = c, nl
        turnover[t] = (tr / eq_reb) if eq_reb > 0 else 0.0
        net[t] = (eq_now / eq_prev - 1.0) if eq_prev > 0 else 0.0
        gross[t] = net[t] + (c / eq_prev if eq_prev > 0 else 0.0)
        eq_prev = eq_now

    return WeightsResult(
        dates=list(dates), equity=equity, gross_returns=gross, net_returns=net,
        turnover=turnover, costs=costs, trades=legs,
        total_cost=sum(costs), trade_count=sum(legs),
    )


# ── run_trades (레인2·3: 이벤트/인트라데이) ──────────────────────────────────
@dataclass
class Trade:
    """단일 거래(롱 기본). notional은 자본 대비 노셔널 분수(예 0.1=자본의 10%)."""
    entry_dt: date | datetime
    entry_price: float
    exit_dt: date | datetime
    exit_price: float
    side: str = "long"
    notional: float = 1.0
    symbol: str | None = None


@dataclass
class TradesResult:
    """이벤트/인트라데이 결과. pnls는 gate.trade_tstat/trade_pnl_bootstrap_ci에 바로 사용."""
    pnls: list[float]              # 거래별 순PnL(비용 후, 자본단위)
    gross_pnls: list[float]        # 거래별 총PnL(비용 전)
    costs: list[float]             # 거래별 비용
    dates: list[date]              # 일별 시계열용 청산일(오름차순·유니크)
    daily_returns: list[float]     # 청산일에 실현PnL을 귀속한 일별 순수익
    equity: list[float]            # 일별 자본(청산 반영), 길이 = len(dates)
    total_cost: float
    n_trades: int
    capital: float

    @property
    def net_pnl(self) -> float:
        return sum(self.pnls)

    @property
    def equity_curve(self) -> list[tuple[date, float]]:
        return list(zip(self.dates, self.equity))


def run_trades(trades: Sequence[Trade], *, cost: CostSpec | None = None,
               capital: float = 1.0) -> TradesResult:
    """이벤트/인트라데이 거래 리스트 → 거래별 순PnL + 일별 수익(사양 §3.5, §8.2/§8.3).

    각 거래: 총수익 = exit/entry−1(롱; 숏이면 부호반전). 진입 노셔널 = notional·capital.
    비용 = (진입 노셔널 + 청산 노셔널)·trade_bps(심볼). FX 없음(티어B USD 상주, 사양 §7).
    일별 수익: 각 거래의 순PnL을 **청산일**에 귀속해 capital 위에 복리로 쌓는다(보유 중 MTM 미반영 —
    이벤트 연구용 근사임을 명시). 거래 자기상관/짧은표본 검정은 pnls(거래단위)로 하는 게 원칙.
    """
    cost = cost or CostSpec()
    pnls: list[float] = []
    gross_pnls: list[float] = []
    cost_list: list[float] = []
    by_date: dict[date, float] = {}

    for tr in trades:
        if tr.entry_price <= 0:
            raise ValueError(f"진입가가 0 이하: {tr}")
        gross_ret = tr.exit_price / tr.entry_price - 1.0
        if tr.side.lower() in ("short", "sell"):
            gross_ret = -gross_ret
        entry_notional = tr.notional * capital
        exit_notional = entry_notional * (tr.exit_price / tr.entry_price)
        tb = cost.trade_bps(tr.symbol) * BPS
        c = abs(entry_notional) * tb + abs(exit_notional) * tb
        gross_pnl = entry_notional * gross_ret
        net = gross_pnl - c
        pnls.append(net)
        gross_pnls.append(gross_pnl)
        cost_list.append(c)
        d = _as_date(tr.exit_dt)
        by_date[d] = by_date.get(d, 0.0) + net

    dts = sorted(by_date)
    daily_returns: list[float] = []
    equity: list[float] = []
    eq = float(capital)
    for d in dts:
        realized = by_date[d]
        r = realized / eq if eq > 0 else 0.0
        eq += realized
        daily_returns.append(r)
        equity.append(eq)

    return TradesResult(
        pnls=pnls, gross_pnls=gross_pnls, costs=cost_list, dates=dts,
        daily_returns=daily_returns, equity=equity, total_cost=sum(cost_list),
        n_trades=len(trades), capital=float(capital),
    )


# ── run_dca_overlay (월적립 DCA, 가치가중) ───────────────────────────────────
@dataclass
class DcaOverlayResult:
    """월적립 DCA 결과. cashflows는 gate.xirr에 바로 넣을 수 있다(입금 음/최종 양)."""
    dates: list[date]
    equity: list[float]                          # 일별 평가액, 길이 N
    deposits: list[tuple[date, float]]           # (날짜, 입금액>0)
    cashflows: list[tuple[date, float]]          # gate.xirr용 (입금 −, 최종청산 +)
    total_deposited: float
    total_cost: float                            # 매매비 + FX 합
    total_fx_cost: float
    n_buys: int
    final_value: float

    @property
    def equity_curve(self) -> list[tuple[date, float]]:
        """(date, equity) — gate.cashflows_from_dca / DcaResult.equity_curve 호환."""
        return list(zip(self.dates, self.equity))

    @property
    def profit(self) -> float:
        return self.final_value - self.total_deposited


def _dca_cashflows(deposits: Sequence[tuple[date, float]], final_value: float,
                   final_date: date) -> list[tuple[date, float]]:
    """입금(양수) 스케줄 + 최종 평가액 → XIRR용 현금흐름(입금 −, 최종 +). gate.xirr 호환."""
    flows: list[tuple[date, float]] = [(d, -abs(amt)) for d, amt in deposits]
    flows.append((final_date, float(final_value)))
    return flows


def run_dca_overlay(panel_closes: Mapping[str, Sequence[float]], dates: Sequence[date],
                    target_weights: Sequence[Mapping[str, float]], *,
                    monthly_usd: float = 35.0, initial_usd: float = 32.0,
                    cost: CostSpec | None = None,
                    cash_rate: Sequence[float] | None = None,
                    exec_lag: int = 0) -> DcaOverlayResult:
    """월 첫 거래일에 입금해 전략 목표비중대로 **매수전용**(buy-only) 투자하는 DCA 오버레이.

    - 입금: 시드 initial_usd(첫날) + 매월 첫 거래일 monthly_usd(사양 §0의 실계좌 현금흐름).
      FX 스프레드는 **입금(신규자본)에만** 부과(사양 §7 티어A). 매매비는 매수마다 부과.
    - 투자: 입금일에 (총평가액×목표비중) 미달분을 신규 현금으로 매수(매도 없음 → 저회전).
      exec_lag=0 이면 입금일 종가 신호로 그날 체결(레인1 배분은 종가체결 근사 허용, 사양 §2.3).
    - 반환: 일별 자본곡선 + gate.xirr용 현금흐름(입금 −, 최종 평가액 +). FX 드래그는 최종
      평가액에 반영되므로 XIRR에 정직하게 나타난다(현금흐름은 명목 입금액).
    """
    cost = cost or CostSpec()
    syms = list(panel_closes)
    n = len(dates)
    if len(target_weights) != n:
        raise ValueError(f"target_weights 길이 {len(target_weights)} != len(dates) {n}")
    _check_panel(panel_closes, dates, "panel_closes")
    if cash_rate is not None and len(cash_rate) != n:
        raise ValueError(f"cash_rate 길이 {len(cash_rate)} != len(dates) {n}")
    universe = set(syms)
    for tw in target_weights:
        extra = set(tw) - universe
        if extra:
            raise ValueError(f"target_weights가 패널에 없는 심볼 참조: {sorted(extra)}")

    starts = month_start_flags(dates)
    val: dict[str, float] = {s: 0.0 for s in syms}
    cash = 0.0
    equity_series: list[float] = []
    deposits: list[tuple[date, float]] = []
    total_deposited = 0.0
    total_cost = 0.0
    total_fx = 0.0
    n_buys = 0

    def buy_toward(equity_ref: float, tw: Mapping[str, float]) -> tuple[float, int]:
        """가용 현금으로 미달분이 큰 심볼부터 목표비중까지 매수(매도 없음). (비용, 레그수)."""
        nonlocal cash
        c_paid = 0.0
        nb = 0
        order = sorted(syms, key=lambda s: tw.get(s, 0.0) * equity_ref - val[s],
                       reverse=True)
        for s in order:
            if cash <= 0:
                break
            need = tw.get(s, 0.0) * equity_ref - val[s]
            if need <= 0:
                continue
            tb = cost.trade_bps(s) * BPS
            spend = need
            if spend * (1.0 + tb) > cash:      # 현금 초과 방지(매수 노셔널 + 비용 ≤ 현금)
                spend = cash / (1.0 + tb)
            c = spend * tb
            val[s] += spend
            cash -= (spend + c)
            c_paid += c
            nb += 1
        return c_paid, nb

    for t in range(n):
        if t > 0:                              # 드리프트(현금 이자 + 자산 수익)
            cr = cash_rate[t] if cash_rate is not None else 0.0
            cash *= (1.0 + cr)
            for s in syms:
                pc = panel_closes[s][t - 1]
                if pc > 0:
                    val[s] *= panel_closes[s][t] / pc

        dep = 0.0
        if t == 0:
            dep += initial_usd
        if starts[t]:
            dep += monthly_usd
        if dep > 0:
            cash += dep
            deposits.append((dates[t], dep))
            total_deposited += dep
            fx = cost.fx_cost(dep)             # FX는 입금에만
            cash -= fx
            total_fx += fx
            total_cost += fx
            sig = t - exec_lag
            if sig >= 0:
                equity_ref = sum(val.values()) + cash
                c, nb = buy_toward(equity_ref, target_weights[sig])
                total_cost += c
                n_buys += nb

        equity_series.append(sum(val.values()) + cash)

    final_value = equity_series[-1] if equity_series else 0.0
    cashflows = _dca_cashflows(deposits, final_value, dates[-1]) if deposits else []
    return DcaOverlayResult(
        dates=list(dates), equity=equity_series, deposits=deposits,
        cashflows=cashflows, total_deposited=total_deposited,
        total_cost=total_cost, total_fx_cost=total_fx, n_buys=n_buys,
        final_value=final_value,
    )


# ── 헬퍼 지표 (전부 인과적: 인덱스 t 출력은 입력 ≤ t 만 참조) ─────────────────
def to_returns(prices: Sequence[float], *, log: bool = False) -> list[float]:
    """가격 → 단순(또는 로그) 일간수익률. 길이 = len(prices), [0]=0.0(직전일 없음)."""
    out = [0.0]
    for t in range(1, len(prices)):
        p0, p1 = prices[t - 1], prices[t]
        if p0 <= 0:
            out.append(0.0)
        elif log:
            out.append(math.log(p1 / p0))
        else:
            out.append(p1 / p0 - 1.0)
    return out


def sma(x: Sequence[float], window: int) -> list[float | None]:
    """단순이동평균. t<window−1 은 None(데이터 부족). O(N)."""
    n = len(x)
    out: list[float | None] = [None] * n
    if window <= 0:
        raise ValueError("window는 양수")
    s = 0.0
    for t in range(n):
        s += x[t]
        if t >= window:
            s -= x[t - window]
        if t >= window - 1:
            out[t] = s / window
    return out


def ema(x: Sequence[float], window: int) -> list[float]:
    """지수이동평균(α=2/(window+1)). ema[0]=x[0] 시드로 인과적. 길이 = len(x)."""
    if window <= 0:
        raise ValueError("window는 양수")
    if not x:
        return []
    alpha = 2.0 / (window + 1.0)
    out = [float(x[0])]
    for t in range(1, len(x)):
        out.append(alpha * x[t] + (1.0 - alpha) * out[-1])
    return out


def rsi(x: Sequence[float], window: int = 14) -> list[float | None]:
    """Wilder RSI. t<window 는 None. 시드 avg = 첫 window개 gain/loss 평균."""
    n = len(x)
    out: list[float | None] = [None] * n
    if window <= 0 or n <= window:
        return out
    gains = 0.0
    losses = 0.0
    for t in range(1, window + 1):            # 첫 window개 변화로 시드
        ch = x[t] - x[t - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_gain = gains / window
    avg_loss = losses / window
    out[window] = 100.0 - 100.0 / (1.0 + (avg_gain / avg_loss)) if avg_loss > 0 else 100.0
    for t in range(window + 1, n):
        ch = x[t] - x[t - 1]
        avg_gain = (avg_gain * (window - 1) + max(ch, 0.0)) / window
        avg_loss = (avg_loss * (window - 1) + max(-ch, 0.0)) / window
        out[t] = 100.0 - 100.0 / (1.0 + (avg_gain / avg_loss)) if avg_loss > 0 else 100.0
    return out


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        window: int = 14) -> list[float | None]:
    """Wilder ATR. true range의 Wilder 평활. t<window−1 은 None."""
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise ValueError("highs/lows/closes 길이 불일치")
    out: list[float | None] = [None] * n
    if window <= 0 or n == 0:
        return out
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for t in range(1, n):
        pc = closes[t - 1]
        tr[t] = max(highs[t] - lows[t], abs(highs[t] - pc), abs(lows[t] - pc))
    if n < window:
        return out
    seed = sum(tr[0:window]) / window          # 첫 window개 TR 평균으로 시드
    out[window - 1] = seed
    prev = seed
    for t in range(window, n):
        prev = (prev * (window - 1) + tr[t]) / window
        out[t] = prev
    return out


def realized_vol(returns: Sequence[float], window: int, *, annualize: bool = True,
                 ppy: int = 252, ddof: int = 1) -> list[float | None]:
    """롤링 실현변동성(수익 리스트 기준). t<window−1 은 None. annualize면 ×sqrt(ppy)."""
    n = len(returns)
    out: list[float | None] = [None] * n
    if window <= 1:
        raise ValueError("window는 2 이상")
    for t in range(window - 1, n):
        sd = _std(returns[t - window + 1:t + 1], ddof)
        out[t] = sd * math.sqrt(ppy) if annualize else sd
    return out


def rolling_max(x: Sequence[float], window: int) -> list[float | None]:
    """롤링 최대(트레일링 window). t<window−1 은 None."""
    n = len(x)
    out: list[float | None] = [None] * n
    if window <= 0:
        raise ValueError("window는 양수")
    for t in range(window - 1, n):
        out[t] = max(x[t - window + 1:t + 1])
    return out


def rolling_min(x: Sequence[float], window: int) -> list[float | None]:
    """롤링 최소(트레일링 window). t<window−1 은 None."""
    n = len(x)
    out: list[float | None] = [None] * n
    if window <= 0:
        raise ValueError("window는 양수")
    for t in range(window - 1, n):
        out[t] = min(x[t - window + 1:t + 1])
    return out


def zscore(x: Sequence[float], window: int, *, ddof: int = 1) -> list[float | None]:
    """롤링 z-score = (x[t]−mean)/std(트레일링 window). std=0 또는 부족은 None."""
    n = len(x)
    out: list[float | None] = [None] * n
    if window <= 1:
        raise ValueError("window는 2 이상")
    for t in range(window - 1, n):
        w = x[t - window + 1:t + 1]
        sd = _std(w, ddof)
        if sd > 0:
            out[t] = (x[t] - _mean(w)) / sd
    return out


def percentile_rank(x: Sequence[float]) -> list[float | None]:
    """확장창(expanding) 백분위 순위 — look-ahead 없음(사양 §3/§9).

    각 t: x[0..t] 중 x[t] 이하인 값의 비율(0~1). 첫 원소는 1개뿐이라 1.0. 오직 과거·현재만 참조.
    """
    n = len(x)
    out: list[float | None] = [None] * n
    ordered: list[float] = []
    for t in range(n):
        bisect.insort(ordered, x[t])
        rank = bisect.bisect_right(ordered, x[t])   # ≤ x[t] 개수
        out[t] = rank / (t + 1)
    return out


# ── 캘린더(거래일 리스트 기반 → 미 공휴일 자동반영) ──────────────────────────
def month_start_flags(dates: Sequence[date]) -> list[bool]:
    """각 날짜가 해당 월의 **첫 거래일**인가. 거래일 리스트에서 유도(공휴일/주말 자동제외)."""
    n = len(dates)
    out = [False] * n
    for t in range(n):
        d = _as_date(dates[t])
        if t == 0:
            out[t] = True
        else:
            p = _as_date(dates[t - 1])
            out[t] = (d.year, d.month) != (p.year, p.month)
    return out


def month_end_flags(dates: Sequence[date]) -> list[bool]:
    """각 날짜가 해당 월의 **마지막 거래일**인가(다음 거래일이 다른 월이거나 마지막)."""
    n = len(dates)
    out = [False] * n
    for t in range(n):
        d = _as_date(dates[t])
        if t == n - 1:
            out[t] = True
        else:
            nx = _as_date(dates[t + 1])
            out[t] = (d.year, d.month) != (nx.year, nx.month)
    return out


def turn_of_month_flags(dates: Sequence[date], days_before: int = 1,
                        days_after: int = 3) -> list[bool]:
    """turn-of-month 창 플래그: 각 월 마지막 days_before 거래일 + 첫 days_after 거래일(사양 §9).

    26년 ≈ 26개 표본이라 검정력 최저 — 신호 자체보다 캘린더 필터 구현용. 거래일 리스트 기반.
    """
    n = len(dates)
    out = [False] * n
    ends = [t for t, f in enumerate(month_end_flags(dates)) if f]
    starts = [t for t, f in enumerate(month_start_flags(dates)) if f]
    for e in ends:
        for k in range(max(0, days_before)):
            i = e - k
            if 0 <= i < n:
                out[i] = True
    for s in starts:
        for k in range(max(0, days_after)):
            i = s + k
            if 0 <= i < n:
                out[i] = True
    return out


def first_trading_day_of_month(dates: Sequence[date]) -> list[date]:
    """각 월의 첫 거래일 리스트."""
    return [_as_date(dates[t]) for t, f in enumerate(month_start_flags(dates)) if f]


def last_trading_day_of_month(dates: Sequence[date]) -> list[date]:
    """각 월의 마지막 거래일 리스트(미 공휴일 자동반영 — 실제 거래일에서 유도)."""
    return [_as_date(dates[t]) for t, f in enumerate(month_end_flags(dates)) if f]


def cash_rate_from_prices(closes: Sequence[float]) -> list[float]:
    """현금 프록시(예 BIL 총수익 종가) → 일간 현금수익률. 길이 = len(closes), [0]=0.0.

    run_weights/run_dca_overlay의 cash_rate 인자에 그대로 넣는다(histdata.load_fred('DTB3') 등은
    수익률 시리즈이므로 cash_rate_from_annual을 쓴다). histdata는 수정하지 않고 소비만 한다.
    """
    return to_returns(closes)


def cash_rate_from_annual(annual_pct: Sequence[float], *, trading_days: int = 252
                          ) -> list[float]:
    """연율 무위험수익률(%, 예 FRED DTB3 3개월 T-bill) → 일간 현금수익률(연율/거래일)."""
    return [max(0.0, (a / 100.0) / trading_days) for a in annual_pct]


# ── look-ahead 가드(테스트 유틸) ─────────────────────────────────────────────
class LookaheadLeak(AssertionError):
    """미래 정보 누수 감지 — t 이하 목표비중이 미래가격 교란에 반응하면 발생(사양 §2.3)."""


def lookahead_guard(signal_fn: Callable[[Mapping[str, Sequence[float]], Sequence[date]],
                                        Sequence[Mapping[str, float]]],
                    panel_closes: Mapping[str, Sequence[float]], dates: Sequence[date],
                    *, splits: Sequence[int] | None = None, factor: float = 1.7,
                    seed: int = 20240101, tol: float = 1e-9) -> bool:
    """전략 신호의 look-ahead를 강제 검증(모든 전략 테스트에서 사용, 사양 §2.3/§9).

    signal_fn(panel_closes, dates) → target_weights(dates에 정렬). 각 분할점 t에 대해 **t 이후**
    가격을 교란한 뒤 signal_fn을 재실행해, i ≤ t 의 목표비중이 **불변**인지 확인한다.
    인과적(look-ahead 없는) 신호면 접두부 계산이 동일 입력이므로 비트단위로 같다 → 통과(True).
    미래를 참조하면 i ≤ t 에서 값이 달라져 LookaheadLeak를 던진다. splits 기본은 N의 1/4·1/2·3/4.
    """
    import random as _random
    rng = _random.Random(seed)
    base = signal_fn(panel_closes, dates)
    n = len(dates)
    if splits is None:
        cand = sorted({max(1, n // 4), n // 2, max(1, (3 * n) // 4)})
        splits = [s for s in cand if 0 < s < n - 1] or [max(1, n // 2)]

    for t in splits:
        pert: dict[str, list[float]] = {s: list(v) for s, v in panel_closes.items()}
        for s, v in pert.items():
            for i in range(t + 1, len(v)):
                v[i] = v[i] * (factor + 0.1 * rng.random())   # 미래(>t)만 교란
        wp = signal_fn(pert, dates)
        m = min(len(base), len(wp), t + 1)
        for i in range(m):
            wb_i = base[i] or {}
            wp_i = wp[i] or {}
            for k in set(wb_i) | set(wp_i):
                a = float(wb_i.get(k, 0.0))
                b = float(wp_i.get(k, 0.0))
                if abs(a - b) > tol:
                    raise LookaheadLeak(
                        f"look-ahead 누수: 분할 t={t}(>{t} 가격 교란) 후 인덱스 {i}"
                        f"(날짜 {dates[i]}) 심볼 {k!r} 목표비중이 {a} → {b} 로 변함. "
                        f"신호가 미래 정보를 참조하고 있습니다.")
    return True
