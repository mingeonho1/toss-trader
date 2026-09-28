"""(A3) 9Sig — TQQQ/AGG 60/40, 분기 9% 신호선 리밸런싱. Lane A PASS(c12_a3_9sig).

experiments/c12_trend_vol.py simulate_9sig 의 **리밸런스 산술을 그대로** 목표비중 공간으로 포팅한다
(파라미터 동결 growth=9%, 시작 60/40, 매수캡 90%·바닥 10%AGG, 30Down·Spike·Base 리셋).
내부에 정규화 모형(v_tq,v_ag)을 두고 매일 가격수익으로 드리프트, 분기 리밸런스일에만 새 목표비중을
낸다(그 외 날은 직전 목표 유지 → 엔진이 홀드/드리프트). tests/test_paperlab_ninesig.py 가 분기 스텝
산술이 simulate_9sig 와 동일함을 픽스처로 검증한다.

타이밍(포워드 안전): 원본은 분기말(3/6/9/12 마지막 거래일)에 신호→다음날 체결. 포워드 엔진은 오늘이
그 달의 마지막 거래일인지 미리 알 수 없으므로(미래참조) **분기 첫 거래일**(1/4/7/10 진입)에 리밸런스한다
— 인과적 등가 규약. 리밸런스 산술 자체는 동일.

적립(task): $1,000 단위장부는 **무적립**(순수 성과), $36 실장부는 매월 첫 거래일 **+$35 적립**
(``real_monthly_contribution``). 엔진이 적립 현금을 실장부에 넣고, 다음 분기 리밸런스에서 그때의 9Sig
목표비중대로 배분한다(정통 9Sig 의 '채권측 적립'을 공유-목표 엔진에서 근사; $36 Total 은 자금가중).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy

TQQQ = "TQQQ"
BOND = "AGG"
GROWTH = 0.09
MONTHLY_CONTRIB = 35.0


def _fresh_model() -> dict[str, Any]:
    return {"v_tq": 0.6, "v_ag": 0.4, "signal_line": 0.6, "ignore_sells": 0,
            "qcloses": [], "prev_qclose": None}


def ninesig_rebalance(m: dict[str, Any], close_t: float, *, growth: float = GROWTH) -> None:
    """분기 리밸런스 1스텝(simulate_9sig 의 qset 블록과 동일). m 을 제자리 갱신."""
    v_tq = float(m["v_tq"])
    v_ag = float(m["v_ag"])
    signal_line = float(m["signal_line"])
    ignore_sells = int(m["ignore_sells"])
    qcloses = list(m["qcloses"])
    prev_qclose = m["prev_qclose"]

    recent = qcloses[-8:] + [close_t]
    thirty_down = close_t <= 0.70 * max(recent) if recent else False
    spike = (prev_qclose is not None and prev_qclose > 0
             and close_t / prev_qclose - 1.0 >= 1.0)
    target = signal_line * (1.0 + growth)
    if v_tq > target:                                  # 매도신호
        if ignore_sells > 0:
            ignore_sells -= 1
        else:
            sell = v_tq - target
            v_tq -= sell
            v_ag += sell
            if v_ag > 0.30 * (v_tq + v_ag):            # Base reset (원본 순서 그대로)
                v_tq = 0.6 * (v_tq + v_ag)
                v_ag = 0.4 * (v_tq + v_ag)
                signal_line = v_tq
                target = signal_line
    elif v_tq < target:                                # 매수신호(캡)
        need = target - v_tq
        cap = 0.9 * v_ag
        buy = min(need, cap)
        port2 = v_tq + v_ag
        if (v_ag - buy) < 0.10 * port2:
            buy = max(0.0, v_ag - 0.10 * port2)
        if buy > 0:
            v_tq += buy
            v_ag -= buy
    signal_line = target
    if thirty_down:
        ignore_sells = 2
    if spike and not thirty_down:                      # Spike reset
        v_tq = 0.6 * (v_tq + v_ag)
        v_ag = 0.4 * (v_tq + v_ag)
        signal_line = v_tq
    qcloses.append(close_t)
    prev_qclose = close_t

    m["v_tq"], m["v_ag"], m["signal_line"] = v_tq, v_ag, signal_line
    m["ignore_sells"], m["qcloses"], m["prev_qclose"] = ignore_sells, qcloses, prev_qclose


def _weights(m: dict[str, Any]) -> dict[str, float]:
    tot = float(m["v_tq"]) + float(m["v_ag"])
    if tot <= 0:
        return {}
    return {TQQQ: float(m["v_tq"]) / tot, BOND: float(m["v_ag"]) / tot}


def _quarter(d) -> tuple[int, int]:
    return (d.year, (d.month - 1) // 3)


class NineSig(Strategy):
    name = "nine_sig"
    fill_on = "close"
    real_monthly_contribution = MONTHLY_CONTRIB      # $36 실장부만(포워드). 단위장부는 무적립.

    def universe(self) -> list[str]:
        return [TQQQ, BOND]

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        htq = history.get(TQQQ) or []
        hag = history.get(BOND) or []
        if not htq or not hag:
            return dict(state.get("last_weights") or {})
        tq_c = htq[-1].close
        ag_c = hag[-1].close
        today = htq[-1].dt

        m = state.get("model")
        if m is None:                                # 최초: 60/40 초기 배분
            m = _fresh_model()
            state["model"] = m
            state["last_tq"] = tq_c
            state["last_ag"] = ag_c
            state["last_q"] = _quarter(today)
            w = _weights(m)
            state["last_weights"] = w
            return dict(w)

        last_tq = float(state.get("last_tq") or tq_c)
        last_ag = float(state.get("last_ag") or ag_c)
        if last_tq > 0 and tq_c > 0:
            m["v_tq"] = float(m["v_tq"]) * (tq_c / last_tq)
        if last_ag > 0 and ag_c > 0:
            m["v_ag"] = float(m["v_ag"]) * (ag_c / last_ag)
        state["last_tq"] = tq_c
        state["last_ag"] = ag_c

        q_now = _quarter(today)
        if tuple(state.get("last_q") or q_now) != q_now:   # 새 분기 첫 거래일 → 리밸런스
            ninesig_rebalance(m, tq_c)
            state["last_weights"] = _weights(m)
        state["last_q"] = q_now
        return dict(state.get("last_weights") or _weights(m))
