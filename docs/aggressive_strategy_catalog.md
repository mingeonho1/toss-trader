# 공격형(수익 우선) 능동 전략 카탈로그 — 레버리지·인버스·변동성·단타

> 작성 2026-09-28 · 리서치 전용(코드 변경 없음) · 대상 계좌: 토스증권 미국주식, 롱온리 현금계좌
> (공매도·옵션·마진 없음), 금액 기준 소수점 매수, 수수료 0.1%/side(체결 ≤$10 무료), 시드 ~$36 + 월 ~$35.
> **위상: 투자자문 아님.** 아래 "claimed" 수치는 전부 출처가 주장한 백테스트(대부분 in-sample)이며
> 이 레포의 사전등록 게이트(`docs/gate_v2_spec.md`)를 통과한 것이 아니다. 목적은 **구현 가능한 수준의
> 정확한 규칙 + 출처 + 공개일(=진짜 OOS 시작점)** 을 모아 게이트에 넣는 것.

---

## 0. 읽기 전에 — 이 계좌에서 결정적인 전제 5개

1. **규제 게이트(실계좌 전용, 페이퍼는 무관).** 2025-12-15부터 해외 레버리지 ETP 첫 거래 전
   금융투자교육원 사전교육(약 1시간) 이수 필요, **2026-05-22부터 해외 레버리지 ETF/ETN 첫 거래자는
   기본예탁금 ₩1,000만** 필요(과거 해외 레버리지 ETP 거래 이력자는 제외). 인버스(-1x)·VIX ETP·
   단일종목 레버리지의 포함 범위는 기사별로 표현이 달라 **토스 고객센터 확인 필요**.
   출처: [헤럴드경제 2026-05-20](https://biz.heraldcorp.com/article/10742195),
   [토스 FAQ](https://support.toss.im/faq/3677?from=10&page=1). → 이력 없는 ~$36 계좌는 실거래로
   TQQQ/SOXL/SQQQ/UVXY/NVDL 등을 **못 살 수 있다**. 1x 대체(QQQ/SPY/SMH/IBIT/개별주)로 신호를
   동일하게 돌리는 "1x 섀도" 버전을 각 전략에 같이 두는 걸 권장.
2. **비용 산식.** 왕복 0.2%. 일간 스위칭 심포니(연 50~150회 전환)는 **연 10~30%p 비용 drag**.
   단 주문을 **≤$10 조각으로 분할하면 무료**(레포 `final_recommendation.md` ② 참조) → $36 계좌는
   4조각이면 사실상 0. 고회전 전략(B·F·G군)의 생사가 이 분할 가능 여부에 달림(주문 수 한도·
   소수점 주문의 실시간 체결 여부·지정가/스톱 지원 여부는 토스 실측 필요).
3. **Composer 백테스트는 과대평가 경향.** (a) 신호를 장 마감 ~10분 전 가격으로 계산해 그 가격에
   체결 가정, (b) 커뮤니티가 수천 개 변형을 백테스트 후 상위만 공유(선택 편향·임계값 곡선맞춤),
   (c) UVXY 2018-02-27 이전은 2x(현재 1.5x), SVXY는 -1x(현재 -0.5x) 시절 가격을 그대로 사용.
   → 각 심포니의 **공개일 이후만 OOS**로 취급할 것.
4. **데이터 제약(상장일).** 백테스트 시작일은 가장 늦게 상장한 구성 종목이 결정한다. 아래 §9 표.
5. **실행 시각 규약(이 문서 공통).** "일간(D, 종가)" 전략 = 15:45~15:50 ET 현재가를 종가로 간주해
   신호 계산 → 즉시 시장가(또는 공격적 지정가) 체결. 백테스트는 두 버전 모두 돌릴 것:
   `close-to-close(낙관)` 와 `신호 t종가 → t+1 시가 체결(보수)`. 두 결과 차이가 크면 과최적/체결의존.

### 공통 지표 정의 (구현 시 이대로)

- `SMA(x,n)` = 최근 n개 **종가** 단순평균(당일 현재가 포함). `price(x)` = 당일 현재가/종가.
- `RSI(x,n)` = Wilder RSI(첫 평균은 SMA, 이후 `avg=(prev*(n-1)+cur)/n`). Composer/namuan 구현은
  Wilder 계열이지만 **±1~2pt 차이가 날 수 있고 79/80·30/31 경계에서 결과가 뒤집힌다** → 임계값
  ±3 민감도 테스트 필수.
- `CR(x,n)` (cumulative return) = `close_t / close_{t-n} − 1` (%로 비교 시 ×100).
- `STD_RET(x,n)` = 최근 n개 일간수익률 표준편차. `HV10` = `STD(ln r,10)×√252×100`.
- `filter top/bottom k by f(n) of [..]` = 후보들을 지표 f(n)로 정렬해 상/하위 k개 동일가중.
- `MaxDD(x,n)` = 최근 n일 고점대비 최대 낙폭.
- 모든 배분은 달리 명시 없으면 100% 단일 종목, 신호 바뀔 때만 거래(일간 체크).

---

## 1. 요약 표

| ID | 전략 | 군 | 주요 종목 | 빈도/시각 | 출처·공개일 | Claimed CAGR / MDD (기간) | 진짜 OOS 시작 | 데이터 최소시작 | 회전(추정) | 핵심 리스크 |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | Leverage Rotation (LRS) 200-SMA | A | UPRO/TQQQ ↔ BIL | D 종가 | Gayed&Bilello, SSRN 2016-03-03 | 3x: 26.8% / −92.2%; 2x: 19.1% / −78.7% (1928–2015, 시뮬) | 2016-04 | 일봉, SPY 1993·QQQ 1999 (합성 LETF 가능) | ~5회/년 | 횡보장 휩쏘, 갭 하락 |
| A2 | 200-SMA +5%/−3% 버퍼 | A | QQQ 신호→TQQQ ↔ SGOV | D 종가 | TradingView 스크립트(r/LETFs 계열), 2023~24 | 미보고 | 스크립트 공개일 이후 | 일봉 QQQ 1999 | 1~3회/년 | 늦은 진입/청산 |
| A3 | 9Sig (Jason Kelly) | A | TQQQ/AGG 60/40 | 분기 | Kelly(뉴스레터 2019~), 규칙정리 BestFolio 2026-04-11 | 39.4% / −72.1% (2010–2026 실 TQQQ); 8.3% / −99.7% (1999–, 합성) | 2019~(뉴스레터) | 분기, TQQQ 2010 | 4회/년 | 무적립 시 닷컴급 붕괴 |
| A4 | Alvarez UPRO/TQQQ 월간 4조건 | A | UPRO+TQQQ / QQQ+SPY / TLT | 월말→익일 시가 | Alvarez Quant Trading 2024-03-27 | 24.4% / 54% (2010–2023) | 2024-04 | 일봉, VWO/BND/VIX | 월 1회 체크 | 저자 스스로 과최적 우려 |
| A5 | In & Out "Distilled Bear" | A | TQQQ ↔ TLT/IEF | D | QuantConnect 포럼 2020-10~11 | 총수익 1,890~2,800%(포럼 게시, 기간 ~2008–2020) | 2021-01 | 일봉 SLV/GLD/XLI/XLU/DBB/UUP | 5~15회/년 | 파라미터 재구성 필요 |
| B1 | TQQQ For The Long Term (FTLT) | B | TQQQ/UVXY/TECL/SPXL/SQQQ/BSV(TLT) | D 15:50 | Dereck Nielsen, Reddit→Composer, ~2022 | Composer 복제본 165.6% / 50.2% (2011–2026); Composer 블로그 "2022-06 이후 +400%, 연 82%" | 2023-01 (보수) | 일봉, UVXY 2011-10 | 30~80회/년 | RSI 임계 곡선맞춤, UVXY 스파이크 의존 |
| B2 | The Holy Grail | B | TQQQ/UVXY/TECL/SOXL/SQQQ/BSV | D 15:50 | Composer 커뮤니티 ~2022 | 155.5% / 47.0% (2011-10–2026-09) | 2022-07-20(Composer 표기) | 일봉, UVXY 2011-10 | 30~80회/년 | B1과 동일 |
| B3 | Beta Baller v2.2 (SMH mod) | B | SOXL/TECL/SOXS/SQQQ/UVXY/VIXY/… | D 15:50 | Composer "Deez, BrianE, HinnomTX, DereckN, Garen", evo 2022-10-11 | 단순판 131.4% / 75.1% (2019-11–2026-09); v2.2 명칭상 "AR 6305%, DD 50.7%"(2019-12~2022) | 2022-11 | 일봉, HIBL 2019-11 | 80~150회/년 | 채권 RSI 신호 과최적, SOXL −90% 가능 |
| B4 | KMLM Switcher (Simon97) | B | TECL/SOXL/SVIX/SQQQ/TLT/UVXY | D 15:50 | Composer ~2022-04 이후 | 523% / 36.1% (2022-04–2026-09), OOS Sharpe 1.66 | 2024-07-22(Composer 표기) | 일봉, KMLM 2020-12 | 100+회/년 | 백테스트 4년뿐, 극단 과최적 |
| B5 | Simple TQQQ RSI>79 → UVXY | B | TQQQ/UVXY | D 15:50 | Composer (FTLT 핵심 블록) | 81.2% / 58.0% (2011-10–2026-09) | 2024-10(Composer 표기) | 일봉 | 10~20회/년 | B1의 ablation 기준선 |
| B6 | IBS 평균회귀 (QQQ 신호→TQQQ) | B | TQQQ/QLD ↔ 현금 | D 종가 직전 | Pagonidis(NAAIM 2014), QuantifiedStrategies | 미수집(원전 확인 필요) | 2014-05 | 일봉 OHLC | 40~70회/년 | 종가 체결 필수 |
| B7 | Connors RSI(2) (QQQ/SPY→3x) | B | TQQQ/UPRO | D 종가 | Connors 2008~2009, QuantifiedStrategies | SPY 1x: 9%/yr, MDD 34%, 노출 28% | 2009 | 일봉 | 15~30회/년 | 폭락장 평균회귀 실패 |
| C1 | HFEA 55/45 | C | UPRO/TMF | 분기 | Bogleheads Hedgefundie 2019-02(40/60)→2019-08(55/45) | 시뮬 1987–2018 ≈18–19%(원 주장 23.75%); 실운용 $100(2019-10)→$137(2025-04) | 2019-09 | 일봉, TMF 2009-04 | 4회/년 | 2022 주식·채권 동반폭락 |
| C2 | TQQQ/TMF 50/50 + 폭락필터 | C | TQQQ/TMF/IEF | 격월 | QuantifiedStrategies (~2021–22) | 44.9% / 월말 MDD 24.5% (2010–~2021) | 게시일 이후 | 일봉 | 6회/년+필터 | 2022에서 필터 미작동 |
| D1 | VRatio (VIX/VIX3M) | D | SVXY(SVIX) ↔ VIXY 또는 BIL | D 15:50 | Tony Cooper, SSRN 2255327, 2013-02 | 원문 시뮬(XIV/VXX) 고수익 — 2018-02 XIV 소멸이 OOS 충격 | 2013-03 | 일봉 VIX·VIX3M(VXV), SVXY 2011 | 10~30회/년 | 하룻밤 −80~90% 가능(2018-02-05) |
| D2 | VRP (VIX − HV10) | D | SVXY ↔ VIXY/BIL | D 15:50 | Cooper 2013 동일 | 동상 | 2013-03 | 일봉 SPX·VIX | 15~40회/년 | 동상 |
| D3 | VIX 선물 basis roll | D | SVXY/VIXY 프록시 | D, 5일 보유 | Simon & Campasano 2013 (Quantpedia) | In-sample 19.67%/yr(2007–2011), OOS 소폭 음수 | 2014 | VX1 선물 일봉(CFE) | ~50회/년 | OOS 붕괴 보고됨 |
| D4 | Concretum "Volatility Edge" dual | D | VIX ETN 롱/숏 | D | Zarattini 등 SSRN 5316487, 2025-06-25 | 16.3% / Sharpe ~1.0 (2008–2025) | 2025-07 | VIX·VX 선물 | 중 | 롱온리 변형 미보고(규칙은 원문 필요) |
| E1 | QQQ 200-SMA TQQQ↔SQQQ (항상 투자) | E | TQQQ/SQQQ | D 종가 | 통제군(LRS 변형, r/LETFs 단골) | 미보고(대체로 LRS보다 열위로 알려짐) | — | 일봉 | ~5–15회/년 | SQQQ 휩쏘·decay |
| E2 | FTLT 약세 브랜치 단독 | E | SQQQ/TLT(BSV)/TECL | D 15:50 | B1에서 분리 | 미보고 | 2023-01 | 일봉 | 20~50회/년 | 약세장 표본 적음(2018Q4, 2020, 2022, 2025) |
| E3 | SOXL/SOXS 급등 역추세 | E | SOXS/SOXL/TECL/BOXX | D 15:50 | Composer "SOXL/SOXS" | OOS CAGR 16%, MDD 66.9% (2022-12–2026-09) | 2025-03-11(Composer 표기) | 일봉 | 10~30회/년 | 추세 지속 시 역방향 3x |
| F1 | Stocks-in-Play 5분 ORB | F | RVOL 상위 20 개별주 | 5분봉, 09:35~16:00 | Zarattini/Barbon/Aziz SSRN 4729284, 2024-02-16 | 1,637% 총, IRR 41.6%, Sharpe 2.81, MDD 12% (2016–2023, 롱숏) | 2024-03 | 1분/5분봉 전종목 + 14일 개장5분 거래량 | 매일 다수 | 롱온리=절반 기회, 슬리피지 |
| F2 | QQQ 5분 ORB → TQQQ | F | TQQQ | 5분봉 | Zarattini & Aziz SSRN 4416622, 2023-04 | TQQQ 1,484% vs QQQ 보유 169% (2016–2023-02) | 2023-05 | 1분/5분봉 QQQ·TQQQ 2016~ | ~250회/년 | 복제: 2.2¢/주 슬리피지면 0, PnL 76%가 2022 |
| F3 | Noise-Area 장중 모멘텀 ("Beat the Market") | F | SPY(→UPRO/TQQQ 롱만) | 30분 체크, 장마감 청산 | Zarattini/Aziz/Barbon SSRN 4824172, 2024-05-14 | 1,985% 총(순), 19.6%/yr, Sharpe 1.33 (2007–2024초, 롱숏) | 2024-06 | 1분봉 SPY 2007~ | ~150–250회/년 | 롱온리/레버리지 변형 미검증 |
| F4 | VWAP 추세 (QQQ/TQQQ) | F | TQQQ | 1분봉 | Zarattini & Aziz SSRN 4631351, 2023-11-13 | QQQ 671%(Sharpe 2.1, MDD 9.4%), TQQQ 8,242% (2018-01–2023-09) | 2023-12 | 1분봉 | 매일 다회 | 수수료·스프레드에 극도로 민감 |
| F5 | Episodic Pivot / 실적 갭 지속 | F | 갭 ≥10% 개별주 | 개장 후 5~60분 진입, 수일 보유 | Qullamaggie(qullamaggie.com), PEAD 문헌 | 공식 백테스트 없음(재량 트레이더) | 사용 시점 | 분봉+실적일정 | 월 수회 | 2002 이후 PEAD 반전 연구 존재 |
| F6 | LETF 장후반 모멘텀 (Chan) | F | 3x ETF들 | 14:00 신호→장마감 청산 | Ernie Chan; QuantRocket 2019-03-04 | ±6%: 31%/yr, Sharpe 1.95 (2008–2016); 2017년 이후 평탄 | 2019-04 | 분봉 | 소수/년 | 이미 decay 보고 |
| G1 | 오버나이트 TQQQ(종가 매수→시가 매도) | G | TQQQ(QQQ) | 15:59 매수, 09:30 매도 | Setup4alpha 등, 학술: Cliff/Cooper/Gulen 2008 | QQQ 2010~: 545% vs 보유 643%, MDD 더 작음; 200SMA 필터 시 301% | 2008 | 일봉 O/C | 252회/년 | 비용 없으면 불가(≤$10 분할 필수) |
| G2 | IBIT 오버나이트 | G | IBIT(→BITX/MSTU 1x 섀도) | 종가 매수→시가 매도 | Bespoke 2025(FA-mag 인용), 2025-12-08 후속 | 2024-01~: 종가→시가 +222% vs 시가→종가 −40.5%, 보유 +40%대 | 2025-12 | 일봉 O/C, IBIT 2024-01-11 | 252회/년 | 표본 2년, 알려진 후 소멸 가능 |
| G3 | BTC 추세 → IBIT/BITX/MSTU/CONL | G | IBIT/BITX/MSTU | D/주간 | Concretum 2025-04-08(Donchian), QuantifiedStrategies | Sharpe >1.5, 알파 10.8%/yr vs BTC (크립토 자체) | 2025-05 | BTC-USD 일봉(2014~) + ETF 상장일 | 10~30회/년 | 증시시간≠24h 크립토 |
| G4 | 월말·월초(TOM) TQQQ/UPRO | G | TQQQ/UPRO ↔ BIL | 월말 D−1 종가 → D+3 종가 | 다수, ETF 적용 FSR 저널 | 미수집(학술: TOM 4일 유의) | 1980s 문헌 | 일봉 | 12회/년 | 알파 약화 |
| G5 | 3x ETF 모멘텀 로테이션(+추세필터) | G | TQQQ/SOXL/TECL/UPRO/FNGU/TNA… | 주간 | 카탈로그 구성(검증 가설) | 미보고 | 테스트 시작일 | 일봉 | 20~50회/년 | 설계 자유도=과최적 |
| G6 | Accelerating Dual Momentum(레버리지판) | G | UPRO/SCZ/TMF(TIP) | 월말 | EngineeredPortfolio 2018-05-02 | 원판(1x) 공개 수치 있음(원문), 레버리지판 미보고 | 2018-06 | 월봉 | ~4–8회/년 | 1개월 채권 선택 노이즈 |

---

## 2. (A) 레버리지 ETF 추세·스위치

### A1. Leverage Rotation Strategy (LRS, "LRR" 200-SMA)

- **출처:** Gayed & Bilello, *Leverage for the Long Run*, SSRN 2741701, 2016-03-03 (2016 Charles H. Dow Award).
  [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2741701) · [PDF](https://cmtassociation.org/wp-content/uploads/2025/08/2016-gayed-bilello.pdf)
- **Claimed (Table 8, 1928-10~2015-10, S&P500 TR, 레버리지 비용 1%/년, 거래비용 0):**
  1.25x LRS 12.5% / MDD −59.0%, **2x LRS 19.1% / −78.7%**, **3x LRS 26.8% / −92.2%**, 모두 ~5회/년.
  비교: 3x 보유 15.3% / −99.9%. 무레버리지 200-SMA 10.9% / −49.5%.
- **규칙:**
  ```
  매 거래일 종가(15:50 근사):
    if price(SPY) > SMA(SPY,200):  hold UPRO   (나스닥판: QQQ 신호 → TQQQ)
    else:                          hold BIL (또는 SGOV)
  ```
  변형: 신호=지수(1x)로 계산, 체결=레버리지 ETF(레버리지 ETF 자체 SMA 사용 금지 — 더 휩쏘).
- **데이터:** 일봉. 실 UPRO 2009-06, TQQQ 2010-02. 그 이전은 1x 지수 일간수익×3 − 비용(연 ~1%+
  차입비용 ≈ T-bill×2)으로 합성.
- **회전:** ~5 전환/년(=10 거래). 비용 무시 가능.
- **비판:** 논문 MDD가 1929~32 포함 −92%(3x)로 여전히 치명적. 2x/3x LRS는 1987-10-19 같은 **갭 폭락은
  못 피함**(신호가 이미 위일 때). 1x 신호 기준 SMA 근처에서 휩쏘(2015~16, 2018, 2022 초).
  → **A2 버퍼**가 그 보완.

### A2. 200-SMA +5% / −3% 버퍼 (QQQ 신호, TQQQ 실행)

- **출처:** TradingView 공개 스크립트 "200 SMA 5%/3% Buffer for SPY/QQQ" (r/LETFs에서 유행한 규칙의 스크립트화).
  [링크](https://www.tradingview.com/script/0S8jQcA5-200-SMA-5-3-Buffer-for-SPY-QQQ)
- **Claimed:** 스크립트 페이지에 수치 없음 → 직접 측정.
- **규칙:**
  ```
  state ∈ {IN, OUT}, 초기 OUT
  매 거래일 종가:
    if state==OUT and close(QQQ) > 1.05 × SMA(QQQ,200): state=IN  → buy TQQQ(100%)
    if state==IN  and close(QQQ) < 0.97 × SMA(QQQ,200): state=OUT → sell TQQQ, buy SGOV
  ```
  민감도: 진입 {+3,+4,+5,+6}% × 청산 {−2,−3,−4,−5}% 격자 → 상위 1개가 아니라 **격자 평균**으로 판단.
- **데이터:** 일봉 QQQ 1999~(합성 TQQQ). SGOV 2020-05(이전은 BIL).
- **회전:** 1~3 전환/년.
- **비판:** 버퍼 폭은 사후 선택된 값. 바닥 반등 진입이 늦어 V자 회복(2020, 2023)에서 큰 수익 누락.

### A3. 9Sig (Jason Kelly "9% Signal", TQQQ/AGG)

- **출처:** Jason Kelly 뉴스레터(3Sig 책 2015 → 9Sig 레버리지판 2019경~).
  [jasonkelly.com strategies](https://jasonkelly.com/resources/strategies/) · 충실 재현 규칙과 백테스트:
  [BestFolio 2026-04-11(09-11 개정)](https://bestfolio.app/blog/kelly-signal-danger-v2)
- **Claimed (BestFolio 재현, 무적립):** 실 TQQQ 2010–2026 **39.4% / −72.1%**, Sharpe 0.82.
  합성 1999–2026 **8.3% / −99.7%**. Kelly 본인은 "월 적립(신규 현금이 채권 쪽으로 들어감)"이 생존의 핵심이라고 전제.
- **규칙 (분기 마지막 거래일 종가, 전부 분기 단위):**
  ```
  초기: 60% TQQQ / 40% AGG ; signal_line = TQQQ 평가액
  매 분기말:
    target = signal_line × 1.09          # 9%/분기 복리, 하향 조정 없음
    if TQQQ_value > target: TQQQ를 (TQQQ_value − target)만큼 매도 → AGG 매수   # 매도신호
    if TQQQ_value < target: 부족분만큼 AGG 매도 → TQQQ 매수
        단, 매수액 ≤ 0.9 × AGG_value, 그리고 매수 후 AGG ≥ 포트폴리오의 10%
    signal_line = target
    "30 Down" 규칙: close(TQQQ) ≤ 0.70 × max(분기말 종가, 직전 8분기) 이면 → 이후 매도신호 2회 무시
    "Base reset": 매도 후 AGG > 포트폴리오 30% → 60/40으로 리셋, signal_line 재설정
    "Spike reset": 30 Down 창 밖에서 TQQQ 1분기 +100% 이상 → 60/40 리셋
  월 적립금: AGG에 넣고 분기 계산에 포함(Kelly 방식)
  ```
- **데이터:** 분기(일봉에서 추출). TQQQ 2010-02, AGG 2003.
- **회전:** 4회/년(+적립). 비용 무시 가능.
- **비판:** 합성 닷컴 구간에서 −99.7% → **나스닥 장기 약세에서 파산급**. 규칙이 비대칭 시그널
  (매수 캡)이라 사실상 "하락 시 분할매수 + 상승 시 이익실현" 리밸런싱. 이 계좌는 월 $35 적립이
  있어 Kelly의 전제에 부합 — **적립 포함/미포함 두 버전**을 게이트에 넣을 것.
- **3Sig(원판):** IJR 80 / AGG 20, 3%/분기 신호선, 동일 메커닉. 9Sig의 저위험 대조군.

### A4. Alvarez UPRO/TQQQ 월간 4조건 레짐

- **출처:** Cesar Alvarez, [Alvarez Quant Trading 2024-03-27](https://alvarezquanttrading.com/blog/upro-tqqq-leveraged-etf-strategy/)
- **Claimed:** 2010-01~2023-12 **CAGR 24.4% / MDD 54%**, 2022 −48%, 2023 +64%. 저자 본인이 과최적 가능성 언급.
- **규칙:**
  ```
  매월 마지막 거래일 종가에 계산, 익일 시가 체결:
    M(x) = (12×R1m + 4×R3m + 2×R6m + R12m) / 4        # "13612W" 모멘텀, R=총수익
    c1 = VIX_close ≤ 25
    c2 = close(SPX) > SMA(SPX,200)
    c3 = M(VWO) > 0
    c4 = M(BND) > 0
    n_false = 4 − (c1+c2+c3+c4)
    n_false==0 → 50% UPRO + 50% TQQQ
    n_false∈{1,2} → 50% QQQ + 50% SPY
    n_false∈{3,4} → 100% TLT
  ```
- **데이터:** 일봉 SPX/VIX/VWO(2005)/BND(2007). 공개일 이후(2024-04~) OOS 2.5년 확보 가능.
- **회전:** 월 1회 점검, 실제 전환 연 3~8회.
- **비판:** VWO→VTI 교체 시 수익 −15%p(저자) = 선택 민감. 방어자산 TLT는 2022에 실패.

### A5. In & Out / "Distilled Bear" (QuantConnect 커뮤니티)

- **출처:** Quantopian "New Strategy – In & Out"(2020-10) → QuantConnect 이관.
  [QC 포럼 9597](https://www.quantconnect.com/forum/discussion/9597/the-in-amp-out-strategy-continued-from-quantopian/p4/comment-31268),
  [QC 10246 "Intersection of ROC comparison using OUT_DAY approach"](https://www.quantconnect.com/forum/discussion/10246/Intersection+of+ROC+comparison+using+OUT_DAY+approach)
- **Claimed:** 포럼 게시 총수익 In&Out 2,090%, Distilled Bear 1,890%, 결합 2,800% (TQQQ 사용 변형, ~2008–2020).
- **규칙 (Distilled Bear, 포럼 코드 기반 재구성 — 원 코드와 대조 필요):**
  ```
  매일 장 마감 전:
    vola   = STD(ret(QQQ),126) × √252
    wait   = int(vola × 85)                  # BASE_RET = 85
    period = int((1 − vola) × 85)
    r(x)   = close_t / close_{t−period} − 1
    exit_signal = r(SLV) < r(GLD)  AND  r(XLI) < r(XLU)  AND  r(DBB) < r(UUP)
    if exit_signal: out_day = today ; state = OUT
    if state==OUT and (거래일수 since out_day) ≥ wait: state = IN
    IN  → 100% TQQQ (원판 QQQ/주식바스켓)
    OUT → 100% TLT 또는 IEF (변형: TMF)
  ```
- **데이터:** 일봉 SLV(2006)/GLD(2004)/XLI·XLU(1998)/DBB(2007)/UUP(2007).
- **회전:** 5~15 전환/년.
- **비판:** 2020년 한 스레드에서 수십 개 변형이 경쟁 → 선택 편향 큼. 채권 OUT 자산은 2022에 손실.
  공개(2020-11) 이후 2021~2026이 깨끗한 OOS.

---

## 3. (B) RSI·평균회귀 로테이션 (Composer 계열)

> Composer 공통: 일간 리밸런스, 장 마감 ~10분 전 가격으로 계산·체결. 가중은 "equal".
> 트리 표기는 정확한 들여쓰기 = if/else 중첩.

### B1. TQQQ For The Long Term (FTLT)

- **출처:** Dereck Nielsen(Composer 파워유저), Reddit 최초 게시 → Composer 커뮤니티 진화.
  [Composer 심포니](https://www.composer.trade/trading-strategies/tqqq-for-the-long-term-reddit-post-link-HukRwDJLlYPLMbrQbua5),
  구현 코드: [namuan/trading-utils `tqqq-for-the-long-run.py`](https://github.com/namuan/trading-utils) (★317, push 2026-08-18),
  원본 EDN: [androslee/compose_symphony_parser `inputs/tqqq_long_term.edn`](https://github.com/androslee/compose_symphony_parser) (★22, push 2023-11).
- **Claimed:** Composer 복제본 2011-10-04~2026-09-28 **CAGR 165.6% / MDD 50.2%**, Sharpe 1.86(OOS 표기 2023-01-24).
  Composer 블로그: "2022-06 이후 +400%, 연 82%". 원본 EDN 이름 "Original (285.1% / 45.6% DD)".
- **공개일:** 2022년 중반 이전(정확한 Reddit 날짜 미확인) → **보수적 OOS 시작 2023-01-01**.
- **규칙 B1a (namuan 구현 = 가장 많이 복제된 "canonical" 판):**
  ```
  if price(SPY) > SMA(SPY,200):                     # 강세 레짐
      if   RSI(TQQQ,10) > 79: UVXY
      elif RSI(SPXL,10) > 80: UVXY
      else:                   TQQQ
  else:                                             # 약세 레짐
      if   RSI(TQQQ,10) < 31: TECL
      elif RSI(SPY,10)  < 30: SPXL   (=UPRO)
      elif RSI(UVXY,10) > 74:
          if RSI(UVXY,10) > 84: → TREND_BLOCK
          else:                 UVXY
      else: → TREND_BLOCK
  TREND_BLOCK:
      if price(TQQQ) > SMA(TQQQ,20):
          if RSI(SQQQ,10) < 31: SQQQ   else: TQQQ
      else:
          filter top 1 by RSI(10) of [SQQQ, BSV]     # (원판은 BSV 대신 TLT)
  ```
- **규칙 B1b (원본 EDN "Original 285.1%/45.6%DD" 파싱 결과 — 더 복잡한 초기판):**
  ```
  if price(SPY) > SMA(SPY,200):
      if RSI(TQQQ,10) > 79: UVXY
      elif RSI(SPXL,10) > 80: UVXY
      elif CR(TQQQ,5) > 20%:
          if RSI(TQQQ,10) < 31: TQQQ
          else: filter top 1 by RSI(10) of [UVXY, SQQQ]
      else: TQQQ
  else:
      if RSI(TQQQ,10) < 31: equal-weight[ group(TECL,TQQQ 각 1/2), SOXL ]   # 즉 TECL 25/TQQQ 25/SOXL 50
      elif RSI(SPY,10) < 30: UPRO
      elif price(TQQQ) < SMA(TQQQ,20):
          if CR(SQQQ,10) > 23%: TQQQ
          else: 50% [top1 RSI(10) of SQQQ,TLT] + 50% [top1 RSI(10) of SOXS,TLT]
      else:
          if RSI(SQQQ,10) < 31: SQQQ
          else: filter bottom 1 by RSI(10) of [TQQQ, SOXL]
  ```
  (GROUP 가중은 EDN 구조상 wt-cash-equal → 위 해석. 구현 전 Composer에서 해당 심포니 "Copy" 후 재확인 권장.)
- **Ablation 필수:** (i) UVXY→BIL 교체(Composer에 "BIL instead of UVXY" 공개 변형 존재) — UVXY 2x 시절
  스파이크(2015-08, 2018-02, 2020-03) 의존도 측정. (ii) 79/80/31/30 ±3 격자. (iii) 체결 t+1 시가.
- **데이터:** 일봉 SPY/TQQQ/SPXL/UVXY/TECL/SQQQ/BSV(TLT). 백테스트 시작 2011-10(UVXY). UVXY 레버리지
  2x→1.5x(2018-02-27) 반영 필수(실가격 사용하면 자동 반영되나 2018 이전은 "다른 상품").
- **회전:** 추정 연 30~80 전환(강세장에선 RSI>79 과열 때만 1~3일 UVXY).
- **비판:** RSI 임계 79/80/31/30/74/84는 전형적 곡선맞춤값. UVXY 보유 1~3일이 수익의 큰 몫을 차지
  (과열 후 조정 타이밍 적중이 몇 번에 집중). 강세 레짐 기본값이 TQQQ 풀보유라 2022급 급락 초기(200SMA
  이탈 전) 손실은 그대로.

### B2. The Holy Grail

- **출처:** Composer 커뮤니티. [Composer](https://www.composer.trade/trading-strategies/the-holy-grail-MmQbpf2U5TMQFmr9Nt2e),
  변형 "THE Holy Grail (165/47) since 2011", "Holy Grail | Hedged", "Holy Grail Revamped (VIXY CR threshold)".
- **Claimed:** 2011-10-04~2026-09-28 **155.5% / 47.0%**, Sharpe 1.81, OOS 표기 시작 **2022-07-20**.
- **규칙 (Composer 페이지 서술 기반; 창 길이 미명시 부분은 10일 가정 — 구현 전 export로 확인):**
  ```
  if price(TQQQ) > SMA(TQQQ,200):          # FTLT와 달리 TQQQ 자체 200SMA
      if RSI(TQQQ,10) > 79: UVXY   else: TQQQ
  else:
      if   RSI(TQQQ,10) < 31: TECL
      elif RSI(SOXL,10) < 30: SOXL
      elif price(TQQQ) < SMA(TQQQ,20): filter top 1 by RSI(10) of [SQQQ, BSV]
      else: TQQQ
  ```
- **데이터/회전/비판:** B1과 동일. TQQQ 자체 SMA라 신호가 SPY판보다 빠르고 휩쏘 많음.

### B3. Beta Baller v2.2 (SMH for the Long Term mod)

- **출처:** Composer "V 2.2 | ☢️ Beta Baller | Deez, BrianE, HinnomTX, DereckN, Garen | SMH 4 Long Term Mod",
  명칭상 "BT date 1DEC19, EVO date 11OCT22". EDN: [androslee/compose_symphony_parser `inputs/betaballer-modified.edn`](https://github.com/androslee/compose_symphony_parser).
  단순판: [Simple Beta Baller Signal](https://www.composer.trade/trading-strategies/simple-beta-baller-signal-8q3X2FUUBwKuYuBNiTRo).
- **Claimed:** 단순판 2019-11-21~2026-09-28 **131.4% / 75.1%**, Sharpe 1.33, OOS 표기 2022-11-04.
  v2.2 명칭 "AR 6305%, DD 50.7%"(2019-12~2022-10 in-sample, 비현실적 수치 = 과최적 신호).
- **공개일:** 2022-10-11 → **OOS 2022-11-01~**.
- **규칙 (EDN 파싱 원문 그대로):**
  ```
  if RSI(BIL,7) < RSI(IEF,7):                                 # 중기채가 T-bill보다 강함 = 위험선호
      if RSI(SPY,6) > 75: filter bottom 1 by RSI(13) of [UVXY, VIXY]
      else: equal-weight [SOXL, TECS]     # ← EDN 원문. 공개 단순판은 SOXL 단독. 둘 다 테스트
  else:
      if RSI(SPY,6) < 27:                                     # 극단 과매도
          if RSI(SHY,10) < RSI(HIBL,10): filter bottom 1 by RSI(7) of [SOXS, SQQQ]
          else:                          filter bottom 1 by RSI(7) of [SOXL, TECL]
      else:                                                   # "SMH for the Long Term"
          if price(SPY) > SMA(SPY,200):
              if RSI(TQQQ,10) > 79: filter bottom 1 by RSI(13) of [UVXY, VIXY]
              else: filter bottom 1 by RSI(21) of [SHV, FAS, TQQQ, UPRO, TECL]   # "Fund Surf"
          else:
              if   RSI(QQQ,10) < 30: SHY
              elif RSI(SPY,10) < 30: filter bottom 1 by RSI(10) of [SPXL, SHY]
              elif price(SMH) > SMA(SMH,20):
                  if RSI(SMH,10) > 50: filter bottom 2 by RSI(12) of [SOXS, UUP, SHY]
                  else: SOXL
              else: filter top 1 by RSI(10) of [SOXS, BSV]
  ```
- **데이터:** 일봉 BIL/IEF/SPY/UVXY/VIXY/SOXL/TECS/SHY/HIBL(2019-11-07 상장 → 백테스트 시작 제약)/SOXS/SQQQ/TECL/
  TQQQ/SHV/FAS/UPRO/QQQ/SPXL/SMH/UUP/BSV. HIBL 대체로 SPHB×3 합성 시 2011~ 확장 가능.
- **회전:** 매우 높음(연 80~150 전환 추정).
- **비판:** "RSI(BIL,7) vs RSI(IEF,7)"는 T-bill ETF(거의 직선)의 RSI라 수치적으로 불안정 — 소수점 가격
  반올림·배당락에 민감. 커뮤니티 최다 "오버피팅" 비판 대상. SOXL 100% 구간은 −60~90% 가능.

### B4. KMLM Switcher (Simon97)

- **출처:** [Composer "KMLM Switcher of Simon97 – Original"](https://www.composer.trade/trading-strategies/kmlm-switcher-of-simon97-original-syMn9OgFREE0wo8LtYsG),
  변형 "Testfolio", "No Shorts", "single pops".
- **Claimed:** 2022-04-13~2026-09-28 **523% / 36.1%**, Sharpe 2.73, OOS 표기 2024-07-22 (OOS Sharpe 1.66 vs SPY 1.07).
- **규칙 (Composer 서술 기반 재구성 — 창 길이는 10 가정, 정확 트리는 export 필요):**
  ```
  if RSI(QQQ,10) > 79 (또는 SPY>80):  UVXY        # "No Shorts" 판: BIL
  elif RSI(QQQ,10) < 30:              filter bottom 1 by RSI(10) of [TECL, SOXL, SPXL]
  elif RSI(XLK,10) > RSI(KMLM,10):    filter bottom 1 by RSI(10) of [TECL, SOXL, SVIX]   # 기술주>추세추종
  else:                               filter top 1 by RSI(10) of [SQQQ, TLT]             # 방어
  ```
- **데이터:** KMLM 2020-12 상장 → 사실상 백테스트 4년. SVIX 2022-03.
- **비판:** 표본이 2022 약세+2023~ AI 강세 단일 사이클. 523% CAGR은 과최적 경고 신호. **우선순위 낮음**.

### B5. Simple TQQQ RSI(10)>79 → UVXY (기준선)

- **출처:** [Composer](https://www.composer.trade/trading-strategies/simple-tqqq-rsi-mean-reversion-4hcYKZBIjhZo3Yg0NTQk)
- **Claimed:** 2011-10~2026-09 **81.2% / 58.0%**.
- **규칙:** `if RSI(TQQQ,10) > 79: UVXY else: TQQQ` (일간).
- **용도:** B1/B2/B3의 "추가 가지가 정말 가치를 더하나"를 재는 ablation 기준선. + `UVXY→BIL` 판도 함께.

### B6. IBS(Internal Bar Strength) 평균회귀 — QQQ 신호, TQQQ 실행

- **출처:** Pagonidis, *The IBS Effect: Mean Reversion in Equity ETFs*, NAAIM 2014
  ([PDF](https://www.naaim.org/wp-content/uploads/2014/04/00V_Alexander_Pagonidis_The-IBS-Effect-Mean-Reversion-in-Equity-ETFs-1.pdf));
  [QuantifiedStrategies IBS](https://www.quantifiedstrategies.com/internal-bar-strength-ibs-indicator-strategies/);
  LETF 적용 코드: [CazSyd/IBS-Strategy](https://github.com/CazSyd/IBS-Strategy)(push 2026-09-25).
- **규칙:**
  ```
  IBS = (close − low) / (high − low)      # 당일, 15:55 근사값 사용
  진입: IBS(QQQ) < 0.2  → 종가에 TQQQ 매수
  청산: IBS(QQQ) > 0.8 인 날 종가에 매도 (또는 close > 전일 high)
  선택 필터: close(QQQ) > SMA(QQQ,200) 일 때만 진입
  ```
- **데이터:** 일봉 OHLC. 15:55 근사 IBS vs 실제 종가 IBS 차이를 반드시 측정(체결가능성).
- **회전:** 40~70 왕복/년 → ≤$10 분할 없으면 비용 8~14%p/년.
- **비판:** 2010년대 후반 이후 에지 약화 보고. 종가 직전 체결이 전제.

### B7. Connors RSI(2) — 지수 신호, 3x 실행

- **출처:** Connors & Alvarez, *Short Term Trading Strategies That Work*(2008)/*High Probability ETF Trading*(2009);
  [QuantifiedStrategies RSI2](https://www.quantifiedstrategies.com/rsi-2-strategy/).
- **Claimed (SPY 1x):** RSI2<10 매수, RSI2>80 매도 → 9%/년, MDD 34%, 노출 28%. 200SMA 필터 시 6.8%/년, MDD 31%.
- **규칙:**
  ```
  진입: close(QQQ) > SMA(QQQ,200) AND RSI(QQQ,2) < 10  → 종가 TQQQ 매수
  청산: close(QQQ) > SMA(QQQ,5)  (Connors 원판)  또는 RSI(QQQ,2) > 80
  ```
- **회전:** 15~30 왕복/년.
- **비판:** 추세 붕괴(2008, 2022)에서 평균회귀 매수가 연속 손실. 3x 실행 시 손절 규칙 없으면 치명.

---

## 4. (C) 레버리지 리스크패리티

### C1. HEDGEFUNDIE's Excellent Adventure (HFEA)

- **출처:** [Bogleheads 스레드 Part I 2019-02](https://www.bogleheads.org/forum/viewtopic.php?t=272007) (40/60) →
  2019-08 55/45로 변경, [Part II](https://www.bogleheads.org/forum/viewtopic.php?t=288192);
  요약: [Optimized Portfolio (2025-09-08 갱신)](https://www.optimizedportfolio.com/hedgefundie-adventure/).
- **Claimed:** 원 백테스트 1987–2018 CAGR 23.75% 주장 → UPRO/TMF 실제 비용 반영 시뮬은 ≈18–19%
  ([Bogleheads 검증 인용](https://www.bogleheads.org/forum/viewtopic.php?t=272007)). 실운용 추적 $100(2019-10) → $137(2025-04).
  2022년 주식·장기채 동반 급락으로 고점대비 대략 −60% 이상(데이터로 확인할 것).
- **공개일:** 2019-02 (55/45는 2019-08) → **OOS 2019-09~**: 이미 2022 붕괴를 포함한 7년 OOS 존재.
- **규칙:**
  ```
  목표: 55% UPRO / 45% TMF
  리밸런스: 분기 첫 거래일(1/4/7/10월) 종가에 목표비중 복원
  변형(각각 별도 테스트):
    - 밴드: 어느 쪽이든 목표 ±10%p 벗어나면 즉시 리밸런스
    - 월간 리밸런스
    - 40/60 원판
    - UPRO 레그에 A1 필터(SPY<200SMA면 UPRO 몫을 BIL로)
    - 채권 대체/분산: TMF 일부를 KMLM(관리선물)·UGL(2x 금)로 — 2022 대비책 (Bogleheads 변형)
  ```
- **데이터:** 일봉 UPRO(2009-06)/TMF(2009-04). 이전은 SPX·장기채 지수 ×3 합성(1986~).
- **회전:** 4회/년.
- **비판:** 1982~2020 금리 하락 40년이 백테스트 전부 = 채권 레그의 구조적 순풍. 금리 상승+주식 하락
  동시 레짐(2022)에서 헤지 붕괴. 이 계좌의 "공격형" 목적에는 수익률이 A/B군보다 낮음(대조군 역할).

### C2. TQQQ/TMF 50/50 격월 + 폭락 필터

- **출처:** [QuantifiedStrategies "Triple Leveraged ETF Trading Strategy (44% Annual Returns)"](https://www.quantifiedstrategies.com/triple-leveraged-etf-trading-strategy/)
- **Claimed:** TQQQ 상장~(10년+) **CAGR 44.9%, 월말 기준 MDD 24.5%**, 총수익 5,800%+.
- **규칙:**
  ```
  기본: 50% TQQQ / 50% TMF, 2개월마다(짝수월 마지막 거래일) 50/50 리밸런스
  폭락필터: TQQQ 일간수익 ≤ −20% 발생 시 → 전량 IEF로
            TQQQ 가 폭락 직전 종가를 회복(초과)하면 → 다시 50/50
  ```
- **비판:** "월말 MDD"는 일중/일간 MDD를 과소 표기. −20% 단일일 트리거는 2020-03-16(−34%) 한 번 위주로
  맞춰진 값. 2022 완만한 동반하락엔 무력.

---

## 5. (D) 변동성 ETF

> 상품 변천: XIV(−1x) 2018-02-15 청산 · SVXY −1x → **−0.5x (2018-02-27)** · UVXY 2x → **1.5x (2018-02-27)** ·
> SVIX(−1x) 2022-03 · VIXY(1x) 2011-01. 2018-02-05 하루에 −1x 단기 VIX선물 지수 ≈ −90%.
> 롱온리 계좌 = "숏변동성"은 SVXY/SVIX **매수**로 구현. 반대쪽(롱변동성) 레그는 VIXY/UVXY 매수 또는 현금.

### D1. VRatio (VIX/VIX3M 기간구조)

- **출처:** Tony Cooper, *Easy Volatility Investing*, SSRN 2255327, 2013-02.
  [SSRN](https://www.ssrn.com/abstract=2255327) · [NAAIM PDF](https://www.naaim.org/wp-content/uploads/2013/10/00R_Easy-Volatility-Investing-+-Abstract-Tony-Cooper.pdf) ·
  재현: [QuantStrat TradeR 2017-11-14](https://quantstrattrader.com/2017/11/14/comparing-some-strategies-from-easy-volatility-investing-and-the-table-drawdowns-command/)
- **Claimed:** 원문 2004~2012 XIV/VXX 시뮬 고수익(원문 표 확인). **2013 공개 → 2018-02 XIV 소멸이 진짜 OOS 충격 시험.**
- **규칙:**
  ```
  15:50 ET:
    VR = VIX / VIX3M          (VIX3M = 구 VXV)
    if VR < 1: hold SVXY  (공격판: SVIX, 2022-03~)
    else:      hold VIXY  (원판 VXX)   | 롱온리 안전판: BIL
  선택 필터: VR < 0.95 일 때만 진입, VR > 1.0 즉시 청산(히스테리시스)
  ```
- **데이터:** 일봉 ^VIX, ^VIX3M(CBOE 무료, 2007-12~), SVXY/VIXY 2011~.
- **회전:** 10~30 전환/년.
- **비판:** 종가 신호로는 **장중/갭 폭락(2018-02-05 장후, 2020-02-24 주말, 2024-08-05 월요일 갭)**을 못 피함.
  VIXY 레그는 장기적으로 −(연 50%+) 흘러내림 → 롱온리에서는 BIL 판이 기본.

### D2. VRP (내재 − 실현 변동성)

- **출처:** Cooper 2013(동상).
- **규칙:**
  ```
  15:50 ET:
    HV10 = STD(ln(SPX_t/SPX_{t−1}), 10) × √252 × 100
    VRP  = SMA(VIX − HV10, 5)
    if VRP > 0: SVXY (또는 SVIX)     else: VIXY | BIL
  ```
- **Concretum 확장(D4):** "The Volatility Edge: A Dual Approach For VIX ETNs Trading", 2025-06-25,
  [SSRN 5316487](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5316487) — VRP + 기간구조 기울기 이중신호,
  동적 사이징, 2008–2025 **CAGR 16.3%, Sharpe ~1.0**, 주식상관 ~15%. 롱온리 변형·정확 임계값은 원문에서 확인 필요.

### D3. VIX 선물 Basis Roll (Simon & Campasano)

- **출처:** Simon & Campasano (2013/2014) *The VIX Futures Basis: Evidence and Trading Strategies*;
  [Quantpedia](https://quantpedia.com/strategies/exploiting-term-structure-of-vix-futures)
- **Claimed:** In-sample 2007–2011 **19.67%/년**, Quantpedia 표기 OOS **소폭 음수** → 약화 사례.
- **규칙 (선물 원판 → ETP 롱온리 프록시):**
  ```
  daily_roll = (VX1 − VIX) / (VX1 만기까지 영업일수)     # VX1 잔존 < 10영업일이면 VX2 사용
  if daily_roll >  0.10: SVXY 매수, 5거래일 보유
  if daily_roll < −0.10: VIXY 매수, 5거래일 보유   (롱온리 보수판: 현금)
  그 외: 현금
  (원판은 E-mini로 베타 헤지 — 롱온리 계좌에선 불가)
  ```
- **데이터:** CFE VX 선물 일봉(무료 CSV), VIX.

---

## 6. (E) 인버스 ETF 약세 레짐

### E1. QQQ 200-SMA 상시투자 TQQQ ↔ SQQQ (통제군)

- **출처:** A1의 "현금 대신 인버스" 변형, r/LETFs 단골 질문.
- **규칙:** `close(QQQ) > SMA(QQQ,200) → TQQQ ; else → SQQQ` (일간 종가).
- **예상:** 200SMA 아래 구간은 고변동·V자 반등이 잦아 SQQQ 3x가 휩쏘·경로의존 손실 → LRS(현금)보다
  열위일 가능성이 높음. **"인버스가 가치를 더하나"를 재는 대조군**으로만 사용.

### E2. FTLT 약세 브랜치 단독 슬리브

- **출처:** B1에서 분리(동일 규칙).
- **규칙:**
  ```
  if price(SPY) > SMA(SPY,200): BIL                  # 강세장엔 쉼
  else:
      if   RSI(TQQQ,10) < 31: TECL                   # 약세장 과매도 반등
      elif RSI(SPY,10)  < 30: UPRO
      elif price(TQQQ) < SMA(TQQQ,20): filter top 1 by RSI(10) of [SQQQ, TLT]   # 하락 추세 = 인버스
      else:
          if RSI(SQQQ,10) < 31: SQQQ                 # 약세장 반등이 과열 = 인버스로 페이드
          else: TQQQ
  ```
- **용도:** 강세 레짐 전략(A1/A2)과 결합 시 약세장에서 인버스가 순기여하는지 분해 측정.
- **비판:** 2011 이후 약세 레짐 표본 = 2011, 2015-16, 2018Q4, 2020-03, 2022, 2025-04 정도 → 통계력 낮음.

### E3. SOXL/SOXS 10일 급등 역추세

- **출처:** [Composer "SOXL/SOXS"](https://www.composer.trade/trading-strategies/soxlsoxs-zBYLpDcWNegfd8j46tfs)
- **Claimed:** 2022-12-28~2026-09-28, OOS(2025-03-11~) CAGR 16%, **MDD 66.9%**.
- **규칙:**
  ```
  if   CR(SOXL,10) > 31%: SOXS          # 10일 +31% 급등 → 반도체 인버스
  elif CR(SOXS,10) > 25%: SOXL          # 인버스 급등(=반도체 급락) → 반등 매수
  elif RSI(SOXL,10) < 31: TECL          # 창 길이 10 가정
  else: BOXX (T-bill 대용)
  ```
- **비판:** 2023~24 AI 랠리처럼 급등이 계속 이어지면 SOXS 3x가 연쇄 손실. MDD 67%.

---

## 7. (F) 개별주·핫스톡 스윙 & 데이트레이딩

> 공통: 롱온리 → 논문의 숏 절반은 버림(또는 인버스 ETF로 부분 대체 불가 — 개별주 인버스 없음).
> 소수점 주문으로 스톱주문이 되는지 불명 → **폴링 기반 가상 스톱**(1분봉 저가 체크) 구현 가정.

### F1. Stocks in Play 5분 ORB

- **출처:** Zarattini, Barbon, Aziz, *A Profitable Day Trading Strategy For The U.S. Equity Market*,
  SSRN 4729284, 2024-02-16 ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284),
  [PDF](https://www.wealth-lab.com/api/discussion/download/pdf/8007-ssrn-4729284-1-pdf)); QC 구현
  [Opening Range Breakout for Stocks in Play](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/).
- **Claimed (Table 2, 2016-01~2023-12, 롱+숏, $25k, $0.0035/주 수수료):** 총 **1,637%**, IRR **41.6%**,
  변동성 14.8%, **Sharpe 2.81**, MDD 12%, 알파 35.8%, 베타 0.00. 기본 ORB(필터 없음)는 29%/Sharpe 0.48.
- **규칙 (원문 그대로):**
  ```
  유니버스(매일): 시가 > $5 ; 14일 평균 거래량 ≥ 1,000,000주 ; ATR(14) > $0.50
  RVOL_j = (오늘 09:30–09:35 거래량) / (직전 14일 09:30–09:35 평균 거래량)
  후보: RVOL ≥ 100% 중 RVOL 상위 20종목
  방향: 첫 5분봉 양봉 → 롱(09:35 이후 OR high에 buy-stop) ; 음봉 → 숏(OR low에 sell-stop) ; 도지 → 없음
  손절: 체결가 − 0.10 × ATR(14)
  청산: 손절 미발생 시 장 마감(16:00) 전량 청산 (익절 목표 없음)
  사이징: 손절 시 손실 = 자본의 1% ; 총 레버리지 ≤ 4x
  롱온리 적용: 음봉 종목은 건너뜀. 레버리지 없음(현금) → 사이징 = min(1% 리스크 규칙, 자본/활성종목수)
  ```
- **데이터:** 전종목 1분봉(최소 개장 5분봉 + 14일 이력), 일봉 ATR. Polygon/Alpaca 급 필요 → 가장 무거움.
- **회전:** 매일 최대 20종목 왕복 → **≤$10 분할 없이는 불가**(0.2%×왕복 vs 평균 수익/거래 ~0.1R).
- **비판:** 롱숏 합산 결과, 베타 0 — 롱만 떼면 성과 불명. 소형 고변동주 슬리피지·호가 공백. 공개 후
  (2024-03~) 성과는 직접 측정해야.

### F2. QQQ 5분 ORB → TQQQ

- **출처:** Zarattini & Aziz, *Can Day Trading Really Be Profitable?*, SSRN 4416622 (2023-04).
  [Concretum](https://concretumgroup.com/can-day-trading-really-be-profitable/) · 독립 복제:
  [giovannibrusco/zarattini-2023-orb-qqq](https://github.com/giovannibrusco/zarattini-2023-orb-qqq)
- **Claimed:** 2016-01~2023-02, TQQQ로 **1,484%** vs QQQ 보유 169%.
- **규칙:**
  ```
  09:35 ET: 첫 5분봉(09:30–09:35) 양봉 → 09:35 시가 롱 ; 음봉 → 숏(롱온리: 스킵 또는 SQQQ 롱) ; 도지 → 없음
  손절: 첫 봉의 반대 극값(롱이면 첫 봉 low)
  익절: 진입가 + 10R (R = 진입 − 손절)
  그 외: 16:00 청산
  사이징: min(1% 자본 리스크 / R, 4x 레버리지 캡)   # 롱온리 현금계좌: 100% 캡
  ```
- **복제 결과(중요):** 슬리피지 0이면 Sharpe 1.06, **$0.02/주 슬리피지면 Sharpe 0.23, 손익분기 ≈ 2.2¢/주**,
  NQ 선물 확인필터 변형의 PnL 76%가 2022년 한 해. 손절 ~75%, 익절 2~3%, 종가청산 ~22%.
- **데이터:** QQQ/TQQQ 1분봉 2016~ (TQQQ 1분봉은 2010~ 가능).

### F3. Noise-Area 장중 모멘텀 ("Beat the Market")

- **출처:** Zarattini, Aziz, Barbon, SSRN 4824172, 2024-05-14. [SSRN](https://ssrn.com/abstract=4824172) ·
  후속 파라미터 개선: [Maróy SSRN 5095349](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5095349)
- **Claimed (2007~2024초, 순수익, 롱숏):** 총 **1,985%**, 연 **19.6%**, **Sharpe 1.33**.
- **규칙:**
  ```
  매일, 매 분 m (09:31~):
    move_d(m) = |close_d(m) / open_d − 1|
    σ(m) = 최근 14거래일의 move(m) 평균                      # 같은 시각의 평균 절대 이동
    UB = max(open_d, prev_close) × (1 + σ(m))                 # 갭다운이면 prev_close 기준으로 상단 상향
    LB = min(open_d, prev_close) × (1 − σ(m))                 # 갭업이면 하단 하향
  체크 시각: 매 HH:00, HH:30 (10:00부터)
    price > UB → 롱 (롱온리: SPY 대신 UPRO/TQQQ 롱, 또는 1x 섀도)
    price < LB → 숏 (롱온리: 현금 / 공격판 SPXU·SQQQ 롱)
  청산(트레일링): 롱 → price < max(UB, VWAP) 되면 청산 ; 매 체크시각마다 재평가
  16:00 전량 청산 (오버나이트 없음)
  원판 사이징: 변동성 타깃(일간 목표변동/σ_SPY, 최대 4x)
  ```
- **데이터:** SPY 1분봉 2007~(또는 QQQ). VWAP은 당일 1분봉 누적.
- **비판:** 레버리지 ETF로 대체 시 장중 레버리지 재조정이 아닌 일간 재조정이므로 장중 수익 ≈3x로 근사 가능
  (OK). 롱온리 절반 효과 불명. 2024-06 이후가 깨끗한 OOS.

### F4. VWAP 추세 (QQQ/TQQQ)

- **출처:** Zarattini & Aziz, *Volume Weighted Average Price (VWAP) The Holy Grail for Day Trading Systems*,
  SSRN 4631351, 2023-11-13. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4631351)
- **Claimed (2018-01-02~2023-09-28, $25k):** QQQ **671%**(Sharpe 2.1, MDD 9.4%), TQQQ **8,242%**($2.09M).
- **규칙:**
  ```
  1분봉, 09:31~15:59:
    VWAP_t = Σ(typical_price × vol) / Σ vol   (당일 누적)
    1분봉 종가 > VWAP → 롱 보유 ; < VWAP → 숏 (롱온리: 현금 또는 SQQQ)
    신호 바뀔 때마다 즉시 전환, 16:00 청산
  ```
- **비판:** 하루 수~수십 회 전환 → 비용·스프레드 극민감(원문 비용가정 확인 필요). 1분 단위 체결이
  토스 API/소수점에서 가능한지부터 확인. **이 계좌에선 1x 섀도 연구용**.

### F5. Episodic Pivot(EP) / 실적 갭 지속

- **출처:** Kristjan Kullamägi(Qullamaggie) — [How to master a setup: Episodic Pivots](https://qullamaggie.com/how-to-master-a-setup-episodic-pivots/),
  [3 timeless setups](https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/).
  학술 배경: PEAD([Quantpedia](https://quantpedia.com/strategies/post-earnings-announcement-effect)), 반대증거:
  Clinch 외 *From Drift to Reversal*([SSRN 5836584](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5836584)).
- **Claimed:** 공식 백테스트 없음(재량 트레이더의 실전 수익 주장).
- **규칙 (체계화판):**
  ```
  스캔(09:30–10:00): 실적/중대뉴스 당일 ; 갭 ≥ +10% ; 첫 15~30분 거래량 ≥ 2× 평소 동시간대 ;
                     직전 3~6개월 횡보/비관심(52주 고점 대비 −20% 이상 또는 60일 수익 |R|<15%) ;
                     가격 > $5, 평균 거래대금 > $20M
  진입: 개장범위(5/15/30분 중 하나 사전고정) 고가 돌파 시 매수
  손절: 당일 저가 (단 손절폭 ≤ 1×ADR(20); 넘으면 스킵)
  관리: 3~5거래일 후 1/3~1/2 매도 ; 잔량은 종가가 10일 SMA(빠른 종목) / 20일 SMA 하회 시 전량 매도
  사이징: 거래당 자본 리스크 0.5~1%
  ```
- **데이터:** 실적 캘린더(날짜·BMO/AMC), 분봉, 일봉. 무료 대안: 일봉 갭(시가/전일종가) + 당일 거래량으로 근사.
- **비판:** 생존편향(상폐 소형주) 없는 데이터 필수. 규칙이 재량 → 체계화 버전은 원작자 성과와 무관.

### F6. LETF 장후반 모멘텀 (Chan "trend day")

- **출처:** Ernie Chan, *Algorithmic Trading*(2013); [QuantRocket 2019-03-04](https://www.quantrocket.com/blog/leveraged-etf-intraday-momentum/),
  코드 [quantrocket-codeload/trend-day](https://github.com/quantrocket-codeload/trend-day).
- **Claimed:** 14개 LETF, ±6% 임계, 2008–2016 **31%/년, Sharpe 1.95** → **2017년부터 평탄**(decay).
- **규칙:**
  ```
  14:00 ET: r = price_14:00 / prev_close − 1   (각 3x ETF)
  r > +k → 롱, 15:45(또는 16:00) 청산 ; r < −k → 숏(롱온리: 스킵 또는 역방향 인버스 ETF 롱)
  k ∈ {2,4,6,8}%
  ```
- **메모:** 2026-08 Bloomberg "Leveraged ETF Boom Amps Up Intraday Momentum Plays" — LETF AUM 급증으로
  리밸런싱 수요 재부각 보도. 단일종목 LETF(NVDL/TSLL/MSTU 등) 기초자산에 적용하는 변형을 **2024~ OOS**로 테스트할 가치.

---

## 8. (G) 기타 신규

### G1. 오버나이트 TQQQ (종가 매수 → 익일 시가 매도)

- **출처:** 학술 Cliff, Cooper, Gulen (2008) "Return differences between trading and non-trading hours: like night and day";
  실무 백테스트 [Setup4alpha](https://setup4alpha.substack.com/p/tested-award-winning-trading-strategy-realtest).
- **Claimed:** QQQ 2010~ 오버나이트만 보유 **545%** vs 보유 643%, MDD는 더 작음. 200SMA 필터 시 301%(MDD 추가 감소).
  레버리지 ETF에 적용 시 지수 보유를 이길 수 있다는 서술.
- **규칙:**
  ```
  매일 15:58 ET: TQQQ 매수 (필터판: close(QQQ) > SMA(QQQ,200) 일 때만)
  익일 09:30~09:31 ET: 전량 매도
  ```
- **데이터:** 일봉 시가·종가. 실측 시 시가 첫 1분 스프레드 반영.
- **회전:** 252 왕복/년 → 0.2%×252 ≈ **연 50% 비용** → **≤$10 분할(무료) 없으면 원천 불가**.
- **비판:** 레버리지 ETF의 일간 리셋은 종가 기준이라 오버나이트 구간은 정확히 3x(OK). 2022 같은 해엔
  오버나이트도 손실. 토스의 시가 체결 품질(개장 동시호가 부재·스프레드) 검증 필수.

### G2. IBIT 오버나이트 (크립토 프록시)

- **출처:** Bespoke Investment Group(FA-Mag 보도 [링크](https://www.fa-mag.com/news/new-bitcoin-etf-chases-gains-that-come-while-wall-street-sleeps-86568.html)),
  후속 [Bespoke 2025-12-08](https://bespokeinvest.substack.com/p/bitcoin-ibit-after-hours-vs-intraday).
- **Claimed:** 2024-01 상장 이후 **종가→시가 +222%** vs **시가→종가 −40.5%**, 보유 +40%대. 이 효과를 노린
  "야간 전용" 비트코인 ETF 상장 신청까지 나옴(= 공개·과밀화 신호).
- **규칙:** `15:58 IBIT 매수 → 익일 09:30 매도`. 공격판: BITX(2x BTC, 2023-06) / MSTU(2x MSTR, 2024-09) 동일 타이밍.
  필터판: BTC-USD > SMA(BTC,50) 일 때만.
- **데이터:** IBIT 일봉 O/C(2024-01-11~), BTC-USD 일봉. 2024-01 이전 확장: BTC-USD 16:00 ET 가격 vs 09:30 ET 가격으로 합성(2014~).
- **비판:** 표본 2.7년. 원인(미국 장중 매도 흐름, 아시아 시간 매수)이 구조적인지 불명. 알려진 후 소멸 위험 → 2025-12 이후가 진짜 OOS.

### G3. BTC 추세 → IBIT/BITX/MSTU/CONL

- **출처:** Concretum *Catching Crypto Trends; A Tactical Approach for Bitcoin and Altcoins* 2025-04-08
  ([링크](https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/)) — Donchian 앙상블,
  변동성 사이징, Sharpe >1.5, BTC 대비 알파 10.8%/년; [QuantifiedStrategies BTC 추세](https://www.quantifiedstrategies.com/trend-following-and-momentum-strategies-on-bitcoin/)(짧은 MA가 최적이었다는 in-sample 결과).
- **규칙 (단순·사전고정판):**
  ```
  매 거래일 15:50 ET, BTC-USD 기준(24h 시장이지만 미국 장 시간 스냅샷 사용):
    신호 = 평균( [close > max(close, 직전 n일)] 돌파 상태 for n in {20, 55, 100} ) ∈ {0, 1/3, 2/3, 1}
          (돌파 후 close < min(직전 n/2일) 시 해당 n 상태 0)
    보유 = 신호 × 자본 → IBIT (공격판 BITX / MSTU / CONL, 보수판 IBIT)
  ```
- **데이터:** BTC-USD 일봉 2014~, IBIT 2024-01, BITX 2023-06, MSTU 2024-09, CONL 2022-08, MSTR/COIN 원주.
- **비판:** 주식 ETF는 주말·야간 미거래 → 신호-체결 괴리. MSTR/COIN은 BTC 베타 + 개별 리스크(희석·mNAV).

### G4. Turn-of-the-Month (TOM) 레버리지

- **출처:** TOM 문헌(Ariel 1987, Lakonishok & Smidt 1988); ETF 적용 [FSR 저널](https://openjournals.libs.uga.edu/fsr/article/view/3338).
- **규칙:** `월 마지막 거래일 전날(D−1) 종가 매수 TQQQ/UPRO → 다음 달 3번째 거래일 종가 매도 ; 나머지 BIL`.
  필터판: A1 추세필터 동시 충족 시만.
- **회전:** 12 왕복/년(비용 2.4%p/년 — ≤$10 분할 시 0).
- **비판:** 알려진 지 40년, 미국 대형주에서 약화 보고. 레버리지는 에지 증폭이 아니라 분산 증폭.

### G5. 3x ETF 모멘텀 로테이션 (+추세필터) — 카탈로그 구성 가설

- **출처:** 공개 원전 없음(Composer "Fund Surf"는 bottom-RSI = 역추세). 게이트용으로 **사전고정** 규칙을 제시.
- **규칙:**
  ```
  유니버스: TQQQ, SOXL, TECL, UPRO, FNGU(2018~; 2025 발행사 변경 여부 확인), TNA, CURE, LABU, FAS, DRN
  매주 금요일 15:50:
    if price(SPY) < SMA(SPY,200): BIL
    else: 점수 = CR(x,63) (3개월) → 상위 1개(공격) 또는 상위 2개 동일가중(보수)
          단, 상위 종목 close < SMA(x,50) 이면 BIL
  ```
- **비판:** 파라미터(63/50/주간/Top1)는 사전에 고정하고 격자 평균으로만 평가. 3x 섹터 ETF 간 상관 높음.

### G6. Accelerating Dual Momentum (레버리지판)

- **출처:** [EngineeredPortfolio 2018-05-02](https://engineeredportfolio.com/2018/05/02/accelerating-dual-momentum-investing/),
  [Allocate Smartly 추적](https://allocatesmartly.com/taa-strategy-accelerating-dual-momentum/),
  코드 [penny-vault/accelerating-dual-momentum](https://github.com/penny-vault/accelerating-dual-momentum).
- **규칙:**
  ```
  매월 마지막 거래일 종가:
    score(x) = (R1m + R3m + R6m) / 3
    if score(SPY) > score(SCZ) and score(SPY) > 0: UPRO      (원판 SPY)
    elif score(SCZ) > score(SPY) and score(SCZ) > 0: SCZ      (3x 해외소형 없음 → 1x 유지 또는 EURL 대체)
    else: R1m 더 높은 쪽 of [TLT, TIP]  (공격판: TLT → TMF)
  ```
- **데이터:** 월봉 SPY/SCZ(2007)/TLT/TIP. 공개 2018-05 → 2018-06~ OOS 8년.

---

## 9. 데이터 가용성 (상장일 · 구조 변경)

| 종목 | 내용 | 상장/데이터 시작 | 구조 변경·주의 |
|---|---|---|---|
| TQQQ / SQQQ | ±3x 나스닥100 | 2010-02 | — |
| UPRO / SPXU | ±3x S&P500 | 2009-06 | — |
| SPXL / SPXS | ±3x S&P500 (Direxion) | 2008-11 | — |
| TECL / TECS | ±3x 기술 | 2008-12 | — |
| SOXL / SOXS | ±3x 반도체 | 2010-03 | 지수 변경 이력 있음(ICE 반도체) |
| UDOW / SDOW | ±3x 다우 | 2010-02 | — |
| TMF / TMV | ±3x 20년+ 국채 | 2009-04 | — |
| FNGU | 3x FANG+ | 2018-01 (BMO ETN) | 2025년 발행 구조 변경 여부 확인 필요 |
| HIBL | 3x 고베타 | 2019-11 | Beta Baller 백테스트 시작 제약 |
| NVDL / TSLL / CONL | 단일종목 2x(CONL 초기 1.5x) | 2022-12 / 2022-08 / 2022-08 | 신규, 표본 짧음 |
| MSTU / MSTX | 2x MSTR | 2024-09 / 2024-08 | 초고변동, 괴리 |
| BITX / IBIT | 2x BTC / 1x BTC 현물 | 2023-06 / 2024-01-11 | — |
| UVXY | 단기 VIX 선물 레버리지 | 2011-10 | **2x → 1.5x (2018-02-27)** |
| SVXY | 단기 VIX 선물 인버스 | 2011-10 | **−1x → −0.5x (2018-02-27)**, 2018-02-05 ≈−80~90% |
| VIXY / SVIX | 1x 롱 VIX / −1x | 2011-01 / 2022-03 | SVIX는 XIV급 위험 |
| KMLM | 관리선물 추세 | 2020-12 | — |
| BIL / SHV / SGOV / BOXX | 현금 대용 | 2007 / 2007 / 2020-05 / 2022-12 | — |
| ^VIX, ^VIX3M | 지수 | 1990 / 2007-12 | CBOE 무료 |

---

## 10. 게이트 투입 시 공통 프로토콜 (제안)

1. **OOS 절단:** 각 전략 공개일 다음 달 1일 이후만 OOS. Composer 심포니는 "OOS start" 표기 대신
   보수적으로 2023-01-01(FTLT), 2022-11-01(Beta Baller), 2022-08-01(Holy Grail).
2. **체결 2버전:** 종가체결(낙관) vs t+1 시가(보수). 인트라데이(F)는 1분봉 + 스프레드 1~2¢ + 첫 1분 미체결 가정.
3. **비용 2버전:** 0.1%/side vs ≤$10 분할 무료(주문 수 제한·분할 가능성 가정 명시).
4. **Ablation:** B군은 B5(단순 RSI)·UVXY→BIL·임계값 ±3 격자. A군은 SMA 150/200/250 격자.
5. **1x 섀도:** 규제 게이트(기본예탁금) 때문에 3x 대신 1x 기초자산으로 동일 신호 실행한 성과도 병기.
6. **생존 기준 추가:** 단일일 최대손실(2018-02-05, 2020-03-16, 2024-08-05, 2025-04-03~07 스트레스 창) 필수 보고.
