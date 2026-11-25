# 에이전트 팀 사이클 요약 — 2026-11-25 (weekly-sat)

- 브랜치: `loop/2026-11-25` · 결정 메모: `docs/loop/decisions/2026-11-25.md`
- **주문 없음 · 자동화 live 설치 없음.** 실계좌 전환은 사용자 승인 사항.

## 1. 기록 건전성 (운영 우선)

| 문제 | 원인 | 조치 |
|---|---|---|
| paper-log 11-16~11-25 미게시(10일) | `data/_publish` 워크트리가 origin/paper-log 와 분기(ahead 11 / behind 2) → 매일 non-fast-forward 거부, 실패 사유가 로그에 안 남음 | 로컬 우선 리베이스 후 밀린 기록 푸시 완료. `publish_records.py`: 푸시 거부 시 fetch→`rebase -X theirs`→1회 재시도(강제 푸시 없음). `daily_scoreboard.py`: 실패 사유 로그 출력. risk-officer APPROVE |
| 장부 11-11 정지 | 11-13 허용 IP 미등록 → 캐시 폴백 | 로컬은 11-16부터 live 복귀·해소 |
| 늦은 일봉 허위 수익 | 엔진이 늦게 도착한 바를 재계산하지 않음(overnight_tqqq +7.1% 허위) | 보유/목표 심볼 바가 없으면 그날 계산을 다음 실행으로 미룸(≤3세션 정지 캐시) |
| mom_top5_ndx 42세션 미체결 | INTC 캐시 09-22 정지 + "전 목표 가격 필요" 규칙 | 3세션 넘게 낡은 심볼 제외 필터 |
| hibeta 계열 | c13a(월간) 대비 매일 재선정, 종목 바 지연 시 전량 현금화 | 월간 바스켓 + 결측 시 기존 바스켓 유지 |

기존 원장은 덮어쓰지 않음.

## 2. 판단 변화 (페이퍼 근거, 세션 2026-11-24, n=42)

- 신규 DEMOTED 8종: buffer_hibeta, ftlt_hibeta, ftlt_hibeta_psq, hibeta_basket, simple_hibeta, lrs200_tqqq, simple_rsi_uvxy, tqqq_bh(벤치).
- 감사 판정 **WEAKENED**: 독립 수익흐름은 2개(하이베타 바스켓, TQQQ 롱)뿐이고 하락장 한 사건을 여러 번 셈. z 분모가 백테스트 변동성이라 약 1.5배 부풀려짐. 실제 남는 괴리는 하이베타 실현 β ≈2.5(백테스트 1.6).
- 결정: DEMOTED 유지(구제 안 함), 오염 7개 원장 표기(R2 확인 전 "보류"). RETIRED 도달 예상 ≈2026-12-23.
- `mom_top5_ndx` = **INVALID**(관찰 선두 표시 철회). `overnight_qqq` z+2.45 는 버그 아님(레짐), 승격 안 함.
- **실계좌 추천 없음 — 기본 DCA 유지.**

## 3. 편입/퇴출

- 편입 없음. 정찰 카드 3장(`docs/pipeline/candidates/2026-11-25_*`) 중 2장 실험:
  - c16a GEM 듀얼 모멘텀 — **FAIL**(홀드아웃 QQQ 대비 0.82×, DSR 0.03)
  - c16b Faber GTAA — **FAIL**(0.72×, 연 6.6회 교체로 자체 kill 규칙 위반)
  - pre-FOMC 드리프트 — 비용 후 엣지 미미로 보류
- 퇴출 없음.

## 4. 되돌려 보낸 횟수·사유

- 0회. 실험 2건 모두 FAIL이라 auditor 반박 단계 불필요. 감사 R1(버그 수정)은 executor로 처리.
- 실행 경로: 서브 계정(alt-agent) 실행이 권한 승인 문제로 차단 → Agent executor로 대체.

## 5. 통합 게이트

- `verify.py --all`: 차단 없음(기존 F11 경고만) · `pytest -q`: 869 passed, 2 skipped.

## 6. 미해결 (다음 사이클로)

- R2 장부 재생 비교(`experiments/2026-11-25_ledger_replay.py`) — 세션 권한으로 실행 불가. 실행: `PYTHONPATH=src .venv/bin/python experiments/2026-11-25_ledger_replay.py`
- R3 β 조정·라이브 변동성 z 진단 · R4 hot_rvol_swing 진입 재생 스캔 · R5 부칙 제안
- 위 5건은 worktree `data/loop/requests.jsonl`에만 기록됨(메인 저장소 `data/loop/requests.jsonl`은 세션 쓰기 범위 밖이라 미반영).

## 7. 사용자 결정 필요

- U1 하이베타 6종 v2 원장(수정 코드, n=0) 신규 시작 여부
- U2 R2 결과 반영 방식(메모만[기본] vs 원장 상태 교체)
- U3 `mom_top5_ndx` 원장 리셋
- U4 R5 부칙: (i) 벤치마크 상태 전이 제외 (ii) 동일 수익흐름 강등 묶기 — 결정권자 찬성, (iii)은 R3 후
- U5 Codex CLI 업그레이드(GPT-6 판사 `gpt-6-astra` 불가)
- U6 R2 재생 스크립트 실행 허용
- 운영: agentloop `git fetch` SSL 타임아웃으로 10-10 이후 자동 사이클 rc=1 반복 — 네트워크 점검
