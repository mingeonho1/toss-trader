# 매일 자동 스코어보드

- 생성: `2026-11-06T15:55:36+09:00`  ·  모드: **live(실시세)**  ·  ET 세션: `2026-11-06T01:55-05:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 6 / 실패 3 / 스킵 1.
- 변경 이력: `2026-11-06T15:55:36+09:00 | steps ok=6/실패=3/스킵=1 | books=6 | shadow_trades=751 | mode=live(실시세)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ⏭ skipped | 0.0s | ET 01:55 < 16:05 (정규장 마감 전) |
| intraday_shadow | ✅ ok | 9.2s |  |
| refresh_closes | ❌ failed | 17.3s | rc=1: refresh_closes stopped: AGG: URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known> |
| forward_paper | ✅ ok | 27.6s |  |
| forward_lifecycle | ✅ ok | 4.0s |  |
| dca_plan | ❌ failed | 0.8s | rc=1: toss_trader.errors.TossAPIError: [403 ip-not-allowed] 허용되지 않은 IP 주소입니다. | requestId=6I9l95J4NIfkMw9u |
| tax_report | ❌ failed | 1.5s | rc=1: toss_trader.errors.TossAPIError: [403 ip-not-allowed] 허용되지 않은 IP 주소입니다. | requestId=6NYBiOzokR5g83V2 |
| paperlab | ✅ ok | 103.9s |  |
| daily_decision | ✅ ok | 0.4s |  |
| llm_judge | ✅ ok | 21.5s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | live | $92.02 | $100.00 | -7.98% | -7.60% | 46 |
| B1 Dual momentum → IEF | live | $88.81 | $100.00 | -11.19% | -12.51% | 46 |
| 200d regime filter → IEF | live | $86.40 | $100.00 | -13.60% | -14.96% | 46 |
| SMA 20/60 trend top3 | live | $90.19 | $100.00 | -9.81% | -9.21% | 46 |
| Lifecycle sleeve (QQQ/QLD) | live | $130.06 | $170.00 | -23.49% | -17.04% | 46 |
| DCA-QQQ (lifecycle benchmark) | live | $149.50 | $170.00 | -12.06% | -8.69% | 46 |

## 인트라데이 섀도 (규칙별, 포지션 $30)

- 누적 가상 트레이드 751건 · 세션 날짜 28개 (2026-09-28~2026-11-05).

| 규칙 | n | 평균 net bps | t-stat | 상태 |
|---|---:|---:|---:|---|
| ORB-5 (QQQ/TQQQ) | 42 | -78.99 | -8.41 | 수집 중 (n<200) |
| ORB-15 (QQQ/TQQQ) | 40 | -67.73 | -4.22 | 수집 중 (n<200) |
| Intraday momentum (QQQ/TQQQ) | 22 | -53.21 | -5.13 | 수집 중 (n<200) |
| ORB-5 (movers) | 333 | 153.64 | 1.30 | 기각 |
| ORB-15 (movers) | 290 | 141.98 | 1.53 | 기각 |
| Gap-and-go (movers) | 2 | -212.79 | -1.31 | 수집 중 (n<200) |

## 오늘의 DCA 플랜 (dry-run, 주문 없음)

- 자격증명 없음 또는 단계 스킵 → 플랜 생략(오프라인 모드에선 정상).

## 양도세 (YTD)

- 자격증명/픽스처 없음 → 양도세 리포트 생략.

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
