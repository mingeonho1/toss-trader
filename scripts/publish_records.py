#!/usr/bin/env python3
"""매일 기록을 GitHub `paper-log` 브랜치에 올린다(맥에 누적하지 않기 위해).

- **허용 목록(allowlist)에 있는 파일만** 복사한다 → .env·토큰 캐시 등 비밀은 구조적으로 제외.
- 복사본을 한 번 더 비밀 패턴으로 검사해, 걸리면 커밋하지 않고 중단한다(이중 안전장치).
- 별도 git worktree(`data/_publish`, gitignore 영역)에서 `paper-log` 브랜치에 커밋·푸시.
  main 작업트리·브랜치는 건드리지 않는다.
- 푸시 성공 후 로컬 로그를 잘라내고 재생성 가능한 백테스트 산출물을 지운다.

  python scripts/publish_records.py            # 복사→검사→커밋→푸시→로컬 정리
  python scripts/publish_records.py --dry-run  # 무엇을 올릴지만 출력
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WT = ROOT / "data" / "_publish"
BRANCH = "paper-log"
KST = timezone(timedelta(hours=9))

# (원본 glob, 브랜치 내 목적지 디렉터리). 여기에 없는 파일은 절대 올라가지 않는다.
ALLOW = [
    ("reports/scoreboard_latest.md", "latest"),
    ("reports/paperlab_latest.md", "latest"),
    ("reports/decision_latest.md", "latest"),           # 데일리 결정 엔진(오늘의 판단)
    ("reports/intraday_shadow_latest.md", "latest"),
    ("reports/forward_paper_latest.md", "latest"),
    ("reports/forward_lifecycle_latest.md", "latest"),
    ("reports/strategy_live_latest.md", "latest"),
    ("data/scoreboard_history.jsonl", "state"),
    ("data/forward_paper_state.json", "state"),
    ("data/forward_lifecycle_state.json", "state"),
    ("data/loop/decision_state.json", "state/loop"),    # 결정 엔진 상태 스냅샷(멱등)
    ("data/loop/decisions.jsonl", "state/loop"),        # 하루 1레코드 판정 이력
    ("data/loop/requests.jsonl", "state/loop"),         # 에이전트 팀 작업요청(머신리더블)
    ("data/intraday_shadow/trades.jsonl", "state/intraday_shadow"),
    ("data/paperlab/*/state.json", "state/paperlab/{parent}"),
    ("data/paperlab/*/trades.jsonl", "state/paperlab/{parent}"),
    ("data/paperlab/*/equity.csv", "state/paperlab/{parent}"),
    ("data/_hist_cache/intraday/*.json", "intraday"),   # 키 없는 분봉은 당일치만 받을 수 있어 여기서 누적 보관
]
SKIP_PARENTS = {"_backtest"}
# daily/YYYY-MM-DD/ 에 날짜별 사본도 남기는 리포트들.
DAILY_SNAPSHOTS = ["reports/paperlab_latest.md", "reports/decision_latest.md"]

SECRET_PATTERNS = [
    re.compile(rb"(API_KEY|SECRET_KEY|TOSS_CLIENT_SECRET|TOSS_CLIENT_ID)\s*="),
    re.compile(rb"access_token\"?\s*[:=]"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def _git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # launchd의 최소 PATH에서도 자격증명 헬퍼(gh)가 보이게 한다.
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=check,
                          capture_output=True, text=True, timeout=120)


def ensure_worktree() -> None:
    if (WT / ".git").exists():
        _git("fetch", "-q", "origin", BRANCH, check=False)
        return
    WT.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "prune", check=False)
    remote = _git("ls-remote", "--heads", "origin", BRANCH, check=False).stdout.strip()
    if remote:
        _git("fetch", "-q", "origin", f"{BRANCH}:{BRANCH}", check=False)
        _git("worktree", "add", "-f", str(WT), BRANCH)
    else:
        _git("worktree", "add", "-f", "--detach", str(WT))
        _git("checkout", "-q", "--orphan", BRANCH, cwd=WT)
        _git("rm", "-rfq", ".", cwd=WT, check=False)
        (WT / "README.md").write_text(
            "# paper-log\n\n매일 자동 스코어보드·페이퍼 랩 기록(읽기 전용 산출물). "
            "`scripts/publish_records.py`가 허용 목록 파일만 커밋한다. 코드는 main 브랜치.\n",
            encoding="utf-8")


def collect() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for pattern, dest in ALLOW:
        for src in sorted(ROOT.glob(pattern)):
            if not src.is_file() or src.parent.name in SKIP_PARENTS:
                continue
            d = dest.format(parent=src.parent.name)
            pairs.append((src, Path(d) / src.name))
    day = datetime.now(KST).date().isoformat()
    for rel in DAILY_SNAPSHOTS:
        snap = ROOT / rel
        if snap.exists():
            pairs.append((snap, Path("daily") / day / snap.name))
    return pairs


def secret_hits(pairs: list[tuple[Path, Path]]) -> list[str]:
    hits = []
    for src, _ in pairs:
        blob = src.read_bytes()
        if any(p.search(blob) for p in SECRET_PATTERNS):
            hits.append(str(src.relative_to(ROOT)))
    return hits


def prune_local(max_log_bytes: int = 1_000_000, keep_lines: int = 2000) -> None:
    """푸시가 끝난 뒤에만 호출: 큰 로그는 뒷부분만 남기고, 재생성 가능한 백테스트 산출물은 지운다."""
    for log in (ROOT / "data").glob("*.log"):
        try:
            if log.stat().st_size > max_log_bytes:
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-keep_lines:]
                log.write_text("\n".join(tail) + "\n", encoding="utf-8")
        except OSError:
            continue
    shutil.rmtree(ROOT / "data" / "paperlab" / "_backtest", ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="매일 기록을 GitHub paper-log 브랜치에 올린다.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-prune", action="store_true")
    args = ap.parse_args(argv)

    pairs = collect()
    hits = secret_hits(pairs)
    if hits:
        print(f"❌ 비밀정보 패턴 감지 — 업로드 중단: {hits}", file=sys.stderr)
        return 2
    if args.dry_run:
        for src, dst in pairs:
            print(f"{src.relative_to(ROOT)} → {BRANCH}:{dst}")
        return 0

    ensure_worktree()
    for src, dst in pairs:
        out = WT / dst
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
    _git("add", "-A", cwd=WT)
    if not _git("status", "--porcelain", cwd=WT).stdout.strip():
        print("변경 없음 — 커밋 생략")
    else:
        stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        _git("commit", "-q", "-m", f"daily: 스코어보드·페이퍼 랩 기록 {stamp}", cwd=WT)
        push = _git("push", "-q", "-u", "origin", BRANCH, cwd=WT, check=False)
        if push.returncode != 0:
            print(f"❌ 푸시 실패(로컬 정리 생략): {push.stderr.strip()[:300]}", file=sys.stderr)
            return 1
        print(f"✅ {BRANCH} 푸시 완료 ({len(pairs)}개 파일)")
    if not args.no_prune:
        prune_local()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
