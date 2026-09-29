#!/usr/bin/env python3
"""PreToolUse(Edit|Write) 훅 — 원장·비밀파일 직접 편집을 차단한다.

Claude Code hook 규약: stdin 으로 JSON({tool_input:{file_path}})을 받고, 차단하려면
stderr 에 사유를 쓰고 exit 2. 통과는 exit 0.

- reports/trials_ledger.jsonl: append-only(오직 gate 하네스 코드로만 추가) → Edit/Write 금지.
- .env* : 비밀정보 → Edit/Write 금지. 단 .env.example(추적되는 템플릿)은 허용.

stdlib 만 사용.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _deny(reason: str) -> None:
    sys.stderr.write("⛔ 하네스 차단(PreToolUse/Edit): " + reason + "\n")
    raise SystemExit(2)


def check(file_path: str) -> None:
    norm = file_path.replace("\\", "/")
    name = Path(norm).name

    if norm.endswith("reports/trials_ledger.jsonl") or name == "trials_ledger.jsonl":
        _deny("원장(reports/trials_ledger.jsonl)은 append-only 입니다. "
              "gate.ledger_append/append_holdout_peek 로만 추가하세요(직접 편집 금지).")

    if name == ".env.example":
        return                                     # 추적되는 템플릿은 허용.
    if name == ".env" or name.startswith(".env."):
        _deny(f"비밀 파일({name}) 직접 편집 금지. 값은 로컬에서만 관리하세요.")


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0
    fp = (data.get("tool_input") or {}).get("file_path", "")
    if not isinstance(fp, str) or not fp.strip():
        return 0
    check(fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
