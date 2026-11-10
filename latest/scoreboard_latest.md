# 매일 자동 스코어보드

- 생성: `2026-11-10T06:25:00+09:00`  ·  모드: **live(실시세)**  ·  ET 세션: `2026-11-09T16:25-05:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 7 / 실패 3 / 스킵 0.
- 변경 이력: `2026-11-10T06:25:00+09:00 | steps ok=7/실패=3/스킵=0 | books=6 | shadow_trades=832 | mode=live(실시세)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ❌ failed | 12870.5s | rc=1: {"summary": {"SPY_1m": {"symbol": "SPY", "interval": "1m", "path": "/Users/mingh/github/toss-trader/data/_hist_cache/intraday/SPY_1m.json", "before": 21296, "added": 745, "updated": 0, "total": 22041, "fetched_rows": 745, "source": "nasdaq"}, "SPY_5m": {"symbol": "SPY", "interval": "5m", "path": "/U |
| intraday_shadow | ✅ ok | 1056.7s |  |
| refresh_closes | ✅ ok | 508.4s |  |
| forward_paper | ✅ ok | 1802.8s |  |
| forward_lifecycle | ✅ ok | 0.6s |  |
| dca_plan | ❌ failed | 0.5s | rc=1: toss_trader.errors.TossAPIError: [403 ip-not-allowed] 허용되지 않은 IP 주소입니다. | requestId=ax8uZyWCl1wa3K9L |
| tax_report | ❌ failed | 0.4s | rc=1: toss_trader.errors.TossAPIError: [403 ip-not-allowed] 허용되지 않은 IP 주소입니다. | requestId=aEZtOFPUO9G3oGJt |
| paperlab | ✅ ok | 228.0s |  |
| daily_decision | ✅ ok | 0.6s |  |
| llm_judge | ✅ ok | 15.2s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | live | $94.49 | $100.00 | -5.51% | -7.60% | 50 |
| B1 Dual momentum → IEF | live | $92.59 | $100.00 | -7.41% | -12.51% | 50 |
| 200d regime filter → IEF | live | $90.57 | $100.00 | -9.43% | -14.96% | 50 |
| SMA 20/60 trend top3 | live | $90.19 | $100.00 | -9.81% | -9.21% | 50 |
| Lifecycle sleeve (QQQ/QLD) | live | $142.44 | $170.00 | -16.21% | -17.04% | 50 |
| DCA-QQQ (lifecycle benchmark) | live | $156.72 | $170.00 | -7.81% | -8.69% | 50 |

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

- 자격증명 없음 또는 단계 스킵 → 플랜 생략(오프라인 모드에선 정상).

## 양도세 (YTD)

- 자격증명/픽스처 없음 → 양도세 리포트 생략.

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
