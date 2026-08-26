"""Tests for the conftest guard that keeps the test suite off the live cycle ring.

`runtime/cycle_events.jsonl` is the engine's production telemetry ring: capped
at 500 lines with rotation and no archive, so a test run that writes there
evicts real events the operator is still reasoning from. Before this guard,
every full-suite run that exercised `cycle_stream.emit()` without an explicit
`ring_path` appended straight into it.
"""
from __future__ import annotations

import os

import pytest

from core_brain import cycle_stream
from core_brain.cycle_stream import DEFAULT_RING_PATH, emit, read_ring


def _ring_state():
    """(size, mtime_ns) of the production ring, or None while absent.

    A missing ring must not make the guard look like it worked -- a test could
    create it and count that as proof. The holding tests therefore require the
    ring to exist before they emit anything.
    """
    if not DEFAULT_RING_PATH.exists():
        return None
    st = DEFAULT_RING_PATH.stat()
    return st.st_size, st.st_mtime_ns


@pytest.fixture()
def require_production_ring_present():
    state = _ring_state()
    if state is None:
        pytest.skip(
            "production cycle ring not present on this checkout; "
            "the hold-across-emit assertion needs a real ring to leave alone"
        )
    return state


def test_default_emit_leaves_production_ring_untouched(
        tmp_path, require_production_ring_present):
    """The regression: default-path emits must not reach the production ring.

    `emit()` with no `ring_path` used to append into
    `runtime/cycle_events.jsonl`; rotation then evicted the oldest lines for
    good. The guard redirects the module global, so the same call now lands in
    the session temp dir and the production ring keeps its exact size and
    mtime across the emit.
    """
    before = require_production_ring_present
    emit(1, "reconciling", "reconcile_ok", market_slug="mkt")
    assert _ring_state() == before


def test_decide_path_default_emit_leaves_production_ring_untouched(
        tmp_path, require_production_ring_present):
    """The decide path is the one that shreds decides -- hold it too.

    `phase="quoting"` + `action="decide"` is what wrote 33 decides per suite
    run into the live ring. It also writes a cycle_intent row, which the
    registry guard already forces onto a temp database; here the ring side of
    the same call is what must stay clean.
    """
    before = require_production_ring_present
    emit(1, "quoting", "decide", market_slug="mkt",
         db_path=tmp_path / "orders.db")
    assert _ring_state() == before


def test_the_fixture_actually_moved_the_default():
    """The module-level import binds the PRE-fixture value, so it is the
    production path; the module attribute is what the fixture patches.

    Without this, every other test here could pass on a checkout where the
    fixture silently did nothing -- an emit that reached the production ring
    and an emit that reached a temp ring both leave the assertions below
    satisfied if the two paths are the same path.
    """
    assert cycle_stream.DEFAULT_RING_PATH != DEFAULT_RING_PATH, (
        "the autouse fixture did not redirect the default ring")
    assert cycle_stream.LIVE_ROOT != DEFAULT_RING_PATH.parent.parent


def test_read_ring_default_resolves_away_from_production_ring():
    """read_ring()'s fallback resolves through LIVE_ROOT; both are redirected.

    The live ring holds hundreds of lines, so an empty default read is
    positive proof the fallback resolved into this test's own temp dir.
    """
    assert cycle_stream.DEFAULT_RING_PATH != DEFAULT_RING_PATH
    assert read_ring(tail=10) == []


def test_redirected_ring_receives_the_event(tmp_path):
    """The redirected default actually works: the event lands and reads back."""
    assert cycle_stream.DEFAULT_RING_PATH != DEFAULT_RING_PATH
    emit(7, "reconciling", "reconcile_ok", market_slug="mkt")
    assert cycle_stream.DEFAULT_RING_PATH.exists(), (
        "the event went somewhere other than the redirected default")
    events = read_ring(tail=10)
    assert len(events) == 1
    assert events[0]["cycle"] == 7
    assert events[0]["action"] == "reconcile_ok"
