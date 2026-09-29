# 매일 자동 스코어보드

- 생성: `2026-09-29T11:21:53+09:00`  ·  모드: **live(실시세)**  ·  ET 세션: `2026-09-28T22:21-04:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 7 / 실패 2 / 스킵 0.
- 변경 이력: `2026-09-29T11:21:53+09:00 | steps ok=7/실패=2/스킵=0 | books=6 | shadow_trades=20 | mode=live(실시세)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ✅ ok | 29.0s |  |
| intraday_shadow | ✅ ok | 0.5s |  |
| refresh_closes | ✅ ok | 41.8s |  |
| forward_paper | ✅ ok | 2.4s |  |
| forward_lifecycle | ✅ ok | 0.5s |  |
| dca_plan | ❌ failed | 0.6s | rc=1: toss_trader.errors.TossAPIError: [403 ip-not-allowed] 허용되지 않은 IP 주소입니다. | requestId=tcgWKxsSYvFbTvxT |
| tax_report | ❌ failed | 0.5s | rc=1: toss_trader.errors.TossAPIError: [403 ip-not-allowed] 허용되지 않은 IP 주소입니다. | requestId=tgJfm9dJ5dDBXyCQ |
| paperlab | ✅ ok | 81.2s |  |
| daily_decision | ✅ ok | 0.3s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | live | $97.41 | $100.00 | -2.59% | -1.38% | 4 |
| B1 Dual momentum → IEF | live | $98.19 | $100.00 | -1.81% | -1.07% | 4 |
| 200d regime filter → IEF | live | $98.19 | $100.00 | -1.81% | -1.07% | 4 |
| SMA 20/60 trend top3 | live | $96.99 | $100.00 | -3.01% | -1.91% | 4 |
| Lifecycle sleeve (QQQ/QLD) | live | $96.65 | $100.00 | -3.35% | -2.15% | 4 |
| DCA-QQQ (lifecycle benchmark) | live | $98.19 | $100.00 | -1.81% | -1.07% | 4 |

## 인트라데이 섀도 (규칙별, 포지션 $30)

- 누적 가상 트레이드 20건 · 세션 날짜 1개 (2026-09-28~2026-09-28).

| 규칙 | n | 평균 net bps | t-stat | 상태 |
|---|---:|---:|---:|---|
| ORB-5 (QQQ/TQQQ) | 0 | 0.00 | 0.00 | 수집 중 (n<200) |
| ORB-15 (QQQ/TQQQ) | 0 | 0.00 | 0.00 | 수집 중 (n<200) |
| Intraday momentum (QQQ/TQQQ) | 0 | 0.00 | 0.00 | 수집 중 (n<200) |
| ORB-5 (movers) | 11 | 95.88 | 0.46 | 수집 중 (n<200) |
| ORB-15 (movers) | 9 | 43.02 | 0.25 | 수집 중 (n<200) |
| Gap-and-go (movers) | 0 | 0.00 | 0.00 | 수집 중 (n<200) |

## 오늘의 DCA 플랜 (dry-run, 주문 없음)

- 자격증명 없음 또는 단계 스킵 → 플랜 생략(오프라인 모드에선 정상).

## 양도세 (YTD)

- 자격증명/픽스처 없음 → 양도세 리포트 생략.

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
