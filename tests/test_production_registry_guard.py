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


_ABS = str(DEFAULT_DB_PATH).replace("\\", "/")

# Plain forms: stripping the `file:` scheme and the query string is enough to
# recognise these, so they were already blocked before the URI parsing landed.
PLAIN_URIS = [
    "file:data/orders.db",
    "file:data/orders.db?mode=ro",
]

# Bypasses: SQLite percent-decodes the path and accepts a `localhost`
# authority, so a guard that only strips the scheme lets each of these open
# the real registry.
BYPASS_URIS = [
    "file:data/orders%2edb",
    "file:data%2Forders.db",
    "file:///" + _ABS,
    "file://localhost/" + _ABS,
]


@pytest.mark.parametrize("uri", PLAIN_URIS)
def test_plain_uri_forms_of_the_production_registry_are_blocked(uri):
    with pytest.raises(BaseException, match="production registry"):
        sqlite3.connect(uri, uri=True)


@pytest.mark.parametrize("uri", BYPASS_URIS)
def test_encoded_uri_forms_of_the_production_registry_are_blocked(uri):
    with pytest.raises(BaseException, match="production registry"):
        sqlite3.connect(uri, uri=True)


def test_file_prefix_without_uri_flag_is_a_literal_filename(tmp_path, monkeypatch):
    """Without `uri=True`, a leading `file:` is part of the name, not a scheme.

    Blocking it anyway would be a false positive, so the guard has to read the
    same flag sqlite3.connect does. Connecting without the guard firing is the
    assertion; the name on disk is left to the platform, since a colon means
    something different on NTFS.
    """
    monkeypatch.chdir(tmp_path)
    # What is under test is that the guard stays quiet, not whether the
    # platform can hold a file whose name contains a colon, so a plain
    # OperationalError is an acceptable outcome here.
    # ProductionRegistryWriteError is not: it derives from BaseException and
    # sails straight through this except clause.
    try:
        sqlite3.connect("file:data/orders.db").close()
    except sqlite3.OperationalError:
        pass


def test_shared_memory_uri_still_connects():
    with sqlite3.connect("file:guardcheck?mode=memory&cache=shared", uri=True) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


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
