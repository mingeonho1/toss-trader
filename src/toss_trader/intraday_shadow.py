"""레인 3 인트라데이 포워드 섀도 트레이딩 — 순수 함수(네트워크·파일 없음).

한 세션의 분봉(price-only)만으로, `docs/intraday_shadow_rules.md` 에 **동결**된 사전등록 규칙
(ORB-5/15, 인트라데이 모멘텀, 갭앤고)의 **가상 트레이드**를 생성한다. 실주문 없음.

설계 원칙(gate_v2_spec §2.3·§3.5):
  - 룩어헤드 금지: 시점 t 결정은 봉 ≤ t 만. 트리거 체결은 신호 봉의 **다음 봉**(next-bar).
  - 비용: 반호가(티어별) + 슬리피지 5bp + `fees.TossFeeSchedule`(≤$10 매수 무료, 매도 SEC/TAF).
  - PRICE-ONLY: close 만 사용(o=h=l=c). VWAP-반전은 거래량 필요 → 미구현.

이 모듈은 I/O 를 하지 않는다. 캐시 로딩·집계·리포트는 `scripts/intraday_shadow_run.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .fees import TossFeeSchedule

__all__ = [
    "Bar", "TradeSignal", "TIER_HALF_SPREAD_BPS", "SLIPPAGE_BPS", "DEFAULT_SIZES",
    "CORE_TIERS", "tier_for", "to_bars", "minute_of_day",
    "fill_price", "simulate_trade", "build_trade_record",
    "orb_signal", "momentum_signal", "gap_and_go_signal",
    "signals_for_symbol", "record_key", "dedup_append",
    "REGULAR_OPEN_MIN", "REGULAR_CLOSE_MIN", "TIME_EXIT_MIN",
    "MOMENTUM_SIGNAL_MIN", "MOMENTUM_ENTRY_MIN", "MOMENTUM_EXIT_MIN",
    "GAP_THRESHOLD", "GAP_HOLD_MIN",
]

# ── 동결 파라미터 ────────────────────────────────────────────────────────────
TIER_HALF_SPREAD_BPS: dict[str, float] = {"etf": 1.0, "leveraged": 2.0, "movers": 15.0}
SLIPPAGE_BPS = 5.0
DEFAULT_SIZES: tuple[float, ...] = (30.0, 1000.0)

CORE_TIERS: dict[str, str] = {"QQQ": "etf", "TQQQ": "leveraged"}

REGULAR_OPEN_MIN = 9 * 60 + 30       # 09:30 ET
REGULAR_CLOSE_MIN = 16 * 60          # 16:00 ET
TIME_EXIT_MIN = REGULAR_CLOSE_MIN - 5  # 15:55 ET (close−5min)

MOMENTUM_SIGNAL_MIN = 10 * 60        # 10:00 ET
MOMENTUM_ENTRY_MIN = 15 * 60 + 30    # 15:30 ET
MOMENTUM_EXIT_MIN = 15 * 60 + 59     # 15:59 ET

GAP_THRESHOLD = 0.05                 # +5%
GAP_HOLD_MIN = REGULAR_OPEN_MIN + 15  # 09:45 ET (첫 15분)

# 규칙 유니버스: core 심볼 vs movers 별 규칙 목록.
RULE_UNIVERSE_CORE = ("orb5_core", "orb15_core", "momentum_core")
RULE_UNIVERSE_MOVERS = ("orb5_movers", "orb15_movers", "gap_and_go_movers")


def tier_for(symbol: str) -> str:
    """심볼 → 반호가 티어. core(QQQ/TQQQ)는 고정, 그 외 movers(§1)."""
    return CORE_TIERS.get(symbol.upper(), "movers")


# ── 봉 표현 ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Bar:
    """한 분봉(price-only). ts=epoch초, et=ET tz-aware, price=체결가, regular=정규장 여부."""
    ts: int
    et: datetime
    price: float
    volume: float
    regular: bool

    @property
    def minute(self) -> int:
        """ET 자정 기준 분(hour*60+minute). 정규장 창 판정용."""
        return self.et.hour * 60 + self.et.minute


def minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _is_regular(dt: datetime) -> bool:
    return REGULAR_OPEN_MIN <= minute_of_day(dt) < REGULAR_CLOSE_MIN


def to_bars(raw) -> list[Bar]:
    """`(ts_et, price[, volume])` 튜플/시퀀스 → 정렬된 Bar 리스트.

    ts_et 는 ET tz-aware(또는 naive-ET) datetime. regular 는 09:30–16:00 ET 로 재계산.
    이미 Bar 인 항목은 그대로. price/volume 은 float 강제.
    """
    out: list[Bar] = []
    for item in raw:
        if isinstance(item, Bar):
            out.append(item)
            continue
        dt = item[0]
        if not isinstance(dt, datetime):
            raise TypeError(f"ts_et 는 datetime 이어야 함: {dt!r}")
        price = float(item[1])
        vol = float(item[2]) if len(item) > 2 and item[2] is not None else 0.0
        out.append(Bar(ts=int(dt.timestamp()), et=dt, price=price, volume=vol,
                       regular=_is_regular(dt)))
    out.sort(key=lambda b: b.ts)
    return out


def _regular(bars: list[Bar]) -> list[Bar]:
    return [b for b in bars if b.regular]


def _first_at_or_after(bars: list[Bar], minute: int) -> Bar | None:
    for b in bars:
        if b.minute >= minute:
            return b
    return None


def _last_at_or_before(bars: list[Bar], minute: int) -> Bar | None:
    found = None
    for b in bars:
        if b.minute <= minute:
            found = b
        else:
            break
    return found


# ── 트레이드 신호 ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TradeSignal:
    """가상 롱 트레이드 1건의 체결 기준(비용 적용 전). entry/exit_ref = 다음봉/예약봉 기준가."""
    rule: str
    entry_ts: int
    exit_ts: int
    entry_ref: float
    exit_ref: float
    exit_reason: str            # "stop" | "time"
    flags: tuple[str, ...] = ()


# ── 비용·수수료 ──────────────────────────────────────────────────────────────
def fill_price(ref: float, side: str, tier: str) -> float:
    """기준가에 반호가+슬리피지 적용. 매수는 불리하게 +, 매도는 −."""
    bps = TIER_HALF_SPREAD_BPS.get(tier, TIER_HALF_SPREAD_BPS["movers"]) + SLIPPAGE_BPS
    frac = bps / 1e4
    if side.upper() == "BUY":
        return ref * (1.0 + frac)
    return ref * (1.0 - frac)


def simulate_trade(entry_ref: float, exit_ref: float, tier: str, size: float,
                   schedule: TossFeeSchedule | None = None) -> dict:
    """롱 1건의 순손익. 반환: entry/exit_fill, shares, buy/sell_fee, gross/net, net_bps."""
    sched = schedule or TossFeeSchedule()
    entry_fill = fill_price(entry_ref, "BUY", tier)
    exit_fill = fill_price(exit_ref, "SELL", tier)
    shares = size / entry_fill if entry_fill > 0 else 0.0
    buy_fee = sched.order_fee("BUY", size)                       # 매수 노셔널 = size
    exit_notional = shares * exit_fill
    sell_fee = sched.order_fee("SELL", exit_notional, shares)
    gross = shares * (exit_fill - entry_fill)
    net = gross - buy_fee - sell_fee
    net_bps = (net / size * 1e4) if size > 0 else 0.0
    return {
        "size": size, "entry_fill": entry_fill, "exit_fill": exit_fill,
        "shares": shares, "buy_fee": buy_fee, "sell_fee": sell_fee,
        "gross": gross, "net": net, "net_bps": net_bps,
    }


def build_trade_record(sig: TradeSignal, *, symbol: str, session_date: str, tier: str,
                       sizes: tuple[float, ...] = DEFAULT_SIZES,
                       schedule: TossFeeSchedule | None = None) -> dict:
    """TradeSignal → trades.jsonl 레코드(크기 2종 경제성 포함). 멱등 키 = (date, rule, symbol)."""
    sched = schedule or TossFeeSchedule()
    return {
        "date": session_date,
        "rule": sig.rule,
        "symbol": symbol.upper(),
        "tier": tier,
        "entry_ts": sig.entry_ts,
        "exit_ts": sig.exit_ts,
        "entry_ref": sig.entry_ref,
        "exit_ref": sig.exit_ref,
        "exit_reason": sig.exit_reason,
        "flags": list(sig.flags),
        "sizes": {
            f"{int(s)}": simulate_trade(sig.entry_ref, sig.exit_ref, tier, s, sched)
            for s in sizes
        },
    }


# ── 규칙(순수) ───────────────────────────────────────────────────────────────
def orb_signal(bars: list[Bar], or_minutes: int, *, rule: str) -> TradeSignal | None:
    """Opening Range Breakout(롱온리). 진입=OR_high 돌파 신호의 다음 봉, 스톱=OR_low, 시간청산=15:55."""
    reg = _regular(bars)
    or_end = REGULAR_OPEN_MIN + or_minutes
    or_bars = [b for b in reg if REGULAR_OPEN_MIN <= b.minute < or_end]
    if not or_bars:
        return None
    or_high = max(b.price for b in or_bars)
    or_low = min(b.price for b in or_bars)
    post = [b for b in reg if b.minute >= or_end]
    # 진입: OR_high 를 상향돌파하는 첫 봉(신호) → 다음 봉에서 체결(next-bar, 룩어헤드 금지).
    entry_idx = None
    for i, b in enumerate(post):
        if b.price > or_high:
            entry_idx = i + 1
            break
    if entry_idx is None or entry_idx >= len(post):
        return None
    entry_bar = post[entry_idx]
    # 시간청산 봉(15:55 ET) — 진입 이후.
    after = post[entry_idx:]
    time_exit = _first_at_or_after([b for b in after if b.minute >= TIME_EXIT_MIN], TIME_EXIT_MIN)
    # 스톱: 진입 봉 **이후** price ≤ OR_low 인 첫 봉 → 다음 봉 체결.
    stop_ref = None
    stop_ts = None
    tail = after[1:]  # 진입 봉 자체는 스톱 판정에서 제외
    for j, b in enumerate(tail):
        if b.minute >= TIME_EXIT_MIN:
            break  # 시간청산이 먼저
        if b.price <= or_low:
            fill = tail[j + 1] if j + 1 < len(tail) else b  # 다음 봉, 없으면 그 봉
            stop_ref, stop_ts = fill.price, fill.ts
            break
    if stop_ref is not None:
        return TradeSignal(rule=rule, entry_ts=entry_bar.ts, exit_ts=stop_ts,
                           entry_ref=entry_bar.price, exit_ref=stop_ref, exit_reason="stop")
    if time_exit is not None:
        return TradeSignal(rule=rule, entry_ts=entry_bar.ts, exit_ts=time_exit.ts,
                           entry_ref=entry_bar.price, exit_ref=time_exit.price, exit_reason="time")
    # 시간청산 봉도 없으면 마지막 정규장 봉으로 청산.
    last = after[-1]
    if last.ts <= entry_bar.ts:
        return None
    return TradeSignal(rule=rule, entry_ts=entry_bar.ts, exit_ts=last.ts,
                       entry_ref=entry_bar.price, exit_ref=last.price, exit_reason="time")


def momentum_signal(bars: list[Bar], *, prior_close: float | None,
                    rule: str = "momentum_core") -> TradeSignal | None:
    """인트라데이 모멘텀(Gao 2018). prior_close→10:00 수익>0 이면 15:30 매수·15:59 매도."""
    reg = _regular(bars)
    if not reg:
        return None
    flags: list[str] = []
    pc = prior_close
    if pc is None:
        pc = reg[0].price
        flags.append("prior_close_proxy_firstbar")
    if pc <= 0:
        return None
    px10_bar = _last_at_or_before(reg, MOMENTUM_SIGNAL_MIN)
    if px10_bar is None:
        return None
    if px10_bar.price / pc - 1.0 <= 0.0:
        return None  # 상승 신호 없음
    entry_bar = _first_at_or_after(reg, MOMENTUM_ENTRY_MIN)
    if entry_bar is None:
        return None
    after_entry = [b for b in reg if b.ts > entry_bar.ts and b.minute <= MOMENTUM_EXIT_MIN]
    exit_bar = after_entry[-1] if after_entry else None
    if exit_bar is None:
        return None
    return TradeSignal(rule=rule, entry_ts=entry_bar.ts, exit_ts=exit_bar.ts,
                       entry_ref=entry_bar.price, exit_ref=exit_bar.price,
                       exit_reason="time", flags=tuple(flags))


def gap_and_go_signal(bars: list[Bar], *, prior_close: float | None,
                      rule: str = "gap_and_go_movers") -> TradeSignal | None:
    """갭앤고(movers). 갭>+5% 且 첫 15분 시가 위 유지 → 09:45 다음 봉 매수, 종가 청산.

    prior_close 없으면 갭 계산 불가 → None(호출측이 no_prior_close 로 집계).
    """
    if prior_close is None or prior_close <= 0:
        return None
    reg = _regular(bars)
    if not reg:
        return None
    session_open = reg[0].price
    if session_open / prior_close - 1.0 <= GAP_THRESHOLD:
        return None
    hold = [b for b in reg if REGULAR_OPEN_MIN <= b.minute < GAP_HOLD_MIN]
    if not hold or min(b.price for b in hold) < session_open:
        return None  # 첫 15분이 시가 아래로 이탈 → 무효
    post = [b for b in reg if b.minute >= GAP_HOLD_MIN]
    if len(post) < 2:
        return None
    entry_bar = post[1]  # 09:45(post[0]) 확인 → 다음 봉 체결(next-bar)
    exit_bar = reg[-1]   # 종가(마지막 정규장 봉)
    if exit_bar.ts <= entry_bar.ts:
        return None
    return TradeSignal(rule=rule, entry_ts=entry_bar.ts, exit_ts=exit_bar.ts,
                       entry_ref=entry_bar.price, exit_ref=exit_bar.price, exit_reason="time")


# ── 멱등 append(순수) ────────────────────────────────────────────────────────
def record_key(rec: dict) -> tuple[str, str, str]:
    """트레이드 레코드의 멱등 키 = (date, rule, symbol)."""
    return (str(rec.get("date")), str(rec.get("rule")), str(rec.get("symbol")).upper())


def dedup_append(existing: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """기존 레코드 뒤에 new 를 멱등 append. 이미 있는 (date,rule,symbol) 은 스킵.

    반환: (병합 리스트, 새로 추가된 개수). 기존 순서 보존, 새 레코드는 뒤에 append.
    """
    seen = {record_key(r) for r in existing}
    merged = list(existing)
    added = 0
    for r in new:
        k = record_key(r)
        if k in seen:
            continue
        seen.add(k)
        merged.append(r)
        added += 1
    return merged, added


def signals_for_symbol(symbol: str, bars: list[Bar], *,
                       prior_close: float | None) -> list[TradeSignal]:
    """심볼의 세그먼트(core/movers)에 맞는 동결 규칙 전부 평가 → TradeSignal 리스트(None 제외)."""
    sym = symbol.upper()
    out: list[TradeSignal] = []
    if sym in CORE_TIERS:
        for orm, rid in ((5, "orb5_core"), (15, "orb15_core")):
            s = orb_signal(bars, orm, rule=rid)
            if s is not None:
                out.append(s)
        m = momentum_signal(bars, prior_close=prior_close, rule="momentum_core")
        if m is not None:
            out.append(m)
    else:
        for orm, rid in ((5, "orb5_movers"), (15, "orb15_movers")):
            s = orb_signal(bars, orm, rule=rid)
            if s is not None:
                out.append(s)
        g = gap_and_go_signal(bars, prior_close=prior_close, rule="gap_and_go_movers")
        if g is not None:
            out.append(g)
    return out
