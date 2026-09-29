#!/usr/bin/env bash
# toss-trader 에이전트 팀 루프 1사이클을 '격리 워크트리'에서 헤드리스로 돌린다.
#
#   bash scripts/loop/run_agent_cycle.sh                 # 실제 1사이클
#   bash scripts/loop/run_agent_cycle.sh --reason "urgent: audit ftlt"
#   bash scripts/loop/run_agent_cycle.sh --dry-run       # 락·워크트리·명령·claude 플래그만 검증(claude 미실행)
#   bash scripts/loop/run_agent_cycle.sh --print-cmd     # 조립되는 claude 명령만 출력(부작용 없음)
#
# 격리 원칙:
# - 전용 워크트리(~/github/toss-trader-loop)에서 origin/main 발 fresh 브랜치 loop/<YYYY-MM-DD> 로만 돈다.
#   메인 작업복사본(~/github/toss-trader; 매일 스코어보드가 여기서 돎)은 절대 건드리지 않는다.
# - .env 등 비밀은 워크트리에 두지 않는다(루프는 매매하지 않음). .venv 는 메인 것을 심볼릭 링크로 재사용.
# - 동시 실행 방지 락 + 최대 실행시간(기본 2h) 타임아웃. launchd 는 caffeinate -i 로 감싼다.
# - --dangerously-skip-permissions 는 절대 쓰지 않는다. 허용 도구는 명시 목록만.
#
# 워크트리 루트에 .claude/ 가 있으므로(도메인 하네스) 프로젝트 훅(PreToolUse 가드 +
# Stop/SubagentStop 검증)이 그대로 발화한다. 사용자 전역 훅(오케스트레이션 게이트)도 함께 적용된다.
set -euo pipefail

# ── 설정: 전부 환경변수로 재정의 가능(운영·테스트 유연성) ────────────────────
MAIN="${AGENTLOOP_MAIN:-/Users/mingh/github/toss-trader}"
WORKTREE="${AGENTLOOP_WORKTREE:-$HOME/github/toss-trader-loop}"
LOCK="${AGENTLOOP_LOCK:-$MAIN/data/loop/agentloop.lock}"
CYCLES_DIR="${AGENTLOOP_CYCLES_DIR:-$MAIN/data/loop/agent_cycles}"
BASE_REF="${LOOP_BASE_REF:-origin/main}"
MODEL="${LOOP_MODEL:-claude-opus-5-5}"
VENV_DIR="${LOOP_VENV_DIR:-$MAIN/.venv}"
PYTHON="${LOOP_PYTHON:-$VENV_DIR/bin/python}"
TIMEOUT_SECS="${LOOP_TIMEOUT_SECS:-7200}"
SUMMARY_REL="reports/agent_cycle_latest.md"     # /loop-cycle 스텝5가 워크트리에 쓰는 요약

# claude 실제 바이너리(zsh 래퍼 함수가 아니라 진짜 실행파일).
CLAUDE_BIN="${CLAUDE_BIN:-}"
if [ -z "$CLAUDE_BIN" ]; then
  for c in /opt/homebrew/bin/claude "$HOME/.local/bin/claude" /usr/local/bin/claude; do
    [ -x "$c" ] && { CLAUDE_BIN="$c"; break; }
  done
  [ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
fi

# launchd 의 최소 PATH 에서도 git/gh/claude 가 보이게.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

usage(){
  cat <<'EOF'
run_agent_cycle.sh — 에이전트 팀 루프 1사이클(격리 워크트리, 헤드리스)
  --reason TEXT   /loop-cycle 인자로 전달(예: "urgent: audit ftlt")
  --dry-run       락·워크트리·명령·claude 플래그 검증만(claude 미실행)
  --print-cmd     조립되는 claude 명령만 출력(부작용 없음)
  -h, --help      이 도움말
EOF
}

REASON=""
MODE="run"      # run | dry | printcmd
while [ $# -gt 0 ]; do
  case "$1" in
    --reason)     REASON="${2:-}"; shift 2;;
    --reason=*)   REASON="${1#*=}"; shift;;
    --dry-run)    MODE="dry"; shift;;
    --print-cmd)  MODE="printcmd"; shift;;
    -h|--help)    usage; exit 0;;
    *) echo "알 수 없는 인자: $1" >&2; usage >&2; exit 64;;
  esac
done

DATE="$(date +%F)"
BRANCH="loop/$DATE"
PROMPT="/loop-cycle"
[ -n "$REASON" ] && PROMPT="/loop-cycle $REASON"

# 명시 허용 도구(콤마 구분 단일 인자 — 괄호 안 공백은 도구 패턴의 일부).
ALLOWED="Read,Write,Edit,Grep,Glob,Agent,WebSearch,WebFetch,Bash(git *),Bash(gh *),Bash(python3 *),Bash(.venv/bin/python *),Bash($VENV_DIR/bin/python *),Bash(ls *),Bash(cat *),Bash(mkdir *)"

CMD=()
build_cmd(){
  CMD=( "$CLAUDE_BIN" -p "$PROMPT"
        --model "$MODEL"
        --permission-mode acceptEdits
        --output-format json
        --allowedTools "$ALLOWED" )
  # 주의: --dangerously-skip-permissions 는 절대 추가하지 않는다.
}

print_cmd(){
  build_cmd
  local out="" a
  for a in "${CMD[@]}"; do
    case "$a" in
      *" "*|*"*"*|*"("*|*")"*) out+="'$a' ";;   # 공백/글로브/괄호 포함 인자는 단일따옴표로 표기
      *) out+="$a ";;
    esac
  done
  printf '%s\n' "${out% }"
}

if [ "$MODE" = "printcmd" ]; then
  print_cmd
  exit 0
fi

# ── 워크트리 가드: 메인 작업복사본에서는 절대 돌지 않는다 ─────────────────────
norm(){ if [ -d "$1" ]; then ( cd "$1" && pwd -P ); else printf '%s' "${1%/}"; fi; }
MAIN_N="$(norm "$MAIN")"
WT_N="$(norm "$WORKTREE")"
if [ "$WT_N" = "$MAIN_N" ]; then
  echo "⛔ 거부: 워크트리 경로가 메인 저장소($MAIN_N)와 동일합니다. 루프는 격리 워크트리에서만 돕니다." >&2
  exit 3
fi

# ── 락: 동시 사이클 방지(mkdir 는 원자적). 죽은 PID 락은 회수 ────────────────
LOCK_HELD=0
release_lock(){ [ "$LOCK_HELD" = "1" ] && rm -rf "$LOCK" 2>/dev/null; return 0; }
acquire_lock(){
  mkdir -p "$(dirname "$LOCK")"
  if mkdir "$LOCK" 2>/dev/null; then
    echo "$$" > "$LOCK/pid"; LOCK_HELD=1; trap release_lock EXIT INT TERM; return 0
  fi
  local pid; pid="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "⛔ 다른 사이클이 실행 중입니다(pid $pid). 락: $LOCK" >&2
    exit 4
  fi
  echo "ℹ️  낡은 락 회수(pid=${pid:-?} 없음): $LOCK" >&2
  rm -rf "$LOCK"
  mkdir "$LOCK" && echo "$$" > "$LOCK/pid" && LOCK_HELD=1
  trap release_lock EXIT INT TERM
}
acquire_lock

# ── 워크트리 준비: origin/main 발 fresh loop/<date> ──────────────────────────
setup_worktree(){
  git -C "$MAIN" fetch -q origin || echo "⚠️ git fetch 실패(계속 시도)" >&2
  if git -C "$MAIN" worktree list --porcelain 2>/dev/null | grep -qx "worktree $WT_N"; then
    git -C "$WORKTREE" fetch -q origin || true
    git -C "$WORKTREE" checkout -q -B "$BRANCH" "$BASE_REF"
  else
    git -C "$MAIN" worktree prune
    git -C "$MAIN" worktree add -q -B "$BRANCH" "$WORKTREE" "$BASE_REF"
  fi
  rm -f "$WORKTREE/.env"                              # 비밀은 워크트리에 두지 않는다
  [ -e "$WORKTREE/.venv" ] || ln -s "$VENV_DIR" "$WORKTREE/.venv"   # venv 재사용(비밀 아님)
}

verify_claude_flags(){
  [ -n "$CLAUDE_BIN" ] && [ -x "$CLAUDE_BIN" ] || { echo "⛔ claude 실행파일을 찾을 수 없습니다." >&2; exit 5; }
  local help; help="$("$CLAUDE_BIN" --help 2>&1 || true)"
  local f
  for f in --print --model --permission-mode --output-format --allowedTools; do
    echo "$help" | grep -q -- "$f" || { echo "⛔ 이 claude 버전 --help 에 $f 가 없습니다(플래그명 불일치)." >&2; exit 5; }
  done
}

setup_worktree
verify_claude_flags

if [ "$MODE" = "dry" ]; then
  echo "── DRY-RUN ──────────────────────────────────────────"
  echo "메인:      $MAIN_N"
  echo "워크트리:  $WT_N  (브랜치 $BRANCH ← $BASE_REF)"
  echo "락:        $LOCK (pid $$)"
  echo ".env:      $([ -e "$WORKTREE/.env" ] && echo '있음 ⚠️(문제)' || echo '없음 ✓')"
  echo ".venv:     $(readlink "$WORKTREE/.venv" 2>/dev/null || echo "$WORKTREE/.venv")"
  echo "python:    $PYTHON"
  echo "타임아웃:  ${TIMEOUT_SECS}s"
  echo "명령:      $(print_cmd)"
  echo "(claude 는 실행하지 않음 — 검증 전용)"
  exit 0
fi

# ── 실제 실행 ────────────────────────────────────────────────────────────────
mkdir -p "$CYCLES_DIR" "$MAIN/reports"
TS="$(date +%Y%m%dT%H%M%S)"
OUT_JSON="$CYCLES_DIR/$TS.json"
ERR_LOG="$CYCLES_DIR/$TS.stderr.log"
SUMMARY_OUT="$CYCLES_DIR/$TS.summary.md"

build_cmd
export PYTHONPATH="$WORKTREE/src:${PYTHONPATH:-}"
TIMEOUT_BIN="$(command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || true)"

echo "▶ 사이클 시작 $TS · $PROMPT · 워크트리 $WT_N"
set +e
if [ -n "$TIMEOUT_BIN" ]; then
  ( cd "$WORKTREE" && "$TIMEOUT_BIN" "$TIMEOUT_SECS" "${CMD[@]}" ) >"$OUT_JSON" 2>"$ERR_LOG"
else
  ( cd "$WORKTREE" && "${CMD[@]}" ) >"$OUT_JSON" 2>"$ERR_LOG"
fi
RC=$?
set -e

# 요약: 사이클이 쓴 워크트리 요약을 우선 채택 → 메인 reports 로도 복사(publish_records 허용목록).
if [ -f "$WORKTREE/$SUMMARY_REL" ]; then
  cp -f "$WORKTREE/$SUMMARY_REL" "$SUMMARY_OUT"
  cp -f "$WORKTREE/$SUMMARY_REL" "$MAIN/$SUMMARY_REL"
else
  "$PYTHON" - "$OUT_JSON" "$SUMMARY_OUT" "$RC" <<'PY' || true
import json, sys, datetime
src, dst, rc = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(src))
    res = d.get("result") or d.get("summary") or "(요약 없음)"
except Exception:
    res = "(사이클 JSON 파싱 실패 — stderr.log 참고)"
open(dst, "w").write(
    f"# 에이전트 사이클 요약\n\n- 생성: {datetime.datetime.now().isoformat()}\n- 종료코드: {rc}\n\n{res}\n")
PY
  [ -f "$SUMMARY_OUT" ] && cp -f "$SUMMARY_OUT" "$MAIN/$SUMMARY_REL"
fi

echo "완료 rc=$RC · json=$OUT_JSON · summary=$SUMMARY_OUT · main=$MAIN/$SUMMARY_REL"
exit "$RC"
