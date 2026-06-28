# toss-trader — 토스증권 OpenAPI 기반 미국주식 스윙 자동매매

> 살아있는 설계 문서. 결정·진행상황·다음 할 일을 여기서 관리한다.
> 매일의 실행 기록은 `journal/YYYY-MM-DD.md`.

## 0. 확정된 방향 (2026-06-15)

| 항목 | 결정 |
|---|---|
| 브로커 | 토스증권 OpenAPI (`https://openapi.tossinvest.com`) |
| 시장 | **미국 주식** (거래세 없음, `orderAmount` 금액단위 소수점 매수 가능) |
| 스타일 | **스윙** (수일~수주 보유) — 초고빈도 불필요 |
| 의사결정 | **결정론적 규칙 기반** 매매. Gemini는 뉴스/필터 **보조**로만 |
| 시작 | **페이퍼트레이딩 먼저** → 검증 후 소액 실거래 |
| 시드 | 10만원(≈$72) 시작. 목표는 아래 "정직한 경제성" 참고 |

## 1. 정직한 경제성 — 먼저 읽을 것

**10만원 → 100만원(10배)은 "신뢰성 있게 달성 가능한 목표"가 아니다.**
세계 최정상 퀀트도 연 20~40%면 전설급이다. 10배를 단기에 노린다는 건
큰 레버리지/집중 = 대부분 깡통. 그래서 목표를 이렇게 재정의한다.

- **1차 목표(필수):** 수수료·환전·슬리피지 다 떼고 **무인으로 안정적으로 도는 것.**
  손실 없이 한 달 굴러가면 그게 진짜 성공이다.
- **2차 목표:** 페이퍼 6~12주 백테스트/포워드테스트에서 **비용 차감 후 양(+)의 기대값**과
  최대낙폭(MDD) 통제(예: -15% 이내)를 검증.
- **3차 목표(현실적 상한):** 실거래 전환 후 **연 환산 +15~40%**를 노린다. 복리로 굴리면
  10배는 "몇 달"이 아니라 "여러 해"의 문제다. 빨리 가려 할수록 깡통 확률이 올라간다.

### 소액의 적: 비용
- 미국주식은 거래세는 없지만 **수수료 + 환전 스프레드(KRW↔USD)**가 붙는다.
- $72에서 왕복 비용이 1%면, 단지 본전을 맞추려면 매매마다 +1%를 먼저 벌어야 한다.
- 그래서 **매매 빈도를 낮추고(스윙)**, **비용 모델을 코드에 박아** 모든 시그널을
  "비용 차감 후 기대값"으로 평가한다. → `costs.py`, 백테스터에 필수 반영.

### 실증 교훈 (2026-06-15, 합성데이터 백테스트)
- $72 시드 / 2년 / 59회 매매에서 **총비용 $10.36 = 자본의 ~14%**.
  무비용 −0.16% → 비용반영 −18.26% (드래그 18.1%p).
- 결론: **시드가 작을수록 매매 빈도를 극단적으로 낮춰야 한다.** 설계 반영:
  (1) 리밸런싱 임계치 상향, (2) 보유기간 장기화(월 단위 추세), (3) 유니버스 회전 억제,
  (4) 최소 진입금액 가드(작은 조각 매매 금지).

### "승률 52%면 됨"의 진실
- 승률만으로는 부족. **손익비(payoff)**가 함께 중요. 켈리/기대값으로 포지션 사이징.
- 기대값 = 승률×평균이익 − 패율×평균손실 − 비용. 이게 양수여야 의미가 있다.

## 2. 아키텍처 (레이어)

```
config / errors / ratelimit        # 인프라
        └─ client (TossClient)     # REST 래퍼: 인증·시세·계좌·주문, 멱등키, 429 재시도
                ├─ marketdata      # 캔들/시세 정규화 + 캐시
                ├─ costs           # 수수료/환전/슬리피지 모델
                ├─ strategy/*      # 결정론적 시그널 (모멘텀, 이평 등 플러그인)
                ├─ llm (Gemini)    # 뉴스요약·종목필터 보조 (실패해도 매매는 계속)
                ├─ risk            # 포지션사이징, 손절/익절, 일일손실차단, 멱등
                ├─ backtest        # 비용반영 백테스터 + 지표(CAGR/MDD/Sharpe/승률/손익비)
                ├─ broker          # PaperBroker | LiveBroker (동일 인터페이스)
                └─ engine/runner   # 스케줄 루프 + 매매일지 + 에러로그
```

핵심 원칙: **paper와 live는 동일한 Broker 인터페이스.** 전략 코드는 어느 쪽인지 모른다.

## 3. 로드맵 / 진행상황

- [x] **Phase 0** 디스커버리: 토스 실제 명세 확보 (엔드포인트/스키마/레이트리밋)
- [x] **Phase 1** 인프라 + API 클라이언트 — **실 API 검증 완료 (2026-06-28)**
  - [x] config / errors / ratelimit (레이트리밋 그룹명을 실 스펙 10개 그룹으로 정합)
  - [x] TossClient (auth·marketdata·account·order, 멱등키, 429/5xx 재시도)
  - [x] **공식 OpenAPI v1.1.5 대조로 클라이언트 정합화** (아래 §6 참고)
  - [x] **smoke_test 실 API 통과** — OAuth/시세/캔들/환율/종목/계좌/보유/매수가능/수수료 전부 정상
  - [x] **.env**: `API_KEY`/`SECRET_KEY`로 저장(코드가 dotenv로 로드, 키는 미기억). `accountSeq=1` 확인
  - [x] **SSL CA 자동탐색**: macOS python.org 빌드 CA 미설치 이슈 → `/etc/ssl/cert.pem` 등 자동 사용(검증 유지)
- [x] **Phase 2** costs(비용모델) + models + metrics
  - [x] **실요율 확정**: 미국 수수료 0.1%(=10bps). `CostModel.from_commissions()`로 라이브 반영
- [x] **Phase 3** PaperBroker(=백테스트·페이퍼 공용) + 백테스터 + 성과지표
  - 합성데이터로 엔진 전 경로 검증 완료. **실증 교훈 ↓**
- [ ] **Phase 4** 전략 플러그인 1~2종 (듀얼모멘텀/이평크로스) + 파라미터 최적화·워크포워드
  - 이평크로스(`sma_cross`) 베이스라인 구현됨. **이제 실 캔들 확보 가능** → 실데이터 백테스트·워크포워드 착수 가능
- [x] **Phase 5** risk + engine + 매매일지 + **LiveBroker** — 동일 인터페이스 완성
  - 결정론적 자가검증(`scripts/selftest.py`) 통과(리스크/엔진/LiveBroker/비용모델). 무인 엔진 데모 정상.
  - **LiveBroker**: 실주문(US MARKET 금액매수/수량매도) + 체결 폴링 + holdings/buying-power 동기화.
    `require_live` 가드로 paper 모드 오작동 방지. 가짜 클라이언트로 결정론적 검증.
  - 교훈①: 일일손실 차단 기준은 '당일 시작'이 아니라 **전일 종가 자본**이어야 갭다운을 잡는다.
  - 교훈②(2026-06-28, 적대적 감사로 발견·수정): **일일손실 차단 = '거래 동결'이지 '투매'가 아니다.**
    halt 시 `adjust_weights`가 빈 dict를 반환하는데, 이를 `_rebalance`에 넘기면 '전 종목 목표 0%'로
    해석돼 보유 전량을 당일 급락 타이밍에 시장가 청산하던 버그(손실 확정+왕복비용)였다.
    → 엔진이 `halted`면 리밸런싱을 건너뛰도록 가드. 보호청산(손절/익절/트레일링)은 그 전에 이미 처리.
    `scripts/selftest.py`에 회귀 케이스 추가(손절 미발동·halt 발동 시 보유 유지 단언).
- [ ] **Phase 6** Gemini 뉴스/필터 보조 (옵셔널, 실패격리)
- [ ] **Phase 7** 페이퍼 포워드테스트 수 주 → 리뷰 → 실거래 소액 전환
  - ⚠️ **현재 계좌 매수가능금액 $0(미입금)** — 실거래 전 입금 필요. 그 전까지 paper.

## 4. 검증되기 전엔 실거래 금지 (게이트)
실거래(`TRADING_MODE=live`) 전환은 다음을 **모두** 만족할 때만:
1. 백테스트에서 비용 차감 후 기대값 > 0, MDD 통제 확인
2. 페이퍼 포워드테스트 ≥ 4주, 시그널/주문/체결 로그 정상
3. 일일 최대손실 차단·멱등 중복주문 방지 동작 확인
4. 사용자 명시적 승인

## 5. 미해결/확인 필요 — **2026-06-28 실응답으로 해소**
- ✅ `/buying-power`: 쿼리 `currency`(KRW|USD) **필수**. `symbol` 아님. 응답 `{currency, cashBuyingPower}`.
- ✅ `/sellable-quantity`: 쿼리 `symbol` 필수. 응답 `{sellableQuantity}` (US 소수점 가능).
- ✅ prices: 배열 `[{symbol, timestamp|null, lastPrice, currency}]` — 현재가는 **lastPrice**.
- ✅ candles: `{candles:[{timestamp, openPrice, highPrice, lowPrice, closePrice, volume, currency}], nextBefore}` — **내림차순**으로 옴(오름차순 변환). 200봉 초과는 `before`/`nextBefore` 페이지네이션.
- ✅ holdings: `{items:[{symbol, marketCountry, currency, quantity, lastPrice, averagePurchasePrice, marketValue, profitLoss, ...}], totalPurchaseAmount/marketValue/profitLoss(통화별 {krw,usd})}`.
- ✅ 수수료: **미국 0.1%**(commissionRate "0.1", 퍼센트표기, endDate 2026-06-29 — 프로모 가능성 재확인), 국내 0%(~6/30 프로모).
- ⚠️ **환전 스프레드 미확정**: exchange-rate의 rate(1541.6) vs midRate(1541.1) 표시 스프레드 ~3bps이나, 명세상 "실거래 환율은 표시환율과 다를 수 있음". 실 체결의 KRW 차감액으로 측정 전까지 비용모델은 보수적 20bps 유지.

## 6. 공식 OpenAPI v1.1.5 정합화 메모 (client.py)
> 출처: `https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`
- 인증: `POST /oauth2/token` (form: grant_type=client_credentials, client_id, client_secret).
  응답 `{access_token, token_type:Bearer, expires_in}` — envelope 아님. 만료 86399s(~24h), refresh 없음.
  토큰 에러는 OAuth2 표준 `{error, error_description}` 포맷(별도 파싱).
- 성공 응답은 공통 envelope `{result: ...}` → client가 result만 벗겨 반환.
- 에러 envelope `{error:{requestId, code, message, data?}}`. code는 flat string(unknown 허용).
- 계좌 컨텍스트 API는 `X-Tossinvest-Account: {accountSeq}` 헤더 필수(정수).
- exchange-rate `baseCurrency`/`quoteCurrency` 필수, stocks는 `symbols`(복수), modify는 `orderType` 필수,
  list_orders는 `status`(OPEN|CLOSED) 필수 — 모두 정합화 완료.
- 레이트리밋: 응답 헤더 `X-RateLimit-Limit/Remaining/Reset`(초당 버킷) + `Retry-After`(429).
  client는 Remaining=0이면 Reset만큼 선제 대기(적응 throttle) + 429 Retry-After 준수.
- 주문: `quantity`(기본 정수, US MARKET SELL만 소수점) | `orderAmount`(US MARKET 전용, 정규장만) 택1.
  `confirmHighValueOrder`는 1억원↑ 주문에 필요.
