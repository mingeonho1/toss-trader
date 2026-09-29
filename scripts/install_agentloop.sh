#!/bin/bash
# 에이전트 팀 루프(주간 자동 실행)를 macOS launchd LaunchAgent 로 설치/제거한다.
# install_scoreboard.sh 와 동일한 관례(caffeinate -i, WorkingDirectory, data/ 로그).
#
#   bash scripts/install_agentloop.sh            # 설치(매주 토 10:00 KST, RunAtLoad=false)
#   bash scripts/install_agentloop.sh --status    # 상태/최근 요약·로그 확인
#   bash scripts/install_agentloop.sh --uninstall # 제거
#   bash scripts/install_agentloop.sh --run-now   # 즉시 1회 실행(urgent kick · 실제 사이클!)
#
# 주간(토 10:00 KST): 미 증시 휴장일이라 안전. RunAtLoad=false → 설치만으로는 실행되지 않는다.
# urgent kick: 데일리 엔진/사람이 `launchctl kickstart -k gui/<uid>/<label>` 로 즉시 발화 가능.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.tosstrader.agentloop"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNNER="$PROJECT/scripts/loop/run_agent_cycle.sh"
UID_NUM="$(id -u)"

uninstall() {
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "🗑  제거 완료: $LABEL"
}

status() {
  echo "== launchd 등록 상태 =="
  launchctl list | grep "$LABEL" || echo "  (등록 안 됨)"
  echo; echo "== 다음 실행 스케줄 =="
  echo "  매주 토요일 10:00 KST (미 증시 휴장) · RunAtLoad=false · urgent kick=kickstart"
  echo; echo "== 최신 사이클 요약 (reports/agent_cycle_latest.md 앞부분) =="
  head -n 10 "$PROJECT/reports/agent_cycle_latest.md" 2>/dev/null || echo "  (아직 사이클 없음)"
  echo; echo "== 최근 사이클 산출물 =="
  ls -t "$PROJECT/data/loop/agent_cycles/"*.json 2>/dev/null | head -n 3 || echo "  (없음)"
  echo; echo "== 최근 launchd 로그 =="
  tail -n 12 "$PROJECT/data/agentloop.launchd.err.log" 2>/dev/null || echo "  (아직 로그 없음)"
}

run_now() {
  echo "▶ urgent kick: $LABEL 즉시 실행(실제 사이클)"
  launchctl kickstart -k "gui/$UID_NUM/$LABEL"
  echo "  진행: bash scripts/install_agentloop.sh --status  (또는 tail -f data/agentloop.launchd.err.log)"
}

case "${1:-}" in
  --uninstall) uninstall; exit 0 ;;
  --status)    status;    exit 0 ;;
  --run-now)   run_now;   exit 0 ;;
esac

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data"
chmod +x "$RUNNER" 2>/dev/null || true

# 토=Weekday 6 (launchd: 0/7=일요일). 10:00 KST. RunAtLoad=false. caffeinate 로 실행 중 잠들지 않게.
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>/bin/bash</string>
    <string>$RUNNER</string>
    <string>--reason</string>
    <string>weekly-sat</string>
  </array>
  <key>RunAtLoad</key><false/>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>6</integer>
    <key>Hour</key><integer>10</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$PROJECT/data/agentloop.launchd.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/data/agentloop.launchd.err.log</string>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null || launchctl load "$PLIST"

echo "✅ 설치 완료 (에이전트 루프 · 매주 토 10:00 KST · RunAtLoad=false)"
echo "   plist:  $PLIST"
echo "   runner: $RUNNER"
echo
echo "urgent kick(즉시 1회): bash scripts/install_agentloop.sh --run-now"
echo "                       또는 launchctl kickstart -k gui/$UID_NUM/$LABEL"
echo "상태 확인:            bash scripts/install_agentloop.sh --status"
echo "제거:                bash scripts/install_agentloop.sh --uninstall"
echo
echo "⚠️ repo 가 ~/Desktop·Documents·Downloads 아래면 launchd 접근 거부(TCC). ~/github 권장."
