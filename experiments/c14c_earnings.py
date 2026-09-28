#!/usr/bin/env python3
"""c14c — 실적발표 프리미엄 / 프리-어닝 런업(Lane A, 사이클14 `c14c`).

가설(문헌): 예정된 실적발표 직전~당일에 개별주는 초과수익을 낸다.
  - Frazzini & Lamont (2007) "The earnings announcement premium and trading volume"
  - Barber, De George, Lehavy & Trueman (2013) "The earnings announcement premium around the globe"
공격형 롱온리(Lane A) — 발표일 전 N거래일에 매수해 발표(런업+발표점프)를 수확한다.

이 스크립트는 **src/ 를 수정하지 않고 소비만** 한다. c3c_pit 의 PIT(as-of) S&P500 멤버십 기계와
c2d_stocks 의 패널·비용 티어, c12_hibeta 의 베타·Lane A 판정을 재사용하고, **실적일 데이터 소스
(SEC EDGAR 8-K Item 2.02)** 와 이벤트 스케줄러·종가-종가 이벤트 포트폴리오만 새로 얹는다.

── 실적일 데이터 소스(키 불필요, 검증) ──────────────────────────────────────
  SEC EDGAR submissions JSON: https://data.sec.gov/submissions/CIK##########.json
  (User-Agent "toss-trader research contact@example.com" 필수, ≤10 req/s). form=8-K 이고
  items 에 **2.02**(Results of Operations and Financial Condition) 를 포함한 제출의 filingDate 를
  실적발표일로 쓴다. acceptanceDateTime(ET) 시각이 16:00 이후면 장마감 후 발표 → **효과 거래일 = 익일**.
  ticker→CIK 는 https://www.sec.gov/files/company_tickers.json (키 불필요, 1회). 캐시 data/earnings/.
  검증: NVDA(Feb/May/Aug/Nov)·AAPL(Jan/Apr/Jul/Oct) 분기 케이던스 일치, 발표 후 익일 효과 확인.

  ⚠ **look-ahead 경고**: 8-K 는 **제출된 날에야** 그 날짜를 알 수 있다(사후). 발표일을 5일 전에
  미리 아는 forward 캘린더가 아니다. 따라서 '실제 발표일(actual)' 스케줄로 E−5 에 진입하는 것은
  **미래 정보 사용(look-ahead)** 이다 — 정직하게 표기하고, 이를 정량화하기 위해 '**예상 발표일
  (expected)**' 대안을 함께 돌린다: 예상일 = **직전 분기 실제 발표일 + ~91일**(직전 분기 8-K 가
  이미 제출돼 있으므로 진입 시점에 인과적으로 알 수 있음). tests/test_c14c.py 가 이 두 성질을 강제한다.

사전등록(실행 전 고정, 튜닝 금지 — 사전등록 1개 config + 평탄성 이웃 ±20~50%):
  1) c14c_pre_earn        : 발표 효과 거래일 E 의 **E−5 종가 매수 → E 종가 매도**(런업+발표 수확).
                            유니버스 = 캐시 보유 PIT S&P500 as-of 멤버. 동시 최대 10포지션,
                            현재 포지션 동일가중(유휴현금 0%). actual 스케줄.
  2) c14c_pre_earn_hibeta : (1) 을 진입일 252d QQQ 베타 상위 50 이름으로 제한(공격형).
  3) c14c_pre_earn_exit_before : E−5 매수 → **E−1 종가 매도**(발표 점프 리스크 회피).
  4) c14c_pre_earn_expected(창의) : **look-ahead 없는 실전형** — 예상 발표일(직전+91일) 기준
                            E'−5 매수 → E' 종가 매도. actual 대비 성과 갭 = 발표일 예측 불확실성의 비용.

비용(task 지정): 0.1%/side(=10bp) + 반호가 **3bp**(대형 유동주) + 슬리피지 5bp → 편도 18bp.
  2× 스트레스, 마이크로 레짐(≤$10 매수 무료 = 수수료 0). 현금은 DTB3.
  분할: 설계 2016-09~2021-12 / 홀드아웃 2022~2026-09. QQQ B&H 벤치.

데이터 한계(c3c 계승): as-of S&P500 멤버 중 캐시 보유 ~30%(대형·생존주 편중) → 성과는 상한(UPPER BOUND).
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))

from toss_trader import gate, histdata, research  # noqa: E402
from toss_trader.research import CostSpec  # noqa: E402
import gate_eval  # noqa: E402
import c2d_stocks as c2d  # noqa: E402
import c3c_pit as c3c  # noqa: E402
import c12_hibeta as c12  # noqa: E402  (beta_to_bench, lane_a_verdict, realized_beta, _cagr, _split_idx)

LEDGER = str(ROOT / "reports" / "trials_ledger.jsonl")
LANE = "A"                                  # 원장 레인(부록 v3 Lane A)
DESIGN_END = date(2021, 12, 31)
START = date(2016, 9, 1)
END = date(2026, 9, 30)
BENCH = "QQQ"

EARN_DIR = ROOT / "data" / "earnings"
EDGAR_UA = "toss-trader research contact@example.com"
EDGAR_HDR = {"User-Agent": EDGAR_UA, "Accept": "application/json"}

LEAD = 5                # 진입 = 효과일 E − LEAD 거래일
MAX_POS = 10            # 동시 최대 포지션(EW, 유휴현금 0%)
MIN_PRICE = 5.0         # 최소 원시 종가($)
BETA_TOP = 50           # 고베타 제한 상위 N
BETA_WIN = c12.BETA_WIN # 252
DV_TOP = c12.DV_TOP     # 63d 달러거래대금 상위 N (PIT 후보 제한)
BANKRUPTCY_MDD = -0.95  # 전표본 MDD 하드캡(파산 가드, 부록 v3-1)
MIN_QUARTER_GAP_DAYS = 45   # 같은 분기 중복 2.02 제거(가드 등 비-분기 2.02 필터)
EXPECTED_GAP_DAYS = 91      # 예상 발표일 = 직전 실제일 + 91일(≈1분기)


# ═════════════════════════════════════════════════════════════════════════════
# 1. 실적일 데이터: SEC EDGAR 8-K Item 2.02 (키 불필요, 검증)
# ═════════════════════════════════════════════════════════════════════════════
def _edgar_get(url: str) -> dict:
    return json.loads(histdata._http_get(url, timeout=30, headers=dict(EDGAR_HDR)))


def load_ticker_cik_map(*, force: bool = False) -> dict[str, int]:
    """ticker→CIK 맵(SEC company_tickers.json). 캐시 data/earnings/_ticker_cik.json."""
    p = EARN_DIR / "_ticker_cik.json"
    if p.exists() and not force:
        return {k: int(v) for k, v in json.loads(p.read_text()).items()}
    tk = _edgar_get("https://www.sec.gov/files/company_tickers.json")
    m = {v["ticker"]: int(v["cik_str"]) for v in tk.values()}
    EARN_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m))
    return m


def _cik_for(m: dict[str, int], sym: str) -> int | None:
    for cand in (sym, sym.replace(".", "-"), sym.replace(".", ""), sym.split(".")[0]):
        if cand in m:
            return m[cand]
    return None


def _fetch_earnings_symbol(cik: int, *, spacing: float = 0.3) -> list[tuple[str, str]]:
    """CIK 의 8-K Item 2.02 제출을 (filingDate, acceptanceDateTime) 로 (recent + 과거 샤드까지).

    429 는 즉시 올려(HTTPError) 호출측이 폴링을 멈추게 한다(사양: 우회 금지).
    """
    rows: list[tuple[str, str]] = []

    def collect(rec: dict) -> None:
        F = rec.get("form", []); D = rec.get("filingDate", [])
        I = rec.get("items", []); A = rec.get("acceptanceDateTime", [])
        for i in range(len(F)):
            if F[i] == "8-K" and "2.02" in (I[i] if i < len(I) else ""):
                rows.append((D[i], A[i] if i < len(A) else ""))

    sub = _edgar_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    collect(sub["filings"]["recent"])
    for f in sub["filings"].get("files", []):
        time.sleep(spacing)
        collect(_edgar_get(f"https://data.sec.gov/submissions/{f['name']}"))
    return sorted(set(rows))


def build_earnings_cache(symbols: list[str], *, force: bool = False,
                         spacing: float = 0.35) -> dict:
    """유니버스 심볼의 실적일을 data/earnings/{SYM}.json 에 확보. 반환: 요약 통계."""
    m = load_ticker_cik_map(force=force)
    EARN_DIR.mkdir(parents=True, exist_ok=True)
    fetched = cached = nocik = 0
    for sym in symbols:
        p = EARN_DIR / f"{sym.replace('.', '_')}.json"
        if p.exists() and not force:
            cached += 1
            continue
        cik = _cik_for(m, sym)
        if cik is None:
            nocik += 1
            continue
        try:
            rows = _fetch_earnings_symbol(cik)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"  EDGAR 429 at {sym} → 중단(우회 금지)", flush=True)
                break
            raise
        p.write_text(json.dumps({"symbol": sym, "cik": cik,
                                 "source": "edgar_8k_item202",
                                 "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "events": rows}))
        fetched += 1
        time.sleep(spacing)
    return {"fetched": fetched, "cached": cached, "no_cik": nocik, "n_symbols": len(symbols)}


def _acc_after_close(acc: str) -> bool:
    """acceptanceDateTime(ET 벽시계 관례) 시각이 16:00 이후면 장마감 후 발표 → 익일 효과.

    시각 파싱 불가 시 보수적으로 True(대다수 실적 8-K 는 장마감 후 제출).
    """
    try:
        t = datetime.fromisoformat(acc.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return True
    return t.hour >= 16


def load_symbol_events(sym: str) -> list[tuple[date, bool]]:
    """캐시된 실적 이벤트 → [(filingDate, after_close)] (오름차순, 창 밖은 제외).

    같은 분기 중복(비-분기 2.02) 제거: 직전 유지 이벤트와 MIN_QUARTER_GAP_DAYS 이내면 버린다.
    """
    p = EARN_DIR / f"{sym.replace('.', '_')}.json"
    if not p.exists():
        return []
    raw = json.loads(p.read_text()).get("events", [])
    parsed: list[tuple[date, bool]] = []
    for d_str, acc in raw:
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        parsed.append((d, _acc_after_close(acc)))
    parsed.sort()
    kept: list[tuple[date, bool]] = []
    for d, ac in parsed:
        if kept and (d - kept[-1][0]).days < MIN_QUARTER_GAP_DAYS:
            continue
        kept.append((d, ac))
    return kept


# ═════════════════════════════════════════════════════════════════════════════
# 2. 이벤트 스케줄러: 발표일 → 효과 거래일 E → (진입, 청산) 인덱스
# ═════════════════════════════════════════════════════════════════════════════
def _first_trading_idx(dates: list[date], D: date) -> int | None:
    i = bisect.bisect_left(dates, D)
    return i if i < len(dates) else None


def _effective_idx(dates: list[date], D: date, after_close: bool) -> int | None:
    """발표일 D → 효과 거래일 인덱스 E. after_close 이고 D 가 거래일이면 익일로 밀어 익일 종가 반영."""
    i = _first_trading_idx(dates, D)
    if i is None:
        return None
    if after_close and dates[i] == D:
        i += 1
    return i if i < len(dates) else None


def _hibeta_top_set(panel: c2d.Panel, t: int, *, beta_top: int = BETA_TOP) -> set[str]:
    """t 시점 live PIT 후보 중 252d QQQ 베타 상위 beta_top 집합(진입일별 캐시)."""
    cache = getattr(panel, "_hibeta_cache", None)
    if cache is None:
        cache = {}
        panel._hibeta_cache = cache             # type: ignore[attr-defined]
    if t in cache:
        return cache[t]
    scored: list[tuple[float, str]] = []
    for s in c3c.pit_candidates(panel, t, need=BETA_WIN, dv_top=DV_TOP):
        if panel.rawclose[s][t] < MIN_PRICE:    # type: ignore[attr-defined]
            continue
        b = c12.beta_to_bench(panel, s, t, BETA_WIN)
        if b is not None:
            scored.append((b, s))
    scored.sort(reverse=True)
    top = {s for _, s in scored[:beta_top]}
    cache[t] = top
    return top


def build_events(panel: c2d.Panel, *, lead: int = LEAD, exit_offset: int = 0,
                 hibeta: bool = False, mode: str = "actual",
                 events_provider=None) -> list[dict]:
    """유니버스 전체의 (진입 idx, 청산 idx, 심볼, 효과 idx, known idx) 이벤트 리스트.

    mode="actual"   : 효과일 E = 실제 8-K 발표일의 효과 거래일. known_idx = E 를 아는 시점
                      = 발표일의 거래 인덱스(≈E−1 장마감후/E 장중). E−lead 진입 → look-ahead(테스트가 강제).
    mode="expected" : 효과일 E' = (직전 분기 실제 발표일 + 91일)의 거래 거래일. known_idx = 직전 분기
                      발표일의 거래 인덱스(진입보다 ~1분기 앞) → **look-ahead 없음**. 1분기차 이벤트는 생략.
    진입은 PIT as-of 멤버·live·가격≥$5(·hibeta 상위50) 필터를 통과해야 편입. 종가-종가 보유.
    events_provider(sym)→[(date, after_close)] 를 주면 캐시 대신 사용(테스트 주입용).
    """
    provider = events_provider or load_symbol_events
    dates = panel.dates
    n = len(dates)
    min_hist = BETA_WIN if hibeta else 1
    events: list[dict] = []
    for s in panel.universe:
        evs = provider(s)
        if not evs:
            continue
        # 각 발표의 (효과 거래일 E, 실제 발표일 거래 인덱스, 원 발표일). E 는 발표일 단조 → 정렬 순서 보존.
        eff: list[tuple[int, int, date]] = []
        for d, ac in evs:
            E = _effective_idx(dates, d, ac)
            fidx = _first_trading_idx(dates, d)
            if E is not None and fidx is not None:
                eff.append((E, fidx, d))
        eff.sort()
        for k, (E, fidx, d) in enumerate(eff):
            if mode == "expected":
                if k == 0:
                    continue                      # 직전 분기 없음
                prev_d = eff[k - 1][2]            # 직전 분기 실제 발표일
                Eexp = _effective_idx(dates, prev_d + timedelta(days=EXPECTED_GAP_DAYS), False)
                if Eexp is None:
                    continue
                E_use = Eexp
                known = eff[k - 1][1]              # 직전 분기 발표 인덱스(진입 전 이미 알던 정보)
            else:
                E_use = E
                known = fidx                      # 실제 발표일 인덱스(사후에야 알 수 있음)
            entry = E_use - lead
            xit = E_use - exit_offset
            if entry < 0 or xit >= n or xit <= entry:
                continue
            if entry - panel.first_idx.get(s, 10 ** 9) < min_hist:
                continue
            if not c3c._pit_live(panel, s, entry, min_hist):
                continue
            if s not in panel.pit_elig[entry]:    # type: ignore[attr-defined]  as-of 멤버
                continue
            if panel.rawclose[s][entry] < MIN_PRICE:   # type: ignore[attr-defined]
                continue
            if panel.close[s][entry] <= 0 or panel.close[s][xit] <= 0:
                continue
            if hibeta and s not in _hibeta_top_set(panel, entry):
                continue
            events.append({"entry": entry, "exit": xit, "sym": s,
                           "eff": E_use, "known": known})
    events.sort(key=lambda e: (e["entry"], e["sym"]))
    return events


# ═════════════════════════════════════════════════════════════════════════════
# 3. 종가-종가 이벤트 포트폴리오(동시 최대 max_pos, EW 활성, 유휴현금 0%)
# ═════════════════════════════════════════════════════════════════════════════
def event_weight_schedule(events: list[dict], n: int, *, max_pos: int = MAX_POS
                          ) -> tuple[dict[int, dict[str, float]], list[tuple[int, int, str]]]:
    """이벤트를 슬롯(동시 max_pos)에 배정 → (활성집합 변경일의 목표 EW 비중 dict, 실제 체결 (진입,청산,심볼)).

    가격 무관(이벤트 인덱스만 사용) → 인과적. 활성집합이 바뀌는 날에만 목표비중을 갱신(전방채움).
    """
    entries_by_day: dict[int, list[tuple[str, int]]] = {}
    for e in events:
        entries_by_day.setdefault(e["entry"], []).append((e["sym"], e["exit"]))
    active: dict[str, int] = {}                  # sym -> 청산 idx
    decisions: dict[int, dict[str, float]] = {}
    taken: list[tuple[int, int, str]] = []
    entry_of: dict[str, int] = {}
    for t in range(n):
        changed = False
        for s in [s for s, xi in active.items() if xi == t]:   # 청산(종가 t)
            del active[s]
            changed = True
        for sym, xi in entries_by_day.get(t, []):              # 신규 진입(종가 t), 슬롯 여유 시
            if sym in active:
                continue
            if len(active) < max_pos:
                active[sym] = xi
                entry_of[sym] = t
                taken.append((t, xi, sym))
                changed = True
        if changed:
            decisions[t] = c2d._equal_weight(list(active.keys()))
    return decisions, taken


def make_signal_fn(panel: c2d.Panel, *, lead: int = LEAD, exit_offset: int = 0,
                   hibeta: bool = False, mode: str = "actual", max_pos: int = MAX_POS,
                   events_provider=None):
    """look-ahead 가드용: (perturbed closes, dates) → 일별 목표비중. 종가 교체 사본에서 재계산.

    이벤트 인덱스는 발표일(외생)에서 오지만 hibeta 선별·live·베타는 종가≤진입 만 참조(인과적).
    미래 종가를 교란해도 진입≤t 의 목표비중은 불변이어야 한다(research.lookahead_guard 로 강제).
    """
    def fn(panel_closes, dates):
        clone = c12.clone_with_closes(panel, dict(panel_closes))
        clone._hibeta_cache = {}                 # type: ignore[attr-defined]
        ev = build_events(clone, lead=lead, exit_offset=exit_offset, hibeta=hibeta,
                          mode=mode, events_provider=events_provider)
        dec, _ = event_weight_schedule(ev, len(dates), max_pos=max_pos)
        return c2d.decisions_to_daily(dec, len(dates))
    return fn


def simulate_c2c(panel: c2d.Panel, decisions: dict[int, dict[str, float]], cost: CostSpec,
                 *, cash_rate: list[float] | None = None) -> c2d.SimResult:
    """종가-종가 EW 이벤트 포트폴리오 시뮬. 목표비중(종가 t) 을 **그 종가에 즉시** 체결(런업 매수/발표 매도).

    포지션은 종가수익으로 MTM, 활성 없으면 현금(cash_rate). 비용 = 매매노셔널×trade_bps(심볼).
    유휴현금 0%: 활성 k개면 각 1/k(전액 투자). run_weights/simulate_portfolio 와 달리 **종가 체결**이라
    task 사양('E−5 종가 매수 → E 종가 매도')에 정확히 부합.
    """
    syms = list(panel.close)
    n = len(panel.dates)
    holdings: dict[str, float] = {}
    cash = 1.0
    equity = [0.0] * n
    returns = [0.0] * n
    turnover = [0.0] * n
    total_cost = 0.0
    prev_eq = 1.0
    for t in range(n):
        if t > 0:                                # MTM(종가수익) + 현금이자
            for s in list(holdings):
                pc = panel.close[s][t - 1]
                if pc > 0:
                    holdings[s] *= panel.close[s][t] / pc
            if cash_rate is not None:
                cash *= (1.0 + cash_rate[t])
        if t in decisions:                       # 종가 t 목표비중으로 즉시 리밸런싱(종가 체결)
            tw = decisions[t]
            eq = cash + sum(holdings.values())
            traded = 0.0
            c_paid = 0.0
            new_hold: dict[str, float] = {}
            for s in set(list(holdings) + list(tw)):
                cur = holdings.get(s, 0.0)
                tv = tw.get(s, 0.0) * eq
                dv = tv - cur
                if abs(dv) > 1e-12:
                    traded += abs(dv)
                    c_paid += abs(dv) * cost.trade_bps(s) * research.BPS
                if tv > 1e-15:
                    new_hold[s] = tv
            holdings = new_hold
            cash = eq - sum(holdings.values()) - c_paid
            total_cost += c_paid
            turnover[t] = traded / eq if eq > 0 else 0.0
        eq_close = cash + sum(holdings.values())
        equity[t] = eq_close
        returns[t] = (eq_close / prev_eq - 1.0) if prev_eq > 0 else 0.0
        prev_eq = eq_close
    return c2d.SimResult(dates=panel.dates, equity=equity, returns=returns,
                         turnover=turnover, total_cost=total_cost, n_rebalances=len(decisions))


def trades_from_events(panel: c2d.Panel, taken: list[tuple[int, int, str]]) -> list[research.Trade]:
    """체결 이벤트 → 거래단위 통계용 Trade(종가-종가, notional=1). 거래별 t/CI 는 이 pnls 로."""
    out: list[research.Trade] = []
    for ei, xi, s in taken:
        ep = panel.close[s][ei]
        xp = panel.close[s][xi]
        if ep > 0 and xp > 0:
            out.append(research.Trade(entry_dt=panel.dates[ei], entry_price=ep,
                                      exit_dt=panel.dates[xi], exit_price=xp,
                                      notional=1.0, symbol=s))
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 4. 비용·평가(Lane A)
# ═════════════════════════════════════════════════════════════════════════════
def cost_for(panel: c2d.Panel, *, mult: float = 1.0, micro: bool = False,
             commission_bps: float = 10.0) -> CostSpec:
    """task 비용: 0.1%/side(10bp) + 3bp 반호가(대형 유동주) + 5bp 슬리피지. micro=수수료 0. mult=스트레스."""
    comm = 0.0 if micro else commission_bps
    c = CostSpec.from_tiers(panel.tier, commission_bps=comm, slippage_bps=5.0, fx_bps=20.0)
    return c.stress(mult) if mult != 1.0 else c


def set_uniform_large_cap_tier(panel: c2d.Panel) -> None:
    """task 지정 '3bp 반호가' → 전 유니버스를 large_cap(3bp), QQQ/XL* 는 ETF(1bp)."""
    for s in list(panel.close):
        panel.tier[s] = research.TIER_ETF if (s == BENCH or s.startswith("XL")) \
            else research.TIER_LARGE_CAP


def _trade_stats(pnls: list[float], *, rc_B: int = 1500) -> dict:
    if len(pnls) < 2:
        return {"n": len(pnls)}
    _, lo, hi = gate.trade_pnl_bootstrap_ci(pnls, B=rc_B)
    return {"n": len(pnls), "tstat": gate.trade_tstat(pnls), "ci_lo": lo, "ci_hi": hi,
            "profit_factor": gate.profit_factor(pnls), "expectancy_bps": gate.expectancy(pnls) * 1e4,
            "mean_ret_bps": gate._mean(pnls) * 1e4,
            "win_rate": sum(1 for p in pnls if p > 0) / len(pnls)}


def _neighbors(base: dict) -> list[dict]:
    """평탄성/Lane A(b)/N 이웃(±20~50%): lead {−2..+2}, max_pos {5,8,13,15}. 그리드서치 아님."""
    out: list[dict] = []
    for ld in (base["lead"] - 2, base["lead"] - 1, base["lead"] + 1, base["lead"] + 2):
        if ld >= 2 and ld != base["lead"]:
            p = dict(base); p["lead"] = ld; out.append(p)
    for mp in (5, 8, 13, 15):
        if mp != base["max_pos"]:
            p = dict(base); p["max_pos"] = mp; out.append(p)
    return out


def evaluate_idea(panel: c2d.Panel, idea_id: str, base: dict, cash_rate,
                  *, qqq_des: float, qqq_hol: float, qqq_full: float,
                  bench_ret: list[float], log: bool = True, rc_B: int = 1500) -> dict:
    """사전등록 config + 이웃 평가. 포트폴리오 CAGR/MDD(Lane A) + 거래단위 t/CI 를 함께 낸다."""
    dates = panel.dates
    n = len(dates)
    ret_dates = dates[1:]
    di, hi = c12._split_idx(ret_dates)
    bench_stream = bench_ret[1:]
    cost = cost_for(panel)

    def run(params: dict, c: CostSpec):
        ev = build_events(panel, lead=params["lead"], exit_offset=params["exit_offset"],
                          hibeta=params["hibeta"], mode=params["mode"])
        dec, taken = event_weight_schedule(ev, n, max_pos=params["max_pos"])
        sim = simulate_c2c(panel, dec, c, cash_rate=cash_rate)
        return sim, sim.returns[1:], taken

    sim_c, cand, taken = run(base, cost)
    _, cand2, _ = run(base, cost_for(panel, mult=2.0))
    _, micro, _ = run(base, cost_for(panel, micro=True))

    d_des = [ret_dates[i] for i in di]; d_hol = [ret_dates[i] for i in hi]
    r_des = [cand[i] for i in di]; r_hol = [cand[i] for i in hi]
    b_des = [bench_stream[i] for i in di]; b_hol = [bench_stream[i] for i in hi]
    cagr_des = c12._cagr(r_des, d_des); cagr_hol = c12._cagr(r_hol, d_hol)
    cagr_full = c12._cagr(cand, ret_dates)
    mdd_full = gate.max_drawdown(gate.returns_to_equity(cand))
    mdd_des = gate.max_drawdown(gate.returns_to_equity(r_des))
    mdd_hol = gate.max_drawdown(gate.returns_to_equity(r_hol))

    # 거래단위 통계(진입일로 분할). costs: 1× / 2×.
    tr1 = research.run_trades(trades_from_events(panel, taken), cost=cost, capital=1.0)
    tr2 = research.run_trades(trades_from_events(panel, taken), cost=cost_for(panel, mult=2.0), capital=1.0)
    # 진입 인덱스(dates 기준)로 설계/홀드 분할
    des_p = [p for p, tr in zip(tr1.pnls, taken) if dates[tr[0]] <= DESIGN_END]
    hol_p = [p for p, tr in zip(tr1.pnls, taken) if dates[tr[0]] > DESIGN_END]
    hol_p2 = [p for p, tr in zip(tr2.pnls, taken) if dates[tr[0]] > DESIGN_END]

    # 이웃(평탄성 + Lane A (b) + DSR N)
    sr_trials: list[float] = []
    neigh_full: dict[str, list[float]] = {}
    frac_full = 0
    neighbors = _neighbors(base)
    for j, p in enumerate(neighbors):
        _, nret, _ = run(p, cost)
        neigh_full[f"n{j}"] = nret
        sr_trials.append(c2d._sr_daily([nret[i] for i in di]))
        if c12._cagr(nret, ret_dates) > qqq_full:
            frac_full += 1
    sr_trials.append(c2d._sr_daily(r_des))
    frac_full_r = frac_full / len(neighbors) if neighbors else 0.0
    n_eff = gate.n_eff_clusters({**{k: [v[i] for i in di] for k, v in neigh_full.items()},
                                 "center": r_des})

    # 2× 비용 스트레스: 설계·홀드 모두 CAGR>QQQ 유지?
    cagr_des_2x = c12._cagr([cand2[i] for i in di], d_des)
    cagr_hol_2x = c12._cagr([cand2[i] for i in hi], d_hol)

    um_hol = gate_eval.unit_capital_metrics(r_hol, dates=d_hol, sr_trials=sr_trials, n_eff=n_eff)
    fam_hol = {k: [v[i] - bench_stream[i] for i in hi] for k, v in neigh_full.items()}
    rc_hol = gate_eval.reality_check([r_hol[k] - b_hol[k] for k in range(len(r_hol))],
                                     family_excess=fam_hol, B=rc_B)

    a_pass = (cagr_des > qqq_des) and (cagr_hol > qqq_hol)
    b_pass = frac_full_r >= 0.60
    c_pass = (cagr_des_2x > qqq_des) and (cagr_hol_2x > qqq_hol)
    bankruptcy = mdd_full <= BANKRUPTCY_MDD
    verdict, reasons = c12.lane_a_verdict(a_pass=a_pass, b_pass=b_pass, c_pass=c_pass,
                                          bankruptcy=bankruptcy)
    if base["mode"] == "actual":
        reasons.append("⚠ actual 스케줄 = 발표일 완전예지(look-ahead 상한). 실전 판정은 expected 변형 참조.")

    if log:
        cfg = dict(base); cfg["idea"] = idea_id
        um_des = gate_eval.unit_capital_metrics(r_des, dates=d_des, sr_trials=sr_trials, n_eff=n_eff)
        gate_eval.log_evaluation(idea_id, cfg, LANE, "design", um_des,
                                 gate_eval.money_weighted_metrics(benchmark_terminal=c12._terminal(b_des),
                                                                  dca_equity=gate.returns_to_equity(r_des)),
                                 window=(d_des[0], d_des[-1]) if d_des else None,
                                 universe=panel.universe, ledger_path=LEDGER)
        for j, p in enumerate(neighbors):
            npar = dict(p); npar["idea"] = idea_id; npar["neighbor"] = j
            um_n = gate_eval.unit_capital_metrics([neigh_full[f"n{j}"][i] for i in di], dates=d_des)
            gate_eval.log_evaluation(idea_id, npar, LANE, "design", um_n, {},
                                     window=(d_des[0], d_des[-1]) if d_des else None,
                                     ledger_path=LEDGER)
        if hi:
            try:
                gate_eval.log_evaluation(idea_id, cfg, LANE, "holdout", um_hol,
                                         gate_eval.money_weighted_metrics(
                                             benchmark_terminal=c12._terminal(b_hol),
                                             dca_equity=gate.returns_to_equity(r_hol)),
                                         window=(d_hol[0], d_hol[-1]), universe=panel.universe,
                                         ledger_path=LEDGER)
            except gate.PeekOnceError:
                pass

    return {
        "idea": idea_id, "params": base, "kind": "event_portfolio",
        "n_trades": len(taken),
        "design": {"cagr": cagr_des, "mdd": mdd_des, "sharpe": gate.sharpe(r_des),
                   "cagr_vs_qqq": cagr_des - qqq_des, "trades": _trade_stats(des_p, rc_B=rc_B)},
        "holdout": {"cagr": cagr_hol, "mdd": mdd_hol, "sharpe": gate.sharpe(r_hol),
                    "cagr_vs_qqq": cagr_hol - qqq_hol, "cagr_2x": cagr_hol_2x,
                    "cagr_micro": c12._cagr([micro[i] for i in hi], d_hol),
                    "dsr": um_hol.get("dsr"), "rc_p": rc_hol["rc_pvalue"], "spa_p": rc_hol["spa_pvalue"],
                    "trades": _trade_stats(hol_p, rc_B=rc_B), "trades_2xcost": _trade_stats(hol_p2, rc_B=rc_B)},
        "full": {"cagr": cagr_full, "mdd": mdd_full},
        "turnover_yr": sim_c.turnover_per_year(),
        "neighbor_frac_full": frac_full_r, "n_neighbors": len(neighbors), "n_eff": n_eff,
        "lane_a": {"a_pass": a_pass, "b_pass": b_pass, "c_pass": c_pass, "bankruptcy": bankruptcy},
        "verdict": verdict, "reasons": reasons,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 5. look-ahead 갭 정량화(actual vs expected) — 리포트/테스트 공용
# ═════════════════════════════════════════════════════════════════════════════
def lookahead_gap(panel: c2d.Panel, cash_rate, *, lead: int = LEAD) -> dict:
    """동일 규칙(pre_earn)을 actual/expected 스케줄로 돌려 거래단위·홀드아웃 CAGR 갭을 잰다."""
    n = len(panel.dates)
    cost = cost_for(panel)
    ret_dates = panel.dates[1:]
    _, hi = c12._split_idx(ret_dates)
    d_hol = [ret_dates[i] for i in hi]
    out = {}
    for mode in ("actual", "expected"):
        ev = build_events(panel, lead=lead, exit_offset=0, hibeta=False, mode=mode)
        dec, taken = event_weight_schedule(ev, n, max_pos=MAX_POS)
        sim = simulate_c2c(panel, dec, cost, cash_rate=cash_rate)
        cand = sim.returns[1:]
        tr = research.run_trades(trades_from_events(panel, taken), cost=cost, capital=1.0)
        out[mode] = {"n_trades": len(taken),
                     "mean_ret_bps": gate._mean(tr.pnls) * 1e4 if tr.pnls else 0.0,
                     "tstat": gate.trade_tstat(tr.pnls),
                     "holdout_cagr": c12._cagr([cand[i] for i in hi], d_hol)}
    out["gap_mean_bps"] = out["actual"]["mean_ret_bps"] - out["expected"]["mean_ret_bps"]
    out["gap_holdout_cagr"] = out["actual"]["holdout_cagr"] - out["expected"]["holdout_cagr"]
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 6. 패널 구성 + main
# ═════════════════════════════════════════════════════════════════════════════
def build_panel() -> tuple[c2d.Panel, list]:
    recs = c3c.load_pit_records()
    panel = c3c.build_pit_panel(recs)
    set_uniform_large_cap_tier(panel)
    panel._beta_cache = {}                       # type: ignore[attr-defined]
    panel._hibeta_cache = {}                     # type: ignore[attr-defined]
    return panel, recs


IDEAS = [
    ("c14c_pre_earn", {"lead": 5, "exit_offset": 0, "hibeta": False, "max_pos": 10, "mode": "actual"}),
    ("c14c_pre_earn_hibeta", {"lead": 5, "exit_offset": 0, "hibeta": True, "max_pos": 10, "mode": "actual"}),
    ("c14c_pre_earn_exit_before", {"lead": 5, "exit_offset": 1, "hibeta": False, "max_pos": 10, "mode": "actual"}),
    ("c14c_pre_earn_expected", {"lead": 5, "exit_offset": 0, "hibeta": False, "max_pos": 10, "mode": "expected"}),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="c14c 실적발표 프리미엄/프리-어닝 런업(Lane A)")
    ap.add_argument("--fetch", action="store_true", help="EDGAR 실적일 캐시 구축(data/earnings/)")
    ap.add_argument("--force-fetch", action="store_true", help="캐시 무시하고 재수집")
    ap.add_argument("--no-ledger", action="store_true", help="원장 적재 생략(개발/디버그)")
    ap.add_argument("--out", type=str, default="", help="결과 JSON 저장 경로")
    ap.add_argument("--quick", action="store_true", help="RC/부트스트랩 축소(빠른 점검)")
    ap.add_argument("--only", type=str, default="", help="일부 아이디어만(쉼표구분 id)")
    args = ap.parse_args()
    log = not args.no_ledger
    rc_B = 400 if args.quick else 1500
    only = set(s.strip() for s in args.only.split(",") if s.strip())

    panel, recs = build_panel()

    if args.fetch or args.force_fetch:
        print("EDGAR 8-K Item 2.02 실적일 수집…", flush=True)
        summ = build_earnings_cache(panel.universe, force=args.force_fetch)
        print(f"  수집 {summ['fetched']} / 캐시 {summ['cached']} / CIK없음 {summ['no_cik']} "
              f"(총 {summ['n_symbols']})", flush=True)

    n_have = sum(1 for s in panel.universe if (EARN_DIR / f"{s.replace('.', '_')}.json").exists())
    cov = c3c.pit_coverage(panel)
    cr = c2d.cash_rate_series(panel)
    br = c2d.bench_returns(panel)
    ret_dates = panel.dates[1:]
    di, hi = c12._split_idx(ret_dates)
    qs = br[1:]
    qqq_des = c12._cagr([qs[i] for i in di], [ret_dates[i] for i in di])
    qqq_hol = c12._cagr([qs[i] for i in hi], [ret_dates[i] for i in hi])
    qqq_full = c12._cagr(qs, ret_dates)
    print(f"PIT 유니버스(가용) {len(panel.universe)} | 실적일 보유 {n_have} | 거래일 {len(panel.dates)} "
          f"{panel.dates[0]}~{panel.dates[-1]}", flush=True)
    print(f"커버리지 설계 {cov['design_mean']:.1%} 홀드 {cov['holdout_mean']:.1%} | "
          f"QQQ B&H CAGR 설계 {qqq_des:.2%} 홀드 {qqq_hol:.2%} 전구간 {qqq_full:.2%}", flush=True)

    results: dict = {
        "meta": {"universe_n": len(panel.universe), "earnings_symbols": n_have,
                 "n_days": len(panel.dates), "dates": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
                 "coverage": cov, "qqq_cagr": {"design": qqq_des, "holdout": qqq_hol, "full": qqq_full},
                 "cost": "10bp/side + 3bp half-spread + 5bp slip; close-to-close; 2x stress; micro=free",
                 "lane": LANE, "earnings_source": "SEC EDGAR 8-K Item 2.02 (keyless)",
                 "lookahead_caveat": "actual 스케줄은 발표일 완전예지(상한). expected(직전+91일)가 실전형.",
                 "upper_bound_caveat": "as-of 멤버 커버리지 ~30% (대형·생존주 편중) → 성과 상한"},
        "ideas": {},
    }

    for idea_id, base in IDEAS:
        if only and idea_id not in only:
            continue
        print(f"\n=== {idea_id} {base} ===", flush=True)
        res = evaluate_idea(panel, idea_id, base, cr, qqq_des=qqq_des, qqq_hol=qqq_hol,
                            qqq_full=qqq_full, bench_ret=br, log=log, rc_B=rc_B)
        results["ideas"][idea_id] = res
        h = res["holdout"]; th = h["trades"]
        print(f"  홀드 CAGR {h['cagr']:.2%}(vsQQQ {h['cagr_vs_qqq']:+.1%}) MDD {h['mdd']:.1%} "
              f"거래 n={th.get('n')} t={th.get('tstat')} CIlo={th.get('ci_lo')} "
              f"turn {res['turnover_yr']:.1f}/yr → {res['verdict']}", flush=True)

    print("\n=== look-ahead 갭(actual vs expected, pre_earn 규칙) ===", flush=True)
    lag = lookahead_gap(panel, cr)
    results["lookahead_gap"] = lag
    print(f"  actual 거래평균 {lag['actual']['mean_ret_bps']:.1f}bp t={lag['actual']['tstat']:.2f} "
          f"vs expected {lag['expected']['mean_ret_bps']:.1f}bp t={lag['expected']['tstat']:.2f} "
          f"| 갭 {lag['gap_mean_bps']:.1f}bp, 홀드CAGR갭 {lag['gap_holdout_cagr']:+.1%}", flush=True)

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\n결과 저장: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
