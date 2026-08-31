"""`queue_marks` must record the best bid, not only our own level.

Without it "we were passed" is unobservable. A maker at the touch is joined
from BEHIND by anyone quoting the same price -- price-time priority puts them
after us, and that already shows up as a negative `cancel_decay`. Being PASSED
is different: someone quotes a better bid, our level stops being the touch, and
every share of tape now clears against them before it can reach us. From our
own level's size those two look identical, and only the second one means our
queue position lost.

Recorded per mark rather than derived later: the book at the moment of the mark
is the only place this exists, and a run that ends without it cannot be
re-interrogated.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core_brain.shadow_exec import (
    ensure_shadow_tables, read_last_queue_mark, write_queue_mark,
)


def _cols(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[1] for r in conn.execute("pragma table_info(queue_marks)")}
    finally:
        conn.close()


class TestBestBidColumn:
    def test_a_fresh_store_has_the_column(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        assert "best_bid" in _cols(db)

    def test_a_store_written_before_this_column_is_migrated(self, tmp_path):
        # `data/shadow.db` already holds thousands of marks from earlier runs.
        # A new column must be added to the existing table, not require the
        # store to be thrown away -- the recorded history is the whole asset.
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE queue_marks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts REAL NOT NULL, condition_id TEXT, market_slug TEXT, "
            "token_id TEXT NOT NULL, price REAL NOT NULL, "
            "level_size REAL NOT NULL, traded REAL NOT NULL DEFAULT 0, "
            "cancel_decay REAL, queue_minutes REAL, run_id TEXT)")
        conn.execute(
            "INSERT INTO queue_marks (ts, token_id, price, level_size, traded) "
            "VALUES (1.0, 'tok', 0.47, 900.0, 0.0)")
        conn.commit()
        conn.close()

        ensure_shadow_tables(db)
        assert "best_bid" in _cols(db)

        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT level_size, best_bid FROM queue_marks").fetchall()
        finally:
            conn.close()
        assert rows == [(900.0, None)], "old marks survive, unbackfilled"

    def test_migrating_twice_is_harmless(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        ensure_shadow_tables(db)
        assert "best_bid" in _cols(db)


class TestWritingAndReading:
    def test_the_best_bid_round_trips(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        write_queue_mark(db, ts=1.0, condition_id="0xabc", market_slug="m",
                         token_id="tok", price=0.47, level_size=900.0,
                         traded=0.0, cancel_decay=None, queue_minutes=None,
                         run_id="r", best_bid=0.47)
        row = read_last_queue_mark(db, "tok", 0.47, run_id="r")
        assert row["best_bid"] == 0.47

    def test_it_defaults_to_null_when_the_book_had_no_bid(self, tmp_path):
        # A one-sided book is a real state and must not be written as 0.0,
        # which would read as "outbid by everything".
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        write_queue_mark(db, ts=1.0, condition_id=None, market_slug="m",
                         token_id="tok", price=0.47, level_size=0.0,
                         traded=0.0, cancel_decay=None, queue_minutes=None,
                         run_id="r")
        row = read_last_queue_mark(db, "tok", 0.47, run_id="r")
        assert row["best_bid"] is None


class TestTheRecorderPopulatesIt:
    def test_settle_market_records_the_book_s_best_bid(self, tmp_path):
        """The value must come from the same book read as `level_size`.

        Fetching it separately would let the two disagree by a cycle, and a
        one-cycle disagreement is exactly the size of the effect being measured.
        """
        from core_brain.shadow_exec import _record_queue_marks

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)

        class _Order:
            token_id = "tok"
            price = 0.47

        class _Market:
            condition_id = "0xabc"
            market_slug = "m"

        book = {"bids": {0.47: 900.0, 0.48: 50.0}, "asks": {0.49: 10.0},
                "best_bid": 0.48, "best_ask": 0.49}
        _record_queue_marks(db, _Market(), [_Order()], {}, lambda h, t: book,
                            now=100.0, run_id="r", clob_host=None)

        row = read_last_queue_mark(db, "tok", 0.47, run_id="r")
        assert row["best_bid"] == 0.48, "we are one tick behind the touch here"
        assert row["level_size"] == 900.0
