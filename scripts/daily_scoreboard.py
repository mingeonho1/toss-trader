#!/usr/bin/env python3
"""매일 자동 스코어보드 — 하루 1회, 멱등, 읽기 전용(주문 없음).

세션이 끝난 뒤에도 "루프"가 **정직한 포워드 증거**를 계속 쌓게 하는 단일 작업이다.
어느 때 돌려도 안전하고(멱등), 절대 주문을 내지 않는다. 여러 하위 단계를 각각
**격리**해 실행하며(한 단계가 실패해도 나머지는 계속), 각 단계에 **타임아웃**을 건다.

단계:
  (a) 인트라데이 수집기 — ET 16:05 이후(정규장 마감)일 때만 오늘 세션을 수집(네트워크).
      그리고 레인3 인트라데이 섀도(가상 트레이드 누적)를 이어 돌린다.
  (b) 포워드 페이퍼 장부 갱신 — 자격증명이 있으면 실 토스 시세, 없으면 캐시(Nasdaq 종가):
      · forward_paper_compare.py (Lump-sum ETF 기준선 + 액티브 후보들)
      · forward_lifecycle_paper.py (라이프사이클 슬리브 vs DCA-QQQ)
  (c) DCA dry-run 플랜(분할·FX 경고 포함, 자격증명 있을 때) + 양도세 리포트.
  (d) reports/scoreboard_latest.md(대시보드) + data/scoreboard_history.jsonl(변경 이력) 기록.

사용:
  PYTHONPATH=src python scripts/daily_scoreboard.py            # 자동 판정(자격증명 유무·ET시각)
  PYTHONPATH=src python scripts/daily_scoreboard.py --offline  # 네트워크·API 없이 캐시만으로(로컬 검증/CI)
  PYTHONPATH=src python scripts/daily_scoreboard.py --status    # 최신 대시보드 출력만
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import sleep as _sleep
from typing import Any, Callable, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Py<3.9만 해당(요구사항 3.11이라 실제로 안 탐)
    ZoneInfo = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

LATEST_REPORT = REPORTS / "scoreboard_latest.md"
HISTORY_FILE = DATA / "scoreboard_history.jsonl"

# 포워드 페이퍼 상태/트레이드 경로(각 스크립트의 기본값과 일치).
FP_STATE = DATA / "forward_paper_state.json"
LC_STATE = DATA / "forward_lifecycle_state.json"
SHADOW_TRADES = DATA / "intraday_shadow" / "trades.jsonl"

KST = timezone(timedelta(hours=9))
ET_CLOSE = time(16, 5)  # 정규장 마감(16:00 ET) + 여유 5분

# forward_paper_compare 가 오프라인에서 읽는 일봉 캐시 유니버스(그 스크립트의 ALL_SYMBOLS).
FP_UNIVERSE = ["QQQ", "SCHD", "GLD", "SPY", "EFA", "IWM", "IEF", "BIL"]
FP_DEPTH = 320
HIST_CACHE = DATA / "_hist_cache"
CANDLE_CACHE = DATA / "_candle_cache"

# 포워드 장부가 실제로 마크투마켓에 쓰는 심볼(forward_paper 유니버스 + lifecycle 의 QLD).
# refresh_closes 는 오직 이 심볼들만 정중히 갱신한다(불필요한 요청 회피).
BOOK_SYMBOLS = sorted(set(FP_UNIVERSE) | {"QLD"})
REFRESH_DAYS = 14          # Nasdaq fromdate 창(최근 ~2주). 병합이 중복 제거하므로 겹쳐도 안전.
REFRESH_SPACING = 1.6      # 심볼 간 최소 요청 간격(초). ≥1.5 준수(정중한 폴링).

# 인트라데이 섀도 규칙 표시 순서/라벨(intraday_shadow_run 과 동일).
RULE_ORDER = ["orb5_core", "orb15_core", "momentum_core",
              "orb5_movers", "orb15_movers", "gap_and_go_movers"]
RULE_LABEL = {
    "orb5_core": "ORB-5 (QQQ/TQQQ)",
    "orb15_core": "ORB-15 (QQQ/TQQQ)",
    "momentum_core": "Intraday momentum (QQQ/TQQQ)",
    "orb5_movers": "ORB-5 (movers)",
    "orb15_movers": "ORB-15 (movers)",
    "gap_and_go_movers": "Gap-and-go (movers)",
}
N_MIN = 200          # spec §3.5: n<200 이면 통계주장 불가
T_MIN = 3.0
BASE_SIZE_FALLBACK = "30"

# 포워드 장부 라벨(상태 파일의 전략/포트폴리오 id → 사람이 읽는 이름).
BOOK_LABELS = {
    "lumpsum_etf": "B0 Lump-sum ETF (QQQ60/SCHD25/GLD15)",
    "dual_momentum": "B1 Dual momentum → IEF",
    "regime_200d": "200d regime filter → IEF",
    "sma_cross": "SMA 20/60 trend top3",
    "lifecycle_sleeve": "Lifecycle sleeve (QQQ/QLD)",
    "dca_qqq": "DCA-QQQ (lifecycle benchmark)",
}


# ── 유틸 ──────────────────────────────────────────────────────────────────────
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _et(now_utc: datetime) -> datetime:
    """UTC → America/New_York. zoneinfo 없으면 UTC 그대로(보수적)."""
    if ZoneInfo is None:  # pragma: no cover
        return now_utc
    try:
        return now_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001  tzdata 없을 때
        return now_utc


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def has_credentials(env: dict[str, str] | None = None) -> bool:
    """토스 자격증명(client id/secret)이 환경변수 또는 .env 에 있는지."""
    env = os.environ if env is None else env
    pairs = [("TOSS_CLIENT_ID", "API_KEY"), ("TOSS_CLIENT_SECRET", "SECRET_KEY")]
    got = {}
    for primary, alt in pairs:
        got[primary] = (env.get(primary) or env.get(alt) or "").strip()
    if all(got.values()):
        return True
    # .env 파일 폴백(단순 KEY=VALUE 파서)
    dotenv = ROOT / ".env"
    if not dotenv.exists():
        return False
    vals: dict[str, str] = {}
    try:
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return False
    for primary, alt in pairs:
        if not (vals.get(primary) or vals.get(alt)):
            return False
    return True


# ── 단계 실행(격리 + 타임아웃) ────────────────────────────────────────────────
@dataclass
class Step:
    name: str
    argv: Sequence[str] | None = None          # None + skip_reason 이면 스킵 전용
    timeout: float = 120.0
    skip_reason: str | None = None
    env: dict[str, str] | None = None


@dataclass
class StepResult:
    name: str
    status: str                                # "ok" | "failed" | "skipped"
    detail: str = ""
    returncode: int | None = None
    duration_sec: float = 0.0
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    return env


def execute_step(step: Step) -> StepResult:
    """한 단계를 서브프로세스로 실행. 절대 예외를 밖으로 던지지 않는다(격리)."""
    if step.skip_reason is not None or not step.argv:
        return StepResult(step.name, "skipped", detail=step.skip_reason or "no command")
    env = _base_env()
    if step.env:
        env.update(step.env)
    start = _now_utc()
    try:
        proc = subprocess.run(
            list(step.argv), cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=step.timeout, text=True,
        )
    except subprocess.TimeoutExpired as exc:  # 타임아웃도 격리
        dur = (_now_utc() - start).total_seconds()
        return StepResult(step.name, "failed", detail=f"타임아웃 {step.timeout:.0f}s 초과",
                          duration_sec=dur, stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "")
    except Exception as exc:  # noqa: BLE001  실행 자체 실패도 격리
        dur = (_now_utc() - start).total_seconds()
        return StepResult(step.name, "failed", detail=f"{type(exc).__name__}: {exc}",
                          duration_sec=dur)
    dur = (_now_utc() - start).total_seconds()
    status = "ok" if proc.returncode == 0 else "failed"
    detail = "" if status == "ok" else f"rc={proc.returncode}: {_tail(proc.stderr or proc.stdout, 1)}"
    return StepResult(step.name, status, detail=detail, returncode=proc.returncode,
                      duration_sec=dur, stdout=proc.stdout or "", stderr=proc.stderr or "")


def execute_steps(steps: Sequence[Step]) -> list[StepResult]:
    return [execute_step(s) for s in steps]


def _tail(text: str, n: int) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return " ⏎ ".join(lines[-n:])[:300]


# ── 오프라인 캐시 시딩(forward_paper_compare 가 읽는 _candle_cache) ─────────────
def seed_candle_cache(symbols: Sequence[str] = FP_UNIVERSE, depth: int = FP_DEPTH,
                      *, hist_dir: Path = HIST_CACHE, candle_dir: Path = CANDLE_CACHE) -> list[str]:
    """_hist_cache/{SYM}.json → _candle_cache/{SYM}_{depth}.json (없는 것만).

    오프라인 포워드 페이퍼가 캐시된 Nasdaq 일봉으로 돌 수 있도록, 이미 있는 표준 일봉
    캐시(_hist_cache)에서 forward_paper_compare 가 기대하는 레이아웃으로 변환한다.
    네트워크·주문 없음. 반환: 새로 시딩한 심볼 목록.
    """
    seeded: list[str] = []
    candle_dir.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        dst = candle_dir / f"{sym}_{depth}.json"
        if dst.exists():
            continue
        src = hist_dir / f"{sym}.json"
        if not src.exists():
            continue
        try:
            rows = json.loads(src.read_text(encoding="utf-8")).get("rows", []) or []
        except (OSError, ValueError):
            continue
        out = [{"d": r["d"], "o": r["o"], "h": r["h"], "l": r["l"],
                "c": r["c"], "v": r.get("v", 0.0)} for r in rows if "d" in r]
        if not out:
            continue
        tmp = dst.with_suffix(".tmp")
        tmp.write_text(json.dumps(out), encoding="utf-8")
        os.replace(tmp, dst)
        seeded.append(sym)
    return seeded


# ── 크리덴셜 없는 일봉 종가 갱신(refresh_closes) ───────────────────────────────
# 캐시된 Nasdaq 일봉의 마지막 봉이 오래되면(예: 2026-09-22 고정) 포워드 장부가 같은 종가로만
# 계속 마크되어 **영영 전진하지 않는다.** 아래는 장부가 필요로 하는 심볼만, 키 없이, 정중하게
# (짧은 fromdate 창·≥1.5s 간격·429/오류 즉시 중단) 최근 종가를 받아 캐시에 병합한다.
def _default_recent_fetcher(symbol: str, days: int) -> list[dict[str, Any]]:
    """기본 페처: Nasdaq /historical 최근 창(키 불필요·네트워크). 테스트는 주입으로 대체."""
    from toss_trader.histdata import fetch_nasdaq_recent  # 지연 임포트(오프라인 단위테스트 격리)
    return fetch_nasdaq_recent(symbol, days=days, assetclass="etf")


def merge_hist_cache(symbol: str, new_rows: Sequence[dict[str, Any]], *,
                     hist_dir: Path = HIST_CACHE) -> tuple[int, str | None]:
    """새 일봉 rows 를 _hist_cache/{SYM}.json 에 **날짜키로 병합**(멱등). (추가된 수, 마지막 날짜).

    이미 있는 날짜는 기존 봉을 보존한다(과거 배당조정 adjclose 'a' 를 덮어쓰지 않기 위해) —
    즉 새 거래일만 추가한다. 원자적 쓰기. 마크투마켓은 원시 종가 'c' 만 쓰므로 이 병합으로 충분.
    """
    path = hist_dir / f"{symbol}.json"
    payload: dict[str, Any] = {"symbol": symbol, "source": "nasdaq", "rows": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError):
            payload = {"symbol": symbol, "source": "nasdaq", "rows": []}
    by_d: dict[str, dict[str, Any]] = {str(r["d"]): r
                                       for r in (payload.get("rows") or []) if "d" in r}
    added = 0
    for r in new_rows:
        d = r.get("d")
        if d is None:
            continue
        if str(d) not in by_d:          # 새 거래일만 추가(기존 날짜의 'a' 보존)
            by_d[str(d)] = r
            added += 1
    rows = [by_d[k] for k in sorted(by_d)]
    payload["rows"] = rows
    payload["symbol"] = payload.get("symbol") or symbol
    payload["source"] = payload.get("source") or "nasdaq"
    payload["fetched"] = _now_utc().isoformat(timespec="seconds")
    hist_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
    return added, (rows[-1]["d"] if rows else None)


def refresh_candle_cache(symbol: str, *, depth: int = FP_DEPTH,
                         hist_dir: Path = HIST_CACHE,
                         candle_dir: Path = CANDLE_CACHE) -> bool:
    """_hist_cache/{SYM}.json → _candle_cache/{SYM}_{depth}.json **재생성(덮어쓰기)**.

    seed_candle_cache 는 없을 때만 만들지만, 갱신 후에는 새 종가를 반영하도록 덮어써야 한다
    (forward_paper 는 _candle_cache 를 읽으므로). 원자적 쓰기.
    """
    src = hist_dir / f"{symbol}.json"
    if not src.exists():
        return False
    try:
        rows = json.loads(src.read_text(encoding="utf-8")).get("rows", []) or []
    except (OSError, ValueError):
        return False
    out = [{"d": r["d"], "o": r["o"], "h": r["h"], "l": r["l"],
            "c": r["c"], "v": r.get("v", 0.0)} for r in rows if "d" in r]
    if not out:
        return False
    candle_dir.mkdir(parents=True, exist_ok=True)
    dst = candle_dir / f"{symbol}_{depth}.json"
    tmp = dst.with_suffix(".tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, dst)
    return True


def refresh_daily_closes(symbols: Sequence[str] = tuple(BOOK_SYMBOLS), *,
                         days: int = REFRESH_DAYS, spacing: float = REFRESH_SPACING,
                         fetcher: Callable[[str, int], list[dict[str, Any]]] = _default_recent_fetcher,
                         sleep: Callable[[float], None] = _sleep,
                         hist_dir: Path = HIST_CACHE, candle_dir: Path = CANDLE_CACHE,
                         depth: int = FP_DEPTH,
                         candle_symbols: Sequence[str] = tuple(FP_UNIVERSE)) -> dict[str, Any]:
    """장부에 필요한 심볼만 최근 일봉을 키 없이 정중하게 갱신(멱등·격리). 예외를 밖으로 안 던짐.

    - 심볼 사이 ≥``spacing``초 간격(기본 1.6s ≥1.5 준수).
    - 429/네트워크/파싱 오류가 나면 **즉시 중단**(더 두드리지 않음 = 정중). 부분 진행은 그대로 유지.
    - _hist_cache 병합(새 거래일만) + _candle_cache 재생성(forward_paper 유니버스 한정).
    반환 요약: {refreshed, added, last_dates, stopped, requested}.
    """
    summary: dict[str, Any] = {"refreshed": [], "added": {}, "last_dates": {},
                               "stopped": None, "requested": list(symbols)}
    candle_set = set(candle_symbols)
    for i, sym in enumerate(symbols):
        if i > 0:
            try:
                sleep(max(0.0, spacing))
            except Exception:  # noqa: BLE001  sleep 인터럽트도 격리
                pass
        try:
            rows = fetcher(sym, days)
        except Exception as exc:  # noqa: BLE001  429/네트워크/파싱 → 즉시 중단(정중)
            summary["stopped"] = f"{sym}: {type(exc).__name__}: {exc}"
            break
        if not rows:
            continue
        try:
            added, last_d = merge_hist_cache(sym, rows, hist_dir=hist_dir)
            if sym in candle_set:
                refresh_candle_cache(sym, depth=depth, hist_dir=hist_dir, candle_dir=candle_dir)
        except Exception as exc:  # noqa: BLE001  쓰기 실패도 격리 → 중단
            summary["stopped"] = f"{sym}: write {type(exc).__name__}: {exc}"
            break
        summary["refreshed"].append(sym)
        summary["added"][sym] = added
        summary["last_dates"][sym] = last_d
    return summary


# ── 포워드 장부 추출(상태 JSON → 장부별 지분/낙폭) ─────────────────────────────
def equity_drawdown(equities: Sequence[float]) -> tuple[float, float, float]:
    """(최신 지분, 최고점, 최대낙폭). 원지분(raw equity) 기준. 2점 미만이면 낙폭 0."""
    vals = [float(e) for e in equities if e is not None]
    if not vals:
        return (0.0, 0.0, 0.0)
    peak = -float("inf")
    mdd = 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return (vals[-1], max(vals), mdd)


def _book(bid: str, equities: Sequence[float], contributed: float,
          n: int, source: str) -> dict[str, Any]:
    latest, _peak, mdd = equity_drawdown(equities)
    money_return = (latest / contributed - 1.0) if contributed > 0 else None
    return {
        "id": bid, "label": BOOK_LABELS.get(bid, bid), "source": source,
        "equity": latest, "contributed": contributed,
        "money_return": money_return, "max_dd": mdd, "n": n,
    }


def forward_paper_books(state: dict[str, Any], *, source: str = "cached") -> list[dict[str, Any]]:
    """forward_paper_state.json → 전략별 장부 리스트."""
    if not isinstance(state, dict):
        return []
    snaps = state.get("snapshots") or []
    meta = state.get("strategies") or {}
    ids: list[str] = []
    for snap in snaps:
        for sid in (snap.get("strategies") or {}):
            if sid not in ids:
                ids.append(sid)
    books: list[dict[str, Any]] = []
    for sid in ids:
        eq = [_f((snap.get("strategies") or {}).get(sid, {}).get("equity"))
              for snap in snaps if sid in (snap.get("strategies") or {})]
        contributed = _f((meta.get(sid) or {}).get("total_contributed")) or \
            _f((meta.get(sid) or {}).get("initial_equity")) or _f(state.get("seed_usd"))
        books.append(_book(sid, eq, contributed, len(eq), source))
    return books


def forward_lifecycle_books(state: dict[str, Any], *, source: str = "cached") -> list[dict[str, Any]]:
    """forward_lifecycle_state.json → 포트폴리오별 장부 리스트."""
    if not isinstance(state, dict):
        return []
    snaps = state.get("snapshots") or []
    ids: list[str] = []
    for snap in snaps:
        for pid in (snap.get("portfolios") or {}):
            if pid not in ids:
                ids.append(pid)
    books: list[dict[str, Any]] = []
    for pid in ids:
        eq = [_f((snap.get("portfolios") or {}).get(pid, {}).get("equity"))
              for snap in snaps if pid in (snap.get("portfolios") or {})]
        contributed = 0.0
        for snap in snaps:
            row = (snap.get("portfolios") or {}).get(pid)
            if row and row.get("contributed") is not None:
                contributed = _f(row.get("contributed"))
        books.append(_book(pid, eq, contributed, len(eq), source))
    return books


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ── 인트라데이 섀도 규칙 통계(trades.jsonl → 규칙별 n/mean bps/t/status) ────────
def _status(n: int, tstat: float) -> str:
    if n < N_MIN:
        return "수집 중 (n<200)"
    if tstat >= T_MIN:
        return "후보 (t≥3)"
    return "기각"


def intraday_rule_stats(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """가상 트레이드 레코드 → 베이스 크기 기준 규칙별 통계 + 세션 날짜 요약."""
    records = list(records)
    dates = sorted({str(r.get("date")) for r in records if r.get("date")})
    # 베이스 크기 = 레코드에 등장하는 가장 작은 숫자 크기 키(없으면 폴백).
    size_keys: set[str] = set()
    for r in records:
        size_keys.update((r.get("sizes") or {}).keys())
    base = None
    for k in size_keys:
        try:
            kv = float(k)
        except (TypeError, ValueError):
            continue
        if base is None or kv < float(base):
            base = k
    size = base or BASE_SIZE_FALLBACK
    rows: list[dict[str, Any]] = []
    for rule in RULE_ORDER:
        pnls = [(_f((r.get("sizes") or {}).get(size, {}).get("net")))
                for r in records if r.get("rule") == rule and size in (r.get("sizes") or {})]
        bps = [(_f((r.get("sizes") or {}).get(size, {}).get("net_bps")))
               for r in records if r.get("rule") == rule and size in (r.get("sizes") or {})]
        n = len(pnls)
        mean_bps = sum(bps) / n if n else 0.0
        tstat = 0.0
        if n >= 2:
            sd = statistics.stdev(pnls)
            if sd > 0:
                tstat = statistics.mean(pnls) / (sd / (n ** 0.5))
        rows.append({"label": RULE_LABEL[rule], "n": n, "mean_bps": mean_bps,
                     "tstat": tstat, "status": _status(n, tstat)})
    return {"size": size, "dates": dates, "total": len(records), "rules": rows}


def read_trades(path: Path = SHADOW_TRADES) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out


# ── DCA 플랜 / 양도세 stdout 파싱(있을 때만) ──────────────────────────────────
def parse_dca_plan(stdout: str) -> list[str]:
    """DCA dry-run stdout 에서 플랜/분할/경고 라인만 추린다."""
    keep: list[str] = []
    for raw in (stdout or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if any(tok in s for tok in ("적립 매수 플랜", "분할매수", "매수 미달분",
                                    "매수가능", "⚠️", "FX", "환율", "ℹ️")):
            # run_dca 로그는 앞에 UTC 타임스탬프가 붙는다 → 제거해 간결히.
            parts = s.split(" ", 1)
            keep.append(parts[1] if len(parts) == 2 and _looks_ts(parts[0]) else s)
    return keep[-8:]


def parse_tax(stdout: str) -> list[str]:
    keep: list[str] = []
    for raw in (stdout or "").splitlines():
        s = raw.strip()
        if any(tok in s for tok in ("실현손익", "남은 기본공제", "미실현 ₩", "하베스팅", "예상세액")):
            keep.append(s)
    return keep[:6]


def _looks_ts(tok: str) -> bool:
    return len(tok) >= 19 and tok[:4].isdigit() and tok[4:5] == "-"


# ── 대시보드 렌더링(순수 함수) ────────────────────────────────────────────────
def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def render_dashboard(ctx: dict[str, Any]) -> str:
    steps: list[StepResult] = ctx["steps"]
    books: list[dict[str, Any]] = ctx.get("books", [])
    intraday: dict[str, Any] = ctx.get("intraday", {})
    dca: list[str] = ctx.get("dca_plan", [])
    tax: list[str] = ctx.get("tax", [])
    n_ok = sum(1 for s in steps if s.status == "ok")
    n_fail = sum(1 for s in steps if s.status == "failed")
    n_skip = sum(1 for s in steps if s.status == "skipped")
    changelog = ctx.get("changelog", "")

    L: list[str] = [
        "# 매일 자동 스코어보드",
        "",
        f"- 생성: `{ctx.get('generated')}`  ·  모드: **{ctx.get('mode')}**  ·  "
        f"ET 세션: `{ctx.get('et')}`",
        "- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).",
        f"- 단계: ok {n_ok} / 실패 {n_fail} / 스킵 {n_skip}.",
        f"- 변경 이력: `{changelog}`",
        "",
        "## 단계 상태",
        "",
        "| 단계 | 상태 | 소요 | 비고 |",
        "|---|---|---:|---|",
    ]
    icon = {"ok": "✅", "failed": "❌", "skipped": "⏭"}
    for s in steps:
        L.append(f"| {s.name} | {icon.get(s.status, '?')} {s.status} | "
                 f"{s.duration_sec:.1f}s | {s.detail or ''} |")

    L += ["", "## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)", ""]
    if books:
        L += ["| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |",
              "|---|---|---:|---:|---:|---:|---:|"]
        for b in books:
            L.append(
                f"| {b['label']} | {b['source']} | ${b['equity']:.2f} | "
                f"${b['contributed']:.2f} | {_fmt_pct(b['money_return'])} | "
                f"{_fmt_pct(b['max_dd'])} | {b['n']} |")
    else:
        L.append("- (포워드 장부 상태 없음 — 이 단계가 실패했거나 아직 초기화 전.)")

    ish = intraday.get("rules") if intraday else None
    L += ["", f"## 인트라데이 섀도 (규칙별, 포지션 ${intraday.get('size', BASE_SIZE_FALLBACK)})", ""]
    if intraday:
        dts = intraday.get("dates") or []
        span = f"{dts[0]}~{dts[-1]}" if dts else "없음"
        L.append(f"- 누적 가상 트레이드 {intraday.get('total', 0)}건 · 세션 날짜 {len(dts)}개 ({span}).")
    if ish:
        L += ["", "| 규칙 | n | 평균 net bps | t-stat | 상태 |",
              "|---|---:|---:|---:|---|"]
        for r in ish:
            L.append(f"| {r['label']} | {r['n']} | {r['mean_bps']:.2f} | "
                     f"{r['tstat']:.2f} | {r['status']} |")
    else:
        L.append("- (섀도 통계 없음.)")

    L += ["", "## 오늘의 DCA 플랜 (dry-run, 주문 없음)", ""]
    if dca:
        L += [f"- {ln}" for ln in dca]
    else:
        L.append("- 자격증명 없음 또는 단계 스킵 → 플랜 생략(오프라인 모드에선 정상).")

    L += ["", "## 양도세 (YTD)", ""]
    if tax:
        L += [f"- {ln}" for ln in tax]
    else:
        L.append("- 자격증명/픽스처 없음 → 양도세 리포트 생략.")

    L += ["", "## 주의", "",
          "- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).",
          "- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).",
          "- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.", ""]
    return "\n".join(L)


# ── 변경 이력(JSONL, 날짜 기준 upsert = 멱등) ─────────────────────────────────
def read_history(path: Path = HISTORY_FILE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out


def upsert_history(record: dict[str, Any], path: Path = HISTORY_FILE) -> list[dict[str, Any]]:
    """같은 'date' 레코드는 교체(멱등). 파일을 원자적으로 다시 쓴다."""
    key = str(record.get("date"))
    rows = [r for r in read_history(path) if str(r.get("date")) != key]
    rows.append(record)
    rows.sort(key=lambda r: str(r.get("date")))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
    return rows


# ── 미국 증시(NYSE/Nasdaq) 정규장 종일 휴장일 ─────────────────────────────────
# 06:30 KST 실행 시 ET 는 전일 16:30(EST)/17:30(EDT). 그 ET 날짜가 '방금 끝난' 세션인데,
# 주말/휴장이면 새 세션이 없으므로 인트라데이 수집을 스킵해야 한다(Nasdaq /chart 는 무조건
# '가장 최근 세션'을 돌려주므로, 휴장일에 돌리면 직전 세션을 날짜만 오귀속해 재수집한다).
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """그 달의 n번째 특정 요일(weekday: 월=0 … 일=6)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """그 달의 마지막 특정 요일."""
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """고정 공휴일의 관측일(NYSE 규칙): 토→전날 금, 일→다음날 월."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _easter_sunday(year: int) -> date:
    """부활절 일요일(Anonymous Gregorian algorithm). Good Friday = 이 날 − 2일."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_market_holidays(year: int) -> set[date]:
    """해당 연도 미국 증시 정규장 종일 휴장일(관측일·Good Friday 반영)."""
    return {
        _observed(date(year, 1, 1)),       # New Year's Day
        _nth_weekday(year, 1, 0, 3),        # MLK Day (1월 셋째 월)
        _nth_weekday(year, 2, 0, 3),        # Washington's Birthday (2월 셋째 월)
        _easter_sunday(year) - timedelta(days=2),   # Good Friday
        _last_weekday(year, 5, 0),          # Memorial Day (5월 마지막 월)
        _observed(date(year, 6, 19)),       # Juneteenth (2021+)
        _observed(date(year, 7, 4)),        # Independence Day
        _nth_weekday(year, 9, 0, 1),        # Labor Day (9월 첫째 월)
        _nth_weekday(year, 11, 3, 4),       # Thanksgiving (11월 넷째 목)
        _observed(date(year, 12, 25)),      # Christmas
    }


def is_us_market_holiday(d: date) -> bool:
    return d in us_market_holidays(d.year)


def et_session_date(now_utc: datetime) -> date | None:
    """실행 시점(UTC)에 '방금 끝난' ET 정규장 세션 날짜. 없으면 None.

    06:30 KST 실행 → ET 는 전일 16:30(EST)/17:30(EDT) → 그 ET 날짜가 방금 끝난 세션이다.
    정규장 마감(16:05 ET) 전·주말·미국 증시 휴장이면 None(수집할 새 세션 없음).
    """
    et = _et(now_utc)
    if et.time() < ET_CLOSE:
        return None
    d = et.date()
    if d.weekday() >= 5 or is_us_market_holiday(d):
        return None
    return d


# ── 기본 단계 구성 ────────────────────────────────────────────────────────────
def collector_skip_reason(offline: bool, et_now: datetime) -> str | None:
    """인트라데이 수집기를 스킵할 사유(없으면 None → 실행). et_now 는 이미 ET 로 변환된 시각."""
    if offline:
        return "offline 모드(네트워크 수집 생략)"
    if et_now.weekday() >= 5:
        return f"주말(ET {et_now:%a}) — 새 세션 없음"
    if et_now.time() < ET_CLOSE:
        return f"ET {et_now:%H:%M} < 16:05 (정규장 마감 전)"
    if is_us_market_holiday(et_now.date()):
        return f"미국 증시 휴장(ET {et_now.date()}) — 새 세션 없음"
    return None


def build_default_steps(*, offline: bool, creds: bool, et_now: datetime,
                        seed_usd: float, py: str) -> list[Step]:
    off = ["--offline-cache"] if (offline or not creds) else []
    steps = [
        Step("intraday_collect",
             [py, str(SCRIPTS / "collect_intraday.py"),
              "--source", "nasdaq", "--intervals", "1m,5m", "--movers", "12"],
             timeout=300.0, skip_reason=collector_skip_reason(offline, et_now)),
        Step("intraday_shadow",
             [py, str(SCRIPTS / "intraday_shadow_run.py")], timeout=180.0),
        # 크리덴셜 없는 경로에서만: 장부 심볼의 최근 일봉을 키 없이 정중히 갱신(캐시 병합).
        # 반드시 forward_* 단계보다 먼저 — 그래야 장부가 새 종가로 전진한다. 격리(실패해도 계속).
        Step("refresh_closes",
             [py, str(SCRIPTS / "daily_scoreboard.py"), "--refresh-closes"],
             timeout=120.0,
             skip_reason=(None if (offline or not creds)
                          else "live 모드(Toss 실시세) — 캐시 갱신 불필요")),
        Step("forward_paper",
             [py, str(SCRIPTS / "forward_paper_compare.py"),
              *off, "--cash-usd", f"{seed_usd:.2f}"], timeout=180.0),
        Step("forward_lifecycle",
             [py, str(SCRIPTS / "forward_lifecycle_paper.py"),
              *off, "--cash-usd", f"{seed_usd:.2f}"], timeout=180.0),
        Step("dca_plan",
             [py, str(SCRIPTS / "run_dca.py")], timeout=120.0,
             skip_reason=None if creds else "자격증명 없음(플랜 생략)"),
        Step("tax_report",
             [py, str(SCRIPTS / "run_dca.py"), "--tax-report"], timeout=120.0,
             skip_reason=None if creds else "자격증명 없음(양도세 생략)"),
        # 공격형 포워드 페이퍼 랩(Lane A) — 키 불필요·멱등·주문 없음. 자체적으로 캐시 종가로
        # 매 새 거래일을 전진시키고 reports/paperlab_latest.md 를 쓴다(offline 이면 네트워크 없이).
        Step("paperlab",
             [py, str(SCRIPTS / "paperlab_run.py"), *(["--offline"] if offline else [])],
             timeout=240.0),
    ]
    return steps


# ── 오케스트레이션 ────────────────────────────────────────────────────────────
def run_scoreboard(*, offline: bool = False, now_utc: datetime | None = None,
                   seed_usd: float = 100.0,
                   steps: Sequence[Step] | None = None,
                   report_path: Path = LATEST_REPORT,
                   history_path: Path = HISTORY_FILE,
                   fp_state: Path = FP_STATE, lc_state: Path = LC_STATE,
                   trades_path: Path = SHADOW_TRADES,
                   creds: bool | None = None) -> dict[str, Any]:
    now_utc = now_utc or _now_utc()
    et_now = _et(now_utc)
    creds = has_credentials() if creds is None else creds
    mode = "live(실시세)" if (creds and not offline) else "offline(캐시)"
    source = "live" if (creds and not offline) else "cached"

    # 오프라인 포워드 페이퍼가 캐시로 돌 수 있도록 일봉 캐시 시딩(멱등).
    if offline or not creds:
        try:
            seed_candle_cache()
        except Exception:  # noqa: BLE001  시딩 실패는 격리(해당 단계에서 드러남)
            pass

    py = sys.executable or "python3"
    if steps is None:
        steps = build_default_steps(offline=offline, creds=creds, et_now=et_now,
                                    seed_usd=seed_usd, py=py)
    results = execute_steps(steps)
    by_name = {r.name: r for r in results}

    # 포워드 장부(상태 파일에서 직접 추출 — 단계 실패와 무관하게 있으면 표시).
    books = forward_paper_books(_load_json(fp_state), source=source) + \
        forward_lifecycle_books(_load_json(lc_state), source=source)

    intraday = intraday_rule_stats(read_trades(trades_path))

    dca = parse_dca_plan(by_name["dca_plan"].stdout) if "dca_plan" in by_name and \
        by_name["dca_plan"].ok else []
    tax = parse_tax(by_name["tax_report"].stdout) if "tax_report" in by_name and \
        by_name["tax_report"].ok else []

    n_ok = sum(1 for r in results if r.status == "ok")
    n_fail = sum(1 for r in results if r.status == "failed")
    n_skip = sum(1 for r in results if r.status == "skipped")
    generated = now_utc.astimezone(KST).isoformat(timespec="seconds")
    changelog = (f"{generated} | steps ok={n_ok}/실패={n_fail}/스킵={n_skip} | "
                 f"books={len(books)} | shadow_trades={intraday['total']} | mode={mode}")

    ctx = {
        "generated": generated, "mode": mode, "et": et_now.isoformat(timespec="minutes"),
        "steps": results, "books": books, "intraday": intraday,
        "dca_plan": dca, "tax": tax, "changelog": changelog,
    }
    report = render_dashboard(ctx)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    record = {
        "date": et_now.date().isoformat(),        # ET 세션 날짜(멱등 키)
        "generated": generated, "mode": mode,
        "steps": {r.name: r.status for r in results},
        "books": [{"id": b["id"], "equity": round(b["equity"], 4),
                   "contributed": round(b["contributed"], 4),
                   "money_return": b["money_return"], "max_dd": b["max_dd"]}
                  for b in books],
        "shadow_trades": intraday["total"],
        "shadow_dates": len(intraday["dates"]),
        "dca_available": bool(dca), "tax_available": bool(tax),
        "changelog": changelog,
    }
    upsert_history(record, history_path)

    return {"report": str(report_path), "history": str(history_path),
            "ok": n_ok, "failed": n_fail, "skipped": n_skip,
            "books": len(books), "changelog": changelog, "report_text": report}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="매일 자동 스코어보드(읽기 전용·멱등·주문 없음).")
    ap.add_argument("--offline", action="store_true",
                    help="네트워크·토스 API 없이 캐시만으로 실행(로컬 검증/CI).")
    ap.add_argument("--seed-usd", type=float, default=100.0,
                    help="포워드 장부 최초 초기화 시드(USD). 기존 상태가 있으면 무시.")
    ap.add_argument("--status", action="store_true",
                    help="실행하지 않고 최신 대시보드만 출력.")
    ap.add_argument("--refresh-closes", action="store_true",
                    help="장부 심볼의 최근 일봉만 키 없이 정중히 갱신(캐시 병합)하고 종료(격리 단계용).")
    args = ap.parse_args(argv)

    if args.refresh_closes:
        summary = refresh_daily_closes()
        print(json.dumps(summary, ensure_ascii=False))
        if summary.get("stopped") and not summary.get("refreshed"):
            print(f"refresh_closes stopped: {summary['stopped']}", file=sys.stderr)
            return 1                                   # 아무것도 못 갱신하고 중단 → 실패로 표시
        return 0

    if args.status:
        if LATEST_REPORT.exists():
            print(LATEST_REPORT.read_text(encoding="utf-8"))
            return 0
        print("(아직 스코어보드가 생성되지 않았습니다. 먼저 인자 없이 실행하세요.)")
        return 1

    res = run_scoreboard(offline=args.offline, seed_usd=args.seed_usd)
    print(json.dumps({k: v for k, v in res.items() if k != "report_text"},
                     ensure_ascii=False))
    print(f"→ {res['report']}")
    # 기록은 맥에 쌓지 않고 GitHub paper-log 브랜치로(허용 목록만·비밀 검사). 실패해도 스코어보드는 성공.
    if not args.offline:
        pub = execute_step(Step("publish_records", [sys.executable or "python3",
                                                    str(SCRIPTS / "publish_records.py")],
                                timeout=180.0))
        print(f"publish_records: {pub.status} {pub.stdout.strip()[-200:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
