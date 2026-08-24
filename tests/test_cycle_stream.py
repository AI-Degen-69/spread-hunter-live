"""Tests for engine.cycle_stream — the live cycle-telemetry ring (#51).

Covers the ring file (append, rotation, tail reads, concurrent appends) and
the cycle_intent table (decide inserts, submit updates, retention window).
All tests use tmp_path; nothing ever touches live/run/.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from core_brain.cycle_stream import close_intent_connections, emit, read_ring

REQUIRED_FIELDS = {
    "ts", "service", "cycle", "phase", "action",
    "market_slug", "reason", "latency_ms", "pid", "extra",
}


def _parse_lines(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _query_intent(db: Path, sql: str, params=()) -> list[tuple]:
    with sqlite3.connect(str(db)) as conn:
        return conn.execute(sql, params).fetchall()


class TestRingFile:
    def test_emit_creates_file(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        emit(1, "reconciling", "reconcile_ok", ring_path=ring)
        assert ring.exists()
        events = _parse_lines(ring)
        assert len(events) == 1
        assert REQUIRED_FIELDS <= set(events[0])
        assert events[0]["cycle"] == 1
        assert events[0]["action"] == "reconcile_ok"

    def test_emit_appends_not_overwrites(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 4):
            emit(i, "reconciling", "reconcile_ok", ring_path=ring)
        events = _parse_lines(ring)
        assert len(events) == 3
        assert [e["cycle"] for e in events] == [1, 2, 3]

    def test_rotation_at_500(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 511):
            emit(i, "reconciling", "reconcile_ok", service="query",
                 ring_path=ring)
        # Algorithm: append, then if lines > 500 keep the last 400. The 501st
        # append triggers one rotation (keeps events 102..501 = 400 lines);
        # events 502..510 add 9 more. The plan's draft numbers (410 / first
        # cycle 111) do not follow from its own rule; these are the actual ones.
        events = _parse_lines(ring)
        assert len(events) == 409
        assert events[0]["cycle"] == 102
        assert events[-1]["cycle"] == 510

    def test_rotation_preserves_valid_json(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 601):
            emit(i, "scanning", "rerank_done", service="query", ring_path=ring)
        events = _parse_lines(ring)
        assert len(events) == 499  # rotated once at 501, then 502..600 appended
        for e in events:
            assert REQUIRED_FIELDS <= set(e)
            assert e["cycle"] >= 102

    def test_decide_service_never_rotates(self, tmp_path):
        # Q3: only the query process owns rotation. Decide appends must never
        # trigger a rewrite, so a concurrent query rotation cannot lose them.
        ring = tmp_path / "cycle_events.jsonl"
        # phase="quoting" + action="decide" also writes a cycle_intent row, so
        # db_path has to point into tmp_path. Without it the row lands in the
        # production registry, which conftest now blocks outright.
        db = tmp_path / "live.db"
        for i in range(1, 511):
            emit(i, "quoting", "decide", service="decide", ring_path=ring,
                 db_path=db)
        assert len(_parse_lines(ring)) == 510

    def test_emit_never_raises(self, tmp_path, capsys):
        # ring_path is a directory: the append fails, but emit must not raise.
        emit(1, "reconciling", "reconcile_ok", ring_path=tmp_path)
        assert "WARNING" in capsys.readouterr().err

    def test_read_ring_tail(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 51):
            emit(i, "reconciling", "reconcile_ok", ring_path=ring)
        events = read_ring(ring_path=ring, tail=10)
        assert len(events) == 10
        assert [e["cycle"] for e in events] == list(range(41, 51))

    def test_concurrent_appends(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"

        def worker(pid_tag: int):
            for i in range(50):
                emit(i, "reconciling", "reconcile_ok", service="fleet",
                     ring_path=ring, extra={"worker": pid_tag})

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Every line is a complete JSON object regardless of interleaving.
        events = _parse_lines(ring)
        assert len(events) == 150


class TestCycleIntent:
    def test_cycle_intent_write(self, tmp_path):
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(7, "quoting", "decide", market_slug="will-eth-pump",
             extra={"intent_count": 3, "top_skip_reason": "spread_too_wide"},
             ring_path=ring, db_path=db)
        rows = _query_intent(db, "SELECT cycle, market_slug, intent_count, "
                                  "submitted, cancelled, top_skip_reason "
                                  "FROM cycle_intent")
        assert len(rows) == 1
        cycle, slug, intent_count, submitted, cancelled, skip = rows[0]
        assert (cycle, slug, intent_count) == (7, "will-eth-pump", 3)
        assert skip == "spread_too_wide"
        assert (submitted, cancelled) == (0, 0)

    def test_cycle_intent_submit_updates_row(self, tmp_path):
        # The submit event of the same visit updates the decide row instead of
        # creating a second one: one row per market visit, with outcomes.
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(7, "quoting", "decide", market_slug="will-eth-pump",
             extra={"intent_count": 2, "condition_id": "0xcid"},
             ring_path=ring, db_path=db)
        emit(7, "quoting", "submit", market_slug="will-eth-pump",
             extra={"submitted": 1, "cancelled": 1},
             ring_path=ring, db_path=db)
        rows = _query_intent(db, "SELECT intent_count, submitted, cancelled, "
                                  "condition_id FROM cycle_intent")
        assert len(rows) == 1
        assert rows[0] == (2, 1, 1, "0xcid")

    def test_cycle_intent_submit_targets_exact_visit(self, tmp_path):
        """A later decide for the same market must not capture an earlier submit."""
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(7, "quoting", "decide", market_slug="mkt",
             extra={"intent_count": 1}, ring_path=ring, db_path=db)
        emit(8, "quoting", "decide", market_slug="mkt",
             extra={"intent_count": 2}, ring_path=ring, db_path=db)
        emit(7, "quoting", "submit", market_slug="mkt",
             extra={"submitted": 1, "cancelled": 0}, ring_path=ring, db_path=db)
        rows = _query_intent(db, "SELECT cycle, intent_count, submitted, "
                                  "cancelled FROM cycle_intent ORDER BY id")
        assert rows[0] == (7, 1, 1, 0)
        assert rows[1] == (8, 2, 0, 0)

    def test_cycle_intent_market_error_persists_partial_counts(self, tmp_path):
        """A submit/cancel failure records the partial counts, not zeros."""
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(7, "quoting", "decide", market_slug="mkt",
             extra={"intent_count": 2}, ring_path=ring, db_path=db)
        emit(7, "quoting", "market_error", market_slug="mkt",
             reason="submit/cancel: boom",
             extra={"submitted": 1, "cancelled": 0}, ring_path=ring, db_path=db)
        rows = _query_intent(db, "SELECT submitted, cancelled FROM cycle_intent")
        assert rows[0] == (1, 0)

    def test_one_connection_serves_many_intent_writes(self, tmp_path, monkeypatch):
        """Connecting costs ~3ms, and emit() runs once per market visit.

        The engine writes one registry for its whole life, so the connection
        is opened once and kept, not rebuilt per decide event.
        """
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        connects: list[str] = []
        real_connect = sqlite3.connect

        def counting_connect(target, *args, **kwargs):
            connects.append(str(target))
            return real_connect(target, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", counting_connect)
        for i in range(1, 51):
            emit(i, "quoting", "decide", market_slug="m",
                 ring_path=ring, db_path=db)

        assert connects.count(str(db)) == 1, (
            f"{connects.count(str(db))} connections for 50 decide events")

    def test_a_dead_cached_handle_still_attributes_the_submit(self, tmp_path):
        """Closing the handle in place is what the retry actually exists for.

        `close_intent_connections()` also drops the cache entry, so a test
        using it never reaches the retry -- the next write just opens a fresh
        connection. Closing the handle while leaving it cached is the real
        failure state, and without the retry the submit event's counts never
        reach the row the decide event inserted.
        """
        from core_brain import cycle_stream

        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(4, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)

        cycle_stream._DB_CACHE[str(db)].close()  # dead, and still cached

        emit(4, "quoting", "submit", market_slug="m",
             extra={"submitted": 2, "cancelled": 1},
             ring_path=ring, db_path=db)

        assert _query_intent(
            db, "SELECT submitted, cancelled FROM cycle_intent") == [(2, 1)]

    def test_a_reopened_connection_keeps_writing(self, tmp_path):
        """The public close is a supported call, not a one-way door."""
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(1, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)

        close_intent_connections()

        emit(2, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)
        cycles = [row[0] for row in
                  _query_intent(db, "SELECT cycle FROM cycle_intent ORDER BY id")]
        assert cycles == [1, 2]

    def test_a_locked_database_warns_and_never_stalls_the_loop(
            self, tmp_path, capsys):
        """A contended write is dropped with a warning, not blocked on.

        emit() runs on the trading loop. Waiting out a long lock would stall a
        cycle, which costs more than the telemetry row is worth, so the retry
        is bounded and the row is discarded if the lock outlasts it.
        """
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(1, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)

        blocker = sqlite3.connect(str(db), timeout=0.1)
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            started = time.monotonic()
            emit(2, "quoting", "decide", market_slug="m",
                 ring_path=ring, db_path=db)
            elapsed = time.monotonic() - started
        finally:
            blocker.rollback()
            blocker.close()

        assert "cycle_intent insert failed" in capsys.readouterr().err
        assert elapsed < 10, f"emit blocked {elapsed:.1f}s on a locked database"

        # The loop keeps going: the next write lands once the lock is gone.
        emit(3, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)
        cycles = [row[0] for row in
                  _query_intent(db, "SELECT cycle FROM cycle_intent ORDER BY id")]
        assert cycles == [1, 3]

    def test_cycle_intent_retention_200(self, tmp_path):
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        for i in range(1, 211):
            emit(i, "quoting", "decide", market_slug=f"m{i % 3}",
                 ring_path=ring, db_path=db)
        (count,) = _query_intent(db, "SELECT COUNT(*) FROM cycle_intent")[0]
        assert count == 200
