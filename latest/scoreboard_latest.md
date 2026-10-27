# 매일 자동 스코어보드

- 생성: `2026-10-27T06:25:01+09:00`  ·  모드: **live(실시세)**  ·  ET 세션: `2026-10-26T17:25-04:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 8 / 실패 2 / 스킵 0.
- 변경 이력: `2026-10-27T06:25:01+09:00 | steps ok=8/실패=2/스킵=0 | books=6 | shadow_trades=496 | mode=live(실시세)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ❌ failed | 10210.4s | rc=1: {"summary": {"SPY_1m": {"error": "SPY 1m: 모든 소스 실패 → nasdaq: HistDataError: nasdaq SPY (stocks): 빈 chart"}, "SPY_5m": {"symbol": "SPY", "interval": "5m", "path": "/Users/mingh/github/toss-trader/data/_hist_cache/intraday/SPY_5m.json", "before": 2833, "added": 168, "updated": 0, "total": 3001, "fetch |
| intraday_shadow | ✅ ok | 4.6s |  |
| refresh_closes | ✅ ok | 780.9s |  |
| forward_paper | ✅ ok | 1855.7s |  |
| forward_lifecycle | ✅ ok | 1.7s |  |
| dca_plan | ❌ failed | 613.0s | rc=1: TimeoutError: The read operation timed out |
| tax_report | ✅ ok | 2.5s |  |
| paperlab | ✅ ok | 83.3s |  |
| daily_decision | ✅ ok | 0.5s |  |
| llm_judge | ✅ ok | 44.1s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | live | $96.95 | $100.00 | -3.05% | -3.17% | 34 |
| B1 Dual momentum → IEF | live | $95.29 | $100.00 | -4.71% | -7.20% | 34 |
| 200d regime filter → IEF | live | $95.29 | $100.00 | -4.71% | -7.20% | 34 |
| SMA 20/60 trend top3 | live | $93.61 | $100.00 | -6.39% | -5.77% | 34 |
| Lifecycle sleeve (QQQ/QLD) | live | $122.49 | $135.00 | -9.27% | -14.30% | 34 |
| DCA-QQQ (lifecycle benchmark) | live | $128.90 | $135.00 | -4.52% | -7.20% | 34 |

## 인트라데이 섀도 (규칙별, 포지션 $30)

- 누적 가상 트레이드 496건 · 세션 날짜 20개 (2026-09-28~2026-10-26).

| 규칙 | n | 평균 net bps | t-stat | 상태 |
|---|---:|---:|---:|---|
| ORB-5 (QQQ/TQQQ) | 28 | -71.25 | -6.00 | 수집 중 (n<200) |
| ORB-15 (QQQ/TQQQ) | 26 | -46.82 | -2.42 | 수집 중 (n<200) |
| Intraday momentum (QQQ/TQQQ) | 12 | -59.55 | -4.82 | 수집 중 (n<200) |
| ORB-5 (movers) | 227 | 32.58 | 0.64 | 기각 |
| ORB-15 (movers) | 196 | 28.48 | 0.46 | 수집 중 (n<200) |
| Gap-and-go (movers) | 1 | -50.13 | 0.00 | 수집 중 (n<200) |

## 오늘의 DCA 플랜 (dry-run, 주문 없음)

- 자격증명 없음 또는 단계 스킵 → 플랜 생략(오프라인 모드에선 정상).

## 양도세 (YTD)

- 실현손익(YTD, 통산) : ₩0  → 예상세액 ₩0 (공제 ₩2,500,000 반영)
- 남은 기본공제        : ₩2,500,000
- 보유 미실현손익(원화, 현재 환율 1,325):
- 합계                                 미실현 ₩0
- 하베스팅 플랜:
- 하베스팅 없음 — 실현할 미실현 이익 없음(모두 손실이거나 보유 없음)

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
