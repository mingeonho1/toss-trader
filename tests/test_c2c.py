"""c2c 캘린더/이벤트 실험 테스트 — look-ahead(가격+캘린더) 이중 가드.

검증 포인트:
- 스케줄 캘린더(nyse_holidays·tdom·tdtme·pre-holiday·opex)의 값 정확성.
- 모든 캘린더 신호는 research.lookahead_guard(미래 '가격' 교란)를 통과한다(신호가 closes 미참조).
- **캘린더 인과성(핵심)**: 신호는 date[t] 스케줄 순수함수라 배열을 뒤에서 잘라도 각 인덱스의
  목표비중이 불변(prefix invariance). "월 마지막 거래일" 판정이 dates[t+1] 을 엿보지 않음을 증명.
  대조군: research.month_end_flags(dates[t+1] 참조)로 만든 신호는 prefix invariance를 **위반**한다
  → 우리 테스트가 실제로 누수를 잡는다는 증거.
- FOMC 예정 발표일 리스트 정합(개수·연도별·검증 플래그).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "experiments"))

from toss_trader import research as R  # noqa: E402
import c2c_calendar as C               # noqa: E402


# ── 스케줄 캘린더 정확성 ──────────────────────────────────────────────────────
def test_nyse_holidays_known_years():
    h2021 = C.nyse_holidays(2021)
    assert date(2021, 1, 1) in h2021        # 신정
    assert date(2021, 1, 18) in h2021       # MLK 셋째 월
    assert date(2021, 2, 15) in h2021       # Presidents 셋째 월
    assert date(2021, 4, 2) in h2021        # Good Friday
    assert date(2021, 5, 31) in h2021       # Memorial 마지막 월
    assert date(2021, 7, 5) in h2021        # Independence(7/4 일 → 7/5 관측)
    assert date(2021, 11, 25) in h2021      # Thanksgiving 넷째 목
    assert date(2021, 12, 24) in h2021      # Christmas(12/25 토 → 12/24 관측)
    # Juneteenth: 2022 신설, 2021 이전엔 없음
    assert date(2021, 6, 18) not in C.nyse_holidays(2021)
    assert date(2022, 6, 20) in C.nyse_holidays(2022)   # 6/19 일 → 6/20 관측
    # MLK: 1998년부터
    assert C._nth_weekday(1996, 1, 0, 3) not in C.nyse_holidays(1996)


def test_new_year_saturday_not_observed():
    # 2022-01-01은 토요일 → NYSE는 전일 금요일(2021-12-31) 휴장하지 않음.
    assert date(2021, 12, 31) not in C.nyse_holidays(2021)
    assert C.is_trading_day(date(2021, 12, 31))


def test_trading_day_positions_jan2021():
    # 2021년 1월: 첫 거래일 1/4, 마지막 1/29.
    assert C.trading_day_of_month(date(2021, 1, 4)) == 1
    assert C.trading_days_to_month_end(date(2021, 1, 29)) == 0
    assert C.trading_days_to_month_end(date(2021, 1, 28)) == 1   # 2nd-to-last
    assert C.trading_day_of_month(date(2021, 1, 1)) == 0         # 휴장 → 0


def test_pre_holiday_and_opex():
    assert C.is_pre_holiday(date(2021, 5, 28)) is True    # 금(메모리얼 월요일 전)
    assert C.is_pre_holiday(date(2021, 5, 21)) is False   # 평범한 금요일
    assert C.is_pre_holiday(date(2021, 12, 23)) is True   # 크리스마스(12/24 관측) 전날
    assert C.opex_week_third_friday(date(2021, 1, 15)) is True   # 셋째 금요일
    assert C.opex_week_third_friday(date(2021, 1, 11)) is True   # 같은 주 월요일
    assert C.opex_week_third_friday(date(2021, 1, 25)) is False  # 다음 주


def test_hold_tom_capture_days():
    # center(n_end=1,n_start=3): {월 마지막, 첫3거래일} 캡처.
    f = C.hold_tom(1, 3)
    assert f(date(2021, 1, 29)) is True      # 마지막
    assert f(date(2021, 2, 1)) is True       # 첫 거래일
    assert f(date(2021, 2, 3)) is True       # 셋째 거래일
    assert f(date(2021, 2, 4)) is False      # 넷째 → 창 밖
    assert f(date(2021, 1, 28)) is False     # 2nd-to-last: 진입일이나 그날 수익은 담지 않음


# ── FOMC 리스트 ──────────────────────────────────────────────────────────────
def test_fomc_list_integrity():
    assert C.FOMC_DATES_VERIFIED is True
    ds = [date.fromisoformat(s) for s in C.FOMC_ANNOUNCEMENT_DATES]
    assert len(ds) == 263
    assert ds == sorted(ds)                  # 오름차순·유니크
    assert len(set(ds)) == len(ds)
    from collections import Counter
    by_year = Counter(d.year for d in ds)
    assert by_year[2020] == 7                # 3월 정규회의 취소
    assert all(by_year[y] == 8 for y in range(1994, 2026) if y != 2020)
    assert date(1994, 2, 4) in ds            # 최초 성명(1994-02-04)
    assert date(2008, 9, 16) in ds           # 리먼 주간 FOMC


# ── 가격 look-ahead 가드: 모든 신호 통과 ──────────────────────────────────────
def _syn_dates(start: date, n: int) -> list[date]:
    """실제 거래일 기준 스케줄 거래일 n개(주말·공휴일 제외)."""
    out, d = [], start
    while len(out) < n:
        if C.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _syn_panel(dates):
    import math
    closes = [100.0 + 5.0 * math.sin(i / 4.0) + 0.1 * i for i in range(len(dates))]
    return {"NDX": list(closes), "NDX2X": list(closes), "NDX3X": list(closes)}


@pytest.mark.parametrize("holdname", ["tom", "fomc", "preholiday", "opex"])
def test_signals_pass_price_lookahead_guard(holdname):
    dates = _syn_dates(date(2015, 1, 2), 520)   # ~2년 스케줄 거래일
    panel = _syn_panel(dates)
    holds = {"tom": C.hold_tom(1, 3), "fomc": C.hold_fomc(),
             "preholiday": C.hold_preholiday(), "opex": C.hold_opex()}
    sig = C.make_calendar_signal(holds[holdname], asset="NDX")
    assert R.lookahead_guard(sig, panel, dates) is True
    # 오버레이(base_asset)도 통과
    sig2 = C.make_calendar_signal(holds[holdname], asset="NDX3X", base_asset="NDX")
    assert R.lookahead_guard(sig2, panel, dates) is True


# ── 캘린더 인과성: prefix invariance (핵심 요구사항) ──────────────────────────
def _assert_prefix_invariant(sig, panel, dates, k):
    full = sig(panel, dates)
    pre = sig({s: v[:k] for s, v in panel.items()}, dates[:k])
    for i in range(k):                       # 스케줄 순수함수라 마지막 인덱스까지 불변
        assert (full[i] or {}) == (pre[i] or {}), (
            f"prefix invariance 위반 @i={i} date={dates[i]}: {full[i]} != {pre[i]}")


@pytest.mark.parametrize("holdname", ["tom", "fomc", "preholiday", "opex"])
def test_signals_are_prefix_invariant(holdname):
    dates = _syn_dates(date(2015, 1, 2), 520)
    panel = _syn_panel(dates)
    holds = {"tom": C.hold_tom(1, 3), "fomc": C.hold_fomc(),
             "preholiday": C.hold_preholiday(), "opex": C.hold_opex()}
    sig = C.make_calendar_signal(holds[holdname], asset="NDX")
    # 여러 절단점에서 확인(특히 월 경계 근처)
    for k in (300, 400, 511):
        _assert_prefix_invariant(sig, panel, dates, k)


def test_naive_month_end_signal_violates_prefix_invariance():
    """대조군: research.month_end_flags(dates[t+1] 참조)로 만든 신호는 캘린더 look-ahead가 있어
    prefix invariance를 위반한다 → 우리 테스트가 실제로 누수를 잡는다는 증거."""
    dates = _syn_dates(date(2015, 1, 2), 520)
    panel = _syn_panel(dates)

    def naive_sig(closes, ds):
        # "오늘이 월 마지막 거래일이면 보유" — dates[t+1] 을 엿봄(=research.month_end_flags).
        me = R.month_end_flags(ds)
        return [{"NDX": 1.0} if me[t] else {} for t in range(len(ds))]

    full = naive_sig(panel, dates)
    k = 400
    pre = naive_sig({s: v[:k] for s, v in panel.items()}, dates[:k])
    # 잘린 배열의 마지막 원소는 항상 '월말'로 오판 → 최소 한 인덱스에서 불일치가 나야 한다.
    mismatches = [i for i in range(k) if (full[i] or {}) != (pre[i] or {})]
    assert mismatches, "대조군이 위반을 내지 않음 — 테스트가 무력함"


def test_leaky_signal_peeking_next_date_is_caught():
    """다음 날짜를 직접 엿보는 신호가 prefix invariance 테스트에 걸리는지.

    미묘점(문서화 가치): dates[t+1] 을 엿보는 신호는 '절단점이 월말일 때'만 누수가 드러난다.
    따라서 인과성 테스트는 반드시 **월말 절단점**을 포함해야 실효가 있다(우리 신호는 스케줄
    순수함수라 어떤 절단점에서도 불변이므로 안전).
    """
    dates = _syn_dates(date(2015, 1, 2), 300)
    panel = _syn_panel(dates)

    def leaky(closes, ds):
        # 내일이 새 달이면(=배열의 다음 원소를 참조) 보유 — 명백한 배열 미래 참조.
        out = []
        for t in range(len(ds)):
            nxt_new_month = (t + 1 < len(ds)) and (ds[t + 1].month != ds[t].month)
            out.append({"NDX": 1.0} if nxt_new_month else {})
        return out

    # 절단점 k-1 이 '월 마지막 거래일'이 되도록 k 선택 → next-date peek 노출.
    k = next(i + 1 for i in range(200, len(dates))
             if C.trading_days_to_month_end(dates[i]) == 0)
    with pytest.raises(AssertionError):
        _assert_prefix_invariant(leaky, panel, dates, k)
    # 반대로 우리 스케줄 기반 TOM 신호는 같은 월말 절단점에서도 불변.
    _assert_prefix_invariant(C.make_calendar_signal(C.hold_tom(1, 3), asset="NDX"),
                             panel, dates, k)
