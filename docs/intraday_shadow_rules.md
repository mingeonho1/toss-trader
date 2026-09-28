# 인트라데이 포워드 섀도 트레이딩 규칙 (레인 3) — 사전확정 사양

작성일 2026-09-23 · 상태 **동결(frozen)** · 구현 `src/toss_trader/intraday_shadow.py`,
`scripts/intraday_shadow_run.py` · 상위 규범 `docs/gate_v2_spec.md` §8.3(레인 3)·§3.5(거래단위 검정)

> 목적: 데이트레이딩 아이디어를 **실주문 없이** 하루하루 정직한 out-of-sample 증거로
> 누적한다. 파라미터는 **평가 전에** 이 문서와 코드에서 동결한다(사후 튜닝 = 새 아이디어, N++).
> 레인 3은 백테스트로 "채택"하지 않는다. 이 섀도는 n≥200·t≥3·부트CI하한>0 를 **필요조건**으로
> 스크리닝할 뿐, 최종 판정은 마이크로구조 + 포워드 페이퍼로 종결한다(spec §8.3).

## 0. 데이터 전제 (정직성)

- 소스 = `intraday_sources`(Nasdaq keyless): **현재 세션만**, **PRICE-ONLY** (o=h=l=c=체결 마지막가,
  v=0). 거래량 신뢰 소스 없음.
- 따라서 **VWAP-반전 규칙은 불가** → 구현하지 않는다(거래량 필요). 문서에 명시적으로 스킵.
- **전일 종가(prior close)**: Nasdaq은 현재 세션만 주므로 대개 없음. 캐시가 여러 세션을 누적하면
  그 심볼의 **직전 세션 마지막 정규장 봉**을 prior close로 쓴다. 없으면 규칙별로 처리:
  - 모멘텀: **세션 첫 정규장 봉을 prior-close 프록시**로 쓰고 `prior_close_proxy_firstbar` 플래그.
  - 갭앤고: 갭 계산 불가 → **트레이드 생성 안 함**(skip), `no_prior_close` 로 집계만.
- 봉 해상도: **1분봉**(`{SYM}_1m.json`) 사용. 정규장 = 09:30–16:00 ET.
- Yahoo 가 언스로틀되면 `histdata.load_intraday` 로 진짜 OHLCV 가 들어올 수 있으나, 규칙은
  price-only 가정으로 동결한다(OHLCV 여도 close 만 사용 → 소스 무관 일관성).

## 1. 심볼 유니버스 & 티어 (동결)

| 세그먼트 | 심볼 | 반호가 티어(half-spread) |
|---|---|---|
| core ETF | `QQQ` | `etf` = 1.0 bp |
| core 레버리지 | `TQQQ` | `leveraged` = 2.0 bp |
| movers | 그 외 수집된 모든 심볼(워치리스트 대형주 + 당일 무버스) | `movers` = 15.0 bp |

**movers 정의(동결·정직)**: 수집기(`collect_intraday.py`)의 유니버스 = 고정 워치리스트 + 당일
"most active/gainers" 무버스다. 과거의 정확한 무버스 리스트를 재구성할 수 없으므로, **core(QQQ/TQQQ)를
제외한 모든 수집 심볼을 movers 로 동결**한다 → 안정적 대형주(AAPL 등)까지 포함되어 movers 규칙이
**과대포함**됨을 명시(보수적: 15bp 반호가로 페널티). 실제 RVOL 필터는 마이크로구조 단계에서.

## 2. 체결·비용 모델 (동결)

- **룩어헤드 금지(spec §2.3)**: 시점 t 의 결정은 봉 ≤ t 만 사용. 브레이크아웃/스톱 트리거는
  신호 봉의 **다음 봉 가격**으로 체결(next-bar fill). 사전 예약 시각(모멘텀 15:30/15:59,
  갭앤고 종가)은 그 봉 가격으로 체결.
- **반호가 + 슬리피지**: 체결가 = 기준가 × (1 ± (half_spread_bps(티어) + 5.0)/1e4).
  매수는 +, 매도는 −. 슬리피지 = **5.0 bp**(양방향), 티어별 반호가는 §1.
- **수수료**: `fees.TossFeeSchedule`(기본값: 0.1%, 건당 ≤$10 매수 무료, 매도 SEC/TAF 최소금액).
  포지션 크기 2종 병행: **$30**(실계좌 소액 근사) 와 **$1000**(스케일 확인). 두 크기의 순PnL/bps 를
  같은 트레이드 레코드에 함께 저장.
- **순손익**: shares = size / 매수체결가. gross = shares×(매도체결가 − 매수체결가).
  net = gross − buy_fee − sell_fee. net_bps = net/size×1e4.

## 3. 규칙 (파라미터 전부 동결)

정규장 = [09:30, 16:00) ET. 하루·심볼·규칙당 **최대 1 트레이드**. **롱온리**.

### 3.1 ORB-5 / ORB-15 (Opening Range Breakout) — core + movers
- `or_minutes ∈ {5, 15}`. 오프닝레인지(OR) = [09:30, 09:30+or_minutes) 정규장 봉.
  OR_high = 그 구간 최대가, OR_low = 최소가.
- **진입**: OR 종료 후 첫 봉부터 스캔, **price > OR_high** 인 첫 봉이 신호 → **다음 봉**에서 매수 체결.
  브레이크아웃 후 체결할 다음 봉이 없으면 트레이드 없음.
- **스톱**: 진입 봉 이후 **price ≤ OR_low** 인 봉이 나오면 그 **다음 봉**에서 청산(reason=`stop`).
- **시간청산**: 스톱 전 도달 시 **close−5min = 15:55 ET** 봉에서 청산(reason=`time`).
- 규칙 id: `orb5_core`, `orb15_core`(QQQ/TQQQ), `orb5_movers`, `orb15_movers`.

### 3.2 인트라데이 모멘텀 (Gao, Han, Li, Zhou 2018) — core
- 신호: **prior_close → 10:00 ET 수익 > 0** 이면 롱. 10:00 ET = 정규장에서 분(minute) ≤ 600 인
  마지막 봉. prior_close 없으면 첫 정규장 봉 프록시(§0, 플래그).
- **진입 15:30 ET 봉 매수, 청산 15:59 ET 봉 매도**(사전 예약 시각 → 해당 봉 체결).
  15:30 봉 이후 15:59 이전 마지막 정규장 봉을 청산 봉으로. 봉이 없으면 트레이드 없음.
- 규칙 id: `momentum_core`. reason=`time`.

### 3.3 갭앤고 (gap-and-go) — movers
- 갭 = 세션시가(첫 정규장 봉) / prior_close − 1. **갭 > +5%** 이고 **첫 15분이 시가 위 유지**
  (= [09:30, 09:45) 최소가 ≥ 세션시가) 이면, **09:45 다음 봉**에서 매수, **종가(마지막 정규장 봉)** 청산.
- prior_close 없으면 갭 계산 불가 → 트레이드 없음(`no_prior_close`).
- 규칙 id: `gap_and_go_movers`. reason=`time`.

### 3.4 미구현(정직)
- **VWAP-반전**: 거래량 필요 → price-only 로 불가. 스킵(위 §0).

## 4. 산출물 · 판정
- 트레이드: `data/intraday_shadow/trades.jsonl`(append-only). **멱등 키 = (date, rule, symbol)**.
- 리포트: `reports/intraday_shadow_latest.md`. 규칙×크기별 누적 **n, 평균 net bps, t-stat
  (`gate.trade_tstat`), 부트스트랩 95% CI(`gate.trade_pnl_bootstrap_ci`), 승률**, 상태:
  - **수집 중 (n<200)**: 표본 부족, 통계주장 불가(spec §3.5).
  - **후보 (t≥3)**: n≥200 且 t≥3 且 부트 CI 하한 > 0 → 마이크로구조/포워드로 승격 검토.
  - **기각**: n≥200 인데 t<3 또는 CI 하한 ≤ 0.
- 판정 통계량은 **net bps**(크기 간 비교 가능) 로 계산. $30·$1000 은 수수료 구조가 달라 별도 행.

## 5. 불변식(테스트로 강제)
- 룩어헤드 없음: exit_ts 이후 봉을 교란해도 트레이드 불변; entry_ts 이후 봉 교란해도 **진입 불변**.
- 멱등: 같은 (date, rule, symbol) 재실행 시 중복 append 없음.
- 수수료: `TossFeeSchedule` 규정(≤$10 매수 무료 등) 손계산과 일치.
