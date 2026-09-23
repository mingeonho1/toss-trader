# Gate v2 — 전략 채택 평가 프로토콜 (정직한 다중검정 게이트)

작성일 2026-09-23 · 상태 설계(사양) · 구현 대상 `src/toss_trader/gate.py` · 선행 `reports/strategy_gate_2026-06-28.md`, `reports/improvement_roadmap_2026-06-28.md`, `docs/forward_strategy_plan.md`, `docs/microstructure_collector_plan.md`

> 목적: 10회 개선 루프에서 수십~수백 개 아이디어(vol targeting, 레버리지 ETF+추세필터, TSMOM, 섹터 모멘텀, 계절성, 단일종목 스윙/데이트레이딩/스캘핑)를 돌릴 때, **과적합·다중검정으로 인한 가짜 알파를 걸러내고** 실제 현금흐름(소액 시드 + 월 적립)에서 **비용 차감 후 진짜로 이득인 것만** 채택하기 위한 규율. v1(QQQ B&H 대비 블록부트스트랩 CI 하한>0)을 대체·확장한다.

---

## 0. 설계 원칙 (왜 이렇게 하나)

1. **실운용 현금흐름으로 평가**: 실계좌는 시드 ~$32 + 월 ~$35(₩5만) 적립(DCA, 매수전용)이다. 따라서 1등급 판정은 **동일 현금흐름을 먹인 DCA 시뮬레이션의 화폐가중 수익(XIRR)·최종자산**이고, 위험지표는 현금흐름 왜곡을 뺀 **단위자본(TWR) 곡선**에서 잰다. 두 곡선을 분리해 보고한다.
2. **다중검정을 1급 시민으로**: "많이 시도하면 우연히 좋아 보이는 게 나온다"가 이 프로젝트의 핵심 위험. 모든 시도를 원장에 적재하고(N 계수), Deflated Sharpe(DSR)와 White Reality Check(RC)로 **탐색 횟수를 페널티로 반영한 유의성**만 인정한다.
3. **OOS는 한 번만 엿본다(peek-once)**: 설계기간에서 파라미터를 확정→동결→홀드아웃 1회 평가. 홀드아웃을 보고 튜닝하면 그건 새 아이디어이고 원장 N이 늘어난다.
4. **비용은 티어로 정밀화**: FX 스프레드는 **환전(펀딩) 비용**이지 매매마다 내는 게 아니다. USD 상주자본으로 회전하는 전략은 매매당 수수료+반호가+슬리피지만 낸다. (§7)
5. **레인 분리**: 저회전 배분(레인1)·단일종목 스윙(레인2)·인트라데이(레인3)는 데이터·통계단위·비용·판정이 다르다. 각 레인의 게이트를 따로 둔다. (§8)
6. **보수 우선**: 불확실하면 비용은 높게, 유의성 임계는 엄격히, 표본은 짧게 잡는다. 백테스트는 언제나 실제보다 낙관적이라고 가정한다. **채택 = 소액 슬리브 + 포워드 페이퍼 확인 후 점증**이지 곧바로 전자본 투입이 아니다. (§6)
7. **순수 stdlib**: 모든 통계는 `math`, `statistics`(NormalDist로 Φ/Φ⁻¹), `random`, `json`, `datetime`, `bisect`로 구현. 외부 의존성 금지(키리스·재현성).

---

## 1. 벤치마크와 지표 — 실현금흐름 기준

### 1.1 두 개의 곡선을 분리한다

| 곡선 | 정의 | 여기서 재는 지표 | 왜 |
|---|---|---|---|
| **DCA 곡선(화폐가중)** | 실제 입금 스케줄(시드+월 적립)을 먹여 전략대로 매수한 포트폴리오 시가 | **최종자산(terminal wealth)**, **XIRR**, 달러 낙폭(behavioral MDD) | "내 돈에 실제로 무슨 일이 일어나나" |
| **단위자본 곡선(시간가중)** | t0에 $1 일시 투자, 이후 외부 현금흐름 없음. 전략의 순수 수익창출 과정 | **CAGR, Sharpe, MDD, Ulcer, CVaR, skew/kurt** | 리스크·위험조정은 입금 스케줄에 오염되면 안 됨(초기 낙폭이 소액에서 나서 MDD가 과소평가됨) |

**핵심 주의**: DCA 곡선의 MDD는 신규 입금이 계속 들어와 **완충되므로 리스크를 과소평가**한다. 그래서 리스크 판정은 반드시 단위자본 곡선에서 한다. 단, 사용자가 실제 계좌에서 보는 "달러가 얼마나 빠졌나"(behavioral drawdown)도 참고로 DCA 곡선에서 병기한다.

### 1.2 벤치마크 3종 (모두 동일 현금흐름·동일 날짜·동일 비용을 먹인다)

| 코드 | 정의 | 역할 |
|---|---|---|
| **B0 baseline DCA** | QQQ60/SCHD25/GLD15 매수전용 DCA | **교체 대상(현 운용).** 후보는 이걸 이겨야 채택 후보 |
| **B1 DCA-QQQ** | 100% QQQ 매수전용 DCA | 강한 단순 기준. "그냥 QQQ 사는 것"보다 나아야 함(sanity) |
| **B2(레버리지 전용)** | 동일 노출의 무레버리지 버전 | 레버리지 전략은 "레버리지 없이도 이겼나"를 봐야 함 |

채택 기준은 **B0 초과**(대체 이득), 온전성 기준은 **B1에 열위 아님**.

### 1.3 XIRR과 최종자산의 관계 (정직한 한계)

동일 현금흐름을 먹이면 XIRR은 최종자산의 단조증가 함수다 → **순위는 최종자산만으로 결정**되고 XIRR은 (a) 연율화된 해석, (b) 서로 다른 입금총액·기간 간 비교를 위해 병기한다. 유의성(우연 여부)은 최종자산 차이가 아니라 **§3의 단위자본 수익스트림 검정**에서 판정한다. 최종자산·XIRR은 "이득의 경제적 크기"를 말할 뿐 "우연이 아님"을 증명하지 않는다.

### 1.4 필수 보고 지표 세트

- 화폐가중: `terminal_wealth`, `xirr`, `xirr_vs_B0`(=후보XIRR−B0XIRR), 달러 MDD.
- 시간가중(단위자본): `cagr`, `sharpe`(rf 상수, 기본 0, §9 주의), `max_drawdown`, `ulcer_index`, `martin_ratio`(=UPI), `calmar`, `cvar_5`, `skew`, `kurt`, 연회전율, 비용/시드%.
- 스트림 통계: 일별(또는 거래별) 순수익 시계열 자체를 원장에 저장(재검정·RC용).

---

## 2. 워크포워드 / 앵커드 OOS

### 2.1 헤드라인 게이트 = 앵커드 홀드아웃(peek-once)

| 유니버스 | 설계기간(파라미터 확정·자유탐색) | 홀드아웃(동결 후 1회 평가) | 근거 |
|---|---|---|---|
| **롱윈도(QQQ/SPY/레버리지/TSMOM/단일종목)** | 2000–2012 (닷컴붕괴+GFC 포함, 추세추종 whipsaw 스트레스) | 2013–2026 (2018Q4·COVID·2022·빅테크 강세) | 데이터 존재/합성 가능 |
| **멀티에셋 배분(SCHD/GLD 포함)** | 2011–2019 | 2020–2026 | SCHD 상장 2011, GLD 2004 → 공통구간이 짧음(§9) |

규칙: **설계기간에서만** 그리드/튜닝/눈으로 보기. 확정 파라미터를 **동결**하고 홀드아웃을 **딱 1회** 평가. 결과는 성패 무관 원장에 기록. 홀드아웃을 본 뒤 바꾸면 = 새 아이디어(새 `idea_id`, N++), 그리고 가능하면 롤링 폴드로 홀드아웃을 회전.

`already_peeked(ledger, idea_id)`가 True면 홀드아웃 평가를 **거부**(코드로 강제). 이것이 peek-once의 실효 장치.

### 2.2 앵커드 워크포워드(로버스트니스용, 튜닝 아님)

확장창(anchored): train[2000, T], **purge/embargo gap = 최대 lookback(≈252봉)**, test[T+gap, T+gap+step]. step=252(1년). 각 폴드에서 설계기간 규칙으로 파라미터 재선택, test 구간을 이어붙여(stitch) 연속 OOS 트랙레코드 생성. 이걸로 (a) 서브구간 일관성(§4.2), (b) DSR용 OOS 스트림, (c) 폴드별 승률을 얻는다. **WF는 폴드마다 재적응하므로 그 자체가 낙관적 → 헤드라인 판정은 2.1의 단일 홀드아웃**, WF는 보강 증거.

- purge: test 구간의 신호가 train 데이터를 lookback으로 참조하지 못하게 gap 삽입(누수 차단).
- embargo: test 직후 구간도 다음 train에서 잠깐 제외(자기상관 누수 차단).
- 고급(선택): Combinatorial Purged CV(López de Prado)로 다수 OOS 경로 생성 → PBO(Probability of Backtest Overfitting) 산출. 여력 될 때만.

### 2.3 look-ahead / 체결 타이밍(레인2·3 필수)

현 백테스터(`backtest.run_backtest`)는 **당일 종가 신호→당일 종가 체결**이다. 월간 배분(레인1)엔 경미하지만 단일종목/인트라데이엔 치명적이다. 레인2/3 하네스는:

- 신호는 t 종가(또는 장중 τ)까지의 정보만.
- 체결은 **다음 봉 시가(next open)** 또는 현실적 지연(장중이면 τ+지연초). `fill_at="next_open"` / `fill_delay` 파라미터 강제.
- 슬리피지·반호가 반영(§7). 종가신호→종가체결 백테스트 결과는 레인2/3에서 **무효**.

---

## 3. 다중검정 통제 (핵심 난제)

### 3.1 시도 원장 `reports/trials_ledger.jsonl`

**append-only**, 1행=1시도(선택 가능했던 모든 평가). 모든 백테스트는 이 원장에 자동 적재하는 하네스를 통해서만 실행한다(수기·애드혹 백테스트는 게이트 증거로 **불인정** — N 누락은 다중검정 통제를 무력화).

```json
{"ts":"2026-09-23T12:00:00Z","idea_id":"tsmom_v1","config_hash":"a1b2c3",
 "lane":1,"universe":["QQQ","SPY","GLD","IEF"],"params":{"lookback":252,"vol_target":0.10},
 "period":"design|holdout|wf","window":["2000-01-01","2012-12-31"],
 "sr_daily":0.041,"sr_annual":0.65,"skew":-0.3,"kurt":6.1,"T":3024,
 "terminal_vs_B0":1.08,"xirr":0.14,"mdd":-0.31,"ulcer":8.2,"cvar5":-0.021,
 "peeked_holdout":false,"stream_ref":"streams/tsmom_v1_a1b2c3.json"}
```

- **N 계수**: DSR·RC는 원장에서 N을 읽는다. 그리드 20개를 돌리고 1개만 보고해도 N=20이 반영된다.
- 두 층위: **아이디어별 N**(같은 `idea_id` 필터) = 그 아이디어 게이트용. **프로그램 전체 N_eff**(전 원장, 상관 클러스터) = "10루프 전체에서 진짜 뭔가 찾았나"의 최종 방어.

### 3.2 유효 시도 수 N_eff (상관 클러스터링)

lookback 200 vs 201처럼 거의 동일한 config를 N으로 다 세면 (a) DSR이 과도하게 엄격해지고 (b) plateau 탐색이 역설적으로 페널티가 된다. 해결: **수익스트림 상관으로 그리디 클러스터링**.

```
n_eff_clusters(streams, theta=0.9):
  clusters=[]
  for name, r in streams:            # r: 표준화된 일/거래 수익 벡터
    joined=False
    for c in clusters:
      if pearson(r, c.medoid) > theta: c.add(name); joined=True; break
    if not joined: clusters.append(Cluster(medoid=r, members=[name]))
  return len(clusters)
```

N_eff = 클러스터 수. 보수적 폴백은 raw N(더 엄격). DSR·RC에 N_eff를 넣는다.

### 3.3 Deflated Sharpe Ratio (Bailey & López de Prado)

모든 SR·모멘트는 **일(또는 거래)단위, 비연율화**로 일관되게 쓴다.

**Probabilistic Sharpe Ratio**
```
PSR(SR*) = Φ( ( (SR_hat − SR*) · sqrt(T − 1) )
              / sqrt( 1 − γ3·SR_hat + ((γ4 − 1)/4)·SR_hat² ) )
```
- `SR_hat` = mean(r)/std(r) (비연율), `T`=관측수, `γ3`=표본 왜도, `γ4`=표본 첨도(정규=3), `Φ`=`NormalDist().cdf`.

**기대 최대 Sharpe(N회 시도의 우연 상한)**
```
SR*₀ = sqrt(V) · [ (1 − γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ]
```
- `V` = 시도들의 SR(비연율) 표본분산(원장에서), `γ`=오일러-마스케로니 0.5772156649, `N`=N_eff, `e`=exp(1), `Φ⁻¹`=`NormalDist().inv_cdf`.

**DSR = PSR(SR* = SR*₀)**. 판정: **DSR ≥ 0.95** → 다중검정을 반영해도 이 Sharpe는 "N회 우연의 최댓값"을 유의하게 초과. (0.90–0.95는 CONDITIONAL 경계.)

> 직관: 많이 시도할수록(N↑) SR*₀가 커져 통과 문턱이 올라간다. 시도 간 SR 분산(V)이 클수록도 문턱↑.

### 3.4 White Reality Check / Hansen SPA (계열 전체 데이터 스누핑)

DSR이 단일 후보의 Sharpe를 보정한다면, RC/SPA는 **"N개 중 최고가 벤치마크를 이겼나"를 가족단위(FWER)로** 검정한다. 둘 다 통과를 권장(가정이 다르므로 교차확인).

정의: 전략 k의 벤치(B0) 대비 일별 초과수익 `d_{k,t}` (단위자본 스트림), `d̄_k = mean_t d_{k,t}`.
검정통계량 `V̄ = max_k sqrt(T)·d̄_k`.

**정상 부트스트랩(Politis–Romano)** 인덱스:
```
stationary_bootstrap_indices(T, q, rng):   # 기대 블록길이 = 1/q, 일봉이면 q≈0.1(L≈10)
  idx=[rng.randrange(T)]
  for _ in range(T-1):
    idx.append(rng.randrange(T) if rng.random()<q else (idx[-1]+1) % T)
  return idx
```
(iid 부트스트랩과 달리 **자기상관을 보존** — 레버리지/추세 수익의 필수 요건.)

**RC p-value**(재중심화로 H0 부과):
```
for b in 1..B:
  I = stationary_bootstrap_indices(T,q,rng)
  V̄*_b = max_k sqrt(T)·( mean_t d_{k,I} − d̄_k )
p = (#{ V̄*_b ≥ V̄ }) / B
```
`p < 0.05` → 최고 전략이 탐색을 감안해도 벤치를 유의하게 초과.

**Hansen SPA(선택, 검정력↑)**: 각 k를 `ω̂_k`(sqrt(T)·d̄_k의 부트스트랩 표준편차)로 스튜던트화하고, `d̄_k ≥ −sqrt((ω̂_k²/T)·2·loglog T)`인 k만 max에 포함(consistent recentering)해 나쁜 전략의 검정력 손실 방지. 통계량 `max_k( sqrt(T)·d̄_k/ω̂_k, 0 )`, 부트스트랩 분포로 p 산출. stdlib로 구현 가능하나 RC가 1차, SPA는 보강.

### 3.5 레인2/3 짧은표본 — 거래단위 검정 (Sharpe-over-years 금지)

인트라데이는 데이터가 짧아(§8.3) 연단위 Sharpe가 무의미. **거래별 순PnL(비용 후)을 관측단위**로:
- `n_trades ≥ 200` (미만이면 통계주장 불가, 스크린만).
- per-trade t: `t = mean(pnl) / (std(pnl)/sqrt(n))`, **t ≥ 3.0** 요구(다중검정+비정규 보정으로 2보다 높게).
- 거래 PnL **정상 부트스트랩** 평균의 95% CI 하한 > 0(거래 자기상관 있으면 q로 블록화).
- profit factor, expectancy(기존 `metrics.py` 재사용) 병기.

---

## 4. 강건성 (튜닝 곡선의 함정 제거)

### 4.1 파라미터 이웃 평탄성(plateau)

각 파라미터를 중심값의 {×0.5, ×0.7, ×0.8, ×1.2, ×1.5}로 흔들어 재평가. 요구:
- 이웃 점의 **≥80%가 net 초과(vs B0) > 0** 유지, 그리고
- 이웃 **median Sharpe ≥ 0.9 × 중심 Sharpe**.

뾰족한 봉우리(중심만 좋고 이웃 급락)= 과적합 → FAIL. 넓은 고원 = 강건. **이웃 평가도 원장에 적재**하되 상관 클러스터(§3.2)로 N_eff에 흡수되어 과도 페널티를 피한다.

### 4.2 서브구간 일관성

롤링 3년 창(step 6개월)에서 후보가 B0를 이긴 창 비율 **≥ 60–70%** 요구. 전 구간 분포를 보고(특정 레짐에서만 이기는 전략 색출). 승리 창이 한 레짐에 몰리면 CONDITIONAL 강등.

### 4.3 비용 스트레스

- 레인1: **총비용 2×**(왕복 70→140bps 등)에서 net 초과(vs B0) > 0 유지.
- 레인3: **슬리피지 1.5–2×** + 반호가 확대에서 per-trade edge > 0 유지(§7).
- **손익분기 비용(bps)** 산출: 초과이득이 0이 되는 왕복 bps. 이 값이 현실 최악비용보다 **여유 있게** 커야 함.

### 4.4 DCA 시작일 랜덤화(행운의 진입 제거)

시작월 offset s ∈ {0,…,S}(예 첫 24–36개월)마다 전략DCA·B0 DCA를 동일하게 돌려 최종자산/XIRR 비교. 요구: 전략이 **≥70% offset에서 승**, median 승폭 > 비용스트레스 여유. 단일 시작일 운을 배제.

---

## 5. 리스크 · 파산위험 · 레버리지 한도

### 5.1 MDD 하드캡 = −50% (단위자본, OOS, 닷컴+GFC 포함)

정당화:
- **회복수학**: −50%는 +100%로 회복(가능). −65%→+186%, −80%→+400%(10년+). ~−50% 넘어가면 회복시간이 폭증.
- **행동적 이탈**: 소액 개인은 통상 −40~−50%에서 투매(capitulation). **바닥에서 버릴 전략은 실현 기대값이 음수** — 백테스트가 아무리 좋아도.
- **소액+DCA 완충**: −50%까지는 적립매수가 오히려 평단을 낮춰 생존 가능. 그 이상은 DCA로도 심리 방어 불가.

→ **단위자본 OOS MDD가 −50%를 깨면 수익 무관 FAIL.** 레버리지 전략은 추세필터가 **닷컴(2000–2002)·GFC(2008) 합성 OOS**에서 이 캡을 지킴을 **증명**해야 한다(그 구간 없는 백테스트는 레버리지 근거로 불인정).

### 5.2 레버리지 전용 추가 게이트

- **합성 데이터 불확실성 명시**: pre-2010 레버리지 시리즈는 `histdata.py`의 **모델(일수익×배수 − 보수 − 차입비)** 이지 실측이 아니다. pre-2010 결과는 "합성·저신뢰"로 라벨. 
- **차입비/보수 스트레스**: 연 경비 +100~300bps를 얹어도 생존해야 함.
- **변동성 감쇠(vol drag) 보고**: `배수×기초CAGR` vs 실제 CAGR 괴리. 괴리가 크면(횡보장 잠식) 취약 → 감점.
- **무레버리지 대조(B2)**: 레버리지 없이도 위험조정 우위인지. 아니면 "그냥 레버리지"일 뿐.

### 5.3 수익↑·MDD↑ 전략을 어떻게 비교하나 (핵심 판단)

수익이 높지만 MDD도 큰 후보는 **자동 통과 금지**. 세 가지를 모두 통과해야 함:
1. **위험조정비 우위**: Calmar(=CAGR/|MDD|) **그리고** Martin/UPI(=초과수익/Ulcer)에서 B0 초과. 둘 중 하나라도 열위면 "레버리지일 뿐" → CONDITIONAL(소액 슬리브) 또는 FAIL.
2. **변동성 매칭 비교**: 후보(또는 B0)를 **동일 목표변동성(예 연 10%)** 로 스케일한 뒤 최종자산 비교. 매칭 후 우위가 사라지면 그 이득은 리스크값 → 각하. (레버리지·vol-target의 진짜 시험.)
3. **하드캡 준수**(§5.1) + DSR/RC 유의(§3).

Sharpe만 높고 Calmar/Ulcer 열위 → 꼬리위험 은닉 신호. CVaR₅(단위자본)로 교차확인.

---

## 6. 결정표 (PASS / CONDITIONAL / FAIL)

각 축을 **AND**로 결합. 하나라도 FAIL 조건이면 전체 FAIL.

| 축 | PASS | CONDITIONAL | FAIL |
|---|---|---|---|
| **경제적 이득** | OOS 최종자산 ≥ B0 **그리고** ≥ B1 아님(온전성) | B0는 이기나 B1엔 소폭 열위 | OOS 최종자산 < B0 |
| **유의성(다중검정)** | DSR ≥ 0.95 **AND** RC p<0.05 | 하나만 통과 or DSR 0.90–0.95 | 둘 다 미달 |
| **평탄성** | §4.1 통과 | 경계 | 뾰족봉우리 |
| **서브구간** | 승률 ≥70% | 60–70% or 한 레짐 편중 | <60% |
| **비용스트레스** | 2×(또는 슬리피지 2×)에서 net>0 | 1.5×까지만 | 1×에서만 양(+) |
| **시작일 랜덤화** | ≥70% 승 | 55–70% | <55% |
| **리스크** | 하드캡 준수 + Calmar·Ulcer 비열위 | 캡 준수하나 MDD↑(슬리브 한정) | 캡(−50%) 위반 |

- **PASS**: 전 축 green. → 채택 절차(6.1) 진입.
- **CONDITIONAL**: 유의하고 강건하나 (a) 위험이 높아 소액 슬리브 한정, 또는 (b) 점추정은 통과인데 DSR 경계 → **포워드 페이퍼로만** 승격 여부 결정.
- **FAIL**: 하드게이트(캡·다중검정·비용) 하나라도 위반. **파라미터 튜닝으로 구제 금지**(구제 시도는 새 아이디어=N++).

### 6.1 "채택"의 운영적 의미 (백테스트는 실전이 아니다)

PASS라도 **전자본 투입이 아니다.** 채택 = 다음 점증 절차:
1. **소액 슬리브 개시**: 월 적립의 5–15%만 후보에 라우팅, 나머지는 B0 유지.
2. **포워드 페이퍼 확인**(기존 `scripts/forward_paper_compare.py`): ≥8–12주 또는 ≥2 리밸런스 사이클. 확인 항목 — 실현 회전율/비용이 모델의 ≤1.5×, 트래킹오차 허용범위, 일일손실 차단·멱등 정상, 운영버그 0.
3. **점증**: 연속 2개 확인창 통과 시에만 슬리브 확대. **레버리지 슬리브는 상한**(예 총자산의 일정%)을 둬 파산위험 제한.
4. **실거래 전환**은 PLAN.md §4 게이트(비용후 기대값>0·MDD통제·페이퍼≥4주·일일손실차단·**사용자 명시 승인**)를 그대로 상속.

이 슬리브+포워드+점증이 잔존 과적합에 대한 안전마진이다.

---

## 7. 비용 모델 티어 (FX = 펀딩비, 매매비 아님)

기존 `CostModel`은 **모든 매매에 FX 20bps**를 물려 왕복 70bps로 잡았다. 이는 매수전용 DCA엔 과대(매도 없으면 출구 FX 없음), 회전형엔 구조 오인이다. 정정:

| 요소 | 기호 | 값(보수) | 부과 시점 |
|---|---|---|---|
| 수수료(편도) | c | 10bps (US 0.1%, endDate 재확인) | **매매마다** |
| FX 스프레드 | φ | 20bps | **환전(KRW→USD 펀딩, USD→KRW 출금) 때만** — 매매마다 아님 |
| 반호가(half-spread) | h | 유동성 티어별: 메가캡ETF/대형주 1–3, 유동대형 3–8, 중형/변동성 15–40+ | **매매마다** |
| 슬리피지 | s | 5bps(대형); 인트라데이 스트레스 ×1.5–2 | **매매마다** |

**티어 A (펀딩 구동, 레인1 DCA)**: 매수는 갓 환전한 KRW로 → `c+φ+s`. 매도 없음(buy-only) → 출구비용 0. (리밸런스 매도가 있고 USD 유지면 그 매도는 `c+s`, FX 없음.)

**티어 B (USD 상주자본, 레인2·3)**: 자본이 이미 USD. 왕복 = `2·(c + h + s)`, **FX는 자본 최초 환전 1회·출금 1회만**(전 자본에 1회 상각). 회전이 잦아도 FX가 매매를 죽이진 않으나 **수수료+반호가+슬리피지가 복리로 누적**.

**인트라데이 손익분기(정직한 규모감)**: 왕복 `2·(10+5+5)=40bps`(FX 없음) 가정 시 하루 5회전 = 일 200bps 비용 → **일 총(gross) edge가 >2%여야 겨우 본전.** 스캘핑은 이 벽이 본질. §4.3에서 손익분기 bps를 못박고, 레인3은 슬리피지 1.5–2×·반호가 확대에서도 edge>0을 요구.

구현: `CostModel`에 `half_spread_bps`(티어별), `fx_on_trade: bool`(티어A=True/티어B=False) 추가. 펀딩 이벤트에만 φ를 부과하는 회계 분리. (본 사양은 인터페이스만 규정; 구현은 executor에 위임.)

---

## 8. 레인 구조 (데이터·통계단위·판정이 레인마다 다름)

### 8.1 레인 1 — 저회전 배분 (DCA-native)

- 데이터: 일봉(롱윈도/멀티에셋, §2.1). 비용 티어 A.
- 통계단위: 단위자본 일수익 스트림(DSR/RC) + DCA 최종자산/XIRR.
- 게이트: §1–6 전체. 채택 가능(슬리브+포워드).

### 8.2 레인 2 — 단일종목 스윙 (수일~수주 보유)

- 데이터: 일봉. 비용 티어 B(+ 반호가 티어). 체결 = next open(§2.3).
- **생존편향 규칙**:
  - 현재 S&P100/Nasdaq100 구성종목 = 생존자 → **명시적으로 편향된 상한(upper bound)** 로만 사용. 이 편향 유니버스에서조차 실패하면 → 확정 FAIL(값싼 기각).
  - 선호: **point-in-time(PIT) 구성종목**(과거 시점 멤버십). 키리스로 PIT 확보 곤란하면 **ETF 프록시**(섹터 SPDR·팩터 ETF)로 로직 검증 — ETF는 단일종목 생존편향이 없음.
  - **haircut**: 편향 유니버스 결과는 생존/상장폐지 프리미엄만큼 깎아 요구선을 올린다(예 B0 대비 연 net ≥ +3–5%p를 "진짜"로 인정하기 전 예비마진). 상장폐지 수익(패자 −100%)을 롱온리 현재멤버가 **경험하지 않음**을 결과에 명시.
  - 종목 유니버스·랭킹 규칙은 **사전 고정**(사후에 이긴 종목 고르기 금지). 섹터 모멘텀도 SPDR 전체집합 고정.
- 통계: 일수익 DSR/RC **그리고** 거래단위 t/부트스트랩(§3.5) 병행. 단일종목 특이위험 크므로 **분산(≥N종목)·포지션캡** 요구, 종목당 파산위험 제한.
- 판정: §6 표 + 생존편향 haircut 반영. 채택 가능하나 레인1보다 문턱 높음(편향·특이위험).

### 8.3 레인 3 — 인트라데이 / 데이트레이딩 / 스캘핑

**Yahoo 인트라데이 데이터 한계**: 1m ≈ 최근 7–30일, 5m ≈ 60일, 1h ≈ 730일. → **연단위 Sharpe 불가**. 짧은표본 전용 규칙:

- 통계단위 = **거래별 순PnL(비용 후)**. `n_trades ≥ 200` 미만이면 통계주장 불가.
- **per-trade t ≥ 3.0** + 거래PnL 정상부트스트랩 CI 하한 > 0(§3.5).
- 비용: 티어 B, **슬리피지 1.5–2× 스트레스 통과 필수**(장중 체결·역선택·종가체결 낙관 보정). 반호가는 실측(마이크로구조) 없으면 보수적으로 크게.
- 체결: next-bar/지연 체결, 종가신호→종가체결 무효(§2.3).

**레인 3 결정규칙 — 백테스트로 "채택"하지 않는다. 포워드 페이퍼로 종결한다.**

```
1) 백테스트(짧은표본)는 필요조건일 뿐 — n≥200 & t≥3 & 부트CI하한>0 & 슬리피지2× 생존.
   → 통과 못하면 즉시 폐기(값싼 기각).
2) 통과 시에도 "채택" 아님. 마이크로구조 수집 → 통계검증 → 페이퍼 순서
   (docs/microstructure_collector_plan.md 원칙 상속).
   - scripts/collect_microstructure.py로 실시간 호가/틱 아카이브 축적(≥수주~수개월).
   - scripts/analyze_microstructure.py: order-book imbalance vs 1–5분 후 mid 수익 상관이
     비용(티어B 왕복)을 넘는지. 넘는 신호만 페이퍼 승격.
3) 포워드 페이퍼(scripts/forward_paper_compare.py)에서 실지연·실호가로 재검증.
   실 마이크로구조 표본에서 edge>비용이 지속될 때만, 최소 슬리브로 실거래 검토.
```

이유: 인트라데이 백테스트는 (a) 데이터가 짧고 (b) 체결가정에 극도로 민감해 **backtest 신뢰불가**. 유일하게 정직한 검증은 **실 마이크로구조 기반 포워드**다. 레인3의 산출물은 "채택"이 아니라 "포워드 페이퍼 편입 여부".

---

## 9. 데이터 주의사항 · 함정 (정직성 섹션)

- **ETF 상장시점**: SCHD 2011, GLD 2004, 섹터SPDR ~1998. B0(QQQ60/SCHD25/GLD15)는 2000까지 온전히 백테스트 불가. → (A) 멀티에셋은 공통구간(2011+)에서 평가하거나, (B) `histdata.py`가 제공하는 문서화된 프록시(배당지수·현물금)로 back-extend하되 **"합성·2차·저신뢰" 라벨**. 롱윈도 테일리스크·레짐 테스트는 단일/소수자산(QQQ/SPY/레버리지)로 수행.
- **합성 레버리지(pre-2010)**: 모델 가정(배수·보수·차입비)이지 실측 아님 → §5.2 스트레스·라벨 필수.
- **짧은 홀드아웃(멀티에셋 2020–2026)**: 독립 레짐 소수(COVID·2022) → DSR/RC 검정력 낮음 → **겸손하게**, 포워드 페이퍼 필수.
- **계절성(sell-in-May·turn-of-month·산타랠리)**: 26년 = 사실상 26개 "5월" → 유효표본 극소. 신호검정은 **연블록(block=252) 부트스트랩**·연단위 해상도로, 표본이 거의 없으므로 검정력 최저 → **거의 채택 불가**. 큰·안정 효과 + 경제적 근거 + 전·후반 양쪽 OOS 통과가 아니면 FAIL.
- **생존편향**(레인2, §8.2), **look-ahead**(§2.3), **거래단위 통계**(§3.5) 재확인.
- **무위험수익률(rf)**: 2000–2026은 T-bill이 0~5% 변동 → 절대 Sharpe는 rf에 민감. 결정은 **차분검정(vs 벤치, rf 상쇄)** 위주로 하고, 절대 Sharpe/DSR은 rf=0 고정(약간 낙관)임을 명시. 여력 되면 3M T-bill 시계열로 초과수익 계산.
- **Yahoo 수정종가**: 배당 재투자(총수익)·분할 반영 가정. 전 자산 동일 기준(총수익)으로 일관.
- **원장 우회 금지**: 하네스 밖 백테스트는 게이트 증거 불인정(§3.1).

---

## 10. 구현 체크리스트 — `src/toss_trader/gate.py` (순수 stdlib)

```python
from __future__ import annotations
import math, json, random, bisect
from datetime import date
from statistics import NormalDist
_Z = NormalDist()                      # _Z.cdf(x)=Φ, _Z.inv_cdf(p)=Φ⁻¹
_EULER = 0.5772156649015329

# ── 화폐가중 ─────────────────────────────────────────────
def xirr(flows: list[tuple[date, float]], guess: float = 0.1) -> float:
    """NPV(r)=Σ cf_i(1+r)^(-Δt_i/365). 입금=음(-), 최종청산=양(+).
    Newton 반복; 발산·미분≈0이면 [-0.9999,10] 이분법 폴백; 부호변화 없으면 nan."""
def cashflows_from_dca(dca_result) -> list[tuple[date, float]]: ...

# ── 시간가중/리스크 (단위자본 곡선) ──────────────────────
def cagr(equity: list[float], days: int) -> float: ...
def sharpe(returns: list[float], ppy: int = 252, rf: float = 0.0) -> float: ...
def max_drawdown(equity: list[float]) -> float: ...
def ulcer_index(equity: list[float]) -> float:
    """R_t=100·(eq_t/peak_t−1); UI=sqrt(mean(R_t²))."""
def martin_ratio(equity, days, ppy=252, rf=0.0) -> float:      # UPI = (ann.ret−rf)/UI
def calmar(cagr_: float, mdd: float) -> float:                 # cagr/|mdd|
def cvar(returns: list[float], alpha: float = 0.05) -> float:
    """정렬 후 하위 α분위 평균(음수=손실). k=ceil(αT), mean(worst k)."""
def skew_kurt(returns: list[float]) -> tuple[float, float]:    # (skew, kurt; 정규=3)

# ── 다중검정 ─────────────────────────────────────────────
def stationary_bootstrap_indices(T: int, q: float, rng: random.Random) -> list[int]: ...
def probabilistic_sharpe_ratio(sr_hat, sr_star, T, skew, kurt) -> float: ...
def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """sqrt(V)·[(1−γ)Φ⁻¹(1−1/N)+γΦ⁻¹(1−1/(N·e))]."""
def deflated_sharpe_ratio(returns, sr_trials: list[float], n_eff: int | None = None) -> float:
    """일단위 SR_hat/skew/kurt/T 계산 → V=var(sr_trials), SR*₀=expected_max_sharpe →
    PSR(SR*₀). n_eff 없으면 len(sr_trials)."""
def whites_reality_check(diffs: dict[str, list[float]], q: float = 0.1,
                         B: int = 5000, rng=None) -> tuple[float, dict]:
    """재중심화 정상부트스트랩 RC p-value + 전략별 통계."""
def hansen_spa(diffs, q=0.1, B=5000, rng=None) -> float:       # 선택(스튜던트화+consistent recenter)
def n_eff_clusters(streams: dict[str, list[float]], theta: float = 0.9) -> int:  # 상관 그리디 클러스터

# ── 강건성 ───────────────────────────────────────────────
def plateau_test(eval_fn, base_params: dict,
                 grid=(0.5,0.7,0.8,1.2,1.5), keep_frac=0.8) -> dict: ...
def subperiod_consistency(strat_curve, bench_curve, dates,
                          window_years=3, step_months=6) -> dict: ...   # 승률 등
def cost_stress(eval_fn, mult: float = 2.0) -> dict: ...
def breakeven_cost_bps(eval_fn, lo=0.0, hi=0.02) -> float:    # net초과=0 되는 왕복 bps 이분탐색
def start_date_randomization(dca_fn_strat, dca_fn_bench, offsets: list[int]) -> dict: ...
def vol_match_scale(returns: list[float], target_ann_vol: float = 0.10, ppy=252) -> float:  # 스케일계수

# ── 거래단위(레인2/3) ────────────────────────────────────
def trade_tstat(pnls: list[float]) -> float:                 # mean/(std/sqrt(n))
def trade_pnl_bootstrap_ci(pnls, q=0.1, B=5000, rng=None, alpha=0.05) -> tuple[float,float,float]:
def profit_factor(pnls: list[float]) -> float:               # Σwin / |Σloss|

# ── 워크포워드 ───────────────────────────────────────────
def anchored_holdout(dates: list[date], design_end: date) -> tuple[list[int], list[int]]: ...
def walk_forward_splits(dates, train_days, test_days, step_days, embargo_days) -> list[tuple]: ...

# ── 시도 원장 ────────────────────────────────────────────
def ledger_append(path: str, record: dict) -> None:          # append-only JSONL
def ledger_count(path: str, idea_id: str | None = None) -> int: ...
def ledger_trial_sharpes(path: str, idea_id: str | None = None) -> list[float]: ...
def already_peeked(path: str, idea_id: str) -> bool:         # 홀드아웃 1회 강제

# ── 결정 ─────────────────────────────────────────────────
def decide(metrics: dict) -> str:                            # "PASS"|"CONDITIONAL"|"FAIL" (§6 표 인코딩)
```

**알고리즘 메모(정확성 포인트)**
- Φ/Φ⁻¹은 `statistics.NormalDist`(Py3.8+, 본 repo 3.13 확인). 근사식 자작 불필요.
- DSR/PSR: SR·모멘트 전부 **비연율 일단위**로 통일(연율화 금지). `T-1` under-root.
- `expected_max_sharpe`: N은 **N_eff**(§3.2), V는 원장 시도 SR(비연율)의 표본분산.
- 부트스트랩은 전부 **정상 부트스트랩**(iid 아님) — 자기상관 보존이 필수.
- RC 재중심화(`− d̄_k`)를 빠뜨리면 H0가 부과되지 않아 p 무효 — 반드시 포함.
- `xirr`: 부호변화 없으면(전손실 등) nan 반환하고 최종자산으로 폴백 해석.
- 기존 자산 재사용: `costs.CostModel`(티어 확장), `backtest.run_dca/run_backtest`(fill_delay·cost 티어 인자 추가), `metrics.compute`(win_rate/payoff/expectancy). 게이트는 이들 위에 얹는다.

---

## 부록 A. v1 → v2 매핑

| v1 (현 `scripts/backtest_strategies.py`) | v2 |
|---|---|
| QQQ B&H 대비 일별초과 블록부트스트랩 CI 하한>0 | 유지하되 **RC(계열 전체)+DSR(다중검정 보정)** 로 승격, 벤치를 **B0 baseline DCA**로 |
| 단일 9년 창 | **앵커드 홀드아웃(peek-once) + WF stitched** |
| 총수익률/Sharpe/MDD | + **XIRR/최종자산(DCA), Ulcer/Calmar/CVaR, vol-match** |
| 시도 계수 없음 | **trials_ledger.jsonl + N_eff** |
| 왕복 70bps 고정(FX 매매마다) | **비용 티어 A/B(FX=펀딩비)** |
| 저회전만 | **레인1/2/3 분리, 레인3은 포워드로 종결** |
| 즉시 채택/기각 | **PASS/CONDITIONAL/FAIL + 슬리브+포워드 점증** |
