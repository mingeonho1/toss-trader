#!/usr/bin/env python3
"""데일리 결정 엔진 — 메인 루프의 심장(매일 페이퍼 로그로 판단을 갱신).

결정론적(LLM 없음)·주문 없음·멱등(ET 세션 날짜 기준). 규칙은 사전등록본
``docs/loop/decision_rules.md`` (2026-09-29) 을 그대로 구현한다. 백테스트 기대치는 동결 파일
``docs/loop/backtest_expectations.json`` 에서 읽는다.

입력: 페이퍼 장부(``data/paperlab/{name}`` 또는 폴백 ``data/_publish/state/paperlab/{name}``)의
단위($1k)장부 에쿼티 곡선 + 벤치마크 ``qqq_bh``/``tqqq_bh`` 곡선.
출력: ``reports/decision_latest.md``(한국어), ``data/loop/decisions.jsonl``,
``data/loop/requests.jsonl``, ``data/loop/decision_state.json``.

상태 = 각 전략 곡선을 세션순으로 **전량 리플레이**해 결정론적으로 재계산(히스테리시스 K=5,
킬 조건 즉시 강등). 그룹 내 순위가 필요한 LIVE_READY 만 교차단면 2차 패스로 층을 올린다.

사용:
  PYTHONPATH=src python scripts/loop/daily_decision.py
  PYTHONPATH=src python scripts/loop/daily_decision.py --no-weights   # 목표비중 산출 생략(순수 판정)
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for _p in (str(SRC), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = ROOT / "data"
REPORTS = ROOT / "reports"
LOOP_DIR = DATA / "loop"
PAPERLAB_DIR = DATA / "paperlab"
PUBLISH_PAPERLAB = DATA / "_publish" / "state" / "paperlab"
EXPECT_FILE = ROOT / "docs" / "loop" / "backtest_expectations.json"

REPORT_LATEST = REPORTS / "decision_latest.md"
STATE_FILE = LOOP_DIR / "decision_state.json"
DECISIONS_FILE = LOOP_DIR / "decisions.jsonl"
REQUESTS_FILE = LOOP_DIR / "requests.jsonl"

# ── 사전등록 상수(docs/loop/decision_rules.md 와 일치; 동결) ─────────────────────
K = 5                       # 히스테리시스: 전이는 조건이 K 연속 세션 유지 시에만(킬 제외)
WARMUP_MAX = 21             # n<21 → WARMUP (≈1 거래월)
EVAL_MAX = 63              # 21≤n<63 → EVALUATING; n≥63 → CANDIDATE 자격 (≈1 분기)
LIVE_READY_EXTRA = 21       # CANDIDATE ≥21 연속 세션 + 그룹 1위 → LIVE_READY 자격
Z_CANDIDATE = -1.0
Z_KILL = -2.0
Z_KILL_MIN_N = 42
DD_CANDIDATE = 1.0
DD_KILL = 1.25
EXCESS_KILL = -0.15
EXCESS_KILL_MIN_N = 63
RETIRE_SESSIONS = 21
REGIME_QQQ_ABS = 0.03       # |오늘 QQQ 로그수익| ≥ 3% → regime_note
TRADING_DAYS = 252

BENCHMARKS = frozenset({"qqq_bh", "tqqq_bh"})
GROUP_LABELS = {"retail": "실계좌", "leverage": "레버ETP", "explore": "탐색"}
GROUP_ORDER = ["retail", "leverage", "explore"]
STATE_PRIORITY = {"LIVE_READY": 5, "CANDIDATE": 4, "EVALUATING": 3,
                  "WARMUP": 2, "DEMOTED": 1, "RETIRED": 0}
# 부록 A(2026-09-29): 실행 가능 추천은 이 상태들에서만. 그 외엔 "추천 없음 — 기본 DCA 유지".
ACTIONABLE_STATES = frozenset({"CANDIDATE", "LIVE_READY"})

# 로스터 빌드 실패 시 폴백(런타임 classify_group 결과와 일치, 2026-09-29 캡처). 동결.
FROZEN_GROUPS = {
    "lrr_tqqq": "leverage", "lrr_tqqq_sqqq": "leverage", "rsi2_tqqq": "leverage",
    "voltarget_3x": "leverage", "mom_top5_ndx": "retail", "hot_rvol_swing": "leverage",
    "qqq_bh": "retail", "tqqq_bh": "leverage", "overnight_tqqq": "leverage",
    "overnight_qqq": "retail", "ep_gap_swing": "retail", "ftlt": "leverage",
    "ftlt_moc": "leverage", "ftlt_1x": "retail", "holy_grail": "leverage",
    "simple_rsi_uvxy": "leverage", "lrs200_tqqq": "leverage", "lrs200_qqq": "retail",
    "sma200_buffer_tqqq": "leverage", "nine_sig": "leverage", "hibeta_basket": "retail",
    "btc_proxy_mstr_coin": "explore", "ftlt_hibeta": "retail", "ftlt_hibeta_psq": "retail",
    "holygrail_hibeta": "retail", "simple_hibeta": "retail", "buffer_hibeta": "retail",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 장부 로딩 ─────────────────────────────────────────────────────────────────
def resolve_books_dir(local: Path = PAPERLAB_DIR, fallback: Path = PUBLISH_PAPERLAB) -> Path:
    """로컬 data/paperlab 에 장부가 있으면 그걸, 없으면 paper-log 체크아웃 폴백."""
    if _has_books(local):
        return local
    if _has_books(fallback):
        return fallback
    return local


def _has_books(d: Path) -> bool:
    if not d.exists():
        return False
    for child in d.iterdir():
        if child.name.startswith("_"):
            continue
        if (child / "state.json").exists():
            return True
    return False


def list_book_names(d: Path) -> list[str]:
    if not d.exists():
        return []
    out = []
    for child in sorted(d.iterdir()):
        if child.name.startswith("_"):
            continue
        if (child / "state.json").exists():
            out.append(child.name)
    return out


def load_unit_curve(d: Path, name: str) -> list[tuple[str, float]]:
    """장부 state.json → 단위($1k)장부 곡선 [(iso_date, unit_equity>0), ...] (세션순)."""
    p = d / name / "state.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[tuple[str, float]] = []
    for row in st.get("equity", []) or []:
        if len(row) >= 2:
            try:
                v = float(row[1])
            except (TypeError, ValueError):
                continue
            if v > 0:
                out.append((str(row[0]), v))
    return out


# ── 지표 ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Metrics:
    n: int
    cum: float | None
    annual: float | None
    excess_qqq: float | None      # 누적 로그 초과 vs QQQ
    excess_tqqq: float | None
    vol: float | None             # 연환산
    mdd: float
    z: float | None
    dd_ratio: float | None


def _max_drawdown(vals: Sequence[float]) -> float:
    peak = -math.inf
    mdd = 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def _cum_log_excess(curve: Sequence[tuple[str, float]], bench: Mapping[str, float]) -> float | None:
    """곡선의 누적 로그수익 − 벤치 누적 로그수익(공통 날짜 스텝만)."""
    if not bench:
        return None
    total = 0.0
    steps = 0
    for i in range(1, len(curve)):
        d0, v0 = curve[i - 1]
        d1, v1 = curve[i]
        b0, b1 = bench.get(d0), bench.get(d1)
        if v0 > 0 and v1 > 0 and b0 and b1 and b0 > 0 and b1 > 0:
            total += math.log(v1 / v0) - math.log(b1 / b0)
            steps += 1
    return total if steps >= 1 else None


def compute_metrics(curve: Sequence[tuple[str, float]], qqq: Mapping[str, float],
                    tqqq: Mapping[str, float], expect: Mapping[str, Any]) -> Metrics:
    """라이브 곡선(prefix) 지표. curve 는 (iso_date, unit_equity) 세션순."""
    n = len(curve)
    if n == 0:
        return Metrics(0, None, None, None, None, None, 0.0, None, None)
    vals = [v for _, v in curve]
    cum = vals[-1] / vals[0] - 1.0 if vals[0] > 0 else None
    annual = None
    if n >= 2 and vals[0] > 0:
        annual = (vals[-1] / vals[0]) ** (TRADING_DAYS / (n - 1)) - 1.0
    log_rets = [math.log(vals[i] / vals[i - 1]) for i in range(1, n)
                if vals[i] > 0 and vals[i - 1] > 0]
    vol = statistics.pstdev(log_rets) * math.sqrt(TRADING_DAYS) if len(log_rets) >= 2 else None
    mdd = _max_drawdown(vals)
    excess_qqq = _cum_log_excess(curve, qqq)
    excess_tqqq = _cum_log_excess(curve, tqqq)
    exp_excess = float(expect.get("exp_annual_log_excess_vs_qqq", 0.0))
    tvol = float(expect.get("tracking_vol_annual", 0.0))
    bt_mdd = float(expect.get("backtest_mdd", 0.0))
    z = None
    if excess_qqq is not None and n >= 2 and tvol > 0:
        expected = exp_excess * (n / TRADING_DAYS)
        denom = tvol * math.sqrt(n / TRADING_DAYS)
        if denom > 0:
            z = (excess_qqq - expected) / denom
    dd_ratio = (mdd / bt_mdd) if bt_mdd < -1e-9 else None
    return Metrics(n, cum, annual, excess_qqq, excess_tqqq, vol, mdd, z, dd_ratio)


# ── 킬/후보 조건 ──────────────────────────────────────────────────────────────
def kill_check(m: Metrics) -> list[tuple[str, str]]:
    """(사유, 종류) 목록. 종류 ∈ {dd, z, excess}. 비었으면 킬 아님."""
    out: list[tuple[str, str]] = []
    if m.dd_ratio is not None and m.dd_ratio > DD_KILL:
        out.append((f"DD ratio {m.dd_ratio:.2f} > {DD_KILL}", "dd"))
    if m.z is not None and m.z < Z_KILL and m.n >= Z_KILL_MIN_N:
        out.append((f"z {m.z:.2f} < {Z_KILL} at n={m.n}≥{Z_KILL_MIN_N}", "z"))
    if m.excess_qqq is not None and m.excess_qqq < EXCESS_KILL and m.n >= EXCESS_KILL_MIN_N:
        out.append((f"excess {m.excess_qqq:+.1%} < {EXCESS_KILL:+.0%} at n={m.n}≥{EXCESS_KILL_MIN_N}",
                    "excess"))
    return out


def candidate_condition(m: Metrics) -> bool:
    return (m.n >= EVAL_MAX and m.excess_qqq is not None and m.excess_qqq > 0
            and m.z is not None and m.z > Z_CANDIDATE
            and (m.dd_ratio is None or m.dd_ratio <= DD_CANDIDATE))


def _band(n: int) -> str:
    return "WARMUP" if n < WARMUP_MAX else "EVALUATING"


# ── 전략 단위 상태 리플레이(히스테리시스 K, 킬 즉시) ─────────────────────────────
@dataclass
class SessionRecord:
    date: str
    state: str
    cand_consec: int
    kills: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    m: Metrics | None = None


def replay_strategy(curve: Sequence[tuple[str, float]], qqq: Mapping[str, float],
                    tqqq: Mapping[str, float], expect: Mapping[str, Any]) -> list[SessionRecord]:
    """전략 곡선 전체를 세션순으로 리플레이 → 세션별 상태 기록(LIVE_READY 제외; 층은 2차 패스)."""
    state = "WARMUP"
    cand_true = fail = demoted = recover = cand_consec = 0
    hist: list[SessionRecord] = []
    for i in range(len(curve)):
        m = compute_metrics(curve[: i + 1], qqq, tqqq, expect)
        kills = kill_check(m)
        prev = state
        if prev == "RETIRED":
            new = "RETIRED"
        elif kills:
            new = "DEMOTED"
            cand_true = fail = recover = cand_consec = 0
        elif prev == "DEMOTED":
            recover += 1
            if recover >= K:                     # 복귀도 K=5 확인(플립플롭 억제)
                recover = 0
                new = _band(m.n)
                cand_true = fail = cand_consec = 0
            else:
                new = "DEMOTED"
        else:
            cond = candidate_condition(m)
            if prev in ("CANDIDATE", "LIVE_READY"):
                if cond:
                    fail = 0
                    cand_true += 1
                    cand_consec += 1
                    new = "CANDIDATE"
                else:
                    fail += 1
                    cand_consec += 1
                    if fail >= K:                # 조건 실패 5연속 → 강등(EVALUATING)
                        new = _band(m.n)
                        cand_true = fail = cand_consec = 0
                    else:
                        new = "CANDIDATE"
            else:                                # WARMUP / EVALUATING
                if cond:
                    cand_true += 1
                    if cand_true >= K:           # 조건 충족 5연속 → 승격
                        new = "CANDIDATE"
                        cand_consec = 1
                        fail = 0
                    else:
                        new = _band(m.n)
                else:
                    cand_true = 0
                    new = _band(m.n)
            recover = 0
        # DEMOTED 연속 카운트 & RETIRE 만료
        if new == "DEMOTED":
            demoted = demoted + 1 if prev in ("DEMOTED", "RETIRED") else 1
            if demoted >= RETIRE_SESSIONS:
                new = "RETIRED"
        elif new != "RETIRED":
            demoted = 0
        state = new
        hist.append(SessionRecord(date=curve[i][0], state=state,
                                  cand_consec=cand_consec if state in ("CANDIDATE", "LIVE_READY") else 0,
                                  kills=[r for r, _ in kills], kinds=[k for _, k in kills], m=m))
    return hist


# ── LIVE_READY 교차단면 층(그룹 1위 + K 연속) ─────────────────────────────────
def layer_live_ready(histories: Mapping[str, list[SessionRecord]],
                     groups: Mapping[str, str]) -> None:
    """CANDIDATE(≥21 연속) 중 그룹 내 excess_qqq 1위가 K 연속이면 그 세션을 LIVE_READY 로 승격(in-place)."""
    by_date: dict[str, dict[str, SessionRecord]] = defaultdict(dict)
    for name, hist in histories.items():
        if name in BENCHMARKS:
            continue
        for rec in hist:
            by_date[rec.date][name] = rec
    streak: dict[str, int] = defaultdict(int)
    for d in sorted(by_date):
        per_group: dict[str, list[tuple[str, float]]] = defaultdict(list)
        day = by_date[d]
        for name, rec in day.items():
            exq = rec.m.excess_qqq if rec.m else None
            if (rec.state == "CANDIDATE" and rec.cand_consec >= LIVE_READY_EXTRA
                    and exq is not None):
                per_group[groups.get(name, "leverage")].append((name, exq))
        tops = {g: max(lst, key=lambda x: x[1])[0] for g, lst in per_group.items() if lst}
        for name, rec in day.items():
            grp = groups.get(name, "leverage")
            is_top = (tops.get(grp) == name and rec.state == "CANDIDATE"
                      and rec.cand_consec >= LIVE_READY_EXTRA)
            streak[name] = streak[name] + 1 if is_top else 0
            if streak[name] >= K:
                rec.state = "LIVE_READY"


# ── 요청 방출 ─────────────────────────────────────────────────────────────────
def build_requests(finals: Mapping[str, SessionRecord], groups: Mapping[str, str],
                   qqq_today_ret: float | None, session_date: str) -> list[dict[str, Any]]:
    """오늘 세션 상태에서 결정론적으로 작업요청 재계산(날짜키 upsert = 멱등)."""
    reqs: list[dict[str, Any]] = []
    for name, rec in sorted(finals.items()):
        if name in BENCHMARKS or rec.m is None:
            continue
        kinds = set(rec.kinds)
        if "dd" in kinds:
            reqs.append({"type": "audit", "strategy": name,
                         "reason": next(r for r in rec.kills if r.startswith("DD"))})
        if "excess" in kinds:
            reqs.append({"type": "audit", "strategy": name,
                         "reason": next(r for r in rec.kills if r.startswith("excess"))})
        if rec.m.z is not None and rec.m.z < Z_KILL and rec.m.n >= Z_KILL_MIN_N:
            reqs.append({"type": "investigate_divergence", "strategy": name,
                         "z": round(rec.m.z, 4)})
    # scout: 실계좌(벤치 제외) 전부 n≥63 인데 CANDIDATE/LIVE_READY 없음
    retail = [(n, r) for n, r in finals.items()
              if groups.get(n) == "retail" and n not in BENCHMARKS and r.m is not None]
    if retail and all(r.m.n >= EVAL_MAX for _, r in retail) \
            and not any(r.state in ("CANDIDATE", "LIVE_READY") for _, r in retail):
        reqs.append({"type": "scout",
                     "reason": "no CANDIDATE in 실계좌 group after 63 sessions"})
    # regime_note: QQQ 일간 급변
    if qqq_today_ret is not None and abs(qqq_today_ret) >= REGIME_QQQ_ABS:
        reqs.append({"type": "regime_note", "qqq_return": round(qqq_today_ret, 4),
                     "reason": "QQQ moved 3%+ today"})
    for r in reqs:
        r["session_date"] = session_date
    return reqs


# ── 추천(그룹 내 최상위) ──────────────────────────────────────────────────────
def reco_eligible(name: str, group: str) -> bool:
    """추천/참고 대상 자격(부록 A): 벤치·낙관적 라벨 변이(`*_moc`)·탐색 그룹 제외."""
    return (name not in BENCHMARKS and not name.endswith("_moc") and group != "explore")


def pick_top(finals: Mapping[str, SessionRecord], groups: Mapping[str, str], group: str,
             expect: Mapping[str, Any]) -> str | None:
    """그룹의 자격 전략(벤치·`*_moc`·탐색 제외) 중 상태 우선 → 라이브 excess → 동결 backtest_cagr 최상위."""
    cands = [(n, r) for n, r in finals.items()
             if groups.get(n) == group and reco_eligible(n, groups.get(n, group))]
    if not cands:
        return None

    def key(item: tuple[str, SessionRecord]) -> tuple:
        n, r = item
        exq = r.m.excess_qqq if (r.m and r.m.excess_qqq is not None) else None
        prior = float((expect.get(n) or {}).get("backtest_cagr", -math.inf))
        # 라이브 excess 있으면 그걸로, 없으면 동결 prior 로 tie-break
        return (STATE_PRIORITY.get(r.state, 0),
                exq if exq is not None else -math.inf,
                prior)

    return max(cands, key=key)[0]


# ── 목표비중(run_strategy 와 동일 코드; 주문 없음) ────────────────────────────
def recommendation_weights(name: str, strat: Any, *, offline: bool = True) -> dict[str, float] | None:
    """오늘의 dry-run 목표비중. compute_target_weights(=run_strategy 와 동일)만 호출 — 주문 API 미접촉."""
    if strat is None:
        return None
    try:
        import paperlab_run as plr
        import run_strategy as rs
        needed = sorted(set(strat.universe()) | {"QQQ"})
        panel = plr._load_panel(needed, offline=offline)
        return rs.compute_target_weights(strat, panel, rs.paper_strategy_state(name))
    except Exception:  # noqa: BLE001  판정 스텝은 항상 견고(가중치 실패해도 리포트는 나온다)
        return None


# ── 로스터/그룹 ───────────────────────────────────────────────────────────────
def build_roster_groups() -> tuple[dict[str, Any], dict[str, str]]:
    """(name→strategy, name→group). 로스터 빌드 실패 시 strategy 비고 + 동결 그룹 폴백."""
    try:
        import paperlab_run as plr
        from toss_trader.paperlab import classify_group
        from toss_trader.paperlab_strategies._universes import NDX_100
        swing = plr._cached_universe()
        mom = [s for s in NDX_100 if plr._cache_file(s).exists()]
        hib = plr._hibeta_cached()
        strats = {s.name: s for s in plr._build(swing, mom, hib)}
        groups = {n: classify_group(s.universe(), getattr(s, "group", None))
                  for n, s in strats.items()}
        return strats, groups
    except Exception:  # noqa: BLE001
        return {}, dict(FROZEN_GROUPS)


# ── 영속화(멱등) ──────────────────────────────────────────────────────────────
def upsert_jsonl(path: Path, record: dict[str, Any], key: str = "session_date") -> None:
    rows = _read_jsonl(path)
    kv = str(record.get(key))
    rows = [r for r in rows if str(r.get(key)) != kv]
    rows.append(record)
    rows.sort(key=lambda r: str(r.get(key)))
    _write_jsonl(path, rows)


def write_requests(path: Path, session_date: str, records: Sequence[dict[str, Any]]) -> None:
    """그 세션 날짜의 기존 요청을 모두 제거하고 새 요청으로 교체(멱등)."""
    rows = [r for r in _read_jsonl(path) if str(r.get("session_date")) != str(session_date)]
    rows.extend(records)
    rows.sort(key=lambda r: (str(r.get("session_date")), str(r.get("type")), str(r.get("strategy", ""))))
    _write_jsonl(path, rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ── 렌더링(한국어) ────────────────────────────────────────────────────────────
def _pct(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x * 100:+.{nd}f}%"


def _num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x + 0.0:.{nd}f}"   # -0.0 → 0.0 정규화


def render_report(*, generated: str, session_date: str, finals: Mapping[str, SessionRecord],
                  prevs: Mapping[str, str | None], groups: Mapping[str, str],
                  changes: Sequence[dict[str, Any]], rec_retail: dict[str, Any] | None,
                  rec_leverage: dict[str, Any] | None, requests: Sequence[dict[str, Any]],
                  books_dir: str) -> str:
    L: list[str] = [
        "# 오늘의 판단 (데일리 결정 엔진)",
        "",
        f"- 생성: `{generated}`  ·  ET 세션: `{session_date}`  ·  장부: `{books_dir}`",
        "- 결정론적 · LLM 없음 · **주문 없음** · 멱등(같은 세션 날짜 재실행 = 동일 결과).",
        "- 규칙: `docs/loop/decision_rules.md` (사전등록 2026-09-29). 기대치: `docs/loop/backtest_expectations.json`.",
        "",
        "## 오늘의 판단",
        "",
    ]
    # 실계좌: CANDIDATE/LIVE_READY 일 때만 실행 가능 추천. 그 외엔 기본 DCA 유지 + 관찰 선두 별기(부록 A).
    if rec_retail and rec_retail.get("actionable"):
        L += [f"- **실계좌 추천**: `{rec_retail['name']}` — 상태 **{rec_retail['state']}**"
              + (f" · 어제 대비: {rec_retail['change']}" if rec_retail.get("change")
                 else " · 어제 대비: 변화 없음"),
              f"  - 오늘의 dry-run 목표비중: {_weights_text(rec_retail.get('target_weights'))}"]
    else:
        L.append("- **실계좌 추천 없음 — 기본 DCA 유지 (CANDIDATE 이상 전략 없음)**")
        if rec_retail:
            L.append(f"  - 관찰 선두(추천 아님): `{rec_retail['name']}` — 상태 {rec_retail['state']}")
    # 레버ETP: 실계좌 대상 아님(예탁금·승인 필요) → 항상 관찰 선두(참고). `*_moc`·탐색·벤치 제외.
    if rec_leverage:
        L.append(f"- **레버ETP 관찰 선두(추천 아님)**: `{rec_leverage['name']}` — "
                 f"상태 {rec_leverage['state']}")
        if rec_leverage.get("actionable"):
            L.append(f"  - (참고) 오늘의 dry-run 목표비중: {_weights_text(rec_leverage.get('target_weights'))}")
    else:
        L.append("- **레버ETP 관찰 선두**: (해당 그룹 전략 없음)")

    L += ["", "## 오늘 상태 변화", ""]
    if changes:
        L += ["| 전략 | 그룹 | 어제 | 오늘 | 사유 |", "|---|---|---|---|---|"]
        for c in changes:
            L.append(f"| `{c['name']}` | {GROUP_LABELS.get(c['group'], c['group'])} | "
                     f"{c['prev']} | **{c['new']}** | {c['reason']} |")
    else:
        L.append("- (상태 변화 없음.)")

    for gkey in GROUP_ORDER:
        rows = [(n, r) for n, r in finals.items() if groups.get(n) == gkey]
        if not rows:
            continue
        rows.sort(key=lambda it: (STATE_PRIORITY.get(it[1].state, 0),
                                  (it[1].m.excess_qqq if it[1].m and it[1].m.excess_qqq is not None
                                   else -math.inf)), reverse=True)
        L += ["", f"### {GROUP_LABELS[gkey]} ({len(rows)})", "",
              "| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for n, r in rows:
            m = r.m
            bench = " (벤치)" if n in BENCHMARKS else ""
            if m is None:
                L.append(f"| `{n}`{bench} | {r.state} | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |")
                continue
            L.append(
                f"| `{n}`{bench} | {r.state} | {m.n} | {_pct(m.cum)} | {_pct(m.annual)} | "
                f"{_pct(m.excess_qqq)} | {_pct(m.excess_tqqq)} | {_pct(m.vol)} | {_pct(m.mdd)} | "
                f"{_num(m.z)} | {_num(m.dd_ratio)} |")

    L += ["", "## 방출된 작업요청 (data/loop/requests.jsonl)", ""]
    if requests:
        for rq in requests:
            L.append(f"- `{rq['type']}` " + json.dumps(
                {k: v for k, v in rq.items() if k not in ("type", "session_date")},
                ensure_ascii=False))
    else:
        L.append("- (트리거된 요청 없음.)")

    L += ["", "## 주의", "",
          "- 상태·지표는 페이퍼 장부만으로 산출(실주문 없음). 실거래 전환은 부록 v3대로 사용자 승인 필요.",
          "- vs QQQ/TQQQ 는 **누적 로그 초과**. z 는 백테스트 기대 대비 표준화 점수(z<0 = 기대 미달).", ""]
    return "\n".join(L)


def _weights_text(tw: dict[str, float] | None) -> str:
    if tw:
        return ", ".join(f"`{s}`={w:.1%}" for s, w in sorted(tw.items()))
    if tw == {}:
        return "100% 현금"
    return "(목표비중 산출 생략/불가)"


# ── 오케스트레이션 ────────────────────────────────────────────────────────────
def _qqq_today_return(qqq: Sequence[tuple[str, float]]) -> float | None:
    if len(qqq) < 2:
        return None
    (_, a), (_, b) = qqq[-2], qqq[-1]
    return math.log(b / a) if a > 0 and b > 0 else None


def _fallback_session_date() -> str:
    try:
        from daily_scoreboard import et_session_date, _now_utc
        d = et_session_date(_now_utc())
        if d:
            return d.isoformat()
    except Exception:  # noqa: BLE001
        pass
    return date.today().isoformat()


def run(*, books_dir: Path | None = None, expect_path: Path = EXPECT_FILE,
        with_weights: bool = True, offline: bool = True,
        report_path: Path = REPORT_LATEST, state_path: Path = STATE_FILE,
        decisions_path: Path = DECISIONS_FILE, requests_path: Path = REQUESTS_FILE,
        strats: Mapping[str, Any] | None = None,
        groups: Mapping[str, str] | None = None) -> dict[str, Any]:
    books_dir = resolve_books_dir() if books_dir is None else books_dir
    try:
        expect = json.loads(expect_path.read_text(encoding="utf-8")).get("strategies", {})
    except (OSError, ValueError):
        expect = {}
    if strats is None or groups is None:
        rstrats, rgroups = build_roster_groups()
        strats = rstrats if strats is None else strats
        groups = rgroups if groups is None else groups

    names = list_book_names(books_dir)
    curves = {n: load_unit_curve(books_dir, n) for n in names}
    qqq_curve = curves.get("qqq_bh", [])
    tqqq_curve = curves.get("tqqq_bh", [])
    qqq_map = dict(qqq_curve)
    tqqq_map = dict(tqqq_curve)

    # 그룹 폴백(장부엔 있으나 로스터에 없을 때).
    groups = {n: (groups.get(n) or FROZEN_GROUPS.get(n, "leverage")) for n in names}

    histories: dict[str, list[SessionRecord]] = {}
    for n in names:
        histories[n] = replay_strategy(curves[n], qqq_map, tqqq_map, expect.get(n, {}))
    layer_live_ready(histories, groups)

    finals: dict[str, SessionRecord] = {}
    prevs: dict[str, str | None] = {}
    for n in names:
        h = histories[n]
        if h:
            finals[n] = h[-1]
            prevs[n] = h[-2].state if len(h) >= 2 else None
        else:
            finals[n] = SessionRecord(date="", state="WARMUP", cand_consec=0, m=None)
            prevs[n] = None

    session_date = max((r.date for r in finals.values() if r.date), default=_fallback_session_date())

    changes: list[dict[str, Any]] = []
    for n in sorted(names):
        prev, cur = prevs[n], finals[n].state
        if prev is not None and prev != cur:
            reason = "; ".join(finals[n].kills) if finals[n].kills else f"{prev}→{cur}"
            changes.append({"name": n, "group": groups.get(n, "?"),
                            "prev": prev, "new": cur, "reason": reason})

    requests = build_requests(finals, groups, _qqq_today_return(qqq_curve), session_date)
    write_requests(requests_path, session_date, requests)

    def _reco(group: str) -> dict[str, Any] | None:
        top = pick_top(finals, groups, group, expect)
        if not top:
            return None
        r = finals[top]
        actionable = r.state in ACTIONABLE_STATES
        change = None
        if prevs.get(top) is not None and prevs[top] != r.state:
            change = "; ".join(r.kills) if r.kills else f"{prevs[top]}→{r.state}"
        tw = None
        if with_weights and actionable:          # 부록 A: 실행 가능할 때만 목표비중 산출(노이즈 추천 방지)
            tw = recommendation_weights(top, (strats or {}).get(top), offline=offline)
        return {"name": top, "state": r.state, "change": change,
                "actionable": actionable, "target_weights": tw}

    rec_retail = _reco("retail")
    rec_leverage = _reco("leverage")

    generated = _now_iso()
    report = render_report(generated=generated, session_date=session_date, finals=finals,
                           prevs=prevs, groups=groups, changes=changes, rec_retail=rec_retail,
                           rec_leverage=rec_leverage, requests=requests, books_dir=str(books_dir))
    _write_text(report_path, report)

    decision_record = {
        "session_date": session_date, "generated": generated,
        "strategies": {n: _metric_row(finals[n], groups.get(n, "?")) for n in sorted(names)},
        "changes": changes,
        "recommendation": {"retail": _reco_lite(rec_retail), "leverage": _reco_lite(rec_leverage)},
        "requests": requests,
    }
    upsert_jsonl(decisions_path, decision_record)

    state_snapshot = {
        "session_date": session_date, "generated": generated, "books_dir": str(books_dir),
        "states": {n: finals[n].state for n in sorted(names)},
        "prev_states": {n: prevs[n] for n in sorted(names)},
    }
    _write_text(state_path, json.dumps(state_snapshot, ensure_ascii=False, indent=2) + "\n")

    return {"session_date": session_date, "report": str(report_path),
            "n_strategies": len(names), "n_changes": len(changes), "n_requests": len(requests),
            "recommendation_retail": (rec_retail or {}).get("name") if (rec_retail or {}).get("actionable") else None,
            "retail_observed_leader": (rec_retail or {}).get("name"),
            "recommendation_leverage": (rec_leverage or {}).get("name"),
            "report_text": report}


def _metric_row(rec: SessionRecord, group: str) -> dict[str, Any]:
    m = rec.m
    d = {"state": rec.state, "group": group}
    if m is not None:
        d.update({"n": m.n, "cum": m.cum, "annual": m.annual, "excess_qqq": m.excess_qqq,
                  "excess_tqqq": m.excess_tqqq, "vol": m.vol, "mdd": m.mdd,
                  "z": m.z, "dd_ratio": m.dd_ratio})
    else:
        d["n"] = 0
    return d


def _reco_lite(reco: dict[str, Any] | None) -> dict[str, Any] | None:
    if not reco:
        return None
    return {"name": reco["name"], "state": reco["state"],
            "actionable": reco.get("actionable", False),
            "target_weights": reco.get("target_weights")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="데일리 결정 엔진(결정론적·주문 없음·멱등).")
    ap.add_argument("--books-dir", type=str, default=None,
                    help="페이퍼 장부 루트(기본: data/paperlab, 없으면 paper-log 폴백).")
    ap.add_argument("--no-weights", action="store_true",
                    help="추천의 오늘 dry-run 목표비중 산출을 생략(순수 판정만).")
    ap.add_argument("--online", action="store_true",
                    help="목표비중용 패널을 온라인으로 로드(기본은 캐시 전용 — refresh 후 실행 가정).")
    args = ap.parse_args(argv)
    res = run(books_dir=Path(args.books_dir) if args.books_dir else None,
              with_weights=not args.no_weights, offline=not args.online)
    print(json.dumps({k: v for k, v in res.items() if k != "report_text"}, ensure_ascii=False))
    print(f"→ {res['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
