"""라이프사이클 정책(src/toss_trader/policy_lifecycle.py) 결정론 테스트.

검증: PV/노출 수식, 클리핑, 노출→비중 매핑, deposit_plan 매수전용·밴드 매도 트리거,
그리고 experiments/c5a_lifecycle.py 의 glide 목표노출과의 **수치 동치성**, run_dca
라이프사이클 dry-run(가짜 client, 주문 없음).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import policy_lifecycle as pl  # noqa: E402
import c5a_lifecycle as c5a  # noqa: E402


# ── PV 손검산 & 성질 ──────────────────────────────────────────────────────────
def test_pv_remaining_hand_checked():
    """PV(299개월, $35, 3% 실질) 손검산 ≈ $7396.6 (연구코드 PV_0 앵커와 동일)."""
    r_m = (1.03) ** (1.0 / 12.0) - 1.0
    expect = 35.0 * (1.0 - (1.0 + r_m) ** (-299)) / r_m
    got = pl.pv_remaining(35.0, 299)
    assert got == pytest.approx(expect, abs=1e-9)
    assert got == pytest.approx(7396.6, abs=1.0)


def test_pv_remaining_edges():
    assert pl.pv_remaining(35.0, 0) == 0.0
    assert pl.pv_remaining(35.0, -5) == 0.0
    # 할인율 0 → 단순 합
    assert pl.pv_remaining(35.0, 10, disc_real=0.0) == pytest.approx(350.0, abs=1e-9)


# ── 목표노출: 클리핑/글라이드 ────────────────────────────────────────────────
def test_lifecycle_target_clips_and_glides():
    # W<=0 → e_max (초기 최대 레버리지)
    assert pl.lifecycle_target(0.0, 35.0, 299) == 2.0
    assert pl.lifecycle_target(-10.0, 35.0, 299) == 2.0
    # 작은 W(적립 PV 대비) → 상한 e_max
    assert pl.lifecycle_target(1.0, 35.0, 299) == 2.0
    # 매우 큰 W → 하한 e_min (글라이드 종점)
    assert pl.lifecycle_target(5_000_000.0, 35.0, 299) == pytest.approx(1.0, abs=1e-9)
    # 커스텀 e_min/e_max 준수
    assert pl.lifecycle_target(0.0, 35.0, 299, e_max=1.5) == 1.5
    e = pl.lifecycle_target(200.0, 35.0, 120, e_min=1.0, e_max=2.0)
    assert 1.0 <= e <= 2.0


def test_lifecycle_target_monotone_in_W():
    """W 가 커질수록(적립 PV 고정) 목표노출은 단조감소(→ 글라이드)."""
    prev = 2.0 + 1e-9
    for W in (1.0, 50.0, 200.0, 1_000.0, 10_000.0, 1_000_000.0):
        e = pl.lifecycle_target(W, 35.0, 299)
        assert e <= prev + 1e-9
        prev = e


# ── 노출 → 비중 매핑 ─────────────────────────────────────────────────────────
def test_allocation_anchors_and_sum():
    assert pl.allocation_for_exposure(2.0) == pytest.approx({"QQQ": 0.0, "QLD": 1.0})
    assert pl.allocation_for_exposure(1.0) == pytest.approx({"QQQ": 1.0, "QLD": 0.0})
    assert pl.allocation_for_exposure(1.5) == pytest.approx({"QQQ": 0.5, "QLD": 0.5})
    for E in (1.0, 1.2, 1.37, 1.8, 2.0):
        a = pl.allocation_for_exposure(E)
        assert a["QQQ"] == pytest.approx(2.0 - E, abs=1e-12)
        assert a["QLD"] == pytest.approx(E - 1.0, abs=1e-12)
        assert a["QQQ"] + a["QLD"] == pytest.approx(1.0, abs=1e-12)
        assert a["QQQ"] >= -1e-12 and a["QLD"] >= -1e-12


def test_allocation_clips_out_of_range():
    assert pl.allocation_for_exposure(2.5) == pytest.approx({"QQQ": 0.0, "QLD": 1.0})
    assert pl.allocation_for_exposure(0.4) == pytest.approx({"QQQ": 1.0, "QLD": 0.0})


# ── deposit_plan: 매수전용 ───────────────────────────────────────────────────
def test_deposit_plan_buy_only_deploys_cash():
    # 목표비중과 정확히 일치하는 보유 → 밴드 미발동, 현금은 목표 유지하며 균등 매수
    plan = pl.deposit_plan({"QQQ": 50.0, "QLD": 50.0}, cash_usd=20.0, E_target=1.5)
    assert plan.sell_triggered is False
    assert not plan.sells
    assert sum(plan.buys.values()) == pytest.approx(20.0, abs=1e-9)
    assert plan.buys.get("QQQ", 0.0) == pytest.approx(10.0, abs=1e-9)
    assert plan.buys.get("QLD", 0.0) == pytest.approx(10.0, abs=1e-9)
    assert plan.e_actual == pytest.approx(1.5, abs=1e-9)


def test_deposit_plan_buy_only_prioritizes_underweight():
    # QQQ 미달이 큼 → 현금이 QQQ 로 먼저 간다(매수전용, 매도 없음)
    plan = pl.deposit_plan({"QQQ": 0.0, "QLD": 100.0}, cash_usd=40.0, E_target=1.3)
    # E_actual = 2.0, 그러나 band(0.3) 기본 → 1.3+0.3=1.6 < 2.0 이면 매도 발동됨.
    # 매도가 발동되지 않도록 넉넉한 밴드로 매수전용 경로만 검증한다.
    plan = pl.deposit_plan({"QQQ": 0.0, "QLD": 100.0}, cash_usd=40.0, E_target=1.3, band=1.0)
    assert plan.sell_triggered is False
    assert not plan.sells
    assert plan.buys.get("QQQ", 0.0) > plan.buys.get("QLD", 0.0)
    assert sum(plan.buys.values()) == pytest.approx(40.0, abs=1e-9)


def test_deposit_plan_no_cash_no_trades_when_within_band():
    plan = pl.deposit_plan({"QQQ": 45.0, "QLD": 55.0}, cash_usd=0.0, E_target=1.3, band=0.3)
    # E_actual = 1.55 < 1.6 → 발동 안 함, 현금 0 → 거래 없음
    assert plan.e_actual == pytest.approx(1.55, abs=1e-9)
    assert plan.sell_triggered is False
    assert not plan.buys and not plan.sells


# ── deposit_plan: 밴드 매도 트리거 ───────────────────────────────────────────
def test_deposit_plan_band_sell_trigger():
    # QLD 전량(E_actual=2.0) > E_target 1.3 + band 0.3 = 1.6 → 리밸런싱(매도+매수)
    plan = pl.deposit_plan({"QQQ": 0.0, "QLD": 100.0}, cash_usd=0.0, E_target=1.3, band=0.3)
    assert plan.e_actual == pytest.approx(2.0, abs=1e-9)
    assert plan.sell_triggered is True
    # 목표 E=1.3 → QQQ 0.7 / QLD 0.3. equity_ref=100 → QLD 70 매도, QQQ 70 매수.
    assert plan.sells.get("QLD", 0.0) == pytest.approx(70.0, abs=1e-9)
    assert plan.buys.get("QQQ", 0.0) == pytest.approx(70.0, abs=1e-9)
    assert "QLD" not in plan.buys and "QQQ" not in plan.sells


def test_deposit_plan_sell_uses_cash_and_proceeds():
    # 현금이 있으면 매도 대금 + 현금으로 미달분 매수(equity_ref = 보유+현금)
    plan = pl.deposit_plan({"QQQ": 0.0, "QLD": 100.0}, cash_usd=20.0, E_target=1.0, band=0.3)
    # E_target=1.0 → QQQ 1.0/QLD 0.0. equity_ref=120 → QLD 전량(100) 매도, QQQ 120 매수.
    assert plan.sell_triggered is True
    assert plan.sells.get("QLD", 0.0) == pytest.approx(100.0, abs=1e-9)
    assert plan.buys.get("QQQ", 0.0) == pytest.approx(120.0, abs=1e-9)


# ── 연구코드(glide)와 수치 동치성 ────────────────────────────────────────────
def test_lifecycle_target_equals_experiment_glide_on_grid():
    """lifecycle_target == c5a.target_exposure(mode='glide') on a grid.

    연구코드의 pv_remaining 내부 N = plan_months−1−month_m 이므로,
    month_m=0, plan_months = months_remaining+1 로 두면 두 함수가 같은 N 을 본다.
    """
    monthly = 35.0
    for months_remaining in (0, 1, 6, 12, 60, 120, 240, 299):
        P = months_remaining + 1
        for W in (0.0, 1.0, 25.0, 67.0, 500.0, 5_000.0, 100_000.0, 5_000_000.0):
            for s_star in (0.6, 0.8, 1.0):
                for disc in (0.02, 0.03, 0.04):
                    exp = c5a.target_exposure(
                        0, W, None, True, "glide",
                        s_star=s_star, plan_months=P, monthly=monthly, disc_real=disc)
                    got = pl.lifecycle_target(
                        W, monthly, months_remaining,
                        s_star=s_star, disc_real=disc)
                    assert got == pytest.approx(exp, abs=1e-12), (
                        months_remaining, W, s_star, disc, got, exp)


# ── run_dca 라이프사이클 dry-run (가짜 client, 주문 없음) ─────────────────────
class _FakeLifecycleSettings:
    client_id = "key"
    client_secret = "sec"
    account_seq = "1"
    trading_mode = "paper"
    policy = "lifecycle"
    lifecycle_sleeve = 0.3
    lifecycle_plan_years = 25
    lifecycle_emax = 2.0

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    def require_credentials(self) -> None:
        pass


class _FakeLifecycleClient:
    """run_dca 라이프사이클 dry-run 용. create_order 는 절대 호출되면 안 된다."""

    def __init__(self, settings):
        self.s = settings
        self.order_calls: list = []

    def get_accounts(self):
        return [{"accountSeq": "1"}]

    def get_buying_power(self, currency="USD"):
        # KRW 135,000 / FX 1350 = $100 가용
        return {"cashBuyingPower": "135000"} if currency == "KRW" else {"cashBuyingPower": "0"}

    def get_exchange_rate(self, base="USD", quote="KRW"):
        return {"rate": "1350"}

    def get_holdings(self, symbol=None):
        return {"items": [
            {"symbol": "QQQ", "quantity": "1", "lastPrice": "100", "marketCountry": "US"},
            {"symbol": "QLD", "quantity": "1", "lastPrice": "80", "marketCountry": "US"},
        ]}

    def create_order(self, *a, **k):  # 방어: dry-run 에서 호출되면 테스트 실패
        self.order_calls.append((a, k))
        raise AssertionError("dry-run 은 주문을 내면 안 된다")


def test_run_dca_lifecycle_dry_run_no_orders(monkeypatch, capsys):
    import run_dca

    settings = _FakeLifecycleSettings()
    fake = _FakeLifecycleClient(settings)
    monkeypatch.setattr(run_dca, "get_settings", lambda *a, **k: settings)
    monkeypatch.setattr(run_dca, "TossClient", lambda *a, **k: fake)
    # dry-run 은 상태파일에 lifecycle_start 를 쓰지 않지만, 안전하게 상태 쓰기도 차단.
    monkeypatch.setattr(run_dca, "_save_state", lambda *a, **k: None)

    rc = run_dca.lifecycle_plan(execute=False, auto=False, split=True)
    assert rc == 0
    assert fake.order_calls == []          # 주문 없음
    out = capsys.readouterr().out
    assert "라이프사이클" in out or "lifecycle" in out.lower()
