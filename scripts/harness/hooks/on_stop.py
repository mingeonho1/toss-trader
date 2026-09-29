#!/usr/bin/env python3
"""Stop / SubagentStop 훅 — 멈추기 전에 verify.py --changed 를 돌려 실패를 되돌려보낸다.

이것이 루프의 'fail → 다시 작업' 기제다. block findings 가 있으면 Claude Code 의
"block" 결정을 내보내(reason=findings) 에이전트가 멈추지 않고 계속 고치게 한다.

무한루프 방지(사용자 지침):
- 입력의 `stop_hook_active` 를 존중한다. 이미 활성(재진입)이고 **같은 findings 가 3회** 지속되면
  멈춤을 허용하고 data/harness_unresolved.json 에 남긴다.

Claude Code hook 규약: stdin JSON 입력. 멈춤 차단은 stdout 에 {"decision":"block","reason":…}
(exit 0). 멈춤 허용은 그냥 exit 0. 비밀은 출력하지 않는다. stdlib 만 사용.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERIFY = ROOT / "scripts" / "harness" / "verify.py"
STATE = ROOT / "data" / ".harness_stop_state.json"
UNRESOLVED = ROOT / "data" / "harness_unresolved.json"
PERSIST_LIMIT = 3


def _python() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _run_verify() -> tuple[list[dict], bool]:
    """(block findings, ran_ok). 실행 불가면 ([], False)."""
    try:
        r = subprocess.run([_python(), str(VERIFY), "--changed", "--json"],
                           cwd=ROOT, capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.SubprocessError):
        return [], False
    try:
        data = json.loads(r.stdout)
    except (ValueError, TypeError):
        return [], False
    blocks = [f for f in data.get("findings", []) if f.get("level") == "block"]
    return blocks, True


def _sig(blocks: list[dict]) -> str:
    key = "\n".join(sorted(f"{f.get('code')}|{f.get('path')}|{f.get('message')}"
                           for f in blocks))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(d: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _clear_state() -> None:
    try:
        STATE.unlink()
    except OSError:
        pass


def _reason(blocks: list[dict]) -> str:
    lines = ["연구 루프 하네스가 차단(block) 문제를 발견했습니다. 멈추지 말고 고치세요:"]
    for f in blocks:
        loc = f.get("path") or "(레포 전역)"
        lines.append(f"- [{f.get('code')}] {loc}: {f.get('message')}")
        lines.append(f"    ↳ 조치: {f.get('fix_hint')}")
    lines.append("고친 뒤 `python scripts/harness/verify.py --changed` 로 재확인하세요.")
    return "\n".join(lines)


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        data = {}
    stop_active = bool(data.get("stop_hook_active"))

    blocks, ran = _run_verify()
    if not ran:
        return 0                                   # 검증 불가(인프라 문제) → 멈춤 허용.
    if not blocks:
        _clear_state()
        return 0                                   # 깨끗함 → 멈춤 허용.

    sig = _sig(blocks)
    prev = _load_state()
    count = prev.get("count", 0) + 1 if prev.get("sig") == sig else 1

    if stop_active and prev.get("sig") == sig and count >= PERSIST_LIMIT:
        # 같은 findings 가 3회 지속 → 무한루프 방지: 멈춤 허용 + 미해결 기록.
        try:
            UNRESOLVED.parent.mkdir(parents=True, exist_ok=True)
            UNRESOLVED.write_text(json.dumps({
                "generated": datetime.now(timezone.utc).isoformat(),
                "persisted": count, "block": blocks,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        _clear_state()
        sys.stderr.write("하네스: 같은 block findings 가 3회 지속되어 멈춤을 허용하고 "
                         "data/harness_unresolved.json 에 남겼습니다.\n")
        return 0

    _save_state({"sig": sig, "count": count})
    print(json.dumps({"decision": "block", "reason": _reason(blocks)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
