# toss-trader

토스증권 OpenAPI 기반 **미국주식 스윙 자동매매** (결정론적 규칙 + Gemini 보조, 페이퍼 우선).

설계 전반과 진행상황은 **[PLAN.md](./PLAN.md)**, 일별 기록은 **[journal/](./journal/)** 참고.

## 빠른 시작
```bash
cp .env.example .env      # 토스 키 2줄 채우기 (PC 웹에서 발급)
PYTHONPATH=src python scripts/smoke_test.py   # 읽기 전용 검증 (주문 없음)
```

요구사항: Python ≥ 3.11. 핵심 클라이언트는 **표준 라이브러리만** 사용(외부 패키지 0).

## 안전장치
- `TRADING_MODE=paper` 가 기본. 실거래(`live`)는 PLAN.md §4 검증 게이트 통과 후에만.
- 주문은 멱등키(clientOrderId)로 중복 방지, 429는 Retry-After 준수, 401은 토큰 자동 재발급.

## 현재 상태
- ✅ Phase 1: 인프라 + `TossClient`(인증/시세/계좌/주문) + 스모크 테스트
- ⏳ 다음: 비용모델 → 페이퍼 브로커/백테스터 → 전략 → 리스크/엔진 → Gemini 보조
