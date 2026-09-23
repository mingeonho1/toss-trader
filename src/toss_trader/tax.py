"""해외주식 양도소득세 최적화 — 결정론적·타이밍 무관 수익 레버(한국 거주자 기준).

이 모듈은 매매 타이밍을 예측하지 않는다. 대신 **한국 세법이 이미 정해 둔 규칙**을 이용해
같은 매매 결과에서 세후 수익을 끌어올린다(공제 활용 + 손익통산). 시장 예측이 아니라 회계다.

──────────────────────────────────────────────────────────────────────────────
한국 거주자의 미국(해외)주식 세금 사실 — 코드에 박아두는 근거 (확인 필요분은 ⚠️로 표기)
──────────────────────────────────────────────────────────────────────────────
- **양도소득세율 22%** = 양도소득세 20% + 지방소득세 2%(양도세의 10%). (`OVERSEAS_CG_RATE`)
- 과세 단위 = **역년(1/1~12/31)**. 그 해 실현손익을 **전 해외종목 통산**(gains·losses netting)한
  '연간 순실현손익'에 대해 과세. (`realized_by_year`, `annual_tax`)
- **기본공제 250만원/년**(`BASIC_DEDUCTION_KRW`). 과세표준 = max(0, 연간순손익 − 250만).
  세액 = 과세표준 × 22%. 공제는 **매년 새로** 주어지고 **이월·저축 불가**(use-it-or-lose-it).
- 손익은 **원화(KRW)로 계산**: 양도차익 = (매도수량×매도가×매도일 환율)
  − (취득원가×취득일 환율) − 필요경비(수수료 등). 즉 **환차익도 과세 대상**이다.
  → 원가는 취득일 환율로 원화 고정, 매도는 매도일 환율. (환율 = KRW per USD)
- **워시세일(wash sale) 규칙 없음**(미국과 달리 한국은 재매수 제한 없음). 그래서 팔았다가
  **즉시 되사도** 손실/이익이 그대로 인정된다 → 이익 실현(익절 후 재매수)로 **원가 스텝업**,
  손실 실현(손절 후 재매수)로 **통산 상계**가 가능(포지션 유지한 채).
- **해외주식 양도손실은 이월되지 않는다**(연내 통산만). 즉 손실 하베스팅은 **그 해 공제 초과
  이익을 상쇄하는 만큼만** 절세 가치가 있다(초과분은 버려짐). ⚠️ 국내주식과의 통산 등
  세부는 신고 시점 규정 확인 필요 — 본 모듈은 해외종목 내 통산만 모델링.
- 배당은 미국에서 **15% 원천징수**(`US_DIVIDEND_WITHHOLDING`). 배당소득세는 양도소득과 별개
  (금융소득종합과세 트랙)라 본 최적화(양도차익 통산/공제)의 통산 대상이 아니다 — 참고용 상수만 둠.

취득원가 산정 방식: 한국 증권사는 해외주식에 흔히 **이동평균법(moving average)** 을 쓴다.
그래서 기본을 이동평균으로 두고 FIFO를 옵션으로 제공한다(`Method`). 두 방식 모두 **원가를
원화(취득일 환율 반영)로** 추적한다.

⚠️ 불확실성 플래그: 세율/공제/통산범위는 2024~2026 기준 일반론이다. 실제 신고는 개인 상황
(다른 해외소득, 이월결손 규정 변경 등)에 좌우되므로 본 모듈 산출은 '의사결정 참고'이며
확정 세액이 아니다. 실주문·신고 전 국세청/세무사 확인.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger("toss_trader.tax")

# ── 세법 상수 ──────────────────────────────────────────────────────────────
OVERSEAS_CG_RATE = 0.22          # 22% = 20%(양도) + 2%(지방)
BASIC_DEDUCTION_KRW = 2_500_000  # 연 250만원 기본공제(이월 불가)
US_DIVIDEND_WITHHOLDING = 0.15   # 미국 배당 원천징수 15%(참고용; 양도소득 통산 대상 아님)

# 하베스팅 라운드트립(매도+재매수)이 절세보다 비싸면 추천 금지할 때의 기본 안전계수.
DEFAULT_SAFETY_FACTOR = 1.5

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER_PATH = _ROOT / "data" / "tax_ledger.json"


class Method(str, Enum):
    """취득원가 산정 방식. 기본은 이동평균(한국 증권사 관행), FIFO 옵션."""
    MOVING_AVERAGE = "moving_average"
    FIFO = "fifo"


# ─────────────────────────────────────────────────────── annual_tax
def annual_tax(realized_krw: float, *, deduction: float = BASIC_DEDUCTION_KRW,
               rate: float = OVERSEAS_CG_RATE) -> float:
    """연간 순실현손익(원화)에 대한 양도소득세(원화). 손실이면 0.

    과세표준 = max(0, realized_krw − deduction);  세액 = 과세표준 × rate.
    """
    taxable = max(0.0, realized_krw - deduction)
    return taxable * rate


# ─────────────────────────────────────────────────────── 로트/포지션
@dataclass
class Lot:
    """취득 로트(원화 원가로 고정). qty·krw_per_share = 취득일 환율 반영 주당 원화원가."""
    quantity: float
    krw_per_share: float
    acquired: date | None = None


@dataclass(frozen=True)
class RealizedSale:
    """실현(매도) 1건의 결과. gain_krw = 양도가액 − 취득원가 − 매도수수료(필요경비)."""
    symbol: str
    date: date
    quantity: float
    proceeds_krw: float   # 양도가액(총액, 매도일 환율) — 수수료 차감 전
    cost_krw: float       # 취득원가(원화, 취득일 환율; 매수수수료 포함)
    fee_krw: float        # 매도 필요경비(수수료 등, 원화)

    @property
    def gain_krw(self) -> float:
        return self.proceeds_krw - self.cost_krw - self.fee_krw

    def to_json(self) -> dict:
        return {"symbol": self.symbol, "date": self.date.isoformat(),
                "quantity": self.quantity, "proceeds_krw": self.proceeds_krw,
                "cost_krw": self.cost_krw, "fee_krw": self.fee_krw,
                "gain_krw": self.gain_krw}


@dataclass(frozen=True)
class Holding:
    """평가 시점 보유(원화 원가 확정본). harvest_plan 입력 단위.

    - price_usd: 현재가(USD),  cost_basis_krw: 보유수량 전체의 취득원가(원화, 매수수수료 포함).
    unrealized_krw = 평가액(현재가×환율) − 취득원가(원화). 환차손익이 자동 포함된다.
    """
    symbol: str
    quantity: float
    price_usd: float
    cost_basis_krw: float

    def market_value_krw(self, fx: float) -> float:
        return self.quantity * self.price_usd * fx

    def unrealized_krw(self, fx: float) -> float:
        return self.market_value_krw(fx) - self.cost_basis_krw


class LotBook:
    """종목별 로트 추적 + 원화 원가 산정(이동평균 기본 / FIFO 옵션).

    - buy: 취득일 환율로 원화 원가에 편입(매수수수료는 취득원가에 가산 = 필요경비).
    - sell: 방식에 따라 원가를 소진하고 RealizedSale(원화 양도차익) 반환.
      매도수수료는 gain에서 차감. 잔여 로트는 그대로(이동평균은 평단 불변, FIFO는 앞 로트 소진).
    이동평균은 종목당 1개 로트로 병합(가중평균)해 유지 → 매도 시 평단 원가 사용.
    """

    def __init__(self, method: Method | str = Method.MOVING_AVERAGE) -> None:
        self.method = Method(method)
        self._lots: dict[str, list[Lot]] = {}

    # -- 조회 --
    def quantity(self, symbol: str) -> float:
        return sum(l.quantity for l in self._lots.get(symbol, []))

    def cost_basis_krw(self, symbol: str) -> float:
        """보유수량 전체 취득원가(원화)."""
        return sum(l.quantity * l.krw_per_share for l in self._lots.get(symbol, []))

    def avg_krw_per_share(self, symbol: str) -> float:
        q = self.quantity(symbol)
        return self.cost_basis_krw(symbol) / q if q > 0 else 0.0

    def symbols(self) -> list[str]:
        return [s for s, lots in self._lots.items() if sum(l.quantity for l in lots) > 1e-12]

    def unrealized_krw(self, symbol: str, price_usd: float, fx: float) -> float:
        return self.quantity(symbol) * price_usd * fx - self.cost_basis_krw(symbol)

    def holding(self, symbol: str, price_usd: float) -> Holding:
        return Holding(symbol, self.quantity(symbol), price_usd,
                       self.cost_basis_krw(symbol))

    def holdings(self, prices_usd: dict[str, float]) -> list[Holding]:
        out: list[Holding] = []
        for s in self.symbols():
            px = prices_usd.get(s)
            if px is not None:
                out.append(self.holding(s, px))
        return out

    # -- 거래 --
    def buy(self, symbol: str, quantity: float, price_usd: float, fx: float,
            when: date | None = None, *, fee_krw: float = 0.0) -> None:
        """매수. 취득원가(원화) = quantity×price_usd×fx + fee_krw(필요경비). fx=취득일 환율."""
        if quantity <= 0:
            raise ValueError("quantity는 양수여야 합니다.")
        krw_cost = quantity * price_usd * fx + fee_krw
        per_share = krw_cost / quantity
        lots = self._lots.setdefault(symbol, [])
        if self.method is Method.MOVING_AVERAGE and lots:
            # 종목당 1개 로트로 가중평균 병합(이동평균).
            cur = lots[0]
            tot_qty = cur.quantity + quantity
            tot_krw = cur.quantity * cur.krw_per_share + krw_cost
            lots[0] = Lot(tot_qty, tot_krw / tot_qty, when or cur.acquired)
        else:
            lots.append(Lot(quantity, per_share, when))

    def sell(self, symbol: str, quantity: float, price_usd: float, fx: float,
             when: date, *, fee_krw: float = 0.0) -> RealizedSale:
        """매도. 원가 소진 방식은 self.method. proceeds=quantity×price_usd×fx(fx=매도일 환율).

        RealizedSale를 반환(원장 기록은 호출자/ TaxLedger가 담당). 잔량 부족 시 ValueError.
        """
        held = self.quantity(symbol)
        if quantity <= 0:
            raise ValueError("quantity는 양수여야 합니다.")
        if quantity > held + 1e-9:
            raise ValueError(f"{symbol} 보유 {held} < 매도 {quantity}")
        cost_krw = self._consume(symbol, quantity)
        proceeds_krw = quantity * price_usd * fx
        return RealizedSale(symbol, when, quantity, proceeds_krw, cost_krw, fee_krw)

    def _consume(self, symbol: str, quantity: float) -> float:
        """방식에 따라 quantity만큼 로트 원가를 소진하고 소진된 취득원가(원화)를 반환."""
        lots = self._lots.get(symbol, [])
        remaining = quantity
        cost = 0.0
        if self.method is Method.MOVING_AVERAGE:
            lot = lots[0]
            cost = remaining * lot.krw_per_share
            lot.quantity -= remaining
            if lot.quantity <= 1e-12:
                self._lots[symbol] = []
            return cost
        # FIFO: 앞 로트부터 소진
        i = 0
        while remaining > 1e-12 and i < len(lots):
            lot = lots[i]
            take = min(lot.quantity, remaining)
            cost += take * lot.krw_per_share
            lot.quantity -= take
            remaining -= take
            i += 1
        self._lots[symbol] = [l for l in lots if l.quantity > 1e-12]
        return cost


# ─────────────────────────────────────────────────────── 실현손익 원장(연도별)
class TaxLedger:
    """역년별 실현손익 원장. data/tax_ledger.json 에 영속화.

    entries: RealizedSale 직렬화 목록. realized_by_year()/realized_ytd(year)로 통산 조회.
    """

    def __init__(self, path: Path | str | None = DEFAULT_LEDGER_PATH,
                 method: Method | str = Method.MOVING_AVERAGE) -> None:
        self.path = Path(path) if path else None
        self.method = Method(method)
        self.entries: list[dict] = []
        if self.path and self.path.exists():
            self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = list(data.get("entries", []))
            if data.get("method"):
                self.method = Method(data["method"])
        except (OSError, ValueError, TypeError) as e:  # 손상 시 빈 원장으로 시작(로그만)
            logger.warning("세금 원장 로드 실패(%s) → 빈 원장 사용", e)
            self.entries = []

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"method": self.method.value, "entries": self.entries}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def record(self, sale: RealizedSale) -> None:
        self.entries.append(sale.to_json())

    def realized_by_year(self) -> dict[int, float]:
        """{연도: 순실현손익(원화)} — 전 종목 통산."""
        out: dict[int, float] = {}
        for e in self.entries:
            try:
                yr = int(str(e["date"])[:4])
            except (KeyError, ValueError, TypeError):
                continue
            out[yr] = out.get(yr, 0.0) + float(e.get("gain_krw", 0.0))
        return out

    def realized_ytd(self, year: int) -> float:
        """해당 역년의 누적 순실현손익(원화). 없으면 0."""
        return self.realized_by_year().get(year, 0.0)

    def tax_for_year(self, year: int, *, deduction: float = BASIC_DEDUCTION_KRW,
                     rate: float = OVERSEAS_CG_RATE) -> float:
        return annual_tax(self.realized_ytd(year), deduction=deduction, rate=rate)


# ─────────────────────────────────────────────────────── 하베스팅 플랜
@dataclass(frozen=True)
class HarvestAction:
    """개별 하베스팅 액션(정확한 매도·재매수 수량). 미국 MARKET은 소수점 수량 허용."""
    kind: str            # "gain_harvest" | "loss_harvest"
    symbol: str
    sell_quantity: float
    rebuy_quantity: float
    price_usd: float
    realized_krw: float  # 이 액션으로 실현되는 손익(원화, 이익은 +/손실은 −)
    fee_krw: float       # 라운드트립(매도+재매수) 필요경비(원화)


@dataclass(frozen=True)
class HarvestPlan:
    """하베스팅 추천 결과. recommended=False면 실행하지 말 것(사유 reason)."""
    mode: str                 # "gain" | "loss" | "none"
    actions: tuple[HarvestAction, ...]
    tax_saved_krw: float      # 이 플랜으로 아끼는(회피/상계) 세액(원화, 총)
    fee_krw: float            # 총 라운드트립 필요경비(원화)
    net_benefit_krw: float    # tax_saved − fee
    recommended: bool
    reason: str
    remaining_deduction_krw: float   # 올해 남은 기본공제(gain 모드에서 사용)
    realized_ytd_krw: float
    harvested_krw: float = 0.0       # 실현하는 손익 총액(이익>0 / 손실<0)

    def summary(self) -> str:
        if self.mode == "none" or not self.actions:
            return f"하베스팅 없음 — {self.reason}"
        head = ("이익 하베스팅(공제 활용·원가 스텝업)" if self.mode == "gain"
                else "손실 하베스팅(통산 상계)")
        legs = ", ".join(
            f"{a.symbol} {'익절' if a.kind=='gain_harvest' else '손절'}"
            f"→재매수 {a.sell_quantity:.4f}주(@${a.price_usd:.2f}, "
            f"실현 ₩{a.realized_krw:,.0f})"
            for a in self.actions)
        flag = "✅추천" if self.recommended else "⏸비추천"
        return (f"{flag} {head}: {legs} | 절세 ₩{self.tax_saved_krw:,.0f} − "
                f"비용 ₩{self.fee_krw:,.0f} = 순효익 ₩{self.net_benefit_krw:,.0f} | {self.reason}")


def _round_trip_fee_krw(notional_usd: float, fx: float,
                        fee_fn: Callable[[float], float]) -> float:
    """매도+재매수 라운드트립 비용(원화). fee_fn(notional_usd)=편도 수수료(USD).

    익절/손절 후 즉시 재매수는 통화 전환(원↔달러) 없이 USD 내에서 이뤄지므로 환전 스프레드는
    호출자의 fee_fn 정의에 위임한다(하베스팅은 보통 환전 없음 → 수수료+슬리피지만).
    """
    return (fee_fn(notional_usd) + fee_fn(notional_usd)) * fx


def harvest_plan(
    holdings: Iterable[Holding],
    fx_now: float,
    realized_ytd_krw: float,
    *,
    deduction: float = BASIC_DEDUCTION_KRW,
    fee_fn: Callable[[float], float],
    rate: float = OVERSEAS_CG_RATE,
    safety_factor: float = DEFAULT_SAFETY_FACTOR,
    today: date | None = None,
) -> HarvestPlan:
    """연말(12월) 하베스팅 추천 — 결정론적. 매매 타이밍이 아니라 공제/통산 회계다.

    (a) **이익 하베스팅**: 올해 남은 기본공제(deduction − realized_ytd) 한도까지 미실현 이익을
        실현(매도 후 즉시 재매수)해 **원가를 스텝업**. 실현분은 공제로 비과세, 미래 과세이익을
        그만큼 줄여 22% 절세. 남은 공제가 있고(realized_ytd < deduction) 미실현 이익이 있을 때.
    (b) **손실 하베스팅**: 이미 공제를 초과한 실현이익(realized_ytd > deduction)이 있을 때만,
        미실현 손실을 실현해 **통산 상계**(22% 절세). 상계는 초과분(realized_ytd − deduction)
        한도까지만 가치 있음(손실 이월 불가 → 초과 실현은 낭비).

    두 상황은 realized_ytd 위치로 갈린다: 공제 미만 → 이익 하베스팅, 공제 초과 → 손실 하베스팅.
    각 액션은 정확한 매도·재매수 수량을 담고(소수점 OK), **절세 < 비용×safety_factor면 비추천**.
    `today`(기본 오늘)로 12월 여부를 판정해 이익 하베스팅의 적기 안내(연내 체결 필요).
    """
    today = today or date.today()
    holdings = list(holdings)
    room = deduction - realized_ytd_krw           # 남은 공제(음수 가능)
    excess = realized_ytd_krw - deduction          # 공제 초과 실현이익(음수면 상계 무의미)

    if room > 0:
        return _gain_harvest(holdings, fx_now, realized_ytd_krw, room, fee_fn,
                             rate, safety_factor, today, deduction)
    # room <= 0: 공제 이미 소진 → 초과이익 상계용 손실 하베스팅 검토
    return _loss_harvest(holdings, fx_now, realized_ytd_krw, max(0.0, excess),
                         fee_fn, rate, safety_factor, today, deduction)


def _gain_harvest(holdings, fx_now, realized_ytd, room, fee_fn, rate, safety,
                  today, deduction) -> HarvestPlan:
    gainers = sorted((h for h in holdings if h.unrealized_krw(fx_now) > 0),
                     key=lambda h: h.unrealized_krw(fx_now), reverse=True)
    actions: list[HarvestAction] = []
    left = room                       # 이번에 실현할 이익 여유(공제 잔여)
    total_gain = 0.0
    total_fee = 0.0
    for h in gainers:
        if left <= 1e-9:
            break
        u = h.unrealized_krw(fx_now)               # 이 종목 미실현 이익(원화)
        take_gain = min(u, left)                    # 이 종목에서 실현할 이익
        frac = take_gain / u if u > 0 else 0.0      # 이동평균: 이익∝수량 → 매도비율
        sell_qty = h.quantity * frac
        if sell_qty <= 0:
            continue
        notional = sell_qty * h.price_usd
        fee = _round_trip_fee_krw(notional, fx_now, fee_fn)
        actions.append(HarvestAction("gain_harvest", h.symbol, sell_qty, sell_qty,
                                     h.price_usd, take_gain, fee))
        total_gain += take_gain
        total_fee += fee
        left -= take_gain

    # 절세: 공제 한도에서 비과세로 실현한 이익 total_gain만큼 미래 과세이익이 줄어 22% 절세.
    tax_saved = rate * total_gain
    net = tax_saved - total_fee
    is_dec = today.month == 12
    ok = bool(actions) and tax_saved >= safety * total_fee and net > 0 and is_dec
    if not actions:
        reason = "실현할 미실현 이익 없음(모두 손실이거나 보유 없음)"
    elif not is_dec:
        reason = (f"남은 공제 ₩{room:,.0f} 활용 가능하나 지금은 {today.month}월 — "
                  "연말(12월)에 실행 권장(가격 변동/연내 체결 고려)")
    elif tax_saved < safety * total_fee:
        reason = (f"절세 ₩{tax_saved:,.0f} < 비용 ₩{total_fee:,.0f}×{safety:g} "
                  "→ 규모 부족, 하베스팅 낭비")
    elif net <= 0:
        reason = "순효익 ≤ 0"
    else:
        reason = (f"12월 남은 공제 ₩{room:,.0f}까지 이익 실현→즉시 재매수(원가 스텝업). "
                  f"실현이익 ₩{total_gain:,.0f}는 공제로 비과세.")
    return HarvestPlan("gain", tuple(actions), tax_saved, total_fee, net, ok, reason,
                       room, realized_ytd, harvested_krw=total_gain)


def _loss_harvest(holdings, fx_now, realized_ytd, offsetable, fee_fn, rate, safety,
                  today, deduction) -> HarvestPlan:
    if offsetable <= 1e-9:
        return HarvestPlan(
            "none", (), 0.0, 0.0, 0.0, False,
            f"실현이익 ₩{realized_ytd:,.0f}이 공제 ₩{deduction:,.0f} 이하 → 상계할 과세이익 없음",
            deduction - realized_ytd, realized_ytd)
    losers = sorted((h for h in holdings if h.unrealized_krw(fx_now) < 0),
                    key=lambda h: h.unrealized_krw(fx_now))   # 가장 큰 손실 먼저
    actions: list[HarvestAction] = []
    left = offsetable                  # 상계 가치가 있는 손실 한도(초과분은 낭비)
    total_loss = 0.0                   # 양수로 누적(상계액)
    total_fee = 0.0
    for h in losers:
        if left <= 1e-9:
            break
        u = h.unrealized_krw(fx_now)               # 음수(손실)
        take_loss = min(-u, left)                   # 실현할 손실(양수)
        frac = take_loss / (-u) if u < 0 else 0.0
        sell_qty = h.quantity * frac
        if sell_qty <= 0:
            continue
        notional = sell_qty * h.price_usd
        fee = _round_trip_fee_krw(notional, fx_now, fee_fn)
        actions.append(HarvestAction("loss_harvest", h.symbol, sell_qty, sell_qty,
                                     h.price_usd, -take_loss, fee))
        total_loss += take_loss
        total_fee += fee
        left -= take_loss

    tax_saved = rate * total_loss      # 초과이익을 상계 → 22% 절세
    net = tax_saved - total_fee
    ok = bool(actions) and tax_saved >= safety * total_fee and net > 0
    if not actions:
        reason = "실현할 미실현 손실 없음"
    elif tax_saved < safety * total_fee:
        reason = (f"절세 ₩{tax_saved:,.0f} < 비용 ₩{total_fee:,.0f}×{safety:g} → 낭비")
    elif net <= 0:
        reason = "순효익 ≤ 0"
    else:
        reason = (f"공제 초과 실현이익 ₩{offsetable:,.0f}을 손실로 상계(재매수로 포지션 유지). "
                  f"상계 ₩{total_loss:,.0f} × 22% 절세.")
    return HarvestPlan("loss", tuple(actions), tax_saved, total_fee, net, ok, reason,
                       deduction - realized_ytd, realized_ytd, harvested_krw=-total_loss)
