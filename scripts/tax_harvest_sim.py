#!/usr/bin/env python3
"""세금 이익-하베스팅 예시 백테스트 — 언제부터 '의미 있어지는지'를 실데이터로 보여준다.

무엇을 비교하나: 2016-09부터 QQQ에 매월 적립(DCA)하고 10년 보유 후 **한 번에 전량 매도**했을
때의 **세후 최종 자산**을, 두 시나리오로 비교한다.
  (A) 무하베스팅: 마지막 해에 누적 미실현이익 전부를 한꺼번에 실현 → 기본공제(250만) 1번만 적용.
  (B) 연말 이익-하베스팅: 매년 12월 남은 공제(연 250만)까지 이익을 실현→즉시 재매수(원가 스텝업).
      실현분은 공제로 비과세. 매년의 공제를 '쓰고' 원가를 높여, 최종 매도 시 과세이익을 줄인다.
      (한국은 워시세일 규칙이 없어 즉시 재매수해도 손익 인정 → 포지션은 그대로 유지된다.)

핵심: 시장 예측이 아니라 **공제(use-it-or-lose-it)를 매년 활용**하는 회계 레버다. 두 시나리오는
동일한 매수·동일한 주식수·동일한 세전 평가액을 가지며, 차이는 오직 (하베스팅 왕복비용)과
(양도세)뿐이다. Δ세후 = 절세 − 비용.

데이터(키 불필요, 캐시 사용):
- QQQ 총수익 일봉: histdata.load_symbol('QQQ')  (배당재투자 조정)
- 원/달러 환율: histdata.load_fred('DEXKOUS')  (FRED, KRW per USD; 정직한 UA로 조회)

⚠️ 단순화: 하베스팅 왕복비용은 최종에 원화로 차감(주식수 누적에는 영향 없게) → 두 시나리오의
세전 경로를 동일하게 두어 세금 효과만 격리. 실제론 재매수가 미세하게 주식수를 바꾼다.
비용/세율/공제는 보수적 일반값이며 확정 세액이 아니다(신고 전 확인).

사용: PYTHONPATH=src python scripts/tax_harvest_sim.py
"""
from __future__ import annotations

import bisect
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.histdata import load_fred, load_symbol           # noqa: E402
from toss_trader.tax import (                                     # noqa: E402
    BASIC_DEDUCTION_KRW,
    OVERSEAS_CG_RATE,
    annual_tax,
)

# ── 가정 ───────────────────────────────────────────────────────────────────
COMMISSION_RATE = 0.0025     # 미국 실요율 0.25%(편도). PLAN.md v1.2.17.
SAFETY_FACTOR = 1.5          # 절세 < 비용×이 값이면 그 해 하베스팅 스킵.
START = date(2016, 9, 1)


def _fee_usd(notional_usd: float) -> float:
    """편도 수수료(USD). 하베스팅 재매수는 통화전환(원↔달러) 없음 → 수수료만(환전 스프레드 X)."""
    return abs(notional_usd) * COMMISSION_RATE


class FxLookup:
    """일자 → 원/달러(KRW per USD). 결측일은 직전 유효값 forward-fill."""

    def __init__(self, candles):
        self._dates = [c.dt for c in candles]
        self._fx = [c.close for c in candles]

    def at(self, d: date) -> float:
        i = bisect.bisect_right(self._dates, d) - 1
        return self._fx[max(0, i)]


def _first_trading_days(dates: list[date]) -> set[date]:
    """각 (연,월)의 첫 거래일 집합."""
    seen: dict[tuple[int, int], date] = {}
    for d in dates:
        seen.setdefault((d.year, d.month), d)
    return set(seen.values())


def _year_end_trading_days(dates: list[date], final_year: int) -> dict[int, date]:
    """연도별 마지막 거래일(단, final_year 제외 — 최종해는 전량 매도로 처리)."""
    last: dict[int, date] = {}
    for d in dates:
        last[d.year] = d
    return {y: d for y, d in last.items() if y < final_year}


def simulate(candles, fx: FxLookup, *, initial_usd=0.0, monthly_usd=0.0,
             monthly_krw=0.0, safety=SAFETY_FACTOR):
    """단일 QQQ 포지션 DCA. 두 시나리오(무/유 하베스팅)를 동시에 계산해 dict 반환."""
    prices = {c.dt: c.close for c in candles}
    dates = [c.dt for c in candles]
    firsts = _first_trading_days(dates)
    final_year = dates[-1].year
    harvest_days = _year_end_trading_days(dates, final_year)
    harvest_by_date = {d: y for y, d in harvest_days.items()}

    shares = 0.0
    buy_basis_krw = 0.0          # 매수로 쌓인 원가(원화; 두 시나리오 공통)
    stepup_krw = 0.0             # 하베스팅으로 늘린 원가(시나리오 B 전용)
    harvest_fee_krw = 0.0        # 하베스팅 왕복비용 누계(원화)
    harvested_krw = 0.0          # 하베스팅으로 실현한 이익 누계(비과세)
    n_harvest = 0
    deposited_krw = 0.0

    started = False
    for i, d in enumerate(dates):
        px = prices[d]
        f = fx.at(d)
        # 첫 거래일에 초기 일시금(있으면) + 매월 적립.
        if d in firsts:
            usd_in = 0.0
            if not started and initial_usd:
                usd_in += initial_usd
                started = True
            if monthly_usd:
                usd_in += monthly_usd
            if monthly_krw:
                usd_in += monthly_krw / f
            if usd_in > 0:
                comm = _fee_usd(usd_in)
                shares += (usd_in - comm) / px
                buy_basis_krw += usd_in * f          # 지불한 원화 전액 = 취득원가
                deposited_krw += usd_in * f
        # 연말 이익 하베스팅(시나리오 B).
        if d in harvest_by_date:
            value_krw = shares * px * f
            basis_now = buy_basis_krw + stepup_krw
            unrealized = value_krw - basis_now
            room = BASIC_DEDUCTION_KRW               # 그 해 유일 실현이벤트 → 공제 전액
            harvestable = min(room, max(0.0, unrealized))
            if harvestable > 0 and unrealized > 0:
                frac = harvestable / unrealized
                sell_qty = shares * frac
                notional = sell_qty * px
                rt_fee_krw = 2.0 * _fee_usd(notional) * f
                tax_saved = OVERSEAS_CG_RATE * harvestable
                if tax_saved >= safety * rt_fee_krw:  # 수수료 게이트
                    stepup_krw += harvestable          # 원가 스텝업(매도+즉시 재매수)
                    harvest_fee_krw += rt_fee_krw
                    harvested_krw += harvestable
                    n_harvest += 1

    # 최종: 전량 매도(같은 세전 평가액).
    end_px = prices[dates[-1]]
    end_fx = fx.at(dates[-1])
    value_end = shares * end_px * end_fx

    gain_no = value_end - buy_basis_krw
    tax_no = annual_tax(gain_no)
    after_no = value_end - tax_no

    basis_with = buy_basis_krw + stepup_krw
    gain_with = value_end - basis_with
    tax_with = annual_tax(gain_with)
    after_with = value_end - tax_with - harvest_fee_krw

    return {
        "deposited_krw": deposited_krw,
        "value_end_krw": value_end,
        "shares": shares,
        "gain_no_krw": gain_no,
        "tax_no_krw": tax_no,
        "after_no_krw": after_no,
        "gain_with_krw": gain_with,
        "tax_with_krw": tax_with,
        "after_with_krw": after_with,
        "harvest_fee_krw": harvest_fee_krw,
        "harvested_krw": harvested_krw,
        "n_harvest": n_harvest,
        "tax_saved_krw": tax_no - tax_with,
        "delta_after_krw": after_with - after_no,
        "start": dates[0],
        "end": dates[-1],
    }


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def main() -> int:
    qqq = load_symbol("QQQ", start=START, adjusted=True)
    fx = FxLookup(load_fred("DEXKOUS", start=date(2015, 1, 1)))
    if len(qqq) < 100:
        print("QQQ 캐시 부족", file=sys.stderr)
        return 1

    scales = [
        ("$32 + $35/월 (실제 규모)", dict(initial_usd=32.0, monthly_usd=35.0)),
        ("₩30만/월", dict(monthly_krw=300_000.0)),
        ("₩100만/월", dict(monthly_krw=1_000_000.0)),
        ("₩300만/월", dict(monthly_krw=3_000_000.0)),
        ("₩1,000만/월", dict(monthly_krw=10_000_000.0)),
    ]

    first = simulate(qqq, fx, **scales[0][1])
    print("해외주식 양도세 이익-하베스팅 시뮬레이션 (QQQ DCA, 세후 최종자산 비교)")
    print(f"  기간 {first['start']} ~ {first['end']} | 환율 FRED DEXKOUS | "
          f"수수료 {COMMISSION_RATE*100:.2f}%/편도 | 공제 ₩{_fmt(BASIC_DEDUCTION_KRW)}/년 | "
          f"세율 {OVERSEAS_CG_RATE*100:.0f}% | 안전계수 {SAFETY_FACTOR:g} | 이동평균법")
    print()
    hdr = ["규모", "입금누계₩", "세전평가₩", "세후 무하베스팅₩",
           "세후 하베스팅₩", "Δ세후₩", "절세₩", "비용₩", "하베스팅"]
    w = [22, 15, 16, 17, 17, 13, 13, 11, 8]
    print(" | ".join(h.ljust(x) for h, x in zip(hdr, w)))
    print("-|-".join("-" * x for x in w))
    for label, kw in scales:
        r = simulate(qqq, fx, **kw)
        row = [
            label,
            _fmt(r["deposited_krw"]),
            _fmt(r["value_end_krw"]),
            _fmt(r["after_no_krw"]),
            _fmt(r["after_with_krw"]),
            f"+{_fmt(r['delta_after_krw'])}",
            _fmt(r["tax_saved_krw"]),
            _fmt(r["harvest_fee_krw"]),
            f"{r['n_harvest']}회",
        ]
        print(" | ".join(str(c).ljust(x) for c, x in zip(row, w)))

    print()
    print("읽는 법: 10년 QQQ 이익(≈₩13M+)이 1회 공제(250만)를 크게 넘으므로, 매년 12월 공제를 써서")
    print("원가를 높이면 최종 일괄매도 과세가 확 준다. 절세 상한 ≈ (하베스팅 연수 × 250만 × 22%) ≈ ₩5M라")
    print("규모가 커져도 절대 이득은 곧 포화된다 → 세후자산 대비 비중은 오히려 소액일수록 크다(실제 규모")
    print("에서도 세금 ₩2.3M 중 ₩1.86M 회수). ⚠️ '마지막에 전량매도' 가정이 무하베스팅 세금을 최대로")
    print("잡으므로, 매도를 여러 해에 분산하면 격차는 줄어든다(그래도 공제를 매년 쓰는 이점은 남는다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
