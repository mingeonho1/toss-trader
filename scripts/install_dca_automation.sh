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

chmod +x "$WRAPPER"
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$WRAPPER</string>
$EXEC_ARG
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>DCA_PYTHON</key><string>$PYBIN</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>1</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>0</integer></dict>
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
echo "   python: $PYBIN"
echo
echo "다음 단계(잠자도 미국 정규장에 깨어 실행하려면 — sudo 필요, 직접 실행):"
echo "   sudo pmset repeat wakeorpoweron MTWRFSU 22:55:00"
echo "   (매일 22:55 KST에 Mac을 깨워 23:00 launchd 작업이 돌게 함. 노트북은 전원 연결 권장.)"
echo
echo "확인: bash scripts/install_dca_automation.sh --status"
echo "제거: bash scripts/install_dca_automation.sh --uninstall"
