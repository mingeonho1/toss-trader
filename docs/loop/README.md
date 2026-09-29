# 루프 엔지니어링 운영 구조

toss-trader는 두 개의 루프로 돈다. **매일 도는 결정론적 판단 루프**가 페이퍼 결과로 판단을 바꾸고,
**에이전트 팀 루프**가 그 판단이 만든 작업 요청을 처리한다. 두 루프는 `paper-log` 브랜치로 연결된다.

```
 [매일 06:25 KST · launchd · 주문 없음]                 [에이전트 팀 · /loop-cycle]
 daily_scoreboard.py                                    paper-analyst ─ 관측·분류
   ├ refresh_closes (키 없는 종가)                         │
   ├ paperlab (27개 전략 포워드 페이퍼)                    ├ scout ─ 새 기법·뉴스·규제 → 후보 카드
   ├ daily_decision ─ 사전등록 상태기계 ──┐                 ├ experimenter ─ 사전등록 실험
   │   (WARMUP→EVALUATING→CANDIDATE      │                 ├ auditor ─ 반박 시도 → 재실험 요청
   │    →LIVE_READY / DEMOTED→RETIRED)   │ requests.jsonl  ├ risk-officer ─ 주문경로·규제·비밀
   ├ llm_judge ─ GPT-6 판사(검증·강등만) │ (+source=llm)   └ decider ─ 편성 변경·실계좌 추천 메모
   └ publish_records ─→ paper-log 브랜치 ─┴──────────────→
                                                              │
                         검증 하네스(scripts/harness) ←─────────┘  Stop/SubagentStop 훅이
                         실패 → 에이전트에게 되돌려 보냄            block 결과를 에이전트에 되돌림
```

## 1. 매일 판단 루프 (결정론, 사람 개입 없음)
- 규칙: `docs/loop/decision_rules.md` (사전등록, 변경은 날짜 붙은 부록으로만).
- 산출물(paper-log): 오늘의 판단 `latest/decision_latest.md`, 날짜별 사본 `daily/<날짜>/`, 상태 `state/loop/`.
- 판단이 바뀌는 근거는 오직 **포워드 페이퍼 장부**다. 백테스트는 '기대치'로만 쓰이고, 실측이 기대에서
  벗어나면(z-score·낙폭비) 상태가 바뀌고 `requests.jsonl`에 감사·정찰 요청이 생긴다.
- 실주문은 절대 내지 않는다. LIVE_READY는 "사용자 승인 필요" 표시일 뿐이다.

## 2. 에이전트 팀
| 에이전트 | 역할 | 쓰는 곳 |
|---|---|---|
| `paper-analyst` | paper-log 읽고 변화·괴리·요청 분류 (읽기 전용) | — |
| `scout` | 논문·GitHub·커뮤니티 기법, 시장 뉴스, 토스/한국 규제·수수료 변경 | `docs/pipeline/candidates/`, `docs/pipeline/intel/` |
| `experimenter` | 사전등록 실험 구현·실행 | `experiments/`, `tests/`, `reports/` |
| `auditor` | 적대적 감사, 재실험 요청 | `reports/audits/` |
| `risk-officer` | 실주문 경로·자동화·규제·비밀 리뷰 (읽기 전용) | — |
| `decider` | 편성 변경·실계좌 추천·규칙 부록 | `docs/loop/decisions/` |

실행: 저장소에서 Claude Code를 열고 `/loop-cycle` (정의: `.claude/commands/loop-cycle.md`).

## 3. 검증 하네스 — 이 프로젝트가 실제로 겪은 실패만 막는다
상세: `docs/harness.md`. 요약하면 수수료 오인(25bp), 결과 파일 덮어쓰기, 사전등록 누락, 홀드아웃 재열람·원장 재작성,
룩어헤드, 생존편향, 신호 재사용 미표시, 규제 대상 ETP의 '실계좌' 오분류, 비밀정보 커밋, 자동 실매수,
레버리지/낙폭 인공물 미점검. 훅(`.claude/settings.json`)이 위험 명령을 사전 차단하고, 에이전트가 끝내려 할 때
검사를 돌려 실패하면 사유와 수정 힌트를 붙여 **되돌려 보낸다**. CI(`.github/workflows/ci.yml`)가 main에 같은 검사를 건다.

## 4. 자동 실행 (에이전트 팀 루프)

에이전트 팀 루프는 사람이 `/loop-cycle` 을 직접 부르지 않아도 **주기적으로** 돈다.

- **러너**: `scripts/loop/run_agent_cycle.sh` — 격리 워크트리에서 헤드리스 Claude Code(`/loop-cycle`)로 1사이클.
  - `--dry-run` (락·워크트리·명령·claude 플래그 검증, claude 미실행), `--print-cmd` (조립 명령만 출력), `--reason TEXT`.
- **주간 정기**: 매주 **토요일 10:00 KST** (미 증시 휴장). launchd `com.tosstrader.agentloop`, `RunAtLoad=false`.
- **긴급 kick**: 데일리 엔진/사람이 즉시 1회 발화 —
  `bash scripts/install_agentloop.sh --run-now` 또는 `launchctl kickstart -k gui/$(id -u)/com.tosstrader.agentloop`.

**격리 (매우 중요)**: 루프는 전용 워크트리 `~/github/toss-trader-loop` 에서 `origin/main` 발 fresh 브랜치
`loop/<YYYY-MM-DD>` 로만 작업한다. 매일 스코어보드가 도는 **메인 작업복사본(`~/github/toss-trader`)은 절대
건드리지 않는다**(러너가 경로 동일 시 거부). 워크트리에는 `.env` 를 두지 않고(루프는 매매 안 함) `.venv` 는
메인 것을 심볼릭 링크로 재사용한다. 동시 실행은 락(`data/loop/agentloop.lock`)으로, 폭주는 타임아웃(기본 2h)으로 막는다.
`--dangerously-skip-permissions` 는 쓰지 않으며 허용 도구는 명시 목록만.

**설치/상태/제거**:
```
bash scripts/install_agentloop.sh            # 설치(주간 토 10:00 KST)
bash scripts/install_agentloop.sh --status    # 등록 상태 · 최근 요약 · 로그
bash scripts/install_agentloop.sh --uninstall # 제거(중지)
```

**로그·산출물** (모두 메인 저장소 아래):
- `data/loop/agent_cycles/<timestamp>.json` — 사이클 원본 출력(`--output-format json`), `.summary.md`, `.stderr.log`.
- `data/agentloop.launchd.{out,err}.log` — launchd 표준출력/에러.
- `reports/agent_cycle_latest.md` — 최신 사이클 요약(paper-log publish 허용목록 포함).

**중지**: `bash scripts/install_agentloop.sh --uninstall` (LaunchAgent 제거). 실행 중 사이클은 락 해제까지 기다리거나
프로세스를 종료(락 자동 회수). 템플릿: `automation/com.tosstrader.agentloop.plist`.

> 전제: 이 루프 인프라(`scripts/loop/run_agent_cycle.sh`, 갱신된 `/loop-cycle`, 하네스)가 **origin/main 에 병합된 뒤**
> 첫 실제 사이클이 유효하다. 러너는 `LOOP_BASE_REF`(기본 `origin/main`)에서 워크트리를 만들기 때문이다.

## 5. LLM 판단 레이어 (GPT-6 · Codex CLI)

`daily_decision`(결정론) **직후** 별도 격리 단계 `llm_judge`(`scripts/loop/llm_judge.py`)가 돈다.
결정론 엔진이 감사 가능한 백본으로 남고, 그 위에 GPT-6(로컬 `codex` CLI, `gpt-6-astra`)이 해석·판단
레이어로 얹힌다. **codex 는 항상 `--sandbox read-only`, 주문 API 미접촉, 승인우회 플래그 금지.**

- **입력 번들(JSON·비밀 없음)**: 오늘 결정론 산출(전 전략 state/지표), 최근 10일 판정 이력, 열린 요청,
  동결 백테스트 기대치, 최신 정찰 인텔(있으면), 한국 리테일 하드 제약.
- **출력(구조화·`--output-schema`)**: `stance`(hold_dca|recommend), `recommended_strategy`, `sleeve_frac`,
  `confidence`, `state_overrides`(강등만), `rationale_ko`, `what_changed_ko`, `watch_items`, `requests`.
- **LLM 하네스(= 루프의 fail→되돌림)**: 도메인 검증 — 추천은 실계좌·CANDIDATE/LIVE_READY 만(벤치·`_moc`·
  탐색 제외), sleeve∈[0,0.5], override 는 강등만, 존재하는 전략만. 위반 시 사유를 붙여 1~2회 재프롬프트 →
  실패면 결정론 폴백(`rejected`). codex 실패/미로그인/타임아웃 → `unavailable`, 결정론 유지.
- **병합(보수적)**: 강등만 병합 뷰에 반영(결정론 백본 파일 불변), LLM 요청은 `requests.jsonl` 에
  `source="llm"` 로 추가. 전량 기록: `data/loop/llm_judgments.jsonl`(프롬프트 해시·모델·원출력·검증오류·
  시도·상태). 리포트 `decision_latest.md` 끝에 "🤖 LLM 판단" 섹션.
- **긴급 킥**: 병합 후 오늘 자 priority `high` 요청(규칙 또는 LLM)이 있으면 agentloop 잡이 로드된 경우에만
  `launchctl kickstart` 로 1회 발화(ET 날짜당 1회, `data/loop/kick_state.json`).
- **안전 검사**: 하네스 F12 가 `llm_judge.py` 에 주문/실행 토큰이 없고 codex 가 read-only 인지 정적 스캔.
- 규칙 전문·제약: `docs/loop/decision_rules.md` 부록 B(2026-09-29).
