"""연구 루프 검증 하네스(scripts/harness/checks.py) 테스트.

각 검사가 **과거에 실제로 난 실패**를 tmp 픽스처로 잡는지, 그리고 **현재 레포**가
(block 없이) 통과하는지 확인한다. stdlib + pytest 만 사용.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "harness"
for _p in (str(HARNESS), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import checks  # noqa: E402  (scripts/harness/checks.py)


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────
def ctx(root: Path, scope=None) -> checks.Context:
    return checks.Context(root=Path(root), scope=scope)


def codes(findings, level=None):
    return [f.code for f in findings if level is None or f.level == level]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True,
                   capture_output=True, text=True)


def make_git_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── 현재 레포: block 없음(warn 만 허용) ───────────────────────────────────────
def test_current_repo_has_no_block_findings():
    findings = checks.run_all(ctx(REPO, scope=None))
    blocks = [f for f in findings if f.level == "block"]
    assert blocks == [], "현재 레포에 block 수준 findings 가 있으면 안 됨: " + \
        "; ".join(f"{f.code} {f.path}: {f.message}" for f in blocks)


def test_verify_all_cli_exit_zero():
    r = subprocess.run([sys.executable, str(HARNESS / "verify.py"), "--all", "--json"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["block"] == 0


# ── F1: 수수료 가정 오류(25bp 주 비용) ────────────────────────────────────────
def test_f1_blocks_25bp_primary_without_10bp(tmp_path):
    write(tmp_path, "experiments/c99_bad.py",
          "BASE = CostSpec(commission_bps=25.0, slippage_bps=5.0)\n")
    f = checks.check_f1_fees(ctx(tmp_path))
    assert "F1" in codes(f, "block")


def test_f1_passes_with_10bp_primary(tmp_path):
    write(tmp_path, "experiments/c99_ok.py",
          "BASE = CostSpec(commission_bps=10.0, slippage_bps=5.0)\n")
    assert checks.check_f1_fees(ctx(tmp_path)) == []


def test_f1_allows_labeled_25bp_stress(tmp_path):
    write(tmp_path, "experiments/c99_stress.py",
          'cs = CostSpec(commission_bps=10.0)\n'
          'specs = {"std_25bp": cost_for(25.0), "stress_2x": cost_for(25.0).stress(2.0)}\n')
    assert codes(checks.check_f1_fees(ctx(tmp_path)), "block") == []


# ── F2: 결과 파일 경로 충돌 ───────────────────────────────────────────────────
def test_f2_blocks_duplicate_results_path(tmp_path):
    for name in ("c12_a.py", "c12_b.py"):
        write(tmp_path, f"experiments/{name}",
              'RESULTS_JSON = ROOT / "reports" / "c12_results.json"\n')
    f = checks.check_f2_result_paths(ctx(tmp_path))
    assert "F2" in codes(f, "block")


def test_f2_passes_unique_paths(tmp_path):
    write(tmp_path, "experiments/c12_a.py",
          'RESULTS_JSON = ROOT / "reports" / "c12_a_results.json"\n')
    write(tmp_path, "experiments/c12_b.py",
          'RESULTS_JSON = ROOT / "reports" / "c12_b_results.json"\n')
    assert checks.check_f2_result_paths(ctx(tmp_path)) == []


# ── F3: 사전등록 순서 ────────────────────────────────────────────────────────
def test_f3_blocks_prereg_after_results(tmp_path):
    write(tmp_path, "reports/cycle99_c99_x.md",
          "# c99\n<!-- RESULTS_BELOW -->\n| 판정 | PASS |\n## 사전등록\n규칙...\n")
    f = checks.check_f3_prereg(ctx(tmp_path))
    assert "F3" in codes(f, "block")


def test_f3_blocks_missing_prereg(tmp_path):
    write(tmp_path, "reports/cycle99_c99_x.md",
          "# c99\n## 결과\n| 판정 | PASS |\n")
    f = checks.check_f3_prereg(ctx(tmp_path))
    assert "F3" in codes(f, "block")


def test_f3_passes_prereg_before_results(tmp_path):
    write(tmp_path, "reports/cycle99_c99_x.md",
          "# c99\n## 사전등록 (결과 보기 전)\n규칙...\n<!-- RESULTS_BELOW -->\n| 판정 | PASS |\n")
    assert codes(checks.check_f3_prereg(ctx(tmp_path)), "block") == []


# ── F4: peek-once · append-only ──────────────────────────────────────────────
def test_f4_blocks_double_holdout(tmp_path):
    rows = [
        {"idea_id": "x1", "period": "holdout", "peeked_holdout": True},
        {"idea_id": "x1", "period": "holdout", "peeked_holdout": True},
    ]
    write(tmp_path, checks.LEDGER, "\n".join(json.dumps(r) for r in rows) + "\n")
    f = checks.check_f4_holdout_append(ctx(tmp_path))
    assert "F4" in codes(f, "block")


def test_f4_passes_single_holdout(tmp_path):
    rows = [
        {"idea_id": "x1", "period": "design", "peeked_holdout": False},
        {"idea_id": "x1", "period": "holdout", "peeked_holdout": True},
    ]
    write(tmp_path, checks.LEDGER, "\n".join(json.dumps(r) for r in rows) + "\n")
    assert codes(checks.check_f4_holdout_append(ctx(tmp_path)), "block") == []


def test_f4_blocks_ledger_rewrite(tmp_path):
    make_git_repo(tmp_path)
    write(tmp_path, checks.LEDGER,
          json.dumps({"idea_id": "a", "period": "design"}) + "\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    # 기존 행을 되쓴다(append-only 파괴).
    write(tmp_path, checks.LEDGER,
          json.dumps({"idea_id": "TAMPERED", "period": "design"}) + "\n")
    f = checks.check_f4_holdout_append(ctx(tmp_path))
    assert any(x.code == "F4" and "append-only" in x.message for x in f)


def test_f4_passes_ledger_append(tmp_path):
    make_git_repo(tmp_path)
    write(tmp_path, checks.LEDGER,
          json.dumps({"idea_id": "a", "period": "design"}) + "\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    with (tmp_path / checks.LEDGER).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"idea_id": "b", "period": "design"}) + "\n")
    assert codes(checks.check_f4_holdout_append(ctx(tmp_path)), "block") == []


# ── F5: 룩어헤드 가드 테스트 ──────────────────────────────────────────────────
def test_f5_blocks_signal_without_guard_test(tmp_path):
    write(tmp_path, "experiments/c99_sig.py",
          "def sig_momentum(panel, dates):\n    return []\n")
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    f = checks.check_f5_lookahead(ctx(tmp_path))
    assert "F5" in codes(f, "block")


def test_f5_passes_with_guard_test(tmp_path):
    write(tmp_path, "experiments/c99_sig.py",
          "def sig_momentum(panel, dates):\n    return []\n")
    write(tmp_path, "tests/test_c99.py",
          "def test_guard():\n    assert research.lookahead_guard(fn, c, d)\n")
    assert checks.check_f5_lookahead(ctx(tmp_path)) == []


def test_f5_ignores_private_weight_helper(tmp_path):
    # c15b 의 _target_weights 같은 private 배분기는 신호함수가 아니다 → 오탐 금지.
    write(tmp_path, "experiments/c99_ens.py",
          "def _target_weights(members, R, k):\n    return []\n")
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    assert checks.check_f5_lookahead(ctx(tmp_path)) == []


# ── F6: 생존편향 ─────────────────────────────────────────────────────────────
def test_f6_warns_single_stock_without_label(tmp_path):
    write(tmp_path, "experiments/c99_stk.py",
          'UNIV = ["AAPL", "MSFT", "NVDA", "AMZN"]\n'
          "def decide_momentum(panel, dates):\n    return []\n")
    f = checks.check_f6_survivorship(ctx(tmp_path))
    assert "F6" in codes(f, "warn")


def test_f6_passes_with_upper_bound_label(tmp_path):
    write(tmp_path, "experiments/c99_stk.py",
          'UNIV = ["AAPL", "MSFT", "NVDA", "AMZN"]  # 결과는 UPPER BOUND(생존편향)\n'
          "def decide_momentum(panel, dates):\n    return []\n")
    assert checks.check_f6_survivorship(ctx(tmp_path)) == []


# ── F7: 신호 재사용 반오염 표기 ───────────────────────────────────────────────
def test_f7_warns_reuse_without_marker(tmp_path):
    rows = [{"idea_id": "c99_rsi2_fee10", "period": "design", "params": {}}]
    write(tmp_path, checks.LEDGER, "\n".join(json.dumps(r) for r in rows) + "\n")
    f = checks.check_f7_signal_reuse(ctx(tmp_path))
    assert "F7" in codes(f, "warn")


def test_f7_passes_when_marked(tmp_path):
    rows = [{"idea_id": "c99_rsi2_fee10", "period": "design",
             "params": {"semi_contaminated": True}}]
    write(tmp_path, checks.LEDGER, "\n".join(json.dumps(r) for r in rows) + "\n")
    assert checks.check_f7_signal_reuse(ctx(tmp_path)) == []


# ── F8: 규제 매매가능성(레버리지 ETP ↔ 실계좌) ────────────────────────────────
def _fake_strategy(name, group, uni, decide_out):
    from toss_trader.paperlab import Strategy

    class _S(Strategy):
        pass
    s = _S()
    s.name = name
    s.group = group
    s.universe = lambda: list(uni)
    s.decide = lambda history, state: dict(decide_out)
    return s


def test_f8_blocks_leverage_held_in_retail(monkeypatch):
    import toss_trader.paperlab_strategies as ps
    fake = _fake_strategy("bad", "retail", ["QQQ", "TQQQ"], {"TQQQ": 1.0})
    monkeypatch.setattr(ps, "build_roster", lambda **k: [fake])
    f = checks.check_f8_tradability(ctx(REPO, scope=None))
    assert any(x.code == "F8" and x.level == "block" for x in f)


def test_f8_passes_signal_only_leverage(monkeypatch):
    # 신호에만 레버리지, 보유는 1x/주식(c13a 패턴) → block 금지.
    import toss_trader.paperlab_strategies as ps
    fake = _fake_strategy("ok", "retail", ["QQQ", "TQQQ", "NVDA"], {"NVDA": 1.0})
    monkeypatch.setattr(ps, "build_roster", lambda **k: [fake])
    f = checks.check_f8_tradability(ctx(REPO, scope=None))
    assert not any(x.code == "F8" and x.level == "block" for x in f)


# ── F9: 비밀정보 ─────────────────────────────────────────────────────────────
def test_f9_blocks_secret_in_publish_allowlist(tmp_path):
    write(tmp_path, "scripts/publish_records.py",
          'ALLOW = [\n    (".env", "x"),\n    ("reports/ok.md", "latest"),\n]\n')
    f = checks.check_f9_secrets(ctx(tmp_path))
    assert "F9" in codes(f, "block")


def test_f9_blocks_tracked_env(tmp_path):
    make_git_repo(tmp_path)
    write(tmp_path, ".env", "TOSS_CLIENT_SECRET=xxx\n")
    _git(tmp_path, "add", "-f", ".env")
    _git(tmp_path, "commit", "-qm", "oops")
    f = checks.check_f9_secrets(ctx(tmp_path))
    assert any(x.code == "F9" and ".env" in x.path for x in f)


def test_f9_allows_env_example(tmp_path):
    make_git_repo(tmp_path)
    write(tmp_path, ".env.example", "TOSS_CLIENT_SECRET=\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "ok")
    assert codes(checks.check_f9_secrets(ctx(tmp_path)), "block") == []


# ── F10: 실주문 안전 ─────────────────────────────────────────────────────────
def _write_plist(root: Path, rel: str, args, env=None):
    import plistlib
    data = {"Label": "x", "ProgramArguments": args,
            "EnvironmentVariables": env or {}}
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        plistlib.dump(data, fh)


def test_f10_blocks_execute_in_plist(tmp_path):
    _write_plist(tmp_path, "automation/x.plist",
                 ["/usr/bin/python3", "run_dca.py", "--auto", "--execute"])
    f = checks.check_f10_real_money(ctx(tmp_path))
    assert any(x.code == "F10" and "--execute" in x.message for x in f)


def test_f10_blocks_live_env_in_plist(tmp_path):
    _write_plist(tmp_path, "automation/x.plist",
                 ["/usr/bin/python3", "run_dca.py", "--auto"],
                 env={"TRADING_MODE": "live"})
    f = checks.check_f10_real_money(ctx(tmp_path))
    assert any(x.code == "F10" and "TRADING_MODE" in x.message for x in f)


def test_f10_passes_clean_plist(tmp_path):
    _write_plist(tmp_path, "automation/x.plist",
                 ["/usr/bin/python3", "run_dca.py", "--auto"])
    assert codes(checks.check_f10_real_money(ctx(tmp_path)), "block") == []


def test_f10_blocks_execute_default_true(tmp_path):
    write(tmp_path, "scripts/run_dca.py",
          'ap.add_argument("--execute", default=True)\n'
          "if args.execute or client.s.is_live:\n    pass\n")
    f = checks.check_f10_real_money(ctx(tmp_path))
    assert any(x.code == "F10" and "dry-run" in x.message for x in f)


def test_f10_passes_store_true_default(tmp_path):
    write(tmp_path, "scripts/run_dca.py",
          'ap.add_argument("--execute", action="store_true")\n'
          "if not client.s.is_live:\n    pass\n")
    assert codes(checks.check_f10_real_money(ctx(tmp_path)), "block") == []


# ── F11: 레버리지/타이밍 주장 감사 ────────────────────────────────────────────
def test_f11_warns_synth_leverage_without_audit(tmp_path):
    write(tmp_path, "reports/cycle99_c99_x.md",
          "## 결과\n합성 3x 로 CAGR 50%, 판정 **PASS**.\n")
    f = checks.check_f11_audit(ctx(tmp_path))
    assert "F11" in codes(f, "warn")


def test_f11_passes_when_audit_mentioned(tmp_path):
    write(tmp_path, "reports/cycle99_c99_x.md",
          "## 결과\n합성 3x, 판정 **PASS**. 배당 이중계상·종료일 절단 감사 완료(코호트 ATH).\n")
    assert checks.check_f11_audit(ctx(tmp_path)) == []


# ── F12: LLM 판단 레이어 안전(주문/실행 경로 · read-only 샌드박스) ─────────────
_F12_CLEAN = (
    "import subprocess\n"
    'argv = [codex, "exec", "-m", "gpt-6-astra", "--sandbox", "read-only",\n'
    '        "--skip-git-repo-check", "--ephemeral", prompt]\n'
    "subprocess.run(argv, capture_output=True)\n"
)


def test_f12_blocks_order_tokens(tmp_path):
    write(tmp_path, "scripts/loop/llm_judge.py",
          _F12_CLEAN + "client.create_order(sym, qty)\n")
    f = checks.check_f12_llm_judge_safety(ctx(tmp_path))
    assert any(x.code == "F12" and "주문/실행" in x.message for x in f)


def test_f12_blocks_dangerous_sandbox(tmp_path):
    write(tmp_path, "scripts/loop/llm_judge.py",
          'argv = ["codex", "exec", "--sandbox", "workspace-write", prompt]\n')
    f = checks.check_f12_llm_judge_safety(ctx(tmp_path))
    assert any(x.code == "F12" and "위험 샌드박스" in x.message for x in f)


def test_f12_blocks_missing_read_only(tmp_path):
    write(tmp_path, "scripts/loop/llm_judge.py",
          'argv = ["codex", "exec", "-m", "gpt-6-astra", prompt]\n')
    f = checks.check_f12_llm_judge_safety(ctx(tmp_path))
    assert any(x.code == "F12" and "read-only" in x.message for x in f)


def test_f12_passes_clean_llm_judge(tmp_path):
    write(tmp_path, "scripts/loop/llm_judge.py", _F12_CLEAN)
    assert checks.check_f12_llm_judge_safety(ctx(tmp_path)) == []


def test_f12_skips_when_out_of_scope(tmp_path):
    write(tmp_path, "scripts/loop/llm_judge.py", _F12_CLEAN + "create_order()\n")
    # 스코프에 없으면 검사 생략(변경 파일만 검사하는 --changed 모드 대응).
    assert checks.check_f12_llm_judge_safety(ctx(tmp_path, scope=set())) == []


# ── F13: 스테일 장부(결정엔진/리더보드가 낡은 페이퍼 랩으로 계산) ─────────────────
def _decision_state(root: Path, session_date: str, generated: str) -> None:
    write(root, "data/loop/decision_state.json",
          json.dumps({"session_date": session_date, "generated": generated,
                      "books_dir": str(root / "data" / "paperlab")}))


def _paperlab_state(root: Path, name: str, last_date: str) -> None:
    write(root, f"data/paperlab/{name}/state.json", json.dumps({"last_date": last_date}))


def test_f13_blocks_stale_session_vs_generated(tmp_path):
    # 실제 사고: 생성 09-30 인데 결정/장부가 09-28 에 고정(최신 완료 세션 09-29) → 스테일.
    _decision_state(tmp_path, "2026-09-28", "2026-09-30T00:44:22+00:00")
    _paperlab_state(tmp_path, "qqq_bh", "2026-09-28")
    f = checks.check_f13_stale_books(ctx(tmp_path))
    assert "F13" in codes(f, "block")
    assert any("2026-09-29" in x.message for x in f)          # 최신 완료 세션 명시


def test_f13_blocks_session_behind_books(tmp_path):
    # 장부는 09-28 까지 전진했는데 결정 세션은 09-25 → 낡은 장부로 판정(생성은 주말이라 캘린더는 무해).
    _decision_state(tmp_path, "2026-09-25", "2026-09-26T20:00:00+00:00")  # 토(ET) → 최신완료 09-25
    _paperlab_state(tmp_path, "qqq_bh", "2026-09-28")
    f = checks.check_f13_stale_books(ctx(tmp_path))
    assert "F13" in codes(f, "block")
    assert any("페이퍼 랩 최신 처리일" in x.message for x in f)


def test_f13_passes_when_fresh(tmp_path):
    # 결정 세션 = 장부 = 최신 완료 세션(09-29) → block 없음.
    _decision_state(tmp_path, "2026-09-29", "2026-09-30T00:44:22+00:00")
    _paperlab_state(tmp_path, "qqq_bh", "2026-09-29")
    _paperlab_state(tmp_path, "mom_top5_ndx", "2026-09-29")
    assert checks.check_f13_stale_books(ctx(tmp_path)) == []


def test_f13_skips_when_no_decision_state(tmp_path):
    # 결정 산출물이 없으면(신규 클론/CI) 검사할 데이터 없음 → 생략.
    _paperlab_state(tmp_path, "qqq_bh", "2026-09-28")
    assert checks.check_f13_stale_books(ctx(tmp_path)) == []


def test_f13_skips_when_out_of_scope(tmp_path):
    _decision_state(tmp_path, "2026-09-28", "2026-09-30T00:44:22+00:00")
    _paperlab_state(tmp_path, "qqq_bh", "2026-09-28")
    # 관련 산출물/코드가 스코프에 없으면 생략(--changed 모드 대응).
    assert checks.check_f13_stale_books(ctx(tmp_path, scope=set())) == []
