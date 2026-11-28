#!/usr/bin/env python3
"""공격형 포워드 페이퍼 랩 러너 — 모든 전략을 키 없는 일봉 종가로 매 새 거래일마다 전진(멱등·주문 없음).

동작:
  1) 필요한 심볼을 캐시(data/_hist_cache)에서 로드(조정종가). 코어 심볼(QQQ/TQQQ/SQQQ)이 없으면
     1회 전체 히스토리 페치(폴백; 429면 즉시 중단). 포워드 갱신은 Nasdaq 최근창으로 정중히(≥1.5s,
     429 즉시 중단) 코어 심볼만 갱신한다.
  2) 각 전략 상태를 data/paperlab/{name}/ 에서 로드/초기화 → 마지막 처리일 이후 새 거래일만 전진.
  3) reports/paperlab_latest.md 리더보드 작성.

옵션:
  --backfill-from YYYY-MM-DD  과거 재현(BACKTEST) 별도 리포트 reports/paperlab_backfill.md 작성(페이퍼 아님).
  --reset                     모든 페이퍼 장부 재초기화(시작일 = START_DATE).
  --no-refresh                포워드 코어 심볼 네트워크 갱신 생략(캐시만).
  --offline                   네트워크 전면 금지(캐시만; 누락 코어 심볼은 그냥 건너뜀).
  --refresh-universe-only     전략 유니버스(hibeta→mom→swing) 종가만 정중히 갱신하고 종료(장부 전진 없음).

사용:
  PYTHONPATH=src python scripts/paperlab_run.py                       # 포워드 전진 + 리더보드
  PYTHONPATH=src python scripts/paperlab_run.py --backfill-from 2021-01-01 --offline
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import histdata                                   # noqa: E402
from toss_trader.models import Candle                              # noqa: E402
from toss_trader.paperlab import (PaperLab, load_state, render_backfill,  # noqa: E402
                                  render_leaderboard, save_state)
from toss_trader.paperlab_strategies import build_roster          # noqa: E402
from toss_trader.paperlab_strategies._universes import ETF_LIKE, NDX_100  # noqa: E402
from toss_trader.paperlab_strategies.hibeta_universe import HIBETA_CANDIDATES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAPERLAB_DIR = DATA / "paperlab"
HIST_CACHE = DATA / "_hist_cache"
REPORTS = ROOT / "reports"
LEADERBOARD = REPORTS / "paperlab_latest.md"
BACKFILL_REPORT = REPORTS / "paperlab_backfill.md"

START_DATE = date(2026, 9, 28)        # 페이퍼 시작일(오늘의 다음 거래일; 부록 v3 목표 변경 시점)
CORE_SYMBOLS = ["QQQ", "TQQQ", "SQQQ"]   # 캐시에 없으면 1회 전체 히스토리 페치(폴백)
REFRESH_SPACING = 1.6                  # 정중 폴링(≥1.5s)
REFRESH_DAYS = 14
MAX_REFRESH = 40                       # 포워드 갱신 심볼 상한(유계 유지; ≥1.5s 간격·429 즉시 중단)
# 유니버스 갱신(별도 단계 `--refresh-universe-only`) 상한(결정 2026-11-28 D6/F15). paperlab 단계
# 타임아웃(240s)을 넘지 않도록 코어 갱신과 분리한다.
MAX_UNIVERSE_REFRESH = 300
# 스윙 유니버스(단일종목)에서 뺄 ETF/레버리지 ETP — ETF_LIKE 가 못 덮는 R4 누출분
# (reports/2026-11-28_hot_rvol_scan.md "ETF 누출").
SWING_EXCLUDE = frozenset({
    "BITX", "BOXX", "BSV", "CONL", "CURE", "DBB", "DRN", "FAS", "HIBL", "LABU", "MSTU", "PSQ",
    "SCZ", "SGOV", "SH", "SPXL", "SVIX", "TECL", "TECS", "TIP", "TNA", "UUP", "VIXM", "VIXY", "VWO",
})

# 포워드 갱신 대상 ETP(신규 전략이 쓰는 레버리지/1x/현금성). assetclass="etf".
REFRESH_ETPS = ["QQQ", "TQQQ", "SQQQ", "SPY", "SPXL", "TECL", "SOXL", "UVXY", "BSV",
                "SMH", "BIL", "SHY", "TMF", "AGG", "PSQ", "IBIT"]
# 포워드 갱신 대상 단일주(btc_proxy + hibeta 상위 몇). assetclass="stocks".
REFRESH_STOCKS_CORE = ["MSTR", "COIN"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    print(f"{_now()} {msg}", flush=True)


def _code_version() -> str:
    """실행 코드 체크아웃의 git 짧은 SHA@브랜치(감사 2026-11-28 F1). 실패 시 'unknown'."""
    try:
        sha, branch = (subprocess.run(["git", "-C", str(ROOT), "rev-parse", *a, "HEAD"],
                                      capture_output=True, text=True, timeout=5, check=True).stdout.strip()
                       for a in (["--short"], ["--abbrev-ref"]))
        return f"{sha}@{branch}"
    except Exception:  # noqa: BLE001
        return "unknown"


# ─────────────────────────────────────────── 데이터 로딩(캐시 우선, 코어만 네트워크)
def _cache_file(symbol: str) -> Path:
    safe = symbol.replace("^", "_").replace("/", "_")
    return HIST_CACHE / f"{safe}.json"


def _load_from_cache(symbol: str) -> list[Candle] | None:
    if not _cache_file(symbol).exists():
        return None
    try:
        return histdata.load_symbol(symbol, adjusted=True)      # 캐시 존재 → 네트워크 없음
    except Exception as exc:  # noqa: BLE001
        _log(f"cache load failed {symbol}: {type(exc).__name__}: {exc}")
        return None


def _fetch_full(symbol: str, *, force: bool = False) -> list[Candle] | None:
    """전체 히스토리 페치(Yahoo→Nasdaq→Stooq). force=False 면 캐시가 있으면 그대로(누락 코어 1회용),
    force=True 면 캐시를 새 전체 히스토리로 교체(캐시 구멍 메우기). 429/실패면 None."""
    try:
        return histdata.load_symbol(symbol, adjusted=True, force=force)
    except Exception as exc:  # noqa: BLE001
        _log(f"full fetch failed {symbol}: {type(exc).__name__}: {exc}")
        return None


def _cache_last_date(symbol: str) -> date | None:
    """캐시 rows 의 마지막 날짜(없거나 읽기 실패면 None)."""
    import json
    try:
        rows = json.loads(_cache_file(symbol).read_text(encoding="utf-8")).get("rows") or []
        return date.fromisoformat(str(rows[-1]["d"])[:10]) if rows else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _polite_refresh(pairs: Sequence[tuple[str, str]], *,
                    client: Any = None, cutoff: Any = None,
                    sleep: Callable[[float], None] = time.sleep,
                    gap_fill: bool = False, stats: dict[str, int] | None = None) -> str | None:
    """(symbol, assetclass) 목록의 최근 종가만 정중히 갱신(새 거래일만 병합).

    라이브(client!=None)면 **토스 일봉 1차 + Nasdaq 폴백**, 아니면 Nasdaq 전용. 회귀: Nasdaq
    /historical 이 최신 완료 세션을 며칠 늦게 실어(2026-09-29 관측) 캐시가 정체 → 페이퍼 랩이
    첫 세션(2026-09-28)에서 못 벗어남. 토스는 완료 세션을 당일 저녁에 이미 제공(``cutoff`` 초과
    당일 미완료 캔들은 제외). ≥1.5s 간격, Nasdaq 폴백까지 실패하면 즉시 중단(정중).
    토스 429/403(재시도 소진 포함)이 한 번이라도 나면 남은 심볼은 Nasdaq 전용(토스 추가 호출 없음).
    ``gap_fill``: 캐시 마지막 봉이 최근 창(REFRESH_DAYS)보다 오래됐으면 증분 대신 전체 재페치
    (영구 중간 구멍 방지; 실패하면 증분 없이 건너뛰어 다음 실행에서 재시도). ``stats``: done/empty/full/full_failed 집계.
    """
    import json
    st = stats if stats is not None else {}
    st.update(done=0, empty=0, full=0, full_failed=0)
    ref = cutoff if isinstance(cutoff, date) else date.today()
    for i, (sym, assetclass) in enumerate(pairs):
        if i > 0:
            sleep(REFRESH_SPACING)
        st["done"] = i + 1
        if gap_fill:
            last = _cache_last_date(sym)
            if last is not None and (ref - last).days > REFRESH_DAYS:
                if _fetch_full(sym, force=True) is not None:
                    st["full"] += 1
                    continue
                st["full_failed"] += 1
                continue        # 증분 금지: 최근 창만 붙이면 영구 중간 구멍 → 다음 실행에서 전체 재시도
        rows: list[dict] = []
        if client is not None:
            try:
                rows = histdata.fetch_toss_recent(sym, days=REFRESH_DAYS, client=client, cutoff=cutoff)
            except Exception as exc:  # noqa: BLE001  토스 실패 → Nasdaq 폴백(여기선 중단 안 함)
                code = getattr(exc, "http_status", None)
                if code in (429, 403):
                    _log(f"toss {code} at {sym} → nasdaq-only for remaining {len(pairs) - i} symbols")
                    client = None
                rows = []
        if not rows:
            try:
                rows = histdata.fetch_nasdaq_recent(sym, days=REFRESH_DAYS, assetclass=assetclass)
            except Exception as exc:  # noqa: BLE001  429/네트워크/파싱 → 중단(정중)
                return f"{sym}: {type(exc).__name__}: {exc}"
        if not rows:
            st["empty"] += 1
            continue
        path = _cache_file(sym)
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else \
                {"symbol": sym, "source": "nasdaq", "rows": []}
            by_d = {str(r["d"]): r for r in (payload.get("rows") or []) if "d" in r}
            for r in rows:
                if str(r.get("d")) not in by_d:                 # 새 거래일만 추가
                    by_d[str(r["d"])] = r
            payload["rows"] = [by_d[k] for k in sorted(by_d)]
            payload["fetched"] = _now()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            import os
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            return f"{sym}: write {type(exc).__name__}: {exc}"
    return None


def _load_panel(symbols: Sequence[str], *, offline: bool) -> dict[str, list[Candle]]:
    panel: dict[str, list[Candle]] = {}
    for sym in symbols:
        candles = _load_from_cache(sym)
        if candles is None and sym in CORE_SYMBOLS and not offline:
            _log(f"core symbol {sym} missing from cache → fetching full history once")
            candles = _fetch_full(sym)
        if candles:
            panel[sym] = candles
    return panel


def _by_date(panel: Mapping[str, list[Candle]]) -> dict[str, dict[date, Candle]]:
    return {sym: {c.dt: c for c in candles} for sym, candles in panel.items()}


def _master_dates(panel: Mapping[str, list[Candle]], *, ref: str = "QQQ") -> list[date]:
    if ref in panel and panel[ref]:
        return [c.dt for c in panel[ref]]
    all_dates: set[date] = set()
    for candles in panel.values():
        all_dates.update(c.dt for c in candles)
    return sorted(all_dates)


def _bench_closes(panel: Mapping[str, list[Candle]]) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    for sym in ("QQQ", "TQQQ"):
        if sym in panel:
            out[sym] = {c.dt: c.close for c in panel[sym]}
    return out


# ─────────────────────────────────────────── 로스터 구성(데이터 인지 유니버스 주입)
def _cached_universe() -> list[str]:
    """캐시된 단일종목 유니버스(hot_rvol_swing 스캔용). ETF/레버리지 제외, FRED/지수 제외."""
    syms: list[str] = []
    for p in sorted(HIST_CACHE.glob("*.json")):
        name = p.stem
        if name.startswith("_") or name in ETF_LIKE or name in SWING_EXCLUDE:
            continue
        if name.isupper() or name.replace(".", "").isalpha():
            syms.append(name)
    return syms


def _build(swing_universe: Sequence[str] | None, mom_universe: Sequence[str] | None,
           hibeta_universe: Sequence[str] | None = None):
    return build_roster(swing_universe=swing_universe, mom_universe=mom_universe,
                        hibeta_universe=hibeta_universe)


def _hibeta_cached() -> list[str]:
    """hibeta 후보 ∩ 캐시(포워드 = 생존편향 없음). 캐시 없는 이름은 자동 제외."""
    return [s for s in HIBETA_CANDIDATES if _cache_file(s).exists()]


def _refresh_pairs(hibeta_uni: Sequence[str]) -> list[tuple[str, str]]:
    """포워드 갱신 (symbol, assetclass) 목록. ETP(etf) + MSTR/COIN + hibeta 단일주(stocks). MAX_REFRESH 상한."""
    pairs: list[tuple[str, str]] = [(s, "etf") for s in REFRESH_ETPS if _cache_file(s).exists()]
    seen = {s for s, _ in pairs}
    for s in list(REFRESH_STOCKS_CORE) + list(hibeta_uni):
        if s not in seen and _cache_file(s).exists():
            pairs.append((s, "stocks"))
            seen.add(s)
    return pairs[:MAX_REFRESH]


def _universe_refresh_pairs(hibeta_uni: Sequence[str], mom_uni: Sequence[str],
                            swing_uni: Sequence[str]) -> list[tuple[str, str]]:
    """유니버스 갱신 (symbol, "stocks") 목록. hibeta → mom → swing 순, 중복·코어(_refresh_pairs) 제외.
    MAX_UNIVERSE_REFRESH 상한. 입력 유니버스는 이미 캐시 존재 기준으로 걸러진 것."""
    seen = {s for s, _ in _refresh_pairs(hibeta_uni)}
    pairs: list[tuple[str, str]] = []
    for s in [*hibeta_uni, *mom_uni, *swing_uni]:
        if s not in seen:
            pairs.append((s, "stocks"))
            seen.add(s)
    return pairs[:MAX_UNIVERSE_REFRESH]


def _live_client_cutoff() -> tuple[Any, Any]:
    """라이브면 토스 1차(Nasdaq 지연 우회)용 (client, cutoff). 실패하면 (None, None) → Nasdaq 전용."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import daily_scoreboard as _sb       # noqa: PLC0415  (scripts/daily_scoreboard.py)
        client = _sb._toss_client_or_none()
        cutoff = _sb.latest_completed_us_session(client=client) if client is not None else None
        return client, cutoff
    except Exception:  # noqa: BLE001
        return None, None


# ─────────────────────────────────────────── 실행: 유니버스 갱신 전용(장부 전진 없음)
def run_universe_refresh(args: argparse.Namespace) -> int:
    if args.offline:
        _log("universe refresh: offline → skipped")
        return 0
    hibeta_uni = _hibeta_cached()
    mom_uni = [s for s in NDX_100 if _cache_file(s).exists()]
    pairs = _universe_refresh_pairs(hibeta_uni, mom_uni, _cached_universe())
    client, cutoff = _live_client_cutoff()
    st: dict[str, int] = {}
    stopped = _polite_refresh(pairs, client=client, cutoff=cutoff, gap_fill=True, stats=st)
    remaining = len(pairs) - st.get("done", 0) + (1 if stopped else 0)   # 중단 심볼 포함 미처리 꼬리
    _log(f"universe refresh: symbols={len(pairs)} "
         f"source={'toss+nasdaq' if client is not None else 'nasdaq'} empty={st.get('empty', 0)} "
         f"gap_full={st.get('full', 0)} gap_full_failed={st.get('full_failed', 0)} "
         f"stopped={stopped} remaining={remaining}")
    return 0


# ─────────────────────────────────────────── 실행: 포워드 페이퍼
def run_forward(args: argparse.Namespace) -> int:
    swing_uni = _cached_universe()
    mom_uni = [s for s in NDX_100 if _cache_file(s).exists()]
    hibeta_uni = _hibeta_cached()
    roster = _build(swing_uni, mom_uni, hibeta_uni)

    needed: set[str] = {"QQQ", "TQQQ"}
    for strat in roster:
        needed.update(strat.universe())

    if not args.offline and not args.no_refresh:
        pairs = _refresh_pairs(hibeta_uni)
        client, cutoff = _live_client_cutoff()
        stopped = _polite_refresh(pairs, client=client, cutoff=cutoff)
        _log(f"refresh closes: symbols={len(pairs)} "
             f"source={'toss+nasdaq' if client is not None else 'nasdaq'} stopped={stopped}")

    panel = _load_panel(sorted(needed), offline=args.offline)
    if "QQQ" not in panel:
        _log("FATAL: QQQ cache missing; cannot run.")
        return 1
    by_date = _by_date(panel)
    master = _master_dates(panel)     # 전체 날짜(워밍업 포함); 엔진이 START_DATE 이전은 신호용으로만 사용

    summaries: list[dict[str, Any]] = []
    for strat in roster:
        lab = PaperLab(strat)
        state = None if args.reset else load_state(PAPERLAB_DIR, strat.name)
        if state is None:
            state = lab.fresh_state(START_DATE)
        state = lab.run(state, master, by_date)
        save_state(PAPERLAB_DIR, strat.name, state)
        summaries.append(lab.summarize(state))

    report = render_leaderboard(summaries, bench=_bench_closes(panel),
                                generated=f"{_now()} · code {_code_version()}",
                                start_default=START_DATE)
    REPORTS.mkdir(parents=True, exist_ok=True)
    LEADERBOARD.write_text(report, encoding="utf-8")
    live = sum(1 for s in summaries if s["days_live"] > 0)
    window = sum(1 for d in master if d >= START_DATE)
    _log(f"paperlab forward: strategies={len(summaries)} with_sessions={live} "
         f"window_sessions={window} → {LEADERBOARD}")
    return 0


# ─────────────────────────────────────────── 실행: 백필(BACKTEST)
def run_backfill(args: argparse.Namespace, backfill_from: date) -> int:
    swing_uni = _cached_universe()
    mom_uni = [s for s in NDX_100 if _cache_file(s).exists()]
    hibeta_uni = _hibeta_cached()
    roster = _build(swing_uni, mom_uni, hibeta_uni)

    needed: set[str] = {"QQQ", "TQQQ"}
    for strat in roster:
        needed.update(strat.universe())
    panel = _load_panel(sorted(needed), offline=args.offline)
    if "QQQ" not in panel:
        _log("FATAL: QQQ cache missing; cannot backfill.")
        return 1
    by_date = _by_date(panel)
    master = _master_dates(panel)     # 전체 날짜(워밍업 포함); 엔진이 backfill_from 이전은 신호용으로만 사용

    summaries: list[dict[str, Any]] = []
    for strat in roster:
        lab = PaperLab(strat)
        state = lab.fresh_state(backfill_from)
        # 백필은 비교 왜곡을 피해 적립 비활성(실장부도 순수). 적립은 포워드 실장부에만 적용.
        state = lab.run(state, master, by_date, contribute=False)
        # 검사 편의를 위해 별도 서브폴더에 멱등 저장(페이퍼 장부와 분리).
        save_state(PAPERLAB_DIR / "_backtest", strat.name, state)
        summaries.append(lab.summarize(state))

    report = render_backfill(summaries, bench=_bench_closes(panel),
                             generated=f"{_now()} · code {_code_version()}",
                             backfill_from=backfill_from)
    REPORTS.mkdir(parents=True, exist_ok=True)
    BACKFILL_REPORT.write_text(report, encoding="utf-8")
    window = sum(1 for d in master if d >= backfill_from)
    _log(f"paperlab backfill from {backfill_from}: strategies={len(summaries)} "
         f"window_sessions={window} → {BACKFILL_REPORT}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Aggressive forward paper lab runner (no real orders).")
    ap.add_argument("--backfill-from", type=str, default=None,
                    help="also run a historical BACKTEST replay from YYYY-MM-DD into a separate report")
    ap.add_argument("--reset", action="store_true", help="re-initialize all paper books")
    ap.add_argument("--no-refresh", action="store_true", help="skip network refresh of core closes")
    ap.add_argument("--offline", action="store_true", help="no network at all (cache only)")
    ap.add_argument("--backfill-only", action="store_true", help="run only the backfill replay")
    ap.add_argument("--refresh-universe-only", action="store_true",
                    help="only refresh strategy-universe closes (hibeta→mom→swing); no ledger advance")
    args = ap.parse_args(argv)

    if args.refresh_universe_only:
        return run_universe_refresh(args)
    rc = 0
    if args.backfill_from:
        bf = date.fromisoformat(args.backfill_from)
        rc |= run_backfill(args, bf)
    if not args.backfill_only:
        rc |= run_forward(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
