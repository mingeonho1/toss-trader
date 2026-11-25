# 오늘의 판단 (데일리 결정 엔진)

- 생성: `2026-11-25T21:27:31+00:00`  ·  ET 세션: `2026-11-25`  ·  장부: `/Users/mingh/github/toss-trader/data/paperlab`
- 결정론적 · LLM 없음 · **주문 없음** · 멱등(같은 세션 날짜 재실행 = 동일 결과).
- 규칙: `docs/loop/decision_rules.md` (사전등록 2026-09-29). 기대치: `docs/loop/backtest_expectations.json`.

## 오늘의 판단

- **실계좌 추천 없음 — 기본 DCA 유지 (CANDIDATE 이상 전략 없음)**
  - 관찰 선두(추천 아님): `mom_top5_ndx` — 상태 EVALUATING
- **레버ETP 관찰 선두(추천 아님)**: `holy_grail` — 상태 EVALUATING

## 오늘 상태 변화

- (상태 변화 없음.)

### 실계좌 (12)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mom_top5_ndx` | EVALUATING | 43 | +0.00% | +0.00% | +15.29% | +53.81% | +0.00% | +0.00% | 0.88 | 0.00 |
| `ftlt_1x` | EVALUATING | 43 | -3.90% | -21.24% | +11.31% | +49.83% | +34.67% | -15.26% | 1.28 | 0.52 |
| `ep_gap_swing` | EVALUATING | 43 | -6.16% | -31.73% | +8.93% | +47.45% | +13.20% | -7.15% | 0.83 | 0.16 |
| `overnight_qqq` | EVALUATING | 43 | -6.67% | -33.93% | +8.38% | +46.90% | +25.69% | -10.88% | 2.06 | 0.14 |
| `qqq_bh` (벤치) | EVALUATING | 43 | -14.18% | -60.04% | +0.00% | +38.52% | +34.83% | -19.90% | 0.00 | 0.57 |
| `lrs200_qqq` | EVALUATING | 43 | -14.82% | -61.79% | -0.75% | +37.77% | +23.82% | -17.17% | -0.08 | 0.68 |
| `holygrail_hibeta` | EVALUATING | 43 | -24.61% | -81.64% | -12.96% | +25.56% | +54.86% | -35.27% | -1.34 | 0.68 |
| `buffer_hibeta` | DEMOTED | 43 | -30.43% | -88.66% | -20.99% | +17.53% | +50.22% | -34.58% | -1.96 | 0.94 |
| `ftlt_hibeta` | DEMOTED | 43 | -33.75% | -91.55% | -25.89% | +12.63% | +60.36% | -36.17% | -2.36 | 0.70 |
| `simple_hibeta` | DEMOTED | 43 | -34.68% | -92.23% | -27.30% | +11.22% | +60.25% | -36.99% | -2.36 | 0.56 |
| `ftlt_hibeta_psq` | DEMOTED | 43 | -34.91% | -92.40% | -27.66% | +10.86% | +63.94% | -37.28% | -2.17 | 0.75 |
| `hibeta_basket` | DEMOTED | 43 | -39.36% | -95.03% | -34.74% | +3.78% | +62.43% | -41.51% | -2.96 | 0.63 |

### 레버ETP (14)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `holy_grail` | EVALUATING | 43 | +9.19% | +69.49% | +24.08% | +62.60% | +120.21% | -46.73% | 0.73 | 0.72 |
| `hot_rvol_swing` | EVALUATING | 43 | +0.00% | +0.00% | +15.29% | +53.81% | +0.00% | +0.00% | 0.89 | 0.00 |
| `overnight_tqqq` | EVALUATING | 43 | -4.38% | -23.55% | +10.82% | +49.34% | +73.80% | -23.02% | 1.24 | 0.28 |
| `rsi2_tqqq` | EVALUATING | 43 | -14.77% | -61.67% | -0.69% | +37.83% | +64.00% | -23.88% | -0.06 | 0.99 |
| `ftlt` | EVALUATING | 43 | -16.75% | -66.71% | -3.04% | +35.48% | +109.45% | -41.95% | -0.33 | 0.63 |
| `voltarget_3x` | EVALUATING | 43 | -16.85% | -66.95% | -3.16% | +35.36% | +34.42% | -21.48% | -0.69 | 0.61 |
| `nine_sig` | EVALUATING | 43 | -25.84% | -83.36% | -14.60% | +23.92% | +54.71% | -32.80% | -1.09 | 0.45 |
| `ftlt_moc` | EVALUATING | 43 | -28.62% | -86.77% | -18.43% | +20.09% | +115.07% | -48.09% | -1.25 | 0.86 |
| `lrr_tqqq_sqqq` | EVALUATING | 43 | -30.03% | -88.26% | -20.42% | +18.10% | +106.68% | -47.37% | -0.68 | 0.72 |
| `lrr_tqqq` | EVALUATING | 43 | -32.68% | -90.69% | -24.28% | +14.24% | +71.60% | -40.11% | -1.82 | 0.81 |
| `sma200_buffer_tqqq` | EVALUATING | 43 | -32.68% | -90.69% | -24.28% | +14.24% | +71.60% | -40.11% | -1.80 | 0.85 |
| `lrs200_tqqq` | DEMOTED | 43 | -40.47% | -95.55% | -36.58% | +1.94% | +74.02% | -45.21% | -2.63 | 0.74 |
| `simple_rsi_uvxy` | DEMOTED | 43 | -41.61% | -96.04% | -38.52% | +0.00% | +101.58% | -51.10% | -2.19 | 0.63 |
| `tqqq_bh` (벤치) | DEMOTED | 43 | -41.61% | -96.04% | -38.52% | +0.00% | +101.58% | -51.10% | -2.18 | 0.63 |

### 탐색 (1)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | EVALUATING | 43 | -30.47% | -88.70% | -21.05% | +17.47% | +58.99% | -32.22% | -1.21 | 0.67 |

## 방출된 작업요청 (data/loop/requests.jsonl)

- `investigate_divergence` {"strategy": "ftlt_hibeta", "z": -2.3594}
- `investigate_divergence` {"strategy": "ftlt_hibeta_psq", "z": -2.1699}
- `investigate_divergence` {"strategy": "hibeta_basket", "z": -2.9568}
- `investigate_divergence` {"strategy": "lrs200_tqqq", "z": -2.6323}
- `investigate_divergence` {"strategy": "simple_hibeta", "z": -2.3633}
- `investigate_divergence` {"strategy": "simple_rsi_uvxy", "z": -2.19}

## 주의

- 상태·지표는 페이퍼 장부만으로 산출(실주문 없음). 실거래 전환은 부록 v3대로 사용자 승인 필요.
- vs QQQ/TQQQ 는 **누적 로그 초과**. z 는 백테스트 기대 대비 표준화 점수(z<0 = 기대 미달).

## 🤖 LLM 판단 (gpt-6-astra)

- 상태: **불가(unavailable)** · 모델 `gpt-6-astra` · 시도 1회 · prompt_hash `570ad040f6a3af08`
- codex(GPT-6) 호출 불가(미로그인/타임아웃/오프라인 등) — 결정론적 판단이 그대로 유지된다.
  - 사유: rc=1: OR: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The 'gpt-6-astra' model requires a newer version of Codex. Please upgrade to the latest app or CLI and try again."}}
- 에이전트 루프 킥: kicked (gui/501/com.tosstrader.agentloop)
