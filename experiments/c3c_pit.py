#!/usr/bin/env python3
"""c3c — point-in-time(PIT) 유니버스로 생존편향을 제거한 단일종목 모멘텀 (레인2, 사이클3 `c3c`).

사양: docs/gate_v2_spec.md(레인2 §8.2, 부록 v2.1), 규약: experiments/README.md.
선행: reports/cycle2_c2d_stocks.md — c2d 는 *오늘의 승자* 110종목(유니버스 선택편향)에서 모멘텀이
CAGR 62~85%로 "통과"처럼 보였으나, 섹터 ETF 프록시에선 전부 QQQ 열위였다. DSR/RC/SPA 는
다중검정만 교정할 뿐 **유니버스 선택편향**을 못 본다. c3c 는 그 편향의 근원을 없앤다:

  유니버스 = **S&P 500 point-in-time 구성종목**(fja05680/sp500 의 공개 PIT CSV).
  각 리밸런스일에 그 시점 실제 지수 멤버십(as-of)만 후보로 쓴다. 종목은 지수에 실제로
  편입돼 있던 기간에만 선택 가능하며, 상장 전·상장폐지 후엔 자연 제외된다.

이 스크립트는 **src/ 를 수정하지 않고 소비만** 한다. c2d_stocks 의 포트폴리오 시뮬레이터
(다음시가 체결·일별 MTM)·비용 티어·gate/gate_eval(판정·원장)을 재사용하고, PIT 멤버십 게이팅과
결정 함수만 새로 얹는다.

사전등록(실행 전 고정, 튜닝 금지 — 아이디어당 사전등록 1개 + 평탄성 이웃 ±20~50%):
  1) c3c_pit_mom5        : 12-1 모멘텀 = close[m-21]/close[m-252]-1, PIT top-100(63d 달러거래대금)
                           중 top5 EW, 월간, 다음시가.
  2) c3c_pit_residmom5   : QQQ 베타(252d) 제거 잔차 12-1 누적, PIT top-100 중 top5 EW, 월간.
  3) c3c_pit_mom5_trend  : (1) + 추세필터(QQQ>SMA200 이면 모멘텀, 아니면 QQQ 100%).
  4) c3c_pit_newentrant  : (창의) '신규편입 효과' — 최근 hold_m개월 내 S&P500 신규편입 종목을 EW 보유
                           (지수편입 후 드리프트 검정), 없으면 QQQ. 월간.

정직성 규칙(부록 v2.1):
  - 신호 재사용 페널티(v2.1-4): 모멘텀 계열(1·2·3)은 홀드아웃 신호가 c2d 에서 이미 관측됨 →
    홀드아웃을 '반오염'으로 태깅하고 통과 문턱을 RC/SPA p<0.01 로 강화. newentrant 는 새 신호(p<0.05).
  - 프로그램 전체 N(v2.1-3): DSR 은 원장 전체 시도의 N_eff 로도 계산해 병기.
  - 상대 리스크 트랙(v2.1-1)은 벤치(QQQ)가 −50% 캡을 지키는 이 구간에선 절대 캡을 유지.
  - 상장폐지/피인수로 데이터 없는 멤버는 정량화하고 보수적 가정을 문서화(§survivorship).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata, research  # noqa: E402
from toss_trader.research import CostSpec  # noqa: E402
import gate_eval  # noqa: E402
import c2d_stocks as c2d  # noqa: E402  (포트폴리오 시뮬레이터·비용·헬퍼 재사용)

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
PIT_DIR = ROOT / "data" / "pit"
PIT_CSV = PIT_DIR / "sp500_components_changes.csv"
FETCH_STATUS = PIT_DIR / "fetch_status.json"

DESIGN_END = date(2021, 12, 31)   # 설계 2016-09~2021-12, 홀드아웃 2022~2026 (레인2 10년표본)
START = date(2016, 9, 1)
END = date(2026, 9, 30)
BENCH = "QQQ"

DV_TOP = 100        # PIT 후보를 63d 달러거래대금 상위 N 으로 제한(≈지수 내 대형주)
DV_WINDOW = 63      # 달러거래대금 평균 창(거래일)


# ─────────────────────────────────────────────────────────────────────────────
# PIT 멤버십: fja05680/sp500 의 (date, tickers) 이벤트 CSV → as-of 멤버십.
# ─────────────────────────────────────────────────────────────────────────────
def load_pit_records(path: Path = PIT_CSV) -> list[tuple[date, frozenset]]:
    """PIT CSV → [(변경일, 그 날짜 이후 유효한 전체 멤버십 frozenset)] (오름차순)."""
    recs: list[tuple[date, frozenset]] = []
    for r in csv.reader(io.StringIO(path.read_text())):
        if len(r) < 2 or r[0] == "date":
            continue
        try:
            d = date.fromisoformat(r[0])
        except ValueError:
            continue
        recs.append((d, frozenset(t.strip() for t in r[1].split(",") if t.strip())))
    recs.sort(key=lambda x: x[0])
    return recs


def pit_asof(recs: list[tuple[date, frozenset]], dt: date) -> frozenset:
    """dt 시점의 멤버십(dt 이하 최신 변경 레코드). 인과적: 미래 편입/편출을 참조하지 않는다."""
    cur: frozenset = frozenset()
    for d, tk in recs:
        if d <= dt:
            cur = tk
        else:
            break
    return cur


def additions_map(recs: list[tuple[date, frozenset]], start: date, end: date
                  ) -> dict[str, date]:
    """[start, end] 구간의 신규편입일 맵 {ticker: 최초 편입일}. 직전 멤버십 대비 새로 추가된 티커.
    구간 진입 시점(start) 이전부터 이미 멤버였던 종목은 신규편입으로 치지 않는다(인과적)."""
    prev = pit_asof(recs, start)
    added: dict[str, date] = {}
    for d, tk in recs:
        if d < start or d > end:
            continue
        for t in (tk - prev):
            added.setdefault(t, d)   # 최초 편입일만 기록
        prev = tk
    return added


# ─────────────────────────────────────────────────────────────────────────────
# PIT 패널: c2d.load_panel(정렬·전방채움·first_idx·vol) 재사용 + 원시종가/last_idx/멤버십 증강.
# ─────────────────────────────────────────────────────────────────────────────
def _fetchable_members(recs: list[tuple[date, frozenset]]) -> tuple[list[str], list[str]]:
    """창 내 전(全) 편입 종목 유니언을, 캐시 데이터 유무로 (가용, 무데이터)로 분리."""
    ever = set(pit_asof(recs, START))
    for d, tk in recs:
        if START <= d <= END:
            ever |= set(tk)
    cached = {p.stem for p in histdata.CACHE_DIR.glob("*.json")}
    have = sorted(t for t in ever if t.replace(".", "_") in cached)
    missing = sorted(t for t in ever if t.replace(".", "_") not in cached)
    return have, missing


def build_pit_panel(recs: list[tuple[date, frozenset]]) -> c2d.Panel:
    """가용 PIT 멤버 + QQQ 로 패널 구성. 티어=large_cap(3bp)로 재분류(S&P500 대형주),
    원시종가(달러거래대금용)·last_idx(상장폐지 후 미선택)·per-index 멤버십/신규편입을 붙인다."""
    have, missing = _fetchable_members(recs)
    panel = c2d.load_panel([BENCH] + have, calendar_symbol=BENCH, start=START, end=END,
                           adjusted=True)
    # 티어 재분류: 주식=large_cap(반호가 3bp, task 지정), ETF(QQQ)=etf(1bp).
    for s in panel.close:
        panel.tier[s] = research.TIER_ETF if (s == BENCH or s.startswith("XL")) \
            else research.TIER_LARGE_CAP
    panel.universe = [s for s in panel.close if s != BENCH]

    # 원시종가(달러거래대금 = 원시가격×거래량)와 last_idx(마지막 실측 인덱스) 증강.
    di = {d: i for i, d in enumerate(panel.dates)}
    n = len(panel.dates)
    panel.rawclose = {}          # type: ignore[attr-defined]
    panel.last_idx = {}          # type: ignore[attr-defined]
    for s in list(panel.close):
        try:
            raw = histdata.load_symbol(s, start=START, end=END, adjusted=False)
        except Exception:  # noqa: BLE001
            raw = []
        rc = [0.0] * n
        last = None
        last_i = panel.first_idx.get(s, 0)
        first_seen = None
        for c in raw:
            i = di.get(c.dt)
            if i is None or c.close <= 0:
                continue
            rc[i] = c.close
            if first_seen is None:
                first_seen = i
            last = c.close
            last_i = i
        # 전방채움 + 상장전 백필(양수 보장; 비중 0으로만 참조).
        if first_seen is not None:
            fv = rc[first_seen]
            for i in range(first_seen):
                rc[i] = fv
            lastv = fv
            for i in range(n):
                if rc[i] > 0:
                    lastv = rc[i]
                else:
                    rc[i] = lastv
        panel.rawclose[s] = rc                      # type: ignore[attr-defined]
        panel.last_idx[s] = last_i                  # type: ignore[attr-defined]

    # per-index PIT 멤버십(가용 멤버만) 과 신규편입일.
    added = additions_map(recs, START, END)
    panel.pit_elig = []          # type: ignore[attr-defined]
    for t, d in enumerate(panel.dates):
        m = pit_asof(recs, d)
        panel.pit_elig.append(frozenset(s for s in panel.universe if s in m))
    panel.added_idx = {}         # type: ignore[attr-defined]
    for s, ad in added.items():
        if s in panel.close:
            # 편입일 이후 첫 거래일 인덱스
            idx = next((i for i, d in enumerate(panel.dates) if d >= ad), None)
            if idx is not None:
                panel.added_idx[s] = idx            # type: ignore[attr-defined]
    panel.missing_members = missing                 # type: ignore[attr-defined]
    panel.pit_records = recs                         # type: ignore[attr-defined]
    return panel


# ─────────────────────────────────────────────────────────────────────────────
# 후보 선정: PIT 멤버 ∩ 데이터보유 ∩ 이력충족 ∩ 63d 달러거래대금 top-DV_TOP.
# ─────────────────────────────────────────────────────────────────────────────
def _pit_live(panel: c2d.Panel, s: str, t: int, need: int) -> bool:
    """t 시점 s 가 선택 가능한가: need 이력 충족 + 마지막 실측(last_idx) 이전(상장폐지 후 제외)."""
    if not c2d._eligible(panel, s, t, need):
        return False
    li = panel.last_idx.get(s)                       # type: ignore[attr-defined]
    return li is None or t <= li


def pit_candidates(panel: c2d.Panel, t: int, *, need: int, dv_top: int = DV_TOP,
                   dv_window: int = DV_WINDOW) -> list[str]:
    """t 시점 PIT 후보: as-of 멤버 ∩ live ∩ 63d 달러거래대금 상위 dv_top."""
    elig = panel.pit_elig[t]                         # type: ignore[attr-defined]
    scored: list[tuple[float, str]] = []
    for s in elig:
        if not _pit_live(panel, s, t, max(need, dv_window)):
            continue
        rc = panel.rawclose[s]                       # type: ignore[attr-defined]
        vo = panel.vol[s]
        dv = sum(rc[t - k] * vo[t - k] for k in range(dv_window)) / dv_window
        if dv > 0:
            scored.append((dv, s))
    scored.sort(reverse=True)
    return [s for _, s in scored[:dv_top]]


# ─────────────────────────────────────────────────────────────────────────────
# 결정 함수(종가 t 정보만 사용, 인과적). 실행은 c2d.simulate_portfolio 가 다음 시가에.
# ─────────────────────────────────────────────────────────────────────────────
def pit_momentum(panel: c2d.Panel, *, topk: int = 5, lookback: int = 252, skip: int = 21,
                 use_filter: bool = False, dv_top: int = DV_TOP) -> dict[int, dict[str, float]]:
    """12-1 모멘텀 = close[m-skip]/close[m-lookback]-1, PIT top-dv_top 중 top-k EW, 월말.
    use_filter=True 면 QQQ>SMA200 일 때만 모멘텀, 아니면 QQQ 100%(추세필터, else QQQ)."""
    sma_b = research.sma(panel.close[BENCH], 200)
    me = research.month_end_flags(panel.dates)
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(me):
        if not f:
            continue
        if use_filter:
            s = sma_b[t]
            if not (s is not None and panel.close[BENCH][t] > s):
                out[t] = {BENCH: 1.0}                # 추세 이탈 → QQQ 보유
                continue
        cands = pit_candidates(panel, t, need=lookback, dv_top=dv_top)
        scored = []
        for s in cands:
            c0 = panel.close[s][t - lookback]
            c1 = panel.close[s][t - skip]
            if c0 > 0:
                scored.append((c1 / c0 - 1.0, s))
        scored.sort(reverse=True)
        out[t] = c2d._equal_weight([s for _, s in scored[:topk]])
    return out


def pit_resid_momentum(panel: c2d.Panel, *, topk: int = 5, lookback: int = 252, skip: int = 21,
                       dv_top: int = DV_TOP) -> dict[int, dict[str, float]]:
    """잔차 모멘텀: [m-lookback, m-skip] 창에서 QQQ 베타 제거 후 잔차누적 top-k EW, PIT top-dv_top."""
    me = research.month_end_flags(panel.dates)
    bench_c = panel.close[BENCH]
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(me):
        if not f:
            continue
        a, b = t - lookback, t - skip
        if a < 1:
            continue
        rb = [bench_c[i] / bench_c[i - 1] - 1.0 for i in range(a, b + 1) if bench_c[i - 1] > 0]
        mb = sum(rb) / len(rb) if rb else 0.0
        var_b = sum((x - mb) ** 2 for x in rb)
        if var_b <= 0:
            out[t] = {}
            continue
        scored = []
        for s in pit_candidates(panel, t, need=lookback, dv_top=dv_top):
            cs = panel.close[s]
            rs = [cs[i] / cs[i - 1] - 1.0 for i in range(a, b + 1) if cs[i - 1] > 0]
            m = min(len(rs), len(rb))
            if m < 60:
                continue
            rs_, rb_ = rs[:m], rb[:m]
            ms = sum(rs_) / m
            cov = sum((rs_[i] - ms) * (rb_[i] - mb) for i in range(m))
            beta = cov / var_b
            resid_cum = sum(rs_[i] - beta * rb_[i] for i in range(m))
            scored.append((resid_cum, s))
        scored.sort(reverse=True)
        out[t] = c2d._equal_weight([s for _, s in scored[:topk]])
    return out


def pit_new_entrant(panel: c2d.Panel, *, hold_m: int = 6, min_hist: int = 21,
                    topk: int = 0) -> dict[int, dict[str, float]]:
    """신규편입 효과: 최근 hold_m개월 내 S&P500 신규편입 종목을 EW 보유(지수편입 드리프트 검정).
    편입일 이후 min_hist 거래일 지나 거래가능해진 종목만. topk>0 이면 그중 거래대금 상위 topk.
    바스켓이 비면 QQQ 100%. 인과적: 편입일/이력 모두 t 이하 정보."""
    me = research.month_end_flags(panel.dates)
    hold_days = int(hold_m * 21)
    out: dict[int, dict[str, float]] = {}
    for t, f in enumerate(me):
        if not f:
            continue
        names = []
        for s, ai in panel.added_idx.items():        # type: ignore[attr-defined]
            if ai is None:
                continue
            age = t - ai
            if min_hist <= age <= hold_days and _pit_live(panel, s, t, min_hist) \
                    and s in panel.pit_elig[t]:       # 여전히 지수 멤버
                names.append(s)
        if topk and len(names) > topk:
            dv = []
            for s in names:
                rc = panel.rawclose[s]               # type: ignore[attr-defined]
                vo = panel.vol[s]
                w = min(DV_WINDOW, t)
                dv.append((sum(rc[t - k] * vo[t - k] for k in range(w)) / max(w, 1), s))
            dv.sort(reverse=True)
            names = [s for _, s in dv[:topk]]
        out[t] = c2d._equal_weight(names) if names else {BENCH: 1.0}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 평가: 설계/홀드아웃 분할 + DSR(아이디어·프로그램) + RC/SPA + 평탄성 + 비용스트레스 + 판정.
# ─────────────────────────────────────────────────────────────────────────────
def _terminal(returns: list[float]) -> float:
    eq = 1.0
    for r in returns:
        eq *= (1.0 + r)
    return eq


def _neighbor_params(base: dict) -> list[dict]:
    """평탄성/N 이웃(±20~50%): lookback ×{0.7,0.8,1.2,1.5}, topk 근방, hold_m 근방. 그리드서치 아님."""
    out = []
    for key in ("lookback", "hold_m"):
        if key in base:
            for g in (0.7, 0.8, 1.2, 1.5):
                p = dict(base); p[key] = max(1, int(round(base[key] * g))); out.append(p)
    if "topk" in base and base["topk"]:
        for k in (max(1, base["topk"] - 2), base["topk"] + 2, base["topk"] + 5):
            if k != base["topk"]:
                p = dict(base); p["topk"] = k; out.append(p)
    return out


def evaluate_pit_idea(panel: c2d.Panel, idea_id: str, decide_fn, base_params: dict,
                      bench_ret: list[float], cash_rate: list[float] | None, *,
                      design_end: date = DESIGN_END, signal_reuse: bool = False,
                      log: bool = True, rc_B: int = 1500) -> dict:
    """PIT 아이디어 1개(사전등록 config + 이웃)를 평가·원장적재.

    signal_reuse=True(모멘텀 계열)면 홀드아웃을 반오염 태깅하고 RC/SPA 통과 문턱을 p<0.01 로 강화.
    반환: 설계/홀드아웃 성과, DSR(아이디어·프로그램 N_eff), RC/SPA, 비용스트레스(2x·10bp),
    회전/비용드래그, 서브구간, PIT 커버리지, 판정.
    """
    cost = c2d.cost_for(panel)
    zcost = CostSpec(commission_bps=0.0, slippage_bps=0.0, fx_bps=0.0, default_half_spread_bps=0.0)
    dates = panel.dates
    ret_dates = dates[1:]
    di = [i for i, d in enumerate(ret_dates) if d <= design_end]
    hi = [i for i, d in enumerate(ret_dates) if d > design_end]

    def sim_returns(params, c):
        dec = decide_fn(panel, **params)
        sim = c2d.simulate_portfolio(panel, dec, c, cash_rate=cash_rate)
        return sim, sim.returns[1:]

    sim_c, cand = sim_returns(base_params, cost)
    _, gross = sim_returns(base_params, zcost)
    bench_stream = bench_ret[1:]

    # 이웃(평탄성 + 아이디어 내부 N). 전체 스트림을 저장해 RC 가족을 분할별로 재구성.
    streams: dict[str, list[float]] = {}
    neigh_full: dict[str, list[float]] = {}
    sr_trials: list[float] = []
    neigh_pos = 0
    neigh_sh: list[float] = []
    cand_des = [cand[i] for i in di]
    center_sh = gate.sharpe(cand_des)
    bench_des = [bench_stream[i] for i in di]
    neighbors = _neighbor_params(base_params)
    for j, p in enumerate(neighbors):
        _, nret = sim_returns(p, cost)
        neigh_full[f"n{j}"] = nret
        nd = [nret[i] for i in di]
        streams[f"n{j}"] = nd
        sr_trials.append(c2d._sr_daily(nd))
        if _terminal(nd) / _terminal(bench_des) - 1.0 > 0:
            neigh_pos += 1
        neigh_sh.append(gate.sharpe(nd))
    streams["center"] = cand_des
    sr_trials.append(c2d._sr_daily(cand_des))
    n_eff = gate.n_eff_clusters(streams)
    frac_pos = neigh_pos / len(neigh_sh) if neigh_sh else 0.0
    neigh_sh.sort()
    med_sh = neigh_sh[len(neigh_sh) // 2] if neigh_sh else 0.0
    plateau_pass = bool(neigh_sh and frac_pos >= 0.8 and med_sh >= 0.9 * center_sh)
    plateau_border = bool(neigh_sh and frac_pos >= 0.6 and not plateau_pass)

    def split_metrics(idxs):
        r = [cand[i] for i in idxs]
        b = [bench_stream[i] for i in idxs]
        d = [ret_dates[i] for i in idxs]
        um = gate_eval.unit_capital_metrics(r, dates=d, sr_trials=sr_trials, n_eff=n_eff)
        excess = [r[k] - b[k] for k in range(len(r))]
        # RC 다중검정 가족 = **같은 분할**의 이웃 config 초과수익(설계는 설계, 홀드는 홀드).
        fam = {name: [nf[i] - bench_stream[i] for i in idxs] for name, nf in neigh_full.items()}
        rc = gate_eval.reality_check(excess, family_excess=fam, B=rc_B)
        term_vs = _terminal(r) / _terminal(b) if _terminal(b) > 0 else float("nan")
        return r, b, d, um, rc, term_vs

    r_des, b_des, d_des, um_des, rc_des, tvs_des = split_metrics(di)
    r_hol, b_hol, d_hol, um_hol, rc_hol, tvs_hol = split_metrics(hi)

    # 비용 스트레스(2x)·10bp what-if — 홀드아웃 순초과(vs QQQ) 부호로 판정
    def net_excess_full(c):
        _, rr = sim_returns(base_params, c)
        rh = [rr[i] for i in hi]
        return _terminal(rh) / _terminal(b_hol) - 1.0 if _terminal(b_hol) > 0 else -1.0
    nx_1x = _terminal(r_hol) / _terminal(b_hol) - 1.0
    nx_2x = net_excess_full(c2d.cost_for(panel, mult=2.0))
    nx_10 = net_excess_full(c2d.cost_for(panel, commission_bps=10.0))
    cost_stress_mult = 2.0 if nx_2x > 0 else (1.0 if nx_1x > 0 else 0.0)

    # 회전/비용드래그(전체 구간)
    turnover_yr = sim_c.turnover_per_year()
    days_full = max((dates[-1] - dates[0]).days, 1)
    cagr_net = gate.cagr(gate.returns_to_equity(cand), days_full)
    cagr_gross = gate.cagr(gate.returns_to_equity(gross), days_full)
    cost_drag = cagr_gross - cagr_net

    # 서브구간 승률(전체)
    sub = gate.subperiod_consistency(gate.returns_to_equity(cand),
                                     gate.returns_to_equity(bench_stream), ret_dates)

    # 원장 적재(설계 center+이웃, 홀드아웃 center 1회 — peek-once)
    if log:
        cfg = dict(base_params); cfg["idea"] = idea_id
        gate_eval.log_evaluation(idea_id, cfg, 2, "design", um_des,
                                 gate_eval.money_weighted_metrics(benchmark_terminal=_terminal(b_des),
                                                                  dca_equity=gate.returns_to_equity(r_des)),
                                 window=(d_des[0], d_des[-1]) if d_des else None,
                                 universe=panel.universe, ledger_path=LEDGER)
        for j, p in enumerate(neighbors):
            npar = dict(p); npar["idea"] = idea_id; npar["neighbor"] = j
            um_n = gate_eval.unit_capital_metrics(streams[f"n{j}"], dates=d_des)
            gate_eval.log_evaluation(idea_id, npar, 2, "design", um_n, {},
                                     window=(d_des[0], d_des[-1]) if d_des else None,
                                     ledger_path=LEDGER)
        if hi:
            try:
                gate_eval.log_evaluation(idea_id, cfg, 2, "holdout", um_hol,
                                         gate_eval.money_weighted_metrics(
                                             benchmark_terminal=_terminal(b_hol),
                                             dca_equity=gate.returns_to_equity(r_hol)),
                                         window=(d_hol[0], d_hol[-1]), universe=panel.universe,
                                         ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    # 프로그램 전체 N_eff DSR(부록 v2.1-3) — 원장 적재 후 계산해 최신 N 반영.
    dsr_prog = gate.dsr_program(r_hol, LEDGER, idea_id) if hi else {}

    # 판정(홀드아웃 축). 절대 캡 유지(QQQ 벤치는 −50% 캡 준수 구간).
    rc_thresh = 0.01 if signal_reuse else 0.05
    axis = {
        "lane": 2, "biased_universe": False,
        "terminal_vs_b0": tvs_hol, "terminal_vs_b1": tvs_hol,
        "dsr": um_hol.get("dsr"), "rc_pvalue": rc_hol["rc_pvalue"],
        "plateau_pass": plateau_pass, "plateau_borderline": plateau_border,
        "subperiod_winrate": sub["win_rate"],
        "cost_stress_mult": cost_stress_mult,
        "mdd": um_hol.get("max_drawdown"),
    }
    decision = gate.decide(axis)
    # 신호 재사용 페널티: p<0.01 강화(RC·SPA 둘 다). 미달이면 유의성 축 강등.
    rc_ok = (rc_hol["rc_pvalue"] is not None and rc_hol["rc_pvalue"] < rc_thresh)
    spa_ok = (rc_hol["spa_pvalue"] is not None and rc_hol["spa_pvalue"] < rc_thresh)
    verdict = decision.verdict
    reasons = list(decision.reasons)
    tags = []
    if signal_reuse:
        tags.append("holdout_semi_contaminated")
        if not (rc_ok and spa_ok) and verdict == "PASS":
            verdict = "CONDITIONAL"
            reasons.append(f"신호재사용(v2.1-4): 반오염 홀드아웃 → RC/SPA p<0.01 요구, "
                           f"RCp={rc_hol['rc_pvalue']:.3f} SPAp={rc_hol['spa_pvalue']:.3f} 미달 → 강등")

    # PIT 커버리지(홀드아웃 구간 평균 as-of 멤버십 데이터 보유율)
    cover = pit_coverage(panel, design_end=design_end)

    return {
        "idea": idea_id, "params": base_params, "signal_reuse": signal_reuse,
        "design": {"cagr": um_des.get("cagr"), "sharpe": um_des.get("sr_annual"),
                   "mdd": um_des.get("max_drawdown"), "ulcer": um_des.get("ulcer"),
                   "terminal_vs_qqq": tvs_des, "dsr": um_des.get("dsr"),
                   "rc_p": rc_des["rc_pvalue"], "spa_p": rc_des["spa_pvalue"]},
        "holdout": {"cagr": um_hol.get("cagr"), "sharpe": um_hol.get("sr_annual"),
                    "mdd": um_hol.get("max_drawdown"), "ulcer": um_hol.get("ulcer"),
                    "terminal_vs_qqq": tvs_hol, "dsr": um_hol.get("dsr"),
                    "rc_p": rc_hol["rc_pvalue"], "spa_p": rc_hol["spa_pvalue"]},
        "dsr_program": dsr_prog,
        "turnover_yr": turnover_yr, "cost_drag": cost_drag,
        "cagr_gross": cagr_gross, "cagr_net": cagr_net,
        "nx_1x_holdout": nx_1x, "nx_2x_holdout": nx_2x, "nx_10bp_holdout": nx_10,
        "n_eff": n_eff, "n_trials": len(sr_trials),
        "plateau_pass": plateau_pass, "plateau_frac_pos": frac_pos,
        "subperiod_winrate": sub["win_rate"],
        "pit_coverage_holdout": cover,
        "verdict": verdict, "reasons": reasons, "tags": tags,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 생존편향 정량화: PIT 멤버 데이터 커버리지 + 무데이터(상장폐지/피인수) 로스터.
# ─────────────────────────────────────────────────────────────────────────────
def pit_coverage(panel: c2d.Panel, *, design_end: date = DESIGN_END) -> dict:
    """월별 as-of S&P500 멤버십 중 '데이터 보유 & live' 비율(설계/홀드아웃 각각 평균)."""
    recs = panel.pit_records                          # type: ignore[attr-defined]
    have = set(panel.close)
    me = [t for t, f in enumerate(research.month_end_flags(panel.dates)) if f]
    des_cov, hol_cov = [], []
    for t in me:
        m = pit_asof(recs, panel.dates[t])
        if not m:
            continue
        live = sum(1 for s in m if s in have and _pit_live(panel, s, t, 1))
        cov = live / len(m)
        (des_cov if panel.dates[t] <= design_end else hol_cov).append(cov)
    return {"design_mean": (sum(des_cov) / len(des_cov)) if des_cov else 0.0,
            "holdout_mean": (sum(hol_cov) / len(hol_cov)) if hol_cov else 0.0,
            "months": len(me)}


def survivorship_report(panel: c2d.Panel) -> dict:
    """무데이터 멤버(상장폐지/피인수/리네임) 정량화: 개수·멤버십 지속월수·대략적 규모 신호.

    보수적 가정(문서화): 무데이터 멤버는 백테스트 후보에서 제외된다(롱온리 → 그들의 손익을 경험 안 함).
    피인수(가격 존재 가정)면 마지막 가격에서 delisting return≈0, 파산/불명이면 −30% 가정이나,
    가격 자체가 없어 백테스트엔 편입 불가 → '잔존 편향 방향'만 서술한다.
    """
    recs = panel.pit_records                          # type: ignore[attr-defined]
    missing = panel.missing_members                   # type: ignore[attr-defined]
    # 각 무데이터 티커가 창 내 멤버였던 개월수(대략)
    me_dates = [panel.dates[t] for t, f in enumerate(research.month_end_flags(panel.dates)) if f]
    dur: dict[str, int] = {t: 0 for t in missing}
    for d in me_dates:
        m = pit_asof(recs, d)
        for t in missing:
            if t in m:
                dur[t] += 1
    active_missing = {t: n for t, n in dur.items() if n > 0}
    ranked = sorted(active_missing.items(), key=lambda x: -x[1])
    # fetch_status 로 '시도했으나 무데이터(상장폐지/피인수/리네임 확정)' vs '미시도(소스 스로틀)' 분리.
    attempted_nodata: list[str] = []
    if FETCH_STATUS.exists():
        try:
            st = json.loads(FETCH_STATUS.read_text())
            attempted_nodata = sorted(k for k, v in st.items()
                                      if v.get("status") == "nodata" and k in active_missing)
        except Exception:  # noqa: BLE001
            attempted_nodata = []
    not_attempted = sorted(t for t in active_missing if t not in set(attempted_nodata))
    return {"n_missing_total": len(missing),
            "n_missing_active_in_window": len(active_missing),
            "n_available": len(panel.universe),
            "n_confirmed_nodata_attempted": len(attempted_nodata),
            "n_not_fetched_throttled": len(not_attempted),
            "confirmed_nodata_sample": attempted_nodata[:30],
            "avg_missing_member_months": (sum(active_missing.values()) / len(active_missing))
            if active_missing else 0.0,
            "top_missing_by_tenure": ranked[:25]}


# ─────────────────────────────────────────────────────────────────────────────
# c2d 편향 유니버스 대조: 동일 mom top5 규칙을 c2d '오늘의 승자' 유니버스에 적용(원장 미적재).
# ─────────────────────────────────────────────────────────────────────────────
def c2d_biased_reference(cash_rate_fn=c2d.cash_rate_series) -> dict:
    """c2d 의 SINGLE_STOCKS(생존편향 유니버스)에서 12-1 mom top5 홀드아웃 CAGR — 생존편향 델타용."""
    import fetch_history as fh
    bp = c2d.load_panel(fh.SINGLE_STOCKS, calendar_symbol=BENCH, start=START, end=END)
    cr = cash_rate_fn(bp)
    dec = c2d.decide_momentum(bp, topk=5, lookback=252, skip=21, use_filter=False)
    sim = c2d.simulate_portfolio(bp, dec, c2d.cost_for(bp), cash_rate=cr)
    r = sim.returns[1:]
    ret_dates = bp.dates[1:]
    di = [i for i, d in enumerate(ret_dates) if d <= DESIGN_END]
    hi = [i for i, d in enumerate(ret_dates) if d > DESIGN_END]
    br = c2d.bench_returns(bp)[1:]

    def cagr_seg(idxs):
        rr = [r[i] for i in idxs]
        dd = [ret_dates[i] for i in idxs]
        return gate.cagr(gate.returns_to_equity(rr), max((dd[-1] - dd[0]).days, 1))

    def cagr_bench(idxs):
        rr = [br[i] for i in idxs]
        dd = [ret_dates[i] for i in idxs]
        return gate.cagr(gate.returns_to_equity(rr), max((dd[-1] - dd[0]).days, 1))

    return {"universe_n": len(bp.universe),
            "design_cagr": cagr_seg(di), "holdout_cagr": cagr_seg(hi),
            "qqq_design_cagr": cagr_bench(di), "qqq_holdout_cagr": cagr_bench(hi)}


def write_membership_summary(panel: c2d.Panel, recs: list[tuple[date, frozenset]],
                             out_csv: Path) -> dict:
    """월말 as-of S&P500 멤버십 수 + 데이터 커버리지를 커밋용 요약 CSV로 저장(reports/).

    컬럼: month_end, sp500_members, members_with_data, coverage_pct. 반환은 요약 통계.
    """
    have = set(panel.close)
    me = [t for t, f in enumerate(research.month_end_flags(panel.dates)) if f]
    rows = [("month_end", "sp500_members", "members_with_data", "coverage_pct")]
    covs = []
    for t in me:
        d = panel.dates[t]
        m = pit_asof(recs, d)
        if not m:
            continue
        live = sum(1 for s in m if s in have and _pit_live(panel, s, t, 1))
        cov = 100.0 * live / len(m)
        covs.append(cov)
        rows.append((d.isoformat(), str(len(m)), str(live), f"{cov:.1f}"))
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_csv.write_text("\n".join(",".join(r) for r in rows) + "\n")
    return {"months": len(covs), "avg_coverage_pct": sum(covs) / len(covs) if covs else 0.0,
            "min_members": min(int(r[1]) for r in rows[1:]) if len(rows) > 1 else 0,
            "max_members": max(int(r[1]) for r in rows[1:]) if len(rows) > 1 else 0}


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
IDEAS = [
    ("c3c_pit_mom5", pit_momentum, {"topk": 5, "lookback": 252, "skip": 21, "use_filter": False}, True),
    ("c3c_pit_residmom5", pit_resid_momentum, {"topk": 5, "lookback": 252, "skip": 21}, True),
    ("c3c_pit_mom5_trend", pit_momentum, {"topk": 5, "lookback": 252, "skip": 21, "use_filter": True}, True),
    ("c3c_pit_newentrant", pit_new_entrant, {"hold_m": 6, "min_hist": 21, "topk": 0}, False),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="c3c PIT 단일종목 모멘텀(생존편향 제거)")
    ap.add_argument("--no-ledger", action="store_true", help="원장 적재 생략(개발/디버그)")
    ap.add_argument("--out", type=str, default="", help="결과 JSON 저장 경로")
    ap.add_argument("--quick", action="store_true", help="RC 부트스트랩 축소(빠른 점검)")
    ap.add_argument("--no-c2d-ref", action="store_true", help="c2d 편향 대조 생략")
    args = ap.parse_args()
    log = not args.no_ledger
    rc_B = 400 if args.quick else 1500

    recs = load_pit_records()
    panel = build_pit_panel(recs)
    cr = c2d.cash_rate_series(panel)
    br = c2d.bench_returns(panel)
    print(f"PIT 유니버스(가용): {len(panel.universe)}종목 | 거래일 {len(panel.dates)} "
          f"({panel.dates[0]}~{panel.dates[-1]})", flush=True)

    surv = survivorship_report(panel)
    print(f"생존편향: 무데이터 멤버 {surv['n_missing_active_in_window']}개(창내 활성) / "
          f"가용 {surv['n_available']}개, 평균 멤버 {surv['avg_missing_member_months']:.0f}개월", flush=True)

    summ = write_membership_summary(panel, recs, ROOT / "reports" / "cycle3_c3c_pit_membership.csv")
    print(f"멤버십 요약 CSV: 월수 {summ['months']}, 멤버 {summ['min_members']}~{summ['max_members']}, "
          f"평균 데이터 커버리지 {summ['avg_coverage_pct']:.1f}%", flush=True)

    results: dict = {"universe": panel.universe, "n_days": len(panel.dates),
                     "dates": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
                     "survivorship": surv, "membership_summary": summ}

    results["ideas"] = {}
    for idea_id, fn, params, reuse in IDEAS:
        print(f"\n=== {idea_id} {params} reuse={reuse} ===", flush=True)
        res = evaluate_pit_idea(panel, idea_id, fn, params, br, cr,
                                signal_reuse=reuse, log=log, rc_B=rc_B)
        results["ideas"][idea_id] = res
        h = res["holdout"]
        dp = res["dsr_program"]
        print(f"  홀드 CAGR {h['cagr']:.2%} Sharpe {h['sharpe']:.2f} MDD {h['mdd']:.1%} "
              f"vsQQQ×{h['terminal_vs_qqq']:.2f} DSR {h['dsr']} "
              f"DSR_prog {dp.get('dsr_program')} RCp {h['rc_p']} SPAp {h['spa_p']} "
              f"→ {res['verdict']} {res['tags']}", flush=True)

    if not args.no_c2d_ref:
        print("\n=== c2d 편향 유니버스 대조(동일 mom top5) ===", flush=True)
        ref = c2d_biased_reference()
        results["c2d_reference"] = ref
        pit_h = results["ideas"]["c3c_pit_mom5"]["holdout"]["cagr"]
        print(f"  c2d 편향 홀드 CAGR {ref['holdout_cagr']:.2%} vs PIT 홀드 CAGR {pit_h:.2%} "
              f"vs QQQ {ref['qqq_holdout_cagr']:.2%} "
              f"→ 생존편향 델타 ≈ {ref['holdout_cagr'] - pit_h:+.1%}p", flush=True)

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\n결과 저장: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
