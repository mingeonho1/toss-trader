# 매일 자동 스코어보드

- 생성: `2026-11-13T06:25:00+09:00`  ·  모드: **offline(캐시) · 토스 API 사용 불가 → 캐시 모드 폴백 (허용 IP 미등록)**  ·  ET 세션: `2026-11-12T16:25-05:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 5 / 실패 3 / 스킵 2.
- 변경 이력: `2026-11-13T06:25:00+09:00 | steps ok=5/실패=3/스킵=2 | books=6 | shadow_trades=832 | mode=offline(캐시) · 토스 API 사용 불가 → 캐시 모드 폴백 (허용 IP 미등록)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ❌ failed | 5934.0s | rc=1: {"summary": {"SPY_1m": {"error": "SPY 1m: 모든 소스 실패 → nasdaq: HTTPError: HTTP Error 403: Forbidden"}, "SPY_5m": {"error": "SPY 5m: 모든 소스 실패 → nasdaq: HTTPError: HTTP Error 403: Forbidden"}, "QQQ_1m": {"error": "QQQ 1m: 모든 소스 실패 → nasdaq: HTTPError: HTTP Error 403: Forbidden"}, "QQQ_5m": {"error": "QQ |
| intraday_shadow | ✅ ok | 1523.2s |  |
| refresh_closes | ❌ failed | 8.2s | rc=1: refresh_closes stopped: AGG: HTTPError: HTTP Error 403: Forbidden |
| forward_paper | ✅ ok | 0.3s |  |
| forward_lifecycle | ✅ ok | 0.2s |  |
| dca_plan | ⏭ skipped | 0.0s | 자격증명 없음(플랜 생략) |
| tax_report | ⏭ skipped | 0.0s | 자격증명 없음(양도세 생략) |
| paperlab | ❌ failed | 138.5s | stale: 장부 2026-11-11 < 최신 완료 세션 2026-11-12 (일봉 소스 지연 — ops_stale_paper 방출) |
| daily_decision | ✅ ok | 0.5s |  |
| llm_judge | ✅ ok | 3566.7s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | cached | $95.46 | $100.00 | -4.54% | -7.60% | 53 |
| B1 Dual momentum → IEF | cached | $92.78 | $100.00 | -7.22% | -12.51% | 53 |
| 200d regime filter → IEF | cached | $92.03 | $100.00 | -7.97% | -14.96% | 53 |
| SMA 20/60 trend top3 | cached | $90.19 | $100.00 | -9.81% | -9.21% | 53 |
| Lifecycle sleeve (QQQ/QLD) | cached | $147.26 | $170.00 | -13.37% | -17.04% | 53 |
| DCA-QQQ (lifecycle benchmark) | cached | $159.24 | $170.00 | -6.33% | -8.69% | 53 |

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
