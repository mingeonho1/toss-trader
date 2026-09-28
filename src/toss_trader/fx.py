"""KRW↔USD 환전(FX) 비용의 **시간대별 우대** 모델 — 표준 라이브러리만.

costs.py / fees.py 를 건드리지 않는 **독립 추가 모듈**(하위호환). 백테스터의 편도 bps
근사(costs.CostModel.fx_spread_bps)와 달리, 여기서는 "언제 환전하느냐(KST 시간대)"에 따라
KRW→USD 환전 스프레드가 달라진다는 사실을 모델링한다.

────────────────────────────────────────────────────────────────────────
사실 정리 (검증/미검증 명시 — 2026-09-28 조사)
- [검증 · 공식] 토스증권 환전 수수료 = 환전은행 실시간 **매매기준환율의 스프레드 1%** 기준에
  시간대별 우대를 적용(2026-04-22 시행).
    · **정규시간(국내 영업일 09:00–15:30 KST) → 95% 우대 = 실효 약 0.05%(5bps)**
    · **정규 외(15:30~익일 09:00) · 주말 · 공휴일 → 50% 우대 = 실효 약 0.5%(50bps)**
  출처: https://corp.tossinvest.com/ko/business?tab=commission , https://support.toss.im/faq/3549
- [검증 · 공식] **자동환전은 "환전이 실제 처리되는 그 시각"의 우대율을 그대로 적용**한다
  (고정 우대율 없음). '주식모으기' 자동주문은 야간 체결 → 50%(0.5%) 적용. 우리 DCA 는 미
  정규장(≈23:35–02:00 KST)에 도는데, 계좌가 KRW 라 주문 시 자동환전되면 **야간 0.5%**를 문다.
- [검증 · 스펙] 토스 OpenAPI(v1.2.17)에는 **환전 전용 엔드포인트가 없다**. 통화 관련 경로는
  /api/v1/buying-power(통화별 현금 매수가능금액)·/api/v1/exchange-rate(참고 환율)뿐이며,
  주문 본문(OrderCreateRequest)에도 currency/자동환전 파라미터가 없다. → 환전 시각을 API로
  제어할 수단이 없다(**앱에서 주간창에 수동/정기 환전**이 유일한 저비용 경로).
- [검증 · 스펙] /exchange-rate 는 rate(매수환율)·midRate(매매기준율)·basisPoint
  (=(rate-midRate)/midRate*1e4)·validFrom/validUntil(≈1분)을 준다. 스펙은 "참고용 표시 환율이며
  **실제 주문 시 적용 환율과 다를 수 있다**"고 명시 → 표시 스프레드가 곧 실환전비용은 아니다.
  시간대 우대 필드는 응답에 **없다**.
- [미검증 · 언론] 달러 부족 시 연결계좌에서 자동 인출·환전 후 체결(야간 50%) 보도 있음.
  토스"증권"에 원하는 시각을 지정하는 독립 예약/정기환전 기능은 공식 확인 안 됨(주식모으기 등
  예약매수 체결 시에만 자동환전). → 요율·창 시각은 조정 가능하도록 **모두 인자로** 둔다.
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

__all__ = [
    "KST",
    "KRX_HOLIDAYS_2026",
    "FxWindow",
    "FxCostModel",
    "AnnualFxCost",
    "in_discount_window",
    "next_window_open",
    "displayed_spread_bps",
    "annual_fx_cost",
]

KST = timezone(timedelta(hours=9))

# 대한민국 증시/은행 휴장 근사(2026). 이 날짜엔 원화 FX '주간 우대창'이 열리지 않는다(정규 외 취급).
# ⚠️ **근사값**(대체공휴일 포함 시도). 정확성이 필요하면 FxWindow(holidays=...)로 주입하라.
KRX_HOLIDAYS_2026 = frozenset({
    date(2026, 1, 1),                                        # 신정
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),  # 설날 연휴
    date(2026, 3, 1), date(2026, 3, 2),                       # 삼일절(+대체)
    date(2026, 5, 1),                                        # 근로자의 날(증시 휴장)
    date(2026, 5, 5),                                        # 어린이날
    date(2026, 5, 24), date(2026, 5, 25),                    # 부처님오신날(+대체)
    date(2026, 6, 6),                                        # 현충일
    date(2026, 8, 15),                                       # 광복절
    date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26),  # 추석 연휴
    date(2026, 10, 3),                                       # 개천절
    date(2026, 10, 9),                                       # 한글날
    date(2026, 12, 25),                                      # 성탄절
    date(2026, 12, 31),                                      # 연말 폐장
})


def _to_kst(dt: datetime) -> datetime:
    """naive datetime 은 KST 로 간주, aware 는 KST 로 변환."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


@dataclass(frozen=True)
class FxWindow:
    """원화 FX 환전 우대 '주간 창' 정의 — 국내 영업일 09:00–15:30 KST(공휴일/주말 제외).

    [검증·공식] 정규시간 = 국내 영업일 09:00–15:30 KST. 그 외(주말·공휴일 포함)는 정규 외.
    ⚠️ 공휴일 목록은 근사(KRX_HOLIDAYS_2026). 필요 시 holidays 를 주입해 갱신하라.
    """
    open_time: time = time(9, 0)
    close_time: time = time(15, 30)
    holidays: frozenset[date] = KRX_HOLIDAYS_2026
    weekend_days: frozenset[int] = frozenset({5, 6})   # weekday(): 토=5, 일=6

    def is_business_day(self, d: date) -> bool:
        """주말/공휴일이 아니면 영업일(=주간 우대창이 열리는 날)."""
        return d.weekday() not in self.weekend_days and d not in self.holidays

    def contains(self, when: datetime) -> bool:
        """주어진 시각이 '주간 우대창(09:00–15:30 KST, 영업일)' 안인가."""
        k = _to_kst(when)
        if not self.is_business_day(k.date()):
            return False
        t = k.time()
        return self.open_time <= t < self.close_time

    def next_open(self, when: datetime) -> datetime:
        """when 이후 가장 이른 '우대창 시작 시각'(KST). 창 안이면 다음 영업일 09:00."""
        k = _to_kst(when)
        for i in range(0, 30):                     # 넉넉한 상한(연휴 대비)
            day = k.date() + timedelta(days=i)
            if self.is_business_day(day):
                op = datetime.combine(day, self.open_time, tzinfo=KST)
                if op > k:
                    return op
        return datetime.combine(k.date() + timedelta(days=30), self.open_time, tzinfo=KST)


@dataclass(frozen=True)
class FxCostModel:
    """시간대별 KRW↔USD 환전 실효 스프레드(편도, bps).

    [검증·공식] 기준 스프레드 1%(100bps)에 우대 적용:
      - in_window_bps = 5bps(0.05%)  ← 정규시간 95% 우대
      - out_window_bps = 50bps(0.5%) ← 정규 외/주말/공휴일 50% 우대
    base_spread_bps(100bps=무우대)는 참고용. 값은 공식 고시 기준이나, 계좌·시점차로 달라질 수
    있어 **모두 인자**로 둔다(실측 시 갱신).
    """
    in_window_bps: float = 5.0
    out_window_bps: float = 50.0
    base_spread_bps: float = 100.0
    window: FxWindow = field(default_factory=FxWindow)

    def fee_bps(self, when: datetime) -> float:
        """해당 시각의 환전 실효 스프레드(bps). 우대창 안=in_window, 밖=out_window."""
        return self.in_window_bps if self.window.contains(when) else self.out_window_bps

    def fee_krw(self, krw_amount: float, when: datetime) -> float:
        """krw_amount 를 환전할 때 그 시각 요율로 물게 되는 환전비(KRW)."""
        return abs(krw_amount) * self.fee_bps(when) * 1e-4

    def extra_bps_vs_window(self) -> float:
        """정규 외 환전이 주간창 대비 추가로 무는 스프레드(bps)."""
        return max(0.0, self.out_window_bps - self.in_window_bps)

    def extra_cost_krw(self, krw_amount: float, when: datetime) -> float:
        """when 에 환전 시 '주간창 대비' 초과 지출(KRW). 창 안이면 0."""
        if self.window.contains(when):
            return 0.0
        return abs(krw_amount) * self.extra_bps_vs_window() * 1e-4


@dataclass(frozen=True)
class AnnualFxCost:
    """정기 적립(월 monthly_krw) 시 연간 환전비 요약(KRW)."""
    monthly_krw: float
    fee_bps: float
    annual_krw: float             # 연 환전 원금(= monthly×12)
    per_deposit_fx_krw: float     # 1회 입금당 환전비
    annual_fx_krw: float          # 연간 환전비


def annual_fx_cost(monthly_krw: float, fee_bps: float) -> AnnualFxCost:
    """월 monthly_krw 를 fee_bps 요율로 매월 환전할 때의 연간 환전비."""
    annual = monthly_krw * 12.0
    return AnnualFxCost(
        monthly_krw=monthly_krw,
        fee_bps=fee_bps,
        annual_krw=annual,
        per_deposit_fx_krw=monthly_krw * fee_bps * 1e-4,
        annual_fx_krw=annual * fee_bps * 1e-4,
    )


def displayed_spread_bps(rate: object, mid_rate: object) -> float | None:
    """/exchange-rate 의 rate·midRate 로 **표시 스프레드**(bps) 계산. 실패 시 None.

    ⚠️ 이는 API 가 보여주는 참고 스프레드일 뿐, 실제 환전 우대율(시간대별 5/50bps)과 다르다
    (스펙: "실제 주문 시 적용 환율과 다를 수 있음").
    """
    try:
        r = float(rate)
        m = float(mid_rate)
    except (TypeError, ValueError):
        return None
    if m <= 0:
        return None
    return (r - m) / m * 1e4


# 모듈 기본 모델(편의 함수용).
_DEFAULT_MODEL = FxCostModel()


def in_discount_window(when: datetime | None = None,
                       model: FxCostModel | None = None) -> bool:
    """지금(또는 when)이 환전 우대창(평일 09:00–15:30 KST) 안인가."""
    when = when or datetime.now(KST)
    return (model or _DEFAULT_MODEL).window.contains(when)


def next_window_open(when: datetime | None = None,
                     model: FxCostModel | None = None) -> datetime:
    """다음 환전 우대창 시작 시각(KST)."""
    when = when or datetime.now(KST)
    return (model or _DEFAULT_MODEL).window.next_open(when)
