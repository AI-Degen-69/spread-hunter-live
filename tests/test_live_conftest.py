"""Verification of live suite hermetic guards and namespace precedence in live/tests/."""
from __future__ import annotations

import os
import importlib
import socket
from pathlib import Path
import pytest

REQUIRED_CREDENTIAL_VARS = frozenset({
    "POLY_PRIVATE_KEY",
    "POLY_KEY",
    "POLY_API_KEY",
    "POLY_API_SECRET",
    "POLY_API_PASSPHRASE",
    "POLY_FUNDER",
    "POLY_SIG_TYPE",
    "PRIVATE_KEY",
    "RELAYER_API_KEY",
    "RELAYER_API_KEY_ADDRESS",
    "POLYGON_RPC",
})


from tests.conftest import CREDENTIAL_VARS


def test_live_env_scrub_removes_credentials():
    """Default test environment in live/ must have zero venue credentials in os.environ."""
    assert REQUIRED_CREDENTIAL_VARS.issubset(set(CREDENTIAL_VARS)), (
        f"Missing credentials in CREDENTIAL_VARS: {REQUIRED_CREDENTIAL_VARS - set(CREDENTIAL_VARS)}"
    )
    for var in REQUIRED_CREDENTIAL_VARS:
        assert var not in os.environ, f"{var} leaked into test environment"


def test_scrub_credential_env_actively_removes_populated_vars(monkeypatch):
    """If environment variables are preset, the scrub logic removes all required venue credentials."""
    import tests.conftest as root_conftest

    for var in REQUIRED_CREDENTIAL_VARS:
        monkeypatch.setenv(var, "secret-test-token")

    # Invoke conftest scrub implementation
    root_conftest.scrub_credentials(monkeypatch)

    for var in REQUIRED_CREDENTIAL_VARS:
        assert var not in os.environ, f"{var} was not scrubbed from environment"




def test_live_network_block_raises_on_outbound_socket():
    """Attempting outbound connection to non-loopback address must raise RuntimeError."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="Network access denied"):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()


def test_every_engine_module_resolves_inside_live():
    """The engine is one package in one directory -- live/engine/ and nowhere else.

    Until 2026-08-18 this package was called `strategy` and had no
    `__init__.py`, so Python could merge it with any same-named directory on
    sys.path into one namespace package spanning both trees. Which directory a
    module resolved from depended on the operator's working directory. The
    rename ended that. This test is what keeps it ended.
    """
    import core_brain
    import core_brain.order_manager as live_exec
    import core_brain.order_registry as order_reg
    import core_brain.markets as markets
    import core_brain.config as config

    live_dir = Path(__file__).resolve().parent.parent

    # A regular package, so __path__ is exactly one directory -- not a namespace
    # package that can silently pick up a same-named directory elsewhere.
    assert list(core_brain.__path__) == [str(live_dir / "core_brain")], list(core_brain.__path__)

    for mod in (live_exec, order_reg, markets, config):
        path = Path(mod.__file__).resolve()
        assert path.parent == live_dir / "core_brain", f"{mod.__name__} resolved to {path}"
