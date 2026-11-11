# 매일 자동 스코어보드

- 생성: `2026-11-12T06:25:00+09:00`  ·  모드: **live(실시세)**  ·  ET 세션: `2026-11-11T16:25-05:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 9 / 실패 1 / 스킵 0.
- 변경 이력: `2026-11-12T06:25:00+09:00 | steps ok=9/실패=1/스킵=0 | books=6 | shadow_trades=832 | mode=live(실시세)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ❌ failed | 20.1s | rc=1: {"summary": {"SPY_1m": {"error": "SPY 1m: 모든 소스 실패 → nasdaq: HTTPError: HTTP Error 403: Forbidden"}, "SPY_5m": {"error": "SPY 5m: 모든 소스 실패 → nasdaq: HTTPError: HTTP Error 403: Forbidden"}, "QQQ_1m": {"error": "QQQ 1m: 모든 소스 실패 → nasdaq: HTTPError: HTTP Error 403: Forbidden"}, "QQQ_5m": {"error": "QQ |
| intraday_shadow | ✅ ok | 9.5s |  |
| refresh_closes | ✅ ok | 38.8s |  |
| forward_paper | ✅ ok | 3.8s |  |
| forward_lifecycle | ✅ ok | 1.6s |  |
| dca_plan | ✅ ok | 1.8s |  |
| tax_report | ✅ ok | 1.9s |  |
| paperlab | ✅ ok | 77.8s |  |
| daily_decision | ✅ ok | 0.6s |  |
| llm_judge | ✅ ok | 10.3s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | live | $95.42 | $100.00 | -4.58% | -7.60% | 52 |
| B1 Dual momentum → IEF | live | $92.78 | $100.00 | -7.22% | -12.51% | 52 |
| 200d regime filter → IEF | live | $91.98 | $100.00 | -8.02% | -14.96% | 52 |
| SMA 20/60 trend top3 | live | $90.19 | $100.00 | -9.81% | -9.21% | 52 |
| Lifecycle sleeve (QQQ/QLD) | live | $147.05 | $170.00 | -13.50% | -17.04% | 52 |
| DCA-QQQ (lifecycle benchmark) | live | $159.16 | $170.00 | -6.38% | -8.69% | 52 |

## 인트라데이 섀도 (규칙별, 포지션 $30)

- 누적 가상 트레이드 832건 · 세션 날짜 30개 (2026-09-28~2026-11-09).

| 규칙 | n | 평균 net bps | t-stat | 상태 |
|---|---:|---:|---:|---|
| ORB-5 (QQQ/TQQQ) | 46 | -62.07 | -3.98 | 수집 중 (n<200) |
| ORB-15 (QQQ/TQQQ) | 44 | -64.29 | -3.41 | 수집 중 (n<200) |
| Intraday momentum (QQQ/TQQQ) | 26 | -62.43 | -6.13 | 수집 중 (n<200) |
| ORB-5 (movers) | 363 | 158.96 | 1.45 | 기각 |
| ORB-15 (movers) | 319 | 144.42 | 1.66 | 기각 |
| Gap-and-go (movers) | 3 | -179.09 | -1.79 | 수집 중 (n<200) |

## 오늘의 DCA 플랜 (dry-run, 주문 없음)

- 계좌 1 | 매수가능 ₩0 (= $0.00 @ 1339.0) | 보유평가 $0.00
- 적립 매수 플랜 (목표배분 {'QQQ': 0.6, 'SCHD': 0.25, 'GLD': 0.15}): 없음

## 양도세 (YTD)

- 실현손익(YTD, 통산) : ₩0  → 예상세액 ₩0 (공제 ₩2,500,000 반영)
- 남은 기본공제        : ₩2,500,000
- 보유 미실현손익(원화, 현재 환율 1,339):
- 합계                                 미실현 ₩0
- 하베스팅 플랜:
- 하베스팅 없음 — 실현할 미실현 이익 없음(모두 손실이거나 보유 없음)

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
