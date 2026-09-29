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
   └ publish_records ─→ paper-log 브랜치 ─┴──────────────→ └ decider ─ 편성 변경·실계좌 추천 메모
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
