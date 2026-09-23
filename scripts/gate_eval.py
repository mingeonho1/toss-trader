#!/usr/bin/env python3
"""gate_eval — Gate v2 재사용 하네스 (사양 docs/gate_v2_spec.md).

다른 실험 스크립트(vol targeting, TSMOM, 섹터 모멘텀, 단일종목 스윙 …)가 **import 해서**
쓰는 얇은 도구 모음이다. 데이터 로딩·백테스터에 의존하지 않는다: 후보의 **단위자본 일수익
시계열**과 **벤치 일수익 시계열**(+선택적으로 DCA 자본곡선/현금흐름)만 넘기면

  1) 전체 지표(단위자본 TWR + 화폐가중 DCA)를 계산하고,
  2) 설계기간(design) vs 홀드아웃(holdout) 분할을 돌리고(사양 §2.1),
  3) 시도 원장(reports/trials_ledger.jsonl)에 적재하며(홀드아웃은 peek-once 강제),
  4) 마크다운 표 1행을 렌더링한다.

핵심: 유의성(DSR·Reality Check)은 **원장의 시도 SR 분포**에서 N/N_eff를 읽어 다중검정을
반영한다. 하네스 밖 백테스트는 게이트 증거로 불인정(§3.1)이므로 모든 평가를 이 모듈로 흘린다.
"""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import gate  # noqa: E402

DEFAULT_LEDGER = str(Path(__file__).resolve().parent.parent / gate.DEFAULT_LEDGER)


# ── 지표 계산 ────────────────────────────────────────────────────────────────
def unit_capital_metrics(returns: list[float], *, dates: list[date] | None = None,
                         days: int | None = None, ppy: int = 252, rf: float = 0.0,
                         sr_trials: list[float] | None = None,
                         n_eff: int | None = None) -> dict:
    """단위자본(TWR) 일수익 스트림 → 리스크·위험조정 지표 세트(사양 §1.4).

    days(달력일수)를 주지 않고 dates를 주면 dates[-1]-dates[0]로 산출. 둘 다 없으면 거래일수를
    365/ppy로 환산(근사). sr_trials(비연율 일 SR 목록)를 주면 DSR도 계산한다.
    """
    if not returns:
        return {}
    if days is None:
        if dates and len(dates) >= 2:
            days = max((dates[-1] - dates[0]).days, 1)
        else:
            days = max(int(round(len(returns) * 365.0 / ppy)), 1)
    eq = gate.returns_to_equity(returns)
    sd = gate._std(returns, ddof=1)
    sr_daily = (gate._mean(returns) / sd) if sd > 0 else 0.0
    sk, ku = gate.skew_kurt(returns)
    cg = gate.cagr(eq, days)
    mdd = gate.max_drawdown(eq)
    m: dict = {
        "T": len(returns),
        "days": days,
        "sr_daily": sr_daily,
        "sr_annual": gate.sharpe(returns, ppy=ppy, rf=rf),
        "ann_vol": gate.annualized_vol(returns, ppy=ppy),
        "cagr": cg,
        "max_drawdown": mdd,
        "ulcer": gate.ulcer_index(eq),
        "martin": gate.martin_ratio(eq, days, ppy=ppy, rf=rf),
        "calmar": gate.calmar(cg, mdd),
        "cvar5": gate.cvar(returns, alpha=0.05),
        "skew": sk,
        "kurt": ku,
        "terminal_unit": eq[-1],
    }
    if sr_trials is not None:
        m["dsr"] = gate.deflated_sharpe_ratio(returns, sr_trials, n_eff=n_eff)
        m["n_trials"] = len(sr_trials)
        m["n_eff"] = n_eff if n_eff is not None else len(sr_trials)
    return m


def money_weighted_metrics(*, cashflows: list[tuple[date, float]] | None = None,
                           dca_equity: list[float] | None = None,
                           benchmark_terminal: float | None = None) -> dict:
    """화폐가중(DCA) 지표: terminal_wealth, XIRR, 달러 MDD(사양 §1.1).

    cashflows(입금 음/청산 양)로 XIRR을 계산하고, dca_equity(자본곡선)로 behavioral 달러 MDD를 잰다.
    benchmark_terminal을 주면 terminal_vs_B0(=후보/B0)도 채운다.
    """
    m: dict = {}
    if cashflows:
        m["xirr"] = gate.xirr(cashflows)
        m["terminal_wealth"] = cashflows[-1][1]
    if dca_equity:
        m["dollar_mdd"] = gate.max_drawdown(dca_equity)
        m.setdefault("terminal_wealth", dca_equity[-1])
    if benchmark_terminal and m.get("terminal_wealth"):
        m["terminal_vs_b0"] = m["terminal_wealth"] / benchmark_terminal
    return m


def reality_check(candidate_excess: list[float], *,
                  family_excess: dict[str, list[float]] | None = None,
                  q: float = gate.DEFAULT_Q, B: int = 2000,
                  rng: random.Random | None = None) -> dict:
    """후보(+동족 전략들)의 벤치 대비 초과수익으로 White RC / Hansen SPA p값(사양 §3.4).

    candidate_excess = 후보의 일별 초과수익(vs B0). family_excess는 같은 아이디어의 다른 config들의
    초과수익(다중검정 가족). 둘 다 재중심화 정상 부트스트랩으로 FWER 통제.
    """
    diffs = {"_candidate": candidate_excess}
    if family_excess:
        diffs.update(family_excess)
    rng = rng or random.Random(12345)
    rc_p, stats = gate.whites_reality_check(diffs, q=q, B=B, rng=rng)
    spa_p = gate.hansen_spa(diffs, q=q, B=B, rng=random.Random(rng.randrange(1 << 30)))
    return {"rc_pvalue": rc_p, "spa_pvalue": spa_p, "rc_stats": stats}


# ── 원장 적재 + 분할 실행 ────────────────────────────────────────────────────
def _ledger_record(idea_id: str, config: dict, lane: int, period: str,
                   window: tuple[date, date] | None, um: dict, mw: dict,
                   universe: list[str] | None, stream_ref: str | None) -> dict:
    rec = {
        "idea_id": idea_id,
        "config_hash": gate.config_hash(config),
        "lane": lane,
        "params": config,
        "period": period,
        "sr_daily": um.get("sr_daily"),
        "sr_annual": um.get("sr_annual"),
        "skew": um.get("skew"),
        "kurt": um.get("kurt"),
        "T": um.get("T"),
        "mdd": um.get("max_drawdown"),
        "ulcer": um.get("ulcer"),
        "cvar5": um.get("cvar5"),
        "terminal_vs_B0": mw.get("terminal_vs_b0"),
        "xirr": mw.get("xirr"),
        "peeked_holdout": period == "holdout",
    }
    if window:
        rec["window"] = [window[0].isoformat(), window[1].isoformat()]
    if universe:
        rec["universe"] = universe
    if stream_ref:
        rec["stream_ref"] = stream_ref
    return rec


def log_evaluation(idea_id: str, config: dict, lane: int, period: str,
                   unit_metrics: dict, money_metrics: dict | None = None, *,
                   window: tuple[date, date] | None = None,
                   universe: list[str] | None = None,
                   stream_ref: str | None = None,
                   ledger_path: str = DEFAULT_LEDGER) -> dict:
    """1회 평가를 원장에 적재. 홀드아웃은 append_holdout_peek로 peek-once 강제(사양 §2.1).

    같은 idea_id로 홀드아웃을 두 번 적재하려 하면 gate.PeekOnceError가 발생한다.
    """
    mw = money_metrics or {}
    rec = _ledger_record(idea_id, config, lane, period, window, unit_metrics, mw,
                         universe, stream_ref)
    if period == "holdout":
        gate.append_holdout_peek(ledger_path, idea_id, rec)
    else:
        gate.ledger_append(ledger_path, rec)
    return rec


def run_splits(idea_id: str, config: dict, lane: int,
               cand_returns: list[float], bench_returns: list[float],
               dates: list[date], design_end: date, *,
               cand_dca_equity: list[float] | None = None,
               cand_cashflows: list[tuple[date, float]] | None = None,
               bench_terminal: float | None = None,
               family_excess: dict[str, list[float]] | None = None,
               universe: list[str] | None = None,
               ledger_path: str = DEFAULT_LEDGER,
               rc_B: int = 2000, log: bool = True) -> dict:
    """설계기간 vs 홀드아웃 분할을 돌리고 원장에 적재한다(헤드라인 게이트, 사양 §2.1).

    cand_returns/bench_returns/dates는 같은 길이로 정렬되어 있어야 한다(수익은 dates[1:]에 대응).
    설계기간에서 시도 SR 분포(원장)를 읽어 DSR을 만들고, 홀드아웃은 딱 1회 평가한다.
    반환: {"design": {...}, "holdout": {...}} 각 축 입력을 담은 dict.
    """
    n = min(len(bench_returns), len(cand_returns), len(dates) - 1)
    cand_returns, bench_returns = cand_returns[:n], bench_returns[:n]
    ret_dates = dates[1:n + 1]
    di = [i for i, d in enumerate(ret_dates) if d <= design_end]
    hi = [i for i, d in enumerate(ret_dates) if d > design_end]
    out: dict = {}

    for period, idxs in (("design", di), ("holdout", hi)):
        if not idxs:
            continue
        cr = [cand_returns[i] for i in idxs]
        br = [bench_returns[i] for i in idxs]
        pdates = [ret_dates[i] for i in idxs]
        sr_trials = gate.ledger_trial_sharpes(ledger_path, idea_id) or None
        n_eff = None
        if sr_trials:
            # 같은 아이디어의 시도 스트림이 있으면 N_eff로 흡수(여기선 raw N 폴백)
            n_eff = len(sr_trials)
        um = unit_capital_metrics(cr, dates=pdates, sr_trials=sr_trials, n_eff=n_eff)
        mw = money_weighted_metrics(cashflows=cand_cashflows,
                                    dca_equity=cand_dca_equity,
                                    benchmark_terminal=bench_terminal)
        excess = [a - b for a, b in zip(cr, br)]
        rc = reality_check(excess, family_excess=family_excess, B=rc_B)
        axis_input = {
            "lane": lane,
            "terminal_vs_b0": mw.get("terminal_vs_b0"),
            "dsr": um.get("dsr"),
            "rc_pvalue": rc["rc_pvalue"],
            "spa_pvalue": rc["spa_pvalue"],
            "mdd": um.get("max_drawdown"),
            **{k: um[k] for k in ("sr_daily", "sr_annual", "cagr", "ulcer",
                                  "martin", "calmar", "cvar5", "skew", "kurt")
               if k in um},
        }
        window = (pdates[0], pdates[-1]) if pdates else None
        if log:
            log_evaluation(idea_id, config, lane, period, um, mw, window=window,
                           universe=universe, ledger_path=ledger_path)
        out[period] = {"unit": um, "money": mw, "reality_check": rc,
                       "axis_input": axis_input, "window": window}
    return out


# ── 마크다운 렌더 ────────────────────────────────────────────────────────────
def markdown_header() -> str:
    return ("| 아이디어 | 레인 | 구간 | 최종/B0 | XIRR | CAGR | Sharpe | MDD | "
            "Ulcer | Calmar | CVaR5 | DSR | RC p | 판정 |\n"
            "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")


def _fmt(v, pct=False, nd=2):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v * 100:+.{nd}f}%" if pct else f"{v:.{nd}f}"


def markdown_row(idea_id: str, lane: int, period: str, um: dict, mw: dict,
                 rc: dict, decision) -> str:
    return (f"| {idea_id} | {lane} | {period} | "
            f"{_fmt(mw.get('terminal_vs_b0'))} | {_fmt(mw.get('xirr'), pct=True, nd=1)} | "
            f"{_fmt(um.get('cagr'), pct=True, nd=1)} | {_fmt(um.get('sr_annual'))} | "
            f"{_fmt(um.get('max_drawdown'), pct=True, nd=1)} | {_fmt(um.get('ulcer'))} | "
            f"{_fmt(um.get('calmar'))} | {_fmt(um.get('cvar5'), pct=True, nd=2)} | "
            f"{_fmt(um.get('dsr'))} | {_fmt(rc.get('rc_pvalue'), nd=3)} | {decision} |")


def evaluate_and_render(idea_id: str, config: dict, lane: int,
                        cand_returns: list[float], bench_returns: list[float],
                        dates: list[date], design_end: date, **kw) -> dict:
    """run_splits + decide + 마크다운 표(헤더+행) 렌더를 한 번에.

    반환에 'markdown'(설계·홀드아웃 각 1행)과 'decisions'를 포함한다.
    """
    splits = run_splits(idea_id, config, lane, cand_returns, bench_returns,
                        dates, design_end, **kw)
    lines = [markdown_header()]
    decisions = {}
    for period, res in splits.items():
        dec = gate.decide(res["axis_input"])
        decisions[period] = dec
        lines.append(markdown_row(idea_id, lane, period, res["unit"], res["money"],
                                  res["reality_check"], dec))
    return {"splits": splits, "decisions": decisions, "markdown": "\n".join(lines)}
