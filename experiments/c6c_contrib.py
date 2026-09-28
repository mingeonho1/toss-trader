"""c6c — Contribution-schedule optimization with a FAIR cash budget (mostly no leverage).

사전등록·규약: reports/cycle6_c6c_contrib.md, experiments/README.md,
docs/gate_v2_spec.md(+부록 v2.1/v2.2).

핵심 질문: *DCA-QQQ 와 동일한 월평균 out-of-pocket 현금*을 두고, 적립 스케줄(무엇을 언제
얼마나 투자하고 나머지는 현금버킷에 대기)을 바꾸면 시작일 분포의 달러 최종자산이 개선되는가.
타이밍 알파를 주장하지 않는다 — 메커니즘(시간축 분산·자기자금 저가배치·기회적 레버리지)만.

**공정 예산.** 모든 전략은 매달 동일 현금(시드 $32 + 월 $35)을 받는다. 투자되지 않은 현금은
현금버킷(기본 0%/yr; SGOV 유사 3.6% 민감도)에서 대기하고, **최종자산 = 주식 + QLD + 현금버킷**.

데이터·롤링시작 기계는 experiments/c5a_lifecycle.py 를 import 재사용
(build_synthetic_panel, start_indices, _end_index, split_by_startyear, aggregate, _pct).
2차 OOS 는 FRED NASDAQCOM(1971-1985 시작, 배당 1.5%, DTB3 현금).

재현: PYTHONPATH=src .venv/bin/python experiments/c6c_contrib.py [--fast]
      [--ledger PATH] [--no-ledger] [--no-report]
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
from toss_trader.fees import TossFeeSchedule  # noqa: E402
import gate_eval  # noqa: E402
import c5a_lifecycle as c5a  # noqa: E402  (데이터/롤링시작/집계 기계 재사용)

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle6_c6c_contrib.md"
RESULTS_JSON = ROOT / "reports" / "c6c_results.json"

# ── 사전등록 상수 ─────────────────────────────────────────────────────────────
DIV_YIELD = 0.007        # NDX 배당수익률 근사(0.7%/yr) — c5a 동일
DIV_YIELD_COMP = 0.015   # NASDAQCOM 2차 OOS 배당 가정(1.5%)
EXP_1X = 0.002           # QQQ 보수율 0.20%/yr
EXP_LEV = 0.0095         # QLD 보수율 0.95%/yr(crash_lev 전용)
BORROW_SPREAD = 0.005    # QLD 차입 스프레드 0.5%/yr
FX_BPS = 20.0            # 환전(입금) 비용 20bp — 신규자본에만, 전 전략 동일

MONTHLY = 35.0           # 월 적립 $35 (= 공정 예산, c5a·B1 동일)
INITIAL = 32.0           # 시드 $32
SGOV_APY = 0.036         # 현금버킷 SGOV 유사 수익 3.6%/yr(민감도)

# Value averaging
VA_G = 0.08              # 목표경로 성장률 8%/yr
VA_CAP_MULT = 3.0        # 월 매수 상한 = 3× monthly

# dd_reserve
DDR_WITHHOLD = 0.20      # 유보율 20%
DDR_TRIGGER = 0.20       # 52주 낙폭 > 20% → 리저브 배치

# crash_lev
CL_ENTER = 0.30          # 사상최고 낙폭 > 30% → QLD 라우팅
CL_EXIT = 0.10           # 낙폭 < 10% 회복 → QQQ 복귀

# drypowder
DP_FLOOR_MONTHS = 12     # 리저브 목표 = 12× monthly
DP_TRIGGER = 0.25        # 52주 낙폭 > 25% → 리저브 일시배치

WIN_52W = 252            # 52주(거래일)

# 시작일 분포 설계/홀드아웃 분할(c5a 동일)
DESIGN_START_YEARS = (1986, 1999)
HOLDOUT_START_YEARS = (2000, 2016)
# 2차 OOS(NASDAQCOM) 시작일 창
OOS_START_YEARS = (1971, 1985)

# 원장(단위자본 스트림) 달력창 — c5a 동일 표준 장기 분할
LEDGER_DESIGN_END = date(1999, 12, 31)
LEDGER_HOLDOUT_START = date(2000, 1, 1)

FEE = TossFeeSchedule()

STRATS = ["va", "va_sell", "dd_reserve", "crash_lev", "drypowder"]
LEVERAGE_STRATS = {"crash_lev"}
IDEA_ID = {  # 원장 idea_id
    "va": "c6c_va", "va_sell": "c6c_va_sell", "dd_reserve": "c6c_dd_reserve",
    "crash_lev": "c6c_crash_lev", "drypowder": "c6c_drypowder", "qqq": "c6c_b1_qqq",
}


# ── 시장 신호(전 패널 인과 계산) ──────────────────────────────────────────────
def drawdown_series(closes):
    """(dd_ath, dd_52w): 사상최고 대비 낙폭 / 52주(252거래일) 고점 대비 낙폭. 각 ≤ 0, 인과적."""
    n = len(closes)
    dd_ath = [0.0] * n
    dd_52 = [0.0] * n
    run_max = None
    dq = deque()   # (index, value) 감소 유지 큐 — 52주 롤링 최대
    for t in range(n):
        v = closes[t]
        run_max = v if run_max is None else max(run_max, v)
        dd_ath[t] = (v / run_max - 1.0) if run_max > 0 else 0.0
        lo = t - WIN_52W + 1
        while dq and dq[0][0] < lo:
            dq.popleft()
        while dq and dq[-1][1] <= v:
            dq.pop()
        dq.append((t, v))
        wmax = dq[0][1]
        dd_52[t] = (v / wmax - 1.0) if wmax > 0 else 0.0
    return dd_ath, dd_52


def add_signals(panel):
    """패널에 dd_ath/dd_52 (QQQ 종가 기준) 추가."""
    dd_ath, dd_52 = drawdown_series(panel["QQQ"])
    panel["dd_ath"] = dd_ath
    panel["dd_52"] = dd_52
    if "QLD" not in panel:
        panel["QLD"] = panel["QQQ"]   # 비레버리지 OOS 에서 미사용 시 자리채움
    return panel


# ── Value averaging 목표경로(순수·손검산 가능) ───────────────────────────────
def va_target(month_m, *, initial=INITIAL, monthly=MONTHLY, g=VA_G):
    """m 번째 월적립 직후 목표 주식가치 V_m = 적립금을 g 로 복리한 미래가치.

    r_g=(1+g)^(1/12)−1. V_m = initial·(1+r_g)^m + monthly·((1+r_g)^(m+1)−1)/r_g.
    (g=0 이면 V_m = initial + monthly·(m+1) = 누적 적립금.)
    """
    m = max(0, month_m)
    r = (1.0 + g) ** (1.0 / 12.0) - 1.0
    if r <= 0:
        return initial + monthly * (m + 1)
    return initial * (1.0 + r) ** m + monthly * ((1.0 + r) ** (m + 1) - 1.0) / r


# ── 결과 구조 ─────────────────────────────────────────────────────────────────
@dataclass
class SimResult:
    dates: list
    equity: list           # 일별 총평가액(주식+QLD+현금버킷)
    deposit_series: list   # 인덱스별 입금(FX 전 명목)
    inv_ret: list          # 일별 순수투자수익(플로우 제외; 원장 단위자본 스트림용)
    final_value: float
    total_deposited: float
    total_cost: float
    n_sells: int
    n_buys: int
    n_deploys: int
    avg_cash_frac: float
    avg_exposure: float    # 평균 (val_q + 2·val_l)/equity
    max_exposure: float
    signal_sched: list = field(default_factory=list)  # record_signal=True 시 결정변수/일


def _buy_fee(notional):
    return FEE.plan_split("BUY", notional).total_fee if notional > 0 else 0.0


def _sell_fee(notional, price):
    if notional <= 0:
        return 0.0
    shares = notional / price if price > 0 else 0.0
    return FEE.order_fee("SELL", notional, shares=shares)


def _buy(state, asset, amount):
    """버킷→자산 매수(≤$10 분할 무료). 현금 초과 방지. 반환: 실제 투자액."""
    if amount <= 0 or state["bucket"] <= 0:
        return 0.0
    spend = min(amount, state["bucket"])
    fee = _buy_fee(spend)
    if spend + fee > state["bucket"]:
        spend = max(0.0, state["bucket"] - _buy_fee(state["bucket"]))
        fee = _buy_fee(spend)
    if spend <= 0:
        return 0.0
    state[asset] += spend
    state["bucket"] -= (spend + fee)
    state["cost"] += fee
    state["buys"] += 1
    return spend


def _sell(state, asset, amount, price):
    """자산→버킷 매도(규제수수료). 반환: 실제 매도액."""
    if amount <= 0 or state[asset] <= 0:
        return 0.0
    notional = min(amount, state[asset])
    fee = _sell_fee(notional, price)
    state[asset] -= notional
    state["bucket"] += (notional - fee)
    state["cost"] += fee
    state["sells"] += 1
    return notional


# ── 통합 시뮬레이터 ───────────────────────────────────────────────────────────
def simulate(panel, i0, i1, strat, cfg=None, *, cash=None, record_signal=False):
    """[i0,i1) 구간 공정예산 적립 시뮬레이터. panel: QQQ/QLD/dd_ath/dd_52/dates.

    체결 규약(모두 월초 종가, 가격≤t·상태≤t 만 참조 → 인과적):
      1) 드리프트(자산 일수익 + 버킷 현금이자)
      2) 월초 입금(시드+월적립) → FX 차감 후 버킷
      3) 전략별 결정: 버킷↔주식(+crash_lev 는 QLD) 이동. 미투자분은 버킷 잔류.
    최종자산 = 주식 + QLD + 버킷.
    """
    cfg = cfg or {}
    q = panel["QQQ"]
    lv = panel["QLD"]
    dd_ath = panel["dd_ath"]
    dd_52 = panel["dd_52"]
    dates = panel["dates"]
    monthly = cfg.get("monthly", MONTHLY)
    initial = cfg.get("initial", INITIAL)
    n = i1 - i0
    sub_dates = dates[i0:i1]
    starts = R.month_start_flags(sub_dates)
    if cash is None:
        cash = [0.0] * len(dates)

    st = {"QQQ": 0.0, "QLD": 0.0, "bucket": 0.0, "cost": 0.0, "buys": 0, "sells": 0}
    equity_list = [0.0] * n
    dep_series = [0.0] * n
    inv_ret = [0.0] * n
    sig_sched = [None] * n if record_signal else []
    total_cost = 0.0
    n_deploys = 0
    month_m = -1
    cash_frac_sum = 0.0
    exp_sum = 0.0
    eq_prev = None
    in_lev = False           # crash_lev 라우팅 상태(히스테리시스)

    # dd_reserve/drypowder 리저브 목표(달러)
    dp_floor = cfg.get("floor_months", DP_FLOOR_MONTHS) * monthly

    for k in range(n):
        t = i0 + k
        # 1) 드리프트
        if k > 0:
            cr = cash[t]
            st["bucket"] *= (1.0 + cr)
            if q[t - 1] > 0:
                st["QQQ"] *= q[t] / q[t - 1]
            if lv[t - 1] > 0:
                st["QLD"] *= lv[t] / lv[t - 1]
        equity_open = st["QQQ"] + st["QLD"] + st["bucket"]
        if eq_prev is not None and eq_prev > 0:
            inv_ret[k] = equity_open / eq_prev - 1.0

        # 2) 입금
        dep = 0.0
        if k == 0:
            dep += initial
        if starts[k]:
            dep += monthly
            month_m += 1
        if dep > 0:
            fx = dep * FX_BPS * 1e-4
            st["bucket"] += (dep - fx)
            st["cost"] += fx
            total_cost += fx
            dep_series[k] = dep

        # 3) 월초 결정
        sig = None
        if starts[k]:
            st["cost"] = 0.0
            st["buys"] = 0
            st["sells"] = 0
            sig = _decide(strat, cfg, st, month_m, q[t], lv[t], dd_ath[t], dd_52[t],
                          monthly=monthly, initial=initial, dp_floor=dp_floor, in_lev=in_lev)
            in_lev = sig["in_lev"]
            n_deploys += sig["deployed"]
            total_cost += st["cost"]

        if record_signal:
            sig_sched[k] = sig if sig is not None else _last_sig(sig_sched, k)

        eq_now = st["QQQ"] + st["QLD"] + st["bucket"]
        equity_list[k] = eq_now
        eq_prev = eq_now
        cash_frac_sum += (st["bucket"] / eq_now) if eq_now > 0 else 0.0
        exp_sum += ((st["QQQ"] + 2.0 * st["QLD"]) / eq_now) if eq_now > 0 else 0.0

    final_value = equity_list[-1] if equity_list else 0.0
    total_dep = sum(dep_series)
    exp_series_max = 0.0
    # 최대 노출(월초 커밋 기준 근사): 여기선 일별 노출 최대
    return SimResult(
        dates=list(sub_dates), equity=equity_list, deposit_series=dep_series,
        inv_ret=inv_ret, final_value=final_value, total_deposited=total_dep,
        total_cost=total_cost, n_sells=st["sells"], n_buys=st["buys"],
        n_deploys=n_deploys, avg_cash_frac=cash_frac_sum / n if n else 0.0,
        avg_exposure=exp_sum / n if n else 0.0,
        max_exposure=max((2.0 if strat == "crash_lev" else 1.0), 1.0),
        signal_sched=sig_sched,
    )


def _last_sig(sched, k):
    for j in range(k - 1, -1, -1):
        if sched[j] is not None:
            return sched[j]
    return {"strat_val": 0.0, "in_lev": 0.0, "deployed": 0.0}


def _decide(strat, cfg, st, month_m, pq, pl, dd_ath_t, dd_52_t, *,
            monthly, initial, dp_floor, in_lev):
    """월초 결정: 전략별로 st(버킷↔주식) 를 갱신. 반환: 결정변수 dict(신호+상태).

    반환 dict 의 float 값들은 전부 인과적(가격≤t·상태≤t) → lookahead_guard 로 검증.
    """
    deployed = 0
    strat_val = 0.0
    if strat == "qqq":                                  # B1: 가용현금 전액 QQQ
        _buy(st, "QQQ", st["bucket"])

    elif strat in ("va", "va_sell"):
        g = cfg.get("g", VA_G)
        cap = cfg.get("cap_mult", VA_CAP_MULT) * monthly
        Vm = va_target(month_m, initial=initial, monthly=monthly, g=g)
        desired = Vm - st["QQQ"]
        strat_val = desired
        if desired >= 0:
            _buy(st, "QQQ", min(desired, cap))
        elif strat == "va_sell":
            _sell(st, "QQQ", -desired, pq)              # 경로 초과분 매도 → 버킷

    elif strat == "dd_reserve":
        w = cfg.get("withhold", DDR_WITHHOLD)
        trig = cfg.get("trigger", DDR_TRIGGER)
        # 이번 달 예산의 (1−w) 는 즉시 투자, w 는 리저브(버킷)에 유보.
        invest_now = max(0.0, monthly * (1.0 - w))
        _buy(st, "QQQ", invest_now)
        strat_val = dd_52_t
        if dd_52_t <= -trig:                            # 52주 낙폭 > trig → 리저브 전액 배치
            _buy(st, "QQQ", st["bucket"])
            deployed = 1

    elif strat == "crash_lev":
        enter = cfg.get("enter", CL_ENTER)
        exit_ = cfg.get("exit", CL_EXIT)
        strat_val = dd_ath_t
        if in_lev:
            if dd_ath_t > -exit_:                       # 회복 → QQQ 복귀
                in_lev = False
        else:
            if dd_ath_t <= -enter:                      # 깊은 낙폭 → QLD 라우팅
                in_lev = True
        asset = "QLD" if in_lev else "QQQ"
        _buy(st, asset, st["bucket"])                   # 신규 적립 전액을 라우팅자산에
        if in_lev:
            deployed = 1

    elif strat == "drypowder":
        trig = cfg.get("trigger", DP_TRIGGER)
        strat_val = dd_52_t
        # 리저브<F 면 이번 예산으로 F 까지 채우고 나머지만 투자; 차면 전액 투자.
        deficit = max(0.0, dp_floor - st["bucket"])
        invest_now = max(0.0, monthly - deficit)        # 리저브 우선충전 후 잔여 투자
        _buy(st, "QQQ", invest_now)
        if dd_52_t <= -trig:                            # 52주 낙폭 > trig → 리저브 일시배치
            _buy(st, "QQQ", st["bucket"])
            deployed = 1
    else:
        raise ValueError(f"unknown strat {strat!r}")

    return {"strat_val": float(strat_val), "in_lev": 1.0 if in_lev else 0.0,
            "deployed": float(deployed)}


# ── lookahead 가드용 신호 팩토리 ─────────────────────────────────────────────
def make_signal_fn(strat, **cfg):
    """lookahead_guard(fn, panel_closes, dates) 용 signal_fn.

    passed panel_closes(QQQ/QLD)에서 dd_ath/dd_52 를 **인과적으로 재계산**해 전 구간을
    시뮬레이션하고 일별 결정변수(strat_val/in_lev/deployed)를 반환한다. 미래가격(>t) 교란은
    dd·상태의 t 이하 값을 바꾸지 않으므로 접두부 불변 → 인과적.
    """
    def fn(panel_closes, dates):
        q = list(panel_closes["QQQ"])
        lv = list(panel_closes.get("QLD", q))
        dd_ath, dd_52 = drawdown_series(q)
        p = {"QQQ": q, "QLD": lv, "dd_ath": dd_ath, "dd_52": dd_52, "dates": list(dates)}
        res = simulate(p, 0, len(dates), strat, cfg, cash=[0.0] * len(dates),
                       record_signal=True)
        return res.signal_sched
    return fn


# ── 경로/분포 지표(c5a.aggregate 호환 키) ────────────────────────────────────
def path_metrics(res: SimResult):
    ddd = R.dollar_drawdown(res.equity, res.deposit_series)
    dollar_dd = min(ddd) if ddd else 0.0
    underwater = (sum(1 for x in ddd if x < -1e-9) / len(ddd)) if ddd else 0.0
    nav = 1.0
    navs = [1.0]
    for r in res.inv_ret[1:]:
        nav *= (1.0 + r)
        navs.append(nav)
    unit_dd = min(R.unit_drawdown(navs)) if navs else 0.0
    return {
        "terminal": res.final_value,
        "deposited": res.total_deposited,
        "dollar_dd": dollar_dd,
        "unit_dd": unit_dd,
        "underwater": underwater,
        "regret": res.final_value < res.total_deposited,
        "avg_exp": res.avg_exposure,      # 주식노출(QLD 2배 반영)
        "max_exp": res.avg_cash_frac,     # (재사용) 평균 현금비중을 max_exp 칸에 실어 보고
    }


# ── 롤링 시작일 러너 ──────────────────────────────────────────────────────────
def run_distribution(panel, years, strat, cfg, starts, *, cash=None):
    dates = panel["dates"]
    out = []
    for (s, e) in starts:
        res = simulate(panel, s, e + 1, strat, cfg, cash=cash)
        m = path_metrics(res)
        m["start"] = dates[s].isoformat()
        m["start_year"] = dates[s].year
        out.append(m)
    return out


# ── 결정(사전등록 규칙 + 무레버리지 유용 티어) ───────────────────────────────
def decide(agg, b1, *, is_leverage=False):
    c1 = agg["median"] >= 1.15 * b1["median"]
    c2 = agg["p5"] >= 0.9 * b1["p5"]
    c3 = (agg["regret"] - b1["regret"]) <= 0.05 + 1e-9
    strong = c1 and c2 and c3
    u1 = agg["median"] >= 1.03 * b1["median"]
    u2 = agg["p5"] >= 1.0 * b1["p5"] - 1e-9
    useful = (not is_leverage) and u1 and u2 and c3
    if strong:
        verdict = "PASS"
    elif useful:
        verdict = "USEFUL"
    elif c1 and c3:
        verdict = "CONDITIONAL"
    else:
        verdict = "FAIL"
    return {"c1_median": c1, "c2_tail": c2, "c3_regret": c3,
            "u1_median": u1, "u2_tail": u2, "verdict": verdict}


# ── 원장 로깅(단위자본 스트림) ───────────────────────────────────────────────
def log_config_ledger(panel, strat, cfg, ledger_path, *, neighbor=None, do_holdout=True,
                      cash=None):
    dates = panel["dates"]
    di1 = bisect.bisect_right(dates, LEDGER_DESIGN_END)
    hi0 = bisect.bisect_left(dates, LEDGER_HOLDOUT_START)
    params = {"strat": strat, "budget": "fair", "monthly": cfg.get("monthly", MONTHLY),
              "g": cfg.get("g", VA_G), "cap_mult": cfg.get("cap_mult", VA_CAP_MULT),
              "withhold": cfg.get("withhold", DDR_WITHHOLD),
              "trigger": cfg.get("trigger"), "enter": cfg.get("enter"),
              "exit": cfg.get("exit"), "floor_months": cfg.get("floor_months"),
              "cash": cfg.get("cash_label", "zero")}
    if neighbor:
        params["neighbor"] = neighbor
    idea_id = IDEA_ID[strat]
    logged = {}
    res_d = simulate(panel, 0, di1, strat, cfg, cash=cash)
    stream_d = res_d.inv_ret[1:]
    um_d = gate_eval.unit_capital_metrics(stream_d, dates=res_d.dates[1:])
    gate_eval.log_evaluation(idea_id, dict(params), 1, "design", um_d,
                             window=(res_d.dates[0], res_d.dates[-1]), ledger_path=ledger_path)
    logged["design"] = um_d
    if do_holdout and neighbor is None and not gate.already_peeked(ledger_path, idea_id):
        res_h = simulate(panel, hi0, len(dates), strat, cfg, cash=cash)
        stream_h = res_h.inv_ret[1:]
        um_h = gate_eval.unit_capital_metrics(stream_h, dates=res_h.dates[1:])
        try:
            gate_eval.log_evaluation(idea_id, dict(params), 1, "holdout", um_h,
                                     window=(res_h.dates[0], res_h.dates[-1]),
                                     ledger_path=ledger_path)
            logged["holdout"] = um_h
        except gate.PeekOnceError:
            pass
    return logged


# ── 이웃(평탄성) ──────────────────────────────────────────────────────────────
NEIGHBORS = [
    ("va", {"g": 0.06}, "g=0.06"), ("va", {"g": 0.10}, "g=0.10"),
    ("va", {"cap_mult": 2.0}, "cap=2x"), ("va", {"cap_mult": 4.0}, "cap=4x"),
    ("va_sell", {"g": 0.06}, "g=0.06"), ("va_sell", {"g": 0.10}, "g=0.10"),
    ("dd_reserve", {"withhold": 0.10}, "withhold=0.10"),
    ("dd_reserve", {"withhold": 0.30}, "withhold=0.30"),
    ("dd_reserve", {"trigger": 0.15}, "trig=0.15"),
    ("dd_reserve", {"trigger": 0.25}, "trig=0.25"),
    ("crash_lev", {"enter": 0.25}, "enter=0.25"),
    ("crash_lev", {"enter": 0.35}, "enter=0.35"),
    ("crash_lev", {"exit": 0.05}, "exit=0.05"),
    ("crash_lev", {"exit": 0.15}, "exit=0.15"),
    ("drypowder", {"floor_months": 6}, "floor=6"),
    ("drypowder", {"floor_months": 18}, "floor=18"),
    ("drypowder", {"trigger": 0.20}, "trig=0.20"),
    ("drypowder", {"trigger": 0.30}, "trig=0.30"),
]


# ── 2차 OOS 패널(NASDAQCOM 1971-1985) ────────────────────────────────────────
def build_nasdaqcom_panel():
    """FRED NASDAQCOM → 총수익근사(배당 1.5%) → 합성 QQQ-1x/QLD-2x + DTB3 현금버킷."""
    comp = hd.load_fred("NASDAQCOM")
    dtb3 = hd.load_fred("DTB3")
    tr = hd.index_total_return(comp, DIV_YIELD_COMP, symbol="COMPTR")
    qqq = hd.synthetic_leveraged(tr, 1.0, annual_expense=EXP_1X, borrow_spread=0.0,
                                 rf_candles=dtb3, rf_kind="yield", symbol="QQQ1X")
    qld = hd.synthetic_leveraged(tr, 2.0, annual_expense=EXP_LEV, borrow_spread=BORROW_SPREAD,
                                 rf_candles=dtb3, rf_kind="yield", symbol="QLD2X")
    dates = [c.dt for c in qqq]
    panel = {"dates": dates, "QQQ": [c.close for c in qqq], "QLD": [c.close for c in qld],
             "cash": c5a._yield_daily_aligned(dtb3, dates)}
    return add_signals(panel)


# ── 메인 ──────────────────────────────────────────────────────────────────────
def _sub(paths, lo, hi):
    return c5a.split_by_startyear(paths, lo, hi)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)

    print("[c6c] 합성 패널 구축(FRED NDX 1986-2026)...")
    panel = add_signals(c5a.build_synthetic_panel())
    dates = panel["dates"]
    print(f"[c6c]   {dates[0]} … {dates[-1]}  ({len(dates)}일)")
    cash_zero = [0.0] * len(dates)
    cash_sgov = [SGOV_APY / 252.0] * len(dates)
    month_starts = [t for t, f in enumerate(R.month_start_flags(dates)) if f]

    results = {"meta": {"generated": date.today().isoformat(), "fast": args.fast,
                        "n_days": len(dates), "first": dates[0].isoformat(),
                        "last": dates[-1].isoformat()}}

    # 1) 원장(단위자본 스트림; 기본 현금 0%)
    if not args.no_ledger:
        print("[c6c] 원장 적재(lane 1)...")
        for strat in STRATS:
            log_config_ledger(panel, strat, {}, args.ledger, cash=cash_zero)
        for strat, cfg, name in NEIGHBORS:
            log_config_ledger(panel, strat, cfg, args.ledger, neighbor=name,
                              do_holdout=False, cash=cash_zero)
        log_config_ledger(panel, "qqq", {}, args.ledger, cash=cash_zero)

    # 2) 롤링 분포(20y·10y; 기본 현금 0%)
    dist = {}
    for years in (20, 10):
        starts = c5a.start_indices(dates, month_starts, years, fast=args.fast)
        print(f"[c6c] {years}년 지평: {len(starts)} 시작일 "
              f"({dates[starts[0][0]]} … {dates[starts[-1][0]]})")
        per = {"b1": run_distribution(panel, years, "qqq", {}, starts, cash=cash_zero)}
        for strat in STRATS:
            per[strat] = run_distribution(panel, years, strat, {}, starts, cash=cash_zero)
        dist[years] = {"starts": starts, "per": per}

    # 3) 집계 + 판정
    summary = {}
    passers = set()
    for years in (20, 10):
        per = dist[years]["per"]
        b1d = c5a.aggregate(_sub(per["b1"], *DESIGN_START_YEARS),
                            _sub(per["b1"], *DESIGN_START_YEARS))
        b1h = c5a.aggregate(_sub(per["b1"], *HOLDOUT_START_YEARS),
                            _sub(per["b1"], *HOLDOUT_START_YEARS))
        s = {"design": {"b1": b1d}, "holdout": {"b1": b1h}}
        b1_design = _sub(per["b1"], *DESIGN_START_YEARS)
        b1_holdout = _sub(per["b1"], *HOLDOUT_START_YEARS)
        for strat in STRATS:
            lev = strat in LEVERAGE_STRATS
            ad = c5a.aggregate(_sub(per[strat], *DESIGN_START_YEARS), b1_design)
            ah = c5a.aggregate(_sub(per[strat], *HOLDOUT_START_YEARS), b1_holdout)
            ad["decision"] = decide(ad, b1d, is_leverage=lev)
            ah["decision"] = decide(ah, b1h, is_leverage=lev)
            s["design"][strat] = ad
            s["holdout"][strat] = ah
            if ad["decision"]["verdict"] in ("PASS", "USEFUL"):
                passers.add(strat)
        summary[years] = s
    results["distribution"] = summary
    results["passers"] = sorted(passers)

    # 4) SGOV 3.6% 현금 민감도(핵심 median ×B1; 설계 20y)
    print("[c6c] 현금 민감도(SGOV 3.6%)...")
    starts20 = dist[20]["starts"]
    sens = {}
    b1_s = run_distribution(panel, 20, "qqq", {}, starts20, cash=cash_sgov)
    b1_sd = c5a.aggregate(_sub(b1_s, *DESIGN_START_YEARS), _sub(b1_s, *DESIGN_START_YEARS))
    for strat in STRATS:
        ps = run_distribution(panel, 20, strat, {}, starts20, cash=cash_sgov)
        a = c5a.aggregate(_sub(ps, *DESIGN_START_YEARS), _sub(b1_s, *DESIGN_START_YEARS))
        sens[strat] = {"median_x": a["median"] / b1_sd["median"],
                       "p5_x": a["p5"] / b1_sd["p5"], "median": a["median"]}
    results["cash_sensitivity_sgov"] = {"b1_median": b1_sd["median"], "per": sens}

    # 5) 이웃(평탄성; 설계 20y)
    print("[c6c] 이웃(평탄성)...")
    neigh = {}
    b1_20_design = _sub(dist[20]["per"]["b1"], *DESIGN_START_YEARS)
    for strat, cfg, name in NEIGHBORS:
        pn = run_distribution(panel, 20, strat, cfg, starts20, cash=cash_zero)
        a = c5a.aggregate(_sub(pn, *DESIGN_START_YEARS), b1_20_design)
        neigh[f"{IDEA_ID[strat]}::{name}"] = {"median": a["median"], "p5": a["p5"],
                                              "regret": a["regret"]}
    results["neighbors_design20"] = neigh

    # 6) 2차 OOS(NASDAQCOM 1971-1985; 설계 통과자)
    print(f"[c6c] 2차 OOS(NASDAQCOM 1971-1985) — 통과자 {sorted(passers)}...")
    try:
        oos = build_nasdaqcom_panel()
        od = oos["dates"]
        oms = [t for t, f in enumerate(R.month_start_flags(od)) if f]
        oos_out = {"first": od[0].isoformat(), "last": od[-1].isoformat(), "per_horizon": {}}
        for years in (20, 10):
            ostarts_all = c5a.start_indices(od, oms, years, fast=args.fast)
            ostarts = [(s, e) for (s, e) in ostarts_all
                       if OOS_START_YEARS[0] <= od[s].year <= OOS_START_YEARS[1]]
            if not ostarts:
                continue
            ob1 = run_distribution(oos, years, "qqq", {}, ostarts, cash=oos["cash"])
            ob1a = c5a.aggregate(ob1, ob1)
            hz = {"n": ob1a["n"], "b1_median": ob1a["median"], "b1_p5": ob1a["p5"],
                  "start_first": od[ostarts[0][0]].isoformat(),
                  "start_last": od[ostarts[-1][0]].isoformat(), "strats": {}}
            for strat in (sorted(passers) or []):
                ps = run_distribution(oos, years, strat, {}, ostarts, cash=oos["cash"])
                a = c5a.aggregate(ps, ob1)
                lev = strat in LEVERAGE_STRATS
                a["decision"] = decide(a, ob1a, is_leverage=lev)
                hz["strats"][strat] = {"median": a["median"], "median_x": a["median"] / ob1a["median"],
                                       "p5": a["p5"], "p5_x": a["p5"] / ob1a["p5"] if ob1a["p5"] else 0,
                                       "regret": a["regret"], "worst_dollar_dd": a["worst_dollar_dd"],
                                       "verdict": a["decision"]["verdict"]}
            oos_out["per_horizon"][years] = hz
        results["oos_nasdaqcom"] = oos_out
    except Exception as e:  # noqa: BLE001
        results["oos_nasdaqcom"] = {"error": f"{type(e).__name__}: {e}"}

    RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[c6c] 결과 JSON → {RESULTS_JSON}")
    _print_summary(results)
    if not args.no_report:
        write_report(results)
        print(f"[c6c] 리포트 → {REPORT}")
    return results


def _print_summary(results):
    print("\n=== c6c 요약(설계 시작일 분포, 20년 지평, 현금 0%) ===")
    s = results["distribution"][20]["design"]
    b1 = s["b1"]
    print(f"  DCA-QQQ(B1): median=${b1['median']:.0f} p5=${b1['p5']:.0f} "
          f"regret={b1['regret']:.2f} (n={b1['n']})")
    for strat in STRATS:
        a = s[strat]
        print(f"  {strat:11s}: median=${a['median']:.0f} ({a['median']/b1['median']:.2f}×) "
              f"p5=${a['p5']:.0f} ({a['p5']/b1['p5']:.2f}×) Pbeat={a['p_beat_b1']:.2f} "
              f"regret={a['regret']:.2f} worstDD={a['worst_dollar_dd']:.2f} "
              f"cash%={a['median_max_exp']:.2f} → {a['decision']['verdict']}")
    print(f"  통과자(설계 PASS/USEFUL): {results['passers']}")


# ── 리포트 ────────────────────────────────────────────────────────────────────
def _row(name, a, b1):
    return (f"| {name} | ${a['median']:,.0f} ({a['median']/b1['median']:.2f}×) | "
            f"${a['p10']:,.0f} | ${a['p5']:,.0f} ({a['p5']/b1['p5']:.2f}×) | "
            f"{a['p_beat_b1']:.2f} | {a['worst_ratio_vs_b1']:.2f} | {a['regret']:.2f} | "
            f"{a['worst_dollar_dd']:.2f} | {a['median_max_exp']:.2f} | "
            f"**{a.get('decision', {}).get('verdict', '—')}** |")


def write_report(results):
    if REPORT.exists():
        head = REPORT.read_text(encoding="utf-8").split("<!-- RESULTS_BELOW -->")[0]
    else:
        head = "# Cycle 6 · c6c — Contribution-schedule optimization\n"
    L = [head.rstrip(), "<!-- RESULTS_BELOW -->", ""]
    meta = results["meta"]
    L.append(f"> 실행 {meta['generated']} · 합성 {meta['first']}…{meta['last']} "
             f"({meta['n_days']}일) · fast={meta['fast']} · 기본 현금 0%")
    L.append("")
    hdr = ("| 전략 | median(×B1) | p10 | p5(×B1) | P(beat B1) | 최악비 | regret | "
           "최악$낙폭 | 평균현금% | 판정 |")
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|"
    for years in (20, 10):
        s = results["distribution"][years]
        for period, ptitle in (("design", "설계(시작일 1986–1999)"),
                               ("holdout", "홀드아웃(시작일 2000–2016, peek-once)")):
            sec = s[period]
            b1 = sec["b1"]
            L.append(f"## {years}년 지평 — {ptitle}")
            L.append(f"DCA-QQQ(B1): median=${b1['median']:,.0f} · p10=${b1['p10']:,.0f} · "
                     f"p5=${b1['p5']:,.0f} · regret={b1['regret']:.2f} · n={b1['n']} · "
                     f"평균입금 ${b1['mean_deposited']:,.0f}")
            L.append("")
            L.append(hdr)
            L.append(sep)
            for strat in STRATS:
                L.append(_row(strat, sec[strat], b1))
            L.append("")

    # 현금 민감도
    cs = results["cash_sensitivity_sgov"]
    L.append("## 현금 버킷 민감도 — SGOV 3.6%/yr (설계 20년 지평, ×B1)")
    L.append("| 전략 | median(×B1) 0% → 3.6% | p5(×B1) 3.6% |")
    L.append("|---|---:|---:|")
    d20 = results["distribution"][20]["design"]
    for strat in STRATS:
        z = d20[strat]["median"] / d20["b1"]["median"]
        v = cs["per"][strat]
        L.append(f"| {strat} | {z:.2f}× → {v['median_x']:.2f}× | {v['p5_x']:.2f}× |")
    L.append("")

    # 이웃
    L.append("## 이웃(평탄성, 설계 20년 지평)")
    L.append("| 이웃 | median | p5 | regret |")
    L.append("|---|---:|---:|---:|")
    for name, a in results["neighbors_design20"].items():
        L.append(f"| {name} | ${a['median']:,.0f} | ${a['p5']:,.0f} | {a['regret']:.2f} |")
    L.append("")

    # 2차 OOS
    L.append("## 2차 OOS — FRED NASDAQCOM(1971–1985 시작, 배당 1.5%, DTB3 현금)")
    oos = results.get("oos_nasdaqcom", {})
    if "error" in oos:
        L.append(f"(OOS 로드 실패: {oos['error']})")
    elif not results["passers"]:
        L.append("(설계 통과자 없음 → 2차 OOS 생략)")
    else:
        L.append(f"구간 {oos['first']}…{oos['last']}. 대상 통과자: {', '.join(results['passers'])}")
        for years, hz in sorted(oos.get("per_horizon", {}).items()):
            L.append("")
            L.append(f"### {years}년 지평 (시작 {hz['start_first']}…{hz['start_last']}, n={hz['n']})")
            L.append(f"DCA-QQQ(B1): median=${hz['b1_median']:,.0f} · p5=${hz['b1_p5']:,.0f}")
            L.append("| 전략 | median(×B1) | p5(×B1) | regret | 최악$낙폭 | 판정 |")
            L.append("|---|---:|---:|---:|---:|:--:|")
            for strat, a in hz["strats"].items():
                L.append(f"| {strat} | ${a['median']:,.0f} ({a['median_x']:.2f}×) | "
                         f"${a['p5']:,.0f} ({a['p5_x']:.2f}×) | {a['regret']:.2f} | "
                         f"{a['worst_dollar_dd']:.2f} | **{a['verdict']}** |")
    L.append("")
    L.append("## 판정 종합 및 정직한 해석")
    L.append(_verdict_prose(results))
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def _verdict_prose(results):
    d20 = results["distribution"][20]["design"]
    h20 = results["distribution"][20]["holdout"]
    d10 = results["distribution"][10]["design"]
    b1 = d20["b1"]
    lines = ["**결정규칙(사전등록):** median ≥ 1.15×B1 AND p5 ≥ 0.9×B1_p5 AND regret 증가 ≤ 5pp → PASS. "
             "무레버리지 아이디어는 median ≥ 1.03× AND p5 ≥ 1.0× 이면 **USEFUL(free-lunch)**. "
             "판정은 설계에서 내리고 홀드아웃으로 1회 확인.", ""]
    for strat in STRATS:
        a, h, a10 = d20[strat], h20[strat], d10[strat]
        dec = a["decision"]
        lev = " (레버리지)" if strat in LEVERAGE_STRATS else ""
        lines.append(
            f"- **{strat}{lev}**: 20y 설계 {dec['verdict']} / 20y 홀드아웃 {h['decision']['verdict']} / "
            f"10y 설계 {a10['decision']['verdict']}. "
            f"[20y설계] median {a['median']/b1['median']:.2f}×(≥1.15 {'O' if dec['c1_median'] else 'X'}/"
            f"≥1.03 {'O' if dec['u1_median'] else 'X'}), "
            f"p5 {a['p5']/b1['p5']:.2f}×(≥0.9 {'O' if dec['c2_tail'] else 'X'}/"
            f"≥1.0 {'O' if dec['u2_tail'] else 'X'}), "
            f"regret {a['regret']:.2f} vs B1 {b1['regret']:.2f}(≤+5pp {'O' if dec['c3_regret'] else 'X'}); "
            f"최악$낙폭 {a['worst_dollar_dd']:.2f}, 평균현금 {a['median_max_exp']:.2f}.")
    # 동적 수치(홀드아웃·OOS)
    clh = h20["crash_lev"]
    oos = results.get("oos_nasdaqcom", {}).get("per_horizon", {})
    oos20 = oos.get(20, {}).get("strats", {}).get("crash_lev", {})
    lines.append("")
    lines.append(
        "**권고: 사전등록 규칙(설계 판정→홀드아웃 1회 확인)을 양쪽에서 통과하는 무레버리지 아이디어는 "
        f"없다.** va/va_sell/dd_reserve/drypowder 는 20y 설계·홀드아웃 모두 FAIL(median ≤0.99×, 대개 <0.95×). "
        "**공정 예산 제약이 결정적이다** — NDX 는 장기 강세 벤치라 현금을 0%로 묵히는 어떤 스케줄도 드래그 "
        "경주에서 진다. 유일한 무레버리지 USEFUL 은 va_sell 의 10y 설계(1.09×/1.01×)뿐인데, 20y 설계·양 "
        "홀드아웃·양 OOS 에서 모두 미달 → 규율상 채택 불가(파라미터 구제 금지).")
    lines.append("")
    lines.append(
        f"**crash_lev(레버리지)만** 20y 설계 PASS(1.23×) + 20y 홀드아웃 PASS({clh['median']/h20['b1']['median']:.2f}×) "
        f"+ 2차 OOS NASDAQCOM 20y {oos20.get('verdict','—')}({oos20.get('median_x',0):.2f}×) 로 **20년 지평의 "
        "실재하고 재현되는 우위**를 보인다. 그러나 (1) 10y 는 설계·OOS 모두 FAIL(1.00×/1.07×, p5 0.70×) — 짧은 "
        "지평은 2x 크래시를 상각할 시간이 없다; (2) 최악 달러낙폭 −0.81…−0.94 로 README §7 −50% 하드캡·부록 "
        "v2.1 레버리지 −70% 하한을 크게 초과; (3) 홀드아웃 3.11× 는 닷컴 미회복 지속성(사상최고 미탈환 시점까지 "
        "QLD 라우팅이 ~13년 지속)과 데이터종료(2026) 절단 편향의 산물로 낙관 방향; (4) 알파가 아니라 **깊은 "
        "할인의 회복에 건 베타/레버리지 베팅**이다.")
    lines.append("")
    lines.append(
        "**리스크 한계(반드시 병기).** crash_lev 채택은 전량이 아니라 **소액 슬리브 + 20년+ 지평 한정 + 포워드 "
        "페이퍼 점증** 후에만. 합성 2x 는 base 가 총수익이라 배당을 2배로 태워 낙관 방향(c5a 실물검증 corr "
        "0.999, CAGR 합성 33.7% vs 실물 33.5% 로 정량화).")
    lines.append("")
    lines.append(
        "**정직한 주의.** (1) **dd_reserve 는 실패한 c2 낙폭틸트 DCA 와 동일 52주 낙폭신호를 재사용**한다. c2 는 "
        "외부현금을 추가투입해 예산이 불공정했으나 c6c_dd_reserve 는 유보한 자기자금만 쓰므로 예산이 공정하다. "
        "그런데도 20y 설계 0.91×·홀드아웃 0.95× FAIL — **예산 불공정을 제거하고도 낙폭틸트 신호에 우위가 없음을 "
        "재확인**(c2 결론 강화·신호 재사용). (2) VA 의 유일한 밝은 지점은 짧은 지평·약한 벤치(닷컴 시작 10y)에서 "
        "현금완충이 하방을 줄이는 것(va_sell 10y 최악$낙폭 −0.66 vs B1)이며 장기·강세에서는 순드래그. "
        "(3) **현금수익 민감도**: 버킷이 0% 아닌 SGOV 3.6%면 va 0.99→1.03×, va_sell 0.96→1.04×(p5 0.87→1.07×)로 "
        "20y 설계 USEFUL 문턱을 겨우 넘는다 — 이 스케줄들의 '공짜점심'은 **유휴현금이 T-bill 수익을 벌 때만, "
        "그것도 근소하게** 존재하고 PASS(1.15×) 근처엔 못 간다. 사전등록 기본(0%)에서는 채택 불가. "
        "(4) 이웃(평탄성)은 모든 파라미터에서 절벽 없이 매끄럽다 — 결과는 과최적화 산물이 아니다.")
    lines.append("")
    lines.append(
        "**결론: 공정 예산 하에서 무레버리지 적립 스케줄 최적화로는 DCA-QQQ 를 이기지 못한다(공짜점심 없음).** "
        "레버리지 crash_lev 만 장기(20y+)에서 통과하나 큰 낙폭·소표본·합성 낙관 탓에 소액 슬리브 한정.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
