"""(c12s) hibeta_basket — "레버리지 ETF 없는 레버리지". Lane A PASS(c12s_hibeta_basket, 유일 PASS).

아이디어: 소액·무이력 한국 리테일은 규제상 TQQQ/QLD(해외 레버리지 ETP)를 못 살 수 있어(예탁금
₩1,000만+교육), 대신 **일반 고베타 단일주식**(소수점 매수 허용)으로 공격적(≈2x) 노출을 만든다.

결정(experiments/c12_hibeta.py decide_hibeta_basket, 파라미터 동결 topk=10/beta_win=252/min_price=$5,
use_filter=False=상시투자): 매월 첫 거래일에 유니버스 중 최소가 ≥$5 이고 252일 QQQ 베타를 추정할 수
있는 이름을 **베타 상위 10** 뽑아 동일가중. 그 달 내내 홀드/드리프트. 신호 종가 → 익일 종가 체결.

포워드 차이(생존편향 없음): 원본은 PIT(as-of) S&P500 멤버십을 썼지만, **포워드는 미래 편향이 없으므로**
"오늘의 S&P500 후보 ∩ 로컬 캐시" 유니버스를 그대로 쓴다(러너가 주입). 캐시는 대형·고유동 편중이라
진짜 초고베타 소형주는 대부분 없다 → 실현 베타가 목표(≈2)보다 낮고, 성과는 **상한(UPPER BOUND)** 으로
읽어야 한다(experiments 리포트의 커버리지 caveat 계승). 타이밍은 분기/월 경계를 인과적으로 잡아
월초(첫 거래일)에 리밸런스한다(원본 월말 신호의 포워드-안전 등가).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from .hibeta_universe import HIBETA_CANDIDATES

BENCH = "QQQ"
TOPK = 10
BETA_WIN = 252
MIN_PRICE = 5.0
MIN_FRACTION = 0.8          # 베타 창의 최소 유효수익 비율(experiments beta_to_bench 규약)


def _beta_to_bench(stock: Sequence[Candle], qmap: Mapping, win: int) -> float | None:
    """252일 QQQ 대비 OLS 베타(cov/var). 공통 거래일의 마지막 win+1 종가로 추정. 부족/무분산이면 None."""
    pairs: list[tuple[float, float]] = []
    for c in stock:
        q = qmap.get(c.dt)
        if q and q > 0 and c.close > 0:
            pairs.append((c.close, q))
    if len(pairs) < 2:
        return None
    pairs = pairs[-(win + 1):]
    rs: list[float] = []
    rq: list[float] = []
    for i in range(1, len(pairs)):
        s0, q0 = pairs[i - 1]
        s1, q1 = pairs[i]
        if s0 > 0 and q0 > 0:
            rs.append(s1 / s0 - 1.0)
            rq.append(q1 / q0 - 1.0)
    if len(rq) < int(win * MIN_FRACTION):
        return None
    mb = sum(rq) / len(rq)
    ms = sum(rs) / len(rs)
    var = sum((x - mb) ** 2 for x in rq)
    if var <= 0:
        return None
    cov = sum((rs[i] - ms) * (rq[i] - mb) for i in range(len(rq)))
    return cov / var


def select_basket(history: Mapping[str, Sequence[Candle]], candidates: Sequence[str], *,
                  topk: int = TOPK, beta_win: int = BETA_WIN,
                  min_price: float = MIN_PRICE) -> dict[str, float]:
    """오늘 기준 고베타 상위 topk 동일가중 목표비중(하나도 없으면 현금)."""
    qh = history.get(BENCH) or []
    if len(qh) < beta_win + 1:
        return {}
    today = qh[-1].dt
    qmap = {c.dt: c.close for c in qh}
    scored: list[tuple[float, str]] = []
    for s in candidates:
        h = history.get(s) or []
        if not h or h[-1].dt != today or h[-1].close < min_price:
            continue
        b = _beta_to_bench(h, qmap, beta_win)
        if b is not None:
            scored.append((b, s))
    scored.sort(reverse=True)
    chosen = [s for _, s in scored[:topk]]
    if not chosen:
        return {}
    w = 1.0 / len(chosen)
    return {s: w for s in chosen}


def _month(d) -> tuple[int, int]:
    return (d.year, d.month)


class HibetaBasket(Strategy):
    name = "hibeta_basket"
    fill_on = "close"

    def __init__(self, universe_names: Sequence[str] | None = None) -> None:
        cands = list(universe_names) if universe_names else list(HIBETA_CANDIDATES)
        # 벤치(QQQ)는 신호용으로만; 후보에서는 제외.
        self._candidates = [s for s in dict.fromkeys(cands) if s != BENCH]

    def universe(self) -> list[str]:
        return [BENCH] + list(self._candidates)

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        qh = history.get(BENCH) or []
        if not qh:
            return dict(state.get("last_weights") or {})
        m_now = _month(qh[-1].dt)
        if tuple(state.get("last_month") or ()) != m_now:      # 월초 첫 거래일 → 리밸런스
            state["last_weights"] = select_basket(history, self._candidates)
            state["last_month"] = m_now
        return dict(state.get("last_weights") or {})
