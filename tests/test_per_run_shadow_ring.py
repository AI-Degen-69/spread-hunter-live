"""Tests for the per-run ring: a rehearsal writes `runtime/shadow-<run_id>.jsonl`.

Before this, `_make_logging_emit` passed no `ring_path`, so a rehearsal's
events fell through to `cycle_stream.DEFAULT_RING_PATH` -- the live engine's
500-line production ring, shared with the query loop, the settling sweep and
the test suite. The rehearsal was a bystander in someone else's rotation.

How to verify (operator, PowerShell):

    python -m pytest -q tests/test_per_run_shadow_ring.py ; python -m pytest -q

The second command is the bar this repo merges against. The production ring
must be byte-identical across it; the fixture below asserts that directly.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from core_brain import cycle_stream
from core_brain.cycle_stream import DEFAULT_RING_PATH


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _read_events(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def require_production_ring_present():
    if not DEFAULT_RING_PATH.exists():
        pytest.skip(
            "production cycle ring not present on this checkout; "
            "the byte-identity assertion needs a real ring to leave alone"
        )
    return _sha(DEFAULT_RING_PATH)


def _expected_ring(run_id: str):
    """Where the per-run ring must land: the production runtime/ shape."""
    return cycle_stream.LIVE_ROOT / "runtime" / f"shadow-{run_id}.jsonl"


class TestPerRunRing:
    """A shadow session's events land in its own file, never the live ring."""

    def test_emit_writes_the_per_run_ring_named_for_the_run(self, tmp_path):
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        db = tmp_path / "shadow.db"
        init_db(db)
        emit = _make_logging_emit(db, run_id="testrun1")
        emit(7, "quoting", "decide", market_slug="dota-2026",
             reason="pair cost 0.985", extra={"intent_count": 2})

        run_ring = _expected_ring("testrun1")
        assert run_ring.exists(), "the per-run ring was not created"
        events = _read_events(run_ring)
        assert len(events) == 1
        assert events[0]["action"] == "decide"

    def test_non_decide_phases_land_in_the_per_run_ring_too(self, tmp_path):
        """Isolation is for the whole session, not only the decide path."""
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        db = tmp_path / "shadow.db"
        init_db(db)
        emit = _make_logging_emit(db, run_id="testrun2")
        emit(9, "settling", "pairs_completed", market_slug="dota-2026")

        assert len(_read_events(_expected_ring("testrun2"))) == 1

    def test_default_ring_is_byte_untouched_by_a_shadow_session(
            self, tmp_path, require_production_ring_present):
        """The assertion that actually proves the isolation.

        Hash the production ring before and after a default-path emit, the
        way test_cycle_ring_guard does. (The conftest redirect already moves
        DEFAULT_RING_PATH itself; this guards the seam's wiring against a
        regression where it stops passing ring_path.)
        """
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        db = tmp_path / "shadow.db"
        init_db(db)
        before = _sha(DEFAULT_RING_PATH)
        emit = _make_logging_emit(db, run_id="testrun3")
        emit(7, "quoting", "decide", market_slug="dota-2026",
             reason="pair cost 0.985", extra={"intent_count": 1})
        assert _sha(DEFAULT_RING_PATH) == before

    def test_no_run_id_falls_back_to_the_default_ring(self, tmp_path):
        """A caller that names no run gets today's behaviour, unchanged.

        Older call sites build `_make_logging_emit` without a `run_id`; they
        must keep writing the default ring rather than silently nowhere.
        """
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        db = tmp_path / "shadow.db"
        init_db(db)
        # getattr with a sentinel keeps this a failing *assertion* while the
        # feature is missing, not an AttributeError.
        assert getattr(_make_logging_emit(db), "ring_path", "<missing>") is None


class TestPerRunRingDoesNotRotate:
    """A 45-minute rehearsal must still have its 45th minute on disk at the end.

    Rotation is disabled on run-scoped rings: the per-run file has exactly one
    writer appending ~2000 short lines over a whole rehearsal (~300 KB), while
    the live ring rotates because many writers share one small file. A cap the
    rehearsal could hit would be the same evidence loss in miniature.
    """

    RUNG_CAP = 50  # tiny stand-in cap, reachable in a unit test

    @pytest.fixture()
    def capped_session(self, tmp_path, monkeypatch):
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        # Shrink the caps so any accidental rotation would fire well inside
        # the test; the assertion is that the per-run ring ignores them.
        monkeypatch.setattr(cycle_stream, "MAX_LINES", self.RUNG_CAP)
        monkeypatch.setattr(cycle_stream, "KEEP_LINES", self.RUNG_CAP - 10)
        db = tmp_path / "shadow.db"
        init_db(db)
        emit = _make_logging_emit(db, run_id="captest")
        return emit, _expected_ring("captest")

    def test_first_line_survives_the_last(self, capped_session):
        emit, ring = capped_session
        total = self.RUNG_CAP * 3  # far past what any rotation would keep
        for i in range(total):
            emit(i, "reconciling", "reconcile_ok", market_slug=f"m{i}")
        events = _read_events(ring)
        assert len(events) == total, "the per-run ring dropped lines"
        assert events[0]["cycle"] == 0, "the first line did not survive"
        assert events[-1]["cycle"] == total - 1

    def test_rotation_is_disabled_on_the_run_scoped_ring(self, tmp_path,
                                                         monkeypatch):
        """The mechanism asserted directly: can_rotate=False reaches emit().

        The wrapper resolves `cycle_stream.emit` at call time, so a module
        attribute patch sees exactly what the rehearsal would send.
        """
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        db = tmp_path / "shadow.db"
        init_db(db)
        emit = _make_logging_emit(db, run_id="captest")

        seen = {}
        orig = cycle_stream.emit

        def spy(cycle, phase, action, **kw):
            seen.update(kw)
            return orig(cycle, phase, action, **kw)

        monkeypatch.setattr(cycle_stream, "emit", spy)
        emit(1, "reconciling", "reconcile_ok", market_slug="m")
        assert seen.get("can_rotate") is False
        assert seen.get("ring_path") == _expected_ring("captest")


class TestTheRingNameIsNotTakenOnTrust:
    """`SH_RUN_ID` is read from the environment verbatim, so the id that names
    this file is operator input, not a generated token."""

    def test_an_id_that_already_says_shadow_is_not_prefixed_twice(self):
        from core_brain.shadow_run import _run_ring_name, shadow_run_id

        generated = shadow_run_id()
        assert generated.startswith("shadow-"), generated
        assert _run_ring_name(generated) == f"{generated}.jsonl", (
            "the generated id already carries the prefix; naming the file "
            "shadow-shadow-<hex>.jsonl makes the run harder to find, not safer")

    @pytest.mark.parametrize("hostile", [
        "../../etc/passwd",
        "..",
        r"a/b\c",
        "run id with spaces",
        "",
    ])
    def test_a_hostile_id_cannot_leave_the_runtime_directory(self, hostile):
        from core_brain.shadow_run import _run_ring_name

        name = _run_ring_name(hostile)
        assert "/" not in name and "\\" not in name, name
        assert ".." not in name, name
        assert name.endswith(".jsonl") and len(name) > len(".jsonl"), name


class TestTheRunRingIsNotNegotiable:
    """With a run_id, a caller must not be able to put the rehearsal back in
    someone else's file or switch rotation on."""

    def test_a_caller_cannot_redirect_or_rotate_the_run_ring(self, tmp_path):
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        db = tmp_path / "shadow.db"
        init_db(db)
        elsewhere = tmp_path / "someone-elses-ring.jsonl"
        emit = _make_logging_emit(db, run_id="testrun2")

        emit(1, "quoting", "decide", market_slug="dota-2026",
             extra={"intent_count": 0}, ring_path=elsewhere, can_rotate=True)

        assert not elsewhere.exists(), (
            "a caller redirected the rehearsal out of its own ring")
        run_ring = _expected_ring("testrun2")
        assert run_ring.exists() and _read_events(run_ring), run_ring
