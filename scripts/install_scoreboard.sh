#!/bin/bash
# 매일 자동 스코어보드를 macOS launchd LaunchAgent로 설치/제거한다.
# 읽기 전용·멱등·주문 없음. install_dca_automation.sh 와 동일한 관례.
#
#   bash scripts/install_scoreboard.sh            # 설치(하루 1회 06:30 KST + RunAtLoad)
#   bash scripts/install_scoreboard.sh --offline   # 캐시만으로 설치(자격증명 무시)
#   bash scripts/install_scoreboard.sh --uninstall # 제거
#   bash scripts/install_scoreboard.sh --status    # 상태/최근 로그 확인
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.tosstrader.scoreboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYBIN="${SCOREBOARD_PYTHON:-$PROJECT/.venv/bin/python3}"
[ -x "$PYBIN" ] || PYBIN="/usr/local/bin/python3"
[ -x "$PYBIN" ] || PYBIN="$(command -v python3)"
# 전체 디스크 접근(FDA)은 심볼릭 링크가 아니라 '실제' 바이너리에 부여해야 한다 → 해석.
PYREAL="$(readlink -f "$PYBIN" 2>/dev/null || "$PYBIN" -c 'import sys;print(sys.executable)')"
RUNNER="$PROJECT/scripts/daily_scoreboard.py"
UID_NUM="$(id -u)"

uninstall() {
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "🗑  제거 완료: $LABEL"
}

status() {
  echo "== launchd 등록 상태 =="
  launchctl list | grep "$LABEL" || echo "  (등록 안 됨)"
  echo; echo "== 최신 대시보드 (reports/scoreboard_latest.md 앞부분) =="
  head -n 8 "$PROJECT/reports/scoreboard_latest.md" 2>/dev/null || echo "  (아직 리포트 없음)"
  echo; echo "== 최근 launchd 로그 =="
  tail -n 10 "$PROJECT/data/scoreboard.launchd.err.log" 2>/dev/null || echo "  (아직 로그 없음)"
}

case "${1:-}" in
  --uninstall) uninstall; exit 0 ;;
  --status)    status;    exit 0 ;;
esac

OFFLINE_ARG=""
MODE="자동(자격증명 있으면 실시세, 없으면 캐시)"
if [ "${1:-}" = "--offline" ]; then
  OFFLINE_ARG='    <string>--offline</string>'
  MODE="offline(캐시 전용)"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data"

# python 을 직접 실행(래퍼 없이) → FDA 부여 대상이 python 하나로 단순.
# 06:30 KST: 미 정규장 마감(EDT 05:00 / EST 06:00 KST) 이후 → 서머타임/표준시 양쪽 커버.
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <!-- pmset 예약 기상(06:25)은 배터리에선 수 초짜리 DarkWake라 곧 다시 잠든다(2026-09-29 관측:
         06:30 작업이 10:05 기상까지 밀림). 기상 시각에 맞춰 발화하고, 실행 중엔 caffeinate로 잠들지 않게 붙잡는다.
         -i(유휴 슬립 방지) + -s(AC 전원일 때 시스템 슬립까지 방지 — 배터리에선 무해). AC 전원 유지 시
         06:25 작업이 밀리지 않고 즉시 완료된다(2026-09-30 페이퍼 랩 정체 회귀 대응). -->
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>-s</string>
    <string>$PYREAL</string>
    <string>$RUNNER</string>
$OFFLINE_ARG
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>$PROJECT/src</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>25</integer></dict>
  <key>StandardOutPath</key><string>$PROJECT/data/scoreboard.launchd.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/data/scoreboard.launchd.err.log</string>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST

# 재설치를 위해 기존 것 먼저 내림
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null || launchctl load "$PLIST"

echo "✅ 설치 완료 (스코어보드 $MODE, 매일 06:30 KST + RunAtLoad)"
echo "   plist: $PLIST"
echo "   실행 바이너리(FDA 부여 대상): $PYREAL"
echo
echo "⚠️ 저장소가 ~/Desktop 아래라면 '전체 디스크 접근(FDA)'을 위 python 에 부여해야 동작합니다:"
echo "   1) 설정 열기:  open \"x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles\""
echo "   2) [+] 클릭 → Cmd+Shift+G → 아래 경로 붙여넣기 → 추가 → 토글 ON:"
echo "      $PYREAL"
echo "   3) 적용:  launchctl kickstart -k gui/$UID_NUM/$LABEL"
echo
echo "확인: bash scripts/install_scoreboard.sh --status   (또는 cat reports/scoreboard_latest.md)"
echo "제거: bash scripts/install_scoreboard.sh --uninstall"
