# 연구 루프 검증 하네스 (`scripts/harness/`)

이 프로젝트가 **실제로 겪은** 실패만 잡는 도메인 특화 검증. 범용 하네스 기능은 넣지
않는다(더 나은 모델이면 불필요해질 일반 검사 배제 — 사용자 방침).

## 구성
- `scripts/harness/checks.py` — 각 검사가 순수 함수로 `list[Finding(level, code, path, message, fix_hint)]` 반환.
- `scripts/harness/verify.py [--changed|--all|--staged] [--json]` — 집계기. block 이 하나라도
  있으면 **exit 2**(메시지·조치 힌트 한국어), 아니면 0. `--changed`= HEAD 대비 변경 + 미추적 파일.
- `scripts/harness/hooks/` — Claude Code 프로젝트 훅(아래 "피드백 루프").
- `.github/workflows/ci.yml` — main·upgrade/** push/PR 에서 `pytest -q` + `verify.py --all`.
- `tests/test_harness.py` — 각 검사가 과거 실패를 잡고 현재 레포는 통과함을 증명.

## 검사 목록 — 무엇을 막나
| 코드 | 수준 | 막는 과거 사고 |
|---|---|---|
| **F1** | block/warn | 수수료 가정 오류: API 명세 '예시'의 25bp 를 주 비용으로 사용. 토스 표준은 0.1%/10bp(≤$10 매수 무료, 매도 SEC/TAF $0.01 최소). block=25bp 를 쓰며 10bp/수수료모델 근거 전무. warn=주 비용 기본값이 25bp. |
| **F2** | block | 결과 파일 경로 충돌: 두 실험이 `reports/c12_results.json` 을 함께 써 덮어씀. 모든 실험의 RESULTS_JSON/REPORT 경로 유일성 정적 스캔. |
| **F3** | block/warn | 사전등록 누락/결과 뒤 배치(결과 보고 등록). 전략 리포트는 결과 마커(`<!-- RESULTS_BELOW -->`) 앞에 사전등록 필수. warn=게이트 평가하면서 원장 미기록. |
| **F4** | block | 홀드아웃 peek-once 위반(idea_id 당 홀드아웃 2회+) · 원장 append-only 파괴(HEAD 기존 행이 작업본에서 변경/삭제). |
| **F5** | block | 룩어헤드(미래참조)로 성과 부풀림. 신호/결정 함수를 정의한 실험은 `lookahead_guard` 를 호출하는 `tests/test_cXX*.py` 필수. |
| **F6** | warn | 생존편향: 단일종목 매매 실험이 PIT 멤버십(c3c) 없이 결과를 '상한(UPPER BOUND)'으로도 라벨 안 함. |
| **F7** | warn | 신호 재사용(홀드아웃 이미 본 신호 재비용/재실행: `_fee10`/`_micro`)인데 semi_contaminated(반오염) 미표기(부록 v2.1 §4, 유의성 p<0.01 강화). |
| **F8** | block | 규제 매매가능성: 레버리지/인버스-레버리지 ETP(TQQQ/SQQQ/SOXL…)를 **보유**하는 전략을 실계좌 그룹에 편성. universe 가 아니라 decide() 실제 보유를 검사 → 신호 전용(c13a hibeta) 정당 전략은 오탐 없음. |
| **F9** | block | 비밀정보 유출: `.env`/`.token_cache.json`/`*.key` 추적·스테이징 또는 publish 허용목록 포함(.env.example 예외). |
| **F10** | block/warn | 실주문 안전: 자동화 plist/스크립트의 `--execute`/`TRADING_MODE=live`, run_dca/run_strategy dry-run 기본값 훼손. 보호 주문경로 파일(client/broker/live_exec/run_dca/run_strategy) 변경 시 주문경로 테스트 통과 필수. |
| **F11** | warn | 레버리지/타이밍 주장 감사: 고점대비 낙폭·합성 2x/3x 로 PASS 를 주장하면서 대응 감사(시장ATH vs 코호트ATH·종료일 절단·합성 배당 이중계상) 미언급. |

## 피드백 루프(훅) — 실패를 어떻게 되돌려보내나
`.claude/settings.json` 이 프로젝트 훅 3종을 등록한다(stdlib·stdin JSON·<5s, 주문경로 테스트 예외):

- **PreToolUse `Bash`** → `hooks/pre_bash.py`: 비밀 스테이징(`git add .env`/`-f data/`),
  라이브 자동화(`install_*_automation.sh --live`·`launchctl` 로 실주문 plist 적재),
  실주문(`TRADING_MODE=live`·`run_dca/run_strategy --execute`), 원장 되쓰기(`sed -i`/`>`/`rm`/`truncate`),
  `git push … main` 을 **exit 2 + stderr 사유**로 차단.
- **PreToolUse `Edit|Write`** → `hooks/pre_edit.py`: `reports/trials_ledger.jsonl`(append-only)과
  `.env*`(비밀) 직접 편집 차단(.env.example 허용).
- **Stop / SubagentStop** → `hooks/on_stop.py`: 멈추기 전에 `verify.py --changed` 실행. block 이
  있으면 Claude Code `{"decision":"block","reason":<findings>}` 를 내보내 **에이전트가 멈추지 않고
  고치게** 한다(= 루프의 "fail → 다시 작업"). 무한루프 방지: 입력 `stop_hook_active` 를 존중하고,
  **같은 findings 가 3회** 지속되면 멈춤을 허용하고 `data/harness_unresolved.json` 에 남긴다.

## 쓰는 법
```bash
python scripts/harness/verify.py --changed     # 작업 중 변경분만(기본)
python scripts/harness/verify.py --all          # 전체(CI)
python scripts/harness/verify.py --staged --json  # 커밋 전 스테이징분, JSON
```

## 설계 메모(현재 레포 상태)
- 현재 레포는 **block 0 · warn 소수**(F1 c2a/c3a 25bp 기본값, F11 합성레버리지 PASS 리포트,
  필요시 F7). 과거 c2/c3 의 25bp 는 c4b 가 10bp 로 정정(문서화)했고, c13a/c14a 재실행판은 이미
  semi_contaminated 를 남겨 F7 을 통과한다.
- F1 은 "25bp 를 쓰며 10bp 근거가 전혀 없는" 순수 사고만 block(현재 실험은 전부 10bp 를 병기).
- F10 의 보호 주문경로 테스트 실행은 **변경 스코프에 보호 파일이 있을 때만**(`--changed`/`--staged`)
  돌린다. `--all` 은 CI 의 전체 pytest 가 대신한다.
