"""토스 API 에러 모델. 코드별 한국어 해결 힌트 포함.

source of truth: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json (v1.2.17)
에러 envelope: {"error": {"requestId", "code", "message", "data?"}}. code는 flat string이며
클라이언트는 unknown code를 허용해야 한다. data의 표준 키(camelCase)는 스펙 ErrorResponse 참고
(field, allowedValues, constraint, retryAfterAt, retryAfterSeconds, regularHours, orderableHours ...).
"""
from __future__ import annotations

from typing import Any


class TossError(Exception):
    """이 패키지의 모든 에러의 베이스."""


# code -> 사람이 읽을 해결 힌트 (토스가 message/data로 주지만 보강용)
_HINTS: dict[str, str] = {
    # 공통/요청
    "invalid-request": "요청 파라미터를 확인하세요. data.field/allowedValues/constraint에 단서가 있습니다.",
    "unsupported-symbol": "지원하지 않는 심볼입니다(그룹별 심볼 카탈로그 확인).",
    "unsupported-market": "해당 시장을 지원하지 않는 엔드포인트입니다(KR 전용 등).",
    "unsupported-date": "조회 불가한 날짜입니다.",
    "unsupported-ranking-duration": "이 랭킹 타입은 해당 duration을 지원하지 않습니다(예: TOP_GAINERS/LOSERS는 realtime 불가).",
    "account-header-required": "X-Tossinvest-Account 헤더(계좌 seq)가 필요합니다.",
    "account-not-found": "조회 가능한 계좌가 없습니다.",
    "account-restricted": "계좌 상태가 주문을 허용하지 않습니다(거래정지 등).",
    "stock-restricted": "해당 종목은 현재 주문이 제한되어 있습니다.",
    "stock-not-found": "존재하지 않는 종목입니다.",
    "login-user-not-found": "로그인 사용자를 찾을 수 없습니다.",
    # 인증/토큰
    "invalid-token": "토큰이 유효하지 않습니다. 토큰을 재발급합니다(자동 재시도).",
    "expired-token": "토큰이 만료됐습니다. 재발급 후 재시도합니다(자동).",
    "token-revoked": "토큰이 폐기됐습니다(새 토큰 발급 시 이전 토큰 무효화). 캐시를 재확인 후 재발급합니다.",
    # 레이트리밋
    "rate-limit-exceeded": "요청 한도 초과. Retry-After만큼 대기 후 재시도합니다(자동).",
    "edge-rate-limit-exceeded": "엣지 레이트리밋 초과. 호출 빈도를 낮추세요(자동 백오프).",
    # 주문 — 멱등/동시성 (409)
    "request-in-progress": "동일 주문 키(clientOrderId)에 대해 처리 중인 요청이 있습니다. 백오프 후 재시도합니다(자동).",
    "opposite-pending-order-exists": "동일 종목에 반대 방향 미체결 주문이 있습니다. 먼저 정리하세요(재시도 금지).",
    # 주문 — 비즈니스 규칙 (422, 재시도 금지)
    "idempotency-key-conflict": "동일 clientOrderId로 다른 내용의 주문을 재요청했습니다. 이미 접수된 기존 주문을 조회해 재사용합니다.",
    "insufficient-buying-power": "매수가능금액 부족. 주문금액/수량을 줄이거나 buying-power를 먼저 확인하세요.",
    "order-hours-closed": "주문 접수 불가 시간입니다. data.retryAfterAt 이후 재시도하거나 market-calendar를 확인하세요.",
    "amount-order-outside-regular-hours": "미국 금액주문(orderAmount)은 정규장 시작~종료 1시간 전까지만 접수됩니다. data.orderableHours 확인.",
    "fractional-quantity-outside-regular-hours": "미국 소수점 수량 주문은 정규장 시작~종료 1시간 전까지만 접수됩니다. data.orderableHours 확인.",
    "fractional-quantity-scale-exceeded": "소수점 수량은 6자리까지만 허용됩니다(초과분은 내림 후 재요청).",
    "price-out-of-range": "주문 가격이 허용 범위를 벗어났습니다.",
    "order-type-not-allowed": "현재 사용할 수 없는 호가 유형입니다.",
    "prerequisite-required": "주문 전 사전 자격 요건(약관 동의/교육 이수/위험고지)이 필요합니다.",
    "market-not-supported-for-stock": "해당 종목은 이 시장에서 거래할 수 없습니다(KR).",
    "investor-exchange-not-integrated": "투자자지시 거래소가 통합(SOR)으로 설정돼야 주문할 수 있습니다(KR).",
    "max-order-amount-exceeded": "최대 주문가능금액을 초과했습니다(data.limits 확인).",
    "confirm-high-value-required": "1억원 이상 주문은 confirm_high_value=True가 필요합니다.",
    "us-modify-quantity-not-supported": "미국 주식 정정은 수량 변경을 지원하지 않습니다(가격만).",
    "order-not-found": "존재하지 않는 주문입니다.",
    "already-filled": "이미 체결된 주문입니다.",
    "already-canceled": "이미 취소된 주문입니다.",
    "already-rejected": "이미 거부된 주문입니다.",
    "already-modified": "이미 정정된 주문입니다.",
    "already-processing": "이미 처리 중인 주문입니다.",
    "modify-restricted": "정정할 수 없는 주문 상태입니다.",
    "cancel-restricted": "취소할 수 없는 주문 상태입니다.",
    "tick-size-violation": "호가 단위에 맞지 않는 가격입니다(data.tickSize 확인).",
    # 조건주문
    "conditional-order-not-found": "존재하지 않는 조건주문입니다(수정 시 새 conditionalOrderId 발급됨에 유의).",
    # 서버/점검 (5xx)
    "internal-error": "서버 일시 오류. 백오프 후 재시도합니다(자동).",
    "maintenance": "시스템 점검 중입니다. data.retryAfterSeconds가 임계치 이하면 대기 후 재시도, 초과면 즉시 실패합니다.",
}


class TossAPIError(TossError):
    def __init__(
        self,
        http_status: int,
        code: str,
        message: str,
        data: Any = None,
        request_id: str | None = None,
    ) -> None:
        self.http_status = http_status
        self.code = code
        self.message = message
        self.data = data
        self.request_id = request_id
        hint = _HINTS.get(code, "")
        suffix = f" | hint: {hint}" if hint else ""
        rid = f" | requestId={request_id}" if request_id else ""
        super().__init__(f"[{http_status} {code}] {message}{rid}{suffix}")

    @property
    def hint(self) -> str:
        return _HINTS.get(self.code, "")


class AuthError(TossAPIError):
    """토큰 관련 401."""


class RateLimitError(TossAPIError):
    """429. retry_after(초) 포함."""

    def __init__(self, *args: Any, retry_after: float = 1.0, **kwargs: Any) -> None:
        self.retry_after = retry_after
        super().__init__(*args, **kwargs)


class RequestInProgressError(TossAPIError):
    """409 request-in-progress. 동일 clientOrderId 처리 중 — 백오프 후 재시도 대상(멱등 보장)."""


class IdempotencyConflictError(TossAPIError):
    """422 idempotency-key-conflict. 동일 clientOrderId로 다른 본문을 재요청.

    이미 접수된 주문이 존재하므로 재시도 대신 기존 주문을 조회(fetch)해 재사용해야 한다.
    """


class MaintenanceError(TossAPIError):
    """500 maintenance. data.retryAfterSeconds(권장 재시도 지연, 초)를 포함할 수 있다."""

    def __init__(self, *args: Any, retry_after_seconds: float | None = None,
                 **kwargs: Any) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(*args, **kwargs)
