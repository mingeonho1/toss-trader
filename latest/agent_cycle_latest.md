# 에이전트 팀 사이클 요약 — 2026-11-26 (weekly-sat)

- 브랜치: `loop/2026-11-26` · 결정 메모: `docs/loop/decisions/2026-11-26.md`
- **주문 없음 · 자동화 live 설치 없음.** 실계좌 전환은 사용자 승인 사항.
- **통합 게이트 미완 → main 머지 안 함(PR 열어 둠).** `verify.py --all` 실행이 세션 권한으로 거부됨. pytest 는 통과.

## 1. 기록 건전성

- paper-log 11-14~11-26 연속 게시, 세션 n=39→43 하루씩 전진. 누락·장부 정지·캐시 폴백 없음.
- 11-20 세션만 2일 늦게 생성(일회성), 이후 정상.
- GPT-6 판사(`gpt-6-astra`) 여전히 불가 — Codex CLI 업그레이드 필요(사용자 환경).
- 자동 리포트가 `mom_top5_ndx`(11-25 INVALID 판정)를 여전히 "관찰 선두"로 표시 → 표시 괴리 요청 등록.

## 2. 판단 변화 (페이퍼 근거, 세션 2026-11-25, n=43)

- 상태 전이 없음. 괴리 요청 6건은 11-25에 결정한 하이베타·lrs200_tqqq·simple_rsi_uvxy 반복(DEMOTED 유지). buffer_hibeta 는 z −2.03→−1.96 로 문턱 위 복귀(전이 아님).
- **holy_grail 감사: BUG → WEAKENED.** 11-09 TQQQ 신호용 봉이 늦게 들어와 낡은 RSI(27.5, 실제 38.7)로 TECL 보유 유지 → 11-11 +9.6% 부당 반영. 손계산 보정 시 누적 +9.19% → 약 −0.65%, z 0.73 → 약 0.33. 원장에 "오염·R2 확인 전 보류" 표기(라벨만, 규칙 변경 없음).
- **ftlt_1x: WEAKENED**(배선 정상). 초과수익 대부분이 11-20 하루. holy_grail·ftlt_1x·overnight_qqq 성과는 독립 증거 3건이 아니라 11-20/11-23 레짐 사건 1건으로 셈.
- **R3 β 진단(신규 수치):** 하이베타 5종 라이브 β 0.81~1.24, 95% CI 모두 1.6 포함(n=42, 유효 블록 ≈8). 11-25 메모의 "실현 β≈2.5" 가설은 지지되지 않음 → 괴리는 β 과다가 아니라 잔차(종목 선택/레짐) 쪽.
- **실계좌 추천 없음 — 기본 DCA 유지.**

## 3. 코드 수정 (이번 사이클)

- `src/toss_trader/paperlab.py`: 늦은 봉 대기 대상을 보유/목표 심볼 → **전략 유니버스 전체(신호 전용 심볼 포함)** 로 확장(일반·오버나이트 경로 모두). holy_grail 11-09 버그 재발 방지.
- 회귀 테스트 `tests/test_paperlab_late_bars.py::test_late_signal_symbol_defers` 추가.
- 영향: 큰 유니버스 전략은 한 심볼이라도 늦으면 ≤3세션 대기 → 정체 경보가 늘 수 있음.

## 4. 편입/퇴출

- 편입 없음 · 퇴출 없음. 정찰은 11-25 수행분으로 생략(7일 미경과), 신규 실험 없음.

## 5. 되돌려 보낸 횟수·사유

- 0회(PASS/CONDITIONAL 실험 없음). auditor 재실험 요청 4건은 다음 사이클로 이월.

## 6. 통합 게이트

- `pytest -q`: **870 passed, 2 skipped**.
- `verify.py --all`: **실행 거부(권한)** → 게이트 미완, 머지 보류.

## 7. 미해결 (requests.jsonl 이월)

- R2 장부 재생 — 3사이클째 실행 거부. 실행 전 임계 3%p·재생 z 추가 필요. 사전 예측: holy_grail ≈ −0.6%(±1%p), ftlt_1x 차이 <0.5%p.
- R4 hot_rvol_swing 스캔(`experiments/2026-11-26_hot_rvol_scan.py`) — 실행 거부. 정적 검토: `decide()`에 최신성 검사 없음(잠재 버그).
- holy_grail RSI 임계 이웃 진단, ftlt_1x 11-18 SPY 200일선 여유폭 로그.
- mom_top5_ndx 자동 리포트 표시 괴리.

## 8. 사용자 결정 필요

- U1~U6 (11-25에서 이월: hibeta v2 원장, R2 반영 방식, mom_top5_ndx 리셋, 부칙 R5, Codex 업그레이드, 재생 스크립트 실행 허용)
- **U7 늦은 봉 수정 PR 머지 승인** — `verify.py --all` 를 돌려 통과하면 머지.
- **실행 권한:** `PYTHONPATH=src .venv/bin/python experiments/*.py`, `python scripts/harness/verify.py` 를 루프 세션 허용목록에 추가해야 R2/R4·게이트가 돈다. hibeta 군 RETIRED 도달(≈12-23) 전에 R2가 필요.
