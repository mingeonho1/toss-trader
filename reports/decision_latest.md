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

- 상태: **채택(accepted)** · 모델 `gpt-6-astra` · 시도 1회 · prompt_hash `ad1e9e5eb277f384`
- 입장(stance): **hold_dca** · 추천 전략: `None` · 슬리브 0.0% · confidence 1.0
- 무엇이 바뀌었나: 평가 세션은 2026-09-28이고 번들은 2026-09-29에 생성되었다. history_last10이 비어 있어 직전 세션과 비교할 수 없다. changes가 빈 배열이라는 사실은 기록된 변경이 없다는 뜻이지, 전일 대비 상태나 성과가 동일하다는 증거는 아니다. 초기 기준점 및 데이터·비용 반영 여부를 확인하는 audit 요청이 이미 열려 있어 중복 요청은 추가하지 않는다.
- 판단 근거: 所有 전략이 WARMUP이고 관측치가 n=1이므로 추천 요건인 CANDIDATE 또는 LIVE_READY를 충족하는 실계좌 전략이 없다. mom_top5_ndx도 actionable=false이므로 자동 선정 결과를 실계좌 추천으로 해석하지 않는다. 누적수익률과 최대낙폭은 모두 0이지만 연율 수익률·초과수익률·변동성·z가 산출되지 않아 백테스트 대비 괴리 자체를 평가할 수 없다. 따라서 노이즈·수수료·레짐 중 어느 원인으로도 귀속하지 않는다. 현재 자료만으로 추가 강등할 근거도 없다. confidence는 추천 보류 규칙 적용에 대한 확신이며 성과 전망이나 괴리 원인에 대한 확신이 아니다. 신규 전략 배분은 0으로 두며 실주문은 수행하지 않는다.
- 관찰 항목:
  - 기존 audit 결과에서 n=1과 수익률·최대낙폭 0이 의도된 초기 기준점인지, 평가 가격·포지션·거래 내역 누락이 있는지 확인한다.
  - 추가 세션에서 유효 수익률 관측치와 초과수익률·변동성·z가 산출되는지 확인한다. 최대낙폭 0을 저위험의 증거로 해석하지 않는다.
  - 제공된 비용 가정에 따라 매수 금액별 수수료, 매도 SEC/TAF 최소 비용, 주간 환전 비용이 반영되는지 기존 audit에서 확인한다. 거래·환전 내역 없이 비용 영향을 추정하지 않는다.
  - 실계좌 그룹의 결정론적 상태가 CANDIDATE 또는 LIVE_READY가 된 뒤에도 제외 대상 및 레버리지 ETP 편입 여부를 확인한다. LIVE_READY 역시 사용자 승인 필요 표시일 뿐이다.
- 적용된 강등: 없음
