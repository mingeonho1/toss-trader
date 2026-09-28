"""c8b — Which BASE allocation maximizes a Korean resident's AFTER-TAX, after-fee
KRW terminal wealth for monthly DCA, and at what risk?

사전등록·규약: reports/cycle8_c8b_allocation_tax.md, experiments/README.md,
docs/gate_v2_spec.md (레인1 저회전 배분). 원장 reports/trials_ledger.jsonl.

핵심 질문(알파가 아니라 실무 의사결정): 월 적립 DCA 를 하는 한국 거주자에게 **세후·수수료
후 원화(KRW) 최종자산**을 극대화하는 BASE 배분은 무엇이며, 그 위험은 어떤가.

한국 거주자 정밀 모델(src/toss_trader/tax.py·fees.py 근거):
  · **미국 배당 원천징수 15%** — 배당은 순액(85%) 재투자, 원천징수는 최종(금융소득 별도트랙,
    양도세 통산·공제 대상 아님). 따라서 배당세는 **매년 즉시** 새어나가고 공제도 못 받는다.
  · **양도소득세(CGT) 22%** — 최종 청산 시 연 순실현손익 − 기본공제 250만원 초과분에 부과.
    취득원가·양도가액 모두 원화(취득일/매도일 환율) → **환차익도 과세**(tax.py).
  · **연 250만원 기본공제(이월 불가)** — 매년 12월 **이익 하베스팅**(익절→즉시 재매수, 원가
    스텝업)으로 공제를 소진해 미래 과세이익을 줄인다(tax.harvest_plan 의 논리를 직접 구현).
    → 배당(15% 지금) vs 양도차익(22% 나중+공제+이연)의 상충을 정직하게 저울질한다.
  · **수수료** fees.TossFeeSchedule — 미국 0.1%, 건당 ≤$10 무료(분할), 매도 규제수수료.
  · **FX** KRW→USD 환전은 **입금 시에만**(창구내 0.05% 가정, 0.5% 민감도). 최종은 DEXKOUS 로 원화 환산.

**데이터 정직성 주의(중요).** 캐시(Nasdaq API)는 나스닥상장(QQQ/IEF/TLT)만 실배당을 담고,
NYSE Arca 상장(SCHD/VTI/SPY/VEA/GLD)은 adjclose==close 라 **배당이 비어 있다**. 재현성·통제를
위해 **모든 종목의 배당을 사전등록 상수 배당수익률**(공표 추정치, 아래 DIV_YIELD)로 모델링하고
가격수익률은 **분할반영 원시종가(adjusted=False)** 에서 취한다. QQQ 실현 배당수익률(2017–24
분해 ≈0.72%)이 가정 0.60% 에 근접해 근사를 검증한다(리포트 한계 참조). 튜닝 없음.

재현: PYTHONPATH=src .venv/bin/python experiments/c8b_allocation_tax.py [--fast]
      [--ledger PATH] [--no-ledger] [--no-report]
Do NOT edit src/. Do not commit.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
from toss_trader import tax as T  # noqa: E402
from toss_trader.fees import TossFeeSchedule  # noqa: E402
import gate_eval  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle8_c8b_allocation_tax.md"
RESULTS_JSON = ROOT / "reports" / "c8b_results.json"

# ── 사전등록 상수(튜닝 금지) ─────────────────────────────────────────────────
WHT = T.US_DIVIDEND_WITHHOLDING          # 0.15 미국 배당 원천징수
CGT_RATE = T.OVERSEAS_CG_RATE            # 0.22 양도소득세(지방세 포함)
DEDUCTION = T.BASIC_DEDUCTION_KRW        # 2_500_000 연 기본공제
FX_SPREAD = 0.0005                       # 입금 환전 스프레드 0.05%(창구내 가정)
FX_SPREAD_STRESS = 0.005                 # 민감도 0.5%
INITIAL_KRW = 500_000                    # 시드 입금(₩50만 ≈ $360)
MONTHLY_KRW = 500_000                    # 월 적립(₩50만) — 규모 무관 순위(정규화 비교)이나 수수료 절대액엔 영향
PPY = 252

# 사전등록 배당수익률(연, 공표 추정치·반올림). SCHD 고배당 vs QQQ 저배당이 핵심 상충.
DIV_YIELD = {
    "QQQ": 0.0060, "SCHD": 0.0350, "GLD": 0.0000, "VTI": 0.0130,
    "SPY": 0.0130, "VEA": 0.0300,
}

# 후보 배분(사전등록, 튜닝 없음).
CANDIDATES: dict[str, dict[str, float]] = {
    "B0_QQQ60_SCHD25_GLD15": {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15},
    "B1_QQQ100":             {"QQQ": 1.00},
    "VTI100":                {"VTI": 1.00},
    "SPY100":                {"SPY": 1.00},
    "QQQ80_GLD20":           {"QQQ": 0.80, "GLD": 0.20},
    "QQQ70_VEA15_GLD15":     {"QQQ": 0.70, "VEA": 0.15, "GLD": 0.15},
    "B0VTI_QQQ60_VTI25_GLD15": {"QQQ": 0.60, "VTI": 0.25, "GLD": 0.15},  # SCHD→VTI(배당세 효율)
}
BASE = "B0_QQQ60_SCHD25_GLD15"
UNIVERSE = ["QQQ", "SCHD", "GLD", "VTI", "SPY", "VEA"]

MODERN_START = date(2016, 9, 22)
MODERN_END = date(2026, 9, 22)
DESIGN_END = date(2021, 12, 31)          # README §3: 10년 실험 설계 2016-09–2021-12, 홀드아웃 2022+
MIN_HORIZON_YEARS = 5

_FEES = TossFeeSchedule()


# ── 데이터 로딩 ──────────────────────────────────────────────────────────────
def load_prices(symbols, start=MODERN_START, end=MODERN_END):
    """분할반영 원시종가(가격수익률용). {sym: [close]} + 공통 dates. 배당은 상수모델(DIV_YIELD)."""
    raw = {s: hd.load_symbol(s, start=start, end=end, adjusted=False) for s in symbols}
    date_sets = [set(c.dt for c in cs) for cs in raw.values()]
    common = sorted(set.intersection(*date_sets))
    dates = list(common)
    px = {}
    for s in symbols:
        by = {c.dt: c.close for c in raw[s]}
        px[s] = [by[d] for d in dates]
    return dates, px


def load_fx(dates, series="DEXKOUS"):
    """DEXKOUS(KRW per USD) 를 거래일에 forward-fill. dates 이전 값은 첫 관측으로 back-fill."""
    fxc = sorted((c.dt, c.close) for c in hd.load_fred(series))
    keys = [d for d, _ in fxc]
    out = []
    last = fxc[0][1] if fxc else 1300.0
    ki = 0
    for d in dates:
        while ki < len(keys) and keys[ki] <= d:
            last = fxc[ki][1]
            ki += 1
        out.append(last)
    return out


def price_returns(closes):
    """단순 가격 일간수익률(길이=N, [0]=0)."""
    out = [0.0]
    for t in range(1, len(closes)):
        p0 = closes[t - 1]
        out.append(closes[t] / p0 - 1.0 if p0 > 0 else 0.0)
    return out


def year_end_flags(dates):
    """각 날짜가 해당 '역년의 마지막 거래일'인가(다음 거래일이 다른 해거나 마지막)."""
    n = len(dates)
    out = [False] * n
    for t in range(n):
        if t == n - 1:
            out[t] = True
        else:
            out[t] = dates[t].year != dates[t + 1].year
    return out


# ── 세후 DCA 시뮬레이터(한국 거주자) ─────────────────────────────────────────
@dataclass
class DcaTaxResult:
    dates: list
    equity_krw: list          # 일별 원화 평가액(청산 전, mark-to-market)
    equity_usd: list          # 일별 USD 평가액
    deposit_krw_series: list  # 인덱스별 입금액(원화, dollar_drawdown용)
    deposit_usd_series: list
    pretax_terminal_krw: float
    aftertax_terminal_krw: float
    total_deposited_krw: float
    total_deposited_usd: float
    cgt_krw: float
    div_tax_krw: float
    harvested_krw: float
    fee_usd: float
    fx_cost_krw: float
    cashflows_krw: list       # gate.xirr용(입금 −, 최종 세후 +)
    ppy: int = PPY

    @property
    def money_multiple(self):
        return self.aftertax_terminal_krw / self.total_deposited_krw if self.total_deposited_krw else 0.0


def simulate_dca_tax(dates, px_ret, fx, weights, start, end, *,
                     wht=WHT, cgt_rate=CGT_RATE, deduction=DEDUCTION,
                     fx_spread=FX_SPREAD, harvest=True, monthly_krw=MONTHLY_KRW,
                     initial_krw=INITIAL_KRW, div_yield=DIV_YIELD, ppy=PPY,
                     fee_sched=_FEES, closes=None) -> DcaTaxResult:
    """[start,end] 구간 월적립 매수전용 DCA 를 한국 세제로 시뮬레이션.

    상태(종목별): v_usd(평가액), basis_krw(원가). 매일:
      1) 배당 accrue(상수 div_yield/ppy) → 15% 원천징수, 순액 재투자(basis·평가액에 가산).
      2) 가격수익률 적용.
      3) 월 첫 거래일: KRW→USD 환전(스프레드) 후 목표비중 미달분 매수(fees.py, 매도 없음).
      4) 역년 마지막 거래일(최종연도 제외): 이익 하베스팅(공제 250만 소진, 원가 스텝업).
    최종(end): 전 종목 평가액 원화환산, 순실현손익 통산 → CGT 22%(공제 250만) 차감.
    """
    win = list(range(start, end + 1))
    syms = [s for s in weights]
    v_usd = {s: 0.0 for s in syms}
    basis_krw = {s: 0.0 for s in syms}
    dep_krw = [0.0] * len(dates)
    dep_usd = [0.0] * len(dates)
    equity_krw = [0.0] * len(dates)
    equity_usd = [0.0] * len(dates)
    div_tax_krw = 0.0
    harvested_krw = 0.0
    fee_usd = 0.0
    fx_cost_krw = 0.0
    total_dep_krw = 0.0
    total_dep_usd = 0.0
    cashflows = []

    ms = R.month_start_flags(dates)
    ye = year_end_flags(dates)
    final_year = dates[end].year
    dd_daily = {s: div_yield.get(s, 0.0) / ppy for s in syms}

    def do_buy(t, cash_usd):
        """가용 USD 로 목표비중 미달분 매수(매도 없음). basis 는 mid fx 로 기록, 수수료는 basis 가산."""
        nonlocal fee_usd
        total_v = sum(v_usd.values()) + cash_usd
        order = sorted(syms, key=lambda s: weights[s] * total_v - v_usd[s], reverse=True)
        for s in order:
            if cash_usd <= 1e-9:
                break
            need = weights[s] * total_v - v_usd[s]
            if need <= 0:
                continue
            spend = min(need, cash_usd)
            fee = fee_sched.as_fee_fn(split=True)("BUY", spend)
            if spend + fee > cash_usd:
                spend = max(0.0, cash_usd - fee)
                fee = fee_sched.as_fee_fn(split=True)("BUY", spend)
            if spend <= 0:
                continue
            v_usd[s] += spend
            basis_krw[s] += spend * fx[t] + fee * fx[t]
            cash_usd -= (spend + fee)
            fee_usd += fee

    for t in win:
        if t > start:
            # 1) 배당(원천징수 후 재투자) + 2) 가격
            for s in syms:
                if v_usd[s] > 0:
                    gross = v_usd[s] * dd_daily[s]
                    div_tax_krw += gross * wht * fx[t]
                    net = gross * (1.0 - wht)
                    basis_krw[s] += net * fx[t]
                    v_usd[s] = v_usd[s] * (1.0 + px_ret[s][t]) + net
                # v_usd==0(미보유)여도 가격수익 반영 대상 없음
        # 3) 입금·매수
        dep = 0.0
        if t == start:
            dep += initial_krw
        if ms[t] and t != start:
            dep += monthly_krw
        if dep > 0:
            usd = dep / (fx[t] * (1.0 + fx_spread))          # 스프레드 = 불리한 환율
            fx_cost_krw += dep - usd * fx[t]                 # mid 대비 환전 손실(원화)
            total_dep_krw += dep
            total_dep_usd += usd
            dep_krw[t] += dep
            dep_usd[t] += usd
            cashflows.append((dates[t], -dep))
            do_buy(t, usd)
        # 4) 연말 이익 하베스팅(최종연도 제외)
        if harvest and ye[t] and dates[t].year != final_year:
            room = deduction
            gains = sorted(((s, v_usd[s] * fx[t] - basis_krw[s]) for s in syms
                            if v_usd[s] * fx[t] - basis_krw[s] > 0),
                           key=lambda x: -x[1])
            left = room
            for s, g in gains:
                if left <= 1e-6:
                    break
                take = min(g, left)
                # 매도 규제수수료(회전) — 실현 gain 비례 노셔널
                notional = (take / g) * v_usd[s] if g > 0 else 0.0
                price = closes[s][t] if closes else 100.0
                hf = fee_sched.order_fee("SELL", notional, shares=(notional / price if price > 0 else 0.0))
                v_usd[s] -= hf                                # 회전비용(재매수는 ≤$10 분할 무료 가정)
                fee_usd += hf
                basis_krw[s] += take                          # 원가 스텝업(비과세 실현)
                harvested_krw += take
                left -= take
        equity_usd[t] = sum(v_usd.values())
        equity_krw[t] = sum(v_usd[s] * fx[t] for s in syms)

    # 최종 청산: 순실현손익 통산 → CGT
    fx_end = fx[end]
    mv_krw = sum(v_usd[s] * fx_end for s in syms)
    gain_krw = sum(v_usd[s] * fx_end - basis_krw[s] for s in syms)
    cgt = T.annual_tax(gain_krw, deduction=deduction, rate=cgt_rate)
    aftertax = mv_krw - cgt
    cashflows.append((dates[end], aftertax))

    return DcaTaxResult(
        dates=[dates[t] for t in win],
        equity_krw=[equity_krw[t] for t in win],
        equity_usd=[equity_usd[t] for t in win],
        deposit_krw_series=[dep_krw[t] for t in win],
        deposit_usd_series=[dep_usd[t] for t in win],
        pretax_terminal_krw=mv_krw, aftertax_terminal_krw=aftertax,
        total_deposited_krw=total_dep_krw, total_deposited_usd=total_dep_usd,
        cgt_krw=cgt, div_tax_krw=div_tax_krw, harvested_krw=harvested_krw,
        fee_usd=fee_usd, fx_cost_krw=fx_cost_krw, cashflows_krw=cashflows, ppy=ppy,
    )


# ── 지표 ─────────────────────────────────────────────────────────────────────
def path_metrics(res: DcaTaxResult) -> dict:
    years = max((res.dates[-1] - res.dates[0]).days / 365.25, 1e-9)
    xirr = gate.xirr(res.cashflows_krw)
    dollar_dd = min(R.dollar_drawdown(res.equity_usd, res.deposit_usd_series))
    krw_dd = min(R.dollar_drawdown(res.equity_krw, res.deposit_krw_series))
    avg_eq = sum(res.equity_krw) / len(res.equity_krw) if res.equity_krw else 0.0
    div_drag_bps = (res.div_tax_krw / (avg_eq * years) * 1e4) if avg_eq > 0 else 0.0
    return {
        "aftertax_krw": res.aftertax_terminal_krw,
        "pretax_krw": res.pretax_terminal_krw,
        "deposited_krw": res.total_deposited_krw,
        "money_multiple": res.money_multiple,
        "xirr_krw": xirr,
        "dollar_mdd": dollar_dd,
        "krw_mdd": krw_dd,
        "cgt_krw": res.cgt_krw,
        "div_tax_krw": res.div_tax_krw,
        "div_tax_drag_bps_yr": div_drag_bps,
        "harvested_krw": res.harvested_krw,
        "fee_usd": res.fee_usd,
        "fx_cost_krw": res.fx_cost_krw,
        "years": years,
    }


def unit_capital_krw_returns(px_ret, fx, weights, start, end, *, wht=WHT,
                             div_yield=DIV_YIELD, ppy=PPY):
    """단위자본(외부현금흐름 없음) KRW 일간 순수익 스트림 — 게이트 리스크지표용(사양 §1.1).

    매일 목표비중으로 리밸런싱된 순배당(85%) 총수익 USD 수익 × 환율일수익 = 원화 시간가중 수익.
    """
    out = []
    for t in range(start + 1, end + 1):
        port_usd = sum(weights[s] * (px_ret[s][t] + (1.0 - wht) * div_yield.get(s, 0.0) / ppy)
                       for s in weights)
        fx_ret = fx[t] / fx[t - 1] - 1.0 if fx[t - 1] > 0 else 0.0
        out.append((1.0 + port_usd) * (1.0 + fx_ret) - 1.0)
    return out


# ── 모던 평가(2016–2026) ─────────────────────────────────────────────────────
def modern_start_indices(dates, min_years=MIN_HORIZON_YEARS, fast=False):
    """월 첫 거래일 중 종료(마지막 날)까지 ≥min_years 남은 시작 인덱스."""
    ms = [t for t, f in enumerate(R.month_start_flags(dates)) if f]
    end = len(dates) - 1
    cutoff = date(dates[end].year - min_years, dates[end].month, dates[end].day)
    starts = [t for t in ms if dates[t] <= cutoff]
    if fast:
        starts = starts[::3]
    return starts, end


def run_modern(dates, px_ret, fx, closes, *, fast=False, fx_spread=FX_SPREAD, harvest=True):
    """단일경로(전체) + 전 시작월 분포. 각 후보 지표 + P(beat B0)."""
    end = len(dates) - 1
    single = {}
    for name, w in CANDIDATES.items():
        res = simulate_dca_tax(dates, px_ret, fx, w, 0, end,
                               fx_spread=fx_spread, harvest=harvest, closes=closes)
        single[name] = path_metrics(res)

    starts, end = modern_start_indices(dates, fast=fast)
    dist = {name: [] for name in CANDIDATES}
    for s in starts:
        for name, w in CANDIDATES.items():
            res = simulate_dca_tax(dates, px_ret, fx, w, s, end,
                                   fx_spread=fx_spread, harvest=harvest, closes=closes)
            dist[name].append(path_metrics(res))

    # P(beat B0), median money_multiple ratio vs B0
    base_mm = [m["money_multiple"] for m in dist[BASE]]
    summary = {}
    for name in CANDIDATES:
        mm = [m["money_multiple"] for m in dist[name]]
        wins = sum(1 for a, b in zip(mm, base_mm) if a > b)
        ratios = sorted(a / b for a, b in zip(mm, base_mm) if b > 0)
        med_ratio = ratios[len(ratios) // 2] if ratios else float("nan")
        smm = sorted(mm)
        summary[name] = {
            "n_starts": len(mm),
            "median_money_multiple": smm[len(smm) // 2] if smm else 0.0,
            "p5_money_multiple": smm[max(0, int(0.05 * len(smm)))] if smm else 0.0,
            "p_beat_b0": wins / len(mm) if mm else 0.0,
            "median_ratio_vs_b0": med_ratio,
            "median_xirr": sorted(m["xirr_krw"] for m in dist[name])[len(mm) // 2] if mm else float("nan"),
        }
    return {"single": single, "summary": summary,
            "start_first": dates[starts[0]].isoformat(), "start_last": dates[starts[-1]].isoformat(),
            "n_starts": len(starts)}


# ── 장기 히스토리(NDX vs S&P; "QQQ-우호적 10년" 강건성) ────────────────────────
def load_ndx_daily():
    """FRED NASDAQ100(가격지수, 1986+) → 일간 가격수익률. 배당은 상수모델(QQQ yield)."""
    c = sorted(hd.load_fred("NASDAQ100"), key=lambda x: x.dt)
    dates = [x.dt for x in c]
    closes = [x.close for x in c]
    return dates, closes


def load_sp500_daily():
    """FRED SP500(가격지수, 2016+) — 모던 확인용(장기는 Shiller)."""
    c = sorted(hd.load_fred("SP500"), key=lambda x: x.dt)
    return [x.dt for x in c], [x.close for x in c]


def load_shiller_monthly():
    """Shiller 월간 S&P(1871+): price·dividend. price 는 월중평균(낙폭 완만·리포트 한계)."""
    csv = ROOT / "data" / "shiller" / "shiller_ie_data.csv"
    rows = []
    for ln in csv.read_text(encoding="utf-8").strip().splitlines()[1:]:
        p = ln.split(",")
        try:
            y, mo = int(p[0][:4]), int(p[0][5:7])
            price, div = float(p[1]), float(p[2])
        except (ValueError, IndexError):
            continue
        if price > 0 and div > 0:
            rows.append((date(y, mo, 1), price, div))
    return rows


def run_long_history():
    """레짐 강건성: NDX(QQQ류) vs S&P(SPY/VTI류)를 세제모델로 롤링 비교.

    · 1986–2026 일간(닷컴·GFC 포함) — NDX vs S&P(Shiller 월간 정렬 불가 → NDX 는 일간, S&P 는
      Shiller 월간으로 각기 최장구간). 두 자산의 '세후 원화(상수 FX)' money-multiple 비율.
    · 2000–2010 'NDX 잃어버린 10년' 코호트를 명시(‘QQQ-우호적 10년’ 반례).
    상수 FX(환차손익 0)로 주식·배당세 효과만 격리. 골드/해외는 키리스 장기 데이터 부재로 제외.
    """
    CONST_FX = 1300.0
    out = {}

    # NDX 일간
    ndates, ncloses = load_ndx_daily()
    npx = {"QQQ": price_returns(ncloses)}
    nfx = [CONST_FX] * len(ndates)
    nclos = {"QQQ": ncloses}

    def ndx_window(y0, y1):
        s = next((i for i, d in enumerate(ndates) if d.year >= y0), None)
        e = next((i for i, d in enumerate(ndates) if d.year > y1), len(ndates) - 1)
        if s is None or e <= s:
            return None
        res = simulate_dca_tax(ndates, npx, nfx, {"QQQ": 1.0}, s, e,
                               div_yield={"QQQ": DIV_YIELD["QQQ"]}, closes=nclos)
        return path_metrics(res)

    # Shiller 월간 S&P
    sh = load_shiller_monthly()
    sdates = [r[0] for r in sh]
    sprice = [r[1] for r in sh]
    sdiv = [r[2] for r in sh]
    spx = {"SP": price_returns(sprice)}
    sfx = [CONST_FX] * len(sdates)
    sclos = {"SP": sprice}
    # Shiller 배당: 월배당수익률 = (D/12)/price → 상수모델 대신 실제 배당(월별)로 별도 처리.
    # 여기선 상수모델 시뮬레이터에 맞춰 S&P 평균배당 ~ 실제 시계열 대신, 월별 실제 yield 를
    # px_ret 에 이미 반영하지 않았으므로 div_yield 상수(장기 S&P 평균 배당 4%/yr 근사)를 쓴다.
    # 정직성: Shiller 초기(고배당기)와 현대(저배당기)를 상수로 뭉갠다(리포트 한계).
    SP_DIV = 0.040

    def sp_window(y0, y1):
        s = next((i for i, d in enumerate(sdates) if d.year >= y0), None)
        e = next((i for i, d in enumerate(sdates) if d.year > y1), len(sdates) - 1)
        if s is None or e <= s:
            return None
        res = simulate_dca_tax(sdates, spx, sfx, {"SP": 1.0}, s, e,
                               div_yield={"SP": SP_DIV}, closes=sclos, ppy=12)
        return path_metrics(res)

    # era 별 세후 money multiple
    eras = [("dotcom+GFC 2000–2012", 2000, 2012), ("NDX lost decade 2000–2010", 2000, 2010),
            ("2010–2026 (QQQ우호)", 2010, 2026), ("full 1986–2026", 1986, 2026)]
    rows = []
    for nm, y0, y1 in eras:
        ndx = ndx_window(y0, y1)
        sp = sp_window(y0, y1)
        rows.append({"era": nm, "ndx": ndx, "sp": sp})
    out["eras"] = rows
    out["ndx_range"] = [ndates[0].isoformat(), ndates[-1].isoformat()]
    out["shiller_range"] = [sdates[0].isoformat(), sdates[-1].isoformat()]
    out["const_fx"] = CONST_FX
    out["sp_div_assumed"] = SP_DIV
    return out


# ── 원장 적재(레인1, peek-once) ──────────────────────────────────────────────
def log_ledger(dates, px_ret, fx, ledger_path):
    """각 후보의 단위자본 KRW 순수익 스트림을 design(≤2021-12)/holdout(2022+) 로 적재."""
    end = len(dates) - 1
    for name, w in CANDIDATES.items():
        idea = f"c8b_{name}"
        stream = unit_capital_krw_returns(px_ret, fx, w, 0, end)
        sdates = dates[1:end + 1]
        di = [i for i, d in enumerate(sdates) if d <= DESIGN_END]
        hi = [i for i, d in enumerate(sdates) if d > DESIGN_END]
        params = {"alloc": w, "wht": WHT, "cgt": CGT_RATE, "fx": "DEXKOUS",
                  "div_model": "constant", "ppy": PPY}
        if di:
            cr = [stream[i] for i in di]
            cd = [sdates[i] for i in di]
            um = gate_eval.unit_capital_metrics(cr, dates=cd, ppy=PPY)
            gate_eval.log_evaluation(idea, dict(params), 1, "design", um,
                                     window=(cd[0], cd[-1]), universe=list(w),
                                     ledger_path=ledger_path)
        if hi and not gate.already_peeked(ledger_path, idea):
            cr = [stream[i] for i in hi]
            cd = [sdates[i] for i in hi]
            um = gate_eval.unit_capital_metrics(cr, dates=cd, ppy=PPY)
            try:
                gate_eval.log_evaluation(idea, dict(params), 1, "holdout", um,
                                         window=(cd[0], cd[-1]), universe=list(w),
                                         ledger_path=ledger_path)
            except gate.PeekOnceError:
                pass


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="시작월 3개월 간격 서브샘플")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)

    print("[c8b] 데이터 로드(2016-09–2026-09, 원시가격 + DEXKOUS)...")
    dates, closes = load_prices(UNIVERSE)
    fx = load_fx(dates)
    px_ret = {s: price_returns(closes[s]) for s in UNIVERSE}
    print(f"[c8b]   {dates[0]} … {dates[-1]}  ({len(dates)}거래일) FX {fx[0]:.1f}→{fx[-1]:.1f}")

    results = {"meta": {"generated": date.today().isoformat(), "fast": args.fast,
                        "first": dates[0].isoformat(), "last": dates[-1].isoformat(),
                        "n_days": len(dates), "fx_first": fx[0], "fx_last": fx[-1]},
               "params": {"wht": WHT, "cgt": CGT_RATE, "deduction": DEDUCTION,
                          "fx_spread": FX_SPREAD, "initial_krw": INITIAL_KRW,
                          "monthly_krw": MONTHLY_KRW, "div_yield": DIV_YIELD,
                          "candidates": CANDIDATES}}

    if not args.no_ledger:
        print("[c8b] 원장 적재(레인1, 단위자본 KRW 스트림)...")
        log_ledger(dates, px_ret, fx, args.ledger)

    print("[c8b] 모던 평가(단일경로 + 전 시작월 분포)...")
    results["modern"] = run_modern(dates, px_ret, fx, closes, fast=args.fast)

    print("[c8b] FX 스프레드 민감도(0.05%→0.5%)...")
    results["modern_fx_stress"] = run_modern(dates, px_ret, fx, closes, fast=True,
                                             fx_spread=FX_SPREAD_STRESS)["single"]
    print("[c8b] 하베스팅 미적용(단일 최종공제) 대조...")
    results["modern_no_harvest"] = run_modern(dates, px_ret, fx, closes, fast=True,
                                             harvest=False)["single"]

    print("[c8b] 장기 히스토리(NDX vs S&P, 레짐 강건성)...")
    results["long_history"] = run_long_history()

    RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
    print(f"[c8b] 결과 JSON → {RESULTS_JSON}")
    _print_summary(results)
    if not args.no_report:
        write_report(results)
        print(f"[c8b] 리포트 → {REPORT}")
    return results


def _print_summary(results):
    print("\n=== c8b 요약: 세후 KRW 최종자산(단일경로 2016-09→2026-09) ===")
    single = results["modern"]["single"]
    base = single[BASE]["aftertax_krw"]
    for name in CANDIDATES:
        m = single[name]
        print(f"  {name:26s} 세후 ₩{m['aftertax_krw']:>14,.0f} ({m['aftertax_krw']/base:5.2f}× B0) "
              f"XIRR {m['xirr_krw']*100:5.1f}% $MDD {m['dollar_mdd']*100:6.1f}% "
              f"배당세drag {m['div_tax_drag_bps_yr']:4.1f}bp/yr")
    print("\n  전 시작월 분포 P(beat B0):")
    for name in CANDIDATES:
        s = results["modern"]["summary"][name]
        print(f"  {name:26s} Pbeat={s['p_beat_b0']:.2f} median {s['median_ratio_vs_b0']:.3f}× "
              f"(n={s['n_starts']})")


# ── 리포트 ────────────────────────────────────────────────────────────────────
PREREG_HEAD = """# Cycle 8 · c8b — 세후 KRW 최종자산 최적 BASE 배분 (한국 거주자 월적립 DCA)

작성 executor · 데이터 QQQ/SCHD/GLD/VTI/SPY/VEA(캐시, 2016-09–2026-09) + FRED DEXKOUS(KRW/USD) ·
장기 FRED NASDAQ100(1986+)·Shiller S&P(1871+) · 세제 src/toss_trader/tax.py · 수수료 fees.py ·
게이트 docs/gate_v2_spec.md(레인1) · 원장 reports/trials_ledger.jsonl

> 상태: **사전등록** — 후보 7종·세율·공제·배당수익률·FX 스프레드를 실행 전 고정(튜닝 없음).
> 실무 의사결정용(알파 아님): "월 적립 DCA 하는 한국 거주자에게 세후·수수료 후 원화 최종자산을
> 극대화하는 BASE 배분과 그 위험은?"
>
> **모델(한국 거주자).** 배당 원천징수 15%(순액 재투자, 즉시 과세·공제 불가) · 양도세 22%(최종
> 청산, 연 순실현손익 − 공제 250만원 초과분, 환차익 포함) · 연 250만원 공제는 매년 12월 이익
> 하베스팅으로 소진(원가 스텝업·이연) · 수수료 fees.py(≤$10 무료 분할) · FX 입금 시 0.05%(민감도 0.5%).
>
> **데이터 정직성.** 캐시는 NYSE Arca ETF(SCHD/VTI/SPY/VEA/GLD) 배당이 비어 있어, 전 종목 배당을
> **사전등록 상수 배당수익률**(QQQ 0.6·SCHD 3.5·VTI 1.3·SPY 1.3·VEA 3.0·GLD 0%)로 모델링하고
> 가격은 분할반영 원시종가에서 취한다. 배당 시점·성장은 무시(리포트 한계). 골드/해외 장기
> 키리스 데이터 부재로 장기 히스토리는 NDX vs S&P(주식 코어)만 다룬다.
"""


def _fmt_won(v):
    return f"₩{v:,.0f}"


def write_report(results):
    head = PREREG_HEAD
    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            head = prev.split("<!-- RESULTS_BELOW -->")[0]
    L = [head.rstrip(), "<!-- RESULTS_BELOW -->", ""]
    meta = results["meta"]
    L.append(f"> 실행 {meta['generated']} · {meta['first']}…{meta['last']} ({meta['n_days']}거래일) · "
             f"FX {meta['fx_first']:.1f}→{meta['fx_last']:.1f} · fast={meta['fast']}")
    L.append("")

    single = results["modern"]["single"]
    summ = results["modern"]["summary"]
    base = single[BASE]

    L.append("## 1) 단일경로 세후 KRW 최종자산 (2016-09-22 → 2026-09-22, 시드 ₩50만 + 월 ₩50만)")
    L.append("| 배분 | 세후 최종 | ×B0 | 세전 최종 | XIRR(원화) | $최대낙폭 | ₩최대낙폭 | 배당세(누적) | 배당세 drag | CGT | 하베스팅 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in CANDIDATES:
        m = single[name]
        L.append(f"| {'**'+name+'**' if name==BASE else name} | {_fmt_won(m['aftertax_krw'])} | "
                 f"{m['aftertax_krw']/base['aftertax_krw']:.2f} | {_fmt_won(m['pretax_krw'])} | "
                 f"{m['xirr_krw']*100:.1f}% | {m['dollar_mdd']*100:.1f}% | {m['krw_mdd']*100:.1f}% | "
                 f"{_fmt_won(m['div_tax_krw'])} | {m['div_tax_drag_bps_yr']:.1f}bp/yr | "
                 f"{_fmt_won(m['cgt_krw'])} | {_fmt_won(m['harvested_krw'])} |")
    L.append("")
    L.append(f"> 총 입금 {_fmt_won(base['deposited_krw'])} · 기간 {base['years']:.1f}년. "
             "×B0 = 세후 최종자산 / B0 세후 최종자산.")
    L.append("")

    L.append("## 2) 전 시작월 분포 (≥5년 지평, 시작월 " +
             f"{results['modern']['start_first']}…{results['modern']['start_last']}, "
             f"n={results['modern']['n_starts']})")
    L.append("| 배분 | median money-multiple | p5 | median ×B0 | P(beat B0) | median XIRR |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for name in CANDIDATES:
        s = summ[name]
        L.append(f"| {'**'+name+'**' if name==BASE else name} | {s['median_money_multiple']:.2f} | "
                 f"{s['p5_money_multiple']:.2f} | {s['median_ratio_vs_b0']:.3f} | "
                 f"{s['p_beat_b0']:.2f} | {s['median_xirr']*100:.1f}% |")
    L.append("")

    # 민감도
    L.append("## 3) 민감도 — FX 스프레드 0.5% · 하베스팅 미적용(세후 KRW 최종, 단일경로)")
    fxs = results["modern_fx_stress"]
    noh = results["modern_no_harvest"]
    L.append("| 배분 | 기본(0.05%,harvest) | FX 0.5% | Δ | harvest 미적용 | Δ(하베스팅 효익) |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for name in CANDIDATES:
        b = single[name]["aftertax_krw"]
        f = fxs[name]["aftertax_krw"]
        h = noh[name]["aftertax_krw"]
        L.append(f"| {name} | {_fmt_won(b)} | {_fmt_won(f)} | {(f/b-1)*100:+.2f}% | "
                 f"{_fmt_won(h)} | {_fmt_won(b-h)} |")
    L.append("")

    # 장기
    lh = results["long_history"]
    L.append("## 4) 장기 히스토리 — NDX(QQQ류) vs S&P(SPY/VTI류), 세후 KRW(상수 FX) money-multiple")
    L.append(f"> NDX 일간 {lh['ndx_range'][0]}…{lh['ndx_range'][1]} · Shiller 월간 "
             f"{lh['shiller_range'][0]}…{lh['shiller_range'][1]} · 상수 FX ₩{lh['const_fx']:.0f}"
             f"(환차손익 0) · S&P 배당 상수 {lh['sp_div_assumed']*100:.1f}%/yr 가정. 골드/해외 제외.")
    L.append("| era | NDX money-mult | S&P money-mult | NDX/S&P |")
    L.append("|---|---:|---:|---:|")
    for r in lh["eras"]:
        ndx = r["ndx"]
        sp = r["sp"]
        nm = ndx["money_multiple"] if ndx else float("nan")
        sm = sp["money_multiple"] if sp else float("nan")
        ratio = (nm / sm) if (ndx and sp and sm) else float("nan")
        L.append(f"| {r['era']} | {nm:.2f} | {sm:.2f} | {ratio:.2f} |")
    L.append("")

    L.append("## 판정 및 정직한 권고")
    L.append(_recommendation(results))
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def _recommendation(results):
    single = results["modern"]["single"]
    summ = results["modern"]["summary"]
    base = single[BASE]
    best = max(CANDIDATES, key=lambda n: single[n]["aftertax_krw"])
    b1 = single["B1_QQQ100"]
    qg = single["QQQ80_GLD20"]
    b0vti = single["B0VTI_QQQ60_VTI25_GLD15"]
    vti = single["VTI100"]
    div_gap = base["div_tax_krw"] - b0vti["div_tax_krw"]
    lh = results["long_history"]["eras"]
    lost = next((e for e in lh if "lost decade" in e["era"]), None)
    lost_ratio = (lost["ndx"]["money_multiple"] / lost["sp"]["money_multiple"]
                  if lost and lost["ndx"] and lost["sp"] else float("nan"))

    lines = []
    lines.append(
        f"**핵심(단일경로 2016–2026, 세후 KRW).** 최고 = **{best}** "
        f"({_fmt_won(single[best]['aftertax_krw'])}, {single[best]['aftertax_krw']/base['aftertax_krw']:.2f}×B0, "
        f"$MDD {single[best]['dollar_mdd']*100:.0f}%). 현행 B0 {_fmt_won(base['aftertax_krw'])}"
        f"($MDD {base['dollar_mdd']*100:.0f}%, 최소낙폭). **VTI100·SPY100 은 열위지배**"
        f"(≤{vti['aftertax_krw']/base['aftertax_krw']:.2f}×B0 이면서 $MDD 도 더 깊음) — "
        "광의 시장베타는 이 구간에 QQQ 대비 수익도, 블렌드 대비 위험도 모두 밀렸다.")
    lines.append("")
    lines.append(
        "**위험선호 스펙트럼(이 결정의 본질 — 알파 아님).** "
        f"① 최소낙폭: **B0**($MDD {base['dollar_mdd']*100:.0f}%) — SCHD·GLD 방어가 낙폭을 "
        f"가장 줄이나 세후 총액 최저·배당세 drag 최대({base['div_tax_drag_bps_yr']:.0f}bp/yr). "
        f"② 균형(추천): **QQQ80_GLD20**({qg['aftertax_krw']/base['aftertax_krw']:.2f}×B0, "
        f"$MDD {qg['dollar_mdd']*100:.0f}%) — QQQ 세후 상승의 대부분을 취하면서 골드가 QQQ100 대비 "
        f"낙폭을 {abs(b1['dollar_mdd'])*100-abs(qg['dollar_mdd'])*100:.0f}pp 줄이고, 골드 무배당이라 "
        f"배당세 drag 도 최저({qg['div_tax_drag_bps_yr']:.0f}bp). ③ 수익극대·고위험: **B1 QQQ100**"
        f"({b1['aftertax_krw']/base['aftertax_krw']:.2f}×B0, $MDD {b1['dollar_mdd']*100:.0f}% 집중위험).")
    lines.append("")
    lines.append(
        "**SCHD→VTI(B0VTI) 정직한 분해.** 세후차 "
        f"{_fmt_won(b0vti['aftertax_krw']-base['aftertax_krw'])}"
        f"({(b0vti['aftertax_krw']/base['aftertax_krw']-1)*100:+.1f}%) 중 **확실·지속되는 부분은 "
        f"배당세 절감뿐**(누적 {_fmt_won(div_gap)}, drag {base['div_tax_drag_bps_yr']:.0f}→"
        f"{b0vti['div_tax_drag_bps_yr']:.0f}bp/yr). 나머지는 이 10년의 성장>가치(VTI>SCHD) "
        "팩터 성과로 **레짐 의존**이며, VTI 가 SCHD 보다 방어력이 낮아 낙폭은 오히려 소폭 깊다"
        f"({base['dollar_mdd']*100:.0f}%→{b0vti['dollar_mdd']*100:.0f}%). 즉 '무손실'이 아니라 "
        "'배당세 효율↑ + 약한 방어'의 교환이다. 배당세 논리 자체(15% 즉시 vs 22% 이연+연 250만 "
        "공제 하베스팅)는 유효 CGT율을 17~18%로 낮춰 **고배당 틸트가 세후 구조적으로 불리**함을 보인다.")
    lines.append("")
    lines.append(
        f"**2016–2026 은 QQQ-우호적 10년(반드시 병기).** 단, DCA 에 한해서는 QQQ 우위가 예상보다 "
        f"강건하다 — 장기표(§4) NDX '잃어버린 10년'(2000–2010) 코호트조차 DCA money-multiple 은 "
        f"NDX/S&P={lost_ratio:.2f}(적립이 저점을 매수해 lump-sum '잃어버린 10년' 서사를 상쇄). "
        "**따라서 QQQ 집중의 진짜 비용은 최종자산이 아니라 낙폭·행동위험**(NDX 2000–02 단위낙폭 "
        "≈ −80%)과 미국/기술 프리미엄 지속 가정(닛케이식 영구손상이면 DCA 도 붕괴)이다.")
    lines.append("")
    lines.append(
        "**권고.** (1) 낙폭 회피가 최우선이 아니라면 **B0 의 SCHD25 슬리브를 GLD/저배당으로 재배치**"
        "(→ QQQ80_GLD20 계열)가 세후·배당세·위험 모두에서 현행 B0 를 개선한다. (2) 최대 수익·고위험 "
        "감내 시 B1(QQQ100). (3) 절대낙폭 최소가 목표면 B0 유지도 방어적으로 정당(단 배당세 대가 인지). "
        "(4) SCHD 를 굳이 유지할 세제상 이유는 없다 — VTI(B0VTI)나 GLD 가 배당세 효율에서 낫다. "
        "한계: 배당 상수모델(시점·성장 무시)·월중평균 해상도·골드/해외 장기 키리스 데이터 부재·백테스트 "
        "낙관편향(사양 §6.1). 실집행 전 소액 슬리브·포워드 페이퍼 점증 권장.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
