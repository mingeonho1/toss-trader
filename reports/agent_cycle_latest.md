# 에이전트 팀 사이클 요약 — 2026-11-28 (weekly-sat)

- 브랜치: `loop/2026-11-28` · 결정 메모: `docs/loop/decisions/2026-11-28.md` · 감사: `reports/audits/2026-11-28_ledger-replay-gaps.md`
- **주문 없음 · 자동화 live 설치 없음 · 실운영 체크아웃 수정 없음.** 실계좌 전환은 사용자 승인 사항.

## 1. 기록 건전성 (운영 우선)

| 항목 | 결과 |
|---|---|
| paper-log 게시 | 11-24~11-28 매일 정상(11-26 추수감사절 휴장, 11-27 반일장 집계 정상). n 42→43→44 |
| LLM 판사 | `gpt-6-astra` 11-27 채택 → U5 해결 |
| **실운영 코드 버전 (CRITICAL)** | 매일 장부를 쓰는 `~/github/toss-trader`가 `upgrade/loop-engineering@92b7164`(#10)에 머물러 있다. **PR #11 수정(늦은 봉 대기, mom 최신성 필터, hibeta 월간 바스켓)은 운영에서 한 번도 돌지 않았다.** mom_top5_ndx가 0% 미체결로 남은 직접 원인이다. 루프는 이 체크아웃을 건드리지 않으므로 사용자 작업 U8로 넘긴다 |

## 2. 판단 변화 (페이퍼 근거, 세션 2026-11-27, n=44)

- 상태 전이 없음. **실계좌 추천 없음, 기본 DCA 유지**(LLM 판사도 hold_dca).
- R2 장부 재생을 처음으로 실행했다. 2%p 넘는 괴리 8건이 모두 **장부 아티팩트**였고 재생 버그는 0건이다.
  - `holy_grail`(레버ETP 관찰 선두): 장부 +9.12%는 11-09 TQQQ **신호** 봉 지연으로 TECL을 하루 더 보유한 허위 수익이었다. 재생은 −0.72%(z 0.61→0.22)라 **관찰 선두를 철회**한다.
  - `mom_top5_ndx`: 장부 0%는 한 번도 체결되지 않은 결과다. 스펙대로 재생하면 −38.8%(z −3.19)다. INVALID를 유지한다.
  - `overnight_tqqq`: +0.58%가 재생에서 −6.25%다. hibeta 3종은 11-20 봉 지연 아티팩트다. 오염 확정 4종(mom_top5_ndx, ftlt_hibeta_psq, simple_hibeta, overnight_tqqq)에는 주석만 단다(상태기계 불변).
- `overnight_qqq` z +1.93은 장부와 재생이 같아 버그가 아니다. 11-23 하룻밤을 빼면 z가 약 1.0이라 승격하지 않는다.
- 비용 통합 감사: 이중차감 없음. 주간 환전 0.05%는 paperlab에 아직 모델링돼 있지 않다.

## 3. 코드 수정 (이번 PR)

| ID | 내용 | 파일 |
|---|---|---|
| F2 | 늦은 봉 대기 대상을 보유·목표 심볼에서 **전략 universe 전체(신호 심볼 포함)**로 넓혔다. holy_grail 11-09가 재발하지 않는다. 장기 정지 심볼은 기존처럼 캐리포워드하므로 영구 정체는 없다 | `src/toss_trader/paperlab.py`, `tests/test_paperlab_late_bars.py` |
| F1(일부) | paperlab 리포트 Generated 줄에 `code <sha>@<branch>`를 기록한다. 운영 코드 버전을 확인하는 용도 | `scripts/paperlab_run.py` |
| F3 | 결정 엔진 `pick_top`에서 한 번도 노출되지 않은 장부(누적 0·변동성 0)를 선두 후보에서 뺀다. 표시 전용이고 상태 전이는 그대로다 | `scripts/loop/daily_decision.py`, `tests/test_daily_decision.py` |

- F2 적용 후 R2 재생(`reports/2026-11-28_ledger_replay_postF2.md`)은 적용 전과 같았고 정체 전략은 0개였다.

## 4. 편입/퇴출

- 편입 0, 퇴출 0. intel이 11-25로 최신이라 scout는 생략했다. 신규 카드도 없어 experimenter 실험도 없다.

## 5. 되돌려 보낸 횟수·사유

- 0회. 실험(PASS/CONDITIONAL)이 없어 auditor 반박 단계를 거치지 않았다. auditor의 수정 요청 F2는 executor가 1회 만에 통과했다.
- 실행 경로: 서브 계정(alt-agent)이 세션 권한에 막혀 Agent executor로 대신했다. U6의 원인은 `PYTHONPATH=` 접두였다. `.venv/bin/python <script>`로 실행해 해결했다.

## 6. 통합 게이트

- `verify.py --all`: 차단 0건(기존 F1/F11 경고 5건) · `pytest -q`: 871 passed, 2 skipped.

## 7. 미해결 (다음 사이클로, worktree `data/loop/requests.jsonl`)

- F4(누적 기준을 시작자본으로), F1 SHA 불일치 경보, R3 β 진단(**12-23 hibeta RETIRED 전에 필요**), R4 hot_rvol 스캔, R2-confirm(11-04~11-12 일별 덤프)
- 반복 요청은 그룹 G1(hibeta 흐름 A), G2(TQQQ 롱 흐름 B), G3(overnight_qqq + 비용 감사)으로 묶어 소비한다.

## 8. 사용자 결정 필요

- **U8 (최우선)**: 이번 PR 병합 후 `~/github/toss-trader`를 `main`으로 전환·갱신한다(현재 `upgrade/loop-engineering`, 리포트 파일 6개가 수정된 상태). 다음 리포트의 SHA가 main과 같은지 확인한다. U1·U3의 전제 조건이다.
- U1 hibeta v2 장부 / U3 mom_top5_ndx 리셋: 찬성 권고, U8 이후 시작.
- U2 재생 반영 방식: (a) 주석만(권고).
- U4 부칙 (i)(ii) 찬성, (iii)은 R3 후. 12-23 전에 결정이 필요하다.
- 해결: U5(Codex), U6(재생 실행), U7(F2로 흡수).
