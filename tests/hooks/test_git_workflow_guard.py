"""Tests for the PreToolUse git workflow guard's entry point.

The guard's policy checks shell out to `gh`, so they are not exercised here.
What is covered is the contract every caller depends on: it takes no arguments,
and unexpected input degrades to allow rather than wedging the repo.
"""
from __future__ import annotations

import io
import json
import sys

import pytest

sys.path.insert(0, "scripts/hooks")

import git_workflow_guard as guard  # noqa: E402


def test_rejects_arguments_instead_of_hanging_on_stdin(capsys):
    # Arrange — the retired sign-off invocation. Falling through to stdin here
    # would block on a terminal read instead of returning.
    argv = ["--approve", "104"]

    # Act
    code = guard.main(argv)

    # Assert
    assert code == 1
    err = capsys.readouterr().err
    assert "takes no arguments" in err
    assert "no sign-off" in err


def test_no_merge_sign_off_api_remains():
    # Arrange / Act / Assert — the approval gate was removed, not disabled.
    for name in ("record_approval", "is_approved", "load_approvals", "APPROVALS"):
        assert not hasattr(guard, name), f"{name} should have been removed"


def test_merge_check_always_allows():
    # Arrange
    pr = "104"

    # Act
    code, message = guard.check_merge(pr)

    # Assert — a merge is never blocked, for any path.
    assert code == 0
    assert message


def test_unparseable_stdin_allows(monkeypatch):
    # Arrange
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))

    # Act
    code = guard.main([])

    # Assert
    assert code == 0


def test_payload_for_an_unrelated_tool_allows(monkeypatch):
    # Arrange
    payload = {"tool_name": "Read", "tool_input": {"file_path": "AGENTS.md"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    # Act
    code = guard.main([])

    # Assert
    assert code == 0
