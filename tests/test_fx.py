"""FX(환전) 시간대 모델 + run_dca FX 프리플라이트 결정론적 테스트(네트워크 없음).

검증:
- FxWindow: 평일 09:00–15:30 KST 안/밖, 주말·공휴일 제외, tz 처리, next_open.
- FxCostModel: 시간대별 요율(5/50bps), 주간창 대비 초과 비용, 연간 환전비.
- run_dca.fx_window_preflight: USD 충분/부족 × 우대창 안/밖 × --require-usd 경고·보류 경로(fake client).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import fx                               # noqa: E402
import run_dca                                           # noqa: E402

KST = fx.KST


def _k(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=KST)


# ───────────────────────────── FxWindow ─────────────────────────────
def test_window_weekday_hours():
    m = fx.FxCostModel()
    w = m.window
    assert w.contains(_k(2026, 7, 1, 9, 0)) is True      # Wed 09:00 (경계 포함)
    assert w.contains(_k(2026, 7, 1, 10, 0)) is True
    assert w.contains(_k(2026, 7, 1, 8, 59)) is False     # 개장 전
    assert w.contains(_k(2026, 7, 1, 15, 30)) is False    # 마감 경계 배타
    assert w.contains(_k(2026, 7, 1, 16, 0)) is False     # 마감 후
    assert w.contains(_k(2026, 7, 1, 23, 0)) is False     # 야간(미 정규장 시간대)


def test_window_weekend_and_holiday():
    w = fx.FxWindow()
    assert w.contains(_k(2026, 7, 4, 10, 0)) is False     # 토요일
    assert w.contains(_k(2026, 7, 5, 10, 0)) is False     # 일요일
    assert w.contains(_k(2026, 1, 1, 10, 0)) is False     # 신정(공휴일)
    assert w.contains(_k(2026, 9, 25, 10, 0)) is False    # 추석(공휴일)
    assert w.is_business_day(_k(2026, 7, 1, 10).date()) is True
    assert w.is_business_day(_k(2026, 1, 1, 10).date()) is False


def test_window_timezone_handling():
    w = fx.FxWindow()
    # 01:00 UTC = 10:00 KST → 창 안
    assert w.contains(datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc)) is True
    # 14:00 UTC = 23:00 KST → 창 밖
    assert w.contains(datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)) is False
    # naive 는 KST 로 간주
    assert w.contains(datetime(2026, 7, 1, 10, 0)) is True


def test_next_open_skips_weekend_and_holiday():
    w = fx.FxWindow()
    # 토요일 정오 → 다음 월요일 09:00
    assert w.next_open(_k(2026, 7, 4, 12, 0)) == _k(2026, 7, 6, 9, 0)
    # 평일 창 안(10:00) → 다음 영업일 09:00(오늘 창은 이미 시작)
    assert w.next_open(_k(2026, 7, 1, 10, 0)) == _k(2026, 7, 2, 9, 0)
    # 평일 개장 전(08:00) → 오늘 09:00
    assert w.next_open(_k(2026, 7, 1, 8, 0)) == _k(2026, 7, 1, 9, 0)
    # 신정(목) 12:00 → 다음 영업일(금) 09:00 (1/1 공휴일 스킵)
    assert w.next_open(_k(2026, 1, 1, 12, 0)) == _k(2026, 1, 2, 9, 0)


# ─────────────────────────── FxCostModel ────────────────────────────
def test_fee_bps_and_costs():
    m = fx.FxCostModel()
    assert m.fee_bps(_k(2026, 7, 1, 10)) == 5.0          # 주간 우대
    assert m.fee_bps(_k(2026, 7, 1, 23)) == 50.0         # 야간
    # ₩50,000 환전: 야간은 주간 대비 45bps 초과 → ₩225
    assert m.extra_cost_krw(50_000, _k(2026, 7, 1, 23)) == 225.0
    assert m.extra_cost_krw(50_000, _k(2026, 7, 1, 10)) == 0.0
    assert m.fee_krw(50_000, _k(2026, 7, 1, 23)) == 250.0
    assert m.fee_krw(50_000, _k(2026, 7, 1, 10)) == 25.0


def test_annual_fx_cost():
    night = fx.annual_fx_cost(50_000, 50.0)
    win = fx.annual_fx_cost(50_000, 5.0)
    assert night.annual_krw == 600_000.0
    assert night.annual_fx_krw == 3_000.0
    assert win.annual_fx_krw == 300.0
    assert night.per_deposit_fx_krw == 250.0
    assert night.annual_fx_krw - win.annual_fx_krw == 2_700.0


def test_displayed_spread_bps():
    assert abs(fx.displayed_spread_bps("1541.6", "1541.1") - 3.244) < 0.01
    assert fx.displayed_spread_bps(None, "1500") is None
    assert fx.displayed_spread_bps("1500", "0") is None


# ─────────────────── run_dca.fx_window_preflight ────────────────────
class _FakeBP:
    """get_buying_power(USD) 만 구현하는 최소 fake client."""

    def __init__(self, usd, *, fail=False):
        self._usd = usd
        self._fail = fail
        self.calls: list[str] = []

    def get_buying_power(self, currency="USD"):
        self.calls.append(currency)
        if self._fail:
            raise RuntimeError("boom")
        return {"currency": currency, "cashBuyingPower": str(self._usd)}


def _capture():
    logs: list[str] = []
    return logs, logs.append


NIGHT = _k(2026, 7, 1, 23, 0)     # 미 정규장 시간대(우대창 밖)
DAY = _k(2026, 7, 1, 10, 0)       # 우대창 안
FX_RATE = 1500.0


def test_preflight_usd_sufficient_no_warning():
    logs, log = _capture()
    client = _FakeBP(usd=100.0)
    proceed, short = run_dca.fx_window_preflight(client, 30.0, FX_RATE, log,
                                                 require_usd=True, now=NIGHT)
    assert proceed is True and short == 0.0
    assert not any("FX 경고" in m for m in logs)
    assert any("자동환전 불필요" in m for m in logs)


def test_preflight_shortfall_out_of_window_warns_but_proceeds():
    logs, log = _capture()
    client = _FakeBP(usd=10.0)                            # 플랜 40 → 부족 30
    proceed, short = run_dca.fx_window_preflight(client, 40.0, FX_RATE, log,
                                                 require_usd=False, now=NIGHT)
    assert proceed is True                                # require_usd=False → 진행
    assert round(short, 2) == 30.0
    assert any("FX 경고" in m for m in logs)              # 야간 경고 기록
    # 초과분 ≈ ₩30 × 1500 × 45bps = ₩2,025 (문자열에 포함)
    assert any("주간창 대비 초과" in m for m in logs)


def test_preflight_shortfall_out_of_window_require_usd_holds():
    logs, log = _capture()
    client = _FakeBP(usd=0.0)
    proceed, short = run_dca.fx_window_preflight(client, 32.0, FX_RATE, log,
                                                 require_usd=True, now=NIGHT)
    assert proceed is False                               # 보류
    assert round(short, 2) == 32.0
    assert any("보류" in m for m in logs)


def test_preflight_shortfall_in_window_no_warning():
    logs, log = _capture()
    client = _FakeBP(usd=0.0)
    proceed, short = run_dca.fx_window_preflight(client, 32.0, FX_RATE, log,
                                                 require_usd=True, now=DAY)
    assert proceed is True                                # 우대창 안 → 진행
    assert round(short, 2) == 32.0
    assert not any("FX 경고" in m for m in logs)
    assert any("환전 우대창 내" in m for m in logs)


def test_preflight_query_failure_is_conservative_pass():
    logs, log = _capture()
    client = _FakeBP(usd=0.0, fail=True)
    proceed, short = run_dca.fx_window_preflight(client, 50.0, FX_RATE, log,
                                                 require_usd=True, now=NIGHT)
    assert proceed is True and short == 0.0               # 조회 실패 → 보수적 통과
    assert any("생략" in m for m in logs)
