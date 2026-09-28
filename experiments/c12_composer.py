"""c12 — Composer/커뮤니티 공격형 전략 (Lane A, 부록 v3).

사전등록·규약: reports/cycle12_composer.md, docs/aggressive_strategy_catalog.md(§B·§E·§9·§10),
docs/gate_v2_spec.md 부록 v3(공격형 수익 레인 Lane A), experiments/README.md.

목적함수(Lane A): **비용 반영 CAGR(단위자본)**. Sharpe/MDD 는 보고하되 탈락 사유 아님
(파산 가드만: 전표본 단위 MDD ≤ −95% 이면 FAIL). Lane A PASS 조건:
  (a) 설계·홀드아웃 **모두** CAGR > QQQ B&H CAGR,
  (b) 파라미터 이웃(±20~50%; RSI 임계는 카탈로그대로 ±3/±6)의 ≥60% 에서 CAGR > QQQ,
  (c) 비용 2배에서도 (a) 유지,
  (d) 사전등록 1개 + 이웃만(그리드서치 금지), 원장 기록.
DSR/SPA 는 **보고만**. 커뮤니티 전략은 공개 규칙 그대로 1회 평가(재튜닝 금지), 공개일 이후 = 진짜 OOS.

체결: 신호 close t → 체결 close t+1 (primary, exec_lag=1). same-close/MOC(exec_lag=0)는 별도 보고.
비용: 토스 0.1%/side(10bp) + 티어 반호가(레버리지 2bp, UVXY/VIXY 5bp, 그외 ETF 1bp) + 5bp 슬리피지.
      2× 스트레스 + ≤$10 분할 micro(매수 무료) 별도. 1x 섀도(3x/UVXY→QQQ/SPY/SMH/PSQ/현금)도 병기.

데이터: histdata 캐시(Nasdaq 폴백, 대부분 2016-09~2026-09; UVXY 2018-09, HIBL 2019-11).
재현: PYTHONPATH=src .venv/bin/python experiments/c12_composer.py
      [--ledger PATH] [--no-ledger] [--no-report] [--fast]
Do NOT edit src/.  src·gate·research·histdata·fees 는 import 만 한다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
import gate_eval as GE  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle12_composer.md"
RESULTS_JSON = ROOT / "reports" / "c12_results.json"

DESIGN_END = date(2021, 12, 31)
LANE = "A"
PPY = 252
BPS = 1e-4

# ── 유동성 티어(사양 §7 + 태스크): 레버리지 2bp, 변동성 ETP 5bp, 그외 ETF 1bp ──
LEV = {"TQQQ", "SQQQ", "SOXL", "SOXS", "TECL", "TECS", "SPXL", "SPXU",
       "UPRO", "TMF", "FAS", "HIBL", "SVIX", "QLD"}
VOL = {"UVXY", "VIXY", "VIXM"}


def build_cost(mult: float = 1.0) -> R.CostSpec:
    hs = {s: 2.0 for s in LEV}
    hs.update({s: 5.0 for s in VOL})
    cs = R.CostSpec(commission_bps=10.0, slippage_bps=5.0, fx_bps=20.0,
                    half_spread_bps=hs, default_half_spread_bps=1.0)  # ETF 기본 1bp
    return cs.stress(mult) if mult != 1.0 else cs


def micro_fee_fn(comm=10.0, hs=2.0, slip=5.0):
    """≤$10 분할 무료 micro 레짐: **매수 수수료 0**, 매도만 수수료. 시장비용(반호가+슬리피지)은 양측.

    run_weights 의 fee_fn(side, notional) 은 심볼을 모르므로 반호가는 대표 레버리지 티어(2bp)로
    근사한다(그외 ETF 는 이보다 작아 보수적). notional 은 자본분수, 반환은 동일단위 '비용'.
    """
    def fn(side: str, notional: float) -> float:
        n = abs(notional)
        market = (hs + slip) * BPS * n
        if side.upper() == "BUY":
            return market                       # 매수 무료(수수료 0)
        return market + comm * BPS * n          # 매도만 수수료
    return fn


# ── 1x 섀도 매핑(규제 게이트: 한국 개인은 3x/UVXY 실거래 불가 가정) ───────────────
SHADOW = {
    "TQQQ": "QQQ", "SPXL": "SPY", "UPRO": "SPY", "SOXL": "SMH", "TECL": "QQQ",
    "FAS": "SPY", "HIBL": "SPY",
    "SQQQ": "PSQ", "SOXS": "PSQ", "SPXU": "SH", "TECS": "PSQ",
    "QQQ": "QQQ", "SPY": "SPY", "SMH": "SMH", "PSQ": "PSQ", "SH": "SH",
    # 현금(1x 대체 없음): 변동성·현금·채권
    "UVXY": None, "VIXY": None, "VIXM": None, "SVIX": None,
    "BIL": None, "BSV": None, "SHY": None, "SHV": None, "BOXX": None,
    "IEF": None, "TMF": None, "UUP": None,
}
SHADOW_UNIVERSE = ["QQQ", "SPY", "SMH", "PSQ", "SH"]


def shadow_weights(weights: list[dict]) -> list[dict]:
    out = []
    for w in weights:
        nw: dict[str, float] = {}
        for sym, wt in w.items():
            tgt = SHADOW.get(sym, sym)
            if tgt is None:
                continue                        # 현금
            nw[tgt] = nw.get(tgt, 0.0) + wt
        out.append(nw)
    return out


# ── 인과적 지표 캐시 ─────────────────────────────────────────────────────────
class Ind:
    """close t 에서 계산한 값은 입력 ≤ t 만 참조(research 헬퍼가 보장). 캐시로 중복계산 회피."""

    def __init__(self, S: dict[str, list[float]]):
        self.S = S
        self._rsi: dict[tuple, list] = {}
        self._sma: dict[tuple, list] = {}
        self._cr: dict[tuple, list] = {}

    def rsi(self, sym: str, n: int) -> list:
        k = (sym, n)
        if k not in self._rsi:
            self._rsi[k] = R.rsi(self.S[sym], n)
        return self._rsi[k]

    def sma(self, sym: str, n: int) -> list:
        k = (sym, n)
        if k not in self._sma:
            self._sma[k] = R.sma(self.S[sym], n)
        return self._sma[k]

    def cr(self, sym: str, n: int) -> list:
        """누적수익 CR(x,n)=close_t/close_{t-n}−1. t<n 은 None."""
        k = (sym, n)
        if k not in self._cr:
            c = self.S[sym]
            out = [None] * len(c)
            for t in range(n, len(c)):
                if c[t - n] > 0:
                    out[t] = c[t] / c[t - n] - 1.0
            self._cr[k] = out
        return self._cr[k]

    def px(self, sym: str) -> list:
        return self.S[sym]


def _pick(ind: Ind, syms, n: int, t: int, k: int, bottom: bool):
    """filter top/bottom k by RSI(n). RSI 미준비면 None 반환(→ 상위에서 현금)."""
    vals = []
    for s in syms:
        r = ind.rsi(s, n)[t]
        if r is None:
            return None
        vals.append((r, s))
    vals.sort(key=lambda x: x[0])
    chosen = [s for _, s in (vals[:k] if bottom else vals[len(vals) - k:])]
    return {s: 1.0 / len(chosen) for s in chosen}


def _ready(*vals) -> bool:
    return all(v is not None for v in vals)


# ── 전략별 목표비중 빌더 (S: 심볼→종가, dates 정렬; p: 파라미터) ──────────────
def build_b1(S, dates, p):
    """B1a FTLT (namuan canonical). 카탈로그 규칙블록 그대로."""
    ind = Ind(S)
    n = len(dates)
    W = [{} for _ in range(n)]
    rw = p["rw"]
    for t in range(n):
        spy = ind.px("SPY")[t]
        spy_sma = ind.sma("SPY", p["sma_long"])[t]
        if not _ready(spy_sma):
            continue
        if spy > spy_sma:                                   # 강세 레짐
            r_tqqq = ind.rsi("TQQQ", rw)[t]
            r_spxl = ind.rsi("SPXL", rw)[t]
            if not _ready(r_tqqq, r_spxl):
                continue
            if r_tqqq > p["t_tqqq"]:
                W[t] = {"UVXY": 1.0}
            elif r_spxl > p["t_spxl"]:
                W[t] = {"UVXY": 1.0}
            else:
                W[t] = {"TQQQ": 1.0}
        else:                                               # 약세 레짐
            r_tqqq = ind.rsi("TQQQ", rw)[t]
            r_spy = ind.rsi("SPY", rw)[t]
            r_uvxy = ind.rsi("UVXY", rw)[t]
            if not _ready(r_tqqq, r_spy, r_uvxy):
                continue
            if r_tqqq < p["t_lo"]:
                W[t] = {"TECL": 1.0}
            elif r_spy < p["t_spy_lo"]:
                W[t] = {"SPXL": 1.0}                         # SPXL(=UPRO)
            elif r_uvxy > p["t_uvxy"]:
                if r_uvxy > p["t_uvxy_hi"]:
                    W[t] = _b1_trend(ind, t, p) or {}
                else:
                    W[t] = {"UVXY": 1.0}
            else:
                W[t] = _b1_trend(ind, t, p) or {}
    return W


def _b1_trend(ind, t, p):
    tqqq = ind.px("TQQQ")[t]
    tqqq_sma = ind.sma("TQQQ", p["sma_short"])[t]
    if not _ready(tqqq_sma):
        return None
    if tqqq > tqqq_sma:
        r_sqqq = ind.rsi("SQQQ", p["rw"])[t]
        if not _ready(r_sqqq):
            return None
        return {"SQQQ": 1.0} if r_sqqq < p["t_sqqq"] else {"TQQQ": 1.0}
    return _pick(ind, ["SQQQ", "BSV"], p["rw"], t, k=1, bottom=False)   # top1


def build_b2(S, dates, p):
    """B2 Holy Grail. TQQQ 자체 200SMA."""
    ind = Ind(S)
    n = len(dates)
    W = [{} for _ in range(n)]
    rw = p["rw"]
    for t in range(n):
        tqqq = ind.px("TQQQ")[t]
        tqqq_l = ind.sma("TQQQ", p["sma_long"])[t]
        if not _ready(tqqq_l):
            continue
        if tqqq > tqqq_l:
            r_tqqq = ind.rsi("TQQQ", rw)[t]
            if not _ready(r_tqqq):
                continue
            W[t] = {"UVXY": 1.0} if r_tqqq > p["t_tqqq"] else {"TQQQ": 1.0}
        else:
            r_tqqq = ind.rsi("TQQQ", rw)[t]
            r_soxl = ind.rsi("SOXL", rw)[t]
            tqqq_s = ind.sma("TQQQ", p["sma_short"])[t]
            if not _ready(r_tqqq, r_soxl, tqqq_s):
                continue
            if r_tqqq < p["t_lo"]:
                W[t] = {"TECL": 1.0}
            elif r_soxl < p["t_soxl"]:
                W[t] = {"SOXL": 1.0}
            elif tqqq < tqqq_s:
                W[t] = _pick(ind, ["SQQQ", "BSV"], rw, t, k=1, bottom=False) or {}
            else:
                W[t] = {"TQQQ": 1.0}
    return W


def build_b3(S, dates, p):
    """B3 Beta Baller v2.2 (EDN 파싱 원문 그대로)."""
    ind = Ind(S)
    n = len(dates)
    W = [{} for _ in range(n)]
    for t in range(n):
        w = _b3_decide(ind, t, p)
        W[t] = w or {}
    return W


def _b3_decide(ind, t, p):
    # 공개 EDN 임계는 고정(75/27/79). p 는 이웃 평탄성용 perturbation 만 받는다(재튜닝 아님).
    t_hi = p.get("t_spy_hi", 75)
    t_ex = p.get("t_spy_lo", 27)
    t_tqqq = p.get("t_tqqq", 79)
    r_bil = ind.rsi("BIL", 7)[t]
    r_ief = ind.rsi("IEF", 7)[t]
    if not _ready(r_bil, r_ief):
        return None
    if r_bil < r_ief:                                       # 위험선호
        r_spy6 = ind.rsi("SPY", 6)[t]
        if not _ready(r_spy6):
            return None
        if r_spy6 > t_hi:
            return _pick(ind, ["UVXY", "VIXY"], 13, t, k=1, bottom=True)
        return {"SOXL": 0.5, "TECS": 0.5}                   # EDN 원문(equal-weight)
    # else: T-bill ≥ 중기채
    r_spy6 = ind.rsi("SPY", 6)[t]
    if not _ready(r_spy6):
        return None
    if r_spy6 < t_ex:                                       # 극단 과매도
        r_shy = ind.rsi("SHY", 10)[t]
        r_hibl = ind.rsi("HIBL", 10)[t]
        if not _ready(r_shy, r_hibl):
            return None
        if r_shy < r_hibl:
            return _pick(ind, ["SOXS", "SQQQ"], 7, t, k=1, bottom=True)
        return _pick(ind, ["SOXL", "TECL"], 7, t, k=1, bottom=True)
    # "SMH for the Long Term"
    spy = ind.px("SPY")[t]
    spy_sma = ind.sma("SPY", 200)[t]
    if not _ready(spy_sma):
        return None
    if spy > spy_sma:
        r_tqqq = ind.rsi("TQQQ", 10)[t]
        if not _ready(r_tqqq):
            return None
        if r_tqqq > t_tqqq:
            return _pick(ind, ["UVXY", "VIXY"], 13, t, k=1, bottom=True)
        return _pick(ind, ["SHV", "FAS", "TQQQ", "UPRO", "TECL"], 21, t, k=1, bottom=True)
    # 약세
    r_qqq = ind.rsi("QQQ", 10)[t]
    r_spy = ind.rsi("SPY", 10)[t]
    if not _ready(r_qqq, r_spy):
        return None
    if r_qqq < 30:
        return {"SHY": 1.0}
    if r_spy < 30:
        return _pick(ind, ["SPXL", "SHY"], 10, t, k=1, bottom=True)
    smh = ind.px("SMH")[t]
    smh_sma = ind.sma("SMH", 20)[t]
    if not _ready(smh_sma):
        return None
    if smh > smh_sma:
        r_smh = ind.rsi("SMH", 10)[t]
        if not _ready(r_smh):
            return None
        if r_smh > 50:
            return _pick(ind, ["SOXS", "UUP", "SHY"], 12, t, k=2, bottom=True)
        return {"SOXL": 1.0}
    return _pick(ind, ["SOXS", "BSV"], 10, t, k=1, bottom=False)     # top1


def build_b5(S, dates, p):
    """B5 simple RSI(TQQQ,10)>79 → UVXY else TQQQ. calm 파라미터 defense_sym."""
    ind = Ind(S)
    n = len(dates)
    W = [{} for _ in range(n)]
    defense = p.get("defense", "UVXY")
    for t in range(n):
        r = ind.rsi("TQQQ", p["rw"])[t]
        if not _ready(r):
            continue
        W[t] = {defense: 1.0} if r > p["t_tqqq"] else {"TQQQ": 1.0}
    return W


def build_b6(S, dates, p):
    """B6 IBS 평균회귀 (QQQ 신호 OHLC → TQQQ). 상태보유(진입/청산)."""
    ind = Ind(S)
    n = len(dates)
    W = [{} for _ in range(n)]
    close = S["QQQ"]
    high = S["QQQ|H"]
    low = S["QQQ|L"]
    sma200 = ind.sma("QQQ", 200)
    use_filter = p.get("trend_filter", False)
    in_pos = False
    for t in range(n):
        rng = high[t] - low[t]
        ibs = (close[t] - low[t]) / rng if rng > 0 else 0.5
        prev_high = high[t - 1] if t > 0 else high[t]
        if in_pos:
            if ibs > p["exit"] or close[t] > prev_high:
                in_pos = False
        else:
            ok_trend = (not use_filter) or (sma200[t] is not None and close[t] > sma200[t])
            if ibs < p["entry"] and ok_trend:
                in_pos = True
        W[t] = {"TQQQ": 1.0} if in_pos else {}
    return W


def build_b7(S, dates, p):
    """B7 Connors RSI(2) (QQQ 신호 → TQQQ). 상태보유."""
    ind = Ind(S)
    n = len(dates)
    W = [{} for _ in range(n)]
    close = S["QQQ"]
    sma_long = ind.sma("QQQ", p["sma_long"])
    sma_exit = ind.sma("QQQ", p["sma_exit"])
    rsi2 = ind.rsi("QQQ", p["rw"])
    in_pos = False
    for t in range(n):
        if not _ready(sma_long[t], rsi2[t]):
            W[t] = {"TQQQ": 1.0} if in_pos else {}
            continue
        if in_pos:
            if (sma_exit[t] is not None and close[t] > sma_exit[t]) or rsi2[t] > p["t_exit"]:
                in_pos = False
        else:
            if close[t] > sma_long[t] and rsi2[t] < p["t_entry"]:
                in_pos = True
        W[t] = {"TQQQ": 1.0} if in_pos else {}
    return W


def build_e1(S, dates, p):
    """E1 QQQ 200SMA 상시투자 TQQQ↔SQQQ (통제군)."""
    ind = Ind(S)
    n = len(dates)
    W = [{} for _ in range(n)]
    close = S["QQQ"]
    sma = ind.sma("QQQ", p["sma_long"])
    for t in range(n):
        if not _ready(sma[t]):
            continue
        W[t] = {"TQQQ": 1.0} if close[t] > sma[t] else {"SQQQ": 1.0}
    return W


def build_e3(S, dates, p):
    """E3 SOXL/SOXS 급등 역추세. BOXX→BIL 대용(플래그)."""
    ind = Ind(S)
    n = len(dates)
    W = [{} for _ in range(n)]
    cash = p.get("cash", "BIL")
    for t in range(n):
        cr_soxl = ind.cr("SOXL", p["cr_win"])[t]
        cr_soxs = ind.cr("SOXS", p["cr_win"])[t]
        r_soxl = ind.rsi("SOXL", p["rw"])[t]
        if not _ready(cr_soxl, cr_soxs, r_soxl):
            continue
        if cr_soxl > p["cr_up"]:
            W[t] = {"SOXS": 1.0}
        elif cr_soxs > p["cr_dn"]:
            W[t] = {"SOXL": 1.0}
        elif r_soxl < p["t_lo"]:
            W[t] = {"TECL": 1.0}
        else:
            W[t] = {cash: 1.0}
    return W


# ── 전략 레지스트리 ──────────────────────────────────────────────────────────
def _wf(x, f):
    return max(2, int(round(x * f)))


def _nb_rsi_thr(base_p, keys):
    """RSI 임계 이웃: 각 임계 ±3, ±6 (카탈로그 79/80/31/30 경계 민감도)."""
    out = []
    for kkey in keys:
        for d in (-6, -3, 3, 6):
            p = dict(base_p)
            p[kkey] = base_p[kkey] + d
            out.append((f"{kkey}{d:+d}", p))
    return out


def _nb_win(base_p, keys):
    """윈도우 이웃: ×0.8, ×1.2 (±20%)."""
    out = []
    for kkey in keys:
        for f in (0.8, 1.2):
            p = dict(base_p)
            p[kkey] = _wf(base_p[kkey], f)
            out.append((f"{kkey}x{f}", p))
    return out


@dataclass
class Strat:
    sid: str
    name: str
    build: object
    symbols: list           # 정렬·정합에 필요한 전 심볼(신호+체결)
    base_p: dict
    neighbors: list         # [(label, p)]
    pub_oos: date | None
    ohlc: str | None = None  # OHLC 신호 심볼(예 "QQQ") 또는 None
    note: str = ""


def _b1_neighbors(p):
    return (_nb_rsi_thr(p, ["t_tqqq", "t_lo"]) + _nb_win(p, ["sma_long", "rw"]))


def _b2_neighbors(p):
    return (_nb_rsi_thr(p, ["t_tqqq", "t_soxl"]) + _nb_win(p, ["sma_long", "rw"]))


def _b3_neighbors(p):
    return _nb_rsi_thr(p, ["t_spy_hi", "t_spy_lo", "t_tqqq"])


def _b5_neighbors(p):
    return _nb_rsi_thr(p, ["t_tqqq"]) + _nb_win(p, ["rw"]) + [("uvxy2bil", {**p, "defense": "BIL"})]


def _b6_neighbors(p):
    out = []
    for d in (-0.03, 0.03):
        out.append((f"entry{d:+.2f}", {**p, "entry": round(p["entry"] + d, 3)}))
        out.append((f"exit{-d:+.2f}", {**p, "exit": round(p["exit"] - d, 3)}))
    out.append(("trend_filter", {**p, "trend_filter": True}))
    return out


def _b7_neighbors(p):
    return (_nb_rsi_thr(p, ["t_entry", "t_exit"]) + _nb_win(p, ["sma_long"]))


def _e1_neighbors(p):
    return _nb_win(p, ["sma_long"]) + [("sma150", {**p, "sma_long": 150}),
                                       ("sma250", {**p, "sma_long": 250})]


def _e3_neighbors(p):
    out = _nb_win(p, ["cr_win"]) + _nb_rsi_thr(p, ["t_lo"])
    for f in (0.8, 1.2):
        out.append((f"cr_up x{f}", {**p, "cr_up": round(p["cr_up"] * f, 3)}))
    return out


def registry():
    b1p = dict(rw=10, sma_long=200, sma_short=20, t_tqqq=79, t_spxl=80,
               t_lo=31, t_spy_lo=30, t_uvxy=74, t_uvxy_hi=84, t_sqqq=31)
    b2p = dict(rw=10, sma_long=200, sma_short=20, t_tqqq=79, t_lo=31, t_soxl=30)
    b3p = dict(t_spy_hi=75, t_spy_lo=27, t_tqqq=79)   # 공개 EDN 임계(이웃 평탄성용만 노출)
    b5p = dict(rw=10, t_tqqq=79, defense="UVXY")
    b6p = dict(entry=0.2, exit=0.8, trend_filter=False)
    b7p = dict(rw=2, sma_long=200, sma_exit=5, t_entry=10, t_exit=80)
    e1p = dict(sma_long=200)
    e3p = dict(cr_win=10, rw=10, cr_up=0.31, cr_dn=0.25, t_lo=31, cash="BIL")
    return _prefix([
        Strat("b1_ftlt", "B1 TQQQ For The Long Term (FTLT, namuan canonical)", build_b1,
              ["SPY", "TQQQ", "SPXL", "UVXY", "TECL", "SQQQ", "BSV"], b1p,
              _b1_neighbors(b1p), date(2023, 1, 1),
              note="SPXL(=UPRO). UVXY 데이터 2018-09 시작→창 단축."),
        Strat("b2_holygrail", "B2 The Holy Grail", build_b2,
              ["TQQQ", "UVXY", "TECL", "SOXL", "SQQQ", "BSV"], b2p,
              _b2_neighbors(b2p), date(2022, 8, 1),
              note="창 길이 10 가정(카탈로그). UVXY 2018-09."),
        Strat("b3_betaballer", "B3 Beta Baller v2.2 (SMH mod, EDN)", build_b3,
              ["BIL", "IEF", "SPY", "UVXY", "VIXY", "SOXL", "TECS", "SHY", "HIBL",
               "SOXS", "SQQQ", "TECL", "TQQQ", "SHV", "FAS", "UPRO", "QQQ", "SPXL",
               "SMH", "UUP", "BSV"], b3p,
              _b3_neighbors(b3p), date(2022, 11, 1),
              note="HIBL 2019-11 상장→설계구간 ~2.1y. EDN 원문(SOXL/TECS)."),
        Strat("b5_simple", "B5 Simple TQQQ RSI(10)>79 → UVXY (기준선)", build_b5,
              ["TQQQ", "UVXY", "BIL"], b5p, _b5_neighbors(b5p), date(2024, 10, 1),
              note="B1/B2 ablation 기준선."),
        Strat("b6_ibs", "B6 IBS 평균회귀 (QQQ 신호 → TQQQ)", build_b6,
              ["TQQQ", "QQQ"], b6p, _b6_neighbors(b6p), date(2014, 5, 1),
              ohlc="QQQ", note="종가직전 체결 전제. 공개(2014)<데이터(2016-09)→전구간 OOS."),
        Strat("b7_connors", "B7 Connors RSI(2) (QQQ 신호 → TQQQ)", build_b7,
              ["TQQQ", "QQQ"], b7p, _b7_neighbors(b7p), date(2009, 1, 1),
              note="공개(2009)<데이터→전구간 OOS. 손절 없음(3x 위험)."),
        Strat("e1_control", "E1 QQQ 200SMA 상시투자 TQQQ↔SQQQ (통제군)", build_e1,
              ["QQQ", "TQQQ", "SQQQ"], e1p, _e1_neighbors(e1p), None,
              note="대조군(인버스가 가치를 더하나)."),
        Strat("e3_soxlsoxs", "E3 SOXL/SOXS 10일 급등 역추세", build_e3,
              ["SOXL", "SOXS", "TECL", "BIL"], e3p, _e3_neighbors(e3p), date(2025, 3, 11),
              note="BOXX→BIL 대용(BOXX 2022-12 상장)."),
    ])


def _prefix(strats, pfx="c12_"):
    for s in strats:
        if not s.sid.startswith(pfx):
            s.sid = pfx + s.sid
    return strats


# ── 데이터 정합 ──────────────────────────────────────────────────────────────
def load_series(symbols, ohlc=None):
    """심볼 집합(+OHLC 신호)을 공통 거래일로 정합해 (dates, S, opens, closes) 반환."""
    need = list(dict.fromkeys(list(symbols) + (["QQQ"] if ohlc else [])))
    panel = hd.align_panel(hd.load_panel(need))
    any_sym = next(iter(panel))
    dates = [c.dt for c in panel[any_sym]]
    closes = {s: [c.close for c in panel[s]] for s in panel}
    opens = {s: [c.open for c in panel[s]] for s in panel}
    S = dict(closes)
    if ohlc:
        S[f"{ohlc}|H"] = [c.high for c in panel[ohlc]]
        S[f"{ohlc}|L"] = [c.low for c in panel[ohlc]]
    return dates, S, opens, closes


def bh_cagr(closes, dates, lo, hi):
    days = max((dates[hi] - dates[lo]).days, 1)
    if closes[lo] <= 0:
        return float("nan")
    return (closes[hi] / closes[lo]) ** (365.0 / days) - 1.0


# ── 단일 전략 평가 ───────────────────────────────────────────────────────────
def run_strategy_weights(closes, dates, opens, weights, cost, *, exec_lag=1,
                         fee_fn=None, cash_rate=None):
    return R.run_weights(closes, dates, weights, exec_lag=exec_lag, cost=cost,
                         cash_rate=cash_rate, fee_fn=fee_fn, start_equity=1.0)


def split_cagrs(net_stream, ret_dates):
    di = [i for i, d in enumerate(ret_dates) if d <= DESIGN_END]
    hi = [i for i, d in enumerate(ret_dates) if d > DESIGN_END]
    out = {}
    for tag, idxs in (("design", di), ("holdout", hi)):
        if len(idxs) < 2:
            out[tag] = {"cagr": float("nan"), "n": len(idxs)}
            continue
        sub = [net_stream[i] for i in idxs]
        dsub = [ret_dates[i] for i in idxs]
        m = GE.unit_capital_metrics(sub, dates=dsub)
        out[tag] = {"cagr": m.get("cagr"), "sharpe": m.get("sr_annual"),
                    "mdd": m.get("max_drawdown"), "n": len(idxs)}
    return out


def evaluate(strat: Strat, *, ledger=LEDGER, do_log=True, fast=False):
    dates, S, opens, closes = load_series(strat.symbols, ohlc=strat.ohlc)
    n = len(dates)
    ret_dates = dates[1:]
    cash_rate = R.cash_rate_from_prices(closes["BIL"]) if "BIL" in closes else None

    # 벤치 B&H 구간 인덱스(같은 거래일)
    di = [i for i in range(n) if dates[i] <= DESIGN_END]
    ho = [i for i in range(n) if dates[i] > DESIGN_END]
    # 벤치 QQQ·TQQQ 종가(패널에 없으면 별도 로드해 같은 거래일에 정합)
    def _bench_closes(sym):
        if sym in closes:
            return closes[sym]
        bc = hd.load_symbol(sym)
        bmap = {c.dt: c.close for c in bc}
        return [bmap.get(d, float("nan")) for d in dates]
    qqq_closes = _bench_closes("QQQ")
    tqqq_closes = _bench_closes("TQQQ")

    def bh2(cl, idxs):
        if cl is None or len(idxs) < 2:
            return float("nan")
        return bh_cagr(cl, dates, idxs[0], idxs[-1])

    bench = {
        "qqq": {"full": bh2(qqq_closes, list(range(n))),
                "design": bh2(qqq_closes, di), "holdout": bh2(qqq_closes, ho)},
        "tqqq": {"full": bh2(tqqq_closes, list(range(n))),
                 "design": bh2(tqqq_closes, di), "holdout": bh2(tqqq_closes, ho)},
    }

    base_cost = build_cost(1.0)
    weights = strat.build(S, dates, strat.base_p)

    # primary(t+1 종가)
    res = run_strategy_weights(closes, dates, opens, weights, base_cost,
                               exec_lag=1, cash_rate=cash_rate)
    net = res.net_stream()
    gross = res.gross_stream()
    full = GE.unit_capital_metrics(net, dates=ret_dates)
    full_gross = GE.unit_capital_metrics(gross, dates=ret_dates)
    splits = split_cagrs(net, ret_dates)
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)

    # 2× 비용
    res2 = run_strategy_weights(closes, dates, opens, weights, build_cost(2.0),
                                exec_lag=1, cash_rate=cash_rate)
    splits2 = split_cagrs(res2.net_stream(), ret_dates)
    full2 = GE.unit_capital_metrics(res2.net_stream(), dates=ret_dates)

    # micro(매수 무료)
    res_micro = run_strategy_weights(closes, dates, opens, weights, base_cost,
                                     exec_lag=1, fee_fn=micro_fee_fn(), cash_rate=cash_rate)
    full_micro = GE.unit_capital_metrics(res_micro.net_stream(), dates=ret_dates)

    # same-close/MOC(낙관)
    res_moc = run_strategy_weights(closes, dates, opens, weights, base_cost,
                                   exec_lag=0, cash_rate=cash_rate)
    full_moc = GE.unit_capital_metrics(res_moc.net_stream(), dates=ret_dates)

    # 1x 섀도
    sh_w = shadow_weights(weights)
    sh_panel = hd.align_panel(hd.load_panel(SHADOW_UNIVERSE))
    sh_dates = [c.dt for c in sh_panel["QQQ"]]
    dmap = {d: i for i, d in enumerate(sh_dates)}
    keep = [i for i, d in enumerate(dates) if d in dmap]
    sh_closes = {s: [sh_panel[s][dmap[dates[i]]].close for i in keep] for s in SHADOW_UNIVERSE}
    sh_d = [dates[i] for i in keep]
    sh_weights = [sh_w[i] for i in keep]
    sh_cash = R.cash_rate_from_prices([closes["BIL"][i] for i in keep]) if "BIL" in closes else None
    res_sh = R.run_weights(sh_closes, sh_d, sh_weights, exec_lag=1,
                           cost=build_cost(1.0), cash_rate=sh_cash, start_equity=1.0)
    sh_net = res_sh.net_stream()
    sh_ret_dates = sh_d[1:]
    full_sh = GE.unit_capital_metrics(sh_net, dates=sh_ret_dates)
    splits_sh = split_cagrs(sh_net, sh_ret_dates)
    sh_qqq = {"full": bh2([sh_panel["QQQ"][dmap[d]].close for d in sh_d], list(range(len(sh_d)))),
              "design": None, "holdout": None}
    sdi = [i for i, d in enumerate(sh_d) if d <= DESIGN_END]
    sho = [i for i, d in enumerate(sh_d) if d > DESIGN_END]
    sh_qqq_cl = [sh_panel["QQQ"][dmap[d]].close for d in sh_d]
    sh_qqq["design"] = bh_cagr(sh_qqq_cl, sh_d, sdi[0], sdi[-1]) if len(sdi) > 1 else float("nan")
    sh_qqq["holdout"] = bh_cagr(sh_qqq_cl, sh_d, sho[0], sho[-1]) if len(sho) > 1 else float("nan")

    # 이웃 평탄성(±20~50% / RSI ±3·±6): FULL CAGR vs QQQ FULL
    neigh = []
    n_beat = 0
    for label, p in strat.neighbors:
        try:
            nb_w = strat.build(S, dates, p)
        except Exception as e:  # noqa: BLE001
            neigh.append({"label": label, "error": str(e)}); continue
        nb_res = run_strategy_weights(closes, dates, opens, nb_w, base_cost,
                                      exec_lag=1, cash_rate=cash_rate)
        nb_net = nb_res.net_stream()
        nb_m = GE.unit_capital_metrics(nb_net, dates=ret_dates)
        beat = (nb_m.get("cagr") is not None and not math.isnan(nb_m["cagr"])
                and nb_m["cagr"] > bench["qqq"]["full"])
        n_beat += int(beat)
        neigh.append({"label": label, "cagr": nb_m.get("cagr"),
                      "mdd": nb_m.get("max_drawdown"), "beat_qqq": beat})
        if do_log:
            GE.log_evaluation(strat.sid, {"neighbor": label, **{k: p[k] for k in p
                              if isinstance(p[k], (int, float, str, bool))}},
                              LANE, "design", nb_m, window=(ret_dates[0], ret_dates[-1]),
                              universe=strat.symbols, ledger_path=ledger)
    n_neigh = sum(1 for x in neigh if "cagr" in x)
    neigh_frac = (n_beat / n_neigh) if n_neigh else 0.0

    # post-publication OOS
    pub = None
    if strat.pub_oos is not None:
        pidx = [i for i, d in enumerate(ret_dates) if d > strat.pub_oos]
        if len(pidx) > 2:
            sub = [net[i] for i in pidx]
            dsub = [ret_dates[i] for i in pidx]
            pm = GE.unit_capital_metrics(sub, dates=dsub)
            # QQQ/TQQQ B&H 동일창
            fidx = [i for i in range(n) if dates[i] >= strat.pub_oos]
            pub = {"start": strat.pub_oos.isoformat(), "n": len(pidx),
                   "cagr": pm.get("cagr"), "mdd": pm.get("max_drawdown"),
                   "sharpe": pm.get("sr_annual"),
                   "qqq": bh2(qqq_closes, fidx), "tqqq": bh2(tqqq_closes, fidx)}

    # ── DSR(보고만): 아이디어 원장 시도 SR 로 ──
    dsr = None
    if do_log:
        sr_trials = gate.ledger_trial_sharpes(ledger, strat.sid) or None
        dm = GE.unit_capital_metrics([net[i] for i in range(len(net)) if ret_dates[i] <= DESIGN_END],
                                     dates=[d for d in ret_dates if d <= DESIGN_END],
                                     sr_trials=sr_trials, n_eff=(len(sr_trials) if sr_trials else None))
        dsr = dm.get("dsr")

    # ── 헤드라인 원장 적재(설계 1회 + 홀드아웃 peek-once) ──
    if do_log:
        di_idx = [i for i, d in enumerate(ret_dates) if d <= DESIGN_END]
        hi_idx = [i for i, d in enumerate(ret_dates) if d > DESIGN_END]
        if len(di_idx) > 1:
            um_d = GE.unit_capital_metrics([net[i] for i in di_idx],
                                           dates=[ret_dates[i] for i in di_idx])
            GE.log_evaluation(strat.sid, {"headline": True, **_scalar(strat.base_p)},
                              LANE, "design", um_d,
                              window=(ret_dates[di_idx[0]], ret_dates[di_idx[-1]]),
                              universe=strat.symbols, ledger_path=ledger)
        if len(hi_idx) > 1 and not gate.already_peeked(ledger, strat.sid):
            um_h = GE.unit_capital_metrics([net[i] for i in hi_idx],
                                           dates=[ret_dates[i] for i in hi_idx])
            try:
                GE.log_evaluation(strat.sid, {"headline": True, **_scalar(strat.base_p)},
                                  LANE, "holdout", um_h,
                                  window=(ret_dates[hi_idx[0]], ret_dates[hi_idx[-1]]),
                                  universe=strat.symbols, ledger_path=ledger)
            except gate.PeekOnceError:
                pass

    verdict = lane_a_verdict(splits, splits2, bench, neigh_frac, full.get("max_drawdown"))

    return {
        "sid": strat.sid, "name": strat.name, "note": strat.note,
        "first": dates[0].isoformat(), "last": dates[-1].isoformat(), "n_days": n,
        "years": years,
        "full": {"cagr": full.get("cagr"), "total_return": res.final_equity - 1.0,
                 "mdd": full.get("max_drawdown"), "sharpe": full.get("sr_annual"),
                 "cvar5": full.get("cvar5"), "calmar": full.get("calmar")},
        "trades_per_yr": res.trade_count / years,
        "turnover_per_yr": sum(res.turnover) / years,
        "cost_drag_pp": (full_gross.get("cagr") - full.get("cagr")) * 100
        if full_gross.get("cagr") is not None else None,
        "total_cost": res.total_cost,
        "splits": splits, "bench": bench,
        "cost2x": {"full_cagr": full2.get("cagr"), "splits": splits2},
        "micro": {"full_cagr": full_micro.get("cagr")},
        "moc": {"full_cagr": full_moc.get("cagr")},
        "shadow": {"full_cagr": full_sh.get("cagr"), "mdd": full_sh.get("max_drawdown"),
                   "splits": splits_sh, "qqq": sh_qqq},
        "neighbors": neigh, "neigh_frac": neigh_frac, "n_neigh": n_neigh,
        "pub_oos": pub, "dsr": dsr, "verdict": verdict,
    }


def _scalar(p):
    return {k: v for k, v in p.items() if isinstance(v, (int, float, str, bool))}


def lane_a_verdict(splits, splits2, bench, neigh_frac, full_mdd):
    """부록 v3 Lane A 판정. (a) 설계·홀드아웃 CAGR>QQQ, (b) 이웃 ≥60% beat, (c) 2×비용에서 (a) 유지.
    파산가드: 전표본 MDD ≤ −95% → FAIL."""
    if full_mdd is not None and full_mdd <= -0.95:
        return "FAIL(파산가드 MDD≤−95%)"
    dc, hc = splits.get("design", {}).get("cagr"), splits.get("holdout", {}).get("cagr")
    qd, qh = bench["qqq"]["design"], bench["qqq"]["holdout"]
    dc2, hc2 = splits2.get("design", {}).get("cagr"), splits2.get("holdout", {}).get("cagr")

    def gt(a, b):
        return a is not None and b is not None and not math.isnan(a) and not math.isnan(b) and a > b
    a = gt(dc, qd) and gt(hc, qh)
    c = gt(dc2, qd) and gt(hc2, qh)
    b = neigh_frac >= 0.60
    if a and b and c:
        return "PASS"
    if a and (b or c):
        return "CONDITIONAL"
    return "FAIL"


# ── 리포트 ───────────────────────────────────────────────────────────────────
def _pc(v, nd=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:+.{nd}f}%"


def _f(v, nd=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:.{nd}f}"


PREREG = """# Cycle 12 — Composer/커뮤니티 공격형 전략 (Lane A)

> 사전등록 2026-09-28 · 결과 보기 전 확정 · 코드 변경 없음(src 미수정) · 레인 A(부록 v3)

## 사전등록 (가설·규칙·파라미터)

**목적함수:** 비용 반영 CAGR(단위자본). Sharpe/MDD 는 보고만(탈락 사유 아님). 파산가드: 전표본
단위 MDD ≤ −95% → FAIL.

**Lane A PASS:** (a) 설계(≤2021-12)·홀드아웃(2022+) **모두** CAGR>QQQ B&H, (b) 이웃(±20~50%;
RSI 임계는 ±3/±6) ≥60% 가 CAGR>QQQ, (c) 비용 2×에서도 (a) 유지, (d) 사전등록 1설정+이웃만·원장기록.
DSR/SPA 보고만. 커뮤니티 전략은 공개 규칙 그대로 1회(재튜닝 없음), 공개일↑=진짜 OOS.

**체결:** 신호 close t → t+1 종가(primary). same-close/MOC(exec_lag=0) 별도. **비용:** 10bp/side +
반호가(레버 2bp·UVXY/VIXY 5bp·ETF 1bp) + 슬리피지 5bp. 2× 스트레스 + ≤$10 분할 micro(매수무료) + 1x 섀도.

**전략(카탈로그 §B·§E 규칙블록 그대로):** B1 FTLT(namuan canonical), B2 Holy Grail, B3 Beta Baller
v2.2(EDN), B5 simple RSI→UVXY, B6 IBS→TQQQ, B7 Connors RSI2→TQQQ, E1 TQQQ↔SQQQ 200SMA(통제군),
E3 SOXL/SOXS 10일 역추세. 규칙·파라미터는 experiments/c12_composer.py registry() 에 고정.

**데이터 한계(플래그):** UVXY Nasdaq 2018-09~ (B1/B2/B5 설계창 단축), HIBL 2019-11~ (B3 설계 ~2.1y),
BOXX 2022-12~ → E3 는 BOXX 대신 BIL 대용. UVXY 2018-02-27 2x→1.5x 구조변경은 실가격에 자동반영(이전 구간 없음).
"""


def write_report(results, meta):
    L = [PREREG.rstrip(), "", "<!-- RESULTS_BELOW -->", ""]
    L.append(f"> 실행 {meta['generated']} · 원장 {meta['ledger']} · 전략 {len(results)}개")
    L.append("")
    L.append("## 헤드라인 (primary t+1 종가, 기본비용)")
    L.append("| 전략 | 구간 | CAGR | 총수익 | MDD | Sharpe | 거래/년 | 회전/년 | 비용drag | "
             "설계 CAGR (QQQ/TQQQ) | 홀드 CAGR (QQQ/TQQQ) | 이웃beat | 2×비용 | micro | MOC | 1x섀도 | DSR | 판정 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---|")
    for r in results:
        s = r["splits"]; b = r["bench"]
        d_cell = f"{_pc(s['design']['cagr'])} ({_pc(b['qqq']['design'])}/{_pc(b['tqqq']['design'])})"
        h_cell = f"{_pc(s['holdout']['cagr'])} ({_pc(b['qqq']['holdout'])}/{_pc(b['tqqq']['holdout'])})"
        L.append(
            f"| {r['sid']} | {r['first'][:7]}…{r['last'][:7]} | {_pc(r['full']['cagr'])} | "
            f"{_pc(r['full']['total_return'],0)} | {_pc(r['full']['mdd'])} | {_f(r['full']['sharpe'])} | "
            f"{_f(r['trades_per_yr'],0)} | {_f(r['turnover_per_yr'],1)} | {_f(r['cost_drag_pp'],1)}pp | "
            f"{d_cell} | {h_cell} | {r['neigh_frac']*100:.0f}%({r['n_neigh']}) | "
            f"{_pc(r['cost2x']['full_cagr'])} | {_pc(r['micro']['full_cagr'])} | "
            f"{_pc(r['moc']['full_cagr'])} | {_pc(r['shadow']['full_cagr'])} | {_f(r['dsr'])} | "
            f"**{r['verdict']}** |")
    L.append("")
    L.append(f"> QQQ B&H full CAGR ≈ {_pc(results[0]['bench']['qqq']['full'])} · "
             f"TQQQ B&H full CAGR ≈ {_pc(results[0]['bench']['tqqq']['full'])} "
             f"(전략별 거래일 상이 → 각 행의 벤치는 해당 전략 창 기준).")
    L.append("")

    # post-publication OOS
    L.append("## Post-publication OOS (공개일↑ = 진짜 OOS) & 1x 섀도 분할")
    L.append("| 전략 | 공개일 | OOS n | OOS CAGR | OOS MDD | vs QQQ | vs TQQQ | 섀도 설계/홀드 CAGR (QQQ) |")
    L.append("|---|---|---:|---:|---:|---:|---:|---|")
    for r in results:
        p = r["pub_oos"]
        sh = r["shadow"]["splits"]; shq = r["shadow"]["qqq"]
        shadow_cell = (f"{_pc(sh['design']['cagr'])}/{_pc(sh['holdout']['cagr'])} "
                       f"({_pc(shq['design'])}/{_pc(shq['holdout'])})")
        if p:
            L.append(f"| {r['sid']} | {p['start']} | {p['n']} | {_pc(p['cagr'])} | {_pc(p['mdd'])} | "
                     f"{_pc(p['qqq'])} | {_pc(p['tqqq'])} | {shadow_cell} |")
        else:
            L.append(f"| {r['sid']} | (통제군) | — | — | — | — | — | {shadow_cell} |")
    L.append("")

    # 이웃 상세
    L.append("## 이웃 평탄성 상세 (FULL CAGR, QQQ FULL 대비)")
    for r in results:
        beats = [x for x in r["neighbors"] if x.get("beat_qqq")]
        L.append(f"- **{r['sid']}** ({r['neigh_frac']*100:.0f}% beat, {len(beats)}/{r['n_neigh']}): "
                 + ", ".join(f"{x['label']}={_pc(x.get('cagr'))}" for x in r["neighbors"] if "cagr" in x))
    L.append("")

    L.append("## 판정 종합 및 정직한 해석")
    L.append(_verdict_prose(results))
    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            L[0] = prev.split("<!-- RESULTS_BELOW -->")[0].rstrip()
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def _verdict_prose(results):
    lines = []
    passes = [r["sid"] for r in results if r["verdict"] == "PASS"]
    conds = [r["sid"] for r in results if r["verdict"].startswith("CONDITIONAL")]
    fails = [r["sid"] for r in results if r["verdict"].startswith("FAIL")]
    lines.append(f"**판정 요약.** PASS: {passes or '없음'} · CONDITIONAL: {conds or '없음'} · "
                 f"FAIL: {fails or '없음'}. (판정 규칙 = 부록 v3 Lane A, 위 사전등록.)")
    lines.append("")
    lines.append(
        "**정직한 해석.** (1) 이 전략들은 대부분 2018~2021 강세장 + 2023~ AI 랠리 표본에 크게 의존한다 "
        "— TQQQ B&H 자체가 홀드아웃에서 매우 높아, 'QQQ 를 이겼나'는 통과해도 'TQQQ 를 이겼나'는 대부분 "
        "실패한다(레버리지 위험을 능동신호가 정당화하는지의 진짜 시험). (2) UVXY 스파이크(2018-02, 2020-03) "
        "포착이 B1/B2/B5 수익의 큰 몫이며, UVXY 데이터가 2018-09 부터라 2018-02 이벤트는 표본 밖 → 설계 "
        "CAGR 이 관대할 수 있다. (3) B3 는 RSI(BIL,7) 등 near-constant 시계열의 RSI 에 의존해 수치적으로 "
        "불안정하고(카탈로그 최다 오버피팅 비판) 설계창이 ~2.1y 로 짧다. (4) MDD 는 대부분 −50~−80% 로 "
        "README §5.1 하드캡을 크게 초과하나, Lane A 는 파산가드(−95%)만 적용하므로 '통과'라도 실채택은 "
        "소액 슬리브 + 포워드 페이퍼 3개월(부록 v3-4) 후에만. (5) 1x 섀도(규제상 한국 개인의 현실 실행가능판)는 "
        "3x/UVXY 를 QQQ/SMH/PSQ/현금으로 대체하므로 CAGR 이 크게 낮아진다 — 레버리지 접근권이 이 전략들의 "
        "핵심 전제임을 보여준다. (6) DSR 은 보고만(Lane A). MOC(same-close) 와 t+1 종가 차이가 크면 "
        "Composer 식 마감직전 체결 의존(과최적 신호)일 수 있다.")
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    import datetime as _dt
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--only", default=None, help="쉼표구분 sid 필터(예 b1_ftlt,e1_control)")
    ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()

    do_log = not args.no_ledger
    only = set(args.only.split(",")) if args.only else None
    strategies = [s for s in registry() if (only is None or s.sid in only)]
    results = []
    for strat in strategies:
        print(f"[c12] {strat.sid} …", flush=True)
        r = evaluate(strat, ledger=args.ledger, do_log=do_log, fast=args.fast)
        results.append(r)
        print(f"      CAGR={_pc(r['full']['cagr'])} MDD={_pc(r['full']['mdd'])} "
              f"설계={_pc(r['splits']['design']['cagr'])} 홀드={_pc(r['splits']['holdout']['cagr'])} "
              f"이웃={r['neigh_frac']*100:.0f}% → {r['verdict']}", flush=True)

    meta = {"generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "ledger": args.ledger if do_log else "(no-ledger)"}
    RESULTS_JSON.write_text(json.dumps({"meta": meta, "results": results}, default=str,
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_report:
        write_report(results, meta)
        print(f"[c12] report → {REPORT}", flush=True)
    print(f"[c12] results → {RESULTS_JSON}", flush=True)


if __name__ == "__main__":
    main()
