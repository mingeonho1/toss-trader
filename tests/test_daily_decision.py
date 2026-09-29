"""데일리 결정 엔진 단위테스트(합성 장부; 네트워크·API 불필요).

검증: 워밍업 게이팅, 히스테리시스(플립플롭 억제 + 비킬은 즉시 강등 아님), 킬스위치 즉시 강등,
z-score 수기검산, 멱등성, 요청 방출, 추천은 실계좌 그룹에서만, 주문 API 미접촉.

실행: PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_daily_decision.py
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "loop"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import daily_decision as dd  # noqa: E402


# ── 헬퍼: 일간 로그수익 리스트 → (iso_date, equity) 곡선 ─────────────────────────
def curve_from_returns(rets, start=1000.0, d0=date(2026, 1, 1)):
    eq = [float(start)]
    for r in rets:
        eq.append(eq[-1] * math.exp(r))
    return [((d0 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(eq)]


EXPECT = {"exp_annual_log_excess_vs_qqq": 0.0, "tracking_vol_annual": 0.2, "backtest_mdd": -0.30}


class MetricsZScoreTest(unittest.TestCase):
    def test_z_score_hand_checked(self) -> None:
        # 2점(1스텝): 전략 +0.02, QQQ +0.01 → 누적 로그초과 = 0.01.
        strat = [("2026-01-02", 100.0), ("2026-01-03", 100.0 * math.exp(0.02))]
        qqq = {"2026-01-02": 100.0, "2026-01-03": 100.0 * math.exp(0.01)}
        expect = {"exp_annual_log_excess_vs_qqq": 0.0, "tracking_vol_annual": 0.1,
                  "backtest_mdd": -0.30}
        m = dd.compute_metrics(strat, qqq, {}, expect)
        self.assertAlmostEqual(m.excess_qqq, 0.01, places=9)
        # z = (0.01 - 0) / (0.1 * sqrt(2/252))
        denom = 0.1 * math.sqrt(2 / 252)
        self.assertAlmostEqual(m.z, 0.01 / denom, places=6)
        self.assertAlmostEqual(m.dd_ratio, 0.0, places=9)      # 단조증가 → mdd 0
        self.assertAlmostEqual(m.excess_tqqq, None if False else m.excess_tqqq)  # tqqq 없음 → None
        self.assertIsNone(m.excess_tqqq)

    def test_excess_and_vol_none_when_insufficient(self) -> None:
        m = dd.compute_metrics([("2026-01-02", 100.0)], {}, {}, EXPECT)
        self.assertEqual(m.n, 1)
        self.assertIsNone(m.vol)
        self.assertIsNone(m.z)


class WarmupGatingTest(unittest.TestCase):
    def test_warmup_below_21_then_evaluating(self) -> None:
        strat = curve_from_returns([0.001] * 70)
        qqq = dict(curve_from_returns([0.0] * 70))
        hist = dd.replay_strategy(strat, qqq, {}, EXPECT)
        self.assertEqual(hist[19].state, "WARMUP")             # n=20 < 21
        self.assertEqual(hist[20].state, "EVALUATING")         # n=21
        self.assertEqual(hist[61].state, "EVALUATING")         # n=62 < 63 (아직 자격 없음)


class HysteresisPromotionTest(unittest.TestCase):
    def test_candidate_needs_5_consecutive(self) -> None:
        # QQQ flat, 전략 +0.001/일 → n≥63부터 후보조건 항상 참(excess>0, z>-1, dd 0).
        strat = curve_from_returns([0.001] * 70)
        qqq = dict(curve_from_returns([0.0] * 70))
        hist = dd.replay_strategy(strat, qqq, {}, EXPECT)
        self.assertEqual(hist[62].state, "EVALUATING")         # n=63 조건 1회차
        self.assertEqual(hist[65].state, "EVALUATING")         # n=66 4회차(아직 미승격)
        self.assertEqual(hist[66].state, "CANDIDATE")          # n=67 5연속 → 승격
        self.assertEqual(hist[-1].state, "CANDIDATE")


class HysteresisDropoutTest(unittest.TestCase):
    def test_nonkill_failure_does_not_immediately_demote_and_drops_after_5(self) -> None:
        # n=67에서 CANDIDATE, 이후 전략+QQQ 동반 -33% 급락(dd_ratio 1.10, 킬 아님) → 조건 실패.
        crash = math.log(0.67)   # ≈ -0.4005 → peak 대비 -33%
        strat = curve_from_returns([0.002] * 66 + [crash] + [0.0] * 4)
        qqq = dict(curve_from_returns([0.001] * 66 + [crash] + [0.0] * 4))
        hist = dd.replay_strategy(strat, qqq, {}, EXPECT)
        self.assertEqual(hist[66].state, "CANDIDATE")          # n=67 승격
        # 급락 후: dd_ratio≈1.10(>1.0 조건 실패, <1.25 킬 아님). 4연속까진 CANDIDATE 유지.
        for i in (67, 68, 69, 70):
            self.assertEqual(hist[i].state, "CANDIDATE", f"idx {i} 비킬인데 조기 강등")
            self.assertNotIn("DEMOTED", hist[i].state)
        self.assertEqual(hist[71].state, "EVALUATING")         # 5연속 실패 → EVALUATING(강등, 킬 아님)
        # dd_ratio 확인(비킬 범위 1.0<r≤1.25).
        self.assertGreater(hist[71].m.dd_ratio, 1.0)
        self.assertLessEqual(hist[71].m.dd_ratio, dd.DD_KILL)


class KillSwitchTest(unittest.TestCase):
    def test_dd_kill_is_immediate_even_at_evaluating(self) -> None:
        # n=30(EVALUATING)에서 -50% 급락 → dd_ratio≈1.67 > 1.25 → 그 세션 즉시 DEMOTED.
        crash = math.log(0.5)    # -0.6931 → peak 대비 -50%
        strat = curve_from_returns([0.002] * 28 + [crash])
        qqq = dict(curve_from_returns([0.0] * 29))
        hist = dd.replay_strategy(strat, qqq, {}, EXPECT)
        self.assertEqual(hist[28].state, "EVALUATING")         # n=29 직전
        self.assertEqual(hist[29].state, "DEMOTED")            # n=30 즉시 강등(카운터 무시)
        self.assertGreater(hist[29].m.dd_ratio, dd.DD_KILL)

    def test_retire_after_21_sessions_demoted(self) -> None:
        # 급락 후 계속 신저점 갱신(매 세션 dd 킬 유지) → 21세션 후 RETIRED.
        rets = [0.002] * 25 + [math.log(0.5)] + [math.log(0.98)] * 30  # 이후 매일 소폭 신저점
        strat = curve_from_returns(rets)
        qqq = dict(curve_from_returns([0.0] * len(rets)))
        hist = dd.replay_strategy(strat, qqq, {}, EXPECT)
        states = [h.state for h in hist]
        self.assertIn("DEMOTED", states)
        self.assertEqual(hist[-1].state, "RETIRED")            # 충분히 오래 강등 → 은퇴


class RequestsEmissionTest(unittest.TestCase):
    def _rec(self, state, n=70, z=None, dd_ratio=None, excess=0.05, kinds=(), kills=()):
        m = dd.Metrics(n=n, cum=0.1, annual=0.2, excess_qqq=excess, excess_tqqq=None,
                       vol=0.3, mdd=-0.4, z=z, dd_ratio=dd_ratio)
        return dd.SessionRecord(date="2026-09-28", state=state, cand_consec=0,
                                kinds=list(kinds), kills=list(kills), m=m)

    def test_audit_investigate_and_regime(self) -> None:
        finals = {
            "ftlt_1x": self._rec("DEMOTED", dd_ratio=1.30, kinds=["dd"],
                                 kills=["DD ratio 1.30 > 1.25"]),
            "lrs200_qqq": self._rec("DEMOTED", n=50, z=-2.5, kinds=["z"],
                                    kills=["z -2.50 < -2.0 at n=50≥42"]),
            "qqq_bh": self._rec("EVALUATING"),
        }
        groups = {"ftlt_1x": "retail", "lrs200_qqq": "retail", "qqq_bh": "retail"}
        reqs = dd.build_requests(finals, groups, qqq_today_ret=-0.05, session_date="2026-09-28")
        types = [(r["type"], r.get("strategy")) for r in reqs]
        self.assertIn(("audit", "ftlt_1x"), types)
        self.assertIn(("investigate_divergence", "lrs200_qqq"), types)
        self.assertTrue(any(r["type"] == "regime_note" for r in reqs))
        self.assertTrue(all(r["session_date"] == "2026-09-28" for r in reqs))
        # 벤치(qqq_bh)는 요청 대상에서 제외.
        self.assertFalse(any(r.get("strategy") == "qqq_bh" for r in reqs))

    def test_scout_when_no_candidate_after_63(self) -> None:
        finals = {
            "ftlt_1x": self._rec("EVALUATING", n=70),
            "lrs200_qqq": self._rec("EVALUATING", n=65),
            "qqq_bh": self._rec("EVALUATING", n=70),  # 벤치는 카운트 제외
        }
        groups = {k: "retail" for k in finals}
        reqs = dd.build_requests(finals, groups, qqq_today_ret=0.0, session_date="2026-09-28")
        self.assertTrue(any(r["type"] == "scout" for r in reqs))

    def test_no_scout_when_a_candidate_exists(self) -> None:
        finals = {"ftlt_1x": self._rec("CANDIDATE", n=70), "lrs200_qqq": self._rec("EVALUATING", n=70)}
        groups = {k: "retail" for k in finals}
        reqs = dd.build_requests(finals, groups, qqq_today_ret=0.0, session_date="2026-09-28")
        self.assertFalse(any(r["type"] == "scout" for r in reqs))


class RecommendationGroupTest(unittest.TestCase):
    def _rec(self, excess, state="EVALUATING"):
        m = dd.Metrics(n=70, cum=0.1, annual=0.2, excess_qqq=excess, excess_tqqq=None,
                       vol=0.3, mdd=-0.4, z=0.0, dd_ratio=0.5)
        return dd.SessionRecord(date="2026-09-28", state=state, cand_consec=0, m=m)

    def test_pick_top_only_from_requested_group(self) -> None:
        finals = {
            "lev_best": self._rec(0.99, "CANDIDATE"),   # 레버, 훨씬 높은 초과
            "retail_a": self._rec(0.02),
            "retail_b": self._rec(0.05),
            "qqq_bh": self._rec(0.50),                  # 벤치(제외)
        }
        groups = {"lev_best": "leverage", "retail_a": "retail",
                  "retail_b": "retail", "qqq_bh": "retail"}
        top = dd.pick_top(finals, groups, "retail", {})
        self.assertEqual(top, "retail_b")               # 실계좌 중 excess 최고(벤치·레버 제외)
        self.assertNotEqual(top, "lev_best")
        self.assertNotEqual(top, "qqq_bh")


# ── 합성 장부 디렉터리 기반 통합/멱등/무-API ─────────────────────────────────────
def _write_book(base: Path, name: str, curve, real=None) -> None:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    eq = [[iso, v, (real if real is not None else v)] for iso, v in curve]
    d.joinpath("state.json").write_text(json.dumps({"name": name, "equity": eq}), encoding="utf-8")


class IntegrationIdempotencyTest(unittest.TestCase):
    def _books(self, base: Path) -> None:
        _write_book(base, "qqq_bh", curve_from_returns([0.0] * 5))
        _write_book(base, "tqqq_bh", curve_from_returns([0.0] * 5))
        _write_book(base, "ftlt_1x", curve_from_returns([0.001] * 5))
        _write_book(base, "ftlt", curve_from_returns([0.002] * 5))

    def test_run_is_idempotent_per_session_date(self) -> None:
        with tempfile.TemporaryDirectory() as dd_:
            base = Path(dd_) / "paperlab"
            out = Path(dd_) / "out"
            self._books(base)
            groups = {"qqq_bh": "retail", "tqqq_bh": "leverage",
                      "ftlt_1x": "retail", "ftlt": "leverage"}
            kw = dict(books_dir=base, with_weights=False, strats={}, groups=groups,
                      report_path=out / "r.md", state_path=out / "s.json",
                      decisions_path=out / "d.jsonl", requests_path=out / "q.jsonl")
            r1 = dd.run(**kw)
            r2 = dd.run(**kw)
            self.assertEqual(r1["session_date"], r2["session_date"])
            dec = [ln for ln in (out / "d.jsonl").read_text().splitlines() if ln.strip()]
            self.assertEqual(len(dec), 1)                       # 같은 세션 → 1줄(멱등)
            self.assertEqual(r1["report_text"].split("생성")[1:], r2["report_text"].split("생성")[1:])
            # WARMUP 이라 실행 가능 추천은 없음(부록 A); 관찰 선두는 retail 그룹에서.
            self.assertIsNone(r1["recommendation_retail"])
            self.assertEqual(r1["retail_observed_leader"], "ftlt_1x")
            self.assertEqual(groups[r1["retail_observed_leader"]], "retail")

    def test_recommendation_retail_is_never_leverage(self) -> None:
        with tempfile.TemporaryDirectory() as dd_:
            base = Path(dd_) / "paperlab"
            self._books(base)
            groups = {"qqq_bh": "retail", "tqqq_bh": "leverage",
                      "ftlt_1x": "retail", "ftlt": "leverage"}
            r = dd.run(books_dir=base, with_weights=False, strats={}, groups=groups,
                       report_path=Path(dd_) / "r.md", state_path=Path(dd_) / "s.json",
                       decisions_path=Path(dd_) / "d.jsonl", requests_path=Path(dd_) / "q.jsonl")
            self.assertEqual(groups[r["retail_observed_leader"]], "retail")   # 관찰 선두는 retail
            self.assertEqual(groups[r["recommendation_leverage"]], "leverage")


class ActionableRecommendationTest(unittest.TestCase):
    """부록 A: CANDIDATE/LIVE_READY 일 때만 실행 가능 추천. `*_moc`·탐색·벤치 제외."""

    def _srec(self, state, excess, n=70):
        m = dd.Metrics(n=n, cum=0.1, annual=0.2, excess_qqq=excess, excess_tqqq=None,
                       vol=0.3, mdd=-0.2, z=0.0, dd_ratio=0.5)
        return dd.SessionRecord(date="2026-09-28", state=state, cand_consec=0, m=m)

    def test_pick_top_excludes_moc_and_explore(self) -> None:
        finals = {
            "ftlt_moc": self._srec("CANDIDATE", 0.99),          # 최고지만 `_moc` → 제외
            "holy_grail": self._srec("CANDIDATE", 0.10),        # 레버
            "btc_proxy_mstr_coin": self._srec("CANDIDATE", 0.99),  # 탐색 → 제외
        }
        groups = {"ftlt_moc": "leverage", "holy_grail": "leverage",
                  "btc_proxy_mstr_coin": "explore"}
        self.assertEqual(dd.pick_top(finals, groups, "leverage", {}), "holy_grail")
        self.assertIsNone(dd.pick_top(finals, groups, "explore", {}))  # 탐색 그룹 자체 제외

    def test_warmup_has_no_actionable_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "paperlab"
            _write_book(base, "qqq_bh", curve_from_returns([0.0] * 5))
            _write_book(base, "ftlt_1x", curve_from_returns([0.001] * 5))     # retail, WARMUP
            _write_book(base, "ftlt_moc", curve_from_returns([0.002] * 5))    # leverage, `_moc`
            _write_book(base, "holy_grail", curve_from_returns([0.001] * 5))  # leverage
            groups = {"qqq_bh": "retail", "ftlt_1x": "retail",
                      "ftlt_moc": "leverage", "holy_grail": "leverage"}
            r = dd.run(books_dir=base, with_weights=False, strats={}, groups=groups,
                       report_path=Path(td) / "r.md", state_path=Path(td) / "s.json",
                       decisions_path=Path(td) / "d.jsonl", requests_path=Path(td) / "q.jsonl")
            self.assertIsNone(r["recommendation_retail"])                   # 실행 가능 추천 없음
            self.assertEqual(r["retail_observed_leader"], "ftlt_1x")       # 관찰 선두는 표기
            self.assertNotEqual(r["recommendation_leverage"], "ftlt_moc")  # `_moc` 제외
            self.assertEqual(r["recommendation_leverage"], "holy_grail")
            text = (Path(td) / "r.md").read_text(encoding="utf-8")
            self.assertIn("추천 없음 — 기본 DCA 유지", text)
            self.assertIn("관찰 선두(추천 아님)", text)
            self.assertNotIn("ftlt_moc", text.split("## 오늘 상태 변화")[0])  # 판단 섹션엔 `_moc` 없음

    def test_candidate_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "paperlab"
            _write_book(base, "qqq_bh", curve_from_returns([0.0] * 69))
            _write_book(base, "cand_strat", curve_from_returns([0.001] * 69))  # n=70, 후보조건 지속
            expect = Path(td) / "expect.json"
            expect.write_text(json.dumps({"strategies": {"cand_strat": {
                "exp_annual_log_excess_vs_qqq": 0.0, "tracking_vol_annual": 0.2,
                "backtest_mdd": -0.30}}}), encoding="utf-8")
            groups = {"qqq_bh": "retail", "cand_strat": "retail"}
            r = dd.run(books_dir=base, expect_path=expect, with_weights=False, strats={},
                       groups=groups, report_path=Path(td) / "r.md", state_path=Path(td) / "s.json",
                       decisions_path=Path(td) / "d.jsonl", requests_path=Path(td) / "q.jsonl")
            self.assertEqual(r["recommendation_retail"], "cand_strat")     # CANDIDATE → 실행 가능
            text = (Path(td) / "r.md").read_text(encoding="utf-8")
            self.assertIn("**실계좌 추천**: `cand_strat`", text)
            self.assertNotIn("추천 없음 — 기본 DCA 유지", text)


class NoOrderApiTest(unittest.TestCase):
    def test_module_has_no_order_calls(self) -> None:
        src = (ROOT / "scripts" / "loop" / "daily_decision.py").read_text(encoding="utf-8")
        for forbidden in ("create_order", "get_holdings", "get_buying_power", "execute_buys",
                          "execute_plan", "TradingMode", ".execute("):
            self.assertNotIn(forbidden, src, f"결정 엔진이 주문 경로({forbidden})를 참조하면 안 됨")

    def test_weights_path_never_constructs_toss_client(self) -> None:
        import run_strategy as rs
        calls = {"n": 0}

        class Bomb:
            def __init__(self, *a, **k):
                calls["n"] += 1
                raise AssertionError("주문 API(TossClient) 사용됨")

        class StubStrat:
            name = "stub"

            def universe(self):
                return ["QQQ"]

            def decide(self, history, state):
                return {"QQQ": 1.0}

        orig = rs.TossClient
        rs.TossClient = Bomb
        try:
            tw = dd.recommendation_weights("stub", StubStrat(), offline=True)
        finally:
            rs.TossClient = orig
        self.assertEqual(calls["n"], 0)                        # TossClient 한 번도 생성 안 함
        self.assertEqual(tw, {"QQQ": 1.0})                     # compute_target_weights(decide)만 사용


class LiveReadyLayerTest(unittest.TestCase):
    def test_group_top_candidate_becomes_live_ready_after_5(self) -> None:
        # 두 전략이 같은 6개 날짜에 CANDIDATE(≥21연속). A 가 매일 excess 1위 → 5일째 LIVE_READY.
        dates = [f"2026-09-{d:02d}" for d in range(1, 7)]

        def hist(exc_list):
            return [dd.SessionRecord(date=dates[i], state="CANDIDATE", cand_consec=25,
                                     m=dd.Metrics(70, 0.1, 0.2, exc_list[i], None, 0.3, -0.2,
                                                  0.0, 0.5)) for i in range(6)]
        histories = {"A": hist([0.10, 0.11, 0.12, 0.13, 0.14, 0.15]),
                     "B": hist([0.01, 0.02, 0.03, 0.04, 0.05, 0.06])}
        groups = {"A": "retail", "B": "retail"}
        dd.layer_live_ready(histories, groups)
        self.assertEqual(histories["A"][4].state, "LIVE_READY")   # 5번째 날 승격
        self.assertEqual(histories["A"][3].state, "CANDIDATE")    # 4번째 날은 아직
        self.assertTrue(all(r.state == "CANDIDATE" for r in histories["B"]))  # 2위는 승격 없음


if __name__ == "__main__":
    unittest.main()
