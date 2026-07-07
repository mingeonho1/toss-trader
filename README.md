# toss-trader

토스증권 OpenAPI 기반 **미국주식 스윙 자동매매** (결정론적 규칙 + Gemini 보조, 페이퍼 우선).

설계 전반과 진행상황은 **[PLAN.md](./PLAN.md)**, 일별 기록은 **[journal/](./journal/)** 참고.

## 빠른 시작
```bash
# .env에 자격증명 2줄 (PC 웹에서 발급): API_KEY=... / SECRET_KEY=...
PYTHONPATH=src python scripts/smoke_test.py   # 읽기 전용 실API 검증 (주문 없음)
PYTHONPATH=src python scripts/selftest.py     # 결정론 자가검증 (키 불필요)
```

요구사항: Python ≥ 3.11. 핵심 클라이언트는 **표준 라이브러리만** 사용(외부 패키지 0).
`.env` 자격증명 이름은 `API_KEY`/`SECRET_KEY`(또는 `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET`) 둘 다 지원.

## 안전장치
- `TRADING_MODE=paper` 가 기본. 실거래(`live`)는 PLAN.md §4 검증 게이트 통과 후에만.
- 주문은 멱등키(clientOrderId)로 중복 방지, 429는 Retry-After 준수, 401은 토큰 자동 재발급.
- `X-RateLimit-*` 헤더 적응 throttle. SSL CA 번들 자동 탐색(검증은 항상 유지).
- `LiveBroker`는 `require_live` 가드로 paper 모드에서 실주문을 막는다.

## 현재 상태 (2026-06-28)
- ✅ Phase 1: `TossClient` — 공식 OpenAPI v1.1.5 정합화 + 실 API 스모크 통과
- ✅ Phase 2·3·5: 비용모델(실 0.1%) · PaperBroker/백테스터/지표 · 리스크/엔진/일지 · LiveBroker
- ✅ Phase 4: 실데이터 멀티레짐 9년 게이트 검증 → **액티브는 B&H 못 이김**
- ✅ **기본 운용 확정 = 적립식(DCA) + 분산 바이앤홀드** (QQQ60/SCHD25/GLD15)

```bash
python scripts/run_dca.py --backtest   # 분산안 과거 검증
python scripts/run_dca.py              # 실계좌 적립 매수 플랜(dry-run, 주문 없음)
# python scripts/run_dca.py --execute  # 실주문(live·정규장에서만)
```
근거·수치는 `reports/strategy_gate_2026-06-28.md`, `reports/improvement_roadmap_2026-06-28.md`.

## 포워드 페이퍼 비교 (실현재가 기반, 실주문 없음)
`scripts/run_dca.py --auto`는 한 번 실행해 현재 계좌 현금 기준 DCA 매수 플랜만 기록하고 끝난다.
하루 동안 실제 현재가로 "지금 샀다면/팔았다면"을 비교하려면 별도 페이퍼 장부를 쓴다.

```bash
# 1회 실행: 현재가로 가상 포트폴리오 초기화/갱신 + 보고서 생성
PYTHONPATH=src python scripts/forward_paper_compare.py --reset

# 장중 반복 실행(5분마다): 터미널을 닫아도 계속, 실주문 없음
PYTHONPATH=src nohup caffeinate -dimsu python scripts/forward_paper_compare.py --watch --interval-sec 300 --reset > data/forward_paper.nohup.out 2>&1 &
echo $! > data/forward_paper.pid

# 확인/중지
tail -f data/forward_paper.nohup.out
tail -f data/forward_paper.log
kill "$(cat data/forward_paper.pid)"
```

비교 대상:
- 일시불 ETF 기준선: QQQ60/SCHD25/GLD15 매수 후 보유
- 듀얼모멘텀: QQQ/SPY/EFA/IWM/GLD 중 12개월 모멘텀 1등, 방어자산 IEF
- 200일 레짐필터: QQQ가 200일선 위면 QQQ, 아래면 IEF
- SMA 20/60 추세: 상승추세 상위 3개 동일비중

상태는 `data/forward_paper_state.json`, 로그는 `data/forward_paper.log`, 최신 보고서는
`reports/forward_paper_latest.md`에 저장된다. 모두 가상 체결이며 토스 계좌 주문은 만들지 않는다.
신규 후보 전략 검증 기록은 `docs/forward_strategy_plan.md`, 최신 게이트 결과는
`reports/strategy_gate_2026-07-07.md` 참고. 불합격 후보는 기본 forward 장부에 넣지 않는다.

## 호가/체결 데이터 수집 (읽기 전용)
단기 퀀트는 바로 매매하지 않고 raw 데이터부터 쌓는다. 수집기는 토스 `orderbook`/`trades`
읽기 API만 호출하며 주문을 만들지 않는다.

```bash
# 1회 수집
PYTHONPATH=src python scripts/collect_microstructure.py --symbols QQQ,SPY --once

# 장중 반복 수집(30초마다, 정규장일 때만)
PYTHONPATH=src nohup caffeinate -dimsu python scripts/collect_microstructure.py \
  --symbols QQQ,SPY --interval-sec 30 --regular-only \
  > data/microstructure.nohup.out 2>&1 &
echo $! > data/microstructure.pid

# 중지
kill "$(cat data/microstructure.pid)"

# 수집 데이터 요약
PYTHONPATH=src python scripts/analyze_microstructure.py --symbol QQQ
```

저장 경로는 `data/microstructure/YYYY-MM-DD/SYMBOL.jsonl`, 계획서는
`docs/microstructure_collector_plan.md`에 있다.

## 자동화 (항상 dry-run) & 실거래 전환
**입금**은 API로 불가 → 은행 자동이체/토스 앱으로 설정(예: 매주 일요일 ₩50,000). 봇은 들어온 현금만 매수.

**자동화(매수)**: macOS `launchd`로 **dry-run**(주문 없이 `data/dca.log`에 '오늘 살 플랜'만 기록).
`cron`이 아니라 `launchd`인 이유 — 잠자다 깨거나 **전원이 켜지면(RunAtLoad) 즉시 실행**.
```bash
bash scripts/install_dca_automation.sh            # 설치(dry-run)
bash scripts/install_dca_automation.sh --status   # 상태 + 최근 로그
bash scripts/install_dca_automation.sh --uninstall # 제거
tail -f data/dca.log                              # 봇이 뭘 하려는지 실시간 관찰
```
> ⚠️ macOS TCC: 프로젝트가 `~/Desktop`(또는 Documents/Downloads) 아래면 launchd가 접근 거부됨.
> 자동화하려면 저장소를 보호되지 않는 경로(예: `~/github/toss-trader`)에 두거나 해당 실행기에 전체 디스크 접근 권한을 부여해야 한다.

### 🔴 실거래(live)로 전환하는 법 — 직접 할 때
자동화는 **항상 dry-run**으로 둔다. 실제 매수는 **본인이 직접** 다음으로 실행:
```bash
# 1) .env 에서 TRADING_MODE=live 로 변경
# 2) 미국 정규장(KST 22:30~05:00, 금액주문은 정규장 전용)에 직접 실행:
TRADING_MODE=live PYTHONPATH=src python scripts/run_dca.py --execute
#    → 매수가능 현금을 QQQ60/SCHD25/GLD15로 시장가 매수. data/dca.log에 기록.
```
(원하면 자동화 자체를 live로: `bash scripts/install_dca_automation.sh --live` — 단 무인 실주문이므로 비권장. 기본은 dry-run.)
