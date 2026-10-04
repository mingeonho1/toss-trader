# 매일 자동 스코어보드

- 생성: `2026-10-03T06:25:01+09:00`  ·  모드: **live(실시세)**  ·  ET 세션: `2026-10-02T17:25-04:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 9 / 실패 1 / 스킵 0.
- 변경 이력: `2026-10-03T06:25:01+09:00 | steps ok=9/실패=1/스킵=0 | books=6 | shadow_trades=123 | mode=live(실시세)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ❌ failed | 17718.2s | rc=1: {"summary": {"SPY_1m": {"symbol": "SPY", "interval": "1m", "path": "/Users/mingh/github/toss-trader/data/_hist_cache/intraday/SPY_1m.json", "before": 3539, "added": 805, "updated": 0, "total": 4344, "fetched_rows": 805, "source": "nasdaq"}, "SPY_5m": {"symbol": "SPY", "interval": "5m", "path": "/Use |
| intraday_shadow | ✅ ok | 1.4s |  |
| refresh_closes | ✅ ok | 15409.2s |  |
| forward_paper | ✅ ok | 3662.6s |  |
| forward_lifecycle | ✅ ok | 3121.7s |  |
| dca_plan | ✅ ok | 3.1s |  |
| tax_report | ✅ ok | 530.5s |  |
| paperlab | ✅ ok | 40788.1s |  |
| daily_decision | ✅ ok | 0.4s |  |
| llm_judge | ✅ ok | 43877.4s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | live | $98.37 | $100.00 | -1.63% | -1.38% | 12 |
| B1 Dual momentum → IEF | live | $99.93 | $100.00 | -0.07% | -1.07% | 12 |
| 200d regime filter → IEF | live | $99.93 | $100.00 | -0.07% | -1.07% | 12 |
| SMA 20/60 trend top3 | live | $98.26 | $100.00 | -1.74% | -1.91% | 12 |
| Lifecycle sleeve (QQQ/QLD) | live | $135.45 | $135.00 | +0.33% | -2.15% | 12 |
| DCA-QQQ (lifecycle benchmark) | live | $135.17 | $135.00 | +0.13% | -1.07% | 12 |

## 인트라데이 섀도 (규칙별, 포지션 $30)

- 누적 가상 트레이드 123건 · 세션 날짜 5개 (2026-09-28~2026-10-02).

| 규칙 | n | 평균 net bps | t-stat | 상태 |
|---|---:|---:|---:|---|
| ORB-5 (QQQ/TQQQ) | 8 | -96.05 | -7.53 | 수집 중 (n<200) |
| ORB-15 (QQQ/TQQQ) | 8 | -118.84 | -7.32 | 수집 중 (n<200) |
| Intraday momentum (QQQ/TQQQ) | 6 | -66.97 | -2.67 | 수집 중 (n<200) |
| ORB-5 (movers) | 55 | 94.41 | 0.81 | 수집 중 (n<200) |
| ORB-15 (movers) | 46 | 94.50 | 0.72 | 수집 중 (n<200) |
| Gap-and-go (movers) | 0 | 0.00 | 0.00 | 수집 중 (n<200) |

## 오늘의 DCA 플랜 (dry-run, 주문 없음)

- 계좌 1 | 매수가능 ₩0 (= $0.00 @ 1349.0) | 보유평가 $0.00
- 적립 매수 플랜 (목표배분 {'QQQ': 0.6, 'SCHD': 0.25, 'GLD': 0.15}): 없음

## 양도세 (YTD)

- 실현손익(YTD, 통산) : ₩0  → 예상세액 ₩0 (공제 ₩2,500,000 반영)
- 남은 기본공제        : ₩2,500,000
- 보유 미실현손익(원화, 현재 환율 1,349):
- 합계                                 미실현 ₩0
- 하베스팅 플랜:
- 하베스팅 없음 — 실현할 미실현 이익 없음(모두 손실이거나 보유 없음)

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
