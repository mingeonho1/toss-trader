# toss-trader

토스증권 OpenAPI 기반 **미국주식 스윙 자동매매** (결정론적 규칙 + Gemini 보조, 페이퍼 우선).

설계 전반과 진행상황은 **[PLAN.md](./PLAN.md)**, 일별 기록은 **[journal/](./journal/)** 참고.

## 빠른 시작
```bash
# .env에 자격증명 2줄 (PC 웹에서 발급): API_KEY=... / SECRET_KEY=...
PYTHONPATH=src python scripts/smoke_test.py   # 읽기 전용 실API 검증 (주문 없음)
PYTHONPATH=src python scripts/selftest.py     # 결정론 자가검증 (키 불필요)
```

요구사항: Python ≥ 3.11. 핵심 클라이언트는 **표준 라이브러리만** 사용(외부 패키지 0).
`.env` 자격증명 이름은 `API_KEY`/`SECRET_KEY`(또는 `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET`) 둘 다 지원.

## 안전장치
- `TRADING_MODE=paper` 가 기본. 실거래(`live`)는 PLAN.md §4 검증 게이트 통과 후에만.
- 주문은 멱등키(clientOrderId)로 중복 방지, 429는 Retry-After 준수, 401은 토큰 자동 재발급.
- `X-RateLimit-*` 헤더 적응 throttle. SSL CA 번들 자동 탐색(검증은 항상 유지).
- `LiveBroker`는 `require_live` 가드로 paper 모드에서 실주문을 막는다.

## 현재 상태 (2026-06-28)
- ✅ Phase 1: 인프라 + `TossClient` — **공식 OpenAPI v1.1.5 정합화 + 실 API 스모크 통과**
- ✅ Phase 2·3·5: 비용모델(실요율 0.1%) · PaperBroker/백테스터/지표 · 리스크/엔진/일지 · **LiveBroker**
- ⏳ 다음: Phase 4(실 캔들 워크포워드) → Phase 6(Gemini 보조) → Phase 7(입금·포워드테스트·실거래)
