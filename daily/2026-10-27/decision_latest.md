# 오늘의 판단 (데일리 결정 엔진)

- 생성: `2026-10-27T01:10:54+00:00`  ·  ET 세션: `2026-10-26`  ·  장부: `/Users/mingh/github/toss-trader/data/paperlab`
- 결정론적 · LLM 없음 · **주문 없음** · 멱등(같은 세션 날짜 재실행 = 동일 결과).
- 규칙: `docs/loop/decision_rules.md` (사전등록 2026-09-29). 기대치: `docs/loop/backtest_expectations.json`.

## 오늘의 판단

- **실계좌 추천 없음 — 기본 DCA 유지 (CANDIDATE 이상 전략 없음)**
  - 관찰 선두(추천 아님): `mom_top5_ndx` — 상태 EVALUATING
- **레버ETP 관찰 선두(추천 아님)**: `hot_rvol_swing` — 상태 EVALUATING

## 오늘 상태 변화

| 전략 | 그룹 | 어제 | 오늘 | 사유 |
|---|---|---|---|---|
| `btc_proxy_mstr_coin` | 탐색 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `buffer_hibeta` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `ep_gap_swing` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `ftlt` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `ftlt_1x` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `ftlt_hibeta` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `ftlt_hibeta_psq` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `ftlt_moc` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `hibeta_basket` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `holy_grail` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `holygrail_hibeta` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `hot_rvol_swing` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `lrr_tqqq` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `lrr_tqqq_sqqq` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `lrs200_qqq` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `lrs200_tqqq` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `mom_top5_ndx` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `nine_sig` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `overnight_qqq` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `overnight_tqqq` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `qqq_bh` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `rsi2_tqqq` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `simple_hibeta` | 실계좌 | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `simple_rsi_uvxy` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `sma200_buffer_tqqq` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `tqqq_bh` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |
| `voltarget_3x` | 레버ETP | WARMUP | **EVALUATING** | WARMUP→EVALUATING |

### 실계좌 (12)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mom_top5_ndx` | EVALUATING | 21 | +0.00% | +0.00% | +3.65% | +12.49% | +0.00% | +0.00% | 0.18 | 0.00 |
| `ftlt_1x` | EVALUATING | 21 | -3.58% | -36.85% | +0.00% | +8.85% | +18.37% | -7.27% | -0.01 | 0.25 |
| `lrs200_qqq` | EVALUATING | 21 | -3.58% | -36.85% | +0.00% | +8.85% | +18.37% | -7.27% | 0.03 | 0.29 |
| `qqq_bh` (벤치) | EVALUATING | 21 | -3.58% | -36.85% | +0.00% | +8.85% | +18.37% | -7.27% | 0.00 | 0.21 |
| `overnight_qqq` | EVALUATING | 21 | -4.80% | -46.19% | -1.27% | +7.58% | +12.50% | -6.72% | 0.43 | 0.08 |
| `ep_gap_swing` | EVALUATING | 21 | -5.15% | -48.62% | -1.64% | +7.21% | +10.87% | -5.15% | -0.11 | 0.11 |
| `buffer_hibeta` | EVALUATING | 21 | -16.79% | -90.13% | -14.73% | -5.88% | +43.10% | -20.48% | -1.91 | 0.56 |
| `ftlt_hibeta` | EVALUATING | 21 | -16.79% | -90.13% | -14.73% | -5.88% | +43.10% | -20.48% | -1.89 | 0.39 |
| `ftlt_hibeta_psq` | EVALUATING | 21 | -16.79% | -90.13% | -14.73% | -5.88% | +43.10% | -20.48% | -1.64 | 0.41 |
| `holygrail_hibeta` | EVALUATING | 21 | -16.79% | -90.13% | -14.73% | -5.88% | +43.10% | -20.48% | -1.91 | 0.39 |
| `simple_hibeta` | EVALUATING | 21 | -16.79% | -90.13% | -14.73% | -5.88% | +43.10% | -20.48% | -1.81 | 0.31 |
| `hibeta_basket` | EVALUATING | 21 | -16.79% | -90.13% | -14.73% | -5.89% | +43.20% | -20.49% | -1.82 | 0.31 |

### 레버ETP (14)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hot_rvol_swing` | EVALUATING | 21 | +0.00% | +0.00% | +3.65% | +12.49% | +0.00% | +0.00% | 0.29 | 0.00 |
| `voltarget_3x` | EVALUATING | 21 | -5.71% | -52.30% | -2.23% | +6.62% | +26.20% | -10.80% | -0.67 | 0.31 |
| `nine_sig` | EVALUATING | 21 | -7.10% | -60.45% | -3.71% | +5.13% | +33.51% | -13.37% | -0.43 | 0.18 |
| `overnight_tqqq` | EVALUATING | 21 | -8.27% | -66.30% | -4.98% | +3.86% | +37.28% | -15.61% | -0.21 | 0.19 |
| `rsi2_tqqq` | EVALUATING | 21 | -10.22% | -74.29% | -7.13% | +1.71% | +44.90% | -10.22% | -0.95 | 0.42 |
| `ftlt_moc` | EVALUATING | 21 | -11.18% | -77.54% | -8.20% | +0.64% | +55.88% | -21.32% | -0.83 | 0.38 |
| `ftlt` | EVALUATING | 21 | -11.74% | -79.28% | -8.85% | +0.00% | +55.76% | -21.32% | -0.67 | 0.32 |
| `holy_grail` | EVALUATING | 21 | -11.74% | -79.28% | -8.85% | +0.00% | +55.76% | -21.32% | -0.72 | 0.33 |
| `lrr_tqqq` | EVALUATING | 21 | -11.74% | -79.28% | -8.85% | +0.00% | +55.76% | -21.32% | -0.98 | 0.43 |
| `lrr_tqqq_sqqq` | EVALUATING | 21 | -11.74% | -79.28% | -8.85% | +0.00% | +55.76% | -21.32% | -0.42 | 0.33 |
| `lrs200_tqqq` | EVALUATING | 21 | -11.74% | -79.28% | -8.85% | +0.00% | +55.76% | -21.32% | -0.96 | 0.35 |
| `simple_rsi_uvxy` | EVALUATING | 21 | -11.74% | -79.28% | -8.85% | +0.00% | +55.76% | -21.32% | -0.77 | 0.26 |
| `sma200_buffer_tqqq` | EVALUATING | 21 | -11.74% | -79.28% | -8.85% | +0.00% | +55.76% | -21.32% | -0.97 | 0.45 |
| `tqqq_bh` (벤치) | EVALUATING | 21 | -11.74% | -79.28% | -8.85% | +0.00% | +55.76% | -21.32% | -0.74 | 0.26 |

### 탐색 (1)

| 전략 | 상태 | n | 누적 | 연환산 | vs QQQ | vs TQQQ | vol | MDD | z | DD비율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | EVALUATING | 21 | -12.89% | -82.43% | -10.15% | -1.31% | +62.18% | -15.08% | -0.83 | 0.31 |

## 방출된 작업요청 (data/loop/requests.jsonl)

- (트리거된 요청 없음.)

## 주의

- 상태·지표는 페이퍼 장부만으로 산출(실주문 없음). 실거래 전환은 부록 v3대로 사용자 승인 필요.
- vs QQQ/TQQQ 는 **누적 로그 초과**. z 는 백테스트 기대 대비 표준화 점수(z<0 = 기대 미달).

## 🤖 LLM 판단 (gpt-6-astra)

- 상태: **채택(accepted)** · 모델 `gpt-6-astra` · 시도 1회 · prompt_hash `d356ef2d962c2206`
- 입장(stance): **hold_dca** · 추천 전략: `None` · 슬리브 0.0% · confidence 0.99
- 무엇이 바뀌었나: 판단 대상은 2026-10-26 세션이며, 직전 관측 세션은 2026-10-23이다. 모든 전략이 WARMUP에서 EVALUATING으로 전환됐고, 직전 미결 요청에 기록된 n=20에서 현재 n=21로 증가했다. 이는 평가 단계 진입이지 성과 검증 완료나 실계좌 실행 허용이 아니다. 실계좌 표시 선두는 mom_top5_ndx로 유지됐고 actionable=false 및 target_weights=null도 그대로다. 직전 요청에 기록된 수치와 비교하면 buffer_hibeta의 누적수익률은 -17.56%에서 -16.79%로 약 0.775%p 회복했고, z는 -1.926에서 -1.906으로 소폭 개선됐지만 dd_ratio=0.556411은 그대로다. overnight_qqq의 누적수익률은 -4.98%에서 -4.80%로 약 0.185%p 회복했으나 excess_qqq는 -0.003626에서 -0.012697로 악화했고 z도 0.586에서 0.433으로 낮아졌다. 절대손실 축소와 상대성과 악화를 구분해야 한다. 다른 전략들은 직전 세션의 상세 성과가 제공되지 않아 일별 손익 변화를 판단하지 않는다.
- 판단 근거: 추천 가능한 결정론적 상태인 CANDIDATE 또는 LIVE_READY에 도달한 실계좌 전략이 없다. 따라서 기존 DCA 기조를 유지하고 전략 슬리브 신규 배정은 하지 않는다. confidence는 이 추천 보류 판단에 대한 확신이며, 괴리 원인의 확정도를 뜻하지 않는다. mom_top5_ndx는 표시상 실계좌 선두지만 EVALUATING이고 actionable=false이며, 누적수익률·변동성·MDD가 모두 0이다. 정상적인 미진입인지 데이터 문제인지 확인하기 전에는 운용 우위로 인정할 수 없다. buffer_hibeta는 누적수익률 -16.79%, z=-1.906으로 양의 백테스트 기대 대비 부진하다. 동일 성과를 보이는 실계좌 전략들의 공통 노출이 불리한 시장 구간과 맞물렸을 가능성을 우선 조사하되, n=21만으로 레짐 전환을 확정하지 않는다. 동일 성과를 독립적인 증거 여러 개로 계산해서도 안 된다. overnight_qqq의 z=0.433은 음의 백테스트 기대 대비 소폭 상회일 뿐, 누적손실이나 음의 상대성과를 뒤집는 구조적 개선의 증거가 아니다. 이 전략의 괴리는 단기 노이즈를 우선 가설로 유지한다. 거래·비용 원장이 없어 어느 전략도 수수료 주도 괴리로 단정할 수 없으며, 짧은 관측의 연환산 수익률을 장기 백테스트 CAGR과 직접 비교하지 않는다. 감사 미결은 불확실성이지만 오류 확정 근거는 아니므로 추가 강등 없이 EVALUATING을 유지한다. 이 판단은 실주문을 발생시키지 않는다.
- 관찰 항목:
  - mom_top5_ndx의 누적수익률·변동성·MDD가 모두 0인 이유: 정상적인 현금 대기, 신호 미발생, 체결 누락 또는 평가가격 미반영 여부.
  - buffer_hibeta·ftlt_hibeta·ftlt_hibeta_psq·holygrail_hibeta·simple_hibeta의 동일 성과가 실제 공통 보유·신호 때문인지 계산 경로 공유 오류인지 확인. hibeta_basket은 유사하지만 완전히 동일한 성과는 아니다.
  - buffer_hibeta의 반등 이후에도 남은 백테스트 기대 대비 부진과 방어 신호의 발생·집행 여부. 단기 반등만으로 괴리 해소를 판정하지 않는다.
  - overnight_qqq의 절대수익 회복과 상대성과 악화가 함께 나타난 원인. 양의 z를 양의 수익률이나 투자 적합성으로 해석하지 않는다.
  - 2026-10-26 원천 가격·장부 반영과 n 산식, 기존 지연 경보 해소 여부. 생성 시각이 2026-10-27T01:10:54+00:00이라는 사실만으로 지연을 확정하지 않는다.
  - 번들의 소액 매수 무료·매도 최소 비용·주간 환전 비용 조건을 거래별로 대조하고, 비용 전후 성과를 분리한다. 레버리지 및 기타 제외 대상은 추천 후보로 편입하지 않는다.
- 적용된 강등: 없음
- LLM 방출 요청(requests.jsonl · source=llm):
  - `audit` [high]  — 기존 고우선순위 데이터·비용·동일 성과 감사를 n=21 기준으로 병합 갱신한다. 2026-10-26 원천 가격·장부 반영, 관측 수 산식 및 WARMUP→EVALUATING 전환 조건을 검증하고 기존 지연 경보 해소 여부를 명시한다. 동일 성과 실계좌 전략들의 보유·신호·체결·평가 경로, mom_top5_ndx의 무수익 상태와 ep_gap_swing의 손실 발생 경로를 확인한다. 거래별 비용 원장으로 제공된 비용 조건과 중복 차감 여부를 점검한다. 별도 중복 감사 대신 기존 요청에 통합한다.
  - `investigate_divergence` [medium] buffer_hibeta — 기존 조사를 n=21, cum=-0.16788668601774215, z=-1.905789862098937, dd_ratio=0.5564110119141328 기준으로 병합 갱신한다. 직전 세션 대비 누적손실과 z는 개선됐으나 낙폭 비율은 유지됐다. 동일 성과 감사와 연계해 공통 보유 노출, 방어 신호 발생·집행 및 비용 전후 성과를 비교한다. 불리한 시장 구간의 노출 영향을 조사하되 단기 노이즈와 구분하고, 동행 전략들을 독립적인 레짐 증거로 중복 계산하지 않는다.
  - `investigate_divergence` [medium] overnight_qqq — 기존 조사를 n=21, cum=-0.047987790081885584, excess_qqq=-0.012697091678331012, z=0.433403355671006 기준으로 병합 갱신한다. 2026-10-23 대비 절대손실 축소와 상대성과·z 악화를 분리한다. 백테스트 CAGR=-0.237297 및 기대 연간 로그 초과수익=-0.429057과 비교할 때 기간·수익률 정의·진입 및 청산 시각·가격·비용·노출 구간을 일치시킨다. 총수익·비용·시장 노출 기여를 분해하며, 단기 노이즈를 우선 가설로 유지하고 수수료 주도 괴리나 구조적 개선은 미확정으로 둔다.
- 에이전트 루프 킥: kicked (gui/504/com.tosstrader.agentloop)
