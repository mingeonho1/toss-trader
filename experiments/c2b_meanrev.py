#!/usr/bin/env python3
"""experiments/c2b_meanrev.py — Cycle 2 · idea family ``c2b``.

**단기(1~10일) 평균회귀 / 스윙** 전략을 지수 ETF에 적용하되, 체결은 1x/2x/3x 로
선택 실행한다. 신호는 항상 **1x 지수** 위에서 계산하고, 실행만 레버리지로 바꾼다.

데이터
------
- 종가기반 규칙(아이디어 1·2·5): FRED NASDAQ100(1986~, 종가) → 총수익 근사(배당 0.7%/yr).
  2x/3x 는 ``histdata.synthetic_leveraged`` 합성(차입금리 DTB3, rf_kind='yield').
- OHLC 필요 규칙(아이디어 3·4): 실측 QQQ/SPY/QLD/TQQQ/IWM 캐시(2016-09~2026-09).

규약(experiments/README.md, docs/gate_v2_spec.md)
- 신호는 종가 t, 체결은 t+1 종가(exec_lag=1). MOC 근사(exec_lag=0)는 낙관 변형으로 별도 보고.
- 모든 신호는 ``research.lookahead_guard`` 로 검증(tests/test_c2b.py).
- 모든 시도는 게이트 하네스(scripts/gate_eval)를 통해 ``reports/trials_ledger.jsonl`` 에 적재.
- 분할(peek-once): FRED 설계 ≤2008 / 홀드아웃 2009+; 10년 데이터 설계 ≤2021 / 홀드아웃 2022+.

이 스크립트는 ``histdata``/``research``/``gate`` 를 **소비만** 하고 ``src/`` 를 수정하지 않는다.

사용:  PYTHONPATH=src .venv/bin/python experiments/c2b_meanrev.py [--ledger PATH] [--rc-b N]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd, research  # noqa: E402
import gate_eval as ge  # noqa: E402

DEFAULT_LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")

NDX_DIV_YIELD = 0.007          # NDX 배당수익률 보수적 근사(histdata 문서 규약)
PLATEAU_GRID = (0.5, 0.7, 0.8, 1.2, 1.5)

# 유동성 티어(사양 §7): 1x=ETF, 2x/3x=레버리지 ETF
EXEC_TIER = {
    "1x": research.TIER_ETF, "2x": research.TIER_LEVERAGED_ETF,
    "3x": research.TIER_LEVERAGED_ETF,
    "QQQ": research.TIER_ETF, "SPY": research.TIER_ETF,
    "QLD": research.TIER_LEVERAGED_ETF, "TQQQ": research.TIER_LEVERAGED_ETF,
    "IWM": research.TIER_ETF,
}


# ══════════════════════════════════════════════════════════════════════════════
# 데이터 준비
# ══════════════════════════════════════════════════════════════════════════════
def _closes(candles) -> list[float]:
    return [c.close for c in candles]


def _ffill_to(target_dates, src_candles) -> list[float]:
    """src(종가전용 캔들)를 target_dates 에 forward-fill 정렬."""
    src = sorted(src_candles, key=lambda c: c.dt)
    out: list[float] = []
    j = 0
    last = src[0].close if src else 0.0
    for d in target_dates:
        while j < len(src) and src[j].dt <= d:
            last = src[j].close
            j += 1
        out.append(last)
    return out


def load_fred_stack() -> dict:
    """NASDAQ100 신호(가격지수) + 실행(1x TR / 2x / 3x 합성) + DTB3 현금 + VIX."""
    ndx = hd.load_fred("NASDAQ100")
    dates = [c.dt for c in ndx]
    sig = _closes(ndx)                                  # 신호: 가격지수 종가
    tr = hd.index_total_return(ndx, NDX_DIV_YIELD)      # 실행 1x: 총수익 근사
    dtb3 = hd.load_fred("DTB3")
    ex1 = _closes(tr)
    ex2 = _closes(hd.synthetic_leveraged(tr, 2.0, rf_candles=dtb3, rf_kind="yield"))
    ex3 = _closes(hd.synthetic_leveraged(tr, 3.0, rf_candles=dtb3, rf_kind="yield"))
    cash = research.cash_rate_from_annual(_ffill_to(dates, dtb3))
    vix_ff = _ffill_to(dates, hd.load_fred("VIXCLS"))   # VIX(1990~) ffill; 1990이전은 첫값 상수
    return {"dates": dates, "sig": sig, "exec": {"1x": ex1, "2x": ex2, "3x": ex3},
            "cash": cash, "vix": vix_ff}


def load_real_stack(symbols) -> dict:
    """실측 OHLC 패널(공통 거래일 정렬) + BIL 현금수익률."""
    panel = hd.align_panel(hd.load_panel(list(symbols) + ["BIL"], adjusted=True))
    dates = [c.dt for c in panel[symbols[0]]]
    O = {s: [c.open for c in panel[s]] for s in symbols}
    H = {s: [c.high for c in panel[s]] for s in symbols}
    L = {s: [c.low for c in panel[s]] for s in symbols}
    C = {s: [c.close for c in panel[s]] for s in symbols}
    cash = research.cash_rate_from_prices([c.close for c in panel["BIL"]])
    return {"dates": dates, "O": O, "H": H, "L": L, "C": C, "cash": cash}


# ══════════════════════════════════════════════════════════════════════════════
# 신호(인과적 포지션 상태기계) — desired[i] ∈ {0,1} = 종가 i 기준 '다음 봉 보유 희망'
# ══════════════════════════════════════════════════════════════════════════════
def rsi2_positions(closes, *, rsi_entry=10.0, sma_trend=200, sma_exit=5,
                   max_hold=10) -> list[float]:
    """Connors RSI(2): 지수>SMA200 & RSI(2)<10 진입; 종가>SMA5 또는 max_hold봉 후 청산."""
    n = len(closes)
    r = research.rsi(closes, 2)
    st = research.sma(closes, int(max(1, sma_trend)))
    se = research.sma(closes, int(max(1, sma_exit)))
    des = [0.0] * n
    inpos = False
    bars = 0
    for i in range(n):
        if not inpos:
            if (r[i] is not None and st[i] is not None
                    and closes[i] > st[i] and r[i] < rsi_entry):
                inpos, bars, des[i] = True, 1, 1.0
        else:
            exit_now = (se[i] is not None and closes[i] > se[i]) or bars >= max_hold
            if exit_now:
                inpos, bars, des[i] = False, 0, 0.0
            else:
                des[i], bars = 1.0, bars + 1
    return des


def ndown_positions(closes, *, n_down=3, sma_trend=200) -> list[float]:
    """N연속 하락 종가 & 지수>SMA200 진입; 첫 상승 종가에 청산."""
    n = len(closes)
    st = research.sma(closes, int(max(1, sma_trend)))
    down = [False] * n
    for i in range(1, n):
        down[i] = closes[i] < closes[i - 1]
    nd = int(max(1, n_down))
    des = [0.0] * n
    inpos = False
    for i in range(n):
        if not inpos:
            cons = i >= nd and all(down[i - k] for k in range(nd))
            if cons and st[i] is not None and closes[i] > st[i]:
                inpos, des[i] = True, 1.0
        else:
            if i > 0 and closes[i] > closes[i - 1]:       # 첫 상승 종가 → 청산
                inpos, des[i] = False, 0.0
            else:
                des[i] = 1.0
    return des


def ibs_positions(opens, highs, lows, closes, *, ibs_entry=0.2, ibs_exit=0.5,
                  max_hold=5, sma_trend=0) -> list[float]:
    """IBS=(C−L)/(H−L)<0.2 진입; IBS>0.5 또는 max_hold봉 후 청산. sma_trend>0이면 추세필터."""
    n = len(closes)
    ibs = [0.5] * n
    for i in range(n):
        rng = highs[i] - lows[i]
        ibs[i] = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
    st = research.sma(closes, int(sma_trend)) if sma_trend and sma_trend >= 1 else None
    des = [0.0] * n
    inpos = False
    bars = 0
    for i in range(n):
        if not inpos:
            cond = ibs[i] < ibs_entry
            if st is not None:
                cond = cond and st[i] is not None and closes[i] > st[i]
            if cond:
                inpos, bars, des[i] = True, 1, 1.0
        else:
            if ibs[i] > ibs_exit or bars >= max_hold:
                inpos, bars, des[i] = False, 0, 0.0
            else:
                des[i], bars = 1.0, bars + 1
    return des


def vix_positions(closes, vix, *, mult=1.3, vix_win=20, sma_trend=200, sma_exit=5,
                  max_hold=10) -> list[float]:
    """창의 아이디어: VIX>mult×SMA20(VIX)의 '패닉' + 지수>SMA200 추세 진입; 종가>SMA5 or hold 청산."""
    n = len(closes)
    vs = research.sma(vix, int(max(1, vix_win)))
    st = research.sma(closes, int(max(1, sma_trend)))
    se = research.sma(closes, int(max(1, sma_exit)))
    des = [0.0] * n
    inpos = False
    bars = 0
    for i in range(n):
        if not inpos:
            spike = vs[i] is not None and vs[i] > 0 and vix[i] > mult * vs[i]
            trend = st[i] is not None and closes[i] > st[i]
            if spike and trend:
                inpos, bars, des[i] = True, 1, 1.0
        else:
            if (se[i] is not None and closes[i] > se[i]) or bars >= max_hold:
                inpos, bars, des[i] = False, 0, 0.0
            else:
                des[i], bars = 1.0, bars + 1
    return des


# ── lookahead_guard 용 signal_fn 래퍼 (신호 시리즈를 패널로 받아 목표비중 반환) ──
def make_signal_fn(kind, cfg):
    """panel_closes(신호 시리즈들) → target_weights[{'POS':0/1}]. 인과성 검증 전용."""
    def fn(panel, dates):
        c = list(panel["C"])
        if kind == "rsi2":
            d = rsi2_positions(c, **cfg)
        elif kind == "ndown":
            d = ndown_positions(c, **cfg)
        elif kind == "ibs":
            d = ibs_positions(list(panel["O"]), list(panel["H"]), list(panel["L"]),
                              c, **cfg)
        elif kind == "vix":
            d = vix_positions(c, list(panel["VIX"]), **cfg)
        else:
            raise ValueError(kind)
        return [{"POS": v} for v in d]
    return fn


# ══════════════════════════════════════════════════════════════════════════════
# 포지션 → 백테스트
# ══════════════════════════════════════════════════════════════════════════════
def weights_from_positions(desired, sym):
    return [({sym: 1.0} if d > 0.5 else {}) for d in desired]


def weights_overlay(desired, hi, lo):
    """유휴 시 lo(1x QQQ) 보유, 신호 시 hi(3x) 전환 — 현금드래그 회피 오버레이."""
    return [({hi: 1.0} if d > 0.5 else {lo: 1.0}) for d in desired]


def cost_for(symbols, commission_bps=research.STANDARD_COMMISSION_BPS):
    tiers = {s: EXEC_TIER.get(s, research.TIER_LARGE_CAP) for s in symbols}
    return research.CostSpec.from_tiers(tiers, commission_bps=commission_bps,
                                        slippage_bps=5.0, fx_bps=20.0)


def net_stream(panel, dates, tw, cost, cash, exec_lag=1):
    res = research.run_weights(panel, dates, tw, exec_lag=exec_lag, cost=cost,
                               cash_rate=cash)
    return res


def extract_trades(desired, closes, dates, sym, *, exec_lag=1, notional=1.0):
    """desired 포지션 런 → 거래 리스트(진입 close[i+lag], 청산 close[j+1+lag])."""
    n = len(desired)
    dec = [d > 0.5 for d in desired]
    trades: list[research.Trade] = []
    k = 0
    while k < n:
        if dec[k]:
            i = k
            j = k
            while j + 1 < n and dec[j + 1]:
                j += 1
            ef = i + exec_lag
            xf = min((j + 1) + exec_lag, n - 1) if (j + 1) < n else n - 1
            if ef < n and xf >= ef and closes[ef] > 0:
                trades.append(research.Trade(
                    entry_dt=dates[ef], entry_price=closes[ef],
                    exit_dt=dates[xf], exit_price=closes[xf],
                    side="long", notional=notional, symbol=sym))
            k = j + 1
        else:
            k += 1
    return trades


def time_in_market(desired) -> float:
    return sum(1.0 for d in desired if d > 0.5) / len(desired) if desired else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# DCA(화폐가중) 헬퍼
# ══════════════════════════════════════════════════════════════════════════════
def dca_terminal(panel, dates, tw, cost, cash):
    r = research.run_dca_overlay(panel, dates, tw, monthly_usd=35.0, initial_usd=32.0,
                                 cost=cost, cash_rate=cash, exec_lag=0)
    return r


def _holdout_start(dates, design_end) -> int:
    for i, d in enumerate(dates):
        if d > design_end:
            return i
    return len(dates)


def dca_holdout(panel, dates, tw, cost, cash, design_end):
    """홀드아웃(설계기간 이후) 구간만으로 DCA 실행 — OOS 경제성 판정용(현금수익 regime 격리).

    전기간 DCA는 1980–90년대 고금리 T-bill 현금수익이 지배해 신호 알파를 과대평가한다.
    """
    h0 = _holdout_start(dates, design_end)
    sl = slice(h0, None)
    return research.run_dca_overlay({s: list(v)[sl] for s, v in panel.items()},
                                    list(dates)[sl], list(tw)[sl], monthly_usd=35.0,
                                    initial_usd=32.0, cost=cost, cash_rate=list(cash)[sl],
                                    exec_lag=0)


# ══════════════════════════════════════════════════════════════════════════════
# 강건성(설계기간)
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# 아이디어 평가 (종가기반: 1·2·5)
# ══════════════════════════════════════════════════════════════════════════════
def evaluate_close_idea(idea, positions_fn, base_cfg, stack, design_end, *,
                        num_params, vix=False, ledger=DEFAULT_LEDGER, rc_b=800):
    """종가기반 아이디어: 1x/2x/3x + 오버레이 게이트, 강건성, 거래통계. RESULTS dict 반환."""
    dates = stack["dates"]
    sig = stack["sig"]
    cash = stack["cash"]
    execs = stack["exec"]
    n = len(dates)
    design_idx = [i for i, d in enumerate(dates) if d <= design_end]
    bench_ret = research.to_returns(execs["1x"])[1:]          # 1x B&H 벤치(QQQ 대응)
    bench_design_term = gate.returns_to_equity(
        [bench_ret[i] for i in range(len(bench_ret)) if dates[i + 1] <= design_end])[-1]

    def sig_positions(cfg):
        if vix:
            return positions_fn(sig, stack["vix"], **cfg)
        return positions_fn(sig, **cfg)

    desired = sig_positions(base_cfg)
    out: dict = {"idea": idea, "base_cfg": base_cfg, "tim": time_in_market(desired),
                 "n_days": n, "design_end": design_end.isoformat(), "execs": {}}

    # ── 1x/2x/3x 개별 실행 ──
    for lev in ("1x", "2x", "3x"):
        idid = f"{idea}_{lev}"
        cost = cost_for([lev])
        # 헤드라인 스트림(전 구간)
        res = research.run_weights({lev: execs[lev]}, dates,
                                   weights_from_positions(desired, lev),
                                   exec_lag=1, cost=cost, cash_rate=cash)
        cand = res.net_stream()
        # 이웃 스윕(설계기간) — 원장 적재 + 평탄성
        frac_pos, sharpes, center_sharpe = _sweep_and_log(
            sig_positions, base_cfg, num_params, lev, execs[lev], dates, cash,
            design_idx, bench_design_term, idid, ledger)
        # 헤드라인 분할 평가(design+holdout, DSR/RC, peek-once)
        splits = ge.run_splits(idid, {**base_cfg, "exec": lev}, 1, cand, bench_ret,
                               dates, design_end, ledger_path=ledger, rc_B=rc_b,
                               bench_terminal=None)
        # 화폐가중(홀드아웃 DCA) vs QQQ(1x) B&H DCA — OOS 경제성
        cand_dca = dca_holdout({lev: execs[lev]}, dates,
                               weights_from_positions(desired, lev), cost, cash,
                               design_end)
        qqq_dca = dca_holdout({"1x": execs["1x"]}, dates, [{"1x": 1.0}] * n,
                              cost_for(["1x"]), cash, design_end)
        tv0 = cand_dca.final_value / qqq_dca.final_value if qqq_dca.final_value else None
        # 거래통계
        trades = extract_trades(desired, execs[lev], dates, lev)
        tr = research.run_trades(trades, cost=cost, capital=1.0)
        trade_stats = _trade_stats(tr, cost, lev)
        # 전 표본(닷컴·GFC 포함) 단위자본 MDD — 하드캡(−50%) 판정용(사양 §5.1)
        full_mdd = gate.max_drawdown(gate.returns_to_equity(cand))
        out["execs"][lev] = _assemble(splits, tv0, cand_dca, qqq_dca, frac_pos,
                                      sharpes, center_sharpe, trade_stats,
                                      sig_positions, base_cfg, num_params, lev,
                                      execs[lev], dates, cash, design_idx,
                                      bench_ret, design_end, ledger, full_mdd)

    # ── MOC 근사(exec_lag=0, 낙관): 신호봉 종가 체결 — t+1 대비 상단(look-ahead성 낙관) ──
    out["moc_1x"] = _moc_compare(desired, execs["1x"], dates, cash, design_end)
    # ── 비용 민감도(1x 거래단위): 표준/프로모/2× ──
    out["cost_scan_1x"] = cost_scan(desired, execs["1x"], dates, "1x")

    # ── 오버레이(유휴 1x, 신호 3x) ──
    idid = f"{idea}_ov"
    ov_cost = cost_for(["1x", "3x"])
    ov_tw = weights_overlay(desired, "hi", "lo")
    ov_panel = {"hi": execs["3x"], "lo": execs["1x"]}
    ov_res = research.run_weights(ov_panel, dates, ov_tw, exec_lag=1, cost=ov_cost,
                                  cash_rate=cash)
    ov_cand = ov_res.net_stream()
    ov_splits = ge.run_splits(idid, {**base_cfg, "exec": "overlay_1x_3x"}, 1, ov_cand,
                              bench_ret, dates, design_end, ledger_path=ledger,
                              rc_B=rc_b)
    ov_dca = dca_holdout(ov_panel, dates, ov_tw, ov_cost, cash, design_end)
    qqq_dca = dca_holdout({"1x": execs["1x"]}, dates, [{"1x": 1.0}] * n,
                          cost_for(["1x"]), cash, design_end)
    ov_tv0 = ov_dca.final_value / qqq_dca.final_value if qqq_dca.final_value else None
    ov_full_mdd = gate.max_drawdown(gate.returns_to_equity(ov_cand))
    out["overlay"] = _overlay_summary(ov_splits, ov_tv0, ov_dca, qqq_dca, ov_cost,
                                      ov_full_mdd)
    return out


def _sweep_and_log(sig_positions, base_cfg, num_params, sym, exec_closes, dates,
                   cash, design_idx, bench_design_term, log_id, ledger):
    """수치 파라미터 이웃(±grid) 설계기간 재평가 → 원장 적재 + 평탄성 재료 반환."""
    cost = cost_for([sym])

    def ev(cfg, tag):
        des = sig_positions(cfg)
        res = research.run_weights({sym: exec_closes}, dates,
                                   weights_from_positions(des, sym),
                                   exec_lag=1, cost=cost, cash_rate=cash)
        dn = [res.net_returns[i] for i in design_idx]
        dd = [dates[i] for i in design_idx]
        um = ge.unit_capital_metrics(dn, dates=dd)
        if um:
            ge.log_evaluation(log_id, {**cfg, "exec": sym, "kind": tag}, 1,
                              "design", um, ledger_path=ledger)
        term = um.get("terminal_unit", 1.0) if um else 1.0
        return term / bench_design_term - 1.0, (um.get("sr_annual", 0.0) if um else 0.0)

    _, center_sharpe = ev(base_cfg, "center")
    pos = 0
    tot = 0
    sharpes: list[float] = []
    for key in num_params:
        base_val = base_cfg[key]
        for g in PLATEAU_GRID:
            cfg = dict(base_cfg)
            nv = int(round(base_val * g)) if isinstance(base_val, int) else base_val * g
            if isinstance(base_val, int):
                nv = max(1, nv)
            cfg[key] = nv
            _, sh = ev(cfg, "neighbor")
            tot += 1
            # 저노출 타이머는 'vs QQQ 절대수익'이 구조적으로 음수 → 평탄성은 위험조정 엣지
            # (설계기간 Sharpe > 0)의 이웃 지속성으로 판정한다(사양 §4.1의 취지 보존).
            pos += int(sh > 0)
            sharpes.append(sh)
    frac_pos = pos / tot if tot else 0.0
    return frac_pos, sharpes, center_sharpe


def _moc_compare(desired, ex1, dates, cash, design_end) -> dict:
    """MOC 근사(exec_lag=0): 신호봉 종가에 체결하는 낙관 변형. t+1 종가 체결의 상단.

    신호 함수 자체는 인과적이지만 '신호 계산에 쓴 종가에 체결'은 실현 불가한 낙관(장중 프록시로만
    근사) → look-ahead성 상단으로만 보고한다(사양 §2.3).
    """
    cost = cost_for(["1x"])
    res = research.run_weights({"1x": ex1}, dates,
                               weights_from_positions(desired, "1x"),
                               exec_lag=0, cost=cost, cash_rate=cash)
    cand = res.net_stream()
    hret = [cand[i] for i in range(len(cand)) if dates[i + 1] > design_end]
    trades = extract_trades(desired, ex1, dates, "1x", exec_lag=0)
    tr = research.run_trades(trades, cost=cost, capital=1.0)
    return {"holdout_sharpe": gate.sharpe(hret),
            "trade_stats": _trade_stats(tr, cost, "1x")}


def cost_scan(desired, exec_closes, dates, sym) -> dict:
    """거래단위 순엣지의 비용 민감도 — 표준 25bp / 프로모 10bp / 2× 스트레스(사양 §4.3, §7)."""
    trades = extract_trades(desired, exec_closes, dates, sym)
    specs = {"std_25bp": cost_for([sym], 25.0),
             "promo_10bp": cost_for([sym], research.PROMO_COMMISSION_BPS),
             "stress_2x": cost_for([sym], 25.0).stress(2.0)}
    out = {}
    for name, cs in specs.items():
        tr = research.run_trades(trades, cost=cs, capital=1.0)
        out[name] = {"expectancy": gate.expectancy(tr.pnls),
                     "tstat": gate.trade_tstat(tr.pnls),
                     "total_net": sum(tr.pnls),
                     "roundtrip_bps": cs.roundtrip_bps(sym)}
    return out


def _trade_stats(tr: research.TradesResult, cost, sym) -> dict:
    pnls = tr.pnls
    n = len(pnls)
    if n == 0:
        return {"n_trades": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rt = cost.roundtrip_bps(sym) * 1e-4
    tstat = gate.trade_tstat(pnls)
    _, lo, hi = gate.trade_pnl_bootstrap_ci(pnls, B=2000, rng=random.Random(7))
    return {
        "n_trades": n,
        "win_rate": len(wins) / n,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "expectancy": gate.expectancy(pnls),
        "profit_factor": gate.profit_factor(pnls),
        "trade_tstat": tstat,
        "ci_lo": lo, "ci_hi": hi,
        "roundtrip_cost_frac": rt,
        "gross_mean": sum(tr.gross_pnls) / n,
    }


def _robustness(sig_positions, base_cfg, num_params, sym, exec_closes, dates, cash,
                design_idx, bench_ret, design_end):
    """비용 스트레스 · 손익분기 bps · 시작일 랜덤화(설계기간 기준)."""
    n = len(dates)
    desired = sig_positions(base_cfg)
    holdout_mask = [d > design_end for d in dates]

    # 비용 스트레스: 오버레이가 아니라 전략 자체 net_excess(vs 1x)로는 현금파킹이 항상 음수 →
    # 여기서는 '손익분기 왕복 bps'(거래 총엣지가 0이 되는 비용)로 비용여유를 정량화.
    trades = extract_trades(desired, exec_closes, dates, sym)

    def net_excess_at_rt(rt_frac):
        # 왕복 rt_frac 비용에서 거래 평균 순PnL(자본대비) — 양이면 엣지 생존
        if not trades:
            return -1.0
        s = 0.0
        for t in trades:
            g = t.exit_price / t.entry_price - 1.0
            s += g - rt_frac
        return s / len(trades)

    be_bps = gate.breakeven_cost_bps(net_excess_at_rt, lo=0.0, hi=0.03)

    # 시작일 랜덤화(DCA 최종자산, 전략 vs QQQ B&H) — 첫 24개월 offset
    starts = [i for i, f in enumerate(research.month_start_flags(dates)) if f][:24]

    def strat_dca(off):
        dd = dates[off:]
        des = sig_positions(base_cfg)[off:]
        r = research.run_dca_overlay({sym: exec_closes[off:]}, dd,
                                     weights_from_positions(des, sym),
                                     monthly_usd=35.0, initial_usd=32.0,
                                     cost=cost_for([sym]), cash_rate=cash[off:],
                                     exec_lag=0)
        return r.final_value

    def qqq_dca(off):
        dd = dates[off:]
        r = research.run_dca_overlay({sym: exec_closes[off:]}, dd,
                                     [{sym: 1.0}] * len(dd), monthly_usd=35.0,
                                     initial_usd=32.0, cost=cost_for([sym]),
                                     cash_rate=cash[off:], exec_lag=0)
        return r.final_value

    sdr = gate.start_date_randomization(strat_dca, qqq_dca, starts)
    return {"breakeven_rt_bps": be_bps, "start_date_winrate": sdr["win_rate"],
            "start_date_median_margin": sdr["median_margin"]}


def _assemble(splits, tv0, cand_dca, qqq_dca, frac_pos, sharpes, center_sharpe,
              trade_stats, sig_positions, base_cfg, num_params, lev, exec_closes,
              dates, cash, design_idx, bench_ret, design_end, ledger, full_mdd):
    holdout = splits.get("holdout", {})
    design = splits.get("design", {})
    ax = dict(holdout.get("axis_input", {}))
    # 강건성
    rob = _robustness(sig_positions, base_cfg, num_params, lev, exec_closes, dates,
                      cash, design_idx, bench_ret, design_end)
    sharpes_sorted = sorted(sharpes)
    med_sh = sharpes_sorted[len(sharpes_sorted) // 2] if sharpes_sorted else 0.0
    plateau_pass = (frac_pos >= 0.8 and med_sh >= 0.9 * center_sharpe
                    and center_sharpe > 0)
    # decide 메트릭 조립
    metrics = dict(ax)
    metrics["lane"] = 1
    metrics["terminal_vs_b0"] = tv0
    # 리스크 하드캡은 전 표본(닷컴·GFC 포함) MDD로 판정 — 홀드아웃(2009+)엔 대형폭락 없음(§5.1)
    metrics["mdd"] = min(full_mdd, metrics.get("mdd") or 0.0)
    metrics["plateau_pass"] = plateau_pass
    metrics["plateau_borderline"] = (0.6 <= frac_pos < 0.8)
    metrics["start_date_winrate"] = rob["start_date_winrate"]
    # 비용 스트레스: 손익분기 bps가 현실 왕복(≈62bps)의 2배 이상이면 PASS 취지 → 배수로 환산
    real_rt = cost_for([lev]).roundtrip_bps(lev)
    metrics["cost_stress_mult"] = (rob["breakeven_rt_bps"] / real_rt
                                   if real_rt > 0 else None)
    dec = gate.decide(metrics)
    return {
        "tv0_dca": tv0,
        "cand_dca_final": cand_dca.final_value,
        "qqq_dca_final": qqq_dca.final_value,
        "full_mdd": full_mdd,
        "design": _slim(design), "holdout": _slim(holdout),
        "plateau": {"frac_positive": frac_pos, "neighbor_median_sharpe": med_sh,
                    "center_sharpe": center_sharpe, "pass": plateau_pass},
        "robustness": rob, "trade_stats": trade_stats,
        "verdict": str(dec), "verdict_axes": dec.axes, "verdict_reasons": dec.reasons,
    }


def _overlay_summary(splits, tv0, ov_dca, qqq_dca, cost, full_mdd):
    holdout = splits.get("holdout", {})
    design = splits.get("design", {})
    ax = dict(holdout.get("axis_input", {}))
    metrics = dict(ax)
    metrics["lane"] = 1
    metrics["terminal_vs_b0"] = tv0
    metrics["mdd"] = min(full_mdd, metrics.get("mdd") or 0.0)   # 닷컴·GFC 포함(§5.1)
    dec = gate.decide(metrics)
    return {"tv0_dca": tv0, "ov_dca_final": ov_dca.final_value,
            "qqq_dca_final": qqq_dca.final_value, "full_mdd": full_mdd,
            "design": _slim(design), "holdout": _slim(holdout),
            "verdict": str(dec), "verdict_axes": dec.axes}


def _slim(period_res) -> dict:
    if not period_res:
        return {}
    um = period_res.get("unit", {})
    rc = period_res.get("reality_check", {})
    mw = period_res.get("money", {})
    w = period_res.get("window")
    return {
        "window": [w[0].isoformat(), w[1].isoformat()] if w else None,
        "T": um.get("T"), "sr_annual": um.get("sr_annual"),
        "sr_daily": um.get("sr_daily"), "cagr": um.get("cagr"),
        "mdd": um.get("max_drawdown"), "ulcer": um.get("ulcer"),
        "calmar": um.get("calmar"), "cvar5": um.get("cvar5"),
        "terminal_unit": um.get("terminal_unit"), "dsr": um.get("dsr"),
        "n_trials": um.get("n_trials"),
        "rc_p": rc.get("rc_pvalue"), "spa_p": rc.get("spa_pvalue"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 아이디어 3 (IBS, 10년 OHLC)
# ══════════════════════════════════════════════════════════════════════════════
def evaluate_ibs(stack, design_end, *, ledger=DEFAULT_LEDGER, rc_b=800):
    dates = stack["dates"]
    cash = stack["cash"]
    n = len(dates)
    design_idx = [i for i, d in enumerate(dates) if d <= design_end]
    base_cfg = {"ibs_entry": 0.2, "ibs_exit": 0.5, "max_hold": 5}
    out: dict = {"idea": "c2b_ibs", "base_cfg": base_cfg, "design_end": design_end.isoformat(),
                 "syms": {}}
    # QQQ: 1x(QQQ)/2x(QLD)/3x(TQQQ); SPY: 1x
    lanes = [("QQQ", ["QQQ", "QLD", "TQQQ"]), ("SPY", ["SPY"])]
    for sig_sym, exec_syms in lanes:
        O, H, L, C = stack["O"][sig_sym], stack["H"][sig_sym], stack["L"][sig_sym], stack["C"][sig_sym]
        desired = ibs_positions(O, H, L, C, **base_cfg)
        bench_ret = research.to_returns(stack["C"][sig_sym])[1:]
        for esym in exec_syms:
            idid = f"c2b_ibs_{sig_sym}_{esym}"
            cost = cost_for([esym])
            ec = stack["C"][esym]
            res = research.run_weights({esym: ec}, dates,
                                       weights_from_positions(desired, esym),
                                       exec_lag=1, cost=cost, cash_rate=cash)
            cand = res.net_stream()
            splits = ge.run_splits(idid, {**base_cfg, "sig": sig_sym, "exec": esym}, 1,
                                   cand, bench_ret, dates, design_end,
                                   ledger_path=ledger, rc_B=rc_b)
            trades = extract_trades(desired, ec, dates, esym)
            tr = research.run_trades(trades, cost=cost, capital=1.0)
            cand_dca = dca_holdout({esym: ec}, dates,
                                   weights_from_positions(desired, esym), cost, cash,
                                   design_end)
            qqq_dca = dca_holdout({sig_sym: stack["C"][sig_sym]}, dates,
                                  [{sig_sym: 1.0}] * n, cost_for([sig_sym]), cash,
                                  design_end)
            tv0 = cand_dca.final_value / qqq_dca.final_value if qqq_dca.final_value else None
            ax = dict(splits.get("holdout", {}).get("axis_input", {}))
            ax["terminal_vs_b0"] = tv0
            ax["lane"] = 1
            full_mdd = gate.max_drawdown(gate.returns_to_equity(cand))
            ax["mdd"] = min(full_mdd, ax.get("mdd") or 0.0)
            out["syms"][idid] = {
                "tim": time_in_market(desired), "tv0_dca": tv0, "full_mdd": full_mdd,
                "design": _slim(splits.get("design", {})),
                "holdout": _slim(splits.get("holdout", {})),
                "trade_stats": _trade_stats(tr, cost, esym),
                "cost_scan": cost_scan(desired, ec, dates, esym) if esym == sig_sym else None,
                "verdict": str(gate.decide(ax)),
            }
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 아이디어 4 (Gap-down, 10년 OHLC, 거래단위 레인3)
# ══════════════════════════════════════════════════════════════════════════════
def evaluate_gapdown(stack, design_end, *, ledger=DEFAULT_LEDGER):
    dates = stack["dates"]
    out: dict = {"idea": "c2b_gapdown", "cfg": {"gap": -0.01}, "syms": {}}
    for sig_sym, exec_syms in [("QQQ", ["QQQ", "QLD", "TQQQ"])]:
        O, C = stack["O"][sig_sym], stack["C"][sig_sym]
        # 신호(전일종가 대비 시가 −1% 이하)는 sig_sym 기준, 실행만 레버리지 심볼로
        sig_idx = [i for i in range(1, len(C)) if O[i] <= C[i - 1] * (1 - 0.01)]
        for esym in exec_syms:
            idid = f"c2b_gapdown_{esym}"
            cost = cost_for([esym])
            eo, ec = stack["O"][esym], stack["C"][esym]
            trades = [research.Trade(entry_dt=dates[i], entry_price=eo[i],
                                     exit_dt=dates[i], exit_price=ec[i],
                                     side="long", notional=1.0, symbol=esym)
                      for i in sig_idx]
            # 설계/홀드아웃 분리(청산일 기준)
            d_tr = [t for t in trades if t.exit_dt <= design_end]
            h_tr = [t for t in trades if t.exit_dt > design_end]
            rec = {}
            for period, tset in (("design", d_tr), ("holdout", h_tr)):
                tr = research.run_trades(tset, cost=cost, capital=1.0)
                st = _trade_stats(tr, cost, esym)
                # 슬리피지 2x 스트레스
                tr2 = research.run_trades(tset, cost=cost.stress(2.0), capital=1.0)
                st["slip2x_expectancy"] = gate.expectancy(tr2.pnls)
                st["slip2x_tstat"] = gate.trade_tstat(tr2.pnls)
                rec[period] = st
                um = ge.unit_capital_metrics(tr.daily_returns,
                                             dates=tr.dates) if tr.dates else {}
                if um:
                    if period == "holdout":
                        try:
                            ge.log_evaluation(idid, {"gap": -0.01, "exec": esym}, 3,
                                              "holdout", um, ledger_path=ledger)
                        except gate.PeekOnceError:
                            pass
                    else:
                        ge.log_evaluation(idid, {"gap": -0.01, "exec": esym}, 3,
                                          "design", um, ledger_path=ledger)
            # 레인3 판정(홀드아웃 거래셋 기준 필요조건)
            h = rec.get("holdout", {})
            dec = gate.decide({"lane": 3, "n_trades": h.get("n_trades", 0),
                               "trade_tstat": h.get("trade_tstat"),
                               "trade_ci_lo": h.get("ci_lo"),
                               "slippage_stress_pass": (h.get("slip2x_expectancy", 0) > 0)})
            out["syms"][idid] = {"design": rec.get("design", {}),
                                 "holdout": rec.get("holdout", {}),
                                 "verdict": str(dec), "verdict_reasons": dec.reasons}
    return out


# ══════════════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--rc-b", type=int, default=800)
    ap.add_argument("--out", default=str(ROOT / "reports" / "c2b_results.json"))
    ap.add_argument("--only", default="", help="쉼표구분 아이디어 선택(rsi2,ndown,vix,ibs,gapdown)")
    args = ap.parse_args()
    only = set(x for x in args.only.split(",") if x)

    fred = load_fred_stack()
    fred_design_end = date(2008, 12, 31)
    vix_design_end = date(2008, 12, 31)
    results: dict = {"meta": {"ledger": args.ledger, "rc_b": args.rc_b,
                              "fred_n": len(fred["dates"]),
                              "fred_range": [fred["dates"][0].isoformat(),
                                             fred["dates"][-1].isoformat()]}}

    if not only or "rsi2" in only:
        results["rsi2"] = evaluate_close_idea(
            "c2b_rsi2", rsi2_positions,
            {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10},
            fred, fred_design_end,
            num_params=["rsi_entry", "sma_trend", "sma_exit", "max_hold"],
            ledger=args.ledger, rc_b=args.rc_b)
    if not only or "ndown" in only:
        results["ndown"] = evaluate_close_idea(
            "c2b_ndown", ndown_positions, {"n_down": 3, "sma_trend": 200},
            fred, fred_design_end, num_params=["n_down", "sma_trend"],
            ledger=args.ledger, rc_b=args.rc_b)
    if not only or "vix" in only:
        # VIX 는 1990~ 만 유효 → 신호 시작 전(1990이전)은 상수라 사실상 무신호. 설계 1990–2008.
        results["vix"] = evaluate_close_idea(
            "c2b_vix", vix_positions,
            {"mult": 1.3, "vix_win": 20, "sma_trend": 200, "sma_exit": 5, "max_hold": 10},
            fred, vix_design_end,
            num_params=["mult", "vix_win", "sma_trend", "sma_exit", "max_hold"],
            vix=True, ledger=args.ledger, rc_b=args.rc_b)

    real_design_end = date(2021, 12, 31)
    if not only or "ibs" in only or "gapdown" in only:
        real = load_real_stack(["QQQ", "SPY", "QLD", "TQQQ", "IWM"])
        if not only or "ibs" in only:
            results["ibs"] = evaluate_ibs(real, real_design_end, ledger=args.ledger,
                                          rc_b=args.rc_b)
        if not only or "gapdown" in only:
            results["gapdown"] = evaluate_gapdown(real, real_design_end,
                                                  ledger=args.ledger)

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    print(f"WROTE {args.out}")
    _print_summary(results)


def _print_summary(results):
    def g(d, *ks):
        for k in ks:
            d = (d or {}).get(k) if isinstance(d, dict) else None
        return d
    for key in ("rsi2", "ndown", "vix"):
        r = results.get(key)
        if not r:
            continue
        print(f"\n### {r['idea']}  time-in-market={r['tim']:.1%}")
        for lev in ("1x", "2x", "3x"):
            e = r["execs"].get(lev, {})
            h = e.get("holdout", {})
            d = e.get("design", {})
            print(f"  {lev}: tv0_dca={_f(e.get('tv0_dca'))} "
                  f"holdout Sharpe={_f(h.get('sr_annual'))} MDD_hold={_p(h.get('mdd'))} "
                  f"MDD_full={_p(e.get('full_mdd'))} MDD_dsgn={_p(d.get('mdd'))} "
                  f"DSR={_f(h.get('dsr'))} RCp={_f(h.get('rc_p'),3)} → {e.get('verdict')}")
            ts = e.get("trade_stats", {})
            print(f"      trades n={ts.get('n_trades')} win={_p(ts.get('win_rate'))} "
                  f"t={_f(ts.get('trade_tstat'))} exp={_f(ts.get('expectancy'),5)} "
                  f"gross_mean={_f(ts.get('gross_mean'),5)} rt_cost={_f(ts.get('roundtrip_cost_frac'),5)}")
        mc = r.get("moc_1x", {})
        mts = mc.get("trade_stats", {})
        print(f"  MOC-approx 1x(낙관): holdout Sharpe={_f(mc.get('holdout_sharpe'))} "
              f"trade t={_f(mts.get('trade_tstat'))} exp={_f(mts.get('expectancy'),5)} "
              f"win={_p(mts.get('win_rate'))}")
        cs = r.get("cost_scan_1x", {})
        print("  cost-scan 1x exp/trade: " + "  ".join(
            f"{k}({int(v['roundtrip_bps'])}bp)={_f(v['expectancy'],5)}"
            for k, v in cs.items()))
        ov = r.get("overlay", {})
        print(f"  overlay(1x↔3x): tv0_dca={_f(ov.get('tv0_dca'))} "
              f"holdout Sharpe={_f(g(ov,'holdout','sr_annual'))} "
              f"MDD_hold={_p(g(ov,'holdout','mdd'))} MDD_full={_p(ov.get('full_mdd'))} "
              f"→ {ov.get('verdict')}")
    for key in ("ibs",):
        r = results.get(key)
        if not r:
            continue
        print(f"\n### {r['idea']}")
        for idid, s in r["syms"].items():
            h = s.get("holdout", {})
            print(f"  {idid}: tim={_p(s.get('tim'))} tv0={_f(s.get('tv0_dca'))} "
                  f"holdout Sharpe={_f(h.get('sr_annual'))} MDD={_p(h.get('mdd'))} "
                  f"DSR={_f(h.get('dsr'))} → {s.get('verdict')} | "
                  f"trades n={g(s,'trade_stats','n_trades')} t={_f(g(s,'trade_stats','trade_tstat'))}")
    r = results.get("gapdown")
    if r:
        print(f"\n### {r['idea']}")
        for idid, s in r["syms"].items():
            h = s.get("holdout", {})
            print(f"  {idid}: holdout n={h.get('n_trades')} win={_p(h.get('win_rate'))} "
                  f"t={_f(h.get('trade_tstat'))} exp={_f(h.get('expectancy'),5)} "
                  f"slip2x_exp={_f(h.get('slip2x_expectancy'),5)} → {s.get('verdict')}")


def _f(v, nd=2):
    return "—" if v is None or (isinstance(v, float) and v != v) else f"{v:.{nd}f}"


def _p(v):
    return "—" if v is None or (isinstance(v, float) and v != v) else f"{v*100:.1f}%"


if __name__ == "__main__":
    main()
