# 매일 자동 스코어보드

- 생성: `2026-10-13T06:25:01+09:00`  ·  모드: **live(실시세)**  ·  ET 세션: `2026-10-12T17:25-04:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 6 / 실패 4 / 스킵 0.
- 변경 이력: `2026-10-13T06:25:01+09:00 | steps ok=6/실패=4/스킵=0 | books=6 | shadow_trades=256 | mode=live(실시세)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ❌ failed | 13617.4s | rc=1: {"summary": {"SPY_1m": {"symbol": "SPY", "interval": "1m", "path": "/Users/mingh/github/toss-trader/data/_hist_cache/intraday/SPY_1m.json", "before": 6802, "added": 822, "updated": 0, "total": 7624, "fetched_rows": 822, "source": "nasdaq"}, "SPY_5m": {"symbol": "SPY", "interval": "5m", "path": "/Use |
| intraday_shadow | ✅ ok | 1.6s |  |
| refresh_closes | ✅ ok | 54.9s |  |
| forward_paper | ✅ ok | 2.5s |  |
| forward_lifecycle | ✅ ok | 0.6s |  |
| dca_plan | ❌ failed | 0.6s | rc=1: toss_trader.errors.TossAPIError: [403 ip-not-allowed] 허용되지 않은 IP 주소입니다. | requestId=d2jbPtqoYKsFsQyy |
| tax_report | ❌ failed | 0.6s | rc=1: toss_trader.errors.TossAPIError: [403 ip-not-allowed] 허용되지 않은 IP 주소입니다. | requestId=dnaxO2fe3KQxnIZm |
| paperlab | ❌ failed | 91.6s | stale: 장부 2026-10-09 < 최신 완료 세션 2026-10-12 (일봉 소스 지연 — ops_stale_paper 방출) |
| daily_decision | ✅ ok | 0.4s |  |
| llm_judge | ✅ ok | 38.9s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | live | $98.93 | $100.00 | -1.07% | -1.38% | 21 |
| B1 Dual momentum → IEF | live | $100.16 | $100.00 | +0.16% | -1.07% | 21 |
| 200d regime filter → IEF | live | $100.16 | $100.00 | +0.16% | -1.07% | 21 |
| SMA 20/60 trend top3 | live | $98.93 | $100.00 | -1.07% | -1.91% | 21 |
| Lifecycle sleeve (QQQ/QLD) | live | $135.95 | $135.00 | +0.70% | -2.94% | 21 |
| DCA-QQQ (lifecycle benchmark) | live | $135.48 | $135.00 | +0.36% | -1.07% | 21 |

## 인트라데이 섀도 (규칙별, 포지션 $30)

- 누적 가상 트레이드 256건 · 세션 날짜 11개 (2026-09-28~2026-10-12).

| 규칙 | n | 평균 net bps | t-stat | 상태 |
|---|---:|---:|---:|---|
| ORB-5 (QQQ/TQQQ) | 14 | -61.69 | -3.73 | 수집 중 (n<200) |
| ORB-15 (QQQ/TQQQ) | 12 | -67.98 | -2.71 | 수집 중 (n<200) |
| Intraday momentum (QQQ/TQQQ) | 8 | -61.97 | -3.32 | 수집 중 (n<200) |
| ORB-5 (movers) | 121 | 58.11 | 0.68 | 수집 중 (n<200) |
| ORB-15 (movers) | 100 | 19.84 | 0.18 | 수집 중 (n<200) |
| Gap-and-go (movers) | 1 | -50.13 | 0.00 | 수집 중 (n<200) |

## 오늘의 DCA 플랜 (dry-run, 주문 없음)

- 자격증명 없음 또는 단계 스킵 → 플랜 생략(오프라인 모드에선 정상).

## 양도세 (YTD)

- 자격증명/픽스처 없음 → 양도세 리포트 생략.

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
