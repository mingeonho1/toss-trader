---
description: toss-trader 루프 1사이클 — paper-log 판단을 읽고 에이전트 팀을 돌려 검증 통과까지 반복한 뒤 main에 squash 통합
---

toss-trader 루프 엔지니어링 **1사이클**을 실행한다. 저장소: 이 디렉터리(toss-trader). 팀 정의: `.claude/agents/`. 흐름 설명: `docs/loop/README.md`.
인자(선택): $ARGUMENTS — 예: `scout-only`, `audit <전략>`, `max-iter 2`.

## 0. 진실의 원천 동기화
- `git fetch origin && git status` — 작업 브랜치는 `loop/<YYYY-MM-DD>` (main에서 분기). main 직접 푸시 금지.
- **paper-log 최신 상태를 당겨온다**: `git fetch origin paper-log`.
- 오늘의 판단을 읽는다: `git show origin/paper-log:latest/decision_latest.md` — 특히 **"🤖 LLM 판단"** 섹션(데일리 LLM 심판 요지)과 오늘 상태 변화·방출된 요청을 확인한다.
- 머신리더블 작업요청을 읽는다: `git show origin/paper-log:state/loop/requests.jsonl` — 이번 사이클에서 처리할 audit/scout/investigate 요청.

## 1. 관측 — `paper-analyst` 에이전트
지난 사이클 이후 변화·괴리·미처리 요청을 분류받는다. **기록 건전성 문제**(빠진 날, 장부 정지, 캐시 폴백)가 있으면 그것부터 고친다(운영 문제가 연구보다 우선).

## 2. 작업 배정 (독립 작업은 병렬로)
- `audit` / `investigate_divergence` 요청 → `auditor`
- `scout` 요청이 있거나 마지막 정찰(docs/pipeline/intel/ 최신 날짜)이 7일 이상 지났으면 → `scout` (카드 ≤3장)
- 새 카드 중 실계좌 가능성이 있는 상위 1~2장 → `experimenter` (사전등록 실험)
- 주문 경로·자동화·허용목록 변경이 있거나 intel에 `⚠️ 비용/규제 변경` → `risk-officer`

## 3. 검증 루프 (핵심 — 통과할 때까지 되돌려 보낸다)
각 실험/수정 산출물에 대해:
1. `python scripts/harness/verify.py --changed` — block 0건이어야 한다. (에이전트가 끝내려 할 때 Stop/SubagentStop 훅이 같은 검사를 돌려 실패하면 자동으로 되돌려 보낸다.)
2. `.venv/bin/python -m pytest -q` 전부 통과.
3. 실험이 PASS/CONDITIONAL이면 `auditor`가 반박 시도. WEAKENED/REFUTED + `## 재실험 요청`이 있으면 그 지시를 **그대로** `experimenter`에게 다시 전달한다.
4. 1~3을 최대 3회(또는 $ARGUMENTS의 max-iter) 반복. 그래도 해결 안 되면 `data/loop/requests.jsonl`에 미해결로 남기고 다음 사이클로 넘긴다(억지로 통과시키지 않는다).

## 4. 결정 — `decider` 에이전트
페이퍼 상태기계 + 감사 + 게이트 판정으로 편성 변경(페이퍼 랩 편입/퇴출)과 실계좌 추천을 결정하고 `docs/loop/decisions/<날짜>.md`를 쓴다. 편입이 결정되면 `experimenter`가 `src/toss_trader/paperlab_strategies/`에 파라미터 동결로 추가(등가성 테스트 포함).
실계좌 전환·`--execute`·자동화 live 설치는 **절대 하지 않는다** — 메모에 "사용자 승인 필요"로만 남긴다.

## 5. 통합
- **사이클 요약 파일 작성(필수)**: `reports/agent_cycle_latest.md` 에 이번 사이클 요약을 쓴다 — 판단 변화(페이퍼 근거), 편입/퇴출 전략, 되돌려 보낸 횟수·사유, 사용자 결정 필요 항목. (자동화 러너 `scripts/loop/run_agent_cycle.sh` 가 이 파일을 메인 저장소 `reports/` 로 복사하고, `scripts/publish_records.py` 가 paper-log 로 올린다.)
- **통합 게이트**: `python scripts/harness/verify.py --all` 과 `.venv/bin/python -m pytest -q`(전체) 를 돌린다.
  - 둘 다 통과 → 작업 브랜치 커밋(작성자 mingeonho1) → PR 생성 → **squash merge to main**(PR 본문에 결과 요약 표). 원장·리포트·결정 메모 포함.
  - 하나라도 실패 → **머지하지 않는다.** PR 은 열어 둔 채, 실패 사유를 PR 본문과 `reports/agent_cycle_latest.md` 에 기록하고 `data/loop/requests.jsonl` 에 미해결로 남긴다(억지 통합 금지).
- 사용자에게 보고: 이번 사이클의 판단 변화(페이퍼 근거), 새로 편입/퇴출된 전략, 되돌려 보낸 횟수와 사유, 사용자 결정이 필요한 사항.
