#!/usr/bin/env python3
"""레인 3 인트라데이 포워드 섀도 실행기 — 수집된 분봉에 동결 규칙을 적용, 증거 누적.

실주문 없음. `data/_hist_cache/intraday/{SYM}_1m.json` 의 각 세션(ET 날짜)에 대해
`toss_trader.intraday_shadow` 의 사전등록 규칙(ORB-5/15·모멘텀·갭앤고)을 돌려 가상 트레이드를
`data/intraday_shadow/trades.jsonl` 에 **멱등**(date+rule+symbol) append 하고,
`reports/intraday_shadow_latest.md` 에 규칙×크기별 누적 통계(n, net bps, t-stat, 부트 CI, 승률,
상태)를 쓴다. 통계·판정은 gate_v2_spec §3.5(거래단위) 를 따른다.

예:
  PYTHONPATH=src python scripts/intraday_shadow_run.py
  PYTHONPATH=src python scripts/intraday_shadow_run.py --cache-dir data/_hist_cache/intraday
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import gate  # noqa: E402
from toss_trader import intraday_shadow as ish  # noqa: E402
from toss_trader.intraday_sources import INTRADAY_CACHE_DIR  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRADES_FILE = ROOT / "data" / "intraday_shadow" / "trades.jsonl"
REPORT_FILE = ROOT / "reports" / "intraday_shadow_latest.md"

# 리포트 행 순서(고정). core 는 QQQ/TQQQ, movers 는 그 외.
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
SIZES = ish.DEFAULT_SIZES
N_MIN = 200          # spec §3.5: n<200 이면 통계주장 불가
T_MIN = 3.0          # per-trade t ≥ 3.0


# ── 캐시 로딩 ────────────────────────────────────────────────────────────────
def _load_symbol_rows(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text()).get("rows", []) or []
    except (ValueError, OSError):
        return []


def _group_by_session(rows: list[dict]) -> dict[str, list[dict]]:
    """rows → {ET-date(str): [row,...]}. et 필드의 앞 10자(YYYY-MM-DD) 로 세션 구분."""
    out: dict[str, list[dict]] = {}
    for r in rows:
        et = str(r.get("et", ""))
        if len(et) < 10:
            continue
        out.setdefault(et[:10], []).append(r)
    for day in out:
        out[day].sort(key=lambda x: int(x["ts"]))
    return out


def _rows_to_bars(rows: list[dict]) -> list[ish.Bar]:
    raw = [(datetime.fromisoformat(r["et"]), float(r["c"]), float(r.get("v", 0.0) or 0.0))
           for r in rows]
    return ish.to_bars(raw)


def _prior_close(sessions: dict[str, list[dict]], day: str) -> float | None:
    """day 직전 세션의 마지막 **정규장** 종가. 없으면 None(첫 세션·정규장 없음)."""
    prior_days = [d for d in sorted(sessions) if d < day]
    for d in reversed(prior_days):
        reg = [r for r in sessions[d] if r.get("regular")]
        if reg:
            return float(reg[-1]["c"])
    return None


# ── 트레이드 생성 ────────────────────────────────────────────────────────────
def collect_trades(cache_dir: Path, interval: str) -> tuple[list[dict], dict]:
    """캐시 전체를 훑어 가상 트레이드 레코드 리스트 + 진단 카운터 반환."""
    records: list[dict] = []
    diag = {"symbols": 0, "sessions": 0, "regular_sessions": 0, "no_prior_close": 0}
    suffix = f"_{interval}.json"
    for path in sorted(cache_dir.glob(f"*{suffix}")):
        symbol = path.name[: -len(suffix)]
        rows = _load_symbol_rows(path)
        if not rows:
            continue
        diag["symbols"] += 1
        sessions = _group_by_session(rows)
        tier = ish.tier_for(symbol)
        for day in sorted(sessions):
            diag["sessions"] += 1
            bars = _rows_to_bars(sessions[day])
            if any(b.regular for b in bars):
                diag["regular_sessions"] += 1
            pc = _prior_close(sessions, day)
            if pc is None:
                diag["no_prior_close"] += 1
            for sig in ish.signals_for_symbol(symbol, bars, prior_close=pc):
                records.append(ish.build_trade_record(
                    sig, symbol=symbol, session_date=day, tier=tier, sizes=SIZES))
    return records, diag


# ── 멱등 append ──────────────────────────────────────────────────────────────
def _read_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _write_trades(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


# ── 통계·리포트 ──────────────────────────────────────────────────────────────
def _status(n: int, tstat: float, ci_low: float | None) -> str:
    if n < N_MIN:
        return "수집 중 (n<200)"
    if tstat >= T_MIN and ci_low is not None and ci_low > 0:
        return "후보 (t≥3)"
    return "기각"


def _rule_size_stats(records: list[dict], rule: str, size_key: str,
                     rng: random.Random, bootstrap_min_n: int) -> dict:
    pnls = [r["sizes"][size_key]["net"] for r in records
            if r["rule"] == rule and size_key in r.get("sizes", {})]
    bps = [r["sizes"][size_key]["net_bps"] for r in records
           if r["rule"] == rule and size_key in r.get("sizes", {})]
    n = len(pnls)
    mean_bps = sum(bps) / n if n else 0.0
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / n if n else 0.0
    tstat = gate.trade_tstat(pnls) if n >= 2 else 0.0
    ci_low = ci_high = None
    if n >= bootstrap_min_n:
        _, ci_low, ci_high = gate.trade_pnl_bootstrap_ci(pnls, rng=rng)
    return {"n": n, "mean_bps": mean_bps, "win_rate": win_rate, "tstat": tstat,
            "ci_low": ci_low, "ci_high": ci_high,
            "status": _status(n, tstat, ci_low)}


def _fmt(x: float | None, spec: str = ".2f") -> str:
    return "n/a" if x is None else format(x, spec)


def write_report(path: Path, records: list[dict], diag: dict, *, rng: random.Random,
                 bootstrap_min_n: int, generated: str) -> None:
    dates = sorted({r["date"] for r in records})
    lines = [
        "# 인트라데이 포워드 섀도 (레인 3) — 누적 증거",
        "",
        f"- 생성: `{generated}`",
        "- 실주문 없음. 사전등록·동결 규칙의 가상 트레이드(`docs/intraday_shadow_rules.md`).",
        f"- 세션 날짜: {len(dates)}개 ({dates[0]}~{dates[-1]})" if dates else "- 세션 날짜: 없음",
        f"- 수집 진단: 심볼 {diag['symbols']}, 세션 {diag['sessions']}"
        f"(정규장 포함 {diag['regular_sessions']}), prior_close 없음 {diag['no_prior_close']}",
        f"- 판정(spec §3.5): n≥{N_MIN} 且 t≥{T_MIN:.0f} 且 부트 95% CI 하한>0 → '후보'."
        f" 부트스트랩은 n≥{bootstrap_min_n} 에서만 계산.",
        "- 레인 3은 백테스트로 '채택'하지 않는다 — 이 표는 필요조건 스크린일 뿐,"
        " 최종 검증은 마이크로구조+포워드 페이퍼(spec §8.3).",
        "",
    ]
    for size in SIZES:
        size_key = f"{int(size)}"
        lines.extend([
            f"## 포지션 크기 ${int(size)}",
            "",
            "| 규칙 | n | 평균 net bps | 승률 | t-stat | 부트 CI(net$) | 상태 |",
            "|---|---:|---:|---:|---:|---|---|",
        ])
        for rule in RULE_ORDER:
            s = _rule_size_stats(records, rule, size_key, rng, bootstrap_min_n)
            ci = ("n/a" if s["ci_low"] is None
                  else f"[{s['ci_low']:+.4f}, {s['ci_high']:+.4f}]")
            lines.append(
                f"| {RULE_LABEL[rule]} | {s['n']} | {_fmt(s['mean_bps'])} | "
                f"{s['win_rate'] * 100:.1f}% | {_fmt(s['tstat'])} | {ci} | {s['status']} |")
        lines.append("")
    lines.extend([
        "## 주의",
        "",
        "- PRICE-ONLY(o=h=l=c, v=0): OR 고저는 분봉 last-price 범위 근사. VWAP-반전은 미구현(거래량 없음).",
        "- movers = core(QQQ/TQQQ) 외 전 수집 심볼(대형주 과대포함, 15bp 반호가로 보수적 페널티).",
        "- prior_close 없으면 모멘텀은 첫 봉 프록시(flag), 갭앤고는 트레이드 생성 안 함.",
        "- 체결: next-bar(트리거)/예약봉(시각). 종가신호→종가체결 아님(spec §2.3).",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ── 엔트리포인트 ─────────────────────────────────────────────────────────────
def run(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="인트라데이 포워드 섀도(가상 트레이드 누적).")
    p.add_argument("--cache-dir", default=str(INTRADAY_CACHE_DIR),
                   help=f"분봉 캐시 디렉터리. 기본 {INTRADAY_CACHE_DIR}.")
    p.add_argument("--interval", default="1m", help="사용할 봉 인터벌(기본 1m).")
    p.add_argument("--trades", default=str(TRADES_FILE), help="트레이드 JSONL 경로.")
    p.add_argument("--report", default=str(REPORT_FILE), help="리포트 markdown 경로.")
    p.add_argument("--bootstrap-min-n", type=int, default=N_MIN,
                   help=f"부트스트랩 CI 를 계산할 최소 n(기본 {N_MIN}).")
    p.add_argument("--seed", type=int, default=20260923, help="부트스트랩 rng 시드(재현성).")
    args = p.parse_args(argv)

    cache_dir = Path(args.cache_dir)
    trades_path = Path(args.trades)
    if not cache_dir.exists():
        print(json.dumps({"error": f"cache dir not found: {cache_dir}"}, ensure_ascii=False))
        return 1

    fresh, diag = collect_trades(cache_dir, args.interval)
    existing = _read_trades(trades_path)
    merged, added = ish.dedup_append(existing, fresh)
    _write_trades(trades_path, merged)

    rng = random.Random(args.seed)
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    write_report(Path(args.report), merged, diag, rng=rng,
                 bootstrap_min_n=args.bootstrap_min_n, generated=generated)

    print(json.dumps({
        "generated": generated, "candidate_trades": len(fresh), "added": added,
        "total_trades": len(merged), "diag": diag,
        "trades": str(trades_path), "report": str(args.report),
    }, ensure_ascii=False))
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
