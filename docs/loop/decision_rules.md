# 데일리 결정 엔진 — 사전등록 규칙 (PRE-REGISTERED 2026-09-29)

> **결과를 보기 전에 규칙을 고정한다.** 이 문서는 `scripts/loop/daily_decision.py` 가 매일
> 페이퍼 장부만으로 각 전략의 상태를 결정론적으로(LLM 없이) 판정하는 규칙의 사전등록본이다.
> 숫자는 **동결(frozen)** 이며, 변경은 아래 *부칙(Addenda)* 에 **날짜를 붙여** 추가한다.
> 이 규칙은 부록 v3 Lane A(“최종 판정 = 실시세 포워드 페이퍼 ≥3개월, 실거래는 사용자 승인 시에만”)를
> 매일 갱신되는 판단으로 구체화한다. **엔진은 절대 주문하지 않는다.**

메인 루프의 심장: **매일, 페이퍼 로그가 바뀌면 판단도 바뀐다.**

---

## 1. 입력 데이터 (전부 로컬·키 불필요)

- **페이퍼 장부**: `data/paperlab/{name}/state.json`(없으면 paper-log 체크아웃
  `data/_publish/state/paperlab/{name}/state.json` 폴백). 각 장부의 `equity`(= `[[iso_date, unit_eq, real_eq], …]`)
  에서 **단위($1k) 장부** 곡선을 쓴다.
- **벤치마크**: 같은 로스터의 `qqq_bh` · `tqqq_bh` 단위 장부 곡선(동일 브로커·수수료로 돌아간 QQQ/TQQQ 프록시).
  각 전략의 라이브 구간을 **날짜로 정렬**해 초과수익을 잰다. 벤치마크 자신(`qqq_bh`,`tqqq_bh`)은 후보·추천·scout 대상에서 제외.
- **백테스트 기대치(동결)**: `docs/loop/backtest_expectations.json` — 2021-01-01 이후 백필 리플레이(≈1439세션,
  단위장부 vs `qqq_bh`, 토스 수수료, 신호 t→체결 t+1)에서 산출한 전략별
  `exp_annual_log_excess_vs_qqq`(연 기대 로그 초과), `tracking_vol_annual`(연 트래킹오차 vol), `backtest_mdd`.
  hibeta/*_psq 는 PIT S&P500 커버리지(~27–31%) 한계로 **상한(upper bound)** 임을 명시.
- **그룹**: `toss_trader.paperlab.classify_group`(선언 그룹 우선, 없으면 유니버스의 레버리지 ETP 포함 여부).
  `실계좌`(retail) · `레버ETP`(leverage) · `탐색`(explore).

세션 날짜 = 장부 곡선의 마지막 거래일(ET). 같은 세션 날짜로 재실행하면 **동일 결과**(멱등, 날짜키 upsert).

---

## 2. 전략별 지표 (라이브 페이퍼 장부에서)

전략의 단위장부 곡선을 라이브 구간 `D = [d₀ … d_{n-1}]`(n = 라이브 세션 수)이라 하자.

- **n (live sessions)** = 곡선 점 개수.
- **누적수익(cumulative)** = `equity[-1]/equity[0] − 1`.
- **연환산(annualized)** = `(equity[-1]/equity[0])^(252/(n−1)) − 1` (거래일 252 기준).
- **QQQ 대비 초과 / TQQQ 대비 초과** = 라이브 구간에서 **누적 로그수익 − 벤치 누적 로그수익**
  (날짜 정렬; 벤치가 그 날짜에 없으면 그 스텝 제외). 이하 `excess_qqq`, `excess_tqqq`.
- **라이브 vol** = 일간 로그수익 표준편차 × √252.
- **최대낙폭(live MDD)** = 단위장부 곡선 peak-to-trough(음수).
- **기대 z-score** (백테스트 기대 대비 라이브 초과의 표준화):

  ```
  z = ( excess_qqq(로그, 누적)  −  exp_annual_log_excess_vs_qqq × n/252 )
      / ( tracking_vol_annual × sqrt(n/252) )
  ```

  분자 = 라이브 누적 로그초과 − 백테스트가 기대하는 같은 세션수의 누적 로그초과.
  분모 = 랜덤워크 가정하 n세션 누적초과의 표준편차. **z≈0 = 백테스트대로, z<0 = 기대 미달.**
- **DD 비율(drawdown ratio)** = `live_MDD / backtest_mdd` (둘 다 음수 → 양수 비율). 예: 라이브 −40% / 백테스트 −30% = 1.33.
  `backtest_mdd`≈0 이면(벤치 등) 비율 판정을 건너뛴다(보수적: DD 킬 미적용).

n<2 또는 벤치 정렬 스텝<2 면 vol·z·excess 는 `None`(WARMUP 단계라 판정에 영향 없음).

---

## 3. 상태 기계 (히스테리시스 K=5, 킬 조건은 즉시 강등)

세션수 밴드(데이터 충분성)와 품질조건을 결합한다. **승격/비킬 강등은 조건이 K=5 연속 세션 유지될 때만
전이**(플립플롭 억제). **킬 조건은 즉시**(연속 카운터 무시). 상태는 각 전략의 전체 곡선을 세션순으로
리플레이해 결정론적으로 재계산한다(영속 상태에 의존하지 않음 → 완전 멱등).

| 상태 | 진입 조건 | 히스테리시스 |
|---|---|---|
| **WARMUP** | `n < 21` | 즉시(단조·플립플롭 불가) |
| **EVALUATING** | `21 ≤ n < 63` | 즉시(세션수 밴드) |
| **CANDIDATE** | `n ≥ 63` **및** `excess_qqq > 0` **및** `z > −1` **및** `DD_ratio ≤ 1.0` | 조건 **5연속** 유지 시 진입; 조건 실패 **5연속** 시 EVALUATING 로 복귀 |
| **LIVE_READY** | CANDIDATE 를 **≥21 연속 세션** 유지 **및** 그룹 내 `excess_qqq` **1위** | 조건 **5연속** 유지 시 진입. **사용자 승인 필요 — 엔진은 절대 거래하지 않음** |
| **DEMOTED** | `DD_ratio > 1.25` **또는** (`z < −2` 且 `n ≥ 42`) **또는** (`excess_qqq < −15%` 且 `n ≥ 63`) | **즉시**(킬, 카운터 무시) |
| **RETIRED** | DEMOTED 를 **21 연속 세션** 유지 | 즉시(카운터 만료) |

- 킬 조건이 참이면 CANDIDATE·EVALUATING·WARMUP 무관하게 그 세션에 즉시 **DEMOTED**.
- DEMOTED 상태에서 킬 조건이 모두 해소되면 다음 세션에 EVALUATING(n 밴드) 또는 WARMUP 로 복귀
  가능하되, 복귀도 K=5 확인을 받는다(플립플롭 억제). RETIRED 는 종착(수동 리셋 전까지 유지).
- LIVE_READY 는 “권고”일 뿐이다. 실거래 전환은 **부록 v3**대로 사용자 승인이 있어야 하며, 이 엔진은
  주문 API 를 절대 호출하지 않는다.

### 숫자 근거(간략)
- **21 ≈ 1 거래월**: vol/z 추정에 필요한 최소 표본. **63 ≈ 1 분기** = Lane A “≥3개월 포워드” 요건과 정렬.
- **K=5 ≈ 1 거래주**: 한 주 노이즈로 상태가 뒤집히지 않게. **LIVE_READY +21** = 후보 안정성 재확인(추가 1개월).
- **z>−1**(1σ 이내 = 백테스트와 일관) 로 후보 자격, **z<−2**(2σ 미달 = 명백한 괴리) 로 강등.
- **DD_ratio ≤1.0**(라이브 낙폭이 백테스트 이하) 로 후보 자격, **>1.25**(25% 악화 = 위험모델 붕괴) 로 즉시 킬.
- **excess −15%**: 한 분기 지나도 QQQ를 15%p 밑돌면 설계 우위 소멸로 간주(하드 플로어).

---

## 4. 작업요청 방출 (`data/loop/requests.jsonl`, 날짜키 upsert = 멱등)

매 세션 상태에서 결정론적으로 재계산(같은 세션 날짜 재실행 시 중복 없음). 방출 규칙:

- **audit** — 어떤 전략이 오늘 `DD_ratio > 1.25`(낙폭 킬) 또는 `excess_qqq < −15%`(n≥63) 로 강등:
  `{"type":"audit","strategy":name,"reason":"DD ratio 1.30 > 1.25"}`.
- **investigate_divergence** — `z < −2` 且 `n ≥ 42` 인 전략:
  `{"type":"investigate_divergence","strategy":name,"z":z}`.
- **scout** — `실계좌` 그룹의 (벤치 제외) 전략이 모두 `n ≥ 63` 인데 CANDIDATE/LIVE_READY 가 하나도 없음:
  `{"type":"scout","reason":"no CANDIDATE in 실계좌 group after 63 sessions"}`.
- **regime_note** — 오늘 QQQ 일간 로그수익 `|r| ≥ 0.03`(≥3% 급변 = 레짐 충격):
  `{"type":"regime_note","qqq_return":r,"reason":"QQQ moved 3%+ today"}`.

각 레코드에 `session_date` 를 포함한다. 재실행 시 그 날짜의 기존 요청을 제거 후 다시 쓴다.

---

## 5. 오늘의 판단 (`reports/decision_latest.md`, 한국어)

- **그룹별 리더보드**: 각 전략의 상태·n·누적·연환산·excess(QQQ/TQQQ)·vol·MDD·z·DD비율.
- **오늘 상태 변화**: 어제 대비 상태가 바뀐 전략(WARMUP→…, →DEMOTED 등)과 사유.
- **오늘의 판단**:
  - `실계좌` 그룹(벤치 제외)에서 **추천 실계좌 전략** 1개 — 상태 우선순위
    `LIVE_READY > CANDIDATE > EVALUATING > WARMUP > DEMOTED > RETIRED`, 동순위는 라이브 `excess_qqq`
    내림차순(라이브 데이터 부족 시 동결 `backtest_cagr` 를 사전순위로 폴백)로 선정.
  - 참고로 `레버ETP` 그룹의 동일 기준 최상위 1개도 병기.
  - 각 추천에 **현재 상태 · 어제 대비 변화 사유 · 오늘의 dry-run 목표비중**(= `run_strategy` 와 동일한
    `compute_target_weights` 코드로 최신 종가에서 산출; **주문 없음**)을 표기.

`data/loop/decisions.jsonl` 에 하루 1레코드(날짜키 upsert)로 전체 상태·지표·추천을 기록한다.

---

## 부칙 (Addenda) — 변경 이력

- 2026-09-29 최초 사전등록.
- 2026-09-29 부록 B: LLM 판단 레이어(GPT-6 · Codex CLI) 추가 — 결정론 백본 위 해석 레이어.

### 부록 A — 2026-09-29, 첫 실측 이전 확정

오케스트레이터 결정(포워드 실측 데이터가 쌓이기 전 확정). 근거: **1일치 노이즈(WARMUP n=1)로 전략을
추천하지 않는다.** 아래를 §5 “오늘의 판단”에 우선 적용한다.

1. **실행 가능(actionable) 추천은 상태가 CANDIDATE 또는 LIVE_READY 일 때만.** 그 외에는 실계좌 행이
   `추천 없음 — 기본 DCA 유지 (CANDIDATE 이상 전략 없음)` 로 표기하고, 그룹 선두는 **`관찰 선두(추천 아님)`**
   로 별도 표기한다(상태만; 실행 목표비중은 산출하지 않는다).
2. **추천·참고 대상에서 제외**: 벤치마크(`qqq_bh`/`tqqq_bh`), **낙관적/라벨 변이 = 이름이 `_moc` 로 끝나는
   전략**(같은-종가 체결 가정이라 실집행 불가), **탐색(explore) 그룹.** 리더보드 표에는 계속 표시하되
   추천/참고 선두 선정에서만 뺀다.
3. `레버ETP` 는 실계좌 대상이 아니므로(예탁금·교육·승인 요건) 항상 `관찰 선두(추천 아님)` 로만 표기한다.

### 부록 B — 2026-09-29, LLM 판단 레이어 (GPT-6 · Codex CLI)

결정론 엔진(§1–§5)은 **감사 가능한 백본**으로 그대로 두고, 그 위에 GPT-6(로컬 `codex` CLI,
`gpt-6-astra`)을 매일 해석·판단 레이어로 얹는다. 구현: `scripts/loop/llm_judge.py`, `daily_scoreboard`
의 `daily_decision` **직후** 격리 단계 `llm_judge`. **엔진과 동일하게 절대 주문하지 않으며**, codex 는
항상 `--sandbox read-only` 로만 호출하고 승인우회/쓰기 샌드박스 플래그를 넘기지 않는다(하네스 F12 정적 스캔).

1. **컨텍스트 번들(JSON·compact·비밀 없음)**: 오늘 결정론 산출(전 전략 state/지표), 최근 10일
   `decisions.jsonl`, 열린 `requests.jsonl`, 동결 `backtest_expectations.json`, 최신 정찰 인텔
   (`docs/pipeline/intel/`, 있으면), 한국 리테일 하드 제약(레버 ETP 예탁금 ₩1,000만·≤$10 무료 매수·
   매도 SEC/TAF $0.01·환전 0.05%·무주문).
2. **구조화 출력(`--output-schema`)**: `{stance(hold_dca|recommend), recommended_strategy, sleeve_frac,
   confidence, state_overrides[], rationale_ko, what_changed_ko, watch_items[], requests[]}`.
3. **도메인 검증 (LLM 하네스 = 루프의 "fail → 되돌림")**:
   - `recommended_strategy` 는 null 이거나 **실계좌(retail)** 그룹이고 결정론 상태가 **CANDIDATE/LIVE_READY**.
     벤치마크·`_moc`·탐색(explore) 는 추천 불가. LLM 은 더 보수적일 수는 있어도 결코 덜 보수적일 수 없다.
   - `stance="recommend"` 는 유효한 `recommended_strategy` 필수. `sleeve_frac∈[0,0.5]`, `confidence∈[0,1]`.
   - `state_overrides` 는 **강등만**(예: CANDIDATE→EVALUATING, 무엇이든→DEMOTED; 승격·동일 금지). 언급 전략은
     모두 번들에 존재해야 한다. 근거에 번들에 없는 전략/지표 토큰이 있으면 경미 플래그(기각 아님).
   - 위반 시 사유를 붙여 **1~2회 재프롬프트**("이전 응답이 다음 규칙을 위반했다: …"), 그래도 실패면 결정론
     폴백 `llm_status="rejected"`. codex 실패/미로그인/타임아웃/오프라인 → `llm_status="unavailable"`,
     결정론 결과가 그대로 선다. 타임아웃 ~180s. 승인우회 플래그는 절대 넘기지 않는다.
4. **병합(보수적)**: 최종 = 결정론 ∘ 검증된 LLM. state_override 는 병합 뷰(`merged_states`)에 강등으로만
   반영하고 결정론 백본 파일(`decision_state.json`/`decisions.jsonl`)은 불변. LLM 요청은 `requests.jsonl` 에
   `source="llm"` 로 추가(세션별 멱등). 전량 기록: `data/loop/llm_judgments.jsonl`(프롬프트 해시·모델·
   원출력·검증오류·시도·상태·병합·킥; 비밀 없음). `reports/decision_latest.md` 끝에
   "🤖 LLM 판단 (gpt-6-astra)" 섹션(상태 accepted/rejected/unavailable·강등·관찰 항목).
5. **긴급 킥**: 병합 후 오늘 자 priority `high` 요청(규칙 또는 LLM)이 새로 있으면 `com.tosstrader.agentloop`
   launchd 잡이 **로드돼 있을 때만** `launchctl kickstart` 로 에이전트 팀 루프를 1회 깨운다(ET 날짜당 1회,
   `data/loop/kick_state.json` 영속). 규칙(결정론) 요청은 `audit`/`investigate_divergence` 를 고우선으로 본다.
