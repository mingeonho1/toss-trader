"""토스 API 에러 모델. 코드별 한국어 해결 힌트 포함."""
from __future__ import annotations

from typing import Any


class TossError(Exception):
    """이 패키지의 모든 에러의 베이스."""


# code -> 사람이 읽을 해결 힌트 (토스가 message/data로 주지만 보강용)
_HINTS: dict[str, str] = {
    "invalid-request": "요청 파라미터를 확인하세요. data.field/allowedValues에 단서가 있습니다.",
    "invalid-token": "토큰이 유효하지 않습니다. 토큰을 재발급합니다(자동 재시도).",
    "expired-token": "토큰이 만료됐습니다. 재발급 후 재시도합니다(자동).",
    "insufficient-buying-power": "매수가능금액 부족. 주문금액/수량을 줄이거나 buying-power를 먼저 확인하세요.",
    "order-hours-closed": "주문 가능 시간이 아닙니다. market-calendar로 장 운영 여부를 확인하세요.",
    "rate-limit-exceeded": "요청 한도 초과. Retry-After만큼 대기 후 재시도합니다(자동).",
    "edge-rate-limit-exceeded": "엣지 레이트리밋 초과. 호출 빈도를 낮추세요(자동 백오프).",
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
