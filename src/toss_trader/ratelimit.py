"""스레드 안전 토큰버킷. 토스 그룹별 req/sec 한도를 클라이언트 측에서 선제 제어한다."""
from __future__ import annotations

import threading
import time


class TokenBucket:
    """rate 토큰/초로 리필되는 버킷. acquire()는 토큰이 생길 때까지 블로킹."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else max(1.0, rate))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                # 부족분이 채워질 때까지 대기할 시간 계산 (락 밖에서 sleep)
                sleep_for = (n - self._tokens) / self.rate
            time.sleep(max(0.0, sleep_for))


# 토스 OpenAPI v1.1.5의 실제 Rate Limits Group(엔드포인트별 태그).
# 명세는 구체 수치를 공개하지 않고 응답 헤더(X-RateLimit-Limit/Remaining/Reset)로
# 동적 전달한다(예시 burst=10/sec). 여기서는 그 한도 아래로 보수적 선제 제어만 하고,
# 실제 한도는 client가 X-RateLimit 헤더로 적응 throttle + 429 Retry-After로 처리한다.
GROUP_RATES: dict[str, float] = {
    "AUTH": 1.0,               # 액세스 토큰은 1개만 유효, 재발급은 드물다
    "MARKET_DATA": 8.0,        # 호가/현재가/체결/상하한가
    "MARKET_DATA_CHART": 4.0,  # 캔들 차트(별도 그룹)
    "MARKET_INFO": 4.0,        # 환율/장 운영 정보
    "STOCK": 4.0,              # 종목 기본정보/유의사항
    "ACCOUNT": 2.0,            # 계좌 목록
    "ASSET": 4.0,              # 보유 주식
    "ORDER_INFO": 4.0,         # 매수가능금액/매도가능수량/수수료
    "ORDER": 4.0,              # 주문 생성/정정/취소
    "ORDER_HISTORY": 4.0,      # 주문 목록/상세
}

# 알 수 없는 그룹이 들어오면 안전하게 떨어질 기본 버킷 키.
DEFAULT_GROUP = "MARKET_DATA"


def make_buckets() -> dict[str, TokenBucket]:
    # capacity는 rate로 두어 버스트를 억제(소액 스윙엔 충분).
    return {g: TokenBucket(rate=r, capacity=r) for g, r in GROUP_RATES.items()}
