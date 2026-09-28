"""(c12s, 탐색용) btc_proxy_mstr_coin — BTC 추세 프록시로 MSTR/COIN 보유. **Lane A FAIL(설계)**.

experiments/c12_hibeta.py decide_btc_proxy 그대로(파라미터 동결 sma_win=100, names=MSTR/COIN):
BTC > SMA100 이면 살아있는 MSTR/COIN 동일가중, 아니면 현금. 신호 종가 → 익일 종가 체결.

**탐색용(EXPLORATORY) 라벨 — 실전 후보 아님.** 설계 구간(≤2021-12) CAGR 16.8% < QQQ 26.7% 라 Lane A
(a) 조건 미충족 → 설계에서 FAIL. 홀드아웃(2022+)만 보면 최고 수익(57%, Sharpe 1.07)이나 전표본
MDD −72%(설계기 COIN 미상장→MSTR 단독 + 크립토 급락). 규제상 1x 단일주(MSTR/COIN)는 매수 가능해
포워드 감시 가치만 있어 소액 슬리브로 편입한다.

BTC 프록시: 원본은 FRED CBBTCUSD(현물 비트코인)를 100일 SMA 와 비교했다. 포워드 페이퍼는 키리스
일봉 캐시만 쓰므로 CBBTCUSD 캐시가 있으면 그걸, 없으면 프록시로 **IBIT(현물 BTC ETF)** 종가를 쓴다
(둘 다 없으면 항상 현금). 어느 쪽을 썼는지 상태에 기록한다.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from ..research import sma

NAMES = ("MSTR", "COIN")
SMA_WIN = 100
BTC_PROXIES = ("CBBTCUSD", "IBIT", "BITO", "GBTC")   # 우선순위: 현물지수 → 현물 ETF


def _btc_series(history: Mapping[str, Sequence[Candle]]) -> tuple[str | None, list[float]]:
    for sym in BTC_PROXIES:
        h = history.get(sym)
        if h:
            return sym, [c.close for c in h]
    return None, []


def decide_btc_proxy(history: Mapping[str, Sequence[Candle]], sma_win: int = SMA_WIN) -> dict[str, float]:
    _, btc = _btc_series(history)
    if len(btc) < sma_win:
        return {}
    m = sma(btc, sma_win)[-1]
    if m is None or btc[-1] <= 0 or btc[-1] <= m:
        return {}                                  # BTC ≤ SMA100 → 현금
    live = [s for s in NAMES if history.get(s)]
    if not live:
        return {}
    w = 1.0 / len(live)
    return {s: w for s in live}


class BtcProxyMstrCoin(Strategy):
    name = "btc_proxy_mstr_coin"
    fill_on = "close"
    group = "explore"          # 탐색용(Lane A 설계 FAIL) — 소액 슬리브 감시 대상

    def universe(self) -> list[str]:
        return list(NAMES) + list(BTC_PROXIES)

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        src, _ = _btc_series(history)
        if src is not None:
            state["btc_source"] = src
        return decide_btc_proxy(history)
