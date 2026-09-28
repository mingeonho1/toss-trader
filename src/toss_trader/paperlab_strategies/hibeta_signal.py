"""(c13a) 신호형 전략의 위험선호 레그를 **고베타 단일주 바스켓**으로 실행 — 비레버리지 실계좌판.

동기(reports/cycle13_c13a_signal_hibeta.md): FTLT/Holy Grail/Simple-RSI/200SMA-버퍼 신호는 Lane A
PASS 지만 위험선호 레그가 TQQQ/SPXL/TECL(해외 레버리지 ETP)라 소액·무이력 한국 리테일이 못 살 수
있다. 위험선호 레그를 **고베타 10종목 바스켓**(c12s_hibeta_basket, 실현 β≈1.6, 소수점 매수 자유)으로,
변동성/인버스 레그는 **현금**으로 치환하면 4종이 그대로 Lane A PASS 한다(실계좌 가능·비레버리지).

레그 매핑(experiments/c13a_signal_hibeta.py remap_leg 그대로, 동결):
  위험선호 3x(TQQQ/SPXL/TECL/SOXL/…) → 고베타 바스켓(select_basket, 상위10 EW)
  변동성(UVXY/…)                      → 현금
  인버스(SQQQ/…)                      → 1차 **현금** / 변형 **PSQ(−1x)**
  채권/현금성(BSV→SHY, BIL→BIL, …)    → 허용 1x
  1x 주식 ETF(QQQ/SPY/SMH)            → QQQ
  BIL/SHY/QQQ/PSQ                     → 그대로
체결: 신호 종가 t → 익일 t+1 종가. 그룹 = 실계좌 가능(비레버리지).

전략: ftlt_hibeta(b1) · holygrail_hibeta(b2) · simple_hibeta(b5) · buffer_hibeta(a2) — 전부 PASS.
변형: ftlt_hibeta_psq — 인버스 레그를 PSQ(−1x QQQ)로. **주의**: PSQ 는 −1x 단일 인버스라 한국
레버리지-ETP 예탁금 규칙 밖이지만(=규칙상 매수 가능처럼 보임), 인버스 ETF 자체가 규제 게이트에
걸릴 불확실성이 있어 참고용으로만 둔다.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Candle
from ..paperlab import Strategy
from .ftlt import SIGNAL_SYMBOLS as FTLT_SYMS, decide_ftlt
from .hibeta_basket import select_basket
from .hibeta_universe import HIBETA_CANDIDATES
from .holy_grail import SIGNAL_SYMBOLS as HG_SYMS, decide_holy_grail
from .simple_rsi_uvxy import decide_simple_rsi
from .sma200_buffer import decide_buffer

# c13a remap_leg 범주(동결). 위험선호 3x → 바스켓, 변동성 → 현금, 인버스 → 현금/PSQ, 채권 → 1x.
RISK_ON_3X = {"TQQQ", "SPXL", "UPRO", "TECL", "SOXL", "FAS", "HIBL", "QLD", "SSO",
              "TNA", "CURE", "LABU", "DRN", "FNGU"}
VOL = {"UVXY", "VIXY", "VIXM", "SVIX", "SVXY"}
INVERSE = {"SQQQ", "SOXS", "SPXU", "TECS", "SH", "PSQ"}
BOND_MAP = {"BSV": "SHY", "BIL": "BIL", "SHY": "SHY", "SHV": "BIL", "IEF": "SHY",
            "TMF": "SHY", "TLT": "SHY", "AGG": "SHY", "BOXX": "BIL"}
EQ1X = {"QQQ": "QQQ", "SPY": "QQQ", "SMH": "QQQ"}
PASSTHROUGH = {"BIL", "SHY", "QQQ", "PSQ"}
EXEC_EXTRA = ["QQQ", "BIL", "SHY", "PSQ"]      # 방어/인버스/베타 실행에 필요한 1x 심볼


def remap_signal(sig: Mapping[str, float], basket: Mapping[str, float], *,
                 inverse_to: str = "cash") -> dict[str, float]:
    """원 신호(레버리지 심볼) → c13a 실행 심볼. 위험선호는 basket(해소된 고베타 바스켓)으로."""
    out: dict[str, float] = {}
    for s, wt in sig.items():
        if s in RISK_ON_3X:
            for bs, bw in basket.items():
                out[bs] = out.get(bs, 0.0) + wt * bw
        elif s in VOL:
            continue                              # 현금
        elif s in INVERSE:
            if inverse_to == "PSQ":
                out["PSQ"] = out.get("PSQ", 0.0) + wt
            # else 현금
        elif s in BOND_MAP:
            t = BOND_MAP[s]
            out[t] = out.get(t, 0.0) + wt
        elif s in EQ1X:
            t = EQ1X[s]
            out[t] = out.get(t, 0.0) + wt
        elif s in PASSTHROUGH:
            out[s] = out.get(s, 0.0) + wt
        # else: 미지 심볼 → 현금(보수적)
    return out


class _HibetaSignal(Strategy):
    group = "retail"           # 실계좌 가능(비레버리지) — 신호에만 레버리지 심볼 사용, 보유는 1x/주식.
    fill_on = "close"
    _inverse_to = "cash"
    _signal_syms: list[str] = []

    def __init__(self, universe_names: Sequence[str] | None = None) -> None:
        cands = list(universe_names) if universe_names else list(HIBETA_CANDIDATES)
        self._candidates = [s for s in dict.fromkeys(cands) if s != "QQQ"]

    def universe(self) -> list[str]:
        return list(dict.fromkeys(list(self._signal_syms) + self._candidates + EXEC_EXTRA))

    def _signal(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        raise NotImplementedError

    def decide(self, history: Mapping[str, Sequence[Candle]], state: dict[str, Any]) -> dict[str, float]:
        sig = self._signal(history, state)
        if not sig:
            return {}
        basket = (select_basket(history, self._candidates)
                  if any(s in RISK_ON_3X for s in sig) else {})
        return remap_signal(sig, basket, inverse_to=self._inverse_to)


class FtltHibeta(_HibetaSignal):
    name = "ftlt_hibeta"
    _signal_syms = FTLT_SYMS

    def _signal(self, history, state):
        return decide_ftlt(history)


class FtltHibetaPsq(FtltHibeta):
    """ftlt_hibeta 의 인버스 레그(SQQQ)를 PSQ(−1x QQQ)로. PSQ 규제 불확실 → 참고용."""
    name = "ftlt_hibeta_psq"
    _inverse_to = "PSQ"


class HolygrailHibeta(_HibetaSignal):
    name = "holygrail_hibeta"
    _signal_syms = HG_SYMS

    def _signal(self, history, state):
        return decide_holy_grail(history)


class SimpleHibeta(_HibetaSignal):
    name = "simple_hibeta"
    _signal_syms = ["TQQQ", "UVXY", "BIL"]

    def _signal(self, history, state):
        return decide_simple_rsi(history)


class BufferHibeta(_HibetaSignal):
    name = "buffer_hibeta"
    _signal_syms = ["QQQ", "TQQQ"]

    def _signal(self, history, state):
        return decide_buffer(history, state)      # 상태보유(진입/청산 버퍼)
