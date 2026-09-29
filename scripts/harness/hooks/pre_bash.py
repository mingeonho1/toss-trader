#!/usr/bin/env python3
"""PreToolUse(Bash) 훅 — 위험한 셸 명령을 사전 차단한다.

Claude Code hook 규약: stdin 으로 JSON({tool_name, tool_input:{command}, ...})을 받고,
차단하려면 **stderr 에 사유를 쓰고 exit 2**. 통과는 exit 0(무출력).

차단 대상(이 프로젝트가 실제로 지켜야 할 것만):
- 비밀정보 스테이징: `git add` .env/토큰캐시/*.key/*.pem, `git add -f data/`.
- 자동화 라이브 설치/기동: install_*_automation.sh --live, launchctl load/bootstrap 로
  --execute/ TRADING_MODE=live 가 든 plist 적재.
- 실주문: TRADING_MODE=live, run_dca/run_strategy --execute (실주문은 사용자만 수동으로).
- 원장 되쓰기: sed -i / > redirect / rm / truncate on reports/trials_ledger.jsonl.
- main 직접 push: git push … main/master (main 은 squash PR 로만).

비밀 '값'은 절대 출력하지 않는다. stdlib 만 사용.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LEDGER = "reports/trials_ledger.jsonl"

# 비밀 경로 토큰(.env.example 는 예외).
_SECRET = re.compile(r"(?<![\w.])\.env(?:\.(?!example)\w+)?\b|\.token_cache\.json\b|"
                     r"token_cache\b|[\w./-]+\.key\b|[\w./-]+\.pem\b|secrets\.\w+")


def _deny(reason: str) -> None:
    sys.stderr.write("⛔ 하네스 차단(PreToolUse/Bash): " + reason + "\n")
    raise SystemExit(2)


def _plist_has_live(path_token: str) -> bool:
    """launchctl 로 적재하려는 plist 에 활성 --execute / TRADING_MODE=live 가 있나."""
    try:
        import plistlib
        p = (ROOT / path_token) if not path_token.startswith("/") else Path(path_token)
        if not p.exists():
            # 홈 LaunchAgents 등 절대경로가 레포 밖이면 텍스트 근사.
            return False
        with open(p, "rb") as f:
            data = plistlib.load(f)
        args = [str(a) for a in (data.get("ProgramArguments") or [])]
        env = data.get("EnvironmentVariables") or {}
        if any("--execute" in a for a in args):
            return True
        if str(env.get("TRADING_MODE", "")).lower() == "live":
            return True
    except Exception:
        return False
    return False


def check(cmd: str) -> None:
    low = cmd.lower()

    # 1) 비밀정보 스테이징.
    if re.search(r"\bgit\s+(add|stage)\b", cmd):
        # .env.example 만 있는 경우는 통과.
        hits = [m.group(0) for m in _SECRET.finditer(cmd)]
        if hits:
            _deny(f"비밀 파일을 스테이징하려 함({hits[0]}). 커밋 금지 대상입니다.")
        if re.search(r"\bgit\s+add\s+(-f|--force)\b.*\bdata/", cmd):
            _deny("git add -f data/ — 무시된 런타임/비밀 산출물을 강제 스테이징하려 함.")

    # 2) 자동화 라이브 설치/기동.
    if re.search(r"install_\w*automation\w*\.sh", cmd) and re.search(r"(?<!\w)--live\b", cmd):
        _deny("install_*_automation.sh --live — 무인 실주문 자동화 설치는 금지(사용자 수동만).")
    if "launchctl" in low and re.search(r"\b(load|bootstrap|start|kickstart)\b", low):
        for tok in re.findall(r"\S+\.plist", cmd):
            if _plist_has_live(tok):
                _deny(f"launchctl 로 실주문 plist 적재({tok}) — --execute/TRADING_MODE=live 포함.")

    # 3) 실주문.
    if re.search(r"TRADING_MODE\s*=\s*live", cmd):
        _deny("TRADING_MODE=live — 실주문 전환은 사용자만 수동으로 결정합니다.")
    if re.search(r"run_(dca|strategy)\.py\b", cmd) and re.search(r"(?<!\w)--execute\b", cmd):
        _deny("run_dca/run_strategy --execute — 실주문 실행은 하네스에서 차단(사용자 수동만).")

    # 4) 원장 되쓰기(append-only 파괴).
    if LEDGER in cmd or "trials_ledger.jsonl" in cmd:
        if re.search(r"\bsed\s+-i\b", cmd) or re.search(r"\bperl\s+-i", cmd):
            _deny("sed -i / perl -i 로 원장 수정 — 원장은 append-only 입니다.")
        if re.search(r"(^|[^>])>\s*[^>]*trials_ledger\.jsonl", cmd):
            _deny("> 리다이렉트로 원장 덮어쓰기 — 원장은 gate 코드로만 append 합니다.")
        if re.search(r"\brm\b[^\n]*trials_ledger\.jsonl", cmd):
            _deny("rm 으로 원장 삭제 — 원장은 삭제 금지(append-only 이력).")
        if re.search(r"\btruncate\b[^\n]*trials_ledger\.jsonl", cmd):
            _deny("truncate 로 원장 절단 — 원장은 append-only 입니다.")
        if re.search(r"\btee\b(?!\s+-a)[^\n]*trials_ledger\.jsonl", cmd):
            _deny("tee(덮어쓰기)로 원장 수정 — 원장은 append-only 입니다.")

    # 5) main 직접 push.
    if re.search(r"\bgit\s+push\b", cmd) and re.search(r"(?<![\w/])(main|master)\b", cmd):
        _deny("git push … main/master — main 은 squash PR 로만 반영합니다(직접 push 금지).")


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0                                   # 입력 파싱 실패시 통과(안전측: 다른 훅/권한이 처리)
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str) or not cmd.strip():
        return 0
    check(cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
