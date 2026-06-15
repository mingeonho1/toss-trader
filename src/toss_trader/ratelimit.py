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


# 토스 명세 기준 그룹별 한도(req/sec). MARKET은 명세 미기재라 보수적으로 설정.
GROUP_RATES: dict[str, float] = {
    "AUTH": 5.0,
    "ACCOUNT": 1.0,
    "ASSET": 5.0,
    "ORDER": 6.0,
    "MARKET": 5.0,
}


def make_buckets() -> dict[str, TokenBucket]:
    # capacity는 rate로 두어 버스트를 억제(소액 스윙엔 충분).
    return {g: TokenBucket(rate=r, capacity=r) for g, r in GROUP_RATES.items()}
