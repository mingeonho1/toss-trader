"""c16b(Faber GTAA) 테스트 — 순수 stdlib, 네트워크 불요(합성 패널).

- lookahead_guard: sig_gtaa 무누출(+ 엔진 누출 검출은 test_c16a).
- 규칙: TR>SMA 이면 20% 보유, 아니면 그 몫 BIL, 비중합 1, 월말에만 갱신, 워밍업 전 현금.
- 집합 불변이면 무매매(드리프트), 집합 변경 시 전체 리밸런스.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "tests"))

import c16_alloc_engine as E  # noqa: E402
import c16b_gtaa as T  # noqa: E402
from test_c16a import _bdays, _panel, _path  # noqa: E402
from toss_trader import research as R  # noqa: E402


def test_lookahead_guard_gtaa_clean():
    dates, panel = _panel(900)
    assert R.lookahead_guard(lambda p, d: T.sig_gtaa(p, d, sma_win=210), panel, dates)


def test_gtaa_rules():
    n = 400
    dates = _bdays(n)
    up, down = _path(n, 0.001), _path(n, -0.001)
    panel = {"SPY": up, "EFA": down, "IEF": up, "GLD": down, "VNQ": down, "BIL": _path(n, 0.0001)}
    w = T.sig_gtaa(panel, dates, sma_win=210)
    assert all(x == {} for x in w[:209])
    me = R.month_end_flags(dates)
    t = next(i for i in range(215, n) if me[i])
    assert w[t] == {"SPY": 0.2, "IEF": 0.2, "BIL": pytest.approx(0.6)}
    assert sum(w[t].values()) == pytest.approx(1.0)


def test_gtaa_no_trade_when_set_unchanged():
    dates, panel = _panel(700)
    panel = {k: panel[k] for k in T.NAMES}
    for a in T.ASSETS:                                  # 모두 상승추세 → 상시 보유, 집합 불변
        panel[a] = _path(700, 0.0008)
    w = T.sig_gtaa(panel, dates, sma_win=210)
    s0 = 300
    sim = E.simulate(panel, dates, w, s0=s0)
    assert sim.n_switch == 0
    assert sim.applied[-1] == {a: 0.2 for a in T.ASSETS}


def test_gtaa_set_change_triggers_full_rebalance():
    dates, panel = _panel(700)
    panel = {k: panel[k] for k in T.NAMES}
    tg = [{a: 0.2 for a in T.ASSETS}] * 400 + [{"SPY": 0.2, "EFA": 0.2, "IEF": 0.2, "GLD": 0.2, "BIL": 0.2}] * 300
    sim = E.simulate(panel, dates, tg, s0=300)
    assert sim.n_switch == 1 and sim.switch_dates[0] == dates[401]      # t+1 체결
