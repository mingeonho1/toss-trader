# 오늘의 판단 (데일리 결정 엔진)

- 생성: `2026-09-30T00:44:22+00:00`  ·  ET 세션: `2026-09-28`  ·  장부: `/Users/mingh/github/toss-trader/data/paperlab`
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

- 상태: **채택(accepted)** · 모델 `gpt-6-astra` · 시도 1회 · prompt_hash `1423cca345b6ff6e`
- 입장(stance): **hold_dca** · 추천 전략: `None` · 슬리브 0.0% · confidence 0.99
- 무엇이 바뀌었나: history_last10이 비어 있고 changes도 없어 어제 대비 상태·성과 변화를 확인할 수 없다. 이는 변화가 없었다는 뜻이 아니라 비교 자료가 없다는 뜻이다. 평가 세션은 2026-09-28이고 보고서 생성 시각은 2026-09-30T00:44:22+00:00이므로, 생성일의 시장 성과로 해석해서는 안 된다. 두 날짜의 차이가 정상적인 집계 시차인지 데이터 갱신 누락인지는 번들만으로 판단할 수 없다.
- 판단 근거: 모든 전략이 WARMUP이므로 추천에 필요한 CANDIDATE 또는 LIVE_READY 조건을 충족하는 실계좌 전략이 없다. mom_top5_ndx도 actionable=false이므로 추천하지 않는다. 관측치는 전략별 n=1이고 누적수익률과 최대낙폭은 0이며, 초과수익률·변동성·z는 산출되지 않았다. 따라서 백테스트 기대 대비 괴리를 측정하거나 노이즈·수수료·레짐으로 분류할 근거가 없다. 0이라는 값만으로 무위험이나 기대성과 달성을 뜻한다고 해석하지 않는다. 강등할 추가 근거도 없어 상태를 유지한다. confidence는 추천 보류 판단에 대한 확신이며 미래 수익률에 대한 확신이 아니다. 이 판단은 실주문을 발생시키지 않는다.
- 관찰 항목:
  - 이전 세션 이력과 유효 수익률 관측치가 축적되는지 확인한다.
  - 모든 전략의 n=1·누적수익률 0이 초기 기준점인지 실제 평가 결과인지 확인한다.
  - 매매·환전 내역과 비용 차감 전후 성과가 확보되기 전에는 수수료를 괴리 원인으로 단정하지 않는다.
  - 향후 추천은 제외 대상을 제거한 retail 그룹의 CANDIDATE 또는 LIVE_READY 전략에만 한정하며 사용자 승인 필요 원칙을 유지한다.
- 적용된 강등: 없음
- LLM 방출 요청(requests.jsonl · source=llm):
  - `audit` [medium]  — 평가 세션 2026-09-28과 생성 시각 2026-09-30T00:44:22+00:00의 차이가 정상인지 확인하고, 이력 부재 및 모든 전략의 n=1·누적수익률 0이 초기화 결과인지 데이터 누락인지 점검한다.
