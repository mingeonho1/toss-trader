"""daily_scoreboard 오프라인 단위테스트.

핵심 요구: (1) 단계 격리 — 한 단계가 실패해도 리포트는 생성된다,
(2) 멱등성 — 같은 날 두 번 실행해도 이력이 중복되지 않는다,
(3) 픽스처로부터의 대시보드 렌더링. 네트워크·실스크립트·자격증명 불필요.

실행: PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_scoreboard.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import daily_scoreboard as sb  # noqa: E402


def _ok(name: str, msg: str = "") -> sb.Step:
    code = f"print({msg!r})" if msg else "pass"
    return sb.Step(name, [sys.executable, "-c", code], timeout=30.0)


def _fail(name: str) -> sb.Step:
    return sb.Step(name, [sys.executable, "-c", "import sys; sys.exit(3)"], timeout=30.0)


class ExecuteStepIsolationTest(unittest.TestCase):
    def test_failing_step_does_not_raise_and_others_run(self) -> None:
        results = sb.execute_steps([
            _ok("a", "hello"),
            _fail("b"),
            sb.Step("c", skip_reason="테스트 스킵"),
        ])
        by = {r.name: r for r in results}
        self.assertEqual(by["a"].status, "ok")
        self.assertEqual(by["b"].status, "failed")           # 실패해도 예외 없이 기록
        self.assertEqual(by["c"].status, "skipped")
        self.assertIn("rc=3", by["b"].detail)

    def test_report_is_rendered_even_when_a_step_failed(self) -> None:
        results = sb.execute_steps([_ok("a"), _fail("b")])
        report = sb.render_dashboard({
            "generated": "2026-09-28T06:30:00+09:00", "mode": "offline(캐시)",
            "et": "2026-09-27T16:30-04:00", "steps": results, "books": [],
            "intraday": sb.intraday_rule_stats([]), "dca_plan": [], "tax": [],
            "changelog": "cl",
        })
        self.assertIn("# 매일 자동 스코어보드", report)
        self.assertIn("| a |", report)
        self.assertIn("| b |", report)                        # 실패 단계도 표에 남는다
        self.assertIn("❌ failed", report)


class TimeoutIsolationTest(unittest.TestCase):
    def test_timeout_is_captured_as_failed(self) -> None:
        step = sb.Step("slow", [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
        r = sb.execute_step(step)
        self.assertEqual(r.status, "failed")
        self.assertIn("타임아웃", r.detail)


class HistoryIdempotencyTest(unittest.TestCase):
    def test_upsert_same_date_replaces_not_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.jsonl"
            sb.upsert_history({"date": "2026-09-27", "changelog": "run1"}, path)
            sb.upsert_history({"date": "2026-09-27", "changelog": "run2"}, path)
            rows = sb.read_history(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["changelog"], "run2")    # 최신으로 교체
            sb.upsert_history({"date": "2026-09-28", "changelog": "run3"}, path)
            rows = sb.read_history(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual([r["date"] for r in rows], ["2026-09-27", "2026-09-28"])


class RunScoreboardIntegrationTest(unittest.TestCase):
    def _fixtures(self, d: Path) -> dict:
        fp = {"seed_usd": 100.0,
              "strategies": {"lumpsum_etf": {"total_contributed": 100.0}},
              "snapshots": [
                  {"session_date": "2026-09-25", "strategies": {"lumpsum_etf": {"equity": 100.0}}},
                  {"session_date": "2026-09-26", "strategies": {"lumpsum_etf": {"equity": 90.0}}},
              ]}
        lc = {"seed_usd": 100.0, "snapshots": [
            {"date": "2026-09-26", "portfolios": {
                "lifecycle_sleeve": {"equity": 105.0, "contributed": 100.0, "E": 2.0}}}]}
        trades = [{"date": "2026-09-26", "rule": "orb5_core",
                   "sizes": {"30": {"net": 0.5, "net_bps": 12.0}}}]
        fp_path = d / "fp.json"; fp_path.write_text(json.dumps(fp))
        lc_path = d / "lc.json"; lc_path.write_text(json.dumps(lc))
        tr_path = d / "trades.jsonl"
        tr_path.write_text("".join(json.dumps(t) + "\n" for t in trades))
        return {"fp": fp_path, "lc": lc_path, "tr": tr_path}

    def test_report_and_history_written_with_a_failing_step(self) -> None:
        with tempfile.TemporaryDirectory() as dd:
            d = Path(dd)
            fx = self._fixtures(d)
            now = datetime(2026, 9, 28, 3, 30, tzinfo=timezone.utc)  # ET 2026-09-27 23:30
            res = sb.run_scoreboard(
                now_utc=now, creds=True,                     # creds=True → 캐시 시딩 스킵(격리)
                steps=[_ok("dca_plan", "적립 매수 플랜: QQQ=$5.00"), _fail("forward_paper")],
                report_path=d / "report.md", history_path=d / "hist.jsonl",
                fp_state=fx["fp"], lc_state=fx["lc"], trades_path=fx["tr"])
            self.assertEqual(res["failed"], 1)
            self.assertTrue((d / "report.md").exists())
            text = (d / "report.md").read_text(encoding="utf-8")
            self.assertIn("B0 Lump-sum ETF", text)           # 장부는 상태파일에서(단계 실패 무관)
            self.assertIn("Lifecycle sleeve", text)
            self.assertIn("적립 매수 플랜", text)             # dca 단계 stdout 반영
            # 이력 멱등: 같은 now 로 재실행해도 1줄
            sb.run_scoreboard(now_utc=now, creds=True, steps=[_ok("x")],
                              report_path=d / "report.md", history_path=d / "hist.jsonl",
                              fp_state=fx["fp"], lc_state=fx["lc"], trades_path=fx["tr"])
            hist = sb.read_history(d / "hist.jsonl")
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[0]["date"], "2026-09-27")


class ForwardBookExtractionTest(unittest.TestCase):
    def test_forward_paper_books_equity_and_drawdown(self) -> None:
        state = {"seed_usd": 100.0,
                 "strategies": {"lumpsum_etf": {"total_contributed": 100.0}},
                 "snapshots": [
                     {"strategies": {"lumpsum_etf": {"equity": 100.0}}},
                     {"strategies": {"lumpsum_etf": {"equity": 120.0}}},
                     {"strategies": {"lumpsum_etf": {"equity": 90.0}}},
                 ]}
        books = sb.forward_paper_books(state, source="cached")
        self.assertEqual(len(books), 1)
        b = books[0]
        self.assertEqual(b["id"], "lumpsum_etf")
        self.assertAlmostEqual(b["equity"], 90.0)
        self.assertAlmostEqual(b["money_return"], -0.10, places=6)      # 90/100-1
        self.assertAlmostEqual(b["max_dd"], 90.0 / 120.0 - 1.0, places=6)  # peak 120 → 90
        self.assertEqual(b["n"], 3)

    def test_forward_lifecycle_books(self) -> None:
        state = {"snapshots": [
            {"portfolios": {"lifecycle_sleeve": {"equity": 99.0, "contributed": 100.0},
                            "dca_qqq": {"equity": 101.0, "contributed": 100.0}}}]}
        books = {b["id"]: b for b in sb.forward_lifecycle_books(state)}
        self.assertAlmostEqual(books["lifecycle_sleeve"]["equity"], 99.0)
        self.assertAlmostEqual(books["dca_qqq"]["money_return"], 0.01, places=6)

    def test_equity_drawdown_math(self) -> None:
        latest, peak, mdd = sb.equity_drawdown([100.0, 110.0, 90.0, 95.0])
        self.assertAlmostEqual(latest, 95.0)
        self.assertAlmostEqual(peak, 110.0)
        self.assertAlmostEqual(mdd, 90.0 / 110.0 - 1.0, places=6)
        self.assertEqual(sb.equity_drawdown([]), (0.0, 0.0, 0.0))


class IntradayRuleStatsTest(unittest.TestCase):
    def test_empty_trades_all_collecting(self) -> None:
        stats = sb.intraday_rule_stats([])
        self.assertEqual(stats["total"], 0)
        self.assertEqual(len(stats["rules"]), len(sb.RULE_ORDER))
        self.assertTrue(all(r["status"].startswith("수집 중") for r in stats["rules"]))

    def test_stats_numeric_and_status(self) -> None:
        # orb5_core: 250건, net 대체로 양(+), 분산 작음 → t≥3 → 후보.
        recs = []
        for i in range(250):
            net = 1.0 + (0.01 if i % 2 else -0.01)
            recs.append({"date": "2026-09-26", "rule": "orb5_core",
                         "sizes": {"30": {"net": net, "net_bps": 10.0}}})
        recs.append({"date": "2026-09-26", "rule": "orb15_core",
                     "sizes": {"30": {"net": -0.2, "net_bps": -5.0}}})
        stats = sb.intraday_rule_stats(recs)
        by = {r["label"]: r for r in stats["rules"]}
        orb5 = by[sb.RULE_LABEL["orb5_core"]]
        self.assertEqual(orb5["n"], 250)
        self.assertAlmostEqual(orb5["mean_bps"], 10.0, places=6)
        self.assertGreater(orb5["tstat"], 3.0)
        self.assertEqual(orb5["status"], "후보 (t≥3)")
        self.assertEqual(by[sb.RULE_LABEL["orb15_core"]]["n"], 1)       # n<200
        self.assertTrue(by[sb.RULE_LABEL["orb15_core"]]["status"].startswith("수집 중"))
        self.assertEqual(stats["size"], "30")


class SeedCandleCacheTest(unittest.TestCase):
    def test_seed_is_idempotent_and_converts_layout(self) -> None:
        with tempfile.TemporaryDirectory() as dd:
            d = Path(dd)
            hist = d / "hist"; hist.mkdir()
            cand = d / "cand"
            (hist / "QQQ.json").write_text(json.dumps(
                {"symbol": "QQQ", "rows": [{"d": "2026-09-25", "o": 1, "h": 2,
                                            "l": 0.5, "c": 1.5, "v": 10, "a": 1.5}]}))
            seeded = sb.seed_candle_cache(["QQQ", "MISSING"], depth=320,
                                          hist_dir=hist, candle_dir=cand)
            self.assertEqual(seeded, ["QQQ"])                # MISSING 은 스킵
            out = json.loads((cand / "QQQ_320.json").read_text())
            self.assertEqual(out[0], {"d": "2026-09-25", "o": 1, "h": 2, "l": 0.5,
                                      "c": 1.5, "v": 10})    # 'a' 제거된 candle 레이아웃
            again = sb.seed_candle_cache(["QQQ"], depth=320, hist_dir=hist, candle_dir=cand)
            self.assertEqual(again, [])                      # 이미 있으면 재시딩 안 함


class GatingAndCredsTest(unittest.TestCase):
    def test_collector_skip_reason(self) -> None:
        et_fri_close = datetime(2026, 9, 25, 16, 30, tzinfo=timezone.utc)   # 금 16:30
        self.assertIsNone(sb.collector_skip_reason(False, et_fri_close))
        self.assertIn("offline", sb.collector_skip_reason(True, et_fri_close) or "")
        et_sun = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)          # 일
        self.assertIn("주말", sb.collector_skip_reason(False, et_sun) or "")
        et_early = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)        # 금 10:00
        self.assertIn("16:05", sb.collector_skip_reason(False, et_early) or "")

    def test_has_credentials_from_env_dict(self) -> None:
        self.assertTrue(sb.has_credentials({"TOSS_CLIENT_ID": "x", "TOSS_CLIENT_SECRET": "y"}))
        self.assertTrue(sb.has_credentials({"API_KEY": "x", "SECRET_KEY": "y"}))
        self.assertFalse(sb.has_credentials({"TOSS_CLIENT_ID": "x"}))


class StdoutParsingTest(unittest.TestCase):
    def test_parse_dca_plan_strips_timestamp(self) -> None:
        out = ("2026-09-28T00:00:00+00:00 계좌 123 | 매수가능 ₩10,000\n"
               "2026-09-28T00:00:00+00:00 적립 매수 플랜 (목표배분 ...): QQQ=$5.00\n"
               "  ↳ 분할매수(≤$10 무료 활용): QQQ×2\n")
        lines = sb.parse_dca_plan(out)
        self.assertTrue(any("적립 매수 플랜" in ln for ln in lines))
        self.assertTrue(any("분할매수" in ln for ln in lines))
        self.assertFalse(any(ln.startswith("2026-09-28T00") for ln in lines))  # 타임스탬프 제거

    def test_parse_tax(self) -> None:
        out = "═══ 리포트 ═══\n실현손익(YTD, 통산) : ₩1,000 → 예상세액 ₩0\n남은 기본공제 : ₩2,490,000\n"
        lines = sb.parse_tax(out)
        self.assertTrue(any("실현손익" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
