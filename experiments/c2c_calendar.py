#!/usr/bin/env python3
"""c2c — 사이클2 아이디어 계열: 캘린더 / 이벤트 효과 (레인1, 저회전).

실험 규약: `experiments/README.md`, 판정: `docs/gate_v2_spec.md`.
이 스크립트는 src/ 를 수정하지 않고 `toss_trader.histdata`(데이터)·`toss_trader.research`
(백테스터)·`scripts/gate_eval.py`(게이트 하네스)만 **소비**한다.

사전등록(아이디어당 설정 1개 + 평탄성 이웃)은 reports/cycle2_c2c_calendar.md 상단에 있다.

── look-ahead(캘린더 인과성) 핵심 ──────────────────────────────────────────────
문제: "월 마지막 거래일"·"turn-of-month 창"·"공휴일 전일" 같은 플래그를 **실현된 거래일
배열 dates[t+1]** 로 판정하면(=research.month_end_flags 방식) 그 자체는 가격 정보를 새게 하진
않지만, "오늘이 이번 달 마지막 거래일인가"를 **미래 거래일의 존재**로 판정하는 것이라 배열
truncation에 취약하다(마지막 원소의 플래그가 뒤 원소 유무에 의존).

해결: 미 증시 휴장 스케줄은 **공개적으로 사전 고지**되므로, 각 날짜의 월내 위치(첫 거래일부터
몇 번째 tdom, 월말까지 몇 거래일 tdtme)와 "공휴일 전일" 여부를 **알고리즘 스케줄 캘린더**
(nyse_holidays)로 date[t] 만의 순수함수로 계산한다. 따라서
  (a) 신호는 closes를 전혀 참조하지 않아 research.lookahead_guard(가격 교란)를 자명히 통과,
  (b) 신호는 date[t] 스케줄 순수함수라 **접두부 불변**(prefix invariance): 뒤 원소를 잘라도
      각 인덱스의 목표비중이 불변 — 이것이 진짜 "캘린더 look-ahead 없음"의 증거다.
tests/test_c2c.py 가 (a),(b)를 모두 강제한다. 실현 거래일이 스케줄과 어긋나는 희귀한 임시휴장
(9/11, 2012 Sandy, 국장일 등)은 타이밍을 하루 어긋나게 할 뿐 미래정보 누수는 아니다(문서화).

체결 타이밍(run_weights, exec_lag=0): **day-t 수익은 target_weights[t-1] 에 귀속**된다(그 날
드리프트로 벌고 종가에 재조정). 따라서 "day D 수익을 담으려면" tw[D-1]=노출. 인덱스로
tw[t] = hold_indicator(next_scheduled_trading_day(date[t])). next는 스케줄 캘린더로 계산하므로
date[t] 순수함수이고 배열 미래원소를 읽지 않는다.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from toss_trader import histdata as H          # noqa: E402
from toss_trader import research as R           # noqa: E402
from toss_trader.models import Candle           # noqa: E402
import gate_eval as GE                          # noqa: E402
from toss_trader import gate                    # noqa: E402

# 원장 경로(디버그 시 C2C_LEDGER 로 임시 원장 사용; 최종 공식 실행만 정본에 적재).
LEDGER = os.environ.get("C2C_LEDGER", str(_ROOT / "reports" / "trials_ledger.jsonl"))
NDX_DIV_YIELD = 0.007        # NDX 보수적 배당수익률(사양)
DESIGN_END = date(2008, 12, 31)   # 롱윈도 설계 1986–2008 / 홀드아웃 2009–2026 (README §3)


# ══════════════════════════════════════════════════════════════════════════════
# 1. 스케줄 미 증시 캘린더 (알고리즘 — date 순수함수, 미래정보 불필요)
# ══════════════════════════════════════════════════════════════════════════════
def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm → 부활절 일요일(Good Friday = 이틀 전)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """그 달의 n번째 특정 요일(weekday: Mon=0)."""
    d = date(year, month, 1)
    shift = (weekday - d.weekday()) % 7
    return d + timedelta(days=shift + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """그 달의 마지막 특정 요일."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed_fixed(y: int, m: int, dd: int, *, new_year: bool = False) -> date | None:
    """고정일 공휴일의 NYSE 관측일. 토→금 앞당김(단, 신정 토요일은 미관측), 일→월."""
    d = date(y, m, dd)
    wd = d.weekday()
    if wd == 5:                       # 토요일
        if new_year:
            return None               # NYSE: 신정이 토요일이면 금요일 휴장 없음
        return d - timedelta(days=1)  # 금요일 관측
    if wd == 6:                       # 일요일 → 월요일 관측
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    """그 해 NYSE/Nasdaq 정규장 휴장일(관측일 기준) 집합. 스케줄(사전고지)만 사용."""
    hols: set[date] = set()
    ny = _observed_fixed(year, 1, 1, new_year=True)
    if ny is not None:
        hols.add(ny)
    # 신정이 일요일이면 1/2(월) 관측이 이미 처리됨. 전년 12/31이 금요일이고 1/1 토요일인 경우
    # 금요일 휴장 없음(위 new_year=None). NYSE 규칙 준수.
    if year >= 1998:
        hols.add(_nth_weekday(year, 1, 0, 3))     # MLK: 1월 셋째 월
    hols.add(_nth_weekday(year, 2, 0, 3))         # Presidents: 2월 셋째 월
    hols.add(_easter(year) - timedelta(days=2))   # Good Friday
    hols.add(_last_weekday(year, 5, 0))           # Memorial: 5월 마지막 월
    if year >= 2022:
        j = _observed_fixed(year, 6, 19)          # Juneteenth (2022~)
        if j is not None:
            hols.add(j)
    ind = _observed_fixed(year, 7, 4)             # Independence
    if ind is not None:
        hols.add(ind)
    hols.add(_nth_weekday(year, 9, 0, 1))         # Labor: 9월 첫 월
    hols.add(_nth_weekday(year, 11, 3, 4))        # Thanksgiving: 11월 넷째 목
    xmas = _observed_fixed(year, 12, 25)          # Christmas
    if xmas is not None:
        hols.add(xmas)
    return hols


_HOL_CACHE: dict[int, set[date]] = {}


def _holidays(year: int) -> set[date]:
    if year not in _HOL_CACHE:
        _HOL_CACHE[year] = nyse_holidays(year)
    return _HOL_CACHE[year]


def is_trading_day(d: date) -> bool:
    """평일이고 스케줄 휴장이 아니면 거래일(스케줄 기준·미래정보 불필요)."""
    return d.weekday() < 5 and d not in _holidays(d.year)


def next_trading_day(d: date) -> date:
    x = d + timedelta(days=1)
    while not is_trading_day(x):
        x += timedelta(days=1)
    return x


def prev_trading_day(d: date) -> date:
    x = d - timedelta(days=1)
    while not is_trading_day(x):
        x -= timedelta(days=1)
    return x


def _trading_days_in_month(year: int, month: int) -> list[date]:
    d = date(year, month, 1)
    out = []
    while d.month == month:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


_TDM_CACHE: dict[tuple[int, int], list[date]] = {}


def _tdm(year: int, month: int) -> list[date]:
    key = (year, month)
    if key not in _TDM_CACHE:
        _TDM_CACHE[key] = _trading_days_in_month(year, month)
    return _TDM_CACHE[key]


def trading_day_of_month(d: date) -> int:
    """월내 거래일 순번(1=첫 거래일). d가 비거래일이면 0."""
    days = _tdm(d.year, d.month)
    return (days.index(d) + 1) if d in days else 0


def trading_days_to_month_end(d: date) -> int:
    """월말까지 남은 거래일(0=마지막 거래일). d가 비거래일이면 -1."""
    days = _tdm(d.year, d.month)
    return (len(days) - 1 - days.index(d)) if d in days else -1


def is_pre_holiday(d: date) -> bool:
    """d가 거래일이고 다음 거래일과의 사이에 '평일 휴장(공휴일)'이 끼면 공휴일 전일.

    단순 주말(금→월)은 제외; 금→화(월요일 공휴일)처럼 사이에 휴장이 있으면 True. 스케줄 순수함수.
    """
    if not is_trading_day(d):
        return False
    nd = next_trading_day(d)
    x = d + timedelta(days=1)
    while x < nd:
        if x.weekday() < 5:            # 사이에 평일이 있는데 비거래일 = 공휴일
            return True
        x += timedelta(days=1)
    return False


def opex_week_third_friday(d: date) -> bool:
    """d가 그 달 '셋째 금요일'을 포함하는 주(월~금)에 속하는가(옵션 만기주). 스케줄 순수함수."""
    tf = _nth_weekday(d.year, d.month, 4, 3)      # 셋째 금요일
    monday = tf - timedelta(days=4)               # 그 주 월요일
    return monday <= d <= tf


# ══════════════════════════════════════════════════════════════════════════════
# 2. FOMC 예정 성명 발표일 (1994~2026) — 스크레이프 검증 대상
#    아래 블록은 scripts 스크레이프(federalreserve.gov)로 채운다. VERIFIED 플래그 참조.
# ══════════════════════════════════════════════════════════════════════════════
# 출처: federalreserve.gov/monetarypolicy/fomccalendars.htm + fomchistorical{1994..2020}.htm
# (전 페이지 HTTP 200 파싱, 2026-09-23 스크레이프). 발표일 = 각 예정회의 마지막 날(2일회의 day2).
# 비정규(unscheduled)·conference call·notation vote 제외. 2020은 3월 정규회의 취소로 7회.
FOMC_DATES_VERIFIED = True
FOMC_ANNOUNCEMENT_DATES: list[str] = [
    "1994-02-04", "1994-03-22", "1994-05-17", "1994-07-06", "1994-08-16", "1994-09-27", "1994-11-15", "1994-12-20",
    "1995-02-01", "1995-03-28", "1995-05-23", "1995-07-06", "1995-08-22", "1995-09-26", "1995-11-15", "1995-12-19",
    "1996-01-31", "1996-03-26", "1996-05-21", "1996-07-03", "1996-08-20", "1996-09-24", "1996-11-13", "1996-12-17",
    "1997-02-05", "1997-03-25", "1997-05-20", "1997-07-02", "1997-08-19", "1997-09-30", "1997-11-12", "1997-12-16",
    "1998-02-04", "1998-03-31", "1998-05-19", "1998-07-01", "1998-08-18", "1998-09-29", "1998-11-17", "1998-12-22",
    "1999-02-03", "1999-03-30", "1999-05-18", "1999-06-30", "1999-08-24", "1999-10-05", "1999-11-16", "1999-12-21",
    "2000-02-02", "2000-03-21", "2000-05-16", "2000-06-28", "2000-08-22", "2000-10-03", "2000-11-15", "2000-12-19",
    "2001-01-31", "2001-03-20", "2001-05-15", "2001-06-27", "2001-08-21", "2001-10-02", "2001-11-06", "2001-12-11",
    "2002-01-30", "2002-03-19", "2002-05-07", "2002-06-26", "2002-08-13", "2002-09-24", "2002-11-06", "2002-12-10",
    "2003-01-29", "2003-03-18", "2003-05-06", "2003-06-25", "2003-08-12", "2003-09-16", "2003-10-28", "2003-12-09",
    "2004-01-28", "2004-03-16", "2004-05-04", "2004-06-30", "2004-08-10", "2004-09-21", "2004-11-10", "2004-12-14",
    "2005-02-02", "2005-03-22", "2005-05-03", "2005-06-30", "2005-08-09", "2005-09-20", "2005-11-01", "2005-12-13",
    "2006-01-31", "2006-03-28", "2006-05-10", "2006-06-29", "2006-08-08", "2006-09-20", "2006-10-25", "2006-12-12",
    "2007-01-31", "2007-03-21", "2007-05-09", "2007-06-28", "2007-08-07", "2007-09-18", "2007-10-31", "2007-12-11",
    "2008-01-30", "2008-03-18", "2008-04-30", "2008-06-25", "2008-08-05", "2008-09-16", "2008-10-29", "2008-12-16",
    "2009-01-28", "2009-03-18", "2009-04-29", "2009-06-24", "2009-08-12", "2009-09-23", "2009-11-04", "2009-12-16",
    "2010-01-27", "2010-03-16", "2010-04-28", "2010-06-23", "2010-08-10", "2010-09-21", "2010-11-03", "2010-12-14",
    "2011-01-26", "2011-03-15", "2011-04-27", "2011-06-22", "2011-08-09", "2011-09-21", "2011-11-02", "2011-12-13",
    "2012-01-25", "2012-03-13", "2012-04-25", "2012-06-20", "2012-08-01", "2012-09-13", "2012-10-24", "2012-12-12",
    "2013-01-30", "2013-03-20", "2013-05-01", "2013-06-19", "2013-07-31", "2013-09-18", "2013-10-30", "2013-12-18",
    "2014-01-29", "2014-03-19", "2014-04-30", "2014-06-18", "2014-07-30", "2014-09-17", "2014-10-29", "2014-12-17",
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17", "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15", "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14", "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def _fomc_set() -> set[date]:
    return {date.fromisoformat(s) for s in FOMC_ANNOUNCEMENT_DATES}


# ══════════════════════════════════════════════════════════════════════════════
# 3. 데이터 조립 (NDX-TR / 합성 2x·3x / 현금 DTB3 / NASDAQCOM / 실물 QQQ·TQQQ)
# ══════════════════════════════════════════════════════════════════════════════
def _closes_on(master: list[date], candles: list[Candle], *, ffill: bool = True) -> list[float]:
    """master 날짜에 정렬된 종가 리스트. 없는 날짜는 직전값 forward-fill(ffill)."""
    by = {c.dt: c.close for c in candles}
    out: list[float] = []
    last = None
    for d in master:
        if d in by:
            last = by[d]
        out.append(last if (last is not None) else (by[master[0]] if master else 0.0))
    if not ffill:
        return [by.get(d, 0.0) for d in master]
    return out


def build_ndx_panel() -> dict:
    """FRED NASDAQ100(1986+) → NDX-TR + 합성 2x/3x(DTB3 조달) + 현금율. 공통 날짜 정렬."""
    ndx = H.load_fred("NASDAQ100")
    tr = H.index_total_return(ndx, NDX_DIV_YIELD, symbol="NDX")
    dtb3 = H.load_fred("DTB3")
    x2 = H.synthetic_leveraged(tr, 2.0, rf_candles=dtb3, rf_kind="yield",
                               annual_expense=0.0095, borrow_spread=0.005, symbol="NDX2X")
    x3 = H.synthetic_leveraged(tr, 3.0, rf_candles=dtb3, rf_kind="yield",
                               annual_expense=0.0095, borrow_spread=0.005, symbol="NDX3X")
    dates = [c.dt for c in tr]
    panel = {
        "NDX": [c.close for c in tr],
        "NDX2X": _closes_on(dates, x2),
        "NDX3X": _closes_on(dates, x3),
    }
    # 현금율: DTB3(연율%) → 일간, master 날짜에 forward-fill
    dtb3_annual = _annual_ffill(dates, dtb3)
    cash_rate = R.cash_rate_from_annual(dtb3_annual)
    return {"dates": dates, "panel": panel, "cash_rate": cash_rate}


def _annual_ffill(master: list[date], candles: list[Candle]) -> list[float]:
    by = {c.dt: c.close for c in sorted(candles, key=lambda c: c.dt)}
    keys = sorted(by)
    out: list[float] = []
    import bisect as _b
    for d in master:
        i = _b.bisect_right(keys, d) - 1
        out.append(by[keys[i]] if i >= 0 else (by[keys[0]] if keys else 0.0))
    return out


def build_comp_panel() -> dict:
    """NASDAQCOM(1971+) — DCA 타이밍 실험용(최장표본). 현금율 DTB3."""
    comp = H.load_fred("NASDAQCOM")
    tr = H.index_total_return(comp, NDX_DIV_YIELD, symbol="COMP")   # 배당근사 동일
    dtb3 = H.load_fred("DTB3")
    dates = [c.dt for c in tr]
    cash_rate = R.cash_rate_from_annual(_annual_ffill(dates, dtb3))
    return {"dates": dates, "closes": [c.close for c in tr], "cash_rate": cash_rate}


# ══════════════════════════════════════════════════════════════════════════════
# 4. 신호(target_weights) — 전부 date 순수함수(closes 미참조 → 캘린더 인과)
# ══════════════════════════════════════════════════════════════════════════════
def make_calendar_signal(hold_fn, asset: str = "NDX", base_asset: str | None = None):
    """캘린더 hold_fn(d)->bool 로 target_weights 생성.

    tw[t] = 노출(asset) if hold_fn(next_scheduled_trading_day(date[t])) else (base_asset or 현금).
    day-(t+1) 수익을 hold_fn(day t+1)로 결정 → run_weights(exec_lag=0)에서 그 수익을 담는다.
    base_asset 지정 시 창 밖에도 그 자산 보유(오버레이). None이면 창 밖 현금(비중 0).
    """
    def sig(closes, dates):
        out = []
        for t in range(len(dates)):
            nd = next_trading_day(_as_date(dates[t]))
            if hold_fn(nd):
                out.append({asset: 1.0})
            elif base_asset is not None:
                out.append({base_asset: 1.0})
            else:
                out.append({})
        return out
    return sig


def _as_date(d):
    return d.date() if hasattr(d, "date") and not isinstance(d, date) else d


# ── hold 지시자(각 날짜 D의 월내/이벤트 위치로 판정) ──────────────────────────
def hold_tom(n_end: int = 1, n_start: int = 3):
    """turn-of-month: 월말 n_end 거래일 + 월초 n_start 거래일 보유.

    사전등록 center: close(−2)→close(+3) = 담는 수익일 {마지막, +1,+2,+3} → n_end=1,n_start=3.
    """
    def f(d: date) -> bool:
        te = trading_days_to_month_end(d)
        ts = trading_day_of_month(d)
        return (0 <= te < n_end) or (1 <= ts <= n_start)
    return f


def hold_fomc():
    fset = _fomc_set()

    def f(d: date) -> bool:
        return d in fset
    return f


def hold_preholiday():
    def f(d: date) -> bool:
        return is_pre_holiday(d)
    return f


def hold_opex():
    def f(d: date) -> bool:
        return is_trading_day(d) and opex_week_third_friday(d)
    return f


# ══════════════════════════════════════════════════════════════════════════════
# 5. 백테스트 → 게이트 지표
# ══════════════════════════════════════════════════════════════════════════════
def _cost(promo: bool = False, mult: float = 1.0) -> R.CostSpec:
    tiers = {"NDX": R.TIER_ETF, "NDX2X": R.TIER_LEVERAGED_ETF, "NDX3X": R.TIER_LEVERAGED_ETF,
             "COMP": R.TIER_ETF}
    base = R.CostSpec.from_tiers(tiers, commission_bps=(10.0 if promo else 25.0),
                                 slippage_bps=5.0, fx_bps=20.0)
    return base.stress(mult) if mult != 1.0 else base


def strat_net_returns(pan: dict, sig, *, promo=False, mult=1.0,
                      band: float = 0.05) -> R.WeightsResult:
    """신호를 run_weights로 실행. band(무매매 밴드)로 비용유발 잔먼지 매매를 차단한다.

    이유: 목표비중 1.0 전액투자 시 매매비가 현금을 −c로 만들어 cur_w>1.0 이 되어 band=0이면
    매일 미세 재조정이 발생(회전·비용 폭증). 신호가 0↔1 이진이라 band(0.05)는 실제 진입/청산
    (변화 1.0)을 절대 막지 않고 dust만 제거한다(test_research.py 관례와 동일).
    """
    tw = sig(pan["panel"], pan["dates"])
    return R.run_weights(pan["panel"], pan["dates"], tw, exec_lag=0,
                         rebalance_band=band, cost=_cost(promo, mult),
                         cash_rate=pan["cash_rate"], start_equity=1.0)


def in_market_daily_returns(sig, pan: dict, asset: str = "NDX") -> list[float]:
    """전략이 노출된 날의 기초자산(asset) 일수익만 모은다(캘린더 효과 존재검정용, gross)."""
    tw = sig(pan["panel"], pan["dates"])
    rets = R.to_returns(pan["panel"][asset])
    held_asset = [asset in (tw[t] or {}) for t in range(len(tw))]
    # day-t 수익은 tw[t-1] 노출에 귀속
    return [rets[t] for t in range(1, len(rets)) if held_asset[t - 1]]


def bench_returns(pan: dict, asset: str = "NDX") -> list[float]:
    return R.to_returns(pan["panel"][asset])


def _in_market_stats(sig, dates) -> tuple[float, float]:
    """연 평균 in-market 일수 / 연 매매 스펠(진입) 수 추정."""
    tw = sig({k: [1.0] * len(dates) for k in ("NDX", "NDX2X", "NDX3X")}, dates)
    held = [1 if w else 0 for w in tw]
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    inmkt = sum(held) / years
    flips = sum(1 for i in range(1, len(held)) if held[i] and not held[i - 1]) / years
    return inmkt, flips


def terminal(returns: list[float], start_idx: int = 0) -> float:
    eq = 1.0
    for r in returns[start_idx:]:
        eq *= (1.0 + r)
    return eq


def start_date_rand_unit(cand: list[float], bench: list[float], dates: list[date],
                         n_off: int = 24, step: int = 21) -> dict:
    """단위자본 시작일 랜덤화: 시작 offset(월≈21거래일)마다 strat/bench 최종부 비교."""
    offsets = list(range(0, n_off * step, step))
    return gate.start_date_randomization(
        lambda s: terminal(cand, s), lambda s: terminal(bench, s), offsets)


# ══════════════════════════════════════════════════════════════════════════════
# 6. DCA 타이밍 (NASDAQCOM 1971+) — 월 $35 를 '언제' 매수하나 (회전 0, 타이밍만)
# ══════════════════════════════════════════════════════════════════════════════
def run_dca_timing(comp: dict, rule: str, *, monthly: float = 35.0, initial: float = 32.0,
                   promo: bool = False) -> dict:
    """월초 입금분을 규칙 rule로 그 달 안에 전액 매수(buy-only). 대기 현금은 DTB3 이자.

    rule: 'first'(입금일=월초 즉시), 'last'(월 마지막 거래일), 'tdom10'(10번째 거래일),
          'after_down'(입금 후 첫 '전일 하락 다음날'; 그 달에 없으면 마지막 거래일).
    전 규칙이 **동일 입금**을 받으므로 XIRR/최종 차이는 순수 '매수 시점' 효과다(회전 불변).
    """
    dates = comp["dates"]
    closes = comp["closes"]
    cash_rate = comp["cash_rate"]
    cost = _cost(promo)
    ms = R.month_start_flags(dates)
    n = len(dates)
    cash = 0.0
    shares_val = 0.0            # COMP 보유 평가액(가격드리프트로 성장)
    deposits: list[tuple[date, float]] = []
    pending_this_month = False  # 이번 달 아직 매수 안 함
    tb = cost.trade_bps("COMP") * R.BPS
    equity_series: list[float] = []

    def do_buy():
        nonlocal cash, shares_val
        if cash <= 0:
            return
        spend = cash / (1.0 + tb)
        c = spend * tb
        shares_val += spend
        cash -= (spend + c)

    for t in range(n):
        if t > 0:
            cash *= (1.0 + cash_rate[t])
            if closes[t - 1] > 0:
                shares_val *= closes[t] / closes[t - 1]
        d = dates[t]
        if ms[t]:
            dep = monthly + (initial if not deposits else 0.0)
            cash += dep - cost.fx_cost(dep)      # FX는 입금에만
            deposits.append((d, dep))
            pending_this_month = True
        if pending_this_month:
            buy = False
            if rule == "first" and ms[t]:
                buy = True
            elif rule == "last" and trading_days_to_month_end(d) == 0:
                buy = True
            elif rule == "tdom10" and (trading_day_of_month(d) == 10
                                       or trading_days_to_month_end(d) == 0):
                buy = True
            elif rule == "after_down":
                down_yday = t >= 2 and closes[t - 1] < closes[t - 2]
                if (down_yday and not ms[t]) or trading_days_to_month_end(d) == 0:
                    buy = True
            if buy:
                do_buy()
                pending_this_month = False
        equity_series.append(shares_val + cash)

    final = equity_series[-1]
    cf = [(d, -a) for d, a in deposits] + [(dates[-1], final)]
    return {"rule": rule, "final": final, "xirr": gate.xirr(cf),
            "total_dep": sum(a for _, a in deposits), "cashflows": cf,
            "equity": equity_series, "dates": dates}


def dca_timing_startrand(comp: dict, rule_a: str, rule_b: str, *, n_off: int = 36) -> dict:
    """시작월 offset 랜덤화: rule_a vs rule_b 최종자산 승률(같은 입금·현금흐름 스케줄 절단)."""
    base_a = run_dca_timing(comp, rule_a)
    base_b = run_dca_timing(comp, rule_b)
    da, db = base_a["equity"], base_b["equity"]
    dates = comp["dates"]
    ms_idx = [t for t, f in enumerate(R.month_start_flags(dates)) if f]

    def terminal_from(equity, start_t):
        # start_t 이후만: 근사로 시작시점 자본을 1로 정규화한 성장배수
        if equity[start_t] <= 0:
            return 0.0
        return equity[-1] / equity[start_t]
    offs = ms_idx[:n_off]
    return gate.start_date_randomization(
        lambda s: terminal_from(da, offs[s]) if s < len(offs) else 0.0,
        lambda s: terminal_from(db, offs[s]) if s < len(offs) else 0.0,
        list(range(len(offs))))


# ══════════════════════════════════════════════════════════════════════════════
# 7. 진단: 요일 효과(Monday effect) — 후보 아님(사양대로 진단만)
# ══════════════════════════════════════════════════════════════════════════════
def fomc_prepost_effect(pan: dict, split: date = date(2012, 1, 1), asset: str = "NDX") -> dict:
    """pre-FOMC 창의 in-market 일수익을 공개 전(1994–2011)·공개 후(2012~)로 분할해 효과 검정.

    Lucca–Moench 이후 성명 실시간공개(2011~)로 사전드리프트가 약화됐다는 가설의 자연 홀드아웃.
    """
    import random as _r
    sig = make_calendar_signal(hold_fomc(), asset=asset)
    tw = sig(pan["panel"], pan["dates"])
    rets = R.to_returns(pan["panel"][asset])
    dates = pan["dates"]
    held = [asset in (tw[t] or {}) for t in range(len(tw))]
    pre, post = [], []
    for t in range(1, len(rets)):
        if held[t - 1]:
            (pre if dates[t] < split else post).append(rets[t])

    def stat(x, seed):
        if len(x) < 3:
            return {"n": len(x), "mean_bps": 0.0, "tstat": 0.0, "ci_lo_bps": 0.0}
        m, lo, hi = gate.stationary_bootstrap_mean_ci(x, q=0.1, B=2000, rng=_r.Random(seed))
        return {"n": len(x), "mean_bps": R._mean(x) * 1e4, "tstat": gate.trade_tstat(x),
                "ci_lo_bps": lo * 1e4, "ci_hi_bps": hi * 1e4}
    return {"split": split.isoformat(), "pre_publication": stat(pre, 11),
            "post_publication": stat(post, 12)}


def weekday_effect(pan: dict, asset: str = "NDX") -> dict:
    rets = R.to_returns(pan["panel"][asset])
    dates = pan["dates"]
    buckets: dict[int, list[float]] = {i: [] for i in range(5)}
    for t in range(1, len(rets)):
        wd = dates[t].weekday()
        if wd < 5:
            buckets[wd].append(rets[t])
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    out = {}
    for i in range(5):
        b = buckets[i]
        out[names[i]] = {"n": len(b), "mean_bps": (R._mean(b) * 1e4) if b else 0.0,
                         "tstat": gate.trade_tstat(b)}
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 8. 아이디어 평가 드라이버
# ══════════════════════════════════════════════════════════════════════════════
def _effect_test(sig, pan: dict, asset: str = "NDX") -> dict:
    """캘린더 창의 in-market 일수익(gross) 존재검정: 평균·t·정상부트 95% CI 하한."""
    ims = in_market_daily_returns(sig, pan, asset)
    if len(ims) < 3:
        return {"n": len(ims), "mean_bps": 0.0, "tstat": 0.0, "ci_lo_bps": 0.0}
    import random as _r
    m, lo, hi = gate.stationary_bootstrap_mean_ci(ims, q=0.1, B=2000, rng=_r.Random(7))
    return {"n": len(ims), "mean_bps": R._mean(ims) * 1e4, "tstat": gate.trade_tstat(ims),
            "ci_lo_bps": lo * 1e4, "ci_hi_bps": hi * 1e4}


def _capture_days(hold_fn, dates) -> set:
    return {d for d in dates if hold_fn(d)}


def _shift_membership(base_days: set, all_sorted: list, pre: int, post: int):
    idx = {d: i for i, d in enumerate(all_sorted)}
    keep = set(base_days)
    for d in base_days:
        i = idx.get(d)
        if i is None:
            continue
        for k in range(1, pre + 1):
            if i - k >= 0:
                keep.add(all_sorted[i - k])
        for k in range(1, post + 1):
            if i + k < len(all_sorted):
                keep.add(all_sorted[i + k])
    return lambda d: d in keep


def evaluate_idea(idea_id: str, center_hold, cfg: dict, pan: dict, *,
                  asset: str = "NDX", base_asset: str | None = None,
                  neighbors: list | None = None, bench_asset: str = "NDX") -> dict:
    """한 아이디어: 이웃 로깅(N) → 헤드라인 설계/홀드아웃 → 강건성. 결과 dict."""
    sig = make_calendar_signal(center_hold, asset=asset, base_asset=base_asset)
    # look-ahead(가격) 가드 — 반드시 통과
    R.lookahead_guard(sig, pan["panel"], pan["dates"])
    inm, flips = _in_market_stats(sig, pan["dates"])
    # 효과 존재검정은 창의 NDX(1x) 일수익으로 측정(오버레이든 아니든 동일한 창의 순수 효과).
    eff = _effect_test(make_calendar_signal(center_hold, asset="NDX"), pan)

    bench_full = bench_returns(pan, bench_asset)
    # ── 이웃(평탄성) 설계기간 로깅으로 N 구축 ──
    neighbors = neighbors or []
    for ncfg, nhold in neighbors:
        nsig = make_calendar_signal(nhold, asset=asset, base_asset=base_asset)
        nres = strat_net_returns(pan, nsig)
        cr = nres.net_returns[1:]
        di = [i for i, d in enumerate(pan["dates"][1:]) if d <= DESIGN_END]
        um = GE.unit_capital_metrics([cr[i] for i in di],
                                     dates=[pan["dates"][1:][i] for i in di])
        GE.log_evaluation(idea_id, ncfg, 1, "design", um,
                          GE.money_weighted_metrics(), ledger_path=LEDGER,
                          window=(pan["dates"][1:][di[0]], pan["dates"][1:][di[-1]]))
    # ── 헤드라인 설계/홀드아웃 ──
    res = strat_net_returns(pan, sig)
    cand = res.net_returns[1:]
    bench_terminal_design = None
    # 경제 축: terminal vs B&H(bench_asset) — 설계·홀드아웃 각각
    rendered = GE.evaluate_and_render(
        idea_id, cfg, 1, cand, bench_full[1:], pan["dates"], DESIGN_END,
        bench_terminal=None, ledger_path=LEDGER, rc_B=2000, log=True)
    splits = rendered["splits"]
    # terminal_vs_b0 를 직접 채운다(unit capital, 각 구간 strat/bench 최종부 비율)
    def seg_terminal(returns, idxs):
        eq = 1.0
        for i in idxs:
            eq *= (1.0 + returns[i])
        return eq
    ret_dates = pan["dates"][1:len(cand) + 1]
    di = [i for i, d in enumerate(ret_dates) if d <= DESIGN_END]
    hi = [i for i, d in enumerate(ret_dates) if d > DESIGN_END]
    tvb = {}
    for name, idxs in (("design", di), ("holdout", hi)):
        if not idxs:
            continue
        st = seg_terminal(cand, idxs)
        bt = seg_terminal(bench_full[1:], idxs)
        tvb[name] = st / bt if bt > 0 else float("nan")
    # ── 강건성: 비용스트레스·breakeven·프로모·시작일 ──
    def net_excess(mult):
        r = strat_net_returns(pan, sig, mult=mult)
        cr = r.net_returns[1:]
        st = seg_terminal(cr, di)
        bt = seg_terminal(bench_full[1:], di)
        return st / bt - 1.0
    cs = {"base": net_excess(1.0), "x2": net_excess(2.0)}
    promo = strat_net_returns(pan, sig, promo=True)
    promo_final = promo.final_equity
    sdr = start_date_rand_unit(cand, bench_full[1:], ret_dates)
    return {
        "idea_id": idea_id, "cfg": cfg, "inmkt_dpy": inm, "entries_py": flips,
        "trade_legs": res.trade_count, "total_cost": res.total_cost,
        "effect": eff, "terminal_vs_bh": tvb,
        "final_unit": res.final_equity, "promo_final": promo_final,
        "cost_stress": cs, "start_date_winrate": sdr["win_rate"],
        "start_date_median_margin": sdr["median_margin"],
        "splits": {k: {"unit": v["unit"], "axis": v["axis_input"],
                       "rc": v["reality_check"]} for k, v in splits.items()},
        "decisions": {k: str(gate.decide({**v["axis_input"],
                       "terminal_vs_b0": tvb.get(k)}))
                      for k, v in splits.items()},
        "markdown": rendered["markdown"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 9. main — 6개 아이디어 + DCA 타이밍 + 요일진단, 원장 적재, 결과 JSON
# ══════════════════════════════════════════════════════════════════════════════
def main() -> dict:
    import json
    ndx = build_ndx_panel()
    dates = ndx["dates"]
    all_sorted = list(dates)
    results: dict = {"meta": {
        "ndx_span": [dates[0].isoformat(), dates[-1].isoformat()],
        "design_end": DESIGN_END.isoformat(), "fomc_verified": FOMC_DATES_VERIFIED,
        "n_fomc": len(FOMC_ANNOUNCEMENT_DATES)}}

    # ── 1) turn-of-month (1x, 창밖 현금) ──
    tom_neigh = [({"kind": "tom", "n_end": ne, "n_start": ns}, hold_tom(ne, ns))
                 for (ne, ns) in [(1, 2), (1, 4), (2, 3), (2, 4)]]
    results["tom_1x"] = evaluate_idea(
        "c2c_tom_1x", hold_tom(1, 3), {"kind": "tom", "n_end": 1, "n_start": 3, "leg": "1x"},
        ndx, asset="NDX", neighbors=tom_neigh)

    # ── 1b) turn-of-month 3x (창 안 3x, 창밖 현금) ──
    results["tom_3x"] = evaluate_idea(
        "c2c_tom_3x", hold_tom(1, 3), {"kind": "tom", "n_end": 1, "n_start": 3, "leg": "3x_window"},
        ndx, asset="NDX3X", neighbors=tom_neigh)

    # ── 1c) 오버레이: 1x 상시 + 창 안 3x ──
    results["tom_overlay"] = evaluate_idea(
        "c2c_tom_overlay", hold_tom(1, 3),
        {"kind": "tom_overlay", "n_end": 1, "n_start": 3, "base": "NDX1x", "win": "NDX3x"},
        ndx, asset="NDX3X", base_asset="NDX", neighbors=tom_neigh)

    # ── 2) pre-FOMC drift (발표일 종가 t-1→t) ──
    fomc_days = _capture_days(hold_fomc(), dates)
    fomc_neigh = [
        ({"kind": "fomc", "pre": 1, "post": 0},
         _shift_membership(fomc_days, all_sorted, 1, 0)),
        ({"kind": "fomc", "pre": 0, "post": 1},
         _shift_membership(fomc_days, all_sorted, 0, 1)),
    ]
    results["prefomc"] = evaluate_idea(
        "c2c_prefomc", hold_fomc(), {"kind": "fomc", "pre": 0, "post": 0}, ndx,
        asset="NDX", neighbors=fomc_neigh)

    # ── 3) pre-holiday (공휴일 전 거래일) ──
    ph_days = _capture_days(hold_preholiday(), dates)
    ph_neigh = [
        ({"kind": "preholiday", "pre": 1}, _shift_membership(ph_days, all_sorted, 1, 0)),
        ({"kind": "preholiday", "post": 1}, _shift_membership(ph_days, all_sorted, 0, 1)),
    ]
    results["preholiday"] = evaluate_idea(
        "c2c_preholiday", hold_preholiday(), {"kind": "preholiday"}, ndx,
        asset="NDX", neighbors=ph_neigh)

    # ── 6) creative: OpEx week (옵션 만기주) ──
    opex_days = _capture_days(hold_opex(), dates)
    opex_neigh = [
        ({"kind": "opex", "shift": "pre1"}, _shift_membership(opex_days, all_sorted, 1, 0)),
        ({"kind": "opex", "shift": "post1"}, _shift_membership(opex_days, all_sorted, 0, 1)),
    ]
    results["opex"] = evaluate_idea(
        "c2c_opex", hold_opex(), {"kind": "opex_week"}, ndx,
        asset="NDX", neighbors=opex_neigh)

    # ── 2b) pre-FOMC pre/post 공개(2012~) 분할 — Lucca–Moench 자연 홀드아웃 ──
    results["prefomc_prepost"] = fomc_prepost_effect(ndx)

    # ── 4) 요일 효과 진단(후보 아님) ──
    results["weekday_diag"] = weekday_effect(ndx)

    # ── 5) DCA 타이밍 (NASDAQCOM 1971+) ──
    comp = build_comp_panel()
    rules = ["first", "last", "tdom10", "after_down"]
    dca = {r: {k: v for k, v in run_dca_timing(comp, r).items()
               if k in ("rule", "final", "xirr", "total_dep")} for r in rules}
    sr_last_first = dca_timing_startrand(comp, "last", "first")
    sr_ad_first = dca_timing_startrand(comp, "after_down", "first")
    results["dca_timing"] = {
        "span": [comp["dates"][0].isoformat(), comp["dates"][-1].isoformat()],
        "rules": dca,
        "xirr_spread_bps": (max(v["xirr"] for v in dca.values())
                            - min(v["xirr"] for v in dca.values())) * 1e4,
        "startrand_last_vs_first_winrate": sr_last_first["win_rate"],
        "startrand_afterdown_vs_first_winrate": sr_ad_first["win_rate"],
    }

    print(json.dumps(results, default=str, ensure_ascii=False, indent=1))
    return results


if __name__ == "__main__":
    main()
