"""Tests for the conftest guard that keeps the test suite off the production registry.

`data/orders.db` is the live order registry. Before this guard,
`cycle_stream.emit()` on the decide path wrote 200 rows of test data straight
into it whenever a test forgot `db_path`, and `emit()`'s blanket
`except Exception` meant nothing ever surfaced.
"""
from __future__ import annotations

import sqlite3

import pytest

from core_brain.cycle_stream import emit
from core_brain.order_registry import DEFAULT_DB_PATH


def test_direct_connect_to_production_registry_is_blocked():
    with pytest.raises(BaseException, match="production registry"):
        sqlite3.connect(str(DEFAULT_DB_PATH))


def test_temporary_database_still_connects(tmp_path):
    db = tmp_path / "live.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
    assert db.exists()


def test_in_memory_database_still_connects():
    with sqlite3.connect(":memory:") as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_emit_decide_without_db_path_fails_loudly(tmp_path):
    """The regression: emit()'s `except Exception` must not swallow the guard.

    `phase="quoting"` + `action="decide"` writes a cycle_intent row. With no
    `db_path` that row goes to DEFAULT_DB_PATH, and emit() catches every
    Exception, so the test would otherwise pass while corrupting the registry.
    """
    with pytest.raises(BaseException, match="production registry"):
        emit(1, "quoting", "decide", market_slug="mkt",
             ring_path=tmp_path / "ring.jsonl")
