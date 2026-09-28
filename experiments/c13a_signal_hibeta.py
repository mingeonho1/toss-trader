#!/usr/bin/env python3
"""c13a — 승리 타이밍 신호를 **고베타 단일주식 바스켓**으로 실행(Lane A, 부록 v3 · 신호재사용 §4).

동기(사용자 제약): Lane A 승자(c12_b1_ftlt·c12_b2_holygrail·c12_b5_simple·c12_a1_lrs·c12_a2_buffer)는
전부 **레버리지 ETP(TQQQ/SOXL/TECL/…)** 로 '위험선호 3x' 레그를 실행한다. 그러나 이력 없는 소액
한국 리테일 계좌는 2026-05-22 시행 규제로 해외 레버리지 ETP 첫 거래에 기본예탁금(₩1,000만)+사전교육이
필요해 **실거래로 못 살 수 있다**. 반면 일반 고베타 단일주식 바스켓(c12s_hibeta_basket, 실현 β≈1.6)은
소수점 매수로 **자유롭게 거래 가능**하며 자체로 Lane A PASS 했다(reports/cycle12_hibeta.md).

**신규 조합 c13a(사전등록):** 승리 신호의 결정트리는 **그대로**(재튜닝 없음, c12 파라미터 동결) 두되,
  · '위험선호 3x' 레그(TQQQ/SPXL/TECL/SOXL/…)  → **고베타 바스켓(HIBETA, 실현 β≈1.6)**
  · 방어/위험회피 레그                         → 허용 1x(현금/BIL/SHY/QQQ)
  · UVXY '변동성 스파이크' 레그                → **현금**(깨끗한 1x 대체 없음)
  · 인버스 레그(SQQQ 등)                        → 1차=**현금**, 별도 변형=**PSQ(−1x QQQ, 있으면)** 로 시험
    (PSQ 등 인버스 ETF도 한국 규제 게이트에 걸릴 수 있어 '허용처럼 보이는' 수단 — **규제 불확실성 플래그**).

바스켓은 HIBETA 합성가격(월간 리밸런스·구성종목 회전비용 내재)으로 넣고, 신호는 그 레그 전체를 매일
in/out 스위칭한다. 레그 스위치 = 다음 종가(primary, exec_lag=1) / 다음 시가(변형). 바스켓 내부 월간
리밸런스는 c12s 그대로 다음 시가.

**사전등록 5설정(각 1 config + c12 동일 이웃집합, 그리드서치 없음):**
  1. c13a_ftlt_hibeta      (신호 = c12_b1_ftlt)
  2. c13a_holygrail_hibeta (신호 = c12_b2_holygrail)
  3. c13a_lrs_hibeta       (신호 = c12_a1_lrs, A1 LRS200)
  4. c13a_buffer_hibeta    (신호 = c12_a2_buffer, A2 200SMA +5/−3 버퍼)
  5. c13a_simple_hibeta    (신호 = c12_b5_simple)

**신호 재사용(부록 v2.1 §4):** 이 신호들의 홀드아웃은 c12 에서 이미 관측됨 → 새 idea_id 를 쓰되
홀드아웃을 **반오염(holdout_semi_contaminated)** 으로 표기하고, 유의성 임계를 **RC/SPA p<0.01** 로 강화한다.
Lane A 판정(부록 v3: a·b·c + 파산가드)은 그대로 적용하되 **RC/SPA p 도 병기**(더 엄격한 <0.01 명시).

**비용(task):** 0.1%/side(10bp) + 반호가(주식=대형주 3bp / 1x ETF 1bp) + 슬리피지 5bp. 2× 스트레스 +
≤$10 분할 micro(매수무료). **데이터 한계:** 고베타 바스켓은 PIT S&P500 멤버 중 캐시보유 ~27~31%
(대형·생존주 편중) → 모든 성과는 **상한(UPPER BOUND)**. 커버리지 보고.

재현: PYTHONPATH=src .venv/bin/python experiments/c13a_signal_hibeta.py [--no-ledger] [--no-report] [--quick]
Do NOT edit src/. src·gate·research·histdata·c12_* 는 import(소비)만 한다.
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
import c2d_stocks as c2d  # noqa: E402
import c12_composer as CC  # noqa: E402
import c12_trend_vol as CT  # noqa: E402
import c12_hibeta as CH  # noqa: E402

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
REPORT = ROOT / "reports" / "cycle13_c13a_signal_hibeta.md"
RESULTS_JSON = ROOT / "reports" / "c13a_results.json"

DESIGN_END = date(2021, 12, 31)          # c12 실물 분할과 동일
LANE = "A"
PPY = 252
HIBETA = "HIBETA"                          # 합성 고베타 바스켓 심볼

# ── 바스켓 실행 사전등록(c12s_hibeta_basket = 유일 Lane A PASS, 아이디어1) ──
BASKET_PARAMS = dict(topk=10, beta_win=CH.BETA_WIN, dv_top=CH.DV_TOP,
                     min_price=CH.MIN_PRICE, use_filter=False)

# ── c13a 레그 매핑 ────────────────────────────────────────────────────────────
# '위험선호 3x' 롱 레버리지 → 고베타 바스켓(HIBETA).
RISK_ON_3X = {"TQQQ", "SPXL", "UPRO", "TECL", "SOXL", "FAS", "HIBL", "QLD", "SSO",
              "TNA", "CURE", "LABU", "DRN", "FNGU"}
# 변동성 ETP → 현금(깨끗한 1x 없음).
VOL = {"UVXY", "VIXY", "VIXM", "SVIX", "SVXY"}
# 인버스(하방 베팅) → 1차 현금 / 변형 PSQ.
INVERSE = {"SQQQ", "SOXS", "SPXU", "TECS", "SH", "PSQ"}
# 채권/현금성 방어 레그 → 허용 1x(BIL/SHY).
BOND_MAP = {"BSV": "SHY", "BIL": "BIL", "SHY": "SHY", "SHV": "BIL", "IEF": "SHY",
            "TMF": "SHY", "TLT": "SHY", "AGG": "SHY", "BOXX": "BIL"}
# 1x 주식 ETF(허용) → QQQ 대용(승자엔 미등장; 견고성용).
EQ1X = {"QQQ": "QQQ", "SPY": "QQQ", "SMH": "QQQ"}

C13A_UNIVERSE = [HIBETA, "QQQ", "PSQ", "BIL", "SHY"]


def remap_leg(w: dict, *, inverse_to: str = "cash") -> tuple[dict, set]:
    """원 신호 목표비중(레버리지 심볼) → c13a 실행 심볼. 반환 (remapped, 미매핑심볼집합).

    inverse_to='cash'(1차) 또는 'PSQ'(변형). 현금 = 심볼 드롭(잔여는 cash_rate 이자).
    """
    out: dict[str, float] = {}
    unknown: set[str] = set()
    for s, wt in w.items():
        if s in RISK_ON_3X:
            tgt = HIBETA
        elif s in VOL:
            tgt = None                          # 현금
        elif s in INVERSE:
            tgt = "PSQ" if inverse_to == "PSQ" else None
        elif s in BOND_MAP:
            tgt = BOND_MAP[s]
        elif s in EQ1X:
            tgt = EQ1X[s]
        elif s in ("BIL", "SHY", "QQQ", "PSQ"):
            tgt = s
        else:
            unknown.add(s)
            tgt = None                          # 미지 심볼 → 현금(보수적)
        if tgt is not None:
            out[tgt] = out.get(tgt, 0.0) + wt
    return out, unknown


def identity_leg(w: dict, *, inverse_to=None) -> tuple[dict, set]:
    """c12 TQQQ 실행 재현용(항등)."""
    return dict(w), set()


# ── 비용 ─────────────────────────────────────────────────────────────────────
BPS = 1e-4


def c13a_cost(mult: float = 1.0) -> R.CostSpec:
    """0.1%/side(10bp) + 반호가(주식 바스켓 3bp / 1x ETF 1bp) + 5bp 슬리피지. mult=스트레스."""
    cs = R.CostSpec(commission_bps=10.0, slippage_bps=5.0, fx_bps=20.0,
                    half_spread_bps={HIBETA: 3.0}, default_half_spread_bps=1.0)
    return cs.stress(mult) if mult != 1.0 else cs


def micro_fee_fn(comm=10.0, hs=3.0, slip=5.0):
    """≤$10 분할 무료 micro: 매수 수수료 0(시장비용만), 매도만 수수료. 반호가는 바스켓 대표 3bp."""
    def fn(side: str, notional: float) -> float:
        n = abs(notional)
        market = (hs + slip) * BPS * n
        return market if side.upper() == "BUY" else market + comm * BPS * n
    return fn


# ── 심볼 종가/시가 맵(캐시) ──────────────────────────────────────────────────
_SYM_CACHE: dict[str, tuple[dict, dict]] = {}


def sym_maps(sym: str) -> tuple[dict, dict]:
    if sym not in _SYM_CACHE:
        cs = hd.load_symbol(sym)
        _SYM_CACHE[sym] = ({c.dt: c.close for c in cs}, {c.dt: c.open for c in cs})
    return _SYM_CACHE[sym]


# ── HIBETA 합성 바스켓(월간 리밸런스·구성종목 비용 내재, 초기매수 비용 제외) ──
_HIB: dict | None = None


def build_hibeta() -> dict:
    """c12s_hibeta_basket(아이디어1)의 일별 NET 수익 → 합성가격 px[0]=1. 커버리지·실현β 포함.

    px 는 sim.returns[1:] 만 복리하므로 c2d 의 **초기 매수비용은 제외**(run_weights 가 레그 진입 시
    별도 과금 → 이중부과 없음). 월간 리밸런스 구성종목 회전비용은 px 에 내재(레그 보유일에만 반영됨)."""
    global _HIB
    if _HIB is not None:
        return _HIB
    panel, _ = CH.build_panel()
    cash = c2d.cash_rate_series(panel)
    dec = CH.decide_hibeta_basket(panel, **BASKET_PARAMS)
    sim = c2d.simulate_portfolio(panel, dec, CH.cost_for(panel), cash_rate=cash)
    rets = sim.returns                         # len n, [0]=0
    dates = panel.dates
    px = [1.0]
    for r in rets[1:]:
        px.append(px[-1] * (1.0 + r))
    br = c2d.bench_returns(panel)               # QQQ 일수익, [0]=0
    # 실현 베타(설계/홀드아웃/전구간)
    di = [i for i in range(1, len(dates)) if dates[i] <= DESIGN_END]
    ho = [i for i in range(1, len(dates)) if dates[i] > DESIGN_END]
    beta = {
        "design": CH.realized_beta([rets[i] for i in di], [br[i] for i in di]),
        "holdout": CH.realized_beta([rets[i] for i in ho], [br[i] for i in ho]),
        "full": CH.realized_beta(rets[1:], br[1:]),
    }
    import c3c_pit as c3c
    cov = c3c.pit_coverage(panel)
    _HIB = {"dates": dates, "px": px, "close": {dates[i]: px[i] for i in range(len(dates))},
            "beta": beta, "coverage": cov, "turnover_yr": sim.turnover_per_year()}
    return _HIB


# ── 실행 엔진(레인1 run_weights: 드리프트 인지·레그 스위치에만 과금) ──────────
def run_exec(dates, weights, closes_map, opens_map, syms, cost, *, exec_lag=1,
             use_open=False, fee_fn=None, cash_rate=None):
    pc = {s: [closes_map[s][d] for d in dates] for s in syms}
    po = {s: [opens_map[s][d] for d in dates] for s in syms} if use_open else None
    return R.run_weights(pc, dates, weights, exec_lag=exec_lag, cost=cost,
                         cash_rate=cash_rate, exec_price="open" if use_open else "close",
                         panel_opens=po, fee_fn=fee_fn, start_equity=1.0)


# ── 설정 스펙 ─────────────────────────────────────────────────────────────────
@dataclass
class Cfg:
    cid: str                 # c13a_*
    base_id: str             # 원 신호 idea_id(c12_*)
    name: str
    build: object            # (S, dates, p) -> per-day weight dicts(원 심볼)
    symbols: list            # 신호가 참조하는 ETF 심볼(신호 패널 정합용)
    base_p: dict
    neighbors: list          # [(label, p)]
    has_inverse: bool
    pub_oos: date | None = None
    note: str = ""


def _composer_builder(strat):
    return lambda S, dates, p, b=strat.build: b(S, dates, p)


def _trendvol_builder(sig):
    def build(S, dates, p, _sig=sig):
        tw, _sd = _sig(S, dates, **p)
        return tw
    return build


def build_specs() -> list[Cfg]:
    reg = {s.sid: s for s in CC.registry()}
    b1, b2, b5 = reg["c12_b1_ftlt"], reg["c12_b2_holygrail"], reg["c12_b5_simple"]

    # A1/A2: trend_vol 이웃집합(비-fast) 그대로 → (label, p) 정규화
    a1_nb = [(f"sma{w}", {"sma_win": w, "lev": "TQQQ"}) for w in (150, 175, 225, 250)]
    a2_grid = [(0.03, 0.02), (0.04, 0.03), (0.06, 0.04), (0.05, 0.02), (0.04, 0.04), (0.06, 0.05)]
    a2_nb = [(f"e{e:g}x{x:g}", {"entry": e, "exit": x, "lev": "TQQQ"}) for e, x in a2_grid]
    a12_syms = ["QQQ", "TQQQ", "BIL"]

    return [
        Cfg("c13a_ftlt_hibeta", "c12_b1_ftlt", "FTLT(namuan) → 고베타 바스켓",
            _composer_builder(b1), b1.symbols, b1.base_p, b1.neighbors,
            has_inverse=True, pub_oos=date(2023, 1, 1),
            note="위험선호 TQQQ/SPXL/TECL→HIBETA; SQQQ→현금(변형 PSQ); UVXY→현금; BSV→SHY."),
        Cfg("c13a_holygrail_hibeta", "c12_b2_holygrail", "Holy Grail → 고베타 바스켓",
            _composer_builder(b2), b2.symbols, b2.base_p, b2.neighbors,
            has_inverse=True, pub_oos=date(2022, 8, 1),
            note="TQQQ/TECL/SOXL→HIBETA; SQQQ→현금(변형 PSQ); UVXY→현금; BSV→SHY."),
        Cfg("c13a_lrs_hibeta", "c12_a1_lrs", "A1 LRS 200SMA → 고베타 바스켓",
            _trendvol_builder(CT.sig_a1), a12_syms, {"sma_win": 200, "lev": "TQQQ"}, a1_nb,
            has_inverse=False, pub_oos=date(2016, 4, 1),
            note="QQQ>SMA200 → HIBETA else 현금(인버스 레그 없음 → PSQ 변형 무의미)."),
        Cfg("c13a_buffer_hibeta", "c12_a2_buffer", "A2 200SMA +5/−3 → 고베타 바스켓",
            _trendvol_builder(CT.sig_a2), a12_syms,
            {"entry": 0.05, "exit": 0.03, "lev": "TQQQ"}, a2_nb,
            has_inverse=False, pub_oos=date(2024, 1, 1),
            note="QQQ 200SMA +5/−3 버퍼 → HIBETA else 현금."),
        Cfg("c13a_simple_hibeta", "c12_b5_simple", "Simple RSI(TQQQ,10)>79 → 고베타 바스켓",
            _composer_builder(b5), b5.symbols, b5.base_p, b5.neighbors,
            has_inverse=False, pub_oos=date(2024, 10, 1),
            note="TQQQ→HIBETA; UVXY(방어)→현금; BIL 유지. 인버스 없음."),
    ]


# ── 신호 패널 로드 ────────────────────────────────────────────────────────────
def load_signal_panel(cfg: Cfg):
    """cfg.symbols 를 공통거래일로 정합해 (dates, S(closes dict)) 반환. A1/A2 는 CT 패널."""
    if cfg.base_id in ("c12_a1_lrs", "c12_a2_buffer"):
        p = CT.build_panel(cfg.symbols)
        return p.dates, p.full()
    dates, S, opens, closes = CC.load_series(cfg.symbols, ohlc=None)
    return dates, S


# ── 지표 헬퍼 ────────────────────────────────────────────────────────────────
def _metrics(net, ret_dates):
    m = GE.unit_capital_metrics(net, dates=ret_dates)
    return {"cagr": m.get("cagr"), "mdd": m.get("max_drawdown"),
            "sharpe": m.get("sr_annual"), "cvar5": m.get("cvar5")}


def _qqq_returns(dates, qmap):
    cl = [qmap[d] for d in dates]
    return [0.0] + [cl[t] / cl[t - 1] - 1.0 if cl[t - 1] > 0 else 0.0 for t in range(1, len(dates))]


def _realized_beta_split(net, qret, ret_dates, lo, hi):
    idx = [i for i, d in enumerate(ret_dates) if lo < d <= hi] if lo else \
          [i for i, d in enumerate(ret_dates) if d <= hi]
    if len(idx) < 2:
        return float("nan")
    return CH.realized_beta([net[i] for i in idx], [qret[i] for i in idx])


# ── 단일 설정 평가 ────────────────────────────────────────────────────────────
def evaluate(cfg: Cfg, hib: dict, *, do_log=True, rc_B=1500):
    sig_dates, S = load_signal_panel(cfg)

    # 실행 심볼 맵(HIBETA 는 합성; 그 외 실물)
    ext = {}
    for s in ("QQQ", "PSQ", "BIL", "SHY", "SMH", "SPY", "SH"):
        ext[s] = sym_maps(s)
    hib_close = hib["close"]

    # 공통 거래일 D = 신호일 ∩ HIBETA ∩ (실행 ETF 전부 존재)
    need_ext = ["QQQ", "PSQ", "BIL", "SHY"]
    D = [d for d in sig_dates if d in hib_close and all(d in ext[s][0] for s in need_ext)]
    ret_dates = D[1:]

    # 실행 종가/시가 맵(HIBETA 시가=종가)
    closes_map = {HIBETA: hib_close, "QQQ": ext["QQQ"][0], "PSQ": ext["PSQ"][0],
                  "BIL": ext["BIL"][0], "SHY": ext["SHY"][0]}
    opens_map = {HIBETA: hib_close, "QQQ": ext["QQQ"][1], "PSQ": ext["PSQ"][1],
                 "BIL": ext["BIL"][1], "SHY": ext["SHY"][1]}
    cash_rate = R.cash_rate_from_prices([ext["BIL"][0][d] for d in D])

    # QQQ 일수익 — net_stream/ret_dates(길이 len(D)-1)와 동일 정렬(시드 제거).
    qret = _qqq_returns(D, ext["QQQ"][0])[1:]

    # 원 신호 목표비중(전 신호일) → 날짜 인덱스
    W_full = cfg.build(S, sig_dates, cfg.base_p)
    wmap = {sig_dates[i]: W_full[i] for i in range(len(sig_dates))}

    def remapped_weights(inverse_to="cash", pmap=None):
        src = pmap if pmap is not None else wmap
        ws, unk = [], set()
        for d in D:
            w = src.get(d, {})
            rw, u = remap_leg(w, inverse_to=inverse_to)
            ws.append(rw)
            unk |= u
        return ws, unk

    base_cost = c13a_cost(1.0)

    # ── primary: hibeta-exec, exec_lag=1 종가 ──
    w_prim, unknown = remapped_weights("cash")
    res = run_exec(D, w_prim, closes_map, opens_map, C13A_UNIVERSE, base_cost,
                   exec_lag=1, cash_rate=cash_rate)
    net = res.net_stream()
    full = _metrics(net, ret_dates)
    splits = CC.split_cagrs(net, ret_dates)
    years = max((D[-1] - D[0]).days / 365.25, 1e-9)

    # 벤치 QQQ / 고베타 바스켓 B&H(같은 D)
    qcl = [ext["QQQ"][0][d] for d in D]
    hcl = [hib_close[d] for d in D]
    di = [i for i in range(len(D)) if D[i] <= DESIGN_END]
    ho = [i for i in range(len(D)) if D[i] > DESIGN_END]

    def bh(cl, idxs):
        return CC.bh_cagr(cl, D, idxs[0], idxs[-1]) if len(idxs) > 1 else float("nan")
    bench = {
        "qqq": {"full": bh(qcl, list(range(len(D)))), "design": bh(qcl, di), "holdout": bh(qcl, ho)},
        "hibeta_bh": {"full": bh(hcl, list(range(len(D)))), "design": bh(hcl, di), "holdout": bh(hcl, ho)},
    }

    # ── c12 TQQQ 실행 재현(같은 D, c12 비용) ──
    tqqq = _run_c12_baseline(cfg, S, sig_dates, D, cash_rate, kind="tqqq")
    shadow = _run_c12_baseline(cfg, S, sig_dates, D, cash_rate, kind="shadow")

    # ── 2× 스트레스 · micro · MOC · 다음시가 변형 ──
    res2 = run_exec(D, w_prim, closes_map, opens_map, C13A_UNIVERSE, c13a_cost(2.0),
                    exec_lag=1, cash_rate=cash_rate)
    splits2 = CC.split_cagrs(res2.net_stream(), ret_dates)
    res_micro = run_exec(D, w_prim, closes_map, opens_map, C13A_UNIVERSE, base_cost,
                         exec_lag=1, fee_fn=micro_fee_fn(), cash_rate=cash_rate)
    res_moc = run_exec(D, w_prim, closes_map, opens_map, C13A_UNIVERSE, base_cost,
                       exec_lag=0, cash_rate=cash_rate)
    res_open = run_exec(D, w_prim, closes_map, opens_map, C13A_UNIVERSE, base_cost,
                        exec_lag=1, use_open=True, cash_rate=cash_rate)
    micro_full = _metrics(res_micro.net_stream(), ret_dates)["cagr"]
    moc_full = _metrics(res_moc.net_stream(), ret_dates)["cagr"]
    open_full = _metrics(res_open.net_stream(), ret_dates)["cagr"]

    # ── 이웃 평탄성(FULL CAGR vs QQQ FULL) + RC 가족(홀드아웃 초과) ──
    neigh, n_beat = [], 0
    fam_hold = {}
    for label, p in cfg.neighbors:
        try:
            nb_full = cfg.build(S, sig_dates, p)
        except Exception as e:  # noqa: BLE001
            neigh.append({"label": label, "error": str(e)})
            continue
        nbmap = {sig_dates[i]: nb_full[i] for i in range(len(sig_dates))}
        nw, _ = remapped_weights("cash", pmap=nbmap)
        nres = run_exec(D, nw, closes_map, opens_map, C13A_UNIVERSE, base_cost,
                        exec_lag=1, cash_rate=cash_rate)
        nnet = nres.net_stream()
        nm = _metrics(nnet, ret_dates)
        beat = nm["cagr"] is not None and not math.isnan(nm["cagr"]) and nm["cagr"] > bench["qqq"]["full"]
        n_beat += int(beat)
        neigh.append({"label": label, "cagr": nm["cagr"], "mdd": nm["mdd"], "beat_qqq": beat})
        fam_hold[label] = [nnet[i] - qret[i] for i in range(len(ret_dates)) if ret_dates[i] > DESIGN_END]
        if do_log:
            di_n = [i for i in range(len(ret_dates)) if ret_dates[i] <= DESIGN_END]
            if len(di_n) > 1:
                um = GE.unit_capital_metrics([nnet[i] for i in di_n],
                                             dates=[ret_dates[i] for i in di_n])
                GE.log_evaluation(cfg.cid, {"neighbor": label, "signal_reuse": True,
                                            "semi_contaminated": True},
                                  LANE, "design", um,
                                  window=(ret_dates[di_n[0]], ret_dates[di_n[-1]]),
                                  universe=C13A_UNIVERSE, ledger_path=LEDGER)
    n_neigh = sum(1 for x in neigh if "cagr" in x)
    neigh_frac = (n_beat / n_neigh) if n_neigh else 0.0

    # ── RC / SPA (홀드아웃 초과, 부록 v2.1 §4: 반오염 → 임계 <0.01) ──
    hi_idx = [i for i in range(len(ret_dates)) if ret_dates[i] > DESIGN_END]
    cand_excess = [net[i] - qret[i] for i in hi_idx]
    rc = GE.reality_check(cand_excess, family_excess=fam_hold, B=rc_B) if len(cand_excess) > 30 \
        else {"rc_pvalue": None, "spa_pvalue": None}
    sig_ok = (rc["rc_pvalue"] is not None and rc["rc_pvalue"] < 0.01
              and rc["spa_pvalue"] is not None and rc["spa_pvalue"] < 0.01)

    # ── 실현 베타(c13a hibeta-exec) ──
    rbeta = {
        "design": _realized_beta_split(net, qret, ret_dates, None, DESIGN_END),
        "holdout": _realized_beta_split(net, qret, ret_dates, DESIGN_END, D[-1]),
        "full": CH.realized_beta(net, qret),
    }

    # ── PSQ 변형(인버스 있는 설정만) ──
    psq = None
    if cfg.has_inverse:
        wp, _ = remapped_weights("PSQ")
        rp = run_exec(D, wp, closes_map, opens_map, C13A_UNIVERSE, base_cost,
                      exec_lag=1, cash_rate=cash_rate)
        sp = CC.split_cagrs(rp.net_stream(), ret_dates)
        mp = _metrics(rp.net_stream(), ret_dates)
        vp = CC.lane_a_verdict(sp, CC.split_cagrs(
            run_exec(D, wp, closes_map, opens_map, C13A_UNIVERSE, c13a_cost(2.0),
                     exec_lag=1, cash_rate=cash_rate).net_stream(), ret_dates),
            bench, neigh_frac, mp["mdd"])
        psq = {"full_cagr": mp["cagr"], "mdd": mp["mdd"], "splits": sp, "verdict": vp}

    # ── post-publication OOS ──
    pub = None
    if cfg.pub_oos is not None:
        pidx = [i for i, d in enumerate(ret_dates) if d > cfg.pub_oos]
        if len(pidx) > 2:
            pm = GE.unit_capital_metrics([net[i] for i in pidx], dates=[ret_dates[i] for i in pidx])
            fidx = [i for i in range(len(D)) if D[i] >= cfg.pub_oos]
            pub = {"start": cfg.pub_oos.isoformat(), "n": len(pidx), "cagr": pm.get("cagr"),
                   "mdd": pm.get("max_drawdown"), "qqq": bh(qcl, fidx)}

    verdict = CC.lane_a_verdict(splits, splits2, bench, neigh_frac, full["mdd"])

    # ── 헤드라인 원장 적재(설계 1회 + 홀드아웃 peek-once, 반오염 표기) ──
    if do_log:
        di_h = [i for i in range(len(ret_dates)) if ret_dates[i] <= DESIGN_END]
        hi_h = [i for i in range(len(ret_dates)) if ret_dates[i] > DESIGN_END]
        hp = {"headline": True, "signal_reuse": True, "semi_contaminated": True,
              "base_signal": cfg.base_id, "exec": "hibeta_basket"}
        if len(di_h) > 1:
            um_d = GE.unit_capital_metrics([net[i] for i in di_h], dates=[ret_dates[i] for i in di_h])
            GE.log_evaluation(cfg.cid, hp, LANE, "design", um_d,
                              window=(ret_dates[di_h[0]], ret_dates[di_h[-1]]),
                              universe=C13A_UNIVERSE, ledger_path=LEDGER)
        if len(hi_h) > 1 and not gate.already_peeked(LEDGER, cfg.cid):
            um_h = GE.unit_capital_metrics([net[i] for i in hi_h], dates=[ret_dates[i] for i in hi_h])
            try:
                GE.log_evaluation(cfg.cid, hp, LANE, "holdout", um_h,
                                  window=(ret_dates[hi_h[0]], ret_dates[hi_h[-1]]),
                                  universe=C13A_UNIVERSE, ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    return {
        "cid": cfg.cid, "base_id": cfg.base_id, "name": cfg.name, "note": cfg.note,
        "first": D[0].isoformat(), "last": D[-1].isoformat(), "n_days": len(D), "years": years,
        "unknown_symbols": sorted(unknown),
        "full": full, "trades_per_yr": res.trade_count / years,
        "turnover_per_yr": sum(res.turnover) / years, "total_cost": res.total_cost,
        "splits": splits, "bench": bench, "realized_beta": rbeta,
        "cost2x": {"design": splits2["design"]["cagr"], "holdout": splits2["holdout"]["cagr"]},
        "micro_full": micro_full, "moc_full": moc_full, "nextopen_full": open_full,
        "neighbors": neigh, "neigh_frac": neigh_frac, "n_neigh": n_neigh,
        "rc_p": rc["rc_pvalue"], "spa_p": rc["spa_pvalue"], "sig_reuse_pass": sig_ok,
        "c12_tqqq": tqqq, "shadow_1x": shadow, "psq_variant": psq, "pub_oos": pub,
        "verdict": verdict,
    }


def _run_c12_baseline(cfg, S, sig_dates, D, cash_rate, *, kind):
    """같은 D 에서 c12 원 실행(TQQQ) 또는 1x 섀도 재현. full/design/holdout CAGR + MDD."""
    W = cfg.build(S, sig_dates, cfg.base_p)
    wmap = {sig_dates[i]: W[i] for i in range(len(sig_dates))}
    if kind == "tqqq":
        # 원 심볼 그대로. 신호 심볼 종가는 S 에 있음(+QQQ/TQQQ 폴백).
        syms = list(dict.fromkeys(list(cfg.symbols)))
        cmap = {}
        for s in syms:
            if s in S:
                cmap[s] = {sig_dates[i]: S[s][i] for i in range(len(sig_dates))}
            else:
                cmap[s] = sym_maps(s)[0]
        Dk = [d for d in D if all(d in cmap[s] for s in syms)]
        weights = [wmap.get(d, {}) for d in Dk]
        cost = CC.build_cost(1.0) if cfg.base_id.startswith("c12_b") else CT.cost_spec(syms)
        cr = R.cash_rate_from_prices([sym_maps("BIL")[0][d] for d in Dk])
        res = run_exec(Dk, weights, {s: cmap[s] for s in syms}, {}, syms, cost,
                       exec_lag=1, cash_rate=cr)
    else:  # 1x 섀도
        if cfg.base_id.startswith("c12_b"):
            shw = CC.shadow_weights([wmap.get(d, {}) for d in D])
            uni = CC.SHADOW_UNIVERSE
            cmap = {s: sym_maps(s)[0] for s in uni}
            Dk = [d for d in D if all(d in cmap[s] for s in uni)]
            weights = CC.shadow_weights([wmap.get(d, {}) for d in Dk])
            cost = CC.build_cost(1.0)
        else:  # A1/A2 섀도 = TQQQ→QQQ
            uni = ["QQQ"]
            cmap = {"QQQ": sym_maps("QQQ")[0]}
            Dk = [d for d in D if d in cmap["QQQ"]]
            weights = [{("QQQ" if k == "TQQQ" else k): v for k, v in wmap.get(d, {}).items()} for d in Dk]
            cost = CT.cost_spec(["QQQ"])
        cr = R.cash_rate_from_prices([sym_maps("BIL")[0][d] for d in Dk])
        res = run_exec(Dk, weights, cmap, {}, uni, cost, exec_lag=1, cash_rate=cr)
    net = res.net_stream()
    rd = Dk[1:]
    full = _metrics(net, rd)
    sp = CC.split_cagrs(net, rd)
    return {"full_cagr": full["cagr"], "mdd": full["mdd"],
            "design": sp["design"]["cagr"], "holdout": sp["holdout"]["cagr"]}


# ── 리포트 ───────────────────────────────────────────────────────────────────
def _pc(v, nd=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:+.{nd}f}%"


def _f(v, nd=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:.{nd}f}"


PREREG = """# Cycle 13 — c13a: 승리 타이밍 신호 × 고베타 단일주식 바스켓 (Lane A)

> 사전등록 2026-09-28 · 결과 보기 전 확정 · src 미수정 · 원장 lane "A" · 신호재사용(부록 v2.1 §4)

## 사전등록 (가설·규칙·파라미터, 결과 보기 전)

**동기.** Lane A 승자(c12_b1_ftlt·b2_holygrail·b5_simple·a1_lrs·a2_buffer)는 전부 레버리지 ETP 로
'위험선호 3x' 레그를 실행한다. 이력 없는 한국 소액 계좌는 2026-05-22 규제(기본예탁금 ₩1,000만 + 사전교육)
때문에 이를 실거래로 못 살 수 있다. 반면 일반 고베타 단일주식 바스켓(c12s_hibeta_basket, 실현 β≈1.6)은
소수점 매수로 자유 거래 가능하며 자체로 Lane A PASS 했다.

**신규 조합(재튜닝 없음, c12 신호 파라미터 동결).** 승리 신호의 결정트리는 그대로. 실행만 재배선:
'위험선호 3x' 레그 → **고베타 바스켓(HIBETA)**; 방어/위험회피 → 허용 1x(현금/BIL/SHY/QQQ);
UVXY 변동성 레그 → **현금**; 인버스(SQQQ 등) → 1차 **현금**, 별도 변형 **PSQ(−1x QQQ)** — PSQ 등 인버스
ETF 도 규제 게이트 가능성 있어 '허용처럼 보이는' 수단(**규제 불확실성 플래그**).

**사전등록 5설정 + c12 동일 이웃집합:** c13a_ftlt_hibeta, c13a_holygrail_hibeta, c13a_lrs_hibeta,
c13a_buffer_hibeta, c13a_simple_hibeta.

**Lane A PASS(부록 v3):** (a) 설계·홀드아웃 **모두** CAGR>QQQ B&H, (b) 이웃(c12 동일집합) ≥60% CAGR>QQQ,
(c) 2×비용에서 (a) 유지, (d) 사전등록 1설정+이웃만·원장기록. 파산가드 전표본 MDD≤−95%→FAIL.

**신호 재사용(부록 v2.1 §4):** 이 신호들의 홀드아웃은 c12 에서 관측됨 → 새 idea_id, 홀드아웃
**반오염(holdout_semi_contaminated)** 표기, 유의성 임계 **RC/SPA p<0.01** 로 강화(둘 다). Lane A 판정은
그대로 적용하되 RC/SPA p 병기.

**체결:** 신호 close t → t+1 종가(primary, 레그 스위치). MOC(exec_lag=0)·다음시가 변형 병기. 바스켓 내부
월간 리밸런스는 다음 시가(c12s). **비용:** 10bp/side + 반호가(주식 바스켓 3bp / 1x ETF 1bp) + 슬리피지 5bp.
2× 스트레스 + ≤$10 micro(매수무료). **데이터 한계:** 바스켓 = PIT S&P500 as-of 멤버 캐시보유 ~27~31%
(대형·생존주 편중) → 모든 성과 **상한(UPPER BOUND)**.
"""


def write_report(results, hib, meta):
    L = [PREREG.rstrip(), "", "<!-- RESULTS_BELOW -->", ""]
    cov = hib["coverage"]
    L.append(f"> 실행 {meta['generated']} · 원장 {meta['ledger']} · rc_B={meta['rc_B']} · 설정 {len(results)}개")
    L.append("")
    L.append(f"**고베타 바스켓(HIBETA) 실현 β:** 설계 {_f(hib['beta']['design'])} · "
             f"홀드 {_f(hib['beta']['holdout'])} · 전구간 {_f(hib['beta']['full'])} "
             f"(vs TQQQ 3x). 회전 {_f(hib['turnover_yr'],1)}/년.")
    L.append(f"**PIT 커버리지(상한 캐비엇):** 설계 {cov.get('design_mean',float('nan')):.1%} · "
             f"홀드 {cov.get('holdout_mean',float('nan')):.1%} → 성과 UPPER BOUND.")
    L.append("")
    L.append("## 1. 헤드라인 (primary t+1 종가, 기본비용) — c13a hibeta-exec vs 대안")
    L.append("| 설정 | 신호 | 구간 | CAGR | MDD | Sharpe | β(홀드) | 설계(QQQ) | 홀드(QQQ) | 이웃 | 2×홀드 | "
             "micro | MOC | 다음시가 | RC p | SPA p | 판정 |")
    L.append("|---|---|---|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in results:
        s, b = r["splits"], r["bench"]
        d_cell = f"{_pc(s['design']['cagr'])} ({_pc(b['qqq']['design'])})"
        h_cell = f"{_pc(s['holdout']['cagr'])} ({_pc(b['qqq']['holdout'])})"
        L.append(
            f"| {r['cid'].replace('c13a_','')} | {r['base_id'].replace('c12_','')} | "
            f"{r['first'][:7]}…{r['last'][:7]} | {_pc(r['full']['cagr'])} | {_pc(r['full']['mdd'])} | "
            f"{_f(r['full']['sharpe'])} | {_f(r['realized_beta']['holdout'])} | {d_cell} | {h_cell} | "
            f"{r['neigh_frac']*100:.0f}%({r['n_neigh']}) | {_pc(r['cost2x']['holdout'])} | "
            f"{_pc(r['micro_full'])} | {_pc(r['moc_full'])} | {_pc(r['nextopen_full'])} | "
            f"{_f(r['rc_p'],3)} | {_f(r['spa_p'],3)} | **{r['verdict']}** |")
    L.append("")
    L.append("> RC/SPA 임계 = **p<0.01**(신호 재사용·반오염). Lane A 판정은 부록 v3 a·b·c(유의성은 병기).")
    L.append("")

    L.append("## 2. 비교 — c13a(hibeta) vs c12 TQQQ 실행 vs 1x 섀도 vs 벤치(같은 창)")
    L.append("| 설정 | c13a CAGR (설계/홀드) | c12 TQQQ CAGR (설계/홀드) | 1x섀도 CAGR | 고베타바스켓 B&H (설계/홀드) | QQQ B&H (설계/홀드) |")
    L.append("|---|---|---|---:|---|---|")
    for r in results:
        s, b, t, sh = r["splits"], r["bench"], r["c12_tqqq"], r["shadow_1x"]
        L.append(
            f"| {r['cid'].replace('c13a_','')} | {_pc(r['full']['cagr'])} "
            f"({_pc(s['design']['cagr'])}/{_pc(s['holdout']['cagr'])}) | "
            f"{_pc(t['full_cagr'])} ({_pc(t['design'])}/{_pc(t['holdout'])}) | {_pc(sh['full_cagr'])} | "
            f"{_pc(b['hibeta_bh']['full'])} ({_pc(b['hibeta_bh']['design'])}/{_pc(b['hibeta_bh']['holdout'])}) | "
            f"{_pc(b['qqq']['full'])} ({_pc(b['qqq']['design'])}/{_pc(b['qqq']['holdout'])}) |")
    L.append("")

    L.append("## 3. PSQ 인버스 변형 & Post-publication OOS")
    L.append("| 설정 | PSQ변형 CAGR (설계/홀드) | PSQ판정 | 공개일 | OOS n | OOS CAGR | OOS vs QQQ |")
    L.append("|---|---|---|---|---:|---:|---:|")
    for r in results:
        p, pub = r["psq_variant"], r["pub_oos"]
        if p:
            pcell = f"{_pc(p['full_cagr'])} ({_pc(p['splits']['design']['cagr'])}/{_pc(p['splits']['holdout']['cagr'])})"
            pv = p["verdict"]
        else:
            pcell, pv = "— (인버스 레그 없음)", "—"
        if pub:
            L.append(f"| {r['cid'].replace('c13a_','')} | {pcell} | {pv} | {pub['start']} | {pub['n']} | "
                     f"{_pc(pub['cagr'])} | {_pc(pub['qqq'])} |")
        else:
            L.append(f"| {r['cid'].replace('c13a_','')} | {pcell} | {pv} | — | — | — | — |")
    L.append("")

    L.append("## 4. 이웃 평탄성 상세 (FULL CAGR, QQQ FULL 대비)")
    for r in results:
        beats = [x for x in r["neighbors"] if x.get("beat_qqq")]
        L.append(f"- **{r['cid'].replace('c13a_','')}** ({r['neigh_frac']*100:.0f}% beat, "
                 f"{len(beats)}/{r['n_neigh']}): "
                 + ", ".join(f"{x['label']}={_pc(x.get('cagr'))}" for x in r["neighbors"] if "cagr" in x))
    L.append("")

    L.append("## 5. 판정 종합 및 정직한 해석")
    L.append(_verdict_prose(results, hib))
    if REPORT.exists():
        prev = REPORT.read_text(encoding="utf-8")
        if "<!-- RESULTS_BELOW -->" in prev:
            L[0] = prev.split("<!-- RESULTS_BELOW -->")[0].rstrip()
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def _verdict_prose(results, hib):
    P = [r["cid"] for r in results if r["verdict"] == "PASS"]
    C = [r["cid"] for r in results if r["verdict"].startswith("CONDITIONAL")]
    F = [r["cid"] for r in results if r["verdict"].startswith("FAIL")]
    lines = [f"**판정 요약.** PASS: {P or '없음'} · CONDITIONAL: {C or '없음'} · FAIL: {F or '없음'}. "
             "(판정 = 부록 v3 Lane A a·b·c + 파산가드.)", ""]
    lines.append(
        "**정직한 해석.** (1) 3x TQQQ 를 실현 β≈1.6 바스켓으로 바꾸면 위험선호 레그 노출이 ~1.6/3.0 로 "
        "낮아져 CAGR 이 c12 TQQQ 판보다 크게 하락한다 — c13a 는 '자유 거래 가능(규제 무관)'의 대가로 낮은 "
        "레버리지를 받아들이는 트레이드오프다. (2) 그럼에도 신호(현금/방어 회피)가 살아 있으면 고베타 바스켓 "
        "B&H(상시투자)보다 홀드아웃 MDD 가 얕아질 수 있다. (3) **홀드아웃은 반오염**(신호가 c12 에서 이미 "
        "관측) — Lane A 통과라도 RC/SPA p<0.01 을 병기해 다중검정·재사용 페널티를 정직하게 노출한다. "
        "(4) 성과는 PIT 커버리지 ~30% 상한(대형·생존주 편중) → **UPPER BOUND**. (5) 실채택은 부록 v3-4 "
        "포워드 페이퍼(공격형 랩 3개월+ 실시세) 후 사용자 승인 시에만. (6) PSQ 변형은 '허용처럼 보이는' "
        "−1x 인버스지만 규제 게이트 가능성 있음 → 규제 확인 전 현금(1차)이 기본.")
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    import datetime as _dt
    ap = argparse.ArgumentParser(description="c13a 승리신호 × 고베타 바스켓(Lane A, 신호재사용)")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--only", default=None, help="쉼표구분 cid 필터")
    ap.add_argument("--quick", action="store_true", help="RC 부트스트랩 축소(빠른 점검)")
    args = ap.parse_args()

    do_log = not args.no_ledger
    rc_B = 400 if args.quick else 1500
    only = set(args.only.split(",")) if args.only else None

    print("[c13a] 고베타 바스켓 합성 중 …", flush=True)
    hib = build_hibeta()
    print(f"[c13a] HIBETA β 홀드 {hib['beta']['holdout']:.2f} · 커버리지 홀드 "
          f"{hib['coverage'].get('holdout_mean', float('nan')):.1%}", flush=True)

    specs = [c for c in build_specs() if (only is None or c.cid in only)]
    results = []
    for cfg in specs:
        print(f"[c13a] {cfg.cid} …", flush=True)
        r = evaluate(cfg, hib, do_log=do_log, rc_B=rc_B)
        results.append(r)
        print(f"      CAGR={_pc(r['full']['cagr'])} MDD={_pc(r['full']['mdd'])} β홀드={_f(r['realized_beta']['holdout'])} "
              f"설계={_pc(r['splits']['design']['cagr'])} 홀드={_pc(r['splits']['holdout']['cagr'])} "
              f"이웃={r['neigh_frac']*100:.0f}% RCp={_f(r['rc_p'],3)} SPAp={_f(r['spa_p'],3)} → {r['verdict']}",
              flush=True)

    meta = {"generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "ledger": args.ledger if do_log else "(no-ledger)", "rc_B": rc_B,
            "hibeta_beta": hib["beta"], "coverage": hib["coverage"],
            "basket_params": BASKET_PARAMS,
            "cost": "10bp/side + 3bp(주식바스켓)/1bp(1x ETF) half-spread + 5bp slip; leg t+1 close; "
                    "basket monthly next-open; 2x stress; micro=free",
            "signal_reuse": True, "semi_contaminated": True, "sig_threshold": 0.01}
    RESULTS_JSON.write_text(json.dumps({"meta": meta, "results": results}, default=str,
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_report:
        write_report(results, hib, meta)
        print(f"[c13a] report → {REPORT}", flush=True)
    print(f"[c13a] results → {RESULTS_JSON}", flush=True)


if __name__ == "__main__":
    main()
