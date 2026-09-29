# toss-trader

토스증권 OpenAPI 기반 **미국주식 스윙 자동매매** (결정론적 규칙 + Gemini 보조, 페이퍼 우선).

설계 전반과 진행상황은 **[PLAN.md](./PLAN.md)**, 일별 기록은 **[journal/](./journal/)** 참고.

## 빠른 시작
```bash
# .env에 자격증명 2줄 (PC 웹에서 발급): API_KEY=... / SECRET_KEY=...
PYTHONPATH=src python scripts/smoke_test.py   # 읽기 전용 실API 검증 (주문 없음)
PYTHONPATH=src python scripts/selftest.py     # 결정론 자가검증 (키 불필요)
```

요구사항: Python ≥ 3.11. 핵심 클라이언트는 **표준 라이브러리만** 사용(외부 패키지 0).
`.env` 자격증명 이름은 `API_KEY`/`SECRET_KEY`(또는 `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET`) 둘 다 지원.

## 안전장치
- `TRADING_MODE=paper` 가 기본. 실거래(`live`)는 PLAN.md §4 검증 게이트 통과 후에만.
- 주문은 멱등키(clientOrderId)로 중복 방지, 429는 Retry-After 준수, 401은 토큰 자동 재발급.
- `X-RateLimit-*` 헤더 적응 throttle. SSL CA 번들 자동 탐색(검증은 항상 유지).
- `LiveBroker`는 `require_live` 가드로 paper 모드에서 실주문을 막는다.

## 현재 상태 (2026-09-28)
> 👉 **먼저 읽을 것: [docs/final_recommendation.md](./docs/final_recommendation.md)** — 10사이클 루프 엔지니어링 결론.

- ✅ `TossClient` — 공식 OpenAPI **v1.2.17** 정합화(DST 주문창, 409/멱등, 토큰 공유캐시, 조건주문 래퍼)
- ✅ 비용: 표준 수수료 **0.1%**, 주문당 **$10 이하 무료** → DCA 매수 자동 분할(`fees.py`).
  **환전이 최대 비용**: 평일 09:00–15:30 KST 0.05% vs 그 외 0.5% → 앱에서 주간에 미리 환전(`scripts/fx_advice.py`).
- ✅ 검증 인프라: Gate v2(DSR·RC·SPA·peek-once 원장), 키 없는 장기 데이터(FRED 1971~, Shiller 1871~), 리서치 백테스터
- ✅ 99개 아이디어·579 시도 → **알파 전략 채택 0건**(타이밍·평균회귀·캘린더·단일종목·레버리지 모두 게이트 FAIL)
- ✅ 확정 이득: 양도세 250만원 공제 하베스팅(`scripts/tax_report.py`), 배당세 효율 배분(세후 연구 `reports/cycle8_c8b_allocation_tax.md`)
- 🟡 조건부: 생애주기 레버리지 슬리브(옵트인·기본 OFF, 30년+ 지평 한정) — 아래 섹션
- ⏳ 포워드 증거 수집: 초단타 섀도(ORB·장중모멘텀·갭앤고), 매일 스코어보드(설치는 선택)
- 기본 운용 = **적립식(DCA) + 분산 바이앤홀드** (QQQ60/SCHD25/GLD15, 변경은 위험선호 결정 — 권고 메모 §4)

```bash
python scripts/run_dca.py --backtest   # 분산안 과거 검증
python scripts/run_dca.py              # 실계좌 적립 매수 플랜(dry-run, 주문 없음)
# python scripts/run_dca.py --execute  # 실주문(live·정규장에서만)
```
근거·수치는 `reports/strategy_gate_2026-06-28.md`, `reports/improvement_roadmap_2026-06-28.md`.

## 📒 매일 기록: `paper-log` 브랜치
**코드는 `main`, 매일의 결과는 [`paper-log`](https://github.com/mingeonho1/toss-trader/tree/paper-log) 브랜치**에 쌓인다.
맥의 launchd 작업(`com.tosstrader.scoreboard`, 매일 06:25 KST, 주문 없음)이 스코어보드·페이퍼 랩·일일 판단을
돌린 뒤 `scripts/publish_records.py`로 커밋·푸시한다(작성자 mingeonho1). 맥에는 쌓아 두지 않는다.

| 경로 (paper-log) | 내용 |
|---|---|
| `latest/decision_latest.md` | **오늘의 판단** — 전략별 상태(WARMUP→…→LIVE_READY/DEMOTED), 실계좌 추천, 어제와 달라진 점 |
| `latest/paperlab_latest.md` | 공격형 페이퍼 랩 리더보드(27개 전략, $1k·$36 가상계좌, 토스 실수수료) |
| `latest/scoreboard_latest.md` | 단계별 실행 상태(실시세/캐시 모드, 실패 단계) |
| `latest/intraday_shadow_latest.md` | 초단타 섀도 규칙별 누적 성과(n≥200 전까지 '수집 중') |
| `daily/<날짜>/` | 날짜별 리더보드·판단 사본 |
| `state/` | 장부 상태(전략별 state/trades/equity), 판단 상태기계, 작업 요청(`requests.jsonl`) |
| `intraday/` | 키 없이 당일치만 받을 수 있는 분봉의 누적본 |

- 올리는 파일은 **허용 목록만**이다(.env·토큰 캐시는 구조적으로 제외 + 비밀 패턴 검사).
- 토스 API 허용 IP가 막히면 자동으로 캐시 모드로 폴백하고 `scoreboard_latest.md` 모드 줄에 사유가 찍힌다.
- 로컬에서 보기: `git fetch origin paper-log && git show origin/paper-log:latest/decision_latest.md`

## 🔁 루프 엔지니어링: 에이전트 팀 + 도메인 하네스
매일 판단 루프(결정론)가 페이퍼 결과로 판단을 바꾸고, 에이전트 팀 루프(`/loop-cycle`)가 그 판단이 만든 작업을 처리한다.
- 팀(`.claude/agents/`): `paper-analyst`(관측) · `scout`(기법·뉴스·규제 정찰) · `experimenter`(사전등록 실험) ·
  `auditor`(반박·재실험 요청) · `risk-officer`(주문경로·규제·비밀) · `decider`(편성·실계좌 추천 결정)
- 하네스(`scripts/harness/`, `docs/harness.md`): 이 프로젝트가 **실제로 겪은 실패**만 검사한다. Claude Code 훅
  (`.claude/settings.json`)이 위험 명령을 막고, 에이전트가 끝내려 할 때 검사를 돌려 실패하면 되돌려 보낸다. CI가 main에 같은 검사.
- 판단 규칙: `docs/loop/decision_rules.md`, 전체 구조: `docs/loop/README.md`.

## 라이프사이클 레버리지 슬리브 (옵트인 · 기본 비활성)
Ayres–Nalebuff *Lifecycle Investing* 의 생애주기 글라이드 노출을 **계좌의 소액 슬리브**에만
적용하는 **선택형** 정책이다. QQQ(1x)+QLD(2x) 혼합으로 목표노출 E=clip(0.8·(W+PV)/W, 1, 2)를
구현한다(W=이미 투자된 슬리브 자산, PV=남은 적립의 현재가치). 젊을수록(W 작음) ~2x 로 시작해
자산이 커지며 1x 로 글라이드한다.

> 🔴 **채택 보류(기본 OFF 유지).** 이 정책은 **항상 dry-run 이 기본**이고 **자동으로 켜지지 않는다**.
> 검증 경과: NDX 1986–2026 20y 사전등록 규칙 PASS(1.36×/1.24×), NASDAQCOM 1971–85 OOS PASS →
> 적대적 감사 **WEAKENED**(유효표본≈2, 닛케이 FAIL) → **Shiller 1871–2026 심층 OOS에서 20y 우위
> 대부분 소멸(pre-1986 시작 median 1.04× → FAIL)**, 30년 지평만 PASS(1.30×).
> 즉 성과는 1986–2026 NDX 레짐 의존적이다. 쓴다면 **30년 이상 지평·소액 슬리브(≤10–15%)** 한정.
> 근거: `reports/cycle6_c6a_lifecycle_audit.md`, `reports/cycle7_c7b_killswitch.md`, `reports/cycle7_c7c_shiller.md`.

**정책 함수:** `src/toss_trader/policy_lifecycle.py`
(`lifecycle_target` / `allocation_for_exposure` / `deposit_plan`). 연구 재현·근거는
`experiments/c5a_lifecycle.py`, `reports/cycle5_c5a_lifecycle.md`.

**설정(.env / 환경변수):**
| 키 | 기본값 | 의미 |
|---|---|---|
| `POLICY` | `dca` | `dca`(기본) 또는 `lifecycle`. lifecycle 이라도 슬리브 0 이면 DCA 로 폴백. |
| `LIFECYCLE_SLEEVE` | `0` | 라이프사이클로 운용할 계좌 비율(0 → **off**). 예: `0.3`. |
| `LIFECYCLE_PLAN_YEARS` | `25` | 생애 적립 계획(년) — PV 산정용. |
| `LIFECYCLE_EMAX` | `2.0` | 슬리브 목표노출 상한(2.0 = 2x). |

```bash
PYTHONPATH=src python scripts/run_dca.py --policy lifecycle            # 슬리브+기본배분 플랜(dry-run)
PYTHONPATH=src python scripts/forward_lifecycle_paper.py --reset       # 페이퍼 슬리브 vs DCA-QQQ 누적
# TRADING_MODE=live PYTHONPATH=src python scripts/run_dca.py --policy lifecycle --execute  # 실매수(정규장)
```
실행기는 **매수만** 접수한다(분할·멱등 재사용). 밴드 초과 시 나오는 **디레버리지 매도는 자동
실행하지 않고 경고만** 한다(레버리지 축소는 수동 검토). 실주문 전 QLD 거래가능성을 stocks
엔드포인트로 읽기전용 확인한다(`scripts/smoke_test.py` 스모크 경로에도 포함).

> ⚠️ **리스크(반드시 병기 — 알파가 아니라 베타/위험선호):**
> - **최악 단위자본 낙폭 ≈ −99%** (닷컴 시작 코호트, 20년 지평). 레버리지 ETF 는 하락장에서
>   원금 대부분을 잃을 수 있다.
> - **10년 지평은 사전등록 채택 규칙을 통과하지 못했다**(짧은 지평은 초기 레버리지 손실을 상각할
>   시간이 부족). 20년 지평 glide 만 설계·홀드아웃 양쪽 통과.
> - 표준(절대낙폭) 게이트로는 **레버리지 자체가 FAIL** 이다. 따라서 **소액 슬리브 한정 + 포워드
>   페이퍼 점증** 후에만 고려한다. 합성 2x 백테스트는 배당을 2배로 태워 **낙관 방향**이다.

## 포워드 페이퍼 비교 (실현재가 기반, 실주문 없음)
`scripts/run_dca.py --auto`는 한 번 실행해 현재 계좌 현금 기준 DCA 매수 플랜만 기록하고 끝난다.
하루 동안 실제 현재가로 "지금 샀다면/팔았다면"을 비교하려면 별도 페이퍼 장부를 쓴다.

```bash
# 1회 실행: 현재가로 가상 포트폴리오 초기화/갱신 + 보고서 생성
PYTHONPATH=src python scripts/forward_paper_compare.py --reset

# 장중 반복 실행(5분마다): 터미널을 닫아도 계속, 실주문 없음
PYTHONPATH=src nohup caffeinate -dimsu python scripts/forward_paper_compare.py --watch --interval-sec 300 --reset > data/forward_paper.nohup.out 2>&1 &
echo $! > data/forward_paper.pid

# 확인/중지
tail -f data/forward_paper.nohup.out
tail -f data/forward_paper.log
kill "$(cat data/forward_paper.pid)"
```

비교 대상:
- 일시불 ETF 기준선: QQQ60/SCHD25/GLD15 매수 후 보유
- 듀얼모멘텀: QQQ/SPY/EFA/IWM/GLD 중 12개월 모멘텀 1등, 방어자산 IEF
- 200일 레짐필터: QQQ가 200일선 위면 QQQ, 아래면 IEF
- SMA 20/60 추세: 상승추세 상위 3개 동일비중

상태는 `data/forward_paper_state.json`, 로그는 `data/forward_paper.log`, 최신 보고서는
`reports/forward_paper_latest.md`에 저장된다. 모두 가상 체결이며 토스 계좌 주문은 만들지 않는다.
신규 후보 전략 검증 기록은 `docs/forward_strategy_plan.md`, 최신 게이트 결과는
`reports/strategy_gate_2026-07-07.md` 참고. 불합격 후보는 기본 forward 장부에 넣지 않는다.

## 호가/체결 데이터 수집 (읽기 전용)
단기 퀀트는 바로 매매하지 않고 raw 데이터부터 쌓는다. 수집기는 토스 `orderbook`/`trades`
읽기 API만 호출하며 주문을 만들지 않는다.

```bash
# 1회 수집
PYTHONPATH=src python scripts/collect_microstructure.py --symbols QQQ,SPY --once

# 장중 반복 수집(30초마다, 정규장일 때만)
PYTHONPATH=src nohup caffeinate -dimsu python scripts/collect_microstructure.py \
  --symbols QQQ,SPY --interval-sec 30 --regular-only \
  > data/microstructure.nohup.out 2>&1 &
echo $! > data/microstructure.pid

# 중지
kill "$(cat data/microstructure.pid)"

# 수집 데이터 요약
PYTHONPATH=src python scripts/analyze_microstructure.py --symbol QQQ
```

저장 경로는 `data/microstructure/YYYY-MM-DD/SYMBOL.jsonl`, 계획서는
`docs/microstructure_collector_plan.md`에 있다.

## 인트라데이 분봉 수집 (키 없음, 누적)
데이트레이딩 아이디어(ORB·초반30분→막판30분 모멘텀·VWAP 되돌림·gap-and-go) 테스트용
1분/5분 미국주식 바를 **키 없이** 모아 자체 데이터셋을 쌓는다. 모듈은
`src/toss_trader/intraday_sources.py`, 수집기는 `scripts/collect_intraday.py`.
`histdata.py`는 건드리지 않고, 캐시는 `data/_hist_cache/intraday/{SYM}_{interval}.json`
(histdata 인트라데이 캐시와 **동일 레이아웃**, `histdata.load_intraday`로도 읽힌다).

소스(2026-09 이 네트워크 실측):
- **Nasdaq** `api.nasdaq.com/api/quote/{SYM}/chart` — 키 없이 **직전(현재) 세션**의 1분 데이터
  (확장장 04:00~20:00 ET 포함, 완결 세션 ≈960틱). 매 호출 1세션만 → 마감 후 크론으로 **누적**해야
  히스토리가 쌓인다. ⚠️ **분당 체결 last-price만**(OHLC 아님) → `o=h=l=c`, 거래량은 신뢰 소스가
  없어 `v=0`. 5분봉은 1분 last-price를 버킷 집계(버킷 내 진짜 고저 범위 생성).
- **Yahoo v8** `chart?interval=1m|5m|60m` — 진짜 OHLCV + 히스토리(1m≈7일·5m≈60일·60m≈730일).
  단 이 네트워크에서 **429 스로틀이 잦다** → 요청 간격 ≥1.5s, 429면 그 실행 동안 Yahoo 중단(우회 금지).
- **Nasdaq** `api.nasdaq.com/api/marketmovers` — 당일 상승률/거래량 상위 → 워치리스트 자동 확장.

```bash
# 마감 후 1회: 워치리스트(SPY QQQ TQQQ NVDA TSLA AAPL AMD META MSFT AMZN PLTR MSTR SMCI COIN)
# 1분·5분 누적 + 당일 무버스 12종목 추가 (Nasdaq만; Yahoo 재-throttle 방지)
PYTHONPATH=src python scripts/collect_intraday.py --source nasdaq --intervals 1m,5m --movers 12

# Yahoo가 열릴 때 백필(넓은 범위); 429면 자동으로 Nasdaq 폴백
PYTHONPATH=src python scripts/collect_intraday.py --backfill --intervals 1m,5m,60m

# 특정 종목만 / 정규장 봉만 / 미리보기
PYTHONPATH=src python scripts/collect_intraday.py --symbols NVDA,TSLA --regular-only --dry-run
```

자동화(마감 후 1회): 참조용 LaunchAgent 템플릿 `automation/com.tosstrader.intraday.plist`
(**설치 안 됨** — 경로 치환 후 수동 `launchctl load`). KST 09:10·10:10 트리거로 미 확장장
마감(20:00 ET, EDT/EST 양쪽)을 커버하고, 수집기는 병합-누적이라 이중 실행이 무해하다.
오프라인 테스트: `PYTHONPATH=src python -m unittest tests.test_intraday_sources`.

## 매일 자동 스코어보드 (선택)
세션이 끝난 뒤에도 "루프"가 **정직한 포워드 증거**를 계속 쌓게 하는 단일 작업이다.
`scripts/daily_scoreboard.py` 는 **읽기 전용·멱등**(아무 때나 여러 번 돌려도 안전)이며 **주문을
절대 내지 않는다.** 하위 단계를 각각 격리해(한 단계가 실패해도 나머지는 계속) 타임아웃과 함께 돌린다:
(a) ET 16:05 이후면 인트라데이 수집기 → 레인3 섀도, (b) 포워드 페이퍼 장부(Lump-sum ETF 기준선 +
액티브 후보 + 라이프사이클 슬리브 vs DCA-QQQ)를 **자격증명 있으면 실 토스 시세, 없으면 캐시(Nasdaq
종가)** 로 갱신, (c) DCA dry-run 플랜(분할·FX 경고 포함, 자격증명 있을 때) + 양도세 리포트,
(d) `reports/scoreboard_latest.md`(대시보드) + `data/scoreboard_history.jsonl`(변경 이력) 기록.

```bash
# 1회 실행: 자격증명 유무·ET 시각을 자동 판정
PYTHONPATH=src python scripts/daily_scoreboard.py
# 네트워크·API 없이 캐시만으로(로컬 검증/CI)
PYTHONPATH=src python scripts/daily_scoreboard.py --offline
# 최신 대시보드만 출력
PYTHONPATH=src python scripts/daily_scoreboard.py --status
```

대시보드에는 장부별 지분·최대낙폭(시작 이후), 규칙별 인트라데이 섀도(n·평균 net bps·t·상태),
오늘의 DCA 플랜, 양도세 YTD, 변경 이력 한 줄이 담긴다.

**자동화(선택, 원커맨드 — 자동 설치 안 됨):** macOS `launchd` 로 매일 **06:30 KST**(미 정규장
마감 이후, EDT/EST 양쪽) 1회 + `RunAtLoad`(전원 켜지면 즉시 보충) 실행.
```bash
bash scripts/install_scoreboard.sh            # 설치(자격증명 있으면 실시세, 없으면 캐시)
bash scripts/install_scoreboard.sh --offline   # 캐시 전용으로 설치
bash scripts/install_scoreboard.sh --status    # 상태 + 최신 대시보드 + 로그
bash scripts/install_scoreboard.sh --uninstall # 제거
```
참조용 LaunchAgent 템플릿은 `automation/com.tosstrader.scoreboard.plist`(경로 치환 후 수동 로드도 가능),
GitHub Actions 초안(비활성)은 `docs/github-actions/scoreboard.yml`. 오프라인 테스트:
`PYTHONPATH=src python -m pytest -q tests/test_scoreboard.py`.
> ⚠️ macOS TCC: repo 가 `~/Desktop`(또는 Documents/Downloads) 아래면 launchd 접근 거부 —
> 보호되지 않는 경로(예: `~/github/toss-trader`)에 두거나 해당 python 에 전체 디스크 접근을 부여한다.

## 자동화 (항상 dry-run) & 실거래 전환
**입금**은 API로 불가 → 은행 자동이체/토스 앱으로 설정(예: 매주 일요일 ₩50,000). 봇은 들어온 현금만 매수.

**자동화(매수)**: macOS `launchd`로 **dry-run**(주문 없이 `data/dca.log`에 '오늘 살 플랜'만 기록).
`cron`이 아니라 `launchd`인 이유 — 잠자다 깨거나 **전원이 켜지면(RunAtLoad) 즉시 실행**.
```bash
bash scripts/install_dca_automation.sh            # 설치(dry-run)
bash scripts/install_dca_automation.sh --status   # 상태 + 최근 로그
bash scripts/install_dca_automation.sh --uninstall # 제거
tail -f data/dca.log                              # 봇이 뭘 하려는지 실시간 관찰
```
> ⚠️ macOS TCC: 프로젝트가 `~/Desktop`(또는 Documents/Downloads) 아래면 launchd가 접근 거부됨.
> 자동화하려면 저장소를 보호되지 않는 경로(예: `~/github/toss-trader`)에 두거나 해당 실행기에 전체 디스크 접근 권한을 부여해야 한다.

### 🔴 실거래(live)로 전환하는 법 — 직접 할 때
자동화는 **항상 dry-run**으로 둔다. 실제 매수는 **본인이 직접** 다음으로 실행:
```bash
# 1) .env 에서 TRADING_MODE=live 로 변경
# 2) 미국 정규장(KST 22:30~05:00, 금액주문은 정규장 전용)에 직접 실행:
TRADING_MODE=live PYTHONPATH=src python scripts/run_dca.py --execute
#    → 매수가능 현금을 QQQ60/SCHD25/GLD15로 시장가 매수. data/dca.log에 기록.
```
(원하면 자동화 자체를 live로: `bash scripts/install_dca_automation.sh --live` — 단 무인 실주문이므로 비권장. 기본은 dry-run.)

## 페이퍼 전략 → 실계좌(선택)
포워드 페이퍼 랩(`scripts/paperlab_run.py`)에서 **검증한 전략 1개**를 골라 실계좌 주문으로 돌리는 브릿지.
`scripts/run_strategy.py` 는 **기본이 항상 dry-run**(주문 없음)이고, **자동으로 켜지지 않는다**. 실주문은
다음이 **전부** 참일 때만 나간다: `--execute` + `TRADING_MODE=live` + 미 정규장 **금액주문 접수 시간창**
(정규장 시작~종료 1시간 전) + 사전 **안전검사 통과**.

동작: 페이퍼 랩과 **동일한 코드**(`strategy.decide`)로 최신 종가 기준 오늘의 목표비중을 산출 → 실보유·매수가능
금액을 읽어 **매도 먼저**(전량은 단건, 부분은 소수점 6자리 내림 단건 — SEC/TAF 최소금액 최소화) → **매수**를
≤$10 무료 청크로 분할(매수가능금액·`--max-usd` 캡). run_dca 의 하드닝된 실행 머신러리(`toss_trader.live_exec`)를 재사용한다.

```bash
# 1) 플랜만 보기(dry-run, 주문 없음) — 예: ftlt_1x(비레버리지 실계좌판)
PYTHONPATH=src python scripts/run_strategy.py --strategy ftlt_1x

# 2) 계좌의 20%만 이 전략에 배정해서 플랜 확인(sleeve)
PYTHONPATH=src python scripts/run_strategy.py --strategy ftlt_1x --sleeve-frac 0.2

# 3) 실주문 — 반드시 미 정규장 접수시간창에서, 한 번 실행당 최대 $50 매수
TRADING_MODE=live PYTHONPATH=src python scripts/run_strategy.py \
    --strategy ftlt_1x --execute --sleeve-frac 0.2 --max-usd 50
```

**안전장치**
- **레버리지/인버스 ETP 게이트**: 매수 대상 종목의 `leverageFactor` 를 조회해 `|배수|>1` 이면 거부하고 한국 규제
  안내를 출력한다 — *최초 거래 기본예탁금 ₩10,000,000 + 사전 교육 1시간, 개별주식 레버리지 ETP는 매수 건마다
  ₩30,000,000*. 요건을 충족했다면 `--allow-leveraged-etp` 로만 진행(본인 책임). 주문 시 422 `prerequisite-required`/
  `stock-restricted` 도 같은 안내로 매핑된다. (TQQQ/SQQQ 등 레버리지 심볼을 쓰는 전략은 `_1x` 섀도판(QQQ/SPY/PSQ)이
  실계좌 대상이다.)
- **킬스위치**: 페이퍼 장부 낙폭이 `--max-paper-dd`(기본 −35%) 또는 계좌 당일 손익이 `--max-intraday-loss`
  (기본 −10%)를 넘으면 실주문을 거부. `0` 을 주면 해당 검사를 끈다.
- **세션당 1회 멱등**: 결정론적 cid(`strat-…`, DCA 봇과 네임스페이스 분리) + `data/strategy_live/{name}_state.json`
  으로 같은 세션 재실행 시 중복 접수가 없다(부분 접수는 청크 번호를 이어 재개).
- **기록**: 모든 플랜/주문을 `data/strategy_live/{name}.jsonl` 과 사람이 읽는 `reports/strategy_live_latest.md` 에 남긴다.

> ⚠️ 이 브릿지는 **레버리지·인버스·고회전 전략을 실계좌에서 자동 집행**할 수 있다. 페이퍼(≥3개월, QQQ 상회, 0 운영오류)로
> 먼저 검증하고, 소액·`--sleeve-frac`·`--max-usd` 로 시작하라. 자동화(launchd 등)에 **live 로 올리지 말 것**(무인 실주문 비권장).
