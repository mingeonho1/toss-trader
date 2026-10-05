# 매일 자동 스코어보드

- 생성: `2026-10-05T06:25:01+09:00`  ·  모드: **offline(캐시) · 토스 API 사용 불가 → 캐시 모드 폴백 (TossError: 토큰 발급 네트워크 오류: <urlopen error [Errno 8] nodename nor servname provided, or not k)**  ·  ET 세션: `2026-10-04T17:25-04:00`
- 읽기 전용 · 주문 없음 · 멱등(같은 날 여러 번 실행해도 이력 중복 없음).
- 단계: ok 6 / 실패 1 / 스킵 3.
- 변경 이력: `2026-10-05T06:25:01+09:00 | steps ok=6/실패=1/스킵=3 | books=6 | shadow_trades=123 | mode=offline(캐시) · 토스 API 사용 불가 → 캐시 모드 폴백 (TossError: 토큰 발급 네트워크 오류: <urlopen error [Errno 8] nodename nor servname provided, or not k)`

## 단계 상태

| 단계 | 상태 | 소요 | 비고 |
|---|---|---:|---|
| intraday_collect | ⏭ skipped | 0.0s | 주말(ET Sun) — 새 세션 없음 |
| intraday_shadow | ✅ ok | 1.3s |  |
| refresh_closes | ❌ failed | 0.4s | rc=1: refresh_closes stopped: AGG: URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known> |
| forward_paper | ✅ ok | 0.3s |  |
| forward_lifecycle | ✅ ok | 0.2s |  |
| dca_plan | ⏭ skipped | 0.0s | 자격증명 없음(플랜 생략) |
| tax_report | ⏭ skipped | 0.0s | 자격증명 없음(양도세 생략) |
| paperlab | ✅ ok | 5262.7s |  |
| daily_decision | ✅ ok | 0.4s |  |
| llm_judge | ✅ ok | 40589.5s |  |

## 포워드 페이퍼 (장부별 지분·낙폭, 시작 이후)

| 장부 | 소스 | 지분 | 납입 | Money Return | 최대낙폭(원지분) | 스냅샷 |
|---|---|---:|---:|---:|---:|---:|
| B0 Lump-sum ETF (QQQ60/SCHD25/GLD15) | cached | $98.32 | $100.00 | -1.68% | -1.38% | 13 |
| B1 Dual momentum → IEF | cached | $99.93 | $100.00 | -0.07% | -1.07% | 13 |
| 200d regime filter → IEF | cached | $99.93 | $100.00 | -0.07% | -1.07% | 13 |
| SMA 20/60 trend top3 | cached | $98.25 | $100.00 | -1.75% | -1.91% | 13 |
| Lifecycle sleeve (QQQ/QLD) | cached | $135.54 | $135.00 | +0.40% | -2.15% | 13 |
| DCA-QQQ (lifecycle benchmark) | cached | $135.18 | $135.00 | +0.13% | -1.07% | 13 |

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

- 자격증명 없음 또는 단계 스킵 → 플랜 생략(오프라인 모드에선 정상).

## 양도세 (YTD)

- 자격증명/픽스처 없음 → 양도세 리포트 생략.

## 주의

- 최대낙폭은 **원지분(raw equity)** peak-to-trough 이며 납입흐름 미보정(장부별 상세 TWR/MDD는 각 스크립트 리포트 참조).
- 인트라데이 상태는 필요조건 스크린(n≥200 且 t≥3). 최종 검증은 마이크로구조+포워드(spec §8.3).
- 이 작업은 읽기 전용이며 실주문을 만들지 않는다.
