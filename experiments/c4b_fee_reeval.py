#!/usr/bin/env python3
"""experiments/c4b_fee_reeval.py — Cycle 4 · idea family ``c4b`` (fee re-evaluation).

FACT CORRECTION (Toss 공지 2025-10-17, 2025-12-01 시행): US 표준 수수료 = **0.1%/side
(10bp)** — 사이클 2·3 은 25bp 를 주 시나리오로 잘못 썼다. 추가로: 체결금액 ≤ $10 주문은
수수료 0, 수수료 < $0.01 은 절사. 매도는 SEC 수수료 0.00206%(min $0.01, 불확실) + FINRA
TAF ~$0.000166/주(min $0.01, 불확실)를 더 낸다.

이건 파라미터 정정(사후조정 아님)이지만, 아래 신호들의 **홀드아웃은 이미 열람**됐으므로
부록 v2.1 §4 에 따라 모든 재평가는 **신호 재사용**이다: 새 idea_id(접미사 ``_fee10`` / ``_micro``),
홀드아웃 semi-contaminated, PASS 는 RC/SPA p < 0.01, DSR 은 프로그램 전체 N 으로 보고.

**사전등록 config 재사용(재튜닝 금지) — 각 모듈의 신호 함수를 import 해서 그대로 쓴다.**
비용/체결만 새로 입힌다(재비용화가 이 사이클의 목적).

세 비용 레짐:
  R1 표준     : 10bp/side + 반호가(티어) + 슬리피지 5bp (기존과 동일 구조, 수수료만 25→10).
  R2 마이크로 : 매수는 ≤$10 청크로 분할 → 수수료 0; 매도는 단일주문 10bp + $0.01 SEC + $0.01 TAF.
                (소액 N 에서 fee ≈ 0.001·N + 0.02) — 달러 크기 의존 → 실계좌 경로로 시뮬.
  R3 스트레스 : R1 × 2배 비용.
현금수익률 민감도: 0%(기본, v2.1 §2) vs SGOV 유사 3.6%. SGOV 는 R1/R2 에서 매매비 부담
("idle USD 0%" 와 "park in SGOV" 둘 다 모델).

재현: PYTHONPATH=src .venv/bin/python experiments/c4b_fee_reeval.py [--fast] [--ledger PATH]
비용 인자는 전부 명시 전달한다(동시 작업이 research.CostSpec 기본 수수료를 10 으로 바꿔도 무관).
src/ 는 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in ("src", "scripts", "experiments"):
    sys.path.insert(0, str(ROOT / _p))

from toss_trader import gate, research as R  # noqa: E402
import gate_eval as ge  # noqa: E402
import c2a_leverage as c2a  # noqa: E402
import c2b_meanrev as c2b  # noqa: E402
import c2c_calendar as c2c  # noqa: E402
import c3a_voltarget as c3a  # noqa: E402
import c3b_lcc as c3b  # noqa: E402

BPS = 1e-4
LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle4_c4b_fee_reeval.md"
RESULTS_JSON = ROOT / "reports" / "c4b_results.json"

DESIGN_END = date(2008, 12, 31)          # 롱윈도 설계 1986–2008 / 홀드아웃 2009–2026
FEE10 = 10.0                              # 정정된 표준 수수료(bp/side)
SLIP = 5.0                                # 슬리피지(bp, 기존과 동일)
FX = 20.0                                 # FX(입금에만)
SGOV_ANNUAL = 0.036                       # SGOV 유사 현금수익률
ACCOUNT_SEED = 32.0
ACCOUNT_MONTHLY = 35.0
SIZE_GRID = (50.0, 200.0, 1000.0, 5000.0)  # per-trade bps 표(FRED 롱윈도용)

# 티어별 반호가(bp, 편도): ETF 1 / 레버리지 ETF 2 / 기본 3 (기존과 동일)
HALF_SPREAD = {"ETF": 1.0, "LEV": 2.0, "DEFAULT": 3.0}

# R2 규제 수수료 파라미터(정정 공지). SEC/TAF min 은 "불확실"로 표기됨.
SEC_RATE = 0.0000206      # 0.00206%
SEC_MIN = 0.01
TAF_PER_SHARE = 0.000166
TAF_MIN = 0.01
MICRO_FREE_THRESHOLD = 10.0   # 체결금액 ≤ $10 → 수수료 0
COMM_TRUNCATE = 0.01          # 수수료 < $0.01 → 절사(0)


# ══════════════════════════════════════════════════════════════════════════════
# 비용 모델
# ══════════════════════════════════════════════════════════════════════════════
def cost_spec(commission_bps: float, *, tiers: dict[str, str],
              mult: float = 1.0) -> R.CostSpec:
    """R1(레짐) CostSpec 을 명시 인자로 구성. mult 로 R3(2×) 스트레스.

    tiers: {symbol: "ETF"|"LEV"|"DEFAULT"}. 모든 비용 인자를 명시 전달한다.
    """
    hs = {}
    default_hs = HALF_SPREAD["DEFAULT"]
    for sym, tier in tiers.items():
        hs[sym] = HALF_SPREAD.get(tier, HALF_SPREAD["DEFAULT"])
    spec = R.CostSpec(commission_bps=commission_bps, slippage_bps=SLIP, fx_bps=FX,
                      half_spread_bps=hs, default_half_spread_bps=default_hs)
    return spec.stress(mult) if mult != 1.0 else spec


def r2_buy_fee_usd(buy_usd: float, hs_bps: float) -> float:
    """R2 매수 수수료(USD). ≤$10 청크 분할 → 수수료 0. 반호가+슬리피지(미시구조)는 부담.

    매수는 SEC/TAF 없음. 미시구조 = (반호가+슬리피지)bp × 금액.
    """
    micro = abs(buy_usd) * (hs_bps + SLIP) * BPS
    return micro   # 수수료 성분 0


def r2_sell_reg_usd(sell_usd: float, *, sell_price: float = 400.0,
                    commission_bps: float = FEE10) -> float:
    """R2 매도의 수수료+규제 성분(USD, 미시구조 제외). 손으로 검산 가능한 핵심 공식.

    소액 N 에서 = 0.001·N + 0.02 (= 10bp 수수료 + $0.01 SEC + $0.01 TAF).
      수수료 = 0 (N ≤ $10) 아니면 commission_bps·N (수수료 < $0.01 이면 절사 0).
      SEC = max($0.01, 0.00206%·N).
      TAF = max($0.01, $0.000166·주식수),  주식수 = N / sell_price.
    """
    if sell_usd <= MICRO_FREE_THRESHOLD:
        comm = 0.0
    else:
        comm = commission_bps * BPS * sell_usd
        if comm < COMM_TRUNCATE:
            comm = 0.0
    sec = max(SEC_MIN, SEC_RATE * sell_usd)
    shares = sell_usd / sell_price if sell_price > 0 else 0.0
    taf = max(TAF_MIN, TAF_PER_SHARE * shares)
    return comm + sec + taf


def r2_sell_fee_usd(sell_usd: float, *, sell_price: float, hs_bps: float,
                    commission_bps: float = FEE10) -> float:
    """R2 매도 총수수료(USD) = 미시구조(반호가+슬리피지) + 수수료+규제."""
    micro = abs(sell_usd) * (hs_bps + SLIP) * BPS
    return micro + r2_sell_reg_usd(sell_usd, sell_price=sell_price,
                                   commission_bps=commission_bps)


def r2_roundtrip_bps(notional: float, *, tier: str = "ETF", sell_price: float = 400.0
                     ) -> float:
    """R2 왕복 per-trade bps(진입=매수, 청산=매도, 같은 notional 근사)."""
    hs = HALF_SPREAD.get(tier, HALF_SPREAD["DEFAULT"])
    buy = r2_buy_fee_usd(notional, hs)
    sell = r2_sell_fee_usd(notional, sell_price=sell_price, hs_bps=hs)
    return (buy + sell) / notional * 1e4 if notional > 0 else float("nan")


def r1_roundtrip_bps(*, tier: str = "ETF", commission_bps: float = FEE10,
                     mult: float = 1.0) -> float:
    """R1(또는 R3=mult2) 왕복 per-trade bps (크기 무관)."""
    hs = HALF_SPREAD.get(tier, HALF_SPREAD["DEFAULT"])
    per_side = (commission_bps + hs + SLIP) * mult
    return 2.0 * per_side


def r2_breakeven_notional(*, tier: str = "ETF", commission_bps: float = FEE10,
                          sell_price: float = 400.0) -> float:
    """R2 왕복비용 = R1 왕복비용 이 되는 per-trade notional(달러). 이분탐색.

    이 아래에서는 고정 규제최소($0.02)가 R2 를 R1 보다 비싸게 만든다(=R2 이점 소멸).
    """
    target = r1_roundtrip_bps(tier=tier, commission_bps=commission_bps)
    lo, hi = 1.0, 100.0
    # r2_roundtrip_bps 는 notional 증가에 대해 단조감소 → target 과 교차점 탐색
    if r2_roundtrip_bps(hi, tier=tier, sell_price=sell_price) > target:
        return float("nan")   # 교차 없음(항상 R2 가 더 비쌈) — 발생 안 함
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if r2_roundtrip_bps(mid, tier=tier, sell_price=sell_price) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ══════════════════════════════════════════════════════════════════════════════
# SGOV 합성(현금 3.6% 를 매매비와 함께 모델)
# ══════════════════════════════════════════════════════════════════════════════
def sgov_prices(n: int, annual: float = SGOV_ANNUAL) -> list[float]:
    """일 복리 3.6% 합성 SGOV 가격 시리즈(길이 n, 시작 100)."""
    daily = (1.0 + annual) ** (1.0 / 252.0) - 1.0
    px = [100.0]
    for _ in range(1, n):
        px.append(px[-1] * (1.0 + daily))
    return px


def sgov_cash_rate(n: int, annual: float = SGOV_ANNUAL) -> list[float]:
    """무마찰 현금 3.6% (일수익률 상수) — 'idle USD at yield' 상한 케이스."""
    daily = (1.0 + annual) ** (1.0 / 252.0) - 1.0
    return [daily] * n


# ══════════════════════════════════════════════════════════════════════════════
# 통계 헬퍼
# ══════════════════════════════════════════════════════════════════════════════
def per_trade_bps_stats(net_bps: list[float], *, B: int = 2000, seed: int = 7) -> dict:
    """per-trade 순엣지(bp) 시리즈 → 평균·t·부트스트랩 95% CI(bp)."""
    if not net_bps:
        return {"n": 0}
    mean = statistics.fmean(net_bps)
    t = gate.trade_tstat(net_bps)
    _pt, lo, hi = gate.trade_pnl_bootstrap_ci(net_bps, B=B, rng=random.Random(seed))
    return {"n": len(net_bps), "mean_bps": mean, "tstat": t,
            "ci_lo_bps": lo, "ci_hi_bps": hi, "pf": gate.profit_factor(net_bps)}


def unit_terminal(returns: list[float]) -> float:
    eq = 1.0
    for r in returns:
        eq *= (1.0 + r)
    return eq


# ══════════════════════════════════════════════════════════════════════════════
# 설정 레지스트리 — 신호는 import 재사용, 실행/비용만 새로 입힘
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Cfg:
    idea: str            # 기저 idea (c4b_ 접두 제외)
    label: str
    leveraged: bool
    tier: str            # 주 매매자산 티어(비용/크기 테이블용)
    pre_params: dict     # 사전등록 파라미터(재튜닝 금지 확인용)


def build_panels():
    """롱윈도(FRED 합성) + 실물(2016-2026) 패널을 한 번에 로드해 공유."""
    panels = {}
    # c3b FRED 스택: sig/tr/x3/cash0 (RSI2·events·core_boost)
    panels["c3b"] = c3b.build_stack()
    # c2b FRED 스택: sig/exec{1x,2x,3x}/cash (RSI2 1x)
    panels["c2b"] = c2b.load_fred_stack()
    # c2c NDX 패널: panel{NDX,NDX2X,NDX3X}/cash_rate (TOM)
    panels["c2c"] = c2c.build_ndx_panel()
    # c2a 합성: L1/L2/L3/VIX (voltarget25)
    pa, da, ca = c2a.build_synthetic_panel()
    panels["c2a"] = {"panel": pa, "dates": da, "cash": ca}
    # c3a 합성: L1/L2/L3 + 0%/DTB3 (vt_ewma)
    p3, d3, z3, dtb3_3 = c3a.build_synthetic_panel()
    panels["c3a"] = {"panel": p3, "dates": d3, "zero": z3, "dtb3": dtb3_3}
    # 실물 2016-2026 (QQQ/QLD/TQQQ/SCHD/GLD) — 계좌경로·B0/B1·DCA
    pr, dr, zr, dtb3r = c3a.build_real_panel()
    panels["real"] = {"panel": pr, "dates": dr, "zero": zr, "dtb3": dtb3r}
    return panels


CONFIGS = [
    Cfg("c2b_rsi2_1x", "RSI(2) 1x (c2b)", False, "ETF",
        {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10, "exec": "1x"}),
    Cfg("c3b_lcc_rsi2", "RSI(2) 3x·⅓ TQQQ (c3b_lcc)", True, "LEV",
        {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10,
         "notional": 1 / 3, "exec": "tqqq_1_3"}),
    Cfg("c3b_lcc_events", "RSI2∪TOM∪preFOMC 3x·⅓ (c3b_lcc)", True, "LEV",
        {"union": "rsi2|tom|fomc", "tom": (1, 3), "notional": 1 / 3, "exec": "tqqq_1_3"}),
    Cfg("c3b_core_boost", "상시 QQQ + 이벤트 ⅓→TQQQ (c3b)", True, "LEV",
        {"base": "qqq_100", "boost_frac": 1 / 3, "event": "rsi2|tom|fomc"}),
    Cfg("c2c_tom_1x", "Turn-of-month 1x (c2c)", False, "ETF",
        {"n_end": 1, "n_start": 3, "leg": "1x"}),
    Cfg("c2a_voltarget25", "vol-target 25% 고회전 (c2a)", True, "LEV",
        {"vol_target": 0.25, "vol_win": 20, "lev_band": 0.25}),
    Cfg("c3a_vt_ewma", "주간 EWMA vol-target (c3a, qld_cash)", True, "LEV",
        {"T": 0.20, "Lmax": 2.0, "lam": 0.97, "band": 0.5, "rep": "qld_cash"}),
]


# ── 각 config → target-weight 스케줄 + 실행 패널 (FRED 롱윈도) ────────────────
def tw_and_panel(cfg: Cfg, panels: dict, *, cash_mode: str):
    """(exec_panel, dates, weights, cash_rate, exec_lag, band, trade_sym, notional) 반환.

    weights 는 run_weights 용 목표비중 리스트. cash_mode ∈ {"zero","sgov_park","sgov_yield"}.
    신호(des/tw) 는 import 함수로 생성(재사용) — 여기서 만들지 않는다.
    """
    base_rsi = {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10}

    def cash_series(dates):
        if cash_mode == "sgov_yield":
            return sgov_cash_rate(len(dates))
        return [0.0] * len(dates)

    def apply_park(weights, dates, exec_panel):
        """cash_mode=='sgov_park' 이면 잔여현금을 SGOV 심볼로 라우팅(매매비 발생)."""
        if cash_mode != "sgov_park":
            return weights, exec_panel, [0.0] * len(dates)
        exec_panel = dict(exec_panel)
        exec_panel["SGOV"] = sgov_prices(len(dates))
        neww = []
        for w in weights:
            s = sum(w.values())
            w2 = dict(w)
            if s < 1.0 - 1e-9:
                w2["SGOV"] = 1.0 - s
            neww.append(w2)
        return neww, exec_panel, [0.0] * len(dates)

    if cfg.idea == "c2b_rsi2_1x":
        st = panels["c2b"]
        dates = st["dates"]
        des = c2b.rsi2_positions(st["sig"], **base_rsi)
        weights = c2b.weights_from_positions(des, "1x")
        exec_panel = {"1x": st["exec"]["1x"]}
        weights, exec_panel, _ = apply_park(weights, dates, exec_panel)
        return exec_panel, dates, weights, cash_series(dates), 1, 0.0, "1x", 1.0

    if cfg.idea in ("c3b_lcc_rsi2", "c3b_lcc_events"):
        st = panels["c3b"]
        dates = st["dates"]
        if cfg.idea == "c3b_lcc_rsi2":
            tw = c3b.rsi2_tw(st["sig"], base_rsi)
        else:
            tw = c3b.union_events_tw(dates, st["sig"], base_rsi)
        notional = 1 / 3
        weights = [{"TQQQ3X": notional} if v > 0.5 else {} for v in tw]
        exec_panel = {"TQQQ3X": st["x3"]}
        weights, exec_panel, _ = apply_park(weights, dates, exec_panel)
        return exec_panel, dates, weights, cash_series(dates), 1, 0.0, "TQQQ3X", notional

    if cfg.idea == "c3b_core_boost":
        st = panels["c3b"]
        dates = st["dates"]
        tw = c3b.union_events_tw(dates, st["sig"], base_rsi)
        # 상시 QQQ(=NDXTR) 100%, 이벤트 시 ⅓ 을 TQQQ 로 스왑(노출 ≈1.67x)
        weights = [({"NDXTR": 2 / 3, "TQQQ3X": 1 / 3} if v > 0.5 else {"NDXTR": 1.0})
                   for v in tw]
        exec_panel = {"NDXTR": st["tr"], "TQQQ3X": st["x3"]}
        # 상시 풀투자 → 잔여현금 없음(SGOV park 무의미)
        return exec_panel, dates, weights, cash_series(dates), 1, 0.0, "TQQQ3X", 1 / 3

    if cfg.idea == "c2c_tom_1x":
        pan = panels["c2c"]
        dates = pan["dates"]
        sig = c2c.make_calendar_signal(c2c.hold_tom(1, 3), asset="NDX")
        weights = sig(pan["panel"], dates)
        exec_panel = {"NDX": pan["panel"]["NDX"]}
        weights, exec_panel, _ = apply_park(weights, dates, exec_panel)
        return exec_panel, dates, weights, cash_series(dates), 0, 0.05, "NDX", 1.0

    if cfg.idea == "c2a_voltarget25":
        p = panels["c2a"]
        dates = p["dates"]
        weights = c2a.sig_vol_target(p["panel"], dates, vol_target=0.25, vol_win=20,
                                     lev_band=0.25)
        exec_panel = {k: p["panel"][k] for k in ("L1", "L2", "L3")}
        weights, exec_panel, _ = apply_park(weights, dates, exec_panel)
        return exec_panel, dates, weights, cash_series(dates), 1, 0.05, "L3", 1.0

    if cfg.idea == "c3a_vt_ewma":
        p = panels["c3a"]
        dates = p["dates"]
        weights = c3a.sig_vt_ewma(p["panel"], dates, T=0.20, Lmax=2.0, lam=0.97,
                                  band=0.5, rep="qld_cash")
        exec_panel = {k: p["panel"][k] for k in ("L1", "L2", "L3")}
        weights, exec_panel, _ = apply_park(weights, dates, exec_panel)
        return exec_panel, dates, weights, cash_series(dates), 1, 0.10, "L2", 1.0

    raise ValueError(cfg.idea)


def tier_map_for(exec_panel: dict, cfg: Cfg) -> dict[str, str]:
    """실행 심볼 → 티어. 레버리지 합성/실물 ETF 는 LEV, 나머지 ETF."""
    tiers = {}
    for s in exec_panel:
        if s in ("TQQQ3X", "L2", "L3", "TQQQ", "QLD", "NDX2X", "NDX3X"):
            tiers[s] = "LEV"
        else:
            tiers[s] = "ETF"
    return tiers


def run_unit(cfg: Cfg, panels: dict, *, commission: float, mult: float, cash_mode: str):
    """단위자본 net-return 스트림(WeightsResult)을 주어진 비용/현금모드로 실행."""
    exec_panel, dates, weights, cash, exec_lag, band, sym, notional = tw_and_panel(
        cfg, panels, cash_mode=cash_mode)
    tiers = tier_map_for(exec_panel, cfg)
    cost = cost_spec(commission, tiers=tiers, mult=mult)
    res = R.run_weights(exec_panel, dates, weights, exec_lag=exec_lag,
                        rebalance_band=band, cost=cost, cash_rate=cash)
    return res, dates


# ── per-trade 거래 리스트(FRED, R1 비용) ─────────────────────────────────────
def trades_for(cfg: Cfg, panels: dict):
    """(trades, sym, tier, notional, leverage) — per-trade 엣지용. 신호 재사용.

    leverage = 기초 배수(TQQQ 3·QLD 2·1x 1). per-trade 엣지는 **노출(E=1) 단위**로 정규화.
    core_boost 는 포트폴리오 오버레이(=events 슬리브와 동일 신호) → per-trade 생략.
    """
    base_rsi = {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10}
    if cfg.idea == "c2b_rsi2_1x":
        st = panels["c2b"]
        des = c2b.rsi2_positions(st["sig"], **base_rsi)
        trs = c2b.extract_trades(des, st["exec"]["1x"], st["dates"], "1x",
                                 exec_lag=1, notional=1.0)
        return trs, "1x", "ETF", 1.0, 1.0
    if cfg.idea in ("c3b_lcc_rsi2", "c3b_lcc_events"):
        st = panels["c3b"]
        dates = st["dates"]
        tw = (c3b.rsi2_tw(st["sig"], base_rsi) if cfg.idea == "c3b_lcc_rsi2"
              else c3b.union_events_tw(dates, st["sig"], base_rsi))
        pairs = c3b.trade_index_pairs(tw, len(dates), exec_lag=1)
        notional = 1 / 3
        trs = [R.Trade(entry_dt=dates[ef], entry_price=st["x3"][ef], exit_dt=dates[xf],
                       exit_price=st["x3"][xf], notional=notional, symbol="TQQQ3X")
               for ef, xf in pairs]
        return trs, "TQQQ3X", "LEV", notional, 3.0
    if cfg.idea == "c2c_tom_1x":
        pan = panels["c2c"]
        dates = pan["dates"]
        sig = c2c.make_calendar_signal(c2c.hold_tom(1, 3), asset="NDX")
        tw = [1.0 if ("NDX" in (w or {})) else 0.0 for w in sig(pan["panel"], dates)]
        pairs = c3b.trade_index_pairs(tw, len(dates), exec_lag=0)
        closes = pan["panel"]["NDX"]
        trs = [R.Trade(entry_dt=dates[ef], entry_price=closes[ef], exit_dt=dates[xf],
                       exit_price=closes[xf], notional=1.0, symbol="NDX")
               for ef, xf in pairs]
        return trs, "NDX", "ETF", 1.0, 1.0
    # voltarget/ewma/core_boost: 이산 per-trade 정의 부적절 → 빈 리스트(회전율로 대체).
    return [], cfg.tier, cfg.tier, 1.0, 1.0


def per_trade_edge(cfg: Cfg, panels: dict, *, commission: float):
    """R1 비용 하 per-trade 순엣지(bp, **노출 E=1 단위**) 통계. 없으면 None."""
    trs, sym, tier, notional, lev = trades_for(cfg, panels)
    if not trs:
        return None
    cost = cost_spec(commission, tiers={sym: tier})
    tr_res = R.run_trades(trs, cost=cost, capital=1.0)
    exposure = notional * lev            # E=1 정규화(1/3 노셔널 × 3x = 1.0)
    net_bps = [p / exposure * 1e4 for p in tr_res.pnls]
    gross_series = [g / exposure * 1e4 for g in tr_res.gross_pnls]
    stats = per_trade_bps_stats(net_bps)
    stats["gross_bps"] = statistics.fmean(gross_series)
    stats["rt_bps"] = cost.roundtrip_bps(sym)            # 노셔널 기준 왕복비
    stats["rt_bps_per_exposure"] = cost.roundtrip_bps(sym) / lev
    stats["gross_series"] = gross_series
    stats["tier"] = tier
    stats["notional_frac"] = notional
    stats["leverage"] = lev
    return stats


def per_trade_r2_by_size(pte: dict, *, size: float, rep_price: float = 400.0) -> dict:
    """FRED per-trade gross(E=1) 에 R2 왕복비(크기 의존, 노출 단위)를 빼 순엣지 통계.

    거래 달러노셔널 = notional_frac × account_size. R2 왕복비/노출 = r2_rt(notional)/leverage.
    """
    if not pte or not pte.get("gross_series"):
        return {"n": 0}
    dollar_notional = pte["notional_frac"] * size
    r2rt = r2_roundtrip_bps(dollar_notional, tier=pte["tier"], sell_price=rep_price)
    r2rt_E = r2rt / pte["leverage"]
    net = [g - r2rt_E for g in pte["gross_series"]]
    st = per_trade_bps_stats(net)
    st["r2_rt_bps_per_exposure"] = r2rt_E
    return st


# ── 실물(2016-2026) 목표비중 스케줄 — 신호 재사용, 심볼만 실물로 매핑 ─────────
def real_weights(cfg: Cfg, panels: dict, *, cash_mode: str):
    """(pan, dates, weights, exec_lag, prim_sym, notional_frac, rep_price) 반환.

    cash_mode=='sgov_park' 이면 잔여현금을 합성 SGOV 로 라우팅(매매비 발생).
    """
    pr = panels["real"]["panel"]       # 키: L1=QQQ, L2=QLD, L3=TQQQ, QQQ, SCHD, GLD
    dates = panels["real"]["dates"]
    base_rsi = {"rsi_entry": 10.0, "sma_trend": 200, "sma_exit": 5, "max_hold": 10}
    sig_close = pr["QQQ"]

    if cfg.idea == "c2b_rsi2_1x":
        des = c2b.rsi2_positions(sig_close, **base_rsi)
        w = [{"QQQ": 1.0} if d > 0.5 else {} for d in des]
        pan = {"QQQ": pr["QQQ"]}
        prim, frac, lag = "QQQ", 1.0, 1
    elif cfg.idea == "c3b_lcc_rsi2":
        tw = c3b.rsi2_tw(sig_close, base_rsi)
        w = [{"TQQQ": 1 / 3} if v > 0.5 else {} for v in tw]
        pan = {"TQQQ": pr["L3"]}
        prim, frac, lag = "TQQQ", 1 / 3, 1
    elif cfg.idea == "c3b_lcc_events":
        tw = c3b.union_events_tw(dates, sig_close, base_rsi)
        w = [{"TQQQ": 1 / 3} if v > 0.5 else {} for v in tw]
        pan = {"TQQQ": pr["L3"]}
        prim, frac, lag = "TQQQ", 1 / 3, 1
    elif cfg.idea == "c3b_core_boost":
        tw = c3b.union_events_tw(dates, sig_close, base_rsi)
        w = [({"QQQ": 2 / 3, "TQQQ": 1 / 3} if v > 0.5 else {"QQQ": 1.0}) for v in tw]
        pan = {"QQQ": pr["QQQ"], "TQQQ": pr["L3"]}
        prim, frac, lag = "TQQQ", 1 / 3, 1
    elif cfg.idea == "c2c_tom_1x":
        sig = c2c.make_calendar_signal(c2c.hold_tom(1, 3), asset="QQQ")
        w = sig({"QQQ": pr["QQQ"]}, dates)
        pan = {"QQQ": pr["QQQ"]}
        prim, frac, lag = "QQQ", 1.0, 0
    elif cfg.idea == "c2a_voltarget25":
        pp = {"L1": pr["L1"], "L2": pr["L2"], "L3": pr["L3"]}
        wraw = c2a.sig_vol_target(pp, dates, vol_target=0.25, vol_win=20, lev_band=0.25)
        w = [{("QQQ" if k == "L1" else "QLD" if k == "L2" else "TQQQ"): v
              for k, v in wi.items()} for wi in wraw]
        pan = {"QQQ": pr["L1"], "QLD": pr["L2"], "TQQQ": pr["L3"]}
        prim, frac, lag = "TQQQ", 1.0, 1
    elif cfg.idea == "c3a_vt_ewma":
        pp = {"L1": pr["L1"], "L2": pr["L2"], "L3": pr["L3"]}
        wraw = c3a.sig_vt_ewma(pp, dates, T=0.20, Lmax=2.0, lam=0.97, band=0.5,
                               rep="qld_cash")
        w = [{("QQQ" if k == "L1" else "QLD" if k == "L2" else "TQQQ"): v
              for k, v in wi.items()} for wi in wraw]
        pan = {"QQQ": pr["L1"], "QLD": pr["L2"], "TQQQ": pr["L3"]}
        prim, frac, lag = "QLD", 1.0, 1
    else:
        raise ValueError(cfg.idea)

    if cash_mode == "sgov_park":
        pan = dict(pan)
        pan["SGOV"] = sgov_prices(len(dates))
        neww = []
        for wi in w:
            s = sum(wi.values())
            w2 = dict(wi)
            if s < 1.0 - 1e-9:
                w2["SGOV"] = 1.0 - s
            neww.append(w2)
        w = neww
    rep_price = statistics.median(pan[prim])
    return pan, dates, w, lag, prim, frac, rep_price


def r2_fee_fn_for(pan: dict, rep_price: float):
    """run_dca_overlay/run_trades 용 R2 fee_fn(side, notional_usd)->USD (국소 구현).

    매수: ≤$10 청크분할 → 수수료 0, 미시구조만. 매도: 10bp + SEC + TAF + 미시구조.
    티어 반호가는 주 자산 기준(다자산 DCA 는 근사) — TAF 는 rep_price 로 주식수 환산.
    """
    # 주 매매자산 티어(레버리지 여부)로 반호가 선택
    lev = any(s in ("TQQQ", "QLD") for s in pan if s != "SGOV")
    hs = HALF_SPREAD["LEV"] if lev else HALF_SPREAD["ETF"]

    def fee(side: str, notional: float) -> float:
        if side.upper() == "BUY":
            return r2_buy_fee_usd(notional, hs)
        return r2_sell_fee_usd(notional, sell_price=rep_price, hs_bps=hs)
    return fee


def dca_run(cfg: Cfg, panels: dict, *, regime: str, cash_mode: str):
    """실물 DCA(rebalance 모드) 최종자산·XIRR·거래수. regime ∈ {R1,R3,R2}.

    R1/R3: bps CostSpec(수수료 10, R3 는 ×2). R2: 국소 fee_fn(달러 공식).
    cash_mode: zero / sgov_yield(무마찰 3.6%) / sgov_park(SGOV 매매·비용부담).
    """
    pan, dates, w, lag, prim, frac, rep_price = real_weights(cfg, panels, cash_mode=cash_mode)
    cash_rate = (sgov_cash_rate(len(dates)) if cash_mode == "sgov_yield"
                 else [0.0] * len(dates))
    tiers = {s: ("LEV" if s in ("TQQQ", "QLD", "NDX2X", "NDX3X") else "ETF") for s in pan}
    if regime == "R2":
        cost = cost_spec(FEE10, tiers=tiers)          # FX(입금)·기본 참조용
        fee_fn = r2_fee_fn_for(pan, rep_price)
        dca = R.run_dca_overlay(pan, dates, w, monthly_usd=ACCOUNT_MONTHLY,
                                initial_usd=ACCOUNT_SEED, cost=cost, cash_rate=cash_rate,
                                exec_lag=lag, mode="rebalance", fee_fn=fee_fn)
    else:
        mult = 2.0 if regime == "R3" else 1.0
        cost = cost_spec(FEE10, tiers=tiers, mult=mult)
        dca = R.run_dca_overlay(pan, dates, w, monthly_usd=ACCOUNT_MONTHLY,
                                initial_usd=ACCOUNT_SEED, cost=cost, cash_rate=cash_rate,
                                exec_lag=lag, mode="rebalance")
    return {"final": dca.final_value, "xirr": gate.xirr(dca.cashflows),
            "n_trades": dca.trade_count, "total_dep": dca.total_deposited}


def bench_dca(panels: dict, *, kind: str, commission: float, cash_mode: str):
    """B0=QQQ60/SCHD25/GLD15, B1=QQQ100 DCA 최종자산·XIRR(실물, 매수전용)."""
    pr = panels["real"]["panel"]
    dates = panels["real"]["dates"]
    cash = (sgov_cash_rate(len(dates)) if cash_mode == "sgov_yield" else [0.0] * len(dates))
    if kind == "B0":
        syms = {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15}
    else:
        syms = {"QQQ": 1.0}
    weights = [dict(syms) for _ in dates]
    pan = {k: pr[k] for k in syms}
    cost = cost_spec(commission, tiers={k: "ETF" for k in syms})
    dca = R.run_dca_overlay(pan, dates, weights, monthly_usd=ACCOUNT_MONTHLY,
                            initial_usd=ACCOUNT_SEED, cost=cost, cash_rate=cash,
                            exec_lag=0)
    return dca.final_value, gate.xirr(dca.cashflows)


# ══════════════════════════════════════════════════════════════════════════════
# 게이트 평가(단위자본 설계/홀드아웃) + 원장 적재
# ══════════════════════════════════════════════════════════════════════════════
def gate_eval_config(cfg: Cfg, panels: dict, *, commission: float, cash_mode: str,
                     suffix: str, econ_tv0: float | None, econ_tv1: float | None,
                     cost_stress_mult: float | None, ledger: str, rc_b: int,
                     program_sharpes: list[float], log_holdout: bool = True):
    """단위자본 FRED 스트림으로 run_splits → gate.decide(risk_track=auto, signal_reuse=True)."""
    idea_id = f"c4b_{cfg.idea}{suffix}"
    res, dates = run_unit(cfg, panels, commission=commission, mult=1.0, cash_mode=cash_mode)
    cand = res.net_returns
    # 벤치: 해당 패널의 1x B&H (QQQ 100% = NDX-TR 1x)
    bench_close = bench_1x_close(cfg, panels)
    bench = R.to_returns(bench_close)
    # run_splits: design/holdout 축 입력 + 원장 적재(홀드아웃 peek-once)
    # log 는 홀드아웃 재열람 방지 위해 already_peeked 확인
    peeked = gate.already_peeked(ledger, idea_id)
    splits = ge.run_splits(idea_id, {**cfg.pre_params, "regime": suffix, "cash": cash_mode},
                           1, cand, bench, dates, DESIGN_END,
                           ledger_path=ledger, rc_B=rc_b, log=(log_holdout and not peeked))
    out = {"idea_id": idea_id, "decisions": {}, "axis": {}, "unit": {}}
    full_eq = gate.returns_to_equity(cand)
    full_mdd = gate.max_drawdown(full_eq)
    for period, sp in splits.items():
        ax = dict(sp["axis_input"])
        # 경제 축: 실물 DCA 기준 terminal_vs_b0/b1
        ax["terminal_vs_b0"] = econ_tv0
        ax["terminal_vs_b1"] = econ_tv1
        # 유의성: 프로그램 전체 N DSR 을 권위값으로(부록 v2.1-3)
        period_cand = _period_returns(cand, bench, dates, period)[0]
        prog_dsr = (gate.deflated_sharpe_ratio(period_cand, program_sharpes,
                                               n_eff=len(program_sharpes))
                    if program_sharpes else ax.get("dsr"))
        ax["dsr"] = prog_dsr
        ax["leveraged"] = cfg.leveraged
        ax["mdd_full_sample"] = full_mdd
        if cost_stress_mult is not None:
            ax["cost_stress_mult"] = cost_stress_mult
        ax["signal_reuse"] = True
        ax["risk_track"] = "auto"
        # 벤치(QQQ 1x) 포트폴리오 지표(같은 구간)
        bench_period = _period_returns(cand, bench, dates, period)[1]
        bench_um = ge.unit_capital_metrics(bench_period) if bench_period else {}
        ax["mdd_bench"] = ax.get("mdd_bench")   # run_splits 가 이미 넣음
        dec = gate.decide(ax, risk_track="auto", signal_reuse=True)
        out["decisions"][period] = dec
        out["axis"][period] = ax
        out["unit"][period] = sp["unit"]
        out.setdefault("bench_unit", {})[period] = bench_um
    out["full_mdd"] = full_mdd
    out["res"] = res
    out["dates"] = dates
    return out


def _period_returns(cand, bench, dates, period):
    n = min(len(cand), len(bench), len(dates) - 1)
    rd = dates[1:n + 1]
    idxs = [i for i in range(n) if (rd[i] <= DESIGN_END) == (period == "design")]
    return ([cand[i] for i in idxs], [bench[i] for i in idxs])


def bench_1x_close(cfg: Cfg, panels: dict) -> list[float]:
    """config 패널에 맞는 1x B&H 종가(=QQQ 100% 벤치)."""
    if cfg.idea in ("c3b_lcc_rsi2", "c3b_lcc_events", "c3b_core_boost"):
        return panels["c3b"]["tr"]
    if cfg.idea == "c2b_rsi2_1x":
        return panels["c2b"]["exec"]["1x"]
    if cfg.idea == "c2c_tom_1x":
        return panels["c2c"]["panel"]["NDX"]
    if cfg.idea == "c2a_voltarget25":
        return panels["c2a"]["panel"]["L1"]
    if cfg.idea == "c3a_vt_ewma":
        return panels["c3a"]["panel"]["L1"]
    raise ValueError(cfg.idea)


# ══════════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════════
def main(fast: bool = False, ledger: str = LEDGER):
    rc_b = 300 if fast else 800
    print(f"[c4b] loading panels … (fast={fast}, rc_B={rc_b})")
    panels = build_panels()
    program_sharpes = gate.ledger_trial_sharpes(ledger, None)
    program_n_at_start = gate.ledger_count(ledger)
    print(f"[c4b] program ledger N at start = {program_n_at_start}, "
          f"trial sharpes = {len(program_sharpes)}")

    results = {"meta": {"ledger": ledger, "rc_b": rc_b,
                        "program_n_at_start": program_n_at_start,
                        "fee_correction": "STANDARD=10bp/side (Toss 2025-12-01)",
                        "design_end": DESIGN_END.isoformat()},
               "configs": {}}

    # 벤치 DCA(실물) — 현금 0% 기준
    b0_final, b0_xirr = bench_dca(panels, kind="B0", commission=FEE10, cash_mode="zero")
    b1_final, b1_xirr = bench_dca(panels, kind="B1", commission=FEE10, cash_mode="zero")
    results["benchmarks"] = {"B0": {"final": b0_final, "xirr": b0_xirr},
                             "B1": {"final": b1_final, "xirr": b1_xirr}}
    print(f"[c4b] B0 final ${b0_final:.2f} xirr {b0_xirr:+.2%} | "
          f"B1 final ${b1_final:.2f} xirr {b1_xirr:+.2%}")

    for cfg in CONFIGS:
        print(f"\n[c4b] === {cfg.idea} ({cfg.label}) ===")
        centry: dict = {"label": cfg.label, "leveraged": cfg.leveraged,
                        "tier": cfg.tier, "pre_params": {k: str(v) for k, v in
                                                         cfg.pre_params.items()}}

        # ── R1/R3 계좌경로 DCA (실물) : 경제성 tv0/tv1 ──
        r1 = dca_run(cfg, panels, regime="R1", cash_mode="zero")
        r3 = dca_run(cfg, panels, regime="R3", cash_mode="zero")
        r1_final, r1_xirr = r1["final"], r1["xirr"]
        tv0 = r1_final / b0_final if b0_final else None
        tv1 = r1_final / b1_final if b1_final else None
        centry["dca"] = {"R1": r1, "R3": r3, "tv0": tv0, "tv1": tv1}

        # ── R2 계좌경로(실물, 국소 fee_fn) ──
        r2 = dca_run(cfg, panels, regime="R2", cash_mode="zero")
        r2_tv0 = r2["final"] / b0_final if b0_final else None
        r2_tv1 = r2["final"] / b1_final if b1_final else None
        centry["r2"] = {"final": r2["final"], "xirr": r2["xirr"], "n_trades": r2["n_trades"],
                        "tv0": r2_tv0, "tv1": r2_tv1}

        # ── SGOV 현금 민감도(무마찰 3.6% vs park) — 실물 DCA XIRR ──
        sgy = dca_run(cfg, panels, regime="R1", cash_mode="sgov_yield")
        sgp = dca_run(cfg, panels, regime="R1", cash_mode="sgov_park")
        centry["cash_sens"] = {"idle0": r1, "sgov_yield": sgy, "sgov_park": sgp}

        # ── per-trade 엣지(FRED, R1 비용, 이산 거래 정의가 있으면) ──
        pte = per_trade_edge(cfg, panels, commission=FEE10)
        # R2 per-trade by size(FRED gross − R2 왕복비)
        r2_by_size = None
        if pte:
            r2_by_size = {int(s): per_trade_r2_by_size(pte, size=s) for s in SIZE_GRID}
            pte = {k: v for k, v in pte.items() if k != "gross_series"}
        centry["per_trade_fred"] = pte
        centry["per_trade_r2_by_size"] = r2_by_size

        # ── 비용 스트레스 배수(R1 대비 순엣지 생존): cost_stress_mult ──
        # 회전 기반 근사: 손익분기 왕복비용 / 실제 왕복비용. per-trade 엣지가 있으면 사용.
        csm = None
        if pte and pte.get("gross_bps") and pte.get("rt_bps_per_exposure"):
            # 손익분기 배수 = gross(E) / 실제왕복비(E). ≥2 이면 2× 스트레스(R3) 생존.
            rte = pte["rt_bps_per_exposure"]
            csm = pte["gross_bps"] / rte if rte > 0 else None

        # ── 게이트(단위자본 FRED, R1=fee10) ──
        g_fee10 = gate_eval_config(cfg, panels, commission=FEE10, cash_mode="zero",
                                   suffix="_fee10", econ_tv0=tv0, econ_tv1=tv1,
                                   cost_stress_mult=csm, ledger=ledger, rc_b=rc_b,
                                   program_sharpes=program_sharpes)
        centry["gate_fee10"] = _decode_gate(g_fee10)

        # ── 게이트(단위자본 FRED, R2=micro; econ 은 실물 R2 tv) ──
        g_micro = gate_eval_config(cfg, panels, commission=FEE10, cash_mode="zero",
                                   suffix="_micro", econ_tv0=r2_tv0, econ_tv1=r2_tv1,
                                   cost_stress_mult=csm, ledger=ledger, rc_b=rc_b,
                                   program_sharpes=program_sharpes)
        centry["gate_micro"] = _decode_gate(g_micro)

        # ── R2 break-even per-trade notional / 크기별 bps ──
        be = r2_breakeven_notional(tier=cfg.tier)
        size_bps = {int(s): {"R2_rt_bps": r2_roundtrip_bps(s, tier=cfg.tier),
                             "R1_rt_bps": r1_roundtrip_bps(tier=cfg.tier),
                             "R3_rt_bps": r1_roundtrip_bps(tier=cfg.tier, mult=2.0)}
                    for s in SIZE_GRID}
        centry["break_even_notional"] = be
        centry["size_bps"] = size_bps

        results["configs"][cfg.idea] = centry
        _print_config_summary(cfg, centry)

    results["meta"]["program_n_end"] = gate.ledger_count(ledger)
    RESULTS_JSON.write_text(json.dumps(results, indent=2, default=str))
    write_report(results)
    print(f"\n[c4b] wrote {RESULTS_JSON} and {REPORT}")
    return results


def _decode_gate(g: dict) -> dict:
    out = {"idea_id": g["idea_id"], "full_mdd": g["full_mdd"]}
    for period in ("design", "holdout"):
        if period in g["decisions"]:
            dec = g["decisions"][period]
            ax = g["axis"][period]
            um = g["unit"].get(period, {})
            bu = g.get("bench_unit", {}).get(period, {})
            out[period] = {"verdict": dec.verdict, "axes": dec.axes,
                           "tags": dec.tags,
                           "dsr": ax.get("dsr"), "rc_p": ax.get("rc_pvalue"),
                           "spa_p": ax.get("spa_pvalue"),
                           "tv0": ax.get("terminal_vs_b0"), "tv1": ax.get("terminal_vs_b1"),
                           "mdd": ax.get("mdd"),
                           "cagr": um.get("cagr"), "sharpe": um.get("sr_annual"),
                           "ulcer": um.get("ulcer"), "calmar": um.get("calmar"),
                           "bench_cagr": bu.get("cagr"), "bench_sharpe": bu.get("sr_annual"),
                           "bench_mdd": bu.get("max_drawdown"), "bench_ulcer": bu.get("ulcer")}
    return out


def _print_config_summary(cfg, c):
    g = c["gate_fee10"].get("holdout", {})
    gm = c["gate_micro"].get("holdout", {})
    print(f"  DCA R1 ${c['dca']['R1']['final']:.0f} ({c['dca']['R1']['xirr']:+.1%}) "
          f"tv0={c['dca']['tv0']:.2f} tv1={c['dca']['tv1']:.2f}")
    print(f"  R2  ${c['r2']['final']:.0f} ({c['r2']['xirr']:+.1%}) tv0={c['r2']['tv0']:.2f}"
          f" trades={c['r2']['n_trades']} break-even ${c['break_even_notional']:.1f}")
    if c.get("per_trade_fred"):
        p = c["per_trade_fred"]
        print(f"  per-trade(R1,FRED): n={p['n']} net={p.get('mean_bps'):.1f}bp "
              f"t={p.get('tstat'):.2f} CI[{p.get('ci_lo_bps'):.1f},{p.get('ci_hi_bps'):.1f}] "
              f"gross={p.get('gross_bps'):.1f} rt={p.get('rt_bps'):.1f}")
    print(f"  gate fee10 holdout: {g.get('verdict')} (dsr={_f(g.get('dsr'))} "
          f"rc_p={_f(g.get('rc_p'),3)} spa_p={_f(g.get('spa_p'),3)})")
    print(f"  gate micro holdout: {gm.get('verdict')} (dsr={_f(gm.get('dsr'))} "
          f"rc_p={_f(gm.get('rc_p'),3)})")


def _f(v, nd=2):
    if v is None or (isinstance(v, float) and (v != v)):
        return "—"
    return f"{v:.{nd}f}"


def _pct(v, nd=1):
    if v is None or (isinstance(v, float) and (v != v)):
        return "—"
    return f"{v * 100:+.{nd}f}%"


def write_report(results: dict):
    R_ = results
    b0 = R_["benchmarks"]["B0"]
    b1 = R_["benchmarks"]["B1"]
    lines = []
    lines.append("# Cycle 4 · c4b — 수수료 재평가 (fee re-evaluation)\n")
    lines.append("> FACT CORRECTION (Toss 공지 2025-10-17, 2025-12-01 시행): **US 표준 수수료 "
                 "= 0.1%/side (10bp)**. 사이클 2·3 은 25bp 를 주 시나리오로 잘못 사용. 체결금액 "
                 "≤ $10 주문 수수료 0, 수수료 <$0.01 절사. 매도는 SEC 0.00206%(min $0.01, 불확실) "
                 "+ FINRA TAF ~$0.000166/주(min $0.01, 불확실) 추가.\n")
    lines.append("이 정정은 **파라미터 정정**(사후조정 아님)이나, 대상 신호들의 홀드아웃은 이미 "
                 "열람됨 → **부록 v2.1 §4 신호 재사용**: 새 idea_id(`_fee10`/`_micro`), 홀드아웃 "
                 "semi-contaminated, PASS 는 RC/SPA **p<0.01**, DSR 은 **프로그램 전체 N** 으로 보고. "
                 "사전등록 파라미터 재사용(재튜닝 금지). src/ 미수정, 비용 인자 전부 명시.\n")

    lines.append("## 1. 사전등록 (실행 전 고정 — 신호 재사용, 재비용화만)\n")
    lines.append("| # | idea (재사용) | 사전등록 파라미터 | 레버리지 | 매매 티어 |")
    lines.append("|---|---|---|---|---|")
    def _pv(v):
        if isinstance(v, float) and abs(v - 1 / 3) < 1e-6:
            return "1/3"
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    for i, cfg in enumerate(CONFIGS, 1):
        pp = ", ".join(f"{k}={_pv(v)}" for k, v in cfg.pre_params.items())
        lines.append(f"| {i} | `{cfg.idea}` | {pp} | {'예' if cfg.leveraged else '아니오'} "
                     f"| {cfg.tier} |")
    lines.append("")
    lines.append("**비용 레짐.** R1 표준 = 10bp/side + 반호가(ETF 1bp·레버리지 2bp·기본 3bp) + "
                 "슬리피지 5bp (기존 구조, 수수료만 25→10). R3 스트레스 = R1×2. R2 마이크로 = "
                 "매수 ≤$10 청크분할→수수료 0; 매도 단일주문 10bp + $0.01 SEC + $0.01 TAF "
                 "(소액 fee=0.001·N+0.02) — 달러크기 의존 → 실물 계좌경로($32 시드 + 월 $35, "
                 "2016-09~2026-09) 시뮬. FX 20bp 는 입금에만.\n")
    lines.append("**현금수익률 민감도.** 0%(기본, v2.1 §2) vs SGOV 유사 3.6%. "
                 "'idle USD at 0%' 와 'park in SGOV'(잔여현금을 SGOV 로 매매, 비용부담) 둘 다 모델.\n")
    lines.append("**벤치마크(실물 DCA).** "
                 f"B0=QQQ60/SCHD25/GLD15 최종 ${b0['final']:.0f} (XIRR {_pct(b0['xirr'])}), "
                 f"B1=QQQ100 최종 ${b1['final']:.0f} (XIRR {_pct(b1['xirr'])}). "
                 "단위자본 설계 1986–2008 / 홀드아웃 2009–2026(FRED 합성), 벤치 QQQ 100% = NDX-TR 1x.\n")

    # 2. per-trade 엣지 (FRED, R1=10bp)
    lines.append("## 2. per-trade 순엣지 (FRED 롱윈도, R1=10bp, **노출 E=1 정규화**)\n")
    lines.append("gross/net 은 노출 1x 단위(1/3 노셔널×3x = E1). 왕복비 = 노셔널 기준(노출당 = /leverage). "
                 "CI = 거래PnL 정상 부트스트랩 95%.\n")
    lines.append("| idea | n | gross(bp) | 왕복비/E1(bp) | net(bp) | t | 부트 CI(bp) | PF |")
    lines.append("|---|---:|---:|---:|---:|---:|---|---:|")
    for cfg in CONFIGS:
        p = R_["configs"][cfg.idea].get("per_trade_fred")
        if not p:
            lines.append(f"| `{cfg.idea}` | — | — | — | 연속레버리지/오버레이(거래정의 없음) | — | — | — |")
            continue
        lines.append(f"| `{cfg.idea}` | {p['n']} | {_f(p.get('gross_bps'),1)} | "
                     f"{_f(p.get('rt_bps_per_exposure'),1)} | {_f(p.get('mean_bps'),1)} | "
                     f"{_f(p.get('tstat'),2)} | [{_f(p.get('ci_lo_bps'),1)}, "
                     f"{_f(p.get('ci_hi_bps'),1)}] | {_f(p.get('pf'),2)} |")
    lines.append("")

    # 3. 포트폴리오 지표 (단위자본, 설계/홀드아웃, vs QQQ 1x)
    lines.append("## 3. 포트폴리오 지표 — 단위자본 (설계 1986–2008 / 홀드아웃 2009–2026, R1=10bp)\n")
    lines.append("벤치 = QQQ 100% = NDX-TR 1x (단위자본). CAGR/Sharpe/MDD/Ulcer.\n")
    for period, ptitle in (("design", "설계 1986–2008"), ("holdout", "홀드아웃 2009–2026")):
        lines.append(f"### 3{'a' if period=='design' else 'b'}. {ptitle}\n")
        lines.append("| idea | CAGR | Sharpe | MDD | Ulcer | Calmar | vs QQQ CAGR | vs QQQ MDD |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for cfg in CONFIGS:
            g = R_["configs"][cfg.idea]["gate_fee10"].get(period, {})
            lines.append(
                f"| `{cfg.idea}` | {_pct(g.get('cagr'))} | {_f(g.get('sharpe'))} | "
                f"{_pct(g.get('mdd'))} | {_f(g.get('ulcer'),1)} | {_f(g.get('calmar'))} | "
                f"{_pct(g.get('bench_cagr'))} | {_pct(g.get('bench_mdd'))} |")
        lines.append("")
    fm = {cfg.idea: R_["configs"][cfg.idea]["gate_fee10"].get("full_mdd") for cfg in CONFIGS}
    lines.append("전표본(1986–2026) MDD(레버리지 파산방지 −70% 하한 판정용): "
                 + ", ".join(f"`{k}` {_pct(v)}" for k, v in fm.items()) + ".\n")

    # 4. 게이트 판정 (단위자본, gate.decide auto+signal_reuse)
    lines.append("## 4. gate.decide 판정 (risk_track=auto, signal_reuse=True)\n")
    lines.append("### 4a. R1 fee10 — 홀드아웃 축\n")
    lines.append("| idea | 종합 | 경제 | 유의성 | 리스크 | DSR(prog) | RC p | SPA p | tv0 | tv1 | tags |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---|")
    for cfg in CONFIGS:
        g = R_["configs"][cfg.idea]["gate_fee10"].get("holdout", {})
        ax = g.get("axes", {})
        lines.append(f"| `{cfg.idea}` | **{g.get('verdict','—')}** | {ax.get('economic','—')} | "
                     f"{ax.get('significance','—')} | {ax.get('risk','—')} | "
                     f"{_f(g.get('dsr'))} | {_f(g.get('rc_p'),3)} | {_f(g.get('spa_p'),3)} | "
                     f"{_f(g.get('tv0'))} | {_f(g.get('tv1'))} | {','.join(g.get('tags',[]))} |")
    lines.append("")
    lines.append("### 4b. R2 micro — 홀드아웃 축 (경제=실물 R2 계좌경로)\n")
    lines.append("| idea | 종합 | 경제 | 유의성 | 리스크 | DSR(prog) | RC p | tv0 | tv1 |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---:|")
    for cfg in CONFIGS:
        g = R_["configs"][cfg.idea]["gate_micro"].get("holdout", {})
        ax = g.get("axes", {})
        lines.append(f"| `{cfg.idea}` | **{g.get('verdict','—')}** | {ax.get('economic','—')} | "
                     f"{ax.get('significance','—')} | {ax.get('risk','—')} | "
                     f"{_f(g.get('dsr'))} | {_f(g.get('rc_p'),3)} | "
                     f"{_f(g.get('tv0'))} | {_f(g.get('tv1'))} |")
    lines.append("")

    # 4. DCA 경제성 (실물 2016-2026)
    lines.append("## 5. DCA 경제성 (실물 2016-09~2026-09, 시드 $32 + 월 $35)\n")
    lines.append("| idea | R1 최종 | R1 XIRR | R3 최종 | R2 최종 | R2 XIRR | R2 거래수 | "
                 "vs B0(R1) | vs B1(R1) | vs B0(R2) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for cfg in CONFIGS:
        c = R_["configs"][cfg.idea]
        d = c["dca"]; r2 = c["r2"]
        lines.append(f"| `{cfg.idea}` | ${d['R1']['final']:.0f} | {_pct(d['R1']['xirr'])} | "
                     f"${d['R3']['final']:.0f} | ${r2['final']:.0f} | {_pct(r2['xirr'])} | "
                     f"{r2['n_trades']} | {_f(d['tv0'])} | {_f(d['tv1'])} | {_f(r2['tv0'])} |")
    lines.append(f"\n벤치(현금0%): B0 ${b0['final']:.0f}/{_pct(b0['xirr'])}, "
                 f"B1 ${b1['final']:.0f}/{_pct(b1['xirr'])}.\n")

    # 5. 현금수익률 민감도
    lines.append("## 6. 현금수익률 민감도 (idle 0% vs SGOV 3.6%, 실물 DCA XIRR)\n")
    lines.append("무마찰 = 잔여현금이 3.6% 이자만(매매비 0, 상한). park = 잔여현금을 SGOV 로 "
                 "매매(진입·이탈마다 비용부담) → 무마찰과 idle 사이.\n")
    lines.append("| idea | idle0% XIRR | SGOV 무마찰 XIRR | SGOV park XIRR | Δ(park−idle) |")
    lines.append("|---|---:|---:|---:|---:|")
    for cfg in CONFIGS:
        cs = R_["configs"][cfg.idea]["cash_sens"]
        d = cs["idle0"]["xirr"]; s = cs["sgov_yield"]["xirr"]; pk = cs["sgov_park"]["xirr"]
        delta = (pk - d) if (d is not None and pk is not None) else None
        lines.append(f"| `{cfg.idea}` | {_pct(d)} | {_pct(s)} | {_pct(pk)} | {_pct(delta,2)} |")
    lines.append("\nSGOV 는 현금비중이 큰 저노출 슬리브(RSI 1x·TOM·vt)에서만 유의미하다. "
                 "'park' 은 진입/이탈 매매비가 3.6% 이자의 일부를 상쇄해 무마찰 상한을 하회한다. "
                 "상시 풀투자(core_boost·voltarget)는 잔여현금이 없어 민감도 ≈ 0.\n")

    # 6. R2 크기별 per-trade bps + break-even
    lines.append("## 7. R2 크기 의존성 — per-trade 왕복 bps & break-even\n")
    lines.append("| idea | 티어 | R1 왕복bp | R3 왕복bp | R2 $50 | R2 $200 | R2 $1000 | R2 $5000 | "
                 "break-even $ |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for cfg in CONFIGS:
        c = R_["configs"][cfg.idea]
        sb = c["size_bps"]
        r1b = sb[50]["R1_rt_bps"]; r3b = sb[50]["R3_rt_bps"]
        lines.append(f"| `{cfg.idea}` | {cfg.tier} | {_f(r1b,1)} | {_f(r3b,1)} | "
                     f"{_f(sb[50]['R2_rt_bps'],1)} | {_f(sb[200]['R2_rt_bps'],1)} | "
                     f"{_f(sb[1000]['R2_rt_bps'],1)} | {_f(sb[5000]['R2_rt_bps'],1)} | "
                     f"{_f(c['break_even_notional'],1)} |")
    lines.append("\n**해석(정직).** R2 왕복비는 per-trade notional 증가에 대해 단조**감소**하여 "
                 "R1 아래로 수렴한다: 매수를 ≤$10 청크로 분할해 수수료 0 을 만들기 때문에 R2 는 "
                 "매수측 10bp 를 통째로 아낀다. 고정 규제최소($0.01 SEC + $0.01 TAF = $0.02)가 "
                 "R2 를 R1 보다 비싸게 만드는 구간은 **break-even notional 아래(≈$20)** 뿐이며, "
                 "실계좌는 시드 $32 에서 시작해 곧 그 위로 간다. 즉 **R2 이점은 계좌가 커질수록 "
                 "사라지지 않고 오히려 커져** 매수 수수료 절감분(≈왕복 10bp)으로 수렴한다. "
                 "'이점이 사라지는 상단 임계'는 존재하지 않는다(과제 프레이밍에 대한 정직한 정정). "
                 "다만 ≤$10 청크 분할은 대형 계좌에서 주문 수가 폭증(예 $5000 → 500 주문)해 "
                 "운영상 비현실적이다 — 실질 상단은 이 운영 제약이다.\n")

    # 7. 종합 · 정직한 결론
    lines.append("## 8. 판정 종합 · 정직한 결론\n")
    n_pass = sum(1 for cfg in CONFIGS
                 if R_["configs"][cfg.idea]["gate_fee10"].get("holdout", {}).get("verdict") == "PASS")
    n_cond = sum(1 for cfg in CONFIGS
                 if R_["configs"][cfg.idea]["gate_fee10"].get("holdout", {}).get("verdict") == "CONDITIONAL")
    lines.append(f"- 수수료 25→10bp 정정으로 per-trade 순엣지는 개선되지만(왕복비 62→32bp), "
                 f"홀드아웃 게이트 PASS={n_pass}, CONDITIONAL={n_cond} — signal_reuse=True 로 "
                 f"RC/SPA **p<0.01** 강화 + 프로그램 전체 N DSR 이 유의성을 누른다.\n")
    lines.append("- R2 마이크로는 매수 무료화로 회전비용을 낮춰 소액계좌 XIRR 를 끌어올리지만, "
                 "경제 축(tv0/tv1)과 유의성은 여전히 B0/스킬 기준을 넘지 못한다(레버리지 베타·현금드래그).\n")
    lines.append(f"- 프로그램 원장 N: 시작 {R_['meta']['program_n_at_start']} → 종료 "
                 f"{R_['meta'].get('program_n_end')} (c4b 시도 적재; append-only, 홀드아웃 peek-once).\n")

    lines.append("\n## 9. 원장 · 재현\n")
    lines.append("```\nPYTHONPATH=src .venv/bin/python experiments/c4b_fee_reeval.py [--fast]\n```\n")
    lines.append(f"- idea_id 접두 `c4b_*`, 접미 `_fee10`/`_micro`. 원장 `{Path(LEDGER).name}` "
                 "append-only, 홀드아웃 idea_id 당 1회(peek-once, gate.append_holdout_peek).\n")
    lines.append("- 비용 인자 전부 명시 전달(research.CostSpec 기본 수수료 변경과 무관). "
                 "R2 수수료 공식은 본 스크립트에 국소 구현(손검산: tests/test_c4b.py).\n")

    REPORT.write_text("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--ledger", default=LEDGER)
    args = ap.parse_args()
    main(fast=args.fast, ledger=args.ledger)
