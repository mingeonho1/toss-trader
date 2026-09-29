#!/usr/bin/env python3
"""연구 루프 도메인 검증 하네스 집계기.

    python scripts/harness/verify.py [--changed | --all | --staged] [--json]

- --changed (기본): HEAD 대비 변경 파일 + 미추적 파일만 검사.
- --staged        : 스테이징된(인덱스) 파일만 검사.
- --all           : 레포 전체 검사(CI 용).
- --json          : 결과를 JSON 으로 출력.

block 수준 findings 가 하나라도 있으면 exit 2, 아니면 exit 0.
비밀정보는 절대 출력하지 않는다(경로/코드/메시지만). stdlib 만 사용.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import checks  # noqa: E402  (scripts/harness/checks.py)

ROOT = _HERE.parents[1]                      # scripts/harness → 레포 루트


def _git_lines(*args: str) -> list[str]:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def changed_scope() -> set[str]:
    """HEAD 대비 변경(추적) + 미추적 파일의 상대경로 집합."""
    files = set(_git_lines("diff", "--name-only", "HEAD"))
    files |= set(_git_lines("ls-files", "--others", "--exclude-standard"))
    return files


def staged_scope() -> set[str]:
    return set(_git_lines("diff", "--cached", "--name-only"))


def _order_findings(fs: list[checks.Finding]) -> list[checks.Finding]:
    lvl = {"block": 0, "warn": 1}
    return sorted(fs, key=lambda f: (lvl.get(f.level, 2), f.code, f.path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="toss-trader 연구 루프 검증 하네스")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--changed", action="store_true", help="HEAD 대비 변경+미추적만(기본)")
    g.add_argument("--all", action="store_true", help="레포 전체")
    g.add_argument("--staged", action="store_true", help="스테이징된 파일만")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args(argv)

    if args.all:
        scope = None
        mode = "all"
    elif args.staged:
        scope = staged_scope()
        mode = "staged"
    else:
        scope = changed_scope()
        mode = "changed"

    ctx = checks.Context(root=ROOT, scope=scope)
    findings = _order_findings(checks.run_all(ctx))
    blocks = [f for f in findings if f.level == "block"]
    warns = [f for f in findings if f.level == "warn"]

    if args.json:
        print(json.dumps({
            "mode": mode,
            "scope": sorted(scope) if scope is not None else None,
            "block": len(blocks), "warn": len(warns),
            "findings": [asdict(f) for f in findings],
        }, ensure_ascii=False, indent=2))
        return 2 if blocks else 0

    if not findings:
        print(f"[harness] {mode}: 통과 — findings 없음.")
        return 0

    def emit(f: checks.Finding) -> None:
        tag = "차단" if f.level == "block" else "경고"
        loc = f.path or "(레포 전역)"
        print(f"  [{f.code}/{tag}] {loc}\n      {f.message}\n      ↳ 조치: {f.fix_hint}")

    if blocks:
        print(f"[harness] {mode}: 차단(block) {len(blocks)}건 — 아래를 고쳐야 합니다.")
        for f in blocks:
            emit(f)
    if warns:
        print(f"[harness] {mode}: 경고(warn) {len(warns)}건")
        for f in warns:
            emit(f)
    if not blocks:
        print(f"[harness] {mode}: 차단 없음(경고만) — 통과.")
    return 2 if blocks else 0


if __name__ == "__main__":
    raise SystemExit(main())
