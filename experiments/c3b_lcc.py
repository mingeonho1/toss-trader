#!/usr/bin/env python3
"""experiments/c3b_lcc.py — Cycle 3 · idea family ``c3b`` (leverage as cost compression).

메커니즘: 수수료는 노셔널의 %다. 노출 1단위를 3x ETF(TQQQ)로 사면 노셔널이 1/3 →
노출당 수수료가 1/3 로 압축된다. 1x 에서 총엣지 ≈ 왕복비용이라 순엣지 0 인 신호가
3x·(1/3노셔널) 집행에서 순엣지 양(+)으로 전환될 수 있다(경로감쇠·경비 차감 후). 이를
이벤트 슬리브·코어-위성 포트폴리오로 사전등록·판정한다. 상세 사전등록은
reports/cycle3_c3b_lcc.md §0–§2.

이 스크립트는 histdata(데이터)·research(백테스터)·gate(판정)·c2b(RSI 신호)·c2c(캘린더/FOMC)
를 **소비만** 하고 src/ 를 수정하지 않는다.

사용:  PYTHONPATH=src .venv/bin/python experiments/c3b_lcc.py [--ledger PATH] [--rc-b N]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
import gate_eval as ge  # noqa: E402
import c2b_meanrev as c2b  # noqa: E402  (RSI(2) 신호 재사용)
import c2c_calendar as c2c  # noqa: E402  (TOM/FOMC 캘린더·스케줄 재사용)

DEFAULT_LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
NDX_DIV_YIELD = 0.007
DESIGN_END = date(2008, 12, 31)          # 롱윈도 설계 1986–2008 / 홀드아웃 2009–2026
REAL_DESIGN_END = date(2021, 12, 31)     # 실물 QQQ/TQQQ 설계 2016–2021 / 홀드아웃 2022–2026
TQQQ_EXPENSE = 0.0095
TQQQ_SPREAD = 0.005
NOTIONAL_FRAC = 1.0 / 3.0                 # 1x-상당 노출을 위한 3x 노셔널 분수
BPS = 1e-4

EXEC_TIER = {"QQQ": R.TIER_ETF, "NDXTR": R.TIER_ETF, "TQQQ3X": R.TIER_LEVERAGED_ETF,
             "TQQQ": R.TIER_LEVERAGED_ETF, "LEV": R.TIER_LEVERAGED_ETF, "BASE": R.TIER_ETF}


# ══════════════════════════════════════════════════════════════════════════════
# 데이터
# ══════════════════════════════════════════════════════════════════════════════
def build_stack() -> dict:
    """FRED NDX 가격지수(신호) + NDX-TR(1x 실행) + 합성 TQQQ(3x) + 현금(0% / DTB3)."""
    ndx = hd.load_fred("NASDAQ100")
    dates = [c.dt for c in ndx]
    sig = [c.close for c in ndx]                                   # 신호: 가격지수
    tr = hd.index_total_return(ndx, NDX_DIV_YIELD, symbol="NDXTR")  # 1x 실행: 총수익
    dtb3 = hd.load_fred("DTB3")
    x3 = hd.synthetic_leveraged(tr, 3.0, rf_candles=dtb3, rf_kind="yield",
                                annual_expense=TQQQ_EXPENSE, borrow_spread=TQQQ_SPREAD,
                                symbol="TQQQ3X")
    tr_c = [c.close for c in tr]
    x3_c = [c.close for c in x3]
    cash_zero = [0.0] * len(dates)
    cash_dtb3 = R.cash_rate_from_annual(c2c._annual_ffill(dates, dtb3))
    return {"dates": dates, "sig": sig, "tr": tr_c, "x3": x3_c,
            "cash0": cash_zero, "cash_dtb3": cash_dtb3}


def build_real_stack() -> dict:
    """실물 QQQ/TQQQ 2016–2026 확인용(BIL 현금)."""
    panel = hd.align_panel(hd.load_panel(["QQQ", "TQQQ", "BIL"], adjusted=True))
    dates = [c.dt for c in panel["QQQ"]]
    return {"dates": dates,
            "sig": [c.close for c in panel["QQQ"]],       # 실물은 QQQ 조정종가로 신호
            "tr": [c.close for c in panel["QQQ"]],
            "x3": [c.close for c in panel["TQQQ"]],
            "cash0": [0.0] * len(dates),
            "cash_dtb3": R.cash_rate_from_prices([c.close for c in panel["BIL"]])}


# ══════════════════════════════════════════════════════════════════════════════
# 신호 / 스케줄 (전부 causal — closes≤t 또는 순수 date 함수)
# ══════════════════════════════════════════════════════════════════════════════
def rsi2_tw(sig_closes, cfg) -> list[float]:
    """c2b RSI(2) 포지션(tw 규약: tw[i] 는 close i 신호, exec_lag=1 로 체결)."""
    return c2b.rsi2_positions(sig_closes, **cfg)


def _nn(d: date) -> date:
    """다음-다음 거래일(순수 date 함수). exec_lag=1 하에서 tw[t] 가 담는 수익일 = date[t+2]."""
    return c2c.next_trading_day(c2c.next_trading_day(d))


def calendar_capture_tw(dates, hold_fn) -> list[float]:
    """캘린더 이벤트를 tw 규약으로: tw[t]=1 이면 exec_lag=1 에서 date[t+2](=이벤트일) 수익을 담는다.

    hold_fn(d)->bool 은 순수 date 함수(c2c). _nn 도 순수 date 함수라 배열 미래참조 없음(prefix invariant).
    """
    return [1.0 if hold_fn(_nn(dates[t])) else 0.0 for t in range(len(dates))]


def _tom_hold(n_end=1, n_start=3):
    return c2c.hold_tom(n_end, n_start)


def _fomc_hold():
    return c2c.hold_fomc()


def union_events_tw(dates, sig_closes, rsi_cfg, *, tom=(1, 3), use_rsi=True,
                    use_tom=True, use_fomc=True) -> list[float]:
    """RSI2 ∨ TOM(창) ∨ pre-FOMC 합집합 in-market 지시자(tw 규약)."""
    n = len(dates)
    out = [0.0] * n
    rsi = rsi2_tw(sig_closes, rsi_cfg) if use_rsi else [0.0] * n
    tom_tw = calendar_capture_tw(dates, _tom_hold(*tom)) if use_tom else [0.0] * n
    fomc_tw = calendar_capture_tw(dates, _fomc_hold()) if use_fomc else [0.0] * n
    for t in range(n):
        out[t] = 1.0 if (rsi[t] > 0.5 or tom_tw[t] > 0.5 or fomc_tw[t] > 0.5) else 0.0
    return out


# ── lookahead_guard 용 signal_fn 래퍼 (panel["SIG"] 종가 + dates → target_weights) ──
def make_signal_fn(kind, cfg):
    def fn(panel, dates):
        sigc = list(panel["SIG"])
        if kind == "rsi2":
            tw = rsi2_tw(sigc, cfg)
        elif kind == "events":
            tw = union_events_tw(dates, sigc, cfg)
        elif kind == "prefomc":
            tw = calendar_capture_tw(dates, _fomc_hold())
        elif kind == "tom":
            tw = calendar_capture_tw(dates, _tom_hold(*cfg.get("tom", (1, 3))))
        else:
            raise ValueError(kind)
        return [{"POS": v} for v in tw]
    return fn


# ══════════════════════════════════════════════════════════════════════════════
# 비용
# ══════════════════════════════════════════════════════════════════════════════
def cost_for(symbols, commission_bps=R.STANDARD_COMMISSION_BPS):
    tiers = {s: EXEC_TIER.get(s, R.TIER_LARGE_CAP) for s in symbols}
    return R.CostSpec.from_tiers(tiers, commission_bps=commission_bps,
                                 slippage_bps=5.0, fx_bps=20.0)


# ══════════════════════════════════════════════════════════════════════════════
# 거래 인덱스 추출 (메커니즘 per-trade 분석: 1x vs 3x·1/3노셔널, 동일 진입/청산)
# ══════════════════════════════════════════════════════════════════════════════
def trade_index_pairs(tw, n, *, exec_lag=1):
    """tw(보유희망) 런 → (진입 idx ef=i+lag, 청산 idx xf=(j+1)+lag) 쌍. c2b.extract_trades 와 동일 규약."""
    dec = [v > 0.5 for v in tw]
    pairs = []
    k = 0
    while k < n:
        if dec[k]:
            i = k
            j = k
            while j + 1 < n and dec[j + 1]:
                j += 1
            ef = i + exec_lag
            xf = min((j + 1) + exec_lag, n - 1) if (j + 1) < n else n - 1
            if ef < n and xf >= ef:
                pairs.append((ef, xf))
            k = j + 1
        else:
            k += 1
    return pairs


def per_trade_mechanism(tw, tr, x3, dates, *, exec_lag=1) -> dict:
    """거래별 메커니즘 지표: 1x 총엣지, 3x 실현 경로감쇠, 25/10bp 노출당 순엣지, t·CI·n."""
    pairs = trade_index_pairs(tw, len(dates), exec_lag=exec_lag)
    if not pairs:
        return {"n_trades": 0}
    # 노출당(E=1): 1x = QQQ 노셔널 1.0; 3x-1/3 = TQQQ 노셔널 1/3(델타 3 → 노출 1)
    gross_1x, decay, hold_days = [], [], []
    tr_trades_1x, x3_trades = [], []
    for ef, xf in pairs:
        r_u = tr[xf] / tr[ef] - 1.0 if tr[ef] > 0 else 0.0
        r_3 = x3[xf] / x3[ef] - 1.0 if x3[ef] > 0 else 0.0
        gross_1x.append(r_u)
        decay.append(r_3 - 3.0 * r_u)          # 실현 3x 경로감쇠(경비·차입·분산드래그 포함)
        hold_days.append(xf - ef)
        tr_trades_1x.append(R.Trade(entry_dt=dates[ef], entry_price=tr[ef],
                                    exit_dt=dates[xf], exit_price=tr[xf],
                                    side="long", notional=1.0, symbol="QQQ"))
        x3_trades.append(R.Trade(entry_dt=dates[ef], entry_price=x3[ef],
                                 exit_dt=dates[xf], exit_price=x3[xf],
                                 side="long", notional=NOTIONAL_FRAC, symbol="TQQQ3X"))

    def leg(trades, sym, commission):
        cs = cost_for([sym], commission)
        treslt = R.run_trades(trades, cost=cs, capital=1.0)
        _, lo, hi = gate.trade_pnl_bootstrap_ci(treslt.pnls, B=2000, rng=random.Random(7))
        return {"expectancy": gate.expectancy(treslt.pnls),
                "tstat": gate.trade_tstat(treslt.pnls),
                "ci_lo": lo, "ci_hi": hi, "pf": gate.profit_factor(treslt.pnls),
                "roundtrip_bps": cs.roundtrip_bps(sym),
                "gross_mean": sum(treslt.gross_pnls) / len(treslt.gross_pnls),
                "total_net": sum(treslt.pnls)}
    return {
        "n_trades": len(pairs),
        "avg_hold_days": sum(hold_days) / len(hold_days),
        "gross_edge_1x": sum(gross_1x) / len(gross_1x),
        "path_decay_3x": sum(decay) / len(decay),
        "leg_1x_25": leg(tr_trades_1x, "QQQ", 25.0),
        "leg_1x_10": leg(tr_trades_1x, "QQQ", 10.0),
        "leg_3x13_25": leg(x3_trades, "TQQQ3X", 25.0),
        "leg_3x13_10": leg(x3_trades, "TQQQ3X", 10.0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 슬리브 백테스트 (config 1,2,5): 1/3 TQQQ + 현금
# ══════════════════════════════════════════════════════════════════════════════
def sleeve_stream(tw, tr, x3, dates, cash, *, notional=NOTIONAL_FRAC,
                  commission=R.STANDARD_COMMISSION_BPS):
    """이벤트 시 notional TQQQ(나머지 현금), 그 외 현금. exec_lag=1. WeightsResult."""
    panel = {"TQQQ3X": x3, "NDXTR": tr}
    target = [({"TQQQ3X": notional} if v > 0.5 else {}) for v in tw]
    return R.run_weights(panel, dates, target, exec_lag=1,
                         cost=cost_for(["TQQQ3X"], commission), cash_rate=cash)


def qqq1x_sleeve_stream(tw, tr, dates, cash, *, commission=R.STANDARD_COMMISSION_BPS):
    """대조: 동일 신호를 1x QQQ 전액(노출 1x)로 집행(=c2b rsi2 1x 슬리브)."""
    target = [({"NDXTR": 1.0} if v > 0.5 else {}) for v in tw]
    return R.run_weights({"NDXTR": tr}, dates, target, exec_lag=1,
                         cost=cost_for(["NDXTR"], commission), cash_rate=cash)


# ══════════════════════════════════════════════════════════════════════════════
# 코어-위성 포트폴리오 (config 3,4) — 코어 B&H 무매매 + 위성 스왑 풀
# ══════════════════════════════════════════════════════════════════════════════
def boost_cost_per_exposure_day_bps(tw, dates, *, swap_frac, off_to_base,
                                    commission=R.STANDARD_COMMISSION_BPS):
    """부스트 노출-일당 커미션(bp) — 결정론적(자본경로 무관, 비압축 스펠 구조만).

    스펠(이벤트 연속 구간)마다: config4(off_to_base) 는 QQQ↔TQQQ 왕복(2자산×진입/청산),
    config3(현금) 은 현금↔TQQQ 왕복. 추가 노출 = swap_frac·(3−1)[config4] 또는 swap_frac·3[config3].
    반환: Σ스펠비용(bp) / Σ(추가노출·보유일). 압축 효과(노셔널↓ → 커미션↓)를 그대로 반영.
    """
    cs = cost_for(["LEV", "BASE"], commission)
    tb_lev = cs.trade_bps("LEV")
    tb_base = cs.trade_bps("BASE")
    pairs = trade_index_pairs(tw, len(dates), exec_lag=1)
    if not pairs:
        return None
    if off_to_base:
        per_spell = 2.0 * swap_frac * (tb_base + tb_lev)   # 진입 sell BASE+buy LEV, 청산 반대
        added_exposure = swap_frac * 2.0
    else:
        per_spell = 2.0 * swap_frac * tb_lev               # 현금↔LEV
        added_exposure = swap_frac * 3.0
    total_cost_bp = per_spell * len(pairs)
    exposure_days = added_exposure * sum((xf - ef) for ef, xf in pairs)
    return total_cost_bp / exposure_days if exposure_days > 0 else None


def core_satellite(tw, tr, x3, dates, cash, *, base_frac, swap_frac, off_to_base,
                   commission=R.STANDARD_COMMISSION_BPS):
    """코어(base_frac QQQ B&H, 무매매) + 위성(swap_frac): 이벤트 시 TQQQ, 그 외 base 또는 현금.

    코어·위성은 교차 리밸런싱 없음(코어 '무매매' 충실). 위성 풀만 run_weights 로 토글.
    반환: 총자본곡선, 순수익 스트림(len n, [0]=0), 위성 결과(비용 집계).
    """
    n = len(dates)
    # 코어: 최초 1회 매수비용 후 B&H(고정 주수 → 가격 드리프트)
    core_cost = cost_for(["NDXTR"], commission)
    entry_c = base_frac * core_cost.trade_bps("NDXTR") * BPS
    core0 = base_frac - entry_c
    core_eq = [core0 * tr[t] / tr[0] if tr[0] > 0 else 0.0 for t in range(n)]
    # 위성 풀
    swap_panel = {"LEV": x3, "BASE": tr}
    swap_tw = [({"LEV": 1.0} if v > 0.5 else ({"BASE": 1.0} if off_to_base else {}))
               for v in tw]
    swap_res = R.run_weights(swap_panel, dates, swap_tw, exec_lag=1,
                             cost=cost_for(["LEV", "BASE"], commission),
                             cash_rate=cash, start_equity=swap_frac)
    total_eq = [core_eq[t] + swap_res.equity[t] for t in range(n)]
    net = R.to_returns(total_eq)
    return {"equity": total_eq, "net": net, "swap": swap_res, "core_eq": core_eq}


# ══════════════════════════════════════════════════════════════════════════════
# 게이트 평가 (분할 + DSR 내부/프로그램 N + 상대리스크 + 비용/10bp)
# ══════════════════════════════════════════════════════════════════════════════
def _seg(returns, dates_ret, design_end, which):
    idx = [i for i, d in enumerate(dates_ret)
           if (d <= design_end if which == "design" else d > design_end)]
    return [returns[i] for i in idx], [dates_ret[i] for i in idx]


def _terminal(returns):
    eq = 1.0
    for r in returns:
        eq *= (1.0 + r)
    return eq


def relative_risk_verdict(cand_net, bench_net, dates_ret, design_end, *, is_leveraged):
    """v2.1 상대리스크: MDD_s ≥ MDD_b−5%p 그리고 Ulcer_s ≤ 1.10·Ulcer_b (설계·홀드아웃 모두).
    레버리지 계열은 전표본 MDD_s ≥ −70% 추가."""
    out = {"periods": {}, "leverage_floor_ok": True}
    for which in ("design", "holdout"):
        cs, _ = _seg(cand_net, dates_ret, design_end, which)
        bs, _ = _seg(bench_net, dates_ret, design_end, which)
        if not cs or not bs:
            continue
        ce = gate.returns_to_equity(cs)
        be = gate.returns_to_equity(bs)
        mdd_s, mdd_b = gate.max_drawdown(ce), gate.max_drawdown(be)
        ul_s, ul_b = gate.ulcer_index(ce), gate.ulcer_index(be)
        ok = (mdd_s >= mdd_b - 0.05) and (ul_s <= 1.10 * ul_b)
        out["periods"][which] = {"mdd_s": mdd_s, "mdd_b": mdd_b, "ulcer_s": ul_s,
                                 "ulcer_b": ul_b, "pass": ok}
    full_e = gate.returns_to_equity(cand_net)
    out["full_mdd_s"] = gate.max_drawdown(full_e)
    if is_leveraged:
        out["leverage_floor_ok"] = out["full_mdd_s"] >= -0.70
    both = all(p.get("pass") for p in out["periods"].values()) if out["periods"] else False
    out["pass"] = bool(both and out["leverage_floor_ok"])
    return out


def gate_eval_config(idea_id, cfg, cand_net, bench_net, dates, design_end, *,
                     is_leveraged, semi_contaminated, ledger, rc_b, program_sharpes,
                     neighbor_streams=None):
    """한 config 를 게이트에 태운다: 이웃 원장적재 → 분할(DSR 내부 N) → 프로그램 DSR →
    상대리스크 → 비용2×·10bp what-if. 결과 dict."""
    n = min(len(cand_net), len(bench_net), len(dates) - 1)
    cand, bench = cand_net[1:n + 1], bench_net[1:n + 1]
    dates_ret = dates[1:n + 1]

    # 이웃(평탄성) 설계기간 원장 적재 → 아이디어 내부 N 구축
    for ncfg, nstream in (neighbor_streams or []):
        ncand = nstream[1:n + 1]
        dn, dd = _seg(ncand, dates_ret, design_end, "design")
        if len(dn) > 2:
            um = ge.unit_capital_metrics(dn, dates=dd)
            if um:
                ge.log_evaluation(idea_id, ncfg, 1, "design", um, ledger_path=ledger)

    splits = ge.run_splits(idea_id, cfg, 1, cand, bench, dates, design_end,
                           ledger_path=ledger, rc_B=rc_b)

    # 프로그램 전체 N 으로 홀드아웃 DSR 재계산(v2.1 §3)
    prog_dsr = None
    hs, _ = _seg(cand, dates_ret, design_end, "holdout")
    if hs and program_sharpes:
        prog_dsr = gate.deflated_sharpe_ratio(hs, program_sharpes,
                                              n_eff=len(program_sharpes))
    # 경제축: 최종/QQQ100% (설계·홀드아웃)
    tvb = {}
    for which in ("design", "holdout"):
        cs, _ = _seg(cand, dates_ret, design_end, which)
        bs, _ = _seg(bench, dates_ret, design_end, which)
        if cs and bs:
            bt = _terminal(bs)
            tvb[which] = _terminal(cs) / bt if bt > 0 else float("nan")

    rr = relative_risk_verdict(cand, bench, dates_ret, design_end,
                               is_leveraged=is_leveraged)

    # 비용 2× 스트레스·10bp what-if(홀드아웃 최종/QQQ 초과)는 호출부에서 스트림 재생성으로 계산
    slim = {}
    for k in ("design", "holdout"):
        s = splits.get(k, {})
        um = s.get("unit", {})
        rc = s.get("reality_check", {})
        slim[k] = {
            "T": um.get("T"), "sr_annual": um.get("sr_annual"),
            "sr_daily": um.get("sr_daily"), "cagr": um.get("cagr"),
            "mdd": um.get("max_drawdown"), "ulcer": um.get("ulcer"),
            "calmar": um.get("calmar"), "cvar5": um.get("cvar5"),
            "dsr": um.get("dsr"), "n_trials": um.get("n_trials"),
            "rc_p": rc.get("rc_pvalue"), "spa_p": rc.get("spa_pvalue"),
            "terminal_vs_qqq": tvb.get(k),
        }
    # RC/SPA 강화 임계(반오염이면 p<0.01, 아니면 p<0.05)
    thr = 0.01 if semi_contaminated else 0.05
    ho = slim["holdout"]
    sig_ok = ((ho.get("rc_p") is not None and ho["rc_p"] < thr) or
              (ho.get("spa_p") is not None and ho["spa_p"] < thr))
    econ_ok = (ho.get("terminal_vs_qqq") is not None and ho["terminal_vs_qqq"] >= 1.0)
    verdict = "FAIL"
    if econ_ok and sig_ok and rr["pass"]:
        verdict = "CONDITIONAL"  # 슬리브+포워드 전제(사양 §6.1); 전 축 green 이어도 백테스트는 슬리브 승격
    return {"idea_id": idea_id, "semi_contaminated": semi_contaminated,
            "rc_threshold": thr, "design": slim["design"], "holdout": slim["holdout"],
            "program_dsr_holdout": prog_dsr, "relative_risk": rr,
            "terminal_vs_qqq": tvb, "econ_ok": econ_ok, "sig_ok": sig_ok,
            "verdict": verdict}


# ══════════════════════════════════════════════════════════════════════════════
# 아이디어별 드라이버
# ══════════════════════════════════════════════════════════════════════════════
def _neighbor_rsi_streams(sig, tr, x3, dates, cash, base_cfg, *, notional=NOTIONAL_FRAC):
    """RSI 파라미터 이웃 → 슬리브 net 스트림(원장 적재용)."""
    out = []
    grid = {"rsi_entry": [5.0, 7.0, 8.0, 12.0, 15.0],
            "sma_exit": [3, 4, 6, 8]}
    for key, vals in grid.items():
        for v in vals:
            cfg = dict(base_cfg)
            cfg[key] = v
            tw = rsi2_tw(sig, cfg)
            res = sleeve_stream(tw, tr, x3, dates, cash, notional=notional)
            out.append(({**cfg, "notional": notional, "kind": "neighbor"},
                        res.net_returns))
    # 노셔널 이웃
    for nf in (0.25, 0.30, 0.40, 0.50):
        tw = rsi2_tw(sig, base_cfg)
        res = sleeve_stream(tw, tr, x3, dates, cash, notional=nf)
        out.append(({**base_cfg, "notional": nf, "kind": "neighbor_notional"},
                    res.net_returns))
    return out


def cost_whatifs(make_stream_fn, bench_net, dates, design_end):
    """비용 2× 스트레스·10bp what-if: 홀드아웃 최종/QQQ 초과(>0 생존?)."""
    out = {}
    for tag, commission in (("std_25bp", 25.0), ("promo_10bp", 10.0)):
        net = make_stream_fn(commission).net_returns
        n = min(len(net), len(bench_net), len(dates) - 1)
        cand, bench = net[1:n + 1], bench_net[1:n + 1]
        dr = dates[1:n + 1]
        cs, _ = _seg(cand, dr, design_end, "holdout")
        bs, _ = _seg(bench, dr, design_end, "holdout")
        out[tag] = _terminal(cs) / _terminal(bs) if bs and _terminal(bs) > 0 else None
    # 2× 스트레스: 25bp 의 모든 비용요율 2배 → commission 50bp 근사(반호가/슬립 포함은 stress)
    net2 = make_stream_fn(("stress2x",)).net_returns
    n = min(len(net2), len(bench_net), len(dates) - 1)
    cs, _ = _seg(net2[1:n + 1], dates[1:n + 1], design_end, "holdout")
    bs, _ = _seg(bench_net[1:n + 1], dates[1:n + 1], design_end, "holdout")
    out["stress_2x"] = _terminal(cs) / _terminal(bs) if bs and _terminal(bs) > 0 else None
    return out


def run_all(stack, design_end, *, tag, ledger, rc_b, program_sharpes):
    dates = stack["dates"]
    sig, tr, x3 = stack["sig"], stack["tr"], stack["x3"]
    cash = stack["cash0"]
    bench_net = R.to_returns(tr)                     # QQQ 100% = NDX-TR B&H
    base_rsi = {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10}
    results = {"tag": tag, "design_end": design_end.isoformat(),
               "span": [dates[0].isoformat(), dates[-1].isoformat()], "configs": {}}

    def stream_maker(tw, kind):
        """commission 인자(또는 ('stress2x',))로 슬리브/포트 net 스트림 생성기 반환."""
        def mk(comm):
            if isinstance(comm, tuple):     # stress2x
                base = sleeve_stream(tw, tr, x3, dates, cash) if kind == "sleeve" else None
                if kind == "sleeve":
                    panel = {"TQQQ3X": x3, "NDXTR": tr}
                    target = [({"TQQQ3X": NOTIONAL_FRAC} if v > 0.5 else {}) for v in tw]
                    return R.run_weights(panel, dates, target, exec_lag=1,
                                         cost=cost_for(["TQQQ3X"]).stress(2.0),
                                         cash_rate=cash)
            return sleeve_stream(tw, tr, x3, dates, cash, commission=comm)
        return mk

    # ── config 1: c3b_lcc_rsi2 ──
    id1 = f"c3b_lcc_rsi2{tag}"
    tw1 = rsi2_tw(sig, base_rsi)
    s1 = sleeve_stream(tw1, tr, x3, dates, cash)
    q1 = qqq1x_sleeve_stream(tw1, tr, dates, cash)      # 대조(1x QQQ 슬리브)
    mech1 = per_trade_mechanism(tw1, tr, x3, dates)
    neigh1 = _neighbor_rsi_streams(sig, tr, x3, dates, cash, base_rsi)
    g1 = gate_eval_config(id1, {**base_rsi, "exec": "tqqq_1_3", "notional": NOTIONAL_FRAC},
                          s1.net_returns, bench_net, dates, design_end,
                          is_leveraged=True, semi_contaminated=True, ledger=ledger,
                          rc_b=rc_b, program_sharpes=program_sharpes,
                          neighbor_streams=neigh1)
    g1["cost_whatifs"] = cost_whatifs(stream_maker(tw1, "sleeve"), bench_net, dates, design_end)
    g1["mechanism"] = mech1
    g1["qqq1x_holdout_sharpe"] = gate.sharpe(_seg(q1.net_returns[1:], dates[1:len(q1.net_returns)],
                                                  design_end, "holdout")[0])
    g1["tim"] = sum(1 for v in tw1 if v > 0.5) / len(tw1)
    results["configs"]["c3b_lcc_rsi2"] = g1

    # ── config 2: c3b_lcc_events ──
    id2 = f"c3b_lcc_events{tag}"
    tw2 = union_events_tw(dates, sig, base_rsi)
    s2 = sleeve_stream(tw2, tr, x3, dates, cash)
    mech2 = per_trade_mechanism(tw2, tr, x3, dates)
    g2 = gate_eval_config(id2, {"union": "rsi2|tom|fomc", "exec": "tqqq_1_3"},
                          s2.net_returns, bench_net, dates, design_end,
                          is_leveraged=True, semi_contaminated=True, ledger=ledger,
                          rc_b=rc_b, program_sharpes=program_sharpes)
    g2["cost_whatifs"] = cost_whatifs(stream_maker(tw2, "sleeve"), bench_net, dates, design_end)
    g2["mechanism"] = mech2
    g2["tim"] = sum(1 for v in tw2 if v > 0.5) / len(tw2)
    results["configs"]["c3b_lcc_events"] = g2

    # ── config 3: c3b_core_sat (85 코어 + 15 위성, 위성 이벤트=전액 TQQQ) ──
    id3 = f"c3b_core_sat{tag}"
    cs3 = core_satellite(tw2, tr, x3, dates, cash, base_frac=0.85, swap_frac=0.15,
                         off_to_base=False)
    g3 = gate_eval_config(id3, {"core": 0.85, "sat": 0.15, "sat_exec": "full_tqqq"},
                          cs3["net"], bench_net, dates, design_end,
                          is_leveraged=True, semi_contaminated=True, ledger=ledger,
                          rc_b=rc_b, program_sharpes=program_sharpes)

    def mk3(comm):
        c = 25.0 if isinstance(comm, tuple) else comm
        r = core_satellite(tw2, tr, x3, dates, cash, base_frac=0.85, swap_frac=0.15,
                           off_to_base=False, commission=c)
        # WeightsResult 유사 객체(net_returns 만 필요)
        return type("X", (), {"net_returns": r["net"]})()
    g3["cost_whatifs"] = cost_whatifs(mk3, bench_net, dates, design_end)
    g3["sat_total_cost"] = cs3["swap"].total_cost
    g3["event_days"] = sum(1 for v in tw2 if v > 0.5)
    results["configs"]["c3b_core_sat"] = g3

    # ── config 4: c3b_core_boost (상시 QQQ, 이벤트 시 1/3 → TQQQ) ──
    id4 = f"c3b_core_boost{tag}"
    cs4 = core_satellite(tw2, tr, x3, dates, cash, base_frac=2.0 / 3.0,
                         swap_frac=1.0 / 3.0, off_to_base=True)
    g4 = gate_eval_config(id4, {"base": "qqq_100", "boost_frac": 1.0 / 3.0,
                                "event": "rsi2|tom|fomc"},
                          cs4["net"], bench_net, dates, design_end,
                          is_leveraged=True, semi_contaminated=True, ledger=ledger,
                          rc_b=rc_b, program_sharpes=program_sharpes)

    def mk4(comm):
        c = 25.0 if isinstance(comm, tuple) else comm
        r = core_satellite(tw2, tr, x3, dates, cash, base_frac=2.0 / 3.0,
                           swap_frac=1.0 / 3.0, off_to_base=True, commission=c)
        return type("X", (), {"net_returns": r["net"]})()
    g4["cost_whatifs"] = cost_whatifs(mk4, bench_net, dates, design_end)
    # 부스트 노출-일당 커미션(bp, 결정론적) — config4(스왑) vs config3(현금슬리브)
    g4["cost_per_boosted_exposure_day_bps"] = boost_cost_per_exposure_day_bps(
        tw2, dates, swap_frac=1.0 / 3.0, off_to_base=True)
    g4["cash_sleeve_cost_per_boost_bps"] = boost_cost_per_exposure_day_bps(
        tw2, dates, swap_frac=0.15, off_to_base=False)
    results["configs"]["c3b_core_boost"] = g4

    # ── config 5: c3b_lcc_prefomc (창의, pre-FOMC 단일일 1/3 TQQQ) ──
    id5 = f"c3b_lcc_prefomc{tag}"
    tw5 = calendar_capture_tw(dates, _fomc_hold())
    s5 = sleeve_stream(tw5, tr, x3, dates, cash)
    mech5 = per_trade_mechanism(tw5, tr, x3, dates)
    g5 = gate_eval_config(id5, {"event": "prefomc", "exec": "tqqq_1_3", "hold": 1},
                          s5.net_returns, bench_net, dates, design_end,
                          is_leveraged=True, semi_contaminated=True, ledger=ledger,
                          rc_b=rc_b, program_sharpes=program_sharpes)
    g5["cost_whatifs"] = cost_whatifs(stream_maker(tw5, "sleeve"), bench_net, dates, design_end)
    g5["mechanism"] = mech5
    g5["tim"] = sum(1 for v in tw5 if v > 0.5) / len(tw5)
    results["configs"]["c3b_lcc_prefomc"] = g5
    return results


# ══════════════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--rc-b", type=int, default=2000)
    ap.add_argument("--out", default=str(ROOT / "reports" / "c3b_results.json"))
    ap.add_argument("--real", action="store_true", help="실물 QQQ/TQQQ 2016+ 확인 동시 실행")
    args = ap.parse_args()

    # 프로그램 전체 N(원장 전체 sr_daily) — v2.1 §3. 실행 전 스냅샷.
    program_sharpes = gate.ledger_trial_sharpes(args.ledger, None)

    stack = build_stack()
    # 합성 검증 지표
    validate = None
    try:
        panel = hd.align_panel(hd.load_panel(["QQQ", "TQQQ", "BIL"], adjusted=True))
        validate = hd.validate_synthetic(panel["QQQ"], panel["TQQQ"], 3.0,
                                         rf_candles=panel["BIL"], rf_kind="price",
                                         annual_expense=TQQQ_EXPENSE,
                                         borrow_spread=TQQQ_SPREAD)
    except Exception as e:  # noqa: BLE001
        validate = {"error": str(e)}

    out = {"meta": {"ledger": args.ledger, "rc_b": args.rc_b,
                    "program_n_at_start": len(program_sharpes),
                    "validate_synthetic": validate}}
    out["fred"] = run_all(stack, DESIGN_END, tag="", ledger=args.ledger,
                          rc_b=args.rc_b, program_sharpes=program_sharpes)

    if args.real:
        real = build_real_stack()
        prog2 = gate.ledger_trial_sharpes(args.ledger, None)
        out["real"] = run_all(real, REAL_DESIGN_END, tag="_real", ledger=args.ledger,
                              rc_b=args.rc_b, program_sharpes=prog2)

    Path(args.out).write_text(json.dumps(out, indent=2, default=str))
    print(f"WROTE {args.out}")
    _print_summary(out)


def _f(v, nd=2):
    return "—" if v is None or (isinstance(v, float) and v != v) else f"{v:.{nd}f}"


def _p(v):
    return "—" if v is None or (isinstance(v, float) and v != v) else f"{v * 100:.1f}%"


def _bp(v):
    return "—" if v is None or (isinstance(v, float) and v != v) else f"{v * 1e4:+.1f}bp"


def _print_summary(out):
    for scope in ("fred", "real"):
        blk = out.get(scope)
        if not blk:
            continue
        print(f"\n{'=' * 70}\n### {scope}  span={blk['span']}  design_end={blk['design_end']}")
        for name, g in blk["configs"].items():
            h = g["holdout"]
            d = g["design"]
            print(f"\n[{name}]  tim={_p(g.get('tim'))}  verdict={g['verdict']}"
                  f"  (semi_contam={g['semi_contaminated']}, RC_thr={g['rc_threshold']})")
            print(f"  design : Sharpe={_f(d.get('sr_annual'))} MDD={_p(d.get('mdd'))} "
                  f"tv/QQQ={_f(d.get('terminal_vs_qqq'))} DSR={_f(d.get('dsr'))}")
            print(f"  holdout: Sharpe={_f(h.get('sr_annual'))} CAGR={_p(h.get('cagr'))} "
                  f"MDD={_p(h.get('mdd'))} Ulcer={_f(h.get('ulcer'))} "
                  f"tv/QQQ={_f(h.get('terminal_vs_qqq'))}")
            print(f"           DSR_idea={_f(h.get('dsr'))} DSR_prog={_f(g.get('program_dsr_holdout'))} "
                  f"RCp={_f(h.get('rc_p'), 3)} SPAp={_f(h.get('spa_p'), 3)} n_trials={h.get('n_trials')}")
            rr = g["relative_risk"]
            print(f"  rel-risk pass={rr['pass']} full_MDD={_p(rr.get('full_mdd_s'))} "
                  f"lev_floor_ok={rr.get('leverage_floor_ok')}  "
                  f"cost_whatif={ {k: _f(v) for k, v in (g.get('cost_whatifs') or {}).items()} }")
            m = g.get("mechanism")
            if m and m.get("n_trades"):
                print(f"  MECH n={m['n_trades']} avg_hold={_f(m['avg_hold_days'],1)}d "
                      f"gross_1x={_bp(m['gross_edge_1x'])} decay_3x={_bp(m['path_decay_3x'])}")
                for lk in ("leg_1x_25", "leg_3x13_25", "leg_1x_10", "leg_3x13_10"):
                    L = m[lk]
                    print(f"    {lk:12s} exp={_bp(L['expectancy'])} t={_f(L['tstat'])} "
                          f"CI=[{_bp(L['ci_lo'])},{_bp(L['ci_hi'])}] rt={_f(L['roundtrip_bps'],1)}bp")
            if g.get("cost_per_boosted_exposure_day_bps") is not None:
                print(f"  cost/boosted-exposure-day={_f(g['cost_per_boosted_exposure_day_bps'],2)}bp"
                      f"  (config3 위성={_f(g.get('cash_sleeve_cost_per_boost_bps'),2)}bp)")


if __name__ == "__main__":
    main()
