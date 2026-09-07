"""The dashboard binds a port it was told to bind, and 8799 is a real address.

The operator's live stack runs `python -m dashboard.server` on 8799 -- that is
the control surface with the START button on it. Anything else that wants to
serve this app, a preview harness included, has to be able to take a different
port WITHOUT being handed a flag, or it collides with the live one.

Journeys under test:
1. As the Owner, the default is still 8799, so nothing about launching my stack
   changes.
2. As the Owner, a harness that can only set an environment variable can still
   move the port off the live one.
3. As the Owner, an explicit `--port` beats the environment, because a flag I
   typed is more specific than a variable I exported once.
4. As the Owner, a `PORT` that is not a port is refused rather than silently
   ignored, which would land the process back on 8799 beside the live stack.
"""
from __future__ import annotations

import pytest

from dashboard.server import DEFAULT_PORT, resolve_port


def test_the_default_is_the_operators_own_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)

    assert resolve_port(None) == DEFAULT_PORT == 8799


def test_the_environment_can_move_the_port(monkeypatch):
    monkeypatch.setenv("PORT", "8811")

    assert resolve_port(None) == 8811


def test_an_explicit_flag_beats_the_environment(monkeypatch):
    monkeypatch.setenv("PORT", "8811")

    assert resolve_port(9001) == 9001


@pytest.mark.parametrize("value", ["", "not-a-port", "0", "-1", "70000", "80.5"])
def test_an_unusable_port_variable_is_refused(monkeypatch, value):
    monkeypatch.setenv("PORT", value)

    with pytest.raises(ValueError):
        resolve_port(None)
