"""Composer 계열(FTLT·Holy Grail·Simple RSI) 공용 헬퍼 — experiments/c12_composer.py 규칙블록의
포워드 페이퍼 포팅.

원본은 정렬 패널 위에서 전 구간 W[t] 를 한 번에 만든다. 여기서는 엔진이 매일 '오늘까지의' 가시
히스토리를 넘겨주므로, 각 지표를 **종가 리스트에 대해 인과적 research.rsi/sma 로 계산해 [-1](=오늘)**
만 쓴다. research 헬퍼가 인덱스 t 출력이 입력 ≤ t 만 참조하도록 보장하므로
``rsi(closes_≤t)[-1] == rsi(full)[t]`` 이며, 동일 픽스처에서 원본 build_* 와 목표비중이 일치한다
(tests/test_paperlab_composer.py 가 이 등가성을 검증). 파라미터는 동결(재튜닝 없음).

1x 섀도(레버리지 ETP 규제 회피): 결정된 목표를 :data:`SHADOW_1X` 로 매핑(레버리지→1x, 변동성/현금성
→현금). experiments SHADOW 와 동일 규약.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..models import Candle
from ..research import rsi, sma

# experiments/c12_composer.py SHADOW 와 동일(레버리지→1x, 변동성/현금/채권→현금(None)).
SHADOW_1X: dict[str, str | None] = {
    "TQQQ": "QQQ", "SPXL": "SPY", "UPRO": "SPY", "SOXL": "SMH", "TECL": "QQQ",
    "FAS": "SPY", "HIBL": "SPY",
    "SQQQ": "PSQ", "SOXS": "PSQ", "SPXU": "SH", "TECS": "PSQ",
    "QQQ": "QQQ", "SPY": "SPY", "SMH": "SMH", "PSQ": "PSQ", "SH": "SH",
    "UVXY": None, "VIXY": None, "VIXM": None, "SVIX": None,
    "BIL": None, "BSV": None, "SHY": None, "SHV": None, "BOXX": None,
    "IEF": None, "TMF": None, "UUP": None,
}


def _closes(history: Mapping[str, Sequence[Candle]], sym: str) -> list[float]:
    return [c.close for c in history.get(sym, [])]


def ready(*vals) -> bool:
    return all(v is not None for v in vals)


def px_now(history: Mapping[str, Sequence[Candle]], sym: str) -> float | None:
    c = _closes(history, sym)
    return c[-1] if c else None


def rsi_now(history: Mapping[str, Sequence[Candle]], sym: str, n: int) -> float | None:
    """Wilder RSI(n) 의 오늘값. 데이터 < n+1 이면 None(research.rsi 규약)."""
    c = _closes(history, sym)
    if len(c) <= n:
        return None
    return rsi(c, n)[-1]


def sma_now(history: Mapping[str, Sequence[Candle]], sym: str, n: int) -> float | None:
    """SMA(n) 의 오늘값. 데이터 < n 이면 None."""
    c = _closes(history, sym)
    if len(c) < n:
        return None
    return sma(c, n)[-1]


def pick1_by_rsi(history: Mapping[str, Sequence[Candle]], syms: Sequence[str], n: int,
                 *, bottom: bool) -> dict[str, float] | None:
    """experiments _pick(k=1): RSI(n) 최저(bottom)/최고(top) 1종목 100%. 하나라도 RSI 미준비면 None."""
    vals: list[tuple[float, str]] = []
    for s in syms:
        r = rsi_now(history, s, n)
        if r is None:
            return None
        vals.append((r, s))
    vals.sort(key=lambda x: x[0])
    chosen = vals[0][1] if bottom else vals[-1][1]
    return {chosen: 1.0}


def to_shadow_1x(target: Mapping[str, float]) -> dict[str, float]:
    """목표비중을 1x 섀도로 매핑(레버리지→1x, 변동성/현금성→현금). 미등록 심볼은 그대로 둔다."""
    out: dict[str, float] = {}
    for sym, w in target.items():
        tgt = SHADOW_1X.get(sym, sym)
        if tgt is None:
            continue                          # 현금
        out[tgt] = out.get(tgt, 0.0) + float(w)
    return out
