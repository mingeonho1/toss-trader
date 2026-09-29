# 오늘의 판단 (데일리 결정 엔진)

- 생성: `2026-09-29T05:20:37+00:00`  ·  ET 세션: `2026-09-28`  ·  장부: `/Users/mingh/github/toss-trader/data/paperlab`
- 결정론적 · LLM 없음 · **주문 없음** · 멱등(같은 세션 날짜 재실행 = 동일 결과).
- 규칙: `docs/loop/decision_rules.md` (사전등록 2026-09-29). 기대치: `docs/loop/backtest_expectations.json`.

## 오늘의 판단

- **실계좌 추천 없음 — 기본 DCA 유지 (CANDIDATE 이상 전략 없음)**
  - 관찰 선두(추천 아님): `mom_top5_ndx` — 상태 WARMUP
- **레버ETP 관찰 선두(추천 아님)**: `holy_grail` — 상태 WARMUP

## 오늘 상태 변화

- (상태 변화 없음.)

### 실계좌 (12)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `buffer_hibeta` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `ep_gap_swing` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `ftlt_1x` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `ftlt_hibeta` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `ftlt_hibeta_psq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `hibeta_basket` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `holygrail_hibeta` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `lrs200_qqq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `mom_top5_ndx` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `overnight_qqq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `qqq_bh` (벤치) | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `simple_hibeta` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |

### 레버ETP (14)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ftlt` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `ftlt_moc` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `holy_grail` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `hot_rvol_swing` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `lrr_tqqq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `lrr_tqqq_sqqq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `lrs200_tqqq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `nine_sig` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `overnight_tqqq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `rsi2_tqqq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `simple_rsi_uvxy` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `sma200_buffer_tqqq` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `tqqq_bh` (벤치) | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |
| `voltarget_3x` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |

### 탐색 (1)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | WARMUP | 1 | +0.00% | n/a | n/a | n/a | n/a | +0.00% | n/a | 0.00 |

## 방출된 작업요청 (data/loop/requests.jsonl)

- (트리거된 요청 없음.)

## 주의

- 상태·지표는 페이퍼 장부만으로 산출(실주문 없음). 실거래 전환은 부록 v3대로 사용자 승인 필요.
- vs QQQ/TQQQ 는 **누적 로그 초과**. z 는 백테스트 기대 대비 표준화 점수(z<0 = 기대 미달).

## 🤖 LLM 판단 (gpt-6-astra)

- 상태: **채택(accepted)** · 모델 `gpt-6-astra` · 시도 1회 · prompt_hash `6226175c02968fe0`
- 입장(stance): **hold_dca** · 추천 전략: `None` · 슬리브 0.0% · confidence 1
- 무엇이 바뀌었나: 평가 세션은 2026-09-28이고 번들 생성일은 2026-09-29이다. history_last10이 비어 있어 전일 대비 성과·상태 변화를 비교할 수 없다. changes가 빈 배열인 것은 기록된 전이가 없다는 뜻이며 전일과 동일함을 입증하지 않는다. 현재 자료는 관측 수 n=1인 초기 스냅샷으로, 성과 개선이나 악화는 확인되지 않는다.
- 판단 근거: 모든 전략이 WARMUP이며 관측 수는 n=1이다. 추천 자격인 CANDIDATE 또는 LIVE_READY를 충족하는 실계좌 전략이 없으므로 전략 배분을 보류한다. mom_top5_ndx는 내부 추천 필드에 표시되어 있지만 actionable=false이고 목표 비중도 없어 추천 근거로 사용할 수 없다. 누적수익률과 최대낙폭이 모두 0이고 초과수익률·변동성·z가 미산출 상태이므로 백테스트 대비 괴리의 존재나 원인을 판정할 수 없다. 이를 노이즈·수수료·레짐 중 하나로 단정하지 않는다. 추가 강등 근거도 없어 상태는 유지한다. confidence는 추천 보류의 규칙상 확실성을 의미하며 미래 성과에 대한 확신이 아니다. 실주문은 실행하지 않는다.
- 관찰 항목:
  - 초기 평가액만 기록된 것인지 실제 수익률 관측 구간이 포함된 것인지 확인하고, 후속 세션의 데이터 연속성을 점검한다.
  - 실계좌 전략의 결정론적 상태가 CANDIDATE 또는 LIVE_READY에 도달하기 전에는 추천하지 않는다.
  - 체결·회전율·비용 자료가 확보되면 번들에 제시된 매수 면제 조건, 매도 최소 비용, 환전 비용의 반영 여부를 확인한다.
  - 백테스트와 관측 성과의 체결 시점 및 비용 기준이 일치하는지 확인한 뒤 괴리 원인을 평가한다.
  - 레버리지 그룹과 명시된 추천·참고 제외 대상은 성과와 무관하게 추천 대상에서 제외한다.
- 적용된 강등: 없음
- LLM 방출 요청(requests.jsonl · source=llm):
  - `audit` [medium]  — 전략별 n=1과 일률적인 누적수익률·최대낙폭 0이 정상적인 초기 기준점 기록인지 확인한다. 실제 수익률 구간, 가격 누락 여부, 체결 및 비용 반영 기준을 점검해 후속 괴리 평가의 기준을 확립한다.
