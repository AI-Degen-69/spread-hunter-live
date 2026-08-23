"""live/tests/conftest.py - Hermetic environment fixtures for live test suite.

Invariants:
1. Every test runs with credentials scrubbed from os.environ by default.
2. Any outbound network connection to a non-loopback address is blocked and raises RuntimeError.
3. No test may open the production registry `data/orders.db`.
"""
from __future__ import annotations

import os
import re
import socket
import sqlite3
import urllib.parse
from pathlib import Path

import pytest

from core_brain.order_registry import DEFAULT_DB_PATH

CREDENTIAL_VARS = (
    "POLY_PRIVATE_KEY",
    "POLY_KEY",
    "POLY_FUNDER",
    "POLY_SIG_TYPE",
    "PRIVATE_KEY",
    "RELAYER_API_KEY",
    "RELAYER_API_KEY_ADDRESS",
    "POLYGON_RPC",
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: opt-in marker to allow network access in a test"
    )


@pytest.fixture(autouse=True)
def scrub_credential_env(monkeypatch):
    """Scrub venue credentials and sensitive endpoint configs from os.environ."""
    for var in CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)


def _is_loopback(address):
    if isinstance(address, tuple) and len(address) >= 1:
        host = address[0]
        if host in LOOPBACK_HOSTS:
            return True
        # Handle IP representation of localhost
        if isinstance(host, str) and (host.startswith("127.") or host == "::1"):
            return True
        return False
    elif isinstance(address, str):
        # AF_UNIX socket path on Linux
        return True
    return False


@pytest.fixture(autouse=True)
def block_non_loopback_sockets(request, monkeypatch):
    """Block all non-loopback socket connections unless explicitly marked with @pytest.mark.allow_network."""
    if request.node.get_closest_marker("allow_network"):
        return

    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        if not _is_loopback(address):
            raise RuntimeError(
                f"Network access denied: test attempted socket connect to non-loopback address {address!r}"
            )
        return orig_connect(self, address)

    def guarded_connect_ex(self, address):
        if not _is_loopback(address):
            raise RuntimeError(
                f"Network access denied: test attempted socket connect_ex to non-loopback address {address!r}"
            )
        return orig_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


class ProductionRegistryWriteError(BaseException):
    """Raised when a test opens the production registry.

    Deliberately not an `Exception`. `cycle_stream.emit()` wraps its whole body
    in `except Exception` and only prints a warning, so an `Exception` here
    would be swallowed and the test would pass while still writing rows into
    `data/orders.db`.
    """


def _uri_to_path(raw: str) -> str | None:
    """Resolve a SQLite `file:` URI to the path it actually opens.

    Returns None when the URI does not name a local file. SQLite decodes
    percent escapes and accepts a `localhost` authority, so comparing the raw
    URI text lets `file:data/orders%2edb` and `file://localhost/<abs>` through
    a guard that only strips the scheme.
    """
    parsed = urllib.parse.urlparse(raw)
    # An empty or `localhost` authority means this machine; anything else is a
    # host we cannot resolve to a local path.
    if parsed.netloc not in ("", "localhost"):
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if "memory" in query.get("mode", ()):
        return None
    path = urllib.parse.unquote(parsed.path)
    # `file:///C:/x` parses to `/C:/x`; drop the slash in front of the drive.
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path or None


def _is_production_registry(database, uri: bool = False) -> bool:
    """True when `database` names the production registry file.

    `uri` mirrors sqlite3.connect's own flag: without it a leading `file:` is
    part of the filename, not a scheme.
    """
    if not isinstance(database, (str, os.PathLike)):
        return False
    raw = os.fspath(database)
    if not raw or raw == ":memory:":
        return False
    if uri and raw.startswith("file:"):
        resolved = _uri_to_path(raw)
        if resolved is None:
            return False
        raw = resolved
    try:
        return Path(raw).resolve() == DEFAULT_DB_PATH.resolve()
    except (OSError, ValueError):
        return False


@pytest.fixture(autouse=True)
def block_production_registry(monkeypatch):
    """Fail any test that opens the production registry.

    `cycle_stream.emit()` and `order_registry.get_connection()` both fall back
    to `DEFAULT_DB_PATH` when no `db_path` is passed, so a single omitted
    keyword argument writes live rows into the real registry. Every test that
    needs a database has to point at `tmp_path`.
    """
    orig_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        # `uri` is the 8th positional parameter of sqlite3.connect, so args[6]
        # once `database` is peeled off.
        uri = args[6] if len(args) >= 7 else kwargs.get("uri", False)
        if _is_production_registry(database, uri=bool(uri)):
            raise ProductionRegistryWriteError(
                f"test opened the production registry {DEFAULT_DB_PATH}; "
                "pass an explicit db_path under tmp_path instead"
            )
        return orig_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
