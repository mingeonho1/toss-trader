"""옵트인 라이프사이클(생애주기) 레버리지 DCA 정책 — 순수 함수, 표준 라이브러리만.

Ayres & Nalebuff, *Lifecycle Investing*(2008/2010)의 Samuelson share(사무엘슨 지분)를
따르는 **글라이드(glide)** 목표노출 정책의 **프로덕션용 재구현**이다. 연구 코드
(`experiments/c5a_lifecycle.py`)의 `target_exposure(..., mode="glide")` 수식을
**import 없이** 그대로 옮겨 적었다(프로덕션이 experiments/ 에 의존하면 안 된다).
동치성은 `tests/test_policy_lifecycle.py` 가 그리드에서 수치 일치로 검증한다.

핵심 수식 (glide):

    PV_t = 남은 미래 월적립의 현재가치(연금 현가, disc_real 실질 할인)
    E_t  = clip( s_star · (W_t + PV_t) / W_t , e_min, e_max )

여기서 W_t 는 **이미 투자된 금융자산**, s_star 는 생애 부(富) 목표 주식지분(기본 0.8).
W 가 작을수록(적립 PV 대비) 시간축 분산이 덜 되어 초기 ~2:1 레버리지 → 포트폴리오가
커지며 1x 로 글라이드한다. 이는 **알파가 아니라 베타/위험선호** 결정이다.

⚠️ 리스크(README §라이프사이클 참고): 이 정책은 QQQ(1x)+QLD(2x) 혼합으로 레버리지를 태운다.
백테스트상 닷컴 시작 코호트의 **단위자본 최악 낙폭 ≈ −99%**, 10년 지평은 사전등록 규칙 미달,
표준 절대낙폭 게이트로는 레버리지 자체가 FAIL 이다. 채택은 **소액 슬리브 한정 + 포워드 페이퍼
점증 후에만**. 기본값은 항상 비활성(POLICY=dca, LIFECYCLE_SLEEVE=0)이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "QQQ", "QLD",
    "pv_remaining", "lifecycle_target", "allocation_for_exposure",
    "deposit_plan", "DepositPlan",
]

QQQ = "QQQ"   # 1x 코어
QLD = "QLD"   # 2x 레버리지


def pv_remaining(monthly_usd: float, months_remaining: int, *,
                 disc_real: float = 0.03) -> float:
    """남은 미래 월적립 `months_remaining` 개의 현재가치(연금 현가).

    r_m = (1+disc_real)^(1/12) − 1 (월 실질 할인율). PV = monthly·(1−(1+r_m)^−N)/r_m.
    할인율이 0 이하이면 단순 합(monthly·N). N<=0 이면 0.

    experiments/c5a_lifecycle.py 의 `pv_remaining(month_m, plan_months, monthly, disc_real)`
    는 내부적으로 N = plan_months−1−month_m(방금 낸 입금 이후 남은 미래 기여 개수)을 쓴다.
    따라서 이 함수의 `months_remaining` 는 그 N 과 같은 의미다.
    """
    n = max(0, int(months_remaining))
    if n <= 0:
        return 0.0
    r_m = (1.0 + disc_real) ** (1.0 / 12.0) - 1.0
    if r_m <= 0:
        return float(monthly_usd) * n
    return float(monthly_usd) * (1.0 - (1.0 + r_m) ** (-n)) / r_m


def lifecycle_target(W_usd: float, monthly_usd: float, months_remaining: int, *,
                     s_star: float = 0.8, disc_real: float = 0.03,
                     e_min: float = 1.0, e_max: float = 2.0) -> float:
    """목표노출 E (베타 단위). E = clip(s_star·(W+PV)/W, e_min, e_max).

    W_usd 는 **이미 투자된** 금융자산(슬리브 기준). W<=0 이면(시드 직전) e_max 로 시작한다
    (초기 최대 레버리지 → 이후 W 가 커지며 e_min 으로 글라이드).
    """
    if W_usd <= 0:
        return e_max
    pv = pv_remaining(monthly_usd, months_remaining, disc_real=disc_real)
    base = s_star * (W_usd + pv) / W_usd
    return min(e_max, max(e_min, base))


def allocation_for_exposure(E: float) -> dict[str, float]:
    """목표노출 E(1~2) → 투자자본 비중 {QQQ: 2−E, QLD: E−1}.

    노출 = 1·w_qqq + 2·w_qld = E, w_qqq + w_qld = 1 (완전투자, 롱온리). E 는 [1,2] 로
    클립해 항상 롱온리·합 1 을 보장한다(정책 함수가 e_min=1·e_max=2 로 반환하므로 통상 무해).
    """
    e = min(2.0, max(1.0, float(E)))
    return {QQQ: 2.0 - e, QLD: e - 1.0}


@dataclass(frozen=True)
class DepositPlan:
    """라이프사이클 슬리브의 신규현금 배치 플랜(달러 단위).

    - buys/sells: {symbol: usd}. 매수전용 경로에선 sells 가 비어 있다.
    - sell_triggered: 실제노출 E_actual > E_target + band 일 때만 True(초과 레버리지 감축).
    - weights: 목표 투자자본 비중(QQQ/QLD). equity_ref: 목표 산정 기준자산(보유+현금).
    """
    e_target: float
    e_actual: float
    weights: dict[str, float]
    buys: dict[str, float] = field(default_factory=dict)
    sells: dict[str, float] = field(default_factory=dict)
    sell_triggered: bool = False
    equity_ref: float = 0.0


def deposit_plan(holdings_value_by_sym: dict[str, float], cash_usd: float,
                 E_target: float, band: float = 0.3, *,
                 sells_executed: bool = True) -> DepositPlan:
    """신규현금을 목표비중으로 배치. 초과 레버리지일 때만 매도 리스트를 낸다.

    규약(experiments/c5a_lifecycle.py 의 월별 결정과 동일한 취지):
    - E_actual = (2·QLD + 1·QQQ) / (QQQ+QLD)  ← **투자된 자산만**의 실효 노출(현금 제외).
    - E_actual ≤ E_target + band: **매수전용**. equity_ref = 보유+현금 기준 목표까지 미달분을
      큰 쪽부터 가용현금으로 매수(매도 없음 → 저회전).
    - E_actual > E_target + band: 초과 레버리지 → 과매수분 매도 신호(sells)를 낸다.

    ⚠️ `sells_executed`: 이 매도 신호가 **실제로 집행되는가**에 따라 매수 자금이 달라진다.
    - True(기본, 예: forward_lifecycle_paper 페이퍼): 매도 대금이 실현되므로 목표비중까지
      **완전 리밸런싱**(미달분 전액 매수). 기존 동작 보존.
    - False(예: run_dca 실계좌 — 매도는 경고만, 자동 집행 안 함): 매도 대금이 없으므로 매수는
      매수전용과 동일하게 **가용현금으로만** 집행(현금 초과 매수 → insufficient-buying-power·과매수 방지).

    QQQ/QLD 외 심볼은 무시한다(슬리브는 QQQ/QLD 전용).
    """
    qqq = max(0.0, float(holdings_value_by_sym.get(QQQ, 0.0)))
    qld = max(0.0, float(holdings_value_by_sym.get(QLD, 0.0)))
    cash = max(0.0, float(cash_usd))
    invested = qqq + qld
    e_actual = (2.0 * qld + qqq) / invested if invested > 1e-12 else 0.0

    weights = allocation_for_exposure(E_target)
    w_qqq, w_qld = weights[QQQ], weights[QLD]
    equity_ref = invested + cash
    targets = {QQQ: w_qqq * equity_ref, QLD: w_qld * equity_ref}
    cur = {QQQ: qqq, QLD: qld}

    sell_triggered = invested > 1e-12 and e_actual > E_target + band
    buys: dict[str, float] = {}
    sells: dict[str, float] = {}

    if sell_triggered:
        for sym in (QQQ, QLD):
            d = targets[sym] - cur[sym]
            if d < -1e-9:
                sells[sym] = -d

    if sell_triggered and sells_executed:
        # 매도 대금 실현 가정 → 목표비중까지 완전 매수(과매수분 매도 + 미달분 전액 매수).
        for sym in (QQQ, QLD):
            d = targets[sym] - cur[sym]
            if d > 1e-9:
                buys[sym] = d
    else:
        # (밴드 내 매수전용) 또는 (매도 미집행): 가용현금으로만 미달분 큰 자산부터 목표까지 매수.
        needs = {sym: max(0.0, targets[sym] - cur[sym]) for sym in (QQQ, QLD)}
        remaining = cash
        for sym in sorted(needs, key=lambda s: needs[s], reverse=True):
            if remaining <= 1e-9:
                break
            spend = min(needs[sym], remaining)
            if spend > 1e-9:
                buys[sym] = spend
                remaining -= spend

    return DepositPlan(
        e_target=float(E_target), e_actual=e_actual, weights=weights,
        buys=buys, sells=sells, sell_triggered=sell_triggered, equity_ref=equity_ref,
    )
