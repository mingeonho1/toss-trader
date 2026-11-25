"""c16 공용 엔진 — 레인1 월간 자산배분(GEM c16a · GTAA c16b) 백테스트/게이트 평가.

신호함수는 각 카드 모듈(c16a_gem.py / c16b_gtaa.py)에 있고, 이 모듈은 데이터·시뮬레이터·게이트 배선만 맡는다.
사전등록은 reports/cycle16_c16a_gem.md / reports/cycle16_c16b_gtaa.md 상단(결과 보기 전 작성).

- 데이터: Nasdaq 키리스 10년(Yahoo 429 → 규약대로 중단). NYSE 상장 ETF 는 Nasdaq 이 배당 이력을 안 줘서
  사전등록 프록시(SPY→VONE, AGG→BND, BIL→DTB3 합성, EFA/VNQ/SCHD→가격+상수배당)를 쓴다.
- 체결: 월말 종가 t 신호 → t+1 종가. 목표가 직전 체결 목표와 같으면 주문 없음(드리프트).
- 비용(주): fees.TossFeeSchedule(0.1%/side, ≤$10 매수 무료 분할, 매도 SEC/TAF 최소) + 반호가 1bp + 슬리피지 5bp.
  스트레스: 2×(무료구간 없음), 25bp/side(스트레스 전용), 손익분기(평탄 bps).
- 원장: gate_eval.log_evaluation / run_splits 로만 append(reports/trials_ledger.jsonl), 홀드아웃 peek-once.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
from toss_trader.fees import TossFeeSchedule  # noqa: E402
import gate_eval as GE  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
EVAL_START = date(2018, 6, 1)
DESIGN_END = date(2021, 12, 31)
LANE = 1
NEIGHBOR_GRID = (0.5, 0.7, 0.8, 1.2, 1.5)
UNIT_USD = 1000.0
DCA_INITIAL = 32.0
DCA_MONTHLY = 35.0
FX_BPS = 20.0
BIL_ER = 0.001356              # BIL 보수(연)
BPS = 1e-4

# 사전등록 데이터 프록시: 카드 티커 → (원천 심볼, 상수배당 연율 or None, 설명)
PROXY = {
    "SPY": ("VONE", None, "VONE(Nasdaq 상장 Russell 1000, 실배당조정)"),
    "AGG": ("BND", None, "BND(Nasdaq 상장 Total Bond, 실배당조정)"),
    "ACWX": ("ACWX", None, "실데이터(Nasdaq 배당조정)"),
    "IEF": ("IEF", None, "실데이터(Nasdaq 배당조정)"),
    "QQQ": ("QQQ", None, "실데이터(Nasdaq 배당조정)"),
    "GLD": ("GLD", None, "실데이터(무배당)"),
    "EFA": ("EFA", 0.029, "EFA 가격 + 2.9%/yr 상수배당(근사)"),
    "VNQ": ("VNQ", 0.039, "VNQ 가격 + 3.9%/yr 상수배당(근사)"),
    "SCHD": ("SCHD", 0.033, "SCHD 가격 + 3.3%/yr 상수배당(근사)"),
}
# 민감도(보고만): 상수배당 근사로 교체
SENS_PROXY = {"SPY": ("SPY", 0.015, "SPY 가격 + 1.5%/yr"), "AGG": ("AGG", 0.026, "AGG 가격 + 2.6%/yr")}


# ── 데이터 ───────────────────────────────────────────────────────────────────
def const_yield_tr(closes: list[float], y: float, trading_days: int = 252) -> list[float]:
    """가격 + 상수배당 근사 총수익(histdata.index_total_return 과 같은 식, 리스트판)."""
    out = [closes[0]]
    for i in range(1, len(closes)):
        r = closes[i] / closes[i - 1] - 1.0 if closes[i - 1] > 0 else 0.0
        out.append(out[-1] * (1.0 + r + y / trading_days))
    return out


def bil_from_dtb3(dates: list[date], dtb3: list[tuple[date, float]]) -> list[float]:
    """DTB3(연 %) 전일값으로 일할 누적 − BIL 보수 → 합성 BIL 총수익 지수(시작 100)."""
    j = 0
    rate = dtb3[0][1]
    out = [100.0]
    for t in range(1, len(dates)):
        while j < len(dtb3) and dtb3[j][0] <= dates[t - 1]:
            rate = dtb3[j][1]
            j += 1
        out.append(out[-1] * (1.0 + (rate / 100.0 - BIL_ER) / 252.0))
    return out


def load_panel(names: list[str], *, sens: bool = False) -> tuple[list[date], dict[str, list[float]], dict]:
    """카드 티커 목록 → (공통 거래일, {티커: 총수익 종가}, 출처표). BIL 은 항상 DTB3 합성."""
    proxy = dict(PROXY)
    if sens:
        proxy.update(SENS_PROXY)
    src_syms = sorted({proxy[n][0] for n in names if n != "BIL"})
    raw = {s: {c.dt: c.close for c in hd.load_symbol(s)} for s in src_syms}
    common = sorted(set.intersection(*(set(v) for v in raw.values())))
    panel: dict[str, list[float]] = {}
    info: dict[str, str] = {}
    for n in names:
        if n == "BIL":
            continue
        sym, y, desc = proxy[n]
        closes = [raw[sym][d] for d in common]
        panel[n] = const_yield_tr(closes, y) if y else closes
        info[n] = desc
    if "BIL" in names:
        dtb3 = [(c.dt, c.close) for c in hd.load_fred("DTB3") if c.close == c.close]
        panel["BIL"] = bil_from_dtb3(common, dtb3)
        info["BIL"] = "FRED DTB3 일할 누적 − 13.56bp/yr(합성)"
    return common, panel, info


def carry_month_end(dates, decide_at):
    """월말(마지막 거래일)에만 decide_at(t) 로 목표를 갱신하고 그 외 날은 직전 목표를 유지(인과적)."""
    me = R.month_end_flags(dates)
    out: list[dict] = []
    cur: dict = {}
    for t in range(len(dates)):
        if me[t]:
            nxt = decide_at(t)
            if nxt is not None:
                cur = nxt
        out.append(dict(cur))
    return out


# ── 비용 ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CostCfg:
    """kind='toss': TossFeeSchedule 실수수료 + extra_bps(반호가+슬리피지). kind='flat': 편도 flat_bps 만."""
    kind: str = "toss"
    sched: TossFeeSchedule = field(default_factory=TossFeeSchedule)
    extra_bps: float = 6.0
    flat_bps: float = 0.0
    label: str = "toss(0.1%,≤$10무료)+hs1+slip5"

    def fee(self, side: str, notional: float, price: float) -> float:
        if notional <= 0:
            return 0.0
        if self.kind == "flat":
            return notional * self.flat_bps * BPS
        if side == "BUY":
            f = self.sched.plan_split("BUY", notional).total_fee
        else:
            f = self.sched.order_fee("SELL", notional, shares=notional / price if price > 0 else 0.0)
        return f + notional * self.extra_bps * BPS


COST_BASE = CostCfg()


def cost_mult(m: float) -> CostCfg:
    """총비용 m배 스트레스(수수료율·반호가·슬리피지 m배, m>1 이면 ≤$10 무료구간 제거)."""
    if m == 1.0:
        return COST_BASE
    s = TossFeeSchedule(commission_rate=0.001 * m, free_threshold_usd=0.0 if m > 1 else 10.0)
    return CostCfg(sched=s, extra_bps=6.0 * m, label=f"stress {m:g}x")


# 25bp/side 는 구 API 예시값 — 스트레스 전용(주 비용 아님).
COST_STRESS_25BP = CostCfg(sched=TossFeeSchedule(commission_rate=0.0025, free_threshold_usd=0.0),
                           extra_bps=6.0, label="stress 25bp/side(구 API 예시값)")


def flat_cost(roundtrip_frac: float) -> CostCfg:
    return CostCfg(kind="flat", flat_bps=roundtrip_frac * 1e4 / 2.0, label="flat")


# ── 시뮬레이터 ───────────────────────────────────────────────────────────────
@dataclass
class Sim:
    dates: list[date]
    equity: list[float]
    n_switch: int
    switch_dates: list[date]
    total_cost: float
    deposits: list[tuple[date, float]]
    applied: list[dict]

    def returns(self) -> list[float]:
        e = self.equity
        return [e[i] / e[i - 1] - 1.0 for i in range(1, len(e))]

    def cashflows(self) -> list[tuple[date, float]]:
        return [(d, -a) for d, a in self.deposits] + [(self.dates[-1], self.equity[-1])]


def _same(a: dict, b: dict | None) -> bool:
    if b is None:
        return False
    keys = set(a) | set(b)
    return all(abs(a.get(k, 0.0) - b.get(k, 0.0)) < 1e-9 for k in keys)


def simulate(panel: dict[str, list[float]], dates: list[date], targets: list[dict], *,
             s0: int, s1: int | None = None, cost: CostCfg = COST_BASE,
             start_usd: float = UNIT_USD, monthly_usd: float = 0.0, fx_bps: float = FX_BPS,
             force_rebalance: list[bool] | None = None) -> Sim:
    """목표 targets[t](종가 t 결정)를 t+1 종가에 체결. 구간 [s0, s1].

    s0 일에는 targets[s0-1] 로 최초 매수. 이후 목표가 직전 체결 목표와 다르면(또는 force_rebalance[t]) 전체
    리밸런스(매도 먼저, 매수는 가용현금 한도). monthly_usd>0 이면 DCA: s0 에 start_usd 시드 + 매월 첫 거래일
    입금(FX fx_bps 차감) → 리밸런스일이 아니면 입금액만 목표비중대로 매수(매수전용, pro-rata).
    """
    s1 = len(dates) - 1 if s1 is None else s1
    syms = list(panel)
    val = {s: 0.0 for s in syms}
    cash = 0.0
    starts = R.month_start_flags(dates)
    applied: dict | None = None
    eq_out: list[float] = []
    dts: list[date] = []
    total_cost = 0.0
    n_sw = 0
    sw_dates: list[date] = []
    deposits: list[tuple[date, float]] = []
    applied_hist: list[dict] = []
    dca = monthly_usd > 0

    def px(s, t):
        return panel[s][t]

    def rebalance(t, tgt):
        nonlocal cash, total_cost
        E = cash + sum(val.values())
        c = 0.0
        for s in syms:                                    # 매도 먼저
            want = tgt.get(s, 0.0) * E
            if val[s] > want + 1e-12:
                n = val[s] - want
                f = cost.fee("SELL", n, px(s, t))
                val[s] = want
                cash += n - f
                c += f
        needs = {s: tgt.get(s, 0.0) * E - val[s] for s in syms if tgt.get(s, 0.0) * E - val[s] > 1e-12}
        tot = sum(needs.values())
        if tot > 0:
            scale = min(1.0, cash / tot)                  # 예산(노셔널+수수료) = 미달분 × scale
            for s, need in needs.items():
                budget = min(need * scale, cash)
                n = max(0.0, budget - cost.fee("BUY", budget, px(s, t)))   # n + fee(n) ≤ budget
                f = cost.fee("BUY", n, px(s, t))
                val[s] += n
                cash -= n + f
                c += f
        total_cost += c

    def buy_new(t, tgt, amount):
        nonlocal cash, total_cost
        w = sum(tgt.values())
        if w <= 0 or amount <= 0:
            return
        for s, ws in tgt.items():
            budget = min(amount * ws / w, cash)
            n = max(0.0, budget - cost.fee("BUY", budget, px(s, t)))
            f = cost.fee("BUY", n, px(s, t))
            val[s] += n
            cash -= n + f
            total_cost += f

    for t in range(s0, s1 + 1):
        if t > s0:
            for s in syms:
                p0 = panel[s][t - 1]
                if p0 > 0:
                    val[s] *= panel[s][t] / p0
        dep = 0.0
        if t == s0:
            dep = start_usd
        elif dca and starts[t]:
            dep = monthly_usd
        if dep > 0:
            fx = dep * fx_bps * BPS if dca else 0.0
            cash += dep - fx
            total_cost += fx
            deposits.append((dates[t], dep))
        tgt = targets[t - 1] if t - 1 >= 0 else {}
        do_reb = (not _same(tgt, applied)) or bool(force_rebalance and force_rebalance[t])
        if do_reb and (tgt or applied):
            if applied is not None and not _same(tgt, applied):
                n_sw += 1
                sw_dates.append(dates[t])
            rebalance(t, tgt)
            applied = dict(tgt)
        elif dep > 0 and applied:
            buy_new(t, applied, dep - (dep * fx_bps * BPS if dca else 0.0))
        eq_out.append(cash + sum(val.values()))
        dts.append(dates[t])
        applied_hist.append(dict(applied or {}))
    return Sim(dts, eq_out, n_sw, sw_dates, total_cost, deposits, applied_hist)


def idx_of(dates: list[date], d: date) -> int:
    """d 이상 첫 거래일 인덱스."""
    for i, x in enumerate(dates):
        if x >= d:
            return i
    return len(dates) - 1


def const_targets(dates, w: dict) -> list[dict]:
    return [dict(w) for _ in dates]


# ── 창 지표 ─────────────────────────────────────────────────────────────────
def window(sim: Sim, a: date, b: date) -> dict:
    idx = [i for i, d in enumerate(sim.dates) if a <= d <= b]
    eq = [sim.equity[i] for i in idx]
    days = max((sim.dates[idx[-1]] - sim.dates[idx[0]]).days, 1)
    rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq))]
    cg = gate.cagr(eq, days)
    mdd = gate.max_drawdown(eq)
    return {"terminal": eq[-1] / eq[0], "cagr": cg, "mdd": mdd, "vol": gate.annualized_vol(rets),
            "sharpe": gate.sharpe(rets), "ulcer": gate.ulcer_index(eq),
            "martin": gate.martin_ratio(eq, days), "calmar": gate.calmar(cg, mdd),
            "rets": rets, "eq": eq, "dates": [sim.dates[i] for i in idx],
            "first": sim.dates[idx[0]].isoformat(), "last": sim.dates[idx[-1]].isoformat()}


def vol_match_terminal(rets: list[float], target: float = 0.10) -> float:
    k = gate.vol_match_scale(rets, target_ann_vol=target)
    if not math.isfinite(k):
        return float("nan")
    eq = 1.0
    for r in rets:
        eq *= 1.0 + k * r
    return eq


def pearson(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return float("nan")
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def yearly_returns(sim: Sim) -> dict[int, float]:
    out: dict[int, float] = {}
    prev_eq = sim.equity[0]
    for i, d in enumerate(sim.dates):
        last_of_year = i == len(sim.dates) - 1 or sim.dates[i + 1].year != d.year
        if last_of_year:
            out[d.year] = sim.equity[i] / prev_eq - 1.0
            prev_eq = sim.equity[i]
    return out


# ── 페이퍼랩 상관(백필 엔진 재사용, ETF 전용 로스터) ─────────────────────────
PAPERLAB_ETF_ROSTER = ["qqq_bh", "lrs200_qqq", "ftlt_1x", "tqqq_bh", "lrr_tqqq", "lrs200_tqqq",
                       "sma200_buffer_tqqq", "voltarget_3x", "rsi2_tqqq", "nine_sig", "ftlt",
                       "holy_grail", "simple_rsi_uvxy"]


def paperlab_streams(start: date) -> dict[str, dict[date, float]]:
    """기존 페이퍼랩 전략(ETF 유니버스만)을 백필 엔진으로 재생 → {name: {date: 단위장부 일수익}}.

    hibeta/mom_top5/스윙(단일종목 60+개)은 이 워크트리 캐시에 없어 제외(리포트에 명시).
    """
    from toss_trader.paperlab import PaperLab
    from toss_trader.paperlab_strategies import ROSTER_FACTORIES
    strats = [ROSTER_FACTORIES[n]() for n in PAPERLAB_ETF_ROSTER]
    syms = sorted({s for st in strats for s in st.universe()} | {"QQQ"})
    by_date = {s: {c.dt: c for c in hd.load_symbol(s)} for s in syms}
    master = sorted(by_date["QQQ"])
    out: dict[str, dict[date, float]] = {}
    for st in strats:
        lab = PaperLab(st)
        state = lab.run(lab.fresh_state(start), master, by_date, contribute=False)
        eq = [(date.fromisoformat(r[0]), float(r[1])) for r in state["equity"]]
        out[st.name] = {eq[i][0]: eq[i][1] / eq[i - 1][1] - 1.0 for i in range(1, len(eq))
                        if eq[i - 1][1] > 0}
    return out


def correlations(sim: Sim, streams: dict[str, dict[date, float]], a: date, b: date) -> dict[str, float]:
    mine = {sim.dates[i]: sim.equity[i] / sim.equity[i - 1] - 1.0 for i in range(1, len(sim.dates))}
    out = {}
    for name, s in streams.items():
        ds = [d for d in sorted(mine) if a <= d <= b and d in s]
        out[name] = pearson([mine[d] for d in ds], [s[d] for d in ds])
    return out


# ── 아이디어 평가(레인1 게이트) ───────────────────────────────────────────────
def evaluate_idea(idea_id: str, sig_fn, base_params: dict, key: str, dates, panel, *,
                  universe: list[str], do_log: bool = True, rc_B: int = 2000) -> dict:
    """c3a 패턴: 이웃(설계) 원장 적재 → 평탄성/N_eff/DSR → run_splits(설계+홀드아웃 peek-once) → 기간별 게이트."""
    s0 = idx_of(dates, EVAL_START)
    hold_start = next(d for d in dates if d > DESIGN_END)
    end = dates[-1]
    qqq_t = const_targets(dates, {"QQQ": 1.0})

    def run(params, cost=COST_BASE, **kw):
        tg = sig_fn(panel, dates, **params)
        return simulate(panel, dates, tg, s0=s0, cost=cost, **kw), tg

    bench = simulate(panel, dates, qqq_t, s0=s0)
    res, targets = run(base_params)
    peeked = gate.already_peeked(LEDGER, idea_id)
    log_now = do_log and not peeked

    # 이웃(설계 구간만) — 원장 적재
    center_val = base_params[key]
    neigh = {}
    d_bench = window(bench, EVAL_START, DESIGN_END)
    for g in NEIGHBOR_GRID:
        p = dict(base_params)
        p[key] = int(round(center_val * g))
        r_n, _ = run(p)
        w = window(r_n, EVAL_START, DESIGN_END)
        neigh[f"{key}x{g}"] = {"params": p, "w": w, "sim": r_n,
                               "net_excess": w["terminal"] - d_bench["terminal"]}
        if log_now:
            um = GE.unit_capital_metrics(w["rets"], dates=w["dates"][1:])
            GE.log_evaluation(idea_id, {"neighbor": f"{key}x{g}", **p}, LANE, "design", um,
                              window=(w["dates"][0], w["dates"][-1]), universe=universe,
                              ledger_path=LEDGER)
    d_center = window(res, EVAL_START, DESIGN_END)
    n_sh = sorted(v["w"]["sharpe"] for v in neigh.values())
    frac_pos = sum(1 for v in neigh.values() if v["net_excess"] > 0) / len(neigh)
    med = n_sh[len(n_sh) // 2] if len(n_sh) % 2 else 0.5 * (n_sh[len(n_sh) // 2 - 1] + n_sh[len(n_sh) // 2])
    plateau_pass = frac_pos >= 0.8 and med >= 0.9 * d_center["sharpe"]
    plateau_border = (not plateau_pass) and frac_pos >= 0.6
    std = {k: v["w"]["rets"] for k, v in neigh.items()}
    std["center"] = d_center["rets"]
    n_eff_idea = gate.n_eff_clusters(std, theta=0.9)

    # 헤드라인 run_splits (원장: 설계 center + 홀드아웃 1회)
    cand = res.returns()
    bst = bench.returns()
    splits = GE.run_splits(idea_id, {**base_params, "neighbor": "center"}, LANE, cand, bst,
                           res.dates, DESIGN_END, universe=universe, program_dsr=True,
                           program_dsr_robust=True, ledger_path=LEDGER, rc_B=rc_B, log=log_now)

    # B0 DCA 기준(시작일 랜덤화용) 패널: QQQ/SCHD/GLD
    out = {"idea_id": idea_id, "params": base_params, "peeked_before_run": peeked,
           "plateau": {"frac_pos": frac_pos, "median_neighbor_sharpe": med,
                       "center_sharpe": d_center["sharpe"], "pass": plateau_pass,
                       "border": plateau_border,
                       "neighbors": {k: {"param": v["params"][key], "terminal": v["w"]["terminal"],
                                         "sharpe": v["w"]["sharpe"], "mdd": v["w"]["mdd"],
                                         "net_excess_vs_qqq": v["net_excess"]}
                                     for k, v in neigh.items()}},
           "n_eff_idea": n_eff_idea, "res": res, "bench": bench, "targets": targets,
           "neigh_sims": {k: v["sim"] for k, v in neigh.items()}, "splits": splits,
           "hold_start": hold_start, "s0": s0}

    for period, a, b in (("design", EVAL_START, DESIGN_END), ("holdout", hold_start, end)):
        wm, wb = window(res, a, b), window(bench, a, b)
        sp = splits.get(period, {})
        ai = sp.get("axis_input", {})
        # 가족 RC(후보 + 이웃 5개, 같은 구간 초과수익 vs QQQ)
        fam = {}
        for k, v in neigh.items():
            wn = window(v["sim"], a, b)
            fam[k] = [x - y for x, y in zip(wn["rets"], wb["rets"])]
        cex = [x - y for x, y in zip(wm["rets"], wb["rets"])]
        rc_fam = GE.reality_check(cex, family_excess=fam, B=rc_B)
        wr = gate.subperiod_consistency(wm["eq"], wb["eq"], wm["dates"], window_years=3, step_months=6)
        base_nx = wm["terminal"] - wb["terminal"]
        cs_mult = 0.0
        stress = {}
        if base_nx > 0:
            cs_mult = 1.0
        for m in (1.5, 2.0):
            r_s, _ = run(base_params, cost=cost_mult(m))
            nx = window(r_s, a, b)["terminal"] - wb["terminal"]
            stress[f"{m}x"] = nx
            if base_nx > 0 and nx > 0:
                cs_mult = m
        r25, _ = run(base_params, cost=COST_STRESS_25BP)
        stress["25bp_stress"] = window(r25, a, b)["terminal"] - wb["terminal"]

        def be_eval(frac):
            rr, _ = run(base_params, cost=flat_cost(frac))
            return window(rr, a, b)["terminal"] - wb["terminal"]
        be_bps = gate.breakeven_cost_bps(be_eval, 0.0, 0.02)
        sd = start_date_randomization(sig_fn, base_params, dates, panel, a, b)
        metrics = {
            "lane": LANE, "terminal_vs_b0": wm["terminal"] / wb["terminal"],
            "dsr": ai.get("dsr"), "rc_pvalue": rc_fam["rc_pvalue"], "spa_pvalue": rc_fam["spa_pvalue"],
            "mdd": wm["mdd"], "mdd_bench": wb["mdd"], "ulcer": wm["ulcer"], "ulcer_bench": wb["ulcer"],
            "plateau_pass": plateau_pass, "plateau_borderline": plateau_border,
            "subperiod_winrate": wr["win_rate"], "cost_stress_mult": cs_mult,
            "start_date_winrate": sd["win_rate"],
            "calmar_not_worse": wm["calmar"] >= wb["calmar"],
            "ulcer_not_worse": wm["martin"] >= wb["martin"],
        }
        dec = gate.decide(metrics)
        out[period] = {
            "window": [wm["first"], wm["last"]],
            "strat": {k: wm[k] for k in ("terminal", "cagr", "mdd", "vol", "sharpe", "ulcer", "martin", "calmar")},
            "qqq": {k: wb[k] for k in ("terminal", "cagr", "mdd", "vol", "sharpe", "ulcer", "martin", "calmar")},
            "vol_match_10": {"strat": vol_match_terminal(wm["rets"]), "qqq": vol_match_terminal(wb["rets"])},
            "corr_qqq_daily": pearson(wm["rets"], wb["rets"]),
            "dsr_program": ai.get("dsr"), "dsr_idea": ai.get("dsr_idea"),
            "program_dsr_detail": {k: v for k, v in (sp.get("program_dsr") or {}).items()
                                   if isinstance(v, (int, float, str))},
            "rc_single_p": ai.get("rc_pvalue"), "rc_family_p": rc_fam["rc_pvalue"],
            "spa_family_p": rc_fam["spa_pvalue"], "subperiod": {"win_rate": wr["win_rate"],
                                                                 "n_windows": wr.get("n_windows")},
            "cost_stress_net_excess": stress, "cost_stress_mult": cs_mult,
            "base_net_excess": base_nx, "breakeven_roundtrip_bps": be_bps,
            "start_date": sd, "metrics": metrics, "decision": dec.to_dict(),
        }
    return out


def start_date_randomization(sig_fn, params, dates, panel, a: date, b: date, max_off: int = 24) -> dict:
    """DCA 시작월 오프셋 0..max_off(구간 내 최소 12개월 남김) — 전략 DCA vs B0(QQQ60/SCHD25/GLD15) 최종자산."""
    tg = sig_fn(panel, dates, **params)
    b0 = const_targets(dates, {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15})
    starts = [i for i, f in enumerate(R.month_start_flags(dates)) if f and a <= dates[i] <= b]
    e = max(i for i, d in enumerate(dates) if d <= b)
    rows = []
    for off in range(0, max_off + 1):
        if off >= len(starts) or (dates[e] - dates[starts[off]]).days < 365:
            break
        s0 = starts[off]
        st = simulate(panel, dates, tg, s0=s0, s1=e, start_usd=DCA_INITIAL, monthly_usd=DCA_MONTHLY)
        bb = simulate(panel, dates, b0, s0=s0, s1=e, start_usd=DCA_INITIAL, monthly_usd=DCA_MONTHLY)
        rows.append((dates[s0].isoformat(), st.equity[-1], bb.equity[-1]))
    wins = sum(1 for _, x, y in rows if x > y)
    margins = sorted(x / y - 1.0 for _, x, y in rows)
    return {"win_rate": wins / len(rows) if rows else 0.0, "n": len(rows),
            "median_margin": margins[len(margins) // 2] if margins else float("nan"),
            "rows": rows[:3] + rows[-2:]}


def dca_compare(sig_fn, params, dates, panel, fx_bps: float = FX_BPS) -> dict:
    """전 평가창 DCA($32 + 월 $35): 전략 vs B0(QQQ60/SCHD25/GLD15) vs B1(QQQ100). XIRR·최종자산·달러MDD."""
    s0 = idx_of(dates, EVAL_START)
    out = {}
    specs = {"strategy": sig_fn(panel, dates, **params),
             "B0_QQQ60_SCHD25_GLD15": const_targets(dates, {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15}),
             "B1_QQQ100": const_targets(dates, {"QQQ": 1.0})}
    for k, tg in specs.items():
        s = simulate(panel, dates, tg, s0=s0, start_usd=DCA_INITIAL, monthly_usd=DCA_MONTHLY, fx_bps=fx_bps)
        out[k] = {"final": s.equity[-1], "deposited": sum(a for _, a in s.deposits),
                  "xirr": gate.xirr(s.cashflows()), "dollar_mdd": gate.max_drawdown(s.equity),
                  "total_cost": s.total_cost, "switches": s.n_switch}
    return out


def core_satellite(res: Sim, bench: Sim, w_sat: float = 0.20) -> dict:
    """QQQ (1−w) + 전략 w, 월초 리밸(비용 포함) vs QQQ 100% — 부록 v2.1 §5 진단."""
    dates = res.dates
    panel = {"QQQ": bench.equity, "STRAT": res.equity}
    tg = const_targets(dates, {"QQQ": 1.0 - w_sat, "STRAT": w_sat})
    fr = R.month_start_flags(dates)
    cs = simulate(panel, dates, tg, s0=1, force_rebalance=fr)
    q = simulate({"QQQ": bench.equity}, dates, const_targets(dates, {"QQQ": 1.0}), s0=1)
    out = {}
    hold_start = next(d for d in dates if d > DESIGN_END)
    for period, a, b in (("design", EVAL_START, DESIGN_END), ("holdout", hold_start, dates[-1]),
                         ("full", EVAL_START, dates[-1])):
        w1, w2 = window(cs, a, b), window(q, a, b)
        out[period] = {"core_sat": {k: w1[k] for k in ("terminal", "cagr", "mdd", "sharpe", "calmar", "martin")},
                       "qqq": {k: w2[k] for k in ("terminal", "cagr", "mdd", "sharpe", "calmar", "martin")}}
    return out


def static_control(panel, dates, weights: dict, s0: int) -> Sim:
    """타이밍 없는 정적 대조(월초 리밸, 같은 비용)."""
    return simulate(panel, dates, const_targets(dates, weights), s0=s0,
                    force_rebalance=R.month_start_flags(dates))


def run_card(idea_id: str, sig_fn, base_params: dict, key: str, names: list[str], *,
             control_weights, do_log: bool = True, rc_B: int = 2000, with_paperlab: bool = True) -> dict:
    """카드 1장 전체 실행: 게이트 평가 + 진단(민감도·정적대조·코어위성·DCA·페이퍼랩 상관)."""
    dates, panel, info = load_panel(names + ["QQQ", "SCHD", "GLD"])
    ev = evaluate_idea(idea_id, sig_fn, base_params, key, dates, panel, universe=names,
                       do_log=do_log, rc_B=rc_B)
    res, bench, s0 = ev["res"], ev["bench"], ev["s0"]
    hold_start, end = ev["hold_start"], dates[-1]
    periods = (("design", EVAL_START, DESIGN_END), ("holdout", hold_start, end), ("full", EVAL_START, end))

    # 정적 대조
    cw = control_weights(res) if callable(control_weights) else control_weights
    ctl = static_control(panel, dates, cw, s0)
    control = {"weights": cw}
    for p, a, b in periods:
        w = window(ctl, a, b)
        control[p] = {k: w[k] for k in ("terminal", "cagr", "mdd", "sharpe", "calmar", "martin")}
    # 데이터 민감도(상수배당 근사로 교체) — 보고만
    sd_, sp_, _ = load_panel(names + ["QQQ"], sens=True)
    s0s = idx_of(sd_, EVAL_START)
    tg_s = sig_fn(sp_, sd_, **base_params)
    rs = simulate(sp_, sd_, tg_s, s0=s0s)
    sens = {"switches": rs.n_switch}
    for p, a, b in periods:
        w = window(rs, a, b)
        sens[p] = {k: w[k] for k in ("terminal", "cagr", "mdd", "sharpe")}
    # 전체창 지표
    full = {}
    for p, a, b in periods:
        full[p] = {"strat": {k: window(res, a, b)[k] for k in ("terminal", "cagr", "mdd", "vol", "sharpe", "calmar", "martin")},
                   "qqq": {k: window(bench, a, b)[k] for k in ("terminal", "cagr", "mdd", "vol", "sharpe", "calmar", "martin")}}
    years = max((end - EVAL_START).days / 365.25, 1e-9)
    out = {
        "idea_id": idea_id, "lane": LANE, "params": base_params, "data_sources": info,
        "panel_first": dates[0].isoformat(), "panel_last": end.isoformat(),
        "eval_start": EVAL_START.isoformat(), "design_end": DESIGN_END.isoformat(),
        "holdout_start": hold_start.isoformat(),
        "cost_primary": COST_BASE.label, "switches": res.n_switch,
        "switches_per_year": res.n_switch / years, "switch_dates": [d.isoformat() for d in res.switch_dates],
        "total_cost_unit_usd": res.total_cost,
        "plateau": ev["plateau"], "n_eff_idea": ev["n_eff_idea"],
        "design": ev["design"], "holdout": ev["holdout"], "full_windows": full,
        "yearly": {"strat": yearly_returns(res), "qqq": yearly_returns(bench)},
        "static_control": control, "data_sensitivity": sens,
        "core_satellite_80_20": core_satellite(res, bench),
        "dca": dca_compare(sig_fn, base_params, dates, panel),
        "dca_fx_night_50bp": dca_compare(sig_fn, base_params, dates, panel, fx_bps=50.0),
        "peeked_before_run": ev["peeked_before_run"],
        "verdict": ev["holdout"]["decision"]["verdict"],
        "_res": res,
    }
    if with_paperlab:
        streams = paperlab_streams(EVAL_START)
        corr = {}
        for p, a, b in periods:
            corr[p] = correlations(res, streams, a, b)
        out["paperlab_corr"] = corr
    return out


def render_results(out: dict, extra_lines: list[str]) -> str:
    """리포트 RESULTS_BELOW 이후 결과 섹션(마크다운)."""
    L: list[str] = []
    h, d = out["holdout"], out["design"]
    L.append(f"\n## 판정: **{out['verdict']}** (레인 1, 홀드아웃 헤드라인 · `gate.decide`)\n")
    L.append(f"- 실행: {out['panel_first']}~{out['panel_last']} 패널, 평가 {out['eval_start']}~, "
             f"설계≤{out['design_end']}, 홀드아웃 {out['holdout_start']}~. 주 비용: {out['cost_primary']}.")
    L.append(f"- 설계 판정 {d['decision']['verdict']} / 홀드아웃 판정 {h['decision']['verdict']}. "
             f"홀드아웃 축: " + ", ".join(f"{k}={v}" for k, v in h["decision"]["axes"].items()))
    for r in h["decision"]["reasons"][:8]:
        L.append(f"  - {r}")
    L.append("\n### 단위자본(시작 $1,000, 비용 후) vs QQQ B&H\n")
    L.append("| 구간 | 전략 최종배수 | QQQ 최종배수 | 전략 CAGR | QQQ CAGR | 전략 MDD | QQQ MDD | 전략 Sharpe | QQQ Sharpe | "
             "전략 Calmar | QQQ Calmar | 변동성(전략/QQQ) |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for p in ("design", "holdout", "full"):
        s, q = out["full_windows"][p]["strat"], out["full_windows"][p]["qqq"]
        L.append(f"| {p} | {fmt(s['terminal'])} | {fmt(q['terminal'])} | {fmt_pct(s['cagr'])} | {fmt_pct(q['cagr'])} | "
                 f"{fmt_pct(s['mdd'])} | {fmt_pct(q['mdd'])} | {fmt(s['sharpe'])} | {fmt(q['sharpe'])} | "
                 f"{fmt(s['calmar'])} | {fmt(q['calmar'])} | {fmt_pct(s['vol'], 0)}/{fmt_pct(q['vol'], 0)} |")
    L.append("\n### 게이트 축 상세\n")
    L.append("| 항목 | 설계 | 홀드아웃 |")
    L.append("|---|---:|---:|")
    rows = [
        ("최종/QQQ(경제)", lambda x: fmt(x["metrics"]["terminal_vs_b0"], 3)),
        ("DSR(프로그램, v2.2 로버스트)", lambda x: fmt(x["dsr_program"], 3)),
        ("DSR(아이디어 내부)", lambda x: fmt(x["dsr_idea"], 3)),
        ("RC p (단일)", lambda x: fmt(x["rc_single_p"], 3)),
        ("RC p (가족: 후보+이웃5)", lambda x: fmt(x["rc_family_p"], 3)),
        ("SPA p (가족)", lambda x: fmt(x["spa_family_p"], 3)),
        ("서브구간 승률(3y/6m vs QQQ)", lambda x: f"{fmt(x['subperiod']['win_rate'])} (n={x['subperiod']['n_windows']})"),
        ("비용 생존 배수", lambda x: fmt(x["cost_stress_mult"], 1)),
        ("net 초과(1×/1.5×/2×/25bp스트레스)", lambda x: " / ".join(
            fmt(v, 3) for v in [x["base_net_excess"], x["cost_stress_net_excess"]["1.5x"],
                                x["cost_stress_net_excess"]["2.0x"], x["cost_stress_net_excess"]["25bp_stress"]])),
        ("손익분기 왕복 bps", lambda x: fmt(x["breakeven_roundtrip_bps"], 0)),
        ("시작일 랜덤화 승률(vs B0 DCA)", lambda x: f"{fmt(x['start_date']['win_rate'])} (n={x['start_date']['n']}, "
                                               f"median {fmt_pct(x['start_date']['median_margin'])})"),
        ("Calmar 비열위 / Martin 비열위", lambda x: f"{x['metrics']['calmar_not_worse']} / {x['metrics']['ulcer_not_worse']}"),
        ("변동성매칭(10%) 최종배수 전략/QQQ", lambda x: f"{fmt(x['vol_match_10']['strat'])} / {fmt(x['vol_match_10']['qqq'])}"),
        ("QQQ 일수익 상관", lambda x: fmt(x["corr_qqq_daily"])),
    ]
    for name, f in rows:
        L.append(f"| {name} | {f(d)} | {f(h)} |")
    pl = out["plateau"]
    L.append(f"\n**평탄성(설계)**: 이웃 net>QQQ 비율 {fmt(pl['frac_pos'])}, 이웃 median Sharpe {fmt(pl['median_neighbor_sharpe'])} "
             f"vs 중심 {fmt(pl['center_sharpe'])} → pass={pl['pass']} border={pl['border']}. N_eff(아이디어)={out['n_eff_idea']}.")
    L.append("\n| 이웃 | 파라미터 | 설계 최종배수 | Sharpe | MDD | net vs QQQ |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for k, v in pl["neighbors"].items():
        L.append(f"| {k} | {v['param']} | {fmt(v['terminal'])} | {fmt(v['sharpe'])} | {fmt_pct(v['mdd'])} | {fmt(v['net_excess_vs_qqq'], 3)} |")
    L.append(f"\n**회전**: 전환 {out['switches']}회({fmt(out['switches_per_year'], 1)}/년), 단위장부 총비용 ${fmt(out['total_cost_unit_usd'])}.")
    dca = out["dca"]
    L.append("\n### DCA($32 시드 + 월 $35, 입금 FX 20bp)\n")
    L.append("| 장부 | 최종 | 입금 | XIRR | 달러MDD | 총비용 | 전환 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for k, v in dca.items():
        L.append(f"| {k} | ${fmt(v['final'])} | ${fmt(v['deposited'], 0)} | {fmt_pct(v['xirr'])} | {fmt_pct(v['dollar_mdd'])} | "
                 f"${fmt(v['total_cost'])} | {v['switches']} |")
    n50 = out["dca_fx_night_50bp"]
    L.append(f"\n야간 환전 50bp 민감도: 전략 ${fmt(n50['strategy']['final'])} vs B0 ${fmt(n50['B0_QQQ60_SCHD25_GLD15']['final'])} "
             f"vs B1 ${fmt(n50['B1_QQQ100']['final'])} (입금에만 부과 → 상대순위 불변).")
    L.append("\n### 진단(게이트 아님)\n")
    c = out["static_control"]
    L.append(f"- 정적 대조 {c['weights']}: 홀드아웃 최종 {fmt(c['holdout']['terminal'])} / CAGR {fmt_pct(c['holdout']['cagr'])} / "
             f"MDD {fmt_pct(c['holdout']['mdd'])} / Sharpe {fmt(c['holdout']['sharpe'])}; 설계 최종 {fmt(c['design']['terminal'])} "
             f"/ MDD {fmt_pct(c['design']['mdd'])} / Sharpe {fmt(c['design']['sharpe'])}.")
    cs = out["core_satellite_80_20"]
    for p in ("design", "holdout", "full"):
        a, q = cs[p]["core_sat"], cs[p]["qqq"]
        L.append(f"- 코어-위성 80% QQQ + 20% 전략 [{p}]: 최종 {fmt(a['terminal'])} vs QQQ {fmt(q['terminal'])}, "
                 f"MDD {fmt_pct(a['mdd'])} vs {fmt_pct(q['mdd'])}, Sharpe {fmt(a['sharpe'])} vs {fmt(q['sharpe'])}, "
                 f"Calmar {fmt(a['calmar'])} vs {fmt(q['calmar'])}.")
    s = out["data_sensitivity"]
    L.append(f"- 데이터 민감도(상수배당 근사로 교체): 홀드아웃 최종 {fmt(s['holdout']['terminal'])} (주 {fmt(out['full_windows']['holdout']['strat']['terminal'])}), "
             f"설계 {fmt(s['design']['terminal'])} (주 {fmt(out['full_windows']['design']['strat']['terminal'])}), 전환 {s['switches']}회.")
    y = out["yearly"]
    L.append("\n| 연도 | " + " | ".join(str(k) for k in y["strat"]) + " |")
    L.append("|---|" + "---:|" * len(y["strat"]))
    L.append("| 전략 | " + " | ".join(fmt_pct(v) for v in y["strat"].values()) + " |")
    L.append("| QQQ | " + " | ".join(fmt_pct(y["qqq"].get(k)) for k in y["strat"]) + " |")
    if "paperlab_corr" in out:
        pc = out["paperlab_corr"]
        L.append("\n### 기존 페이퍼랩(ETF 유니버스, 백필 엔진) 일수익 상관\n")
        L.append("| 전략 | 설계 | 홀드아웃 | 전체 |")
        L.append("|---|---:|---:|---:|")
        for k in pc["full"]:
            L.append(f"| {k} | {fmt(pc['design'][k])} | {fmt(pc['holdout'][k])} | {fmt(pc['full'][k])} |")
        L.append("\n(hibeta·mom_top5·스윙 등 단일종목 전략은 이 워크트리 캐시에 60+종목이 없어 제외 — 이들은 QQQ 상관이 높아 "
                 "qqq_bh/ftlt_1x 상관이 근사 상한.)")
    L.extend(extra_lines)
    return "\n".join(L) + "\n"


def write_report(path: Path, body: str) -> None:
    """사전등록 섹션(RESULTS_BELOW 마커까지)은 보존하고 그 아래를 결과로 교체."""
    text = path.read_text(encoding="utf-8")
    marker = "<!-- RESULTS_BELOW -->"
    head = text.split(marker)[0] + marker + "\n"
    path.write_text(head + body, encoding="utf-8")


def fmt_pct(x, nd=1):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    return f"{x * 100:+.{nd}f}%"


def fmt(x, nd=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    return f"{x:.{nd}f}"


def jsonable(o):
    """Sim 등 비직렬화 객체 제거·날짜 문자열화."""
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items() if not isinstance(v, Sim)}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o
