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


def _invoke(payload: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(payload),
        capture_output=True, text=True, timeout=60, check=False,
    )


def _bash(command: str, tool_name: str = "Bash") -> subprocess.CompletedProcess:
    return _invoke({"tool_name": tool_name, "tool_input": {"command": command}})


@pytest.fixture
def guard():
    """Import the guard module so its shell-outs can be stubbed."""
    sys.path.insert(0, str(GUARD.parent))
    import git_workflow_guard as mod
    yield mod
    sys.path.remove(str(GUARD.parent))


class TestClassify:
    """Routing has to survive global options and compound command lines.

    `git -C /repo push` and `gh --repo owner/name pr merge 15` are ordinary
    forms, and a guard that ignores them is a guard anyone steps around by
    accident.
    """

    @pytest.mark.parametrize("command", [
        "git push",
        "git push -u origin main",
        "git -C /repo push",
        "git -c user.name=x push",
        "git --git-dir=/repo/.git push",
        "cd /repo && git push",
        "git status; git push",
    ])
    def test_push_forms_are_recognised(self, guard, command):
        assert guard.classify(command)[0] == "push"

    @pytest.mark.parametrize("command,expected_pr", [
        ("gh pr merge 15 --merge", "15"),
        ("gh pr merge --merge", None),
        ("gh --repo owner/name pr merge 15", "15"),
        ("gh -R owner/name pr merge 16 --squash", "16"),
        ("cd /repo && gh pr merge 17", "17"),
    ])
    def test_merge_forms_are_recognised(self, guard, command, expected_pr):
        assert guard.classify(command) == ("merge", expected_pr)

    def test_merge_wins_over_push_on_one_line(self, guard):
        """Merge is the irreversible end of the list, so it takes precedence."""
        assert guard.classify("git push && gh pr merge 15")[0] == "merge"

    @pytest.mark.parametrize("command", [
        "ls -la",
        "git log --grep=push",
        "git status",
        "echo gh pr merge",
        "",
    ])
    def test_unrelated_commands_route_nowhere(self, guard, command):
        assert guard.classify(command)[0] == ""


class TestPayloadShapes:
    """Valid JSON of the wrong shape must allow, not crash with exit 1."""

    @pytest.mark.parametrize("payload", [
        [],
        [1, 2, 3],
        "a string",
        42,
        None,
        {"tool_name": "Bash", "tool_input": []},
        {"tool_name": "Bash", "tool_input": None},
        {"tool_name": "Bash", "tool_input": {"command": 5}},
        {"tool_name": "Bash", "tool_input": {"command": "   "}},
        {"tool_name": "Bash"},
        {},
    ])
    def test_unexpected_shapes_allow(self, payload):
        res = _invoke(payload)
        assert res.returncode == 0, res.stderr
        assert res.stdout == ""

    def test_malformed_json_allows(self):
        res = subprocess.run(
            [sys.executable, str(GUARD)], input="not json",
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert res.returncode == 0

    def test_non_bash_tool_is_ignored(self):
        res = _bash("git push", tool_name="Read")
        assert res.returncode == 0
        assert res.stdout == ""

    def test_unrelated_command_is_ignored(self):
        res = _bash("ls -la")
        assert res.returncode == 0
        assert res.stdout == ""

    def test_pr_create_surfaces_the_placeholder_rule(self):
        res = _bash("gh pr create --title x")
        assert res.returncode == 0
        assert "@coderabbitai summary" in res.stdout


class TestPush:
    def test_push_without_an_open_pr_is_silent(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: None)
        assert guard.check_push() == (0, "")

    def test_push_on_an_early_round_reminds_but_allows(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "15")
        monkeypatch.setattr(guard, "_reviews", lambda pr: ["CHANGES_REQUESTED"])
        code, message = guard.check_push()
        assert code == 0
        assert "ONE commit and push ONCE" in message

    def test_push_at_the_third_review_blocks(self, guard, monkeypatch):
        """Three rounds is the runaway guard: a fourth must not just happen."""
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "15")
        monkeypatch.setattr(guard, "_reviews",
                            lambda pr: ["CHANGES_REQUESTED"] * 3)
        code, message = guard.check_push()
        assert code == 2
        assert "BLOCKED" in message
        assert "fourth" in message

    def test_an_approval_is_not_a_runaway_round(self, guard, monkeypatch):
        """Counting an approval toward the threshold blocks the wrong push.

        It stops the commit that lands the fixes the review asked for, which
        is the opposite of what the stop condition exists to prevent.
        """
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_reviews", lambda pr: [
            "CHANGES_REQUESTED", "CHANGES_REQUESTED", "APPROVED",
        ])
        assert guard.check_push()[0] == 0

    def test_an_approval_followed_by_more_findings_still_blocks(self, guard,
                                                                monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_reviews", lambda pr: [
            "APPROVED", "CHANGES_REQUESTED", "CHANGES_REQUESTED",
        ])
        assert guard.check_push()[0] == 2

    def test_unreadable_review_states_do_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_run", lambda args: "<html>login</html>")
        assert guard._reviews("16") == []
        assert guard.check_push()[0] == 0


class TestMerge:
    def test_merge_of_a_routine_pr_is_allowed(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_changed_paths",
                            lambda pr: ["tests/test_x.py", "README.md"])
        code, message = guard.check_merge("15")
        assert code == 0
        assert "no sign-off paths" in message

    @pytest.mark.parametrize("path", [
        "core_brain/order_manager.py",
        "scoring/rank.py",
        "dashboard/server.py",
    ])
    def test_merge_touching_a_sign_off_path_blocks(self, guard, monkeypatch, path):
        monkeypatch.setattr(guard, "_changed_paths", lambda pr: ["tests/t.py", path])
        monkeypatch.setattr(guard, "_head_sha", lambda pr: "abc1234")
        monkeypatch.setattr(guard, "load_approvals", dict)
        code, message = guard.check_merge("15")
        assert code == 2
        assert "operator sign-off" in message
        assert path in message
        assert "--approve 15" in message

    def test_dashboard_static_is_not_a_sign_off_path(self, guard, monkeypatch):
        """Only dashboard/server.py needs sign-off, not the whole dashboard."""
        monkeypatch.setattr(guard, "_changed_paths",
                            lambda pr: ["dashboard/static/app.js"])
        assert guard.check_merge("15")[0] == 0


class TestApproval:
    """A block the operator cannot clear is not sign-off, it is a wall."""

    def test_recorded_approval_unblocks_the_same_commit(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_changed_paths",
                            lambda pr: ["core_brain/venue.py"])
        monkeypatch.setattr(guard, "_head_sha", lambda pr: "abc1234")
        monkeypatch.setattr(guard, "load_approvals",
                            lambda: {"15": {"head_sha": "abc1234"}})
        code, message = guard.check_merge("15")
        assert code == 0
        assert "approved" in message

    def test_approval_lapses_when_the_branch_moves(self, guard, monkeypatch):
        """Sign-off is bound to a commit; a later push has to be signed off again."""
        monkeypatch.setattr(guard, "_changed_paths",
                            lambda pr: ["core_brain/venue.py"])
        monkeypatch.setattr(guard, "_head_sha", lambda pr: "def5678")
        monkeypatch.setattr(guard, "load_approvals",
                            lambda: {"15": {"head_sha": "abc1234"}})
        assert guard.check_merge("15")[0] == 2

    def test_approval_for_another_pr_does_not_carry_over(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "load_approvals",
                            lambda: {"99": {"head_sha": "abc1234"}})
        assert guard.is_approved("15", "abc1234") is False

    def test_unknown_head_is_never_approved(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "load_approvals",
                            lambda: {"15": {"head_sha": "abc1234"}})
        assert guard.is_approved("15", "") is False

    def test_record_approval_round_trips(self, guard, monkeypatch, tmp_path):
        monkeypatch.setattr(guard, "APPROVALS", tmp_path / "merge-approvals.json")
        monkeypatch.setattr(guard, "_head_sha", lambda pr: "abc1234def")
        assert guard.record_approval("15") == 0
        assert guard.is_approved("15", "abc1234def") is True
        assert guard.is_approved("15", "other") is False

    def test_record_approval_without_a_head_fails_loudly(self, guard, monkeypatch,
                                                         tmp_path):
        monkeypatch.setattr(guard, "APPROVALS", tmp_path / "merge-approvals.json")
        monkeypatch.setattr(guard, "_head_sha", lambda pr: "")
        assert guard.record_approval("15") == 1

    def test_approve_without_a_number_says_so_instead_of_hanging(self):
        """A bare `--approve` used to fall through to reading standard input.

        The operator saw the command hang until they sent end-of-file, on the
        one command in this file they are meant to run by hand.
        """
        res = subprocess.run(
            [sys.executable, str(GUARD), "--approve"], input="",
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert res.returncode == 1
        assert "usage" in res.stderr.lower()

    def test_approve_with_extra_arguments_is_rejected(self, guard):
        assert guard.main(["--approve", "15", "16"]) == 1


class TestDegradesToAllow:
    def test_missing_gh_binary_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "")
        assert guard._open_pr_for_head() is None
        assert guard.check_push() == (0, "")

    def test_unparseable_gh_output_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "<html>login</html>")
        assert guard._open_pr_for_head() is None
        assert guard._reviews("15") == []
        assert guard._head_sha("15") == ""

    def test_gh_returning_a_list_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "[1, 2]")
        assert guard._open_pr_for_head() is None

    def test_corrupt_approvals_file_reads_as_no_approvals(self, guard, monkeypatch,
                                                          tmp_path):
        path = tmp_path / "merge-approvals.json"
        path.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(guard, "APPROVALS", path)
        assert guard.load_approvals() == {}
        assert guard.is_approved("15", "abc") is False
