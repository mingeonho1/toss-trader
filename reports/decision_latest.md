# 오늘의 판단 (데일리 결정 엔진)

- 생성: `2026-09-29T02:17:50+00:00`  ·  ET 세션: `2026-09-28`  ·  장부: `/Users/mingh/github/toss-trader/data/paperlab`
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
