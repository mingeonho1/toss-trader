# 오늘의 판단 (데일리 결정 엔진)

- 생성: `2026-09-29T02:24:30+00:00`  ·  ET 세션: `2026-09-28`  ·  장부: `/Users/mingh/github/toss-trader/data/paperlab`
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

- 상태: **채택(accepted)** · 모델 `gpt-6-astra` · 시도 1회 · prompt_hash `7cd116a64291d8ae`
- 입장(stance): **hold_dca** · 추천 전략: `None` · 슬리브 0.0% · confidence 1
- 무엇이 바뀌었나: 판단 대상은 2026-09-28 세션이며 번들 생성일은 2026-09-29이다. history_last10이 비어 있어 전일 대비 성과·상태 변화를 비교할 수 없다. changes가 빈 배열이라는 사실은 기록된 변경이 없다는 뜻일 뿐, 전일과 동일함을 입증하지 않는다. 현재 번들에서는 모든 전략의 첫 관측과 WARMUP 상태만 확인된다.
- 판단 근거: 모든 전략이 WARMUP이므로 추천 가능한 CANDIDATE 또는 LIVE_READY 상태의 실계좌 전략이 없다. 자동 요약에 표시된 mom_top5_ndx도 actionable=false이고 목표 비중이 없어 추천 근거가 되지 않는다. 모든 전략의 관측 수는 1이며 누적수익률과 최대낙폭은 0, 연율화 수익률·초과수익률·변동성·z는 미산출 상태다. 따라서 백테스트 기대 대비 괴리를 측정하거나 노이즈·수수료·레짐 중 하나로 분류할 수 없다. 초기화 기준점인지 확인하기 전에는 0 수익률을 안정성이나 비용 부재의 증거로 해석하지 않는다. 추가 강등 근거도 없어 상태를 유지한다. confidence는 추천 보류가 규칙상 확실하다는 의미이며, 향후 성과에 대한 확신이 아니다. 전략 슬리브 배정은 0이며 실주문은 실행하지 않는다.
- 관찰 항목:
  - 후속 세션에서 유효 수익률 관측과 전일 이력이 축적되는지 확인한다.
  - mom_top5_ndx가 결정론적으로 CANDIDATE 또는 LIVE_READY에 도달하는지 확인하되, 도달 자체를 사용자 승인이나 주문 허가로 해석하지 않는다.
  - 제공된 비용 가정에 따라 매수 금액별 수수료, 매도 SEC/TAF 최소 비용, 환전 비용이 백테스트와 일별 평가에 일관되게 반영되는지 확인한다.
  - 괴리 판단에는 동일 기간·동일 수익률 정의의 기대값과 관측값, 거래·비용 내역이 필요하다. 자료가 확보되기 전에는 노이즈·수수료·레짐 원인을 단정하지 않는다.
- 적용된 강등: 없음
- LLM 방출 요청(requests.jsonl · source=llm):
  - `audit` [medium]  — 모든 전략의 n=1 및 누적수익률·최대낙폭 0이 의도된 초기 기준점인지 확인하고, 평가 가격·포지션·거래 내역 누락 여부와 제공된 비용 가정의 반영 여부를 점검한다. 현재 수치만으로 오류가 확인된 것은 아니다.
