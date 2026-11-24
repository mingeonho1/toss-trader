# 오늘의 판단 (데일리 결정 엔진)

- 생성: `2026-11-24T23:09:14+00:00`  ·  ET 세션: `2026-11-24`  ·  장부: `/Users/mingh/github/toss-trader/data/paperlab`
- 결정론적 · LLM 없음 · **주문 없음** · 멱등(같은 세션 날짜 재실행 = 동일 결과).
- 규칙: `docs/loop/decision_rules.md` (사전등록 2026-09-29). 기대치: `docs/loop/backtest_expectations.json`.

## 오늘의 판단

- **실계좌 추천 없음 — 기본 DCA 유지 (CANDIDATE 이상 전략 없음)**
  - 관찰 선두(추천 아님): `mom_top5_ndx` — 상태 EVALUATING
- **레버ETP 관찰 선두(추천 아님)**: `holy_grail` — 상태 EVALUATING

## 오늘 상태 변화

| 전략 | 그룹 | 어제 | 오늘 | 사유 |
|---|---|---|---|---|
| `buffer_hibeta` | 실계좌 | EVALUATING | **DEMOTED** | z -2.03 < -2.0 at n=42≥42 |
| `ftlt_hibeta` | 실계좌 | EVALUATING | **DEMOTED** | z -2.44 < -2.0 at n=42≥42 |
| `ftlt_hibeta_psq` | 실계좌 | EVALUATING | **DEMOTED** | z -2.24 < -2.0 at n=42≥42 |
| `hibeta_basket` | 실계좌 | EVALUATING | **DEMOTED** | z -2.96 < -2.0 at n=42≥42 |
| `lrs200_tqqq` | 레버ETP | EVALUATING | **DEMOTED** | z -2.71 < -2.0 at n=42≥42 |
| `simple_hibeta` | 실계좌 | EVALUATING | **DEMOTED** | z -2.34 < -2.0 at n=42≥42 |
| `simple_rsi_uvxy` | 레버ETP | EVALUATING | **DEMOTED** | z -2.14 < -2.0 at n=42≥42 |
| `tqqq_bh` | 레버ETP | EVALUATING | **DEMOTED** | z -2.13 < -2.0 at n=42≥42 |

### 실계좌 (12)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mom_top5_ndx` | EVALUATING | 42 | +0.00% | +0.00% | +14.63% | +51.80% | +0.00% | +0.00% | 0.84 | 0.00 |
| `overnight_qqq` | EVALUATING | 42 | -3.32% | -18.73% | +11.25% | +48.42% | +24.59% | -10.88% | 2.45 | 0.14 |
| `ftlt_1x` | EVALUATING | 42 | -3.90% | -21.70% | +10.65% | +47.82% | +35.09% | -15.26% | 1.22 | 0.52 |
| `ep_gap_swing` | EVALUATING | 42 | -7.15% | -36.61% | +7.21% | +44.38% | +13.01% | -7.15% | 0.70 | 0.16 |
| `qqq_bh` (벤치) | EVALUATING | 42 | -13.61% | -59.31% | +0.00% | +37.17% | +35.25% | -19.90% | 0.00 | 0.57 |
| `lrs200_qqq` | EVALUATING | 42 | -14.82% | -62.68% | -1.41% | +35.76% | +24.09% | -17.17% | -0.18 | 0.68 |
| `holygrail_hibeta` | EVALUATING | 42 | -24.69% | -82.50% | -13.73% | +23.44% | +55.49% | -35.27% | -1.41 | 0.68 |
| `buffer_hibeta` | DEMOTED | 42 | -30.43% | -89.24% | -21.65% | +15.52% | +50.79% | -34.58% | -2.03 | 0.94 |
| `ftlt_hibeta` | DEMOTED | 42 | -33.83% | -92.10% | -26.66% | +10.51% | +61.03% | -36.17% | -2.44 | 0.70 |
| `simple_hibeta` | DEMOTED | 42 | -33.86% | -92.12% | -26.71% | +10.46% | +60.98% | -36.20% | -2.34 | 0.54 |
| `ftlt_hibeta_psq` | DEMOTED | 42 | -34.99% | -92.91% | -28.43% | +8.74% | +64.65% | -37.28% | -2.24 | 0.75 |
| `hibeta_basket` | DEMOTED | 42 | -38.76% | -95.09% | -34.42% | +2.75% | +63.18% | -40.93% | -2.96 | 0.62 |

### 레버ETP (14)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `holy_grail` | EVALUATING | 42 | +9.06% | +70.46% | +23.31% | +60.48% | +121.67% | -46.73% | 0.72 | 0.72 |
| `overnight_tqqq` | EVALUATING | 42 | +6.34% | +45.93% | +20.78% | +57.95% | +69.87% | -23.02% | 1.99 | 0.28 |
| `hot_rvol_swing` | EVALUATING | 42 | +0.00% | +0.00% | +14.63% | +51.80% | +0.00% | +0.00% | 0.86 | 0.00 |
| `rsi2_tqqq` | EVALUATING | 42 | -14.77% | -62.56% | -1.35% | +35.82% | +64.77% | -23.88% | -0.12 | 0.99 |
| `voltarget_3x` | EVALUATING | 42 | -16.55% | -67.11% | -3.46% | +33.71% | +34.84% | -21.48% | -0.76 | 0.61 |
| `ftlt` | EVALUATING | 42 | -16.85% | -67.83% | -3.82% | +33.35% | +110.77% | -41.95% | -0.36 | 0.63 |
| `nine_sig` | EVALUATING | 42 | -25.23% | -83.25% | -14.44% | +22.73% | +55.38% | -32.80% | -1.09 | 0.45 |
| `ftlt_moc` | EVALUATING | 42 | -28.70% | -87.50% | -19.20% | +17.97% | +116.44% | -48.09% | -1.29 | 0.86 |
| `lrr_tqqq_sqqq` | EVALUATING | 42 | -31.41% | -90.15% | -23.07% | +14.10% | +107.74% | -47.37% | -0.78 | 0.72 |
| `lrr_tqqq` | EVALUATING | 42 | -32.68% | -91.22% | -24.95% | +12.22% | +72.43% | -40.11% | -1.88 | 0.81 |
| `sma200_buffer_tqqq` | EVALUATING | 42 | -32.68% | -91.22% | -24.95% | +12.22% | +72.43% | -40.11% | -1.87 | 0.85 |
| `simple_rsi_uvxy` | DEMOTED | 42 | -40.43% | -95.86% | -37.17% | +0.00% | +102.80% | -51.10% | -2.14 | 0.63 |
| `tqqq_bh` (벤치) | DEMOTED | 42 | -40.43% | -95.86% | -37.17% | +0.00% | +102.80% | -51.10% | -2.13 | 0.63 |
| `lrs200_tqqq` | DEMOTED | 42 | -40.47% | -95.88% | -37.24% | -0.07% | +74.85% | -45.21% | -2.71 | 0.74 |

### 탐색 (1)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | EVALUATING | 42 | -30.47% | -89.29% | -21.71% | +15.46% | +59.66% | -32.22% | -1.27 | 0.67 |

## 방출된 작업요청 (data/loop/requests.jsonl)

- `investigate_divergence` {"strategy": "buffer_hibeta", "z": -2.0328}
- `investigate_divergence` {"strategy": "ftlt_hibeta", "z": -2.4448}
- `investigate_divergence` {"strategy": "ftlt_hibeta_psq", "z": -2.2445}
- `investigate_divergence` {"strategy": "hibeta_basket", "z": -2.9605}
- `investigate_divergence` {"strategy": "lrs200_tqqq", "z": -2.7054}
- `investigate_divergence` {"strategy": "simple_hibeta", "z": -2.3389}
- `investigate_divergence` {"strategy": "simple_rsi_uvxy", "z": -2.1399}

## 주의

- 상태·지표는 페이퍼 장부만으로 산출(실주문 없음). 실거래 전환은 부록 v3대로 사용자 승인 필요.
- vs QQQ/TQQQ 는 **누적 로그 초과**. z 는 백테스트 기대 대비 표준화 점수(z<0 = 기대 미달).

## 🤖 LLM 판단 (gpt-6-astra)

- 상태: **불가(unavailable)** · 모델 `gpt-6-astra` · 시도 1회 · prompt_hash `2491212bb584cda1`
- codex(GPT-6) 호출 불가(미로그인/타임아웃/오프라인 등) — 결정론적 판단이 그대로 유지된다.
  - 사유: rc=1: OR: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The 'gpt-6-astra' model requires a newer version of Codex. Please upgrade to the latest app or CLI and try again."}}
- 에이전트 루프 킥: kicked (gui/501/com.tosstrader.agentloop)
