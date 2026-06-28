#!/bin/bash
# 토스 적립식 자동 매수를 macOS launchd LaunchAgent로 설치/제거한다.
#
#   bash scripts/install_dca_automation.sh           # dry-run(플랜만 기록)으로 설치
#   bash scripts/install_dca_automation.sh --live     # 실주문으로 설치 (TRADING_MODE=live 필요)
#   bash scripts/install_dca_automation.sh --uninstall # 제거
#   bash scripts/install_dca_automation.sh --status    # 상태/최근 로그 확인
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.tosstrader.dca"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WRAPPER="$PROJECT/scripts/dca_cron.sh"
PYBIN="${DCA_PYTHON:-/usr/local/bin/python3}"
[ -x "$PYBIN" ] || PYBIN="$(command -v python3)"
# 전체 디스크 접근(FDA)은 심볼릭 링크가 아니라 '실제' 바이너리에 부여해야 한다 → 해석.
PYREAL="$(readlink -f "$PYBIN" 2>/dev/null || "$PYBIN" -c 'import sys;print(sys.executable)')"
RUNNER="$PROJECT/scripts/run_dca.py"
UID_NUM="$(id -u)"

uninstall() {
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "🗑  제거 완료: $LABEL"
}

status() {
  echo "== launchd 등록 상태 =="
  launchctl list | grep "$LABEL" || echo "  (등록 안 됨)"
  echo; echo "== 최근 매매 로그 (data/dca.log) =="
  tail -n 15 "$PROJECT/data/dca.log" 2>/dev/null || echo "  (아직 로그 없음)"
}

case "${1:-}" in
  --uninstall) uninstall; exit 0 ;;
  --status)    status;    exit 0 ;;
esac

EXEC_ARG=""
MODE="dry-run(플랜만 기록)"
if [ "${1:-}" = "--live" ]; then
  EXEC_ARG='    <string>--execute</string>'
  MODE="LIVE 실주문"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data"

# bash를 거치지 않고 python을 직접 실행 → FDA를 줄 대상이 python 하나로 단순해진다.
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYREAL</string>
    <string>$RUNNER</string>
    <string>--auto</string>
$EXEC_ARG
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>$PROJECT/src</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$PROJECT/data/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/data/launchd.err.log</string>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST

# 재설치를 위해 기존 것 먼저 내림
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null || launchctl load "$PLIST"

echo "✅ 설치 완료 ($MODE)"
echo "   plist: $PLIST"
echo "   실행 바이너리(FDA 부여 대상): $PYREAL"
echo
echo "⚠️ 저장소가 ~/Desktop 아래라면 '전체 디스크 접근(FDA)'을 위 python에 부여해야 동작합니다:"
echo "   1) 설정 열기:  open \"x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles\""
echo "   2) [+] 클릭 → Cmd+Shift+G → 아래 경로 붙여넣기 → 추가 → 토글 ON:"
echo "      $PYREAL"
echo "   3) 적용:  launchctl kickstart -k gui/$UID_NUM/$LABEL"
echo
echo "확인: bash scripts/install_dca_automation.sh --status   (또는 tail -f data/dca.log)"
echo "제거: bash scripts/install_dca_automation.sh --uninstall"
