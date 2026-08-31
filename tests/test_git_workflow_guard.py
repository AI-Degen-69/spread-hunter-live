"""Tests for the PreToolUse guard over git and GitHub commands.

The guard exists because the rules it enforces sit in a file AGENTS.md links to
rather than imports, and a partial read of that file misses them. It has to hold
when `gh` is missing, unauthenticated, or slow, so every helper degrades to
"allow" rather than blocking the repo.

The merge sign-off / `--approve` tests that used to live here were deleted with
the feature itself (#42, PR #107): merges are autonomous now, and
`tests/hooks/test_git_workflow_guard.py` asserts that API stays absent.
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
            "CHANGES_REQUESTED",
        ])
        assert guard.check_push()[0] == 2

    def test_a_trailing_approval_does_not_hide_the_rounds(self, guard, monkeypatch):
        """The real pattern on PR #16: every round ended in an approval.

        Exempting "latest review is an approval" meant the block could never
        fire, because the reviewer posts CHANGES_REQUESTED and then APPROVED
        for the same commit. Five rounds ran with the guard installed.
        """
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_reviews", lambda pr: [
            "CHANGES_REQUESTED",
            "CHANGES_REQUESTED", "APPROVED",
            "CHANGES_REQUESTED", "APPROVED",
        ])
        code, message = guard.check_push()
        assert code == 2
        assert "3 rounds" in message

    def test_only_the_review_bot_counts(self, guard, monkeypatch):
        """A person requesting changes is not an automatic review round."""
        page = [
            {"user": {"login": guard.REVIEW_BOT}, "state": "CHANGES_REQUESTED"},
            {"user": {"login": "a-human"}, "state": "CHANGES_REQUESTED"},
            {"user": {"login": guard.REVIEW_BOT}, "state": "APPROVED"},
        ]
        monkeypatch.setattr(guard, "_run", lambda args: json.dumps([page]))
        assert guard._reviews("16") == ["CHANGES_REQUESTED", "APPROVED"]

    def test_every_page_of_reviews_is_read(self, guard, monkeypatch):
        """The endpoint returns 30 reviews a page.

        A pull request past that count is exactly the runaway this guard is
        for, so reading one page would undercount it into silence.
        """
        captured: list[list[str]] = []
        bot = {"user": {"login": guard.REVIEW_BOT}, "state": "CHANGES_REQUESTED"}
        pages = [[bot] * 30, [bot] * 5]

        def fake_run(args):
            captured.append(args)
            return json.dumps(pages)

        monkeypatch.setattr(guard, "_run", fake_run)
        assert len(guard._reviews("16")) == 35
        assert "--paginate" in captured[0]
        # Without --slurp, --paginate emits one JSON value per page rather than
        # a single array. The parse then fails and _reviews returns [], which
        # reads as no rounds and silently disables the block.
        assert "--slurp" in captured[0]
        # gh rejects --slurp alongside --jq, so the filtering is done in Python.
        assert "--jq" not in captured[0]

    @pytest.mark.parametrize("payload", [
        '{"not": "a list"}',
        '[{"no": "user"}]',
        '[[{"user": null, "state": "CHANGES_REQUESTED"}]]',
        '[[{"user": {"login": "coderabbitai[bot]"}, "state": 5}]]',
        '[["not a review object"]]',
        '["a page that is not a list"]',
    ])
    def test_unexpected_review_payloads_count_as_no_rounds(self, guard,
                                                           monkeypatch, payload):
        monkeypatch.setattr(guard, "_run", lambda args: payload)
        assert guard._reviews("16") == []

    def test_unreadable_review_states_do_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_run", lambda args: "<html>login</html>")
        assert guard._reviews("16") == []
        assert guard.check_push()[0] == 0


class TestMerge:
    def test_merge_of_a_pr_is_allowed_with_rules(self, guard):
        code, message = guard.check_merge("15")
        assert code == 0
        assert "merge check" in message.lower()
        assert "Read the full diff" in message

    def test_merge_without_pr_is_allowed(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: None)
        code, message = guard.check_merge(None)
        assert code == 0
        assert "Before merging" in message


class TestDegradesToAllow:
    def test_missing_gh_binary_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "")
        assert guard._open_pr_for_head() is None
        assert guard.check_push() == (0, "")

    def test_unparseable_gh_output_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "<html>login</html>")
        assert guard._open_pr_for_head() is None
        assert guard._reviews("15") == []
        # `_head_sha` went with the approval flow it served (#42, PR #107).

    def test_gh_returning_a_list_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "[1, 2]")
        assert guard._open_pr_for_head() is None
