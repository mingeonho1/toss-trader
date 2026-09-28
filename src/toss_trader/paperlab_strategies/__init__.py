"""공격형 페이퍼 랩 초기 로스터 + 신규 전략 확장 훅.

각 전략은 이 패키지의 별도 모듈이며 :class:`~toss_trader.paperlab.Strategy` 를 상속한다.
파라미터는 **동결**(frozen)이다 — 사후조정 금지(부록 v3 Lane A: 사전등록 설정 1개 + 이웃만).

신규 전략 추가(hook, docs/aggressive_strategy_catalog.md 후보용):
    1) 이 패키지에 ``my_strategy.py`` 를 만들고 ``Strategy`` 하위클래스를 정의.
    2) 아래 ``build_roster`` 의 리스트에 인스턴스를 추가(또는 ``register`` 로 등록).
그 외 엔진/리포트/러너는 손대지 않아도 새 전략이 리더보드에 자동 편입된다.
"""
from __future__ import annotations

from typing import Callable, Sequence

from ..paperlab import Strategy
from .benchmarks import QqqBH, TqqqBH
from .hot_rvol_swing import HotRvolSwing
from .lrr_tqqq import LrrTqqq
from .lrr_tqqq_sqqq import LrrTqqqSqqq
from .mom_top5_ndx import MomTop5Ndx
from .rsi2_tqqq import Rsi2Tqqq
from .voltarget_3x import Voltarget3x

__all__ = ["build_roster", "register", "ROSTER_FACTORIES"]

# 확장 훅: (name -> factory). 새 전략은 여기에 등록하면 build_roster 에 자동 포함된다.
ROSTER_FACTORIES: dict[str, Callable[..., Strategy]] = {
    "lrr_tqqq": LrrTqqq,
    "lrr_tqqq_sqqq": LrrTqqqSqqq,
    "rsi2_tqqq": Rsi2Tqqq,
    "voltarget_3x": Voltarget3x,
    "mom_top5_ndx": MomTop5Ndx,
    "hot_rvol_swing": HotRvolSwing,
    "qqq_bh": QqqBH,
    "tqqq_bh": TqqqBH,
}


def register(name: str, factory: Callable[..., Strategy]) -> None:
    """신규 전략 팩토리 등록(런타임 확장점)."""
    ROSTER_FACTORIES[name] = factory


def build_roster(*, swing_universe: Sequence[str] | None = None,
                 mom_universe: Sequence[str] | None = None) -> list[Strategy]:
    """초기 로스터 인스턴스 목록. 데이터 인지 전략에는 캐시 유니버스를 주입한다.

    - ``swing_universe``: hot_rvol_swing 이 스캔할 캐시 심볼 목록(없으면 모듈 기본값).
    - ``mom_universe``: mom_top5_ndx 후보(없으면 NDX_100 전체; 러너가 캐시 교집합으로 좁힌다).
    """
    roster: list[Strategy] = []
    for name, factory in ROSTER_FACTORIES.items():
        if name == "hot_rvol_swing":
            roster.append(factory(swing_universe))
        elif name == "mom_top5_ndx":
            roster.append(factory(mom_universe))
        else:
            roster.append(factory())
    return roster
