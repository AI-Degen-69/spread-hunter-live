"""Tests for the PreToolUse guard over git and GitHub commands.

The guard exists because the rules it enforces sit in a file AGENTS.md links to
rather than imports, and a partial read of that file misses them. It has to hold
when `gh` is missing, unauthenticated, or slow, so every helper degrades to
"allow" rather than blocking the repo.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "git_workflow_guard.py"


def _invoke(command: str, tool_name: str = "Bash") -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": tool_name, "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(GUARD)], input=payload, capture_output=True,
        text=True, timeout=60, check=False,
    )


@pytest.fixture
def guard(monkeypatch):
    """Import the guard module with its shell-outs stubbed."""
    sys.path.insert(0, str(GUARD.parent))
    import git_workflow_guard as mod
    yield mod
    sys.path.remove(str(GUARD.parent))


class TestRouting:
    def test_unrelated_command_is_ignored(self):
        res = _invoke("ls -la")
        assert res.returncode == 0
        assert res.stdout == ""

    def test_non_bash_tool_is_ignored(self):
        res = _invoke("git push", tool_name="Read")
        assert res.returncode == 0
        assert res.stdout == ""

    def test_malformed_payload_allows(self):
        res = subprocess.run(
            [sys.executable, str(GUARD)], input="not json",
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert res.returncode == 0

    def test_pr_create_surfaces_the_placeholder_rule(self):
        res = _invoke("gh pr create --title x")
        assert res.returncode == 0
        assert "@coderabbitai summary" in res.stdout


class TestPush:
    def test_push_without_an_open_pr_is_silent(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_pr_number_for_head", lambda: None)
        assert guard.check_push() == (0, "")

    def test_push_on_an_early_round_reminds_but_allows(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_pr_number_for_head", lambda: "15")
        monkeypatch.setattr(guard, "_review_count", lambda pr: 1)
        code, message = guard.check_push()
        assert code == 0
        assert "ONE commit and push ONCE" in message

    def test_push_at_the_third_review_blocks(self, guard, monkeypatch):
        """Three rounds is the runaway guard: a fourth review must not just happen."""
        monkeypatch.setattr(guard, "_pr_number_for_head", lambda: "15")
        monkeypatch.setattr(guard, "_review_count", lambda pr: 3)
        code, message = guard.check_push()
        assert code == 2
        assert "BLOCKED" in message
        assert "fourth" in message


class TestMerge:
    def test_merge_of_a_routine_pr_is_allowed(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_changed_paths",
                            lambda pr: ["tests/test_x.py", "README.md"])
        code, message = guard.check_merge("gh pr merge 15 --merge")
        assert code == 0
        assert "no sign-off paths" in message

    @pytest.mark.parametrize("path", [
        "core_brain/order_manager.py",
        "scoring/rank.py",
        "dashboard/server.py",
    ])
    def test_merge_touching_a_sign_off_path_blocks(self, guard, monkeypatch, path):
        monkeypatch.setattr(guard, "_changed_paths", lambda pr: ["tests/t.py", path])
        code, message = guard.check_merge("gh pr merge 15 --merge")
        assert code == 2
        assert "operator sign-off" in message
        assert path in message

    def test_dashboard_static_is_not_a_sign_off_path(self, guard, monkeypatch):
        """Only dashboard/server.py needs sign-off, not the whole dashboard."""
        monkeypatch.setattr(guard, "_changed_paths",
                            lambda pr: ["dashboard/static/app.js"])
        code, _ = guard.check_merge("gh pr merge 15 --merge")
        assert code == 0


class TestDegradesToAllow:
    def test_missing_gh_binary_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "")
        assert guard._pr_number_for_head() is None
        assert guard.check_push() == (0, "")

    def test_unparseable_gh_output_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "<html>login</html>")
        assert guard._pr_number_for_head() is None
        assert guard._review_count("15") == 0
