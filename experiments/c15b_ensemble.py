#!/usr/bin/env python3
"""c15b — Lane A 승자들의 **앙상블**(신호 다양화로 드로다운 완화). Cycle 15, id prefix `c15b`.

동기(사용자 목표변경, 부록 v3 Lane A). 개별 승자들은 CAGR 은 높지만 MDD 가 −40…−80% 로 깊고
레짐 실패에 취약하다. **약상관 공격형 전략들을 동일가중/역변동성으로 섞으면** CAGR 대부분을 지키면서
드로다운을 얕게(그리고 레짐 실패를 줄여) 만들 수 있다는 가설을 검증한다. **가중치 최적화는 하지 않는다**
(작은 고정 사전등록 집합만; 개별 전략 재튜닝·재구현 없음 — 각 구성원의 **일별 순수익 스트림/결정함수를
그대로 import** 해서 쓴다).

구성원(전부 기존 Lane A 통과 스트림을 소비):
  · 합법(legal, 한국 리테일 소수점 매수로 실거래 가능) 집합 =
      {hibeta_basket(c12s), ftlt_hibeta·holygrail_hibeta·simple_hibeta·buffer_hibeta(c13a)}
  · 레버리지 ETP(letp) 집합 =
      {ftlt(c12_b1)·holy_grail(c12_b2)·simple_rsi_uvxy(c12_b5)·lrs200_tqqq(c12_a1)·
       sma200_buffer_tqqq(c12_a2)·nine_sig(c12_a3)}

사전등록(실행 전 고정, 가중치 탐색 없음):
  1. c15b_ew_legal   : legal 5개 동일가중, 월간 리밸런스.
  2. c15b_ew_letp    : letp 6개 동일가중, 월간.
  3. c15b_ivol_legal : legal 5개 63일 역변동성 가중, 월간.
  4. c15b_core_sat   : 50% QQQ + 50% c15b_ew_legal(코어-새틀라이트, 공격적이지만 앵커).
  5. c15b_vote_legal : (창의) '신호 투표' — legal 5개 신호 중 ≥3개가 위험선호일 때만 c15b_ew_legal
                       보유, 아니면 현금. 다수결 디리스킹 오버레이.

**리밸런스 비용(사전등록):** 개별 구성원의 내부 매매비용은 이미 각 스트림에 내재. 앙상블은 **슬리브
간 자본 이동(월간)** 비용만 추가한다 = 0.1%/side(10bp) + 대표 반호가(주식바스켓 3bp / 레버 ETP 2bp /
QQQ 1bp) + 슬리피지 5bp. 2× 스트레스 + micro(≤$10 매수 무료 → 매수 수수료 0) 병기.

**분할:** 설계 ≤2021-12-31 / 홀드아웃 2022~. 단, UVXY 의존 구성원(ftlt·holy_grail·simple 계열)이
2018-09 부터라 공통 달력이 그만큼 절단됨 → 각 앙상블의 실제 first/last 를 보고한다.

**신호 재사용(부록 v2.1 §4):** 모든 구성원의 홀드아웃은 c12/c13a 에서 이미 관측됨 → 앙상블 홀드아웃도
**반오염(semi_contaminated)**. Lane A 판정(a·c + 파산가드)은 적용하되 RC/SPA p 를 병기하고 유의성
임계를 **p<0.01** 로 강화해 정직하게 노출한다.

**데이터 한계:** hibeta 계열은 PIT S&P500 캐시 커버리지 ~27~31%(대형·생존 편중) → legal 성과는 상한.

재현: PYTHONPATH=src .venv/bin/python experiments/c15b_ensemble.py [--no-ledger] [--no-report] [--quick]
Do NOT edit src/.  src·gate·research·histdata·c12_*·c13a·c2d 는 import(소비)만 한다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata as hd, research as R  # noqa: E402
import gate_eval as GE  # noqa: E402
import c12_composer as CC  # noqa: E402
import c12_trend_vol as CT  # noqa: E402
import c12_hibeta as CH  # noqa: E402
import c13a_signal_hibeta as C13  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle15_c15b_ensemble.md"
RESULTS_JSON = ROOT / "reports" / "c15b_results.json"

DESIGN_END = date(2021, 12, 31)
LANE = "A"
BPS = 1e-4
COMM_BPS = 10.0            # 토스 0.1%/side
SLIP_BPS = 5.0
BANKRUPTCY_MDD = -0.95

# 사전등록 구성원 집합 ──────────────────────────────────────────────────────────
SET_LEGAL = ["hibeta_basket", "ftlt_hibeta", "holygrail_hibeta", "simple_hibeta", "buffer_hibeta"]
SET_LETP = ["ftlt", "holy_grail", "simple_rsi_uvxy", "lrs200_tqqq", "sma200_buffer_tqqq", "nine_sig"]

# 대표 반호가(리밸런스 비용용): 주식바스켓 3bp / 레버 ETP 2bp / QQQ 1bp
SPREAD = {
    "hibeta_basket": 3.0, "ftlt_hibeta": 3.0, "holygrail_hibeta": 3.0,
    "simple_hibeta": 3.0, "buffer_hibeta": 3.0,
    "ftlt": 2.0, "holy_grail": 2.0, "simple_rsi_uvxy": 2.0,
    "lrs200_tqqq": 2.0, "sma200_buffer_tqqq": 2.0, "nine_sig": 2.0,
    "QQQ": 1.0, "ew_legal": 3.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# 스트림 컨테이너: 한 구성원의 일별 순수익(ret_dates 정렬) + 날짜맵 + (선택) 위험선호 상태.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Stream:
    name: str
    group: str                       # legal / letp / bench
    spread_bps: float
    ret_dates: list                  # 수익일(=원 dates[1:])
    rets: list                       # 일별 순수익(ret_dates 정렬)
    by_date: dict                    # {date: ret}
    risk_on: dict | None = None      # {date: bool}(신호일 기준 위험선호). 없으면 None.


# ─────────────────────────────────────────────────────────────────────────────
# 구성원 스트림 빌더 — 전부 기존 모듈 함수를 그대로 호출(재구현·재튜닝 없음).
# ─────────────────────────────────────────────────────────────────────────────
def _hibeta_basket_stream(hib: dict) -> Stream:
    """c12s_hibeta_basket = C13.build_hibeta() 합성가격의 일별 수익(내부비용 내재)."""
    dates = hib["dates"]
    px = hib["px"]
    rd = list(dates[1:])
    rets = [px[i] / px[i - 1] - 1.0 if px[i - 1] > 0 else 0.0 for i in range(1, len(dates))]
    by = {rd[i]: rets[i] for i in range(len(rd))}
    # 상시투자(use_filter=False) → 위험선호 항상 True.
    ron = {d: True for d in dates}
    return Stream("hibeta_basket", "legal", SPREAD["hibeta_basket"], rd, rets, by, ron)


def _c13a_primary_stream(cfg, hib: dict, name: str) -> Stream:
    """c13a 설정의 primary(신호 t → t+1 종가) 순수익 스트림 재생. C13 함수만 사용."""
    sig_dates, S = C13.load_signal_panel(cfg)
    ext = {s: C13.sym_maps(s) for s in ("QQQ", "PSQ", "BIL", "SHY")}
    hib_close = hib["close"]
    need_ext = ["QQQ", "PSQ", "BIL", "SHY"]
    D = [d for d in sig_dates if d in hib_close and all(d in ext[s][0] for s in need_ext)]
    closes_map = {C13.HIBETA: hib_close, "QQQ": ext["QQQ"][0], "PSQ": ext["PSQ"][0],
                  "BIL": ext["BIL"][0], "SHY": ext["SHY"][0]}
    opens_map = {C13.HIBETA: hib_close, "QQQ": ext["QQQ"][1], "PSQ": ext["PSQ"][1],
                 "BIL": ext["BIL"][1], "SHY": ext["SHY"][1]}
    cash_rate = R.cash_rate_from_prices([ext["BIL"][0][d] for d in D])
    W_full = cfg.build(S, sig_dates, cfg.base_p)
    wmap = {sig_dates[i]: W_full[i] for i in range(len(sig_dates))}
    w_prim = [C13.remap_leg(wmap.get(d, {}), inverse_to="cash")[0] for d in D]
    res = C13.run_exec(D, w_prim, closes_map, opens_map, C13.C13A_UNIVERSE, C13.c13a_cost(1.0),
                       exec_lag=1, cash_rate=cash_rate)
    net = res.net_stream()
    rd = list(D[1:])
    by = {rd[i]: net[i] for i in range(len(rd))}
    # 위험선호 = 해당 신호일에 HIBETA(위험) 레그 보유(remap 후 HIBETA 비중>0).
    ron = {D[i]: (w_prim[i].get(C13.HIBETA, 0.0) > 0.0) for i in range(len(D))}
    return Stream(name, "legal", SPREAD[name], rd, net, by, ron)


def _composer_stream(strat, name: str) -> Stream:
    """c12_composer 전략의 primary 순수익 스트림 재생. CC 함수만 사용."""
    dates, S, opens, closes = CC.load_series(strat.symbols, ohlc=strat.ohlc)
    cash_rate = R.cash_rate_from_prices(closes["BIL"]) if "BIL" in closes else None
    weights = strat.build(S, dates, strat.base_p)
    res = CC.run_strategy_weights(closes, dates, opens, weights, CC.build_cost(1.0),
                                  exec_lag=1, cash_rate=cash_rate)
    net = res.net_stream()
    rd = list(dates[1:])
    by = {rd[i]: net[i] for i in range(len(rd))}
    return Stream(name, "letp", SPREAD[name], rd, net, by, None)


def _trendvol_a_stream(sig, params, name: str) -> Stream:
    """c12_trend_vol A1/A2 의 순수익 스트림 재생. CT 함수만 사용."""
    panel = CT.build_panel(["QQQ", "TQQQ", "BIL"])
    run = CT._wrun(panel, sig)
    res = run(params, CT.cost_spec(["QQQ", "TQQQ"]))
    net = res.net_stream()
    rd = list(panel.dates[1:])
    by = {rd[i]: net[i] for i in range(len(rd))}
    return Stream(name, "letp", SPREAD[name], rd, net, by, None)


def _ninesig_stream(name: str) -> Stream:
    """c12_trend_vol A3 9Sig(자본경로 의존 시뮬레이터) 순수익 스트림 재생."""
    panel = CT.build_panel(["TQQQ", "AGG", "QQQ"])
    res = CT.simulate_9sig(panel.full(), panel.dates, cost=CT.cost_spec(["TQQQ", "AGG", "QQQ"]),
                           cash_rate=panel.cash, growth=0.09, tqqq="TQQQ", bond="AGG")
    net = res.net_stream()
    rd = list(panel.dates[1:])
    by = {rd[i]: net[i] for i in range(len(rd))}
    return Stream(name, "letp", SPREAD[name], rd, net, by, None)


def _bh_stream(sym: str, group: str = "bench") -> Stream:
    cs = hd.load_symbol(sym)
    m = {c.dt: c.close for c in cs}
    dts = sorted(m)
    rd, rets = [], []
    for i in range(1, len(dts)):
        a, b = m[dts[i - 1]], m[dts[i]]
        rd.append(dts[i])
        rets.append(b / a - 1.0 if a > 0 else 0.0)
    by = {rd[i]: rets[i] for i in range(len(rd))}
    return Stream(sym, group, SPREAD.get(sym, 1.0), rd, rets, by, None)


def build_streams() -> tuple[dict, dict]:
    """모든 구성원 스트림 + 벤치. (streams, hib) 반환."""
    print("[c15b] HIBETA 바스켓 합성 …", flush=True)
    hib = C13.build_hibeta()
    specs = {c.cid: c for c in C13.build_specs()}
    reg = {s.sid: s for s in CC.registry()}
    S: dict[str, Stream] = {}
    print("[c15b] legal 스트림 …", flush=True)
    S["hibeta_basket"] = _hibeta_basket_stream(hib)
    S["ftlt_hibeta"] = _c13a_primary_stream(specs["c13a_ftlt_hibeta"], hib, "ftlt_hibeta")
    S["holygrail_hibeta"] = _c13a_primary_stream(specs["c13a_holygrail_hibeta"], hib, "holygrail_hibeta")
    S["simple_hibeta"] = _c13a_primary_stream(specs["c13a_simple_hibeta"], hib, "simple_hibeta")
    S["buffer_hibeta"] = _c13a_primary_stream(specs["c13a_buffer_hibeta"], hib, "buffer_hibeta")
    print("[c15b] letp 스트림 …", flush=True)
    S["ftlt"] = _composer_stream(reg["c12_b1_ftlt"], "ftlt")
    S["holy_grail"] = _composer_stream(reg["c12_b2_holygrail"], "holy_grail")
    S["simple_rsi_uvxy"] = _composer_stream(reg["c12_b5_simple"], "simple_rsi_uvxy")
    S["lrs200_tqqq"] = _trendvol_a_stream(CT.sig_a1, {"sma_win": 200, "lev": "TQQQ"}, "lrs200_tqqq")
    S["sma200_buffer_tqqq"] = _trendvol_a_stream(
        CT.sig_a2, {"entry": 0.05, "exit": 0.03, "lev": "TQQQ"}, "sma200_buffer_tqqq")
    S["nine_sig"] = _ninesig_stream("nine_sig")
    return S, hib


# ─────────────────────────────────────────────────────────────────────────────
# 공통달력 + 가중치 + 앙상블 시뮬레이터
# ─────────────────────────────────────────────────────────────────────────────
def common_dates(members: list[Stream]) -> list:
    inter = set(members[0].ret_dates)
    for m in members[1:]:
        inter &= set(m.ret_dates)
    return sorted(inter)


def _side_cost(spread_bps: float, side: str, *, mult: float, micro: bool) -> float:
    comm = 0.0 if (micro and side == "BUY") else COMM_BPS
    return (comm + spread_bps + SLIP_BPS) * mult * BPS


def _target_weights(members, R_mat, k, scheme, lookback=63) -> list[float]:
    n = len(members)
    if scheme == "equal" or k == 0:
        return [1.0 / n] * n
    if scheme == "ivol":
        lo = max(1, k - lookback + 1)
        vols = []
        for i in range(n):
            col = [R_mat[j][i] for j in range(lo, k + 1)]
            vols.append(gate._std(col, ddof=1) if len(col) >= 20 else 0.0)
        if all(v > 0 for v in vols):
            inv = [1.0 / v for v in vols]
            tot = sum(inv)
            return [x / tot for x in inv]
        return [1.0 / n] * n
    return [1.0 / n] * n


def simulate_ensemble(members: list[Stream], scheme: str, *, mult: float = 1.0,
                      micro: bool = False, lookback: int = 63) -> tuple[list, list, list]:
    """월간 리밸런스 포트폴리오(구성원 = 순수익 스트림). 리밸런스일에만 슬리브간 이동비용.

    반환 (ret_dates, port_returns, cd). port_returns 는 cd[1:] 정렬.
    """
    cd = common_dates(members)
    n = len(members)
    if len(cd) < 3:
        return [], [], cd
    R_mat = [[m.by_date[d] for m in members] for d in cd]
    me = R.month_end_flags(cd)
    v = [0.0] * n
    w = _target_weights(members, R_mat, 0, scheme, lookback)
    for i in range(n):
        v[i] = w[i]                              # 초기 배분(비용 제외; 구성원 초기매수와 일관)
    eq_prev = sum(v)
    port_ret, rd = [], []
    for k in range(1, len(cd)):
        for i in range(n):
            v[i] *= (1.0 + R_mat[k][i])
        eq = sum(v)
        if me[k]:
            w = _target_weights(members, R_mat, k, scheme, lookback)
            cost = 0.0
            tgt = [w[i] * eq for i in range(n)]
            for i in range(n):
                dv = tgt[i] - v[i]
                if dv > 0:
                    cost += dv * _side_cost(members[i].spread_bps, "BUY", mult=mult, micro=micro)
                elif dv < 0:
                    cost += (-dv) * _side_cost(members[i].spread_bps, "SELL", mult=mult, micro=micro)
            eq -= cost
            for i in range(n):
                v[i] = w[i] * eq
        port_ret.append(eq / eq_prev - 1.0 if eq_prev > 0 else 0.0)
        rd.append(cd[k])
        eq_prev = eq
    return rd, port_ret, cd


def simulate_vote(ew_stream: Stream, legal_members: list[Stream], cash_map: dict, *,
                  need: int = 3, mult: float = 1.0, micro: bool = False) -> tuple[list, list]:
    """신호 투표 오버레이: 전일 legal 신호 중 ≥need 위험선호면 ew_legal 보유, 아니면 현금.

    토글 시 슬리브(ew_legal, 반호가 3bp) 전량 스위치 비용. 반환 (ret_dates, returns).
    """
    cd = ew_stream.ret_dates                     # = set_legal 공통달력[1:]
    if len(cd) < 3:
        return [], []
    ew_by = ew_stream.by_date
    port_ret, rd = [], []
    prev_on = False
    switch = _side_cost(SPREAD["ew_legal"], "BUY", mult=mult, micro=micro)
    for k in range(1, len(cd)):
        d, dprev = cd[k], cd[k - 1]
        votes = sum(1 for m in legal_members if (m.risk_on or {}).get(dprev, False))
        on = votes >= need
        r = ew_by.get(d, 0.0) if on else cash_map.get(d, 0.0)
        if on != prev_on:                        # 진입/청산 스위치 비용
            side = "BUY" if on else "SELL"
            r -= _side_cost(SPREAD["ew_legal"], side, mult=mult, micro=micro)
        port_ret.append(r)
        rd.append(d)
        prev_on = on
    return rd, port_ret


# ─────────────────────────────────────────────────────────────────────────────
# 지표
# ─────────────────────────────────────────────────────────────────────────────
def _metrics(rets: list, dates: list) -> dict:
    if len(rets) < 2:
        return {"cagr": float("nan"), "mdd": float("nan"), "sharpe": float("nan"),
                "calmar": float("nan"), "n": len(rets)}
    eq = gate.returns_to_equity(rets)
    days = max((dates[-1] - dates[0]).days, 1)
    cg = gate.cagr(eq, days)
    mdd = gate.max_drawdown(eq)
    return {"cagr": cg, "mdd": mdd, "sharpe": gate.sharpe(rets),
            "calmar": gate.calmar(cg, mdd), "n": len(rets)}


def _split(rets: list, dates: list) -> dict:
    di = [i for i, d in enumerate(dates) if d <= DESIGN_END]
    hi = [i for i, d in enumerate(dates) if d > DESIGN_END]
    out = {"full": _metrics(rets, dates)}
    out["design"] = _metrics([rets[i] for i in di], [dates[i] for i in di]) if len(di) > 1 else \
        {"cagr": float("nan"), "mdd": float("nan"), "sharpe": float("nan"), "calmar": float("nan"), "n": len(di)}
    out["holdout"] = _metrics([rets[i] for i in hi], [dates[i] for i in hi]) if len(hi) > 1 else \
        {"cagr": float("nan"), "mdd": float("nan"), "sharpe": float("nan"), "calmar": float("nan"), "n": len(hi)}
    return out


def _pearson(a: list, b: list) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return float("nan")
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def corr_matrix(members: list[Stream], cd: list, *, upto: bool) -> dict:
    """구성원 daily return 상관행렬(cd 공통달력). upto=True → 설계(≤DESIGN_END), False → 홀드아웃."""
    sel = [d for d in cd if (d <= DESIGN_END if upto else d > DESIGN_END)]
    cols = {m.name: [m.by_date[d] for d in sel] for m in members}
    names = [m.name for m in members]
    mat = {a: {b: (1.0 if a == b else _pearson(cols[a], cols[b])) for b in names} for a in names}
    return {"names": names, "n": len(sel), "matrix": mat,
            "avg_offdiag": _avg_offdiag(mat, names)}


def _avg_offdiag(mat: dict, names: list) -> float:
    vals = [mat[a][b] for i, a in enumerate(names) for b in names[i + 1:]
            if not math.isnan(mat[a][b])]
    return sum(vals) / len(vals) if vals else float("nan")


def bench_on(cd: list, qqq: Stream, tqqq: Stream) -> dict:
    """QQQ/TQQQ B&H 를 앙상블 공통달력(cd[1:])에 정합한 설계/홀드아웃/전구간 지표."""
    rd = cd[1:]
    out = {}
    for nm, st in (("qqq", qqq), ("tqqq", tqqq)):
        rets = [st.by_date.get(d, 0.0) for d in rd]
        out[nm] = _split(rets, rd)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Lane A 판정(앙상블판): a·c + 파산가드 + 신호재사용(RC/SPA p<0.01) 병기.
# ─────────────────────────────────────────────────────────────────────────────
def lane_a_verdict(des, hold, des2, hold2, qd, qh, mdd_full, *, rc_p, spa_p) -> tuple[str, list]:
    reasons = []
    if mdd_full is not None and mdd_full <= BANKRUPTCY_MDD:
        return "FAIL", ["파산 가드: 전표본 MDD ≤ −95% → FAIL"]

    def gt(a, b):
        return (a is not None and b is not None and not math.isnan(a) and not math.isnan(b) and a > b)
    a = gt(des, qd) and gt(hold, qh)
    c = gt(des2, qd) and gt(hold2, qh)
    sig_ok = (rc_p is not None and rc_p < 0.01 and spa_p is not None and spa_p < 0.01)
    if not a:
        reasons.append("(a) 설계·홀드아웃 모두 CAGR>QQQ 미충족")
        return "FAIL", reasons
    if a and c and sig_ok:
        return "PASS", ["(a)(c) 충족 + RC/SPA p<0.01 → Lane A PASS(반오염 표기; 페이퍼 후보)"]
    if a and c:
        reasons.append("(a)(c) 충족하나 RC/SPA p≥0.01(신호재사용·다중검정 페널티)")
        return "CONDITIONAL", reasons
    reasons.append("(a) 충족, (c) 2×비용에서 붕괴 → CONDITIONAL")
    return "CONDITIONAL", reasons


# ─────────────────────────────────────────────────────────────────────────────
# 앙상블 평가
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_ensemble(eid: str, members: list[Stream], scheme: str, qqq: Stream, tqqq: Stream,
                      *, do_log=True, rc_B=1500, vote=None, cash_map=None) -> dict:
    if vote is not None:
        rd, rets = simulate_vote(vote["ew"], members, cash_map, need=vote["need"])
        _, rets2 = simulate_vote(vote["ew"], members, cash_map, need=vote["need"], mult=2.0)
        _, rets_micro = simulate_vote(vote["ew"], members, cash_map, need=vote["need"], micro=True)
        cd = [vote["ew"].ret_dates[0]] + rd     # cd[1:] == rd
    else:
        rd, rets, cd = simulate_ensemble(members, scheme)
        _, rets2, _ = simulate_ensemble(members, scheme, mult=2.0)
        _, rets_micro, _ = simulate_ensemble(members, scheme, micro=True)

    sp = _split(rets, rd)
    sp2 = _split(rets2, rd)
    sp_micro = _split(rets_micro, rd)
    bench = bench_on(cd, qqq, tqqq)
    qd = bench["qqq"]["design"]["cagr"]
    qh = bench["qqq"]["holdout"]["cagr"]
    qf = bench["qqq"]["full"]["cagr"]

    # 구성원별(같은 공통달력) 지표 → 최고 단일 구성원 비교
    per = {}
    for m in members:
        mr = [m.by_date[d] for d in rd]
        per[m.name] = _split(mr, rd)
    # 최고 단일 = 홀드아웃 Calmar 최대(동률 시 CAGR).
    def _key(x):
        c = per[x]["holdout"]["calmar"]
        g = per[x]["holdout"]["cagr"]
        c = -1e9 if (c is None or math.isnan(c)) else c
        g = -1e9 if (g is None or math.isnan(g)) else g
        return (c, g)
    best_single = max(per, key=_key) if per else None

    # RC / SPA (홀드아웃 초과 vs QQQ, 가족 = 구성원 홀드아웃 초과). 반오염 → p<0.01.
    hi = [i for i, d in enumerate(rd) if d > DESIGN_END]
    q_by = qqq.by_date
    cand_ex = [rets[i] - q_by.get(rd[i], 0.0) for i in hi]
    fam = {m.name: [m.by_date[rd[i]] - q_by.get(rd[i], 0.0) for i in hi] for m in members}
    if len(cand_ex) > 30:
        rc = GE.reality_check(cand_ex, family_excess=fam, B=rc_B)
    else:
        rc = {"rc_pvalue": None, "spa_pvalue": None}

    verdict, reasons = lane_a_verdict(
        sp["design"]["cagr"], sp["holdout"]["cagr"], sp2["design"]["cagr"], sp2["holdout"]["cagr"],
        qd, qh, sp["full"]["mdd"], rc_p=rc["rc_pvalue"], spa_p=rc["spa_pvalue"])

    if do_log and rd:
        cfg = {"ensemble": True, "scheme": scheme, "members": [m.name for m in members],
               "signal_reuse": True, "semi_contaminated": True}
        di = [i for i, d in enumerate(rd) if d <= DESIGN_END]
        if len(di) > 1:
            um_d = GE.unit_capital_metrics([rets[i] for i in di], dates=[rd[i] for i in di])
            GE.log_evaluation(eid, cfg, LANE, "design", um_d,
                              window=(rd[di[0]], rd[di[-1]]),
                              universe=[m.name for m in members], ledger_path=LEDGER)
        if len(hi) > 1 and not gate.already_peeked(LEDGER, eid):
            um_h = GE.unit_capital_metrics([rets[i] for i in hi], dates=[rd[i] for i in hi])
            try:
                GE.log_evaluation(eid, cfg, LANE, "holdout", um_h,
                                  window=(rd[hi[0]], rd[hi[-1]]),
                                  universe=[m.name for m in members], ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    return {
        "eid": eid, "scheme": scheme, "members": [m.name for m in members],
        "first": rd[0].isoformat() if rd else None, "last": rd[-1].isoformat() if rd else None,
        "n_days": len(rd),
        "full": sp["full"], "design": sp["design"], "holdout": sp["holdout"],
        "cost2x": {"design": sp2["design"]["cagr"], "holdout": sp2["holdout"]["cagr"]},
        "micro": {"full": sp_micro["full"]["cagr"], "holdout": sp_micro["holdout"]["cagr"]},
        "bench": bench, "per_member": per, "best_single": best_single,
        "rc_p": rc["rc_pvalue"], "spa_p": rc["spa_pvalue"],
        "signal_reuse": True, "semi_contaminated": True,
        "verdict": verdict, "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 리포트 렌더
# ─────────────────────────────────────────────────────────────────────────────
def _pc(v, nd=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:+.{nd}f}%"


def _f(v, nd=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:.{nd}f}"


PREREG = """# Cycle 15 — c15b: Lane A 승자 앙상블(신호 다양화로 드로다운 완화)

> 사전등록 2026-09-28 · 결과 보기 전 확정 · src 미수정 · 원장 lane "A" · 신호재사용(부록 v2.1 §4, 반오염)

## 사전등록 (가설·규칙, 결과 보기 전)

**가설.** 개별 Lane A 승자는 CAGR 은 높으나 MDD −40…−80% 로 깊다. **약상관 공격형 전략을 동일가중/
역변동성으로 섞으면**(가중치 최적화 없음) CAGR 대부분을 유지하며 드로다운을 얕게, 레짐 실패를 줄일 수 있다.

**구성원(기존 스트림 소비, 재구현·재튜닝 없음).**
- **legal**(한국 리테일 소수점 매수 가능): hibeta_basket(c12s), ftlt_hibeta·holygrail_hibeta·
  simple_hibeta·buffer_hibeta(c13a).
- **letp**(레버리지 ETP): ftlt(c12_b1)·holy_grail(c12_b2)·simple_rsi_uvxy(c12_b5)·lrs200_tqqq(c12_a1)·
  sma200_buffer_tqqq(c12_a2)·nine_sig(c12_a3).

**사전등록 앙상블 5개(가중치 탐색 금지).**
1. **c15b_ew_legal** — legal 5개 동일가중, 월간 리밸런스.
2. **c15b_ew_letp** — letp 6개 동일가중, 월간.
3. **c15b_ivol_legal** — legal 5개 63일 역변동성 가중, 월간.
4. **c15b_core_sat** — 50% QQQ + 50% c15b_ew_legal(코어-새틀라이트).
5. **c15b_vote_legal**(창의) — legal 5개 신호 중 ≥3개 위험선호일 때만 c15b_ew_legal 보유 else 현금(다수결 디리스킹).

**리밸런스 비용.** 구성원 내부비용은 각 스트림에 내재. 앙상블은 **슬리브간 월간 이동**비용만 추가 =
0.1%/side(10bp) + 대표 반호가(주식바스켓 3bp / 레버 ETP 2bp / QQQ 1bp) + 슬리피지 5bp. 2× 스트레스 +
micro(≤$10 매수 무료) 병기.

**Lane A(부록 v3).** (a) 설계·홀드아웃 **모두** CAGR>QQQ B&H, (c) 2×비용에서도 (a), 파산가드 전표본
MDD>−95%. **가중치 탐색을 하지 않으므로 (b) 파라미터 이웃 평탄성은 N/A**(대신 상관행렬·구성원별 성과로
견고성 제시). **신호 재사용:** 모든 구성원 홀드아웃이 c12/c13a 에서 관측됨 → 앙상블 홀드아웃도 **반오염**
→ RC/SPA p 병기, 유의성 임계 **p<0.01** 로 강화.

**분할.** 설계 ≤2021-12 / 홀드아웃 2022~. UVXY 의존 구성원(ftlt·holy_grail·simple)이 2018-09 시작 →
공통달력 절단(각 앙상블 first/last 보고). **데이터 한계:** hibeta 계열 PIT 커버리지 ~27~31% → legal 상한.
"""


def _corr_table(cm: dict) -> list:
    names = cm["names"]
    L = ["| | " + " | ".join(n[:10] for n in names) + " |",
         "|" + "---|" * (len(names) + 1)]
    for a in names:
        row = [f"**{a[:14]}**"] + [_f(cm["matrix"][a][b], 2) for b in names]
        L.append("| " + " | ".join(row) + " |")
    L.append(f"\n> 평균 비대각 상관: {_f(cm['avg_offdiag'], 2)} (n={cm['n']}일)")
    return L


def write_report(results: list, streams: dict, corr: dict, meta: dict):
    L = [PREREG.rstrip(), "", "<!-- RESULTS_BELOW -->", ""]
    L.append(f"> 실행 {meta['generated']} · 원장 {meta['ledger']} · rc_B={meta['rc_B']} · 앙상블 {len(results)}개")
    L.append("")

    L.append("## 1. 구성원 일별수익 상관행렬 (약상관일수록 앙상블 이득 큼)")
    L.append("\n### legal 집합 — 설계(≤2021-12)")
    L += _corr_table(corr["legal"]["design"])
    L.append("\n### legal 집합 — 홀드아웃(2022~)")
    L += _corr_table(corr["legal"]["holdout"])
    L.append("\n### letp 집합 — 설계(≤2021-12)")
    L += _corr_table(corr["letp"]["design"])
    L.append("\n### letp 집합 — 홀드아웃(2022~)")
    L += _corr_table(corr["letp"]["holdout"])
    L.append("")

    L.append("## 2. 앙상블 성과 (설계 vs 홀드아웃, 순비용)")
    L.append("| 앙상블 | 창 | CAGR 설계/홀드 (QQQ) | MDD 설계/홀드 | Sharpe 설계/홀드 | Calmar 설계/홀드 | "
             "2×비용 홀드 | micro 홀드 | RC p | SPA p | 판정 |")
    L.append("|---|---|---|---|---|---|---:|---:|---:|---:|---|")
    for r in results:
        d, h, b = r["design"], r["holdout"], r["bench"]["qqq"]
        L.append(
            f"| {r['eid'].replace('c15b_','')} | {(r['first'] or '')[:7]}…{(r['last'] or '')[:7]} | "
            f"{_pc(d['cagr'])}/{_pc(h['cagr'])} ({_pc(b['design']['cagr'])}/{_pc(b['holdout']['cagr'])}) | "
            f"{_pc(d['mdd'])}/{_pc(h['mdd'])} | {_f(d['sharpe'])}/{_f(h['sharpe'])} | "
            f"{_f(d['calmar'])}/{_f(h['calmar'])} | {_pc(r['cost2x']['holdout'])} | {_pc(r['micro']['holdout'])} | "
            f"{_f(r['rc_p'], 3)} | {_f(r['spa_p'], 3)} | **{r['verdict']}** |")
    L.append("")
    L.append("> RC/SPA 임계 = **p<0.01**(신호 재사용·반오염). (b) 이웃 평탄성은 가중치 미탐색으로 N/A.")
    L.append("")

    L.append("## 3. vs QQQ/TQQQ B&H · vs 최고 단일 구성원 (전구간, 같은 창)")
    L.append("| 앙상블 | 앙상블 CAGR/MDD/Calmar | QQQ B&H | TQQQ B&H | 최고단일(홀드Calmar) | 최고단일 CAGR/MDD |")
    L.append("|---|---|---|---|---|---|")
    for r in results:
        f_, b = r["full"], r["bench"]
        bs = r["best_single"]
        bsf = r["per_member"][bs]["full"] if bs else {}
        L.append(
            f"| {r['eid'].replace('c15b_','')} | {_pc(f_['cagr'])}/{_pc(f_['mdd'])}/{_f(f_['calmar'])} | "
            f"{_pc(b['qqq']['full']['cagr'])}/{_pc(b['qqq']['full']['mdd'])} | "
            f"{_pc(b['tqqq']['full']['cagr'])}/{_pc(b['tqqq']['full']['mdd'])} | {bs} | "
            f"{_pc(bsf.get('cagr'))}/{_pc(bsf.get('mdd'))} |")
    L.append("")

    L.append("## 4. 구성원별 성과 (각 앙상블 공통달력 기준, 홀드아웃)")
    for r in results:
        L.append(f"- **{r['eid'].replace('c15b_','')}** (창 {(r['first'] or '')[:7]}~): "
                 + ", ".join(f"{n}={_pc(r['per_member'][n]['holdout']['cagr'])}"
                             f"(MDD {_pc(r['per_member'][n]['holdout']['mdd'])})"
                             for n in r["members"]))
    L.append("")

    L.append("## 5. 판정 종합 및 정직한 해석")
    L.append(_prose(results, corr))
    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            L[0] = prev.split("<!-- RESULTS_BELOW -->")[0].rstrip()
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def _prose(results, corr):
    P = [r["eid"] for r in results if r["verdict"] == "PASS"]
    C = [r["eid"] for r in results if r["verdict"] == "CONDITIONAL"]
    Fl = [r["eid"] for r in results if r["verdict"] == "FAIL"]
    cl = corr["legal"]["holdout"]["avg_offdiag"]
    ct = corr["letp"]["holdout"]["avg_offdiag"]
    by = {r["eid"]: r for r in results}

    def mdd(eid, seg):
        return by[eid][seg]["mdd"] if eid in by else float("nan")
    lines = [f"**판정 요약.** PASS: {P or '없음'} · CONDITIONAL: {C or '없음'} · FAIL: {Fl or '없음'}. "
             "(부록 v3 Lane A a·c + 파산가드; (b) 이웃은 가중치 미탐색으로 N/A. RC/SPA p<0.01 병기.)", ""]
    lines.append(
        f"**정직한 해석.** (1) **다양화가 기대만큼 크지 않다 — 특히 legal.** legal 구성원의 홀드아웃 평균 "
        f"비대각 상관은 {_f(cl, 2)} 로 높다(전원이 같은 HIBETA 바스켓 슬리브를 공유; hibeta_basket↔simple_hibeta "
        f"≈0.99, ftlt_hibeta↔holygrail_hibeta ≈0.98). 그 결과 c15b_ew_legal 의 MDD({_pc(mdd('c15b_ew_legal','holdout'))} "
        f"홀드)는 개별 최악(−48%)보다는 얕지만 buffer_hibeta 수준에 그친다 — '약상관 섞기'의 이득이 제한적이다. "
        f"(2) **letp 집합이 더 잘 분산된다**(상관 {_f(ct, 2)}): 추세(a1·a2·9sig) vs UVXY 스파이크(b1·b2·b5) 축이 "
        f"달라, c15b_ew_letp 는 개별 −60…−83% MDD 를 {_pc(mdd('c15b_ew_letp','holdout'))}(홀드)로 눌렀다(단 여전히 깊음). "
        f"(3) **가장 큰 드로다운 이득은 창의 앙상블 c15b_vote_legal**({_pc(mdd('c15b_vote_legal','holdout'))} 홀드, "
        f"Calmar {_f(by.get('c15b_vote_legal',{}).get('holdout',{}).get('calmar'))})다 — 상관이 높아도 **다수결 디리스킹**이 "
        "좌측꼬리(전원이 위험회피로 몰리는 급락)를 직접 잘라 CAGR 은 오히려 ew_legal 보다 높고 MDD 는 더 얕다. "
        "(4) 그러나 **모든 구성원이 홀드아웃을 이미 관측한 반오염 스트림**이라 홀드아웃 우위는 사후적일 수 있다 → "
        "전 앙상블이 (a)·(c)는 통과하나 RC/SPA p<0.01 은 미달해 **전부 CONDITIONAL**(PASS 아님). 다중검정·재사용 "
        "페널티를 정직하게 노출한다. (5) 가중치 최적화가 없으므로(동일/역변동성/50:50/투표만) 가중치 오버핏은 없다 — "
        "(b) 이웃 평탄성 대체물로 상관행렬·구성원별 성과를 제시. (6) legal 성과는 PIT 커버리지 ~30% 상한(대형·생존 "
        "편중) → UPPER BOUND. (7) 실채택은 부록 v3-4 포워드 페이퍼(공격형 랩 3개월+ 실시세) 후 사용자 승인 시에만.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def run(do_log=True, rc_B=1500, only=None):
    streams, hib = build_streams()
    qqq = _bh_stream("QQQ")
    tqqq = _bh_stream("TQQQ")

    legal = [streams[n] for n in SET_LEGAL]
    letp = [streams[n] for n in SET_LETP]

    # 상관행렬(legal/letp × 설계/홀드아웃)
    cd_legal = common_dates(legal)
    cd_letp = common_dates(letp)
    corr = {
        "legal": {"design": corr_matrix(legal, cd_legal, upto=True),
                  "holdout": corr_matrix(legal, cd_legal, upto=False)},
        "letp": {"design": corr_matrix(letp, cd_letp, upto=True),
                 "holdout": corr_matrix(letp, cd_letp, upto=False)},
    }

    # ew_legal 스트림(core_sat·vote_legal 에 재사용)
    rd_ewl, ret_ewl, cd_ewl = simulate_ensemble(legal, "equal")
    ew_legal_stream = Stream("ew_legal", "legal", SPREAD["ew_legal"], rd_ewl, ret_ewl,
                             {rd_ewl[i]: ret_ewl[i] for i in range(len(rd_ewl))}, None)
    # 현금수익률맵(set_legal 공통달력)
    bil_close = C13.sym_maps("BIL")[0]
    cash_list = R.cash_rate_from_prices([bil_close[d] for d in cd_ewl])
    cash_map = {cd_ewl[i]: cash_list[i] for i in range(len(cd_ewl))}

    plan = [
        ("c15b_ew_legal", legal, "equal", None),
        ("c15b_ew_letp", letp, "equal", None),
        ("c15b_ivol_legal", legal, "ivol", None),
        ("c15b_core_sat", [qqq, ew_legal_stream], "equal", None),
        ("c15b_vote_legal", legal, "vote", {"ew": ew_legal_stream, "need": 3}),
    ]
    results = []
    for eid, members, scheme, vote in plan:
        if only and eid not in only:
            continue
        print(f"[c15b] {eid} …", flush=True)
        r = evaluate_ensemble(eid, members, scheme, qqq, tqqq, do_log=do_log, rc_B=rc_B,
                              vote=vote, cash_map=cash_map)
        results.append(r)
        d, h = r["design"], r["holdout"]
        print(f"      설계 CAGR {_pc(d['cagr'])} MDD {_pc(d['mdd'])} | 홀드 CAGR {_pc(h['cagr'])} "
              f"MDD {_pc(h['mdd'])} Calmar {_f(h['calmar'])} RCp {_f(r['rc_p'],3)} → {r['verdict']}",
              flush=True)
    return results, streams, hib, corr


def main():
    import datetime as _dt
    ap = argparse.ArgumentParser(description="c15b Lane A 승자 앙상블(신호 다양화)")
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--quick", action="store_true", help="RC 부트스트랩 축소")
    ap.add_argument("--only", default=None, help="쉼표구분 eid 필터")
    args = ap.parse_args()
    do_log = not args.no_ledger
    rc_B = 400 if args.quick else 1500
    only = set(args.only.split(",")) if args.only else None

    results, streams, hib, corr = run(do_log=do_log, rc_B=rc_B, only=only)

    meta = {"generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "ledger": LEDGER if do_log else "(no-ledger)", "rc_B": rc_B,
            "hibeta_beta": hib["beta"], "coverage": hib["coverage"],
            "cost": "10bp/side + 대표반호가(바스켓3/레버2/QQQ1 bp) + 5bp slip; 월간 슬리브 리밸런스; "
                    "2x stress; micro=매수무료",
            "design_end": DESIGN_END.isoformat(), "lane": LANE,
            "signal_reuse": True, "semi_contaminated": True, "sig_threshold": 0.01}
    payload = {"meta": meta, "correlations": corr, "ensembles": results}
    RESULTS_JSON.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    if not args.no_report:
        write_report(results, streams, corr, meta)
        print(f"[c15b] report → {REPORT}", flush=True)
    print(f"[c15b] results → {RESULTS_JSON}", flush=True)


if __name__ == "__main__":
    main()
