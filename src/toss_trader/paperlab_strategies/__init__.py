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
from .btc_proxy import BtcProxyMstrCoin
from .ep_gap_swing import EpGapSwing
from .ftlt import Ftlt, Ftlt1x, FtltMoc
from .hibeta_basket import HibetaBasket
from .hibeta_signal import (BufferHibeta, FtltHibeta, FtltHibetaPsq, HolygrailHibeta,
                            SimpleHibeta)
from .holy_grail import HolyGrail
from .hot_rvol_swing import HotRvolSwing
from .lrr_tqqq import LrrTqqq
from .lrr_tqqq_sqqq import LrrTqqqSqqq
from .lrs200 import Lrs200Qqq, Lrs200Tqqq
from .mom_top5_ndx import MomTop5Ndx
from .nine_sig import NineSig
from .overnight import OvernightQqq, OvernightTqqq
from .rsi2_tqqq import Rsi2Tqqq
from .simple_rsi_uvxy import SimpleRsiUvxy
from .sma200_buffer import Sma200BufferTqqq
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
    # 2026-09-28 신규 사전등록(docs/intraday_shadow_rules.md §6.3)
    "overnight_tqqq": OvernightTqqq,
    "overnight_qqq": OvernightQqq,
    "ep_gap_swing": EpGapSwing,
    # 사이클12 Lane A PASS 이식(Composer B1/B2/B5 · Trend/Vol A1/A2/A3 · hibeta) + 탐색용 btc_proxy.
    "ftlt": Ftlt,                       # B1 FTLT (t+1 체결, 보수적)
    "ftlt_moc": FtltMoc,                # B1 FTLT (당일 종가 체결, 낙관적)
    "ftlt_1x": Ftlt1x,                  # B1 1x 섀도(비레버리지)
    "holy_grail": HolyGrail,            # B2
    "simple_rsi_uvxy": SimpleRsiUvxy,   # B5
    "lrs200_tqqq": Lrs200Tqqq,          # A1 (밴드0; lrr_tqqq 와 별개)
    "lrs200_qqq": Lrs200Qqq,            # A1 1x 섀도
    "sma200_buffer_tqqq": Sma200BufferTqqq,  # A2
    "nine_sig": NineSig,                # A3
    "hibeta_basket": HibetaBasket,      # c12s (고베타 단일주 바스켓)
    "btc_proxy_mstr_coin": BtcProxyMstrCoin,  # c12s 탐색용(Lane A FAIL)
    # 사이클13a: 신호형(비레버리지 실계좌판) — 위험선호 레그를 고베타 바스켓으로. 전부 Lane A PASS.
    "ftlt_hibeta": FtltHibeta,
    "ftlt_hibeta_psq": FtltHibetaPsq,   # 인버스 레그 → PSQ(−1x, 규제 불확실 참고용)
    "holygrail_hibeta": HolygrailHibeta,
    "simple_hibeta": SimpleHibeta,
    "buffer_hibeta": BufferHibeta,
}

# 러너가 고베타 후보 유니버스(HIBETA_CANDIDATES ∩ 캐시)를 주입할 전략들.
HIBETA_INJECT = {"hibeta_basket", "ftlt_hibeta", "ftlt_hibeta_psq", "holygrail_hibeta",
                 "simple_hibeta", "buffer_hibeta"}


def register(name: str, factory: Callable[..., Strategy]) -> None:
    """신규 전략 팩토리 등록(런타임 확장점)."""
    ROSTER_FACTORIES[name] = factory


def build_roster(*, swing_universe: Sequence[str] | None = None,
                 mom_universe: Sequence[str] | None = None,
                 hibeta_universe: Sequence[str] | None = None) -> list[Strategy]:
    """초기 로스터 인스턴스 목록. 데이터 인지 전략에는 캐시 유니버스를 주입한다.

    - ``swing_universe``: hot_rvol_swing 이 스캔할 캐시 심볼 목록(없으면 모듈 기본값).
    - ``mom_universe``: mom_top5_ndx 후보(없으면 NDX_100 전체; 러너가 캐시 교집합으로 좁힌다).
    - ``hibeta_universe``: hibeta_basket 후보(없으면 모듈 기본 HIBETA_CANDIDATES; 러너가 캐시 교집합).
    """
    roster: list[Strategy] = []
    for name, factory in ROSTER_FACTORIES.items():
        if name == "hot_rvol_swing":
            roster.append(factory(swing_universe))
        elif name in ("mom_top5_ndx", "ep_gap_swing"):
            roster.append(factory(mom_universe))     # 대형주 캐시 유니버스(NDX100 ∩ 캐시)
        elif name in HIBETA_INJECT:
            roster.append(factory(hibeta_universe))  # 고베타 후보(HIBETA_CANDIDATES ∩ 캐시)
        else:
            roster.append(factory())
    return roster
