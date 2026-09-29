"""scripts/loop/run_agent_cycle.sh 단위테스트 — 실제 claude 호출 없음.

검증:
- --print-cmd 로 조립되는 claude 명령의 인자(모델·권한모드·출력포맷·허용도구·프롬프트),
  그리고 --dangerously-skip-permissions 가 절대 포함되지 않음.
- 락(동시 실행 방지): 살아있는 PID 의 락이 있으면 거부(exit 4).
- 워크트리 가드: 워크트리 경로가 메인 저장소와 같으면 거부(exit 3, ~/github/toss-trader 보호).

실행: .venv/bin/python -m pytest -q tests/test_agentloop_runner.py
      (PYTHONPATH 불필요 — 셸 스크립트를 subprocess 로만 돌린다.)
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "loop" / "run_agent_cycle.sh"


def _clean_env(**overrides: str) -> dict:
    """AGENTLOOP_*/LOOP_* 오염 제거 후 오버라이드만 주입한 환경."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AGENTLOOP_", "LOOP_")) and k != "CLAUDE_BIN"}
    env.update(overrides)
    return env


def _run(args, env=None, timeout=60):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


class PrintCmdTest(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(SCRIPT.exists(), f"스크립트 없음: {SCRIPT}")

    def test_builds_expected_command_with_reason(self):
        # CLAUDE_BIN 을 스텁으로 고정 → 출력 결정적, 머신 의존 제거.
        env = _clean_env(CLAUDE_BIN="/usr/bin/true")
        r = _run(["--print-cmd", "--reason", "foo"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        for token in [
            "-p", "/loop-cycle foo",
            "--model", "claude-opus-5-5",
            "--permission-mode acceptEdits",
            "--output-format json",
            "--allowedTools",
            "Read", "Write", "Edit", "Grep", "Glob", "Agent",
            "WebSearch", "WebFetch",
            "Bash(git *)", "Bash(gh *)", "Bash(python3 *)",
            "Bash(.venv/bin/python *)", "Bash(ls *)", "Bash(cat *)", "Bash(mkdir *)",
        ]:
            self.assertIn(token, out, f"명령에 '{token}' 가 없음:\n{out}")

    def test_never_skips_permissions(self):
        env = _clean_env(CLAUDE_BIN="/usr/bin/true")
        r = _run(["--print-cmd", "--reason", "x"], env=env)
        self.assertNotIn("--dangerously-skip-permissions", r.stdout)
        self.assertNotIn("bypassPermissions", r.stdout)

    def test_no_reason_prompt_is_bare(self):
        env = _clean_env(CLAUDE_BIN="/usr/bin/true")
        r = _run(["--print-cmd"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("/loop-cycle", r.stdout)
        self.assertNotIn("foo", r.stdout)

    def test_model_override(self):
        env = _clean_env(CLAUDE_BIN="/usr/bin/true", LOOP_MODEL="my-custom-model")
        r = _run(["--print-cmd"], env=env)
        self.assertIn("my-custom-model", r.stdout)
        self.assertNotIn("claude-opus-5-5", r.stdout)

    def test_venv_abs_path_uses_main(self):
        env = _clean_env(CLAUDE_BIN="/usr/bin/true", AGENTLOOP_MAIN="/tmp/tt-main")
        r = _run(["--print-cmd"], env=env)
        self.assertIn("Bash(/tmp/tt-main/.venv/bin/python *)", r.stdout)


class LockTest(unittest.TestCase):
    def test_refuses_when_live_lock_held(self):
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "main"; wt = Path(d) / "wt"
            main.mkdir(); wt.mkdir()
            lock = Path(d) / "agentloop.lock"
            lock.mkdir()
            (lock / "pid").write_text(str(os.getpid()))   # 살아있는 PID
            env = _clean_env(
                CLAUDE_BIN="/usr/bin/true",
                AGENTLOOP_MAIN=str(main),
                AGENTLOOP_WORKTREE=str(wt),
                AGENTLOOP_LOCK=str(lock),
            )
            r = _run(["--dry-run"], env=env)
            self.assertEqual(r.returncode, 4, f"stdout={r.stdout}\nstderr={r.stderr}")
            self.assertIn("실행 중", r.stderr)

    def test_reclaims_stale_lock_then_guards(self):
        # 죽은 PID 락은 회수되어야 하며(회수 메시지), 그 뒤 단계로 진행한다.
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "main"; wt = Path(d) / "wt"
            main.mkdir(); wt.mkdir()
            lock = Path(d) / "agentloop.lock"
            lock.mkdir()
            (lock / "pid").write_text("999999")           # 존재하지 않을 PID
            env = _clean_env(
                CLAUDE_BIN="/usr/bin/true",
                AGENTLOOP_MAIN=str(main),
                AGENTLOOP_WORKTREE=str(wt),
                AGENTLOOP_LOCK=str(lock),
            )
            r = _run(["--dry-run"], env=env, timeout=60)
            # 락은 회수(exit!=4). git fetch/worktree 는 실패할 수 있으나 락 거부는 아니어야 한다.
            self.assertNotEqual(r.returncode, 4, f"stderr={r.stderr}")
            self.assertIn("낡은 락 회수", r.stderr)


class WorktreeGuardTest(unittest.TestCase):
    def test_refuses_when_worktree_equals_main(self):
        with tempfile.TemporaryDirectory() as d:
            same = Path(d) / "repo"; same.mkdir()
            env = _clean_env(
                CLAUDE_BIN="/usr/bin/true",
                AGENTLOOP_MAIN=str(same),
                AGENTLOOP_WORKTREE=str(same),
                AGENTLOOP_LOCK=str(Path(d) / "lock"),
            )
            r = _run(["--dry-run"], env=env)
            self.assertEqual(r.returncode, 3, f"stdout={r.stdout}\nstderr={r.stderr}")
            self.assertIn("워크트리", r.stderr)

    def test_refuses_running_in_default_main_repo(self):
        # 메인 저장소 자체를 워크트리로 지목하면(=메인에서 돌리려는 시도) 거부.
        main = "/Users/mingh/github/toss-trader"
        if not Path(main).is_dir():
            self.skipTest("메인 저장소 경로 없음")
        with tempfile.TemporaryDirectory() as d:
            env = _clean_env(
                CLAUDE_BIN="/usr/bin/true",
                AGENTLOOP_MAIN=main,
                AGENTLOOP_WORKTREE=main,
                AGENTLOOP_LOCK=str(Path(d) / "lock"),
            )
            r = _run(["--dry-run"], env=env)
            self.assertEqual(r.returncode, 3, r.stderr)


if __name__ == "__main__":
    unittest.main()
