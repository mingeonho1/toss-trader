"""LLM 판단 레이어(scripts/loop/llm_judge.py) 단위테스트 — 가짜 codex 러너로 결정론.

검증: 유효 출력 채택 · 승격 시도 기각→재프롬프트→폴백 · 레버리지/탐색/_moc 추천 기각 ·
codex 불가→결정론 유지 · 요청 병합(멱등) · ET 날짜당 킥 1회 · 주문 API 미접촉.

실행: PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_llm_judge.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "loop"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import llm_judge as lj  # noqa: E402


# ── 픽스처 ──────────────────────────────────────────────────────────────────────
def strat(state: str, group: str, **m) -> dict:
    d = {"state": state, "group": group, "n": 70}
    d.update(m)
    return d


STRATS = {
    "mom_top5_ndx": strat("CANDIDATE", "retail", excess_qqq=0.05),
    "lrs200_qqq": strat("EVALUATING", "retail"),
    "holy_grail": strat("CANDIDATE", "leverage"),
    "ftlt_moc": strat("CANDIDATE", "leverage"),
    "btc_proxy_mstr_coin": strat("CANDIDATE", "explore"),
    "qqq_bh": strat("WARMUP", "retail"),
}
SESSION = "2026-09-28"


def valid_output(**over) -> dict:
    base = {"stance": "hold_dca", "recommended_strategy": None, "sleeve_frac": 0.0,
            "confidence": 0.5, "state_overrides": [], "rationale_ko": "근거",
            "what_changed_ko": "변화 없음", "watch_items": [], "requests": []}
    base.update(over)
    return base


class FakeCodex:
    """responses: 각 원소 {"raw": <obj|str>} (ok) 또는 {"error": "..."} (실패). 마지막을 반복."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        self.calls += 1
        r = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if r.get("error"):
            return {"status": "error", "raw": None, "error": r["error"], "returncode": 1}
        raw = r["raw"]
        if not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False)
        return {"status": "ok", "raw": raw, "error": None, "returncode": 0}


class CountingKicker:
    def __init__(self, status="kicked"):
        self.status = status
        self.calls = 0

    def __call__(self, label):
        self.calls += 1
        return {"status": self.status, "detail": label}


def write_decisions(path: Path, strategies: dict, session_date: str = SESSION) -> None:
    rec = {"session_date": session_date, "generated": "2026-09-29T02:00:00+00:00",
           "strategies": strategies, "changes": [],
           "recommendation": {"retail": None, "leverage": None}, "requests": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")


def run_judge(tmp: Path, fake, *, strategies=None, requests_pre=None, kicker=None, **kw):
    strategies = STRATS if strategies is None else strategies
    dpath = tmp / "decisions.jsonl"
    write_decisions(dpath, strategies)
    rpath = tmp / "requests.jsonl"
    if requests_pre is not None:
        rpath.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests_pre),
                         encoding="utf-8")
    kwargs = dict(
        decisions_path=dpath, requests_path=rpath, state_path=tmp / "state.json",
        expect_path=tmp / "nope.json", report_path=tmp / "decision_latest.md",
        judgments_path=tmp / "llm_judgments.jsonl", kick_state_path=tmp / "kick_state.json",
        intel_dir=tmp / "intel", codex_runner=fake,
        kicker=kicker or (lambda label: {"status": "not_loaded", "detail": "미로드"}),
    )
    kwargs.update(kw)
    return lj.run(**kwargs), rpath, kwargs


# ── 검증 함수 단위 ──────────────────────────────────────────────────────────────
class ValidateUnitTest(unittest.TestCase):
    def _bundle(self):
        return {"today": {"strategies": STRATS}}

    def test_valid_recommendation_passes(self):
        obj = valid_output(stance="recommend", recommended_strategy="mom_top5_ndx",
                           sleeve_frac=0.2, confidence=0.6)
        hard, _ = lj.validate(obj, self._bundle())
        self.assertEqual(hard, [])

    def test_leverage_recommendation_rejected(self):
        obj = valid_output(stance="recommend", recommended_strategy="holy_grail", sleeve_frac=0.1)
        hard, _ = lj.validate(obj, self._bundle())
        self.assertTrue(any("retail" in e for e in hard))

    def test_moc_recommendation_rejected(self):
        obj = valid_output(stance="recommend", recommended_strategy="ftlt_moc")
        hard, _ = lj.validate(obj, self._bundle())
        self.assertTrue(any("_moc" in e for e in hard))

    def test_explore_recommendation_rejected(self):
        obj = valid_output(stance="recommend", recommended_strategy="btc_proxy_mstr_coin")
        hard, _ = lj.validate(obj, self._bundle())
        self.assertTrue(hard)

    def test_non_actionable_state_rejected(self):
        # lrs200_qqq 는 retail 이지만 EVALUATING(추천 불가 상태).
        obj = valid_output(stance="recommend", recommended_strategy="lrs200_qqq")
        hard, _ = lj.validate(obj, self._bundle())
        self.assertTrue(any("CANDIDATE/LIVE_READY" in e for e in hard))

    def test_upgrade_override_rejected_downgrade_ok(self):
        up = valid_output(state_overrides=[{"strategy": "lrs200_qqq", "to": "CANDIDATE",
                                            "reason": "x"}])
        hard, _ = lj.validate(up, self._bundle())
        self.assertTrue(any("강등만" in e for e in hard))
        down = valid_output(state_overrides=[{"strategy": "lrs200_qqq", "to": "DEMOTED",
                                              "reason": "x"}])
        self.assertEqual(lj.validate(down, self._bundle())[0], [])

    def test_sleeve_and_confidence_bounds(self):
        self.assertTrue(lj.validate(valid_output(sleeve_frac=0.9), self._bundle())[0])
        self.assertTrue(lj.validate(valid_output(confidence=2.0), self._bundle())[0])

    def test_unknown_strategy_in_override_rejected(self):
        obj = valid_output(state_overrides=[{"strategy": "ghost", "to": "DEMOTED", "reason": "x"}])
        self.assertTrue(lj.validate(obj, self._bundle())[0])

    def test_soft_flag_for_unknown_token_in_rationale(self):
        obj = valid_output(rationale_ko="`ghost_strat` 이 좋아 보인다")
        hard, soft = lj.validate(obj, self._bundle())
        self.assertEqual(hard, [])
        self.assertTrue(any("ghost_strat" in s for s in soft))


# ── 통합: 유효 출력 채택 ──────────────────────────────────────────────────────────
class AcceptedTest(unittest.TestCase):
    def test_valid_output_accepted_and_merged(self):
        out = valid_output(
            stance="recommend", recommended_strategy="mom_top5_ndx", sleeve_frac=0.2,
            confidence=0.7,
            state_overrides=[{"strategy": "lrs200_qqq", "to": "DEMOTED", "reason": "괴리"}],
            requests=[{"type": "audit", "strategy": "mom_top5_ndx", "reason": "확인",
                       "priority": "medium"}],
            watch_items=["QQQ 레짐"])
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res, rpath, kw = run_judge(tmp, FakeCodex([{"raw": out}]))
            self.assertEqual(res["llm_status"], "accepted")
            self.assertEqual(res["recommended_strategy"], "mom_top5_ndx")
            self.assertEqual(res["n_downgrades"], 1)
            self.assertEqual(res["n_llm_requests"], 1)
            # requests.jsonl 에 source=llm 행이 추가됨.
            rows = [json.loads(x) for x in rpath.read_text().splitlines() if x.strip()]
            llm_rows = [r for r in rows if r.get("source") == "llm"]
            self.assertEqual(len(llm_rows), 1)
            self.assertEqual(llm_rows[0]["session_date"], SESSION)
            # 판정 기록 + 리포트 섹션.
            j = json.loads(Path(kw["judgments_path"]).read_text().splitlines()[-1])
            self.assertEqual(j["merged_states"]["lrs200_qqq"], "DEMOTED")   # 병합 뷰에 강등 반영
            self.assertEqual(j["downgrades"][0]["from"], "EVALUATING")
            report = Path(kw["report_path"]).read_text(encoding="utf-8")
            self.assertIn(lj.LLM_SECTION_HEADER, report)
            self.assertIn("채택", report)

    def test_reprompt_then_accept(self):
        bad = valid_output(state_overrides=[{"strategy": "lrs200_qqq", "to": "CANDIDATE",
                                             "reason": "승격시도"}])
        good = valid_output(stance="recommend", recommended_strategy="mom_top5_ndx",
                            sleeve_frac=0.1, confidence=0.5)
        with tempfile.TemporaryDirectory() as td:
            fake = FakeCodex([{"raw": bad}, {"raw": good}])
            res, _, _ = run_judge(Path(td), fake)
            self.assertEqual(res["llm_status"], "accepted")
            self.assertEqual(fake.calls, 2)
            self.assertIn("이전 응답이 다음 규칙을 위반했다", fake.prompts[1])


# ── 통합: 승격 시도 기각 → 재프롬프트 → 폴백 ─────────────────────────────────────
class RejectedFallbackTest(unittest.TestCase):
    def test_persistent_upgrade_attempt_falls_back_to_deterministic(self):
        bad = valid_output(state_overrides=[{"strategy": "lrs200_qqq", "to": "LIVE_READY",
                                             "reason": "승격"}])
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake = FakeCodex([{"raw": bad}])          # 매 시도 동일 위반.
            res, rpath, kw = run_judge(tmp, fake)
            self.assertEqual(res["llm_status"], "rejected")
            self.assertEqual(fake.calls, lj.MAX_ATTEMPTS)      # 최초 + 재프롬프트 소진.
            self.assertIn("이전 응답이 다음 규칙을 위반했다", fake.prompts[-1])
            # 결정론 유지: LLM 요청 미추가.
            rows = [json.loads(x) for x in rpath.read_text().splitlines() if x.strip()] \
                if rpath.exists() else []
            self.assertFalse(any(r.get("source") == "llm" for r in rows))
            j = json.loads(Path(kw["judgments_path"]).read_text().splitlines()[-1])
            self.assertTrue(j["validation_errors"])
            self.assertIsNone(j["validated"])
            self.assertIn("기각", Path(kw["report_path"]).read_text(encoding="utf-8"))

    def test_leverage_recommendation_rejected_end_to_end(self):
        for name in ("holy_grail", "ftlt_moc", "btc_proxy_mstr_coin"):
            bad = valid_output(stance="recommend", recommended_strategy=name, sleeve_frac=0.1)
            with tempfile.TemporaryDirectory() as td:
                res, _, _ = run_judge(Path(td), FakeCodex([{"raw": bad}]), max_attempts=1)
                self.assertEqual(res["llm_status"], "rejected", f"{name} 는 기각돼야 함")


# ── 통합: codex 불가 → 결정론 유지 ───────────────────────────────────────────────
class UnavailableTest(unittest.TestCase):
    def test_codex_error_is_unavailable_no_reprompt(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake = FakeCodex([{"error": "not logged in"}])
            res, rpath, kw = run_judge(tmp, fake)
            self.assertEqual(res["llm_status"], "unavailable")
            self.assertEqual(fake.calls, 1)                    # codex 실패는 재프롬프트 안 함.
            rows = [json.loads(x) for x in rpath.read_text().splitlines() if x.strip()] \
                if rpath.exists() else []
            self.assertFalse(any(r.get("source") == "llm" for r in rows))
            self.assertIn("불가", Path(kw["report_path"]).read_text(encoding="utf-8"))

    def test_offline_skips_codex(self):
        with tempfile.TemporaryDirectory() as td:
            fake = FakeCodex([{"raw": valid_output()}])
            res, _, _ = run_judge(Path(td), fake, offline=True)
            self.assertEqual(res["llm_status"], "unavailable")
            self.assertEqual(fake.calls, 0)                    # offline → codex 미호출.


# ── 통합: 요청 병합(규칙 + LLM, 멱등) ────────────────────────────────────────────
class RequestsMergeTest(unittest.TestCase):
    def test_rule_and_llm_requests_coexist_and_idempotent(self):
        rule_req = {"type": "scout", "reason": "규칙", "session_date": SESSION}
        out = valid_output(requests=[{"type": "audit", "strategy": "mom_top5_ndx",
                                      "reason": "llm", "priority": "low"}])
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = dict(
                decisions_path=tmp / "d.jsonl", requests_path=tmp / "q.jsonl",
                state_path=tmp / "s.json", expect_path=tmp / "n.json",
                report_path=tmp / "r.md", judgments_path=tmp / "j.jsonl",
                kick_state_path=tmp / "kick.json", intel_dir=tmp / "intel",
                kicker=lambda label: {"status": "not_loaded", "detail": "x"})
            write_decisions(paths["decisions_path"], STRATS)
            # 규칙(결정론) 요청을 미리 기록(source 없음).
            paths["requests_path"].write_text(json.dumps(rule_req, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
            lj.run(codex_runner=FakeCodex([{"raw": out}]), **paths)
            rows1 = [json.loads(x) for x in paths["requests_path"].read_text().splitlines()
                     if x.strip()]
            self.assertTrue(any(r.get("source") == "llm" for r in rows1))
            self.assertTrue(any(r.get("type") == "scout" and "source" not in r for r in rows1))
            # 같은 세션 재실행 → LLM 행 중복 없음(멱등). 규칙 행도 보존.
            lj.run(codex_runner=FakeCodex([{"raw": out}]), **paths)
            rows2 = [json.loads(x) for x in paths["requests_path"].read_text().splitlines()
                     if x.strip()]
            self.assertEqual(sum(1 for r in rows2 if r.get("source") == "llm"), 1)
            self.assertEqual(sum(1 for r in rows2 if r.get("type") == "scout"), 1)


# ── 통합: 킥은 ET 날짜당 1회 ─────────────────────────────────────────────────────
class KickTest(unittest.TestCase):
    def test_high_priority_kicks_once_per_day(self):
        out = valid_output(requests=[{"type": "audit", "strategy": "mom_top5_ndx",
                                      "reason": "긴급", "priority": "high"}])
        kicker = CountingKicker(status="kicked")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = dict(
                decisions_path=tmp / "d.jsonl", requests_path=tmp / "q.jsonl",
                state_path=tmp / "s.json", expect_path=tmp / "n.json",
                report_path=tmp / "r.md", judgments_path=tmp / "j.jsonl",
                kick_state_path=tmp / "kick.json", intel_dir=tmp / "intel")
            write_decisions(paths["decisions_path"], STRATS)
            res1 = lj.run(codex_runner=FakeCodex([{"raw": out}]), kicker=kicker, **paths)
            self.assertEqual(res1["kick"]["status"], "kicked")
            self.assertEqual(kicker.calls, 1)
            self.assertTrue(paths["kick_state_path"].exists())
            # 같은 세션 재실행 → 킥 재시도 안 함(already).
            res2 = lj.run(codex_runner=FakeCodex([{"raw": out}]), kicker=kicker, **paths)
            self.assertEqual(res2["kick"]["status"], "already")
            self.assertEqual(kicker.calls, 1)

    def test_no_high_priority_no_kick(self):
        out = valid_output(requests=[{"type": "audit", "strategy": "mom_top5_ndx",
                                      "reason": "낮음", "priority": "low"}])
        kicker = CountingKicker()
        with tempfile.TemporaryDirectory() as td:
            res, _, _ = run_judge(Path(td), FakeCodex([{"raw": out}]), kicker=kicker)
            self.assertFalse(res["kick"]["attempted"])
            self.assertEqual(kicker.calls, 0)

    def test_rule_audit_without_priority_triggers_kick(self):
        # 규칙(결정론) audit 요청은 priority 필드가 없어도 킥 유발(HIGH_PRIORITY_RULE_TYPES).
        rule_req = {"type": "audit", "strategy": "mom_top5_ndx", "reason": "규칙킬",
                    "session_date": SESSION}
        kicker = CountingKicker()
        with tempfile.TemporaryDirectory() as td:
            res, _, _ = run_judge(Path(td), FakeCodex([{"error": "no codex"}]),
                                  requests_pre=[rule_req], kicker=kicker)
            self.assertEqual(res["llm_status"], "unavailable")   # LLM 없어도
            self.assertTrue(res["kick"]["attempted"])            # 규칙 요청만으로 킥 시도
            self.assertEqual(kicker.calls, 1)


# ── 주문 API 미접촉(F12 와 별개의 소스 스캔) ─────────────────────────────────────
class NoOrderApiTest(unittest.TestCase):
    def test_module_has_no_order_or_bypass_tokens(self):
        src = (ROOT / "scripts" / "loop" / "llm_judge.py").read_text(encoding="utf-8")
        for forbidden in ("create_order", "get_holdings", "get_buying_power", "execute_buys",
                          "execute_plan", "place_order", "submit_order", ".execute(",
                          "--execute", "TRADING_MODE", "live_exec",
                          "danger-full-access", "workspace-write", "dangerously-bypass"):
            self.assertNotIn(forbidden, src, f"LLM 판단 모듈이 금지 토큰({forbidden})을 참조하면 안 됨")

    def test_codex_call_is_read_only_sandbox(self):
        src = (ROOT / "scripts" / "loop" / "llm_judge.py").read_text(encoding="utf-8")
        self.assertIn("--sandbox", src)
        self.assertIn("read-only", src)


if __name__ == "__main__":
    unittest.main()
