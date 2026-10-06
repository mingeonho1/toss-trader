# 매일 자동 스코어보드

- 생성: `2026-10-07T06:25:02+09:00`  ·  모드: **offline(캐시) · 토스 API 사용 불가 → 캐시 모드 폴백 (TossError: 토큰 발급 네트워크 오류: <urlopen error [Errno 8] nodename nor servname provided, or not k)**  ·  ET 세션: `2026-10-06T17:25-04:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 6 / 실패 2 / 스킵 2.
- 변경 이력: `2026-10-07T06:25:02+09:00 | steps ok=6/실패=2/스킵=2 | books=6 | shadow_trades=181 | mode=offline(캐시) · 토스 API 사용 불가 → 캐시 모드 폴백 (TossError: 토큰 발급 네트워크 오류: <urlopen error [Errno 8] nodename nor servname provided, or not k)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ❌ failed | 7521.2s | rc=1: {"summary": {"SPY_1m": {"error": "SPY 1m: 모든 소스 실패 → nasdaq: URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>"}, "SPY_5m": {"error": "SPY 5m: 모든 소스 실패 → nasdaq: URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>"}, "QQQ_1m": {"error": "QQ |
| intraday_shadow | ✅ ok | 1.6s |  |
| refresh_closes | ✅ ok | 48.2s |  |
| forward_paper | ✅ ok | 0.3s |  |
| forward_lifecycle | ✅ ok | 0.2s |  |
| dca_plan | ⏭ skipped | 0.0s | 자격증명 없음(플랜 생략) |
| tax_report | ⏭ skipped | 0.0s | 자격증명 없음(양도세 생략) |
| paperlab | ❌ failed | 6.4s | stale: 장부 2026-10-05 < 최신 완료 세션 2026-10-06 (일봉 소스 지연 — ops_stale_paper 방출) |
| daily_decision | ✅ ok | 0.4s |  |
| llm_judge | ✅ ok | 43.3s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | cached | $98.83 | $100.00 | -1.17% | -1.38% | 15 |
| B1 Dual momentum → IEF | cached | $100.82 | $100.00 | +0.82% | -1.07% | 15 |
| 200d regime filter → IEF | cached | $100.82 | $100.00 | +0.82% | -1.07% | 15 |
| SMA 20/60 trend top3 | cached | $99.02 | $100.00 | -0.98% | -1.91% | 15 |
| Lifecycle sleeve (QQQ/QLD) | cached | $137.91 | $135.00 | +2.15% | -2.15% | 15 |
| DCA-QQQ (lifecycle benchmark) | cached | $136.37 | $135.00 | +1.02% | -1.07% | 15 |

## 인트라데이 섀도 (규칙별, 포지션 $30)

- 누적 가상 트레이드 181건 · 세션 날짜 7개 (2026-09-28~2026-10-06).

| 규칙 | n | 평균 net bps | t-stat | 상태 |
|---|---:|---:|---:|---|
| ORB-5 (QQQ/TQQQ) | 10 | -66.49 | -2.88 | 수집 중 (n<200) |
| ORB-15 (QQQ/TQQQ) | 10 | -91.09 | -3.98 | 수집 중 (n<200) |
| Intraday momentum (QQQ/TQQQ) | 8 | -61.97 | -3.32 | 수집 중 (n<200) |
| ORB-5 (movers) | 84 | 126.92 | 1.04 | 수집 중 (n<200) |
| ORB-15 (movers) | 68 | 142.70 | 0.96 | 수집 중 (n<200) |
| Gap-and-go (movers) | 1 | -50.13 | 0.00 | 수집 중 (n<200) |

## 오늘의 DCA 플랜 (dry-run, 주문 없음)

- 자격증명 없음 또는 단계 스킵 → 플랜 생략(오프라인 모드에선 정상).

## 양도세 (YTD)

- 자격증명/픽스처 없음 → 양도세 리포트 생략.

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
