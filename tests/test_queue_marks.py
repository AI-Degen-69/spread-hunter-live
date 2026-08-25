"""Per-cycle recording of what the queue at our own price is actually doing.

Two numbers a shadow run has never captured, both free from data the loop
already fetches every cycle:

  * `queue_minutes` -- resting size at our level divided by the observed rate
    of trading at that same level. Session B of run-2809a7161de1 implied 686
    minutes at best across 10 markets, but that was reconstructed afterwards
    from queue drain; nothing recorded it live.
  * `cancel_decay` -- the shares that left our level WITHOUT trading. The fill
    model credits queue progress only from the tape, so it is blind to the
    mechanism most likely to move a maker up a 13,000-share queue: the orders
    ahead of it cancelling. Three zero-fill rehearsals produced no information
    about it at all.

Recording is opt-in through `book_fn`. A caller that does not pass one keeps
the previous behaviour exactly, which is what every existing test relies on.
"""
from __future__ import annotations

import sqlite3

import pytest

from core_brain.shadow_exec import (
    ensure_shadow_tables, read_last_queue_mark, settle_market, write_queue_mark,
)


class FakeMarket:
    condition_id = "0xabc"
    up_token = "tok-up"
    down_token = "tok-dn"
    market_slug = "fake-market"


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM queue_marks ORDER BY id")]
    finally:
        conn.close()


class TestQueueMarkStore:
    def test_a_mark_round_trips(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        write_queue_mark(db, ts=100.0, condition_id="0xabc",
                         market_slug="m", token_id="tok", price=0.47,
                         level_size=1000.0, traded=0.0,
                         cancel_decay=None, queue_minutes=None)
        got = read_last_queue_mark(db, "tok", 0.47)
        assert got["level_size"] == 1000.0
        assert got["ts"] == 100.0

    def test_no_previous_mark_reads_as_none(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        assert read_last_queue_mark(db, "tok", 0.47) is None

    def test_a_level_is_keyed_by_price_not_only_token(self, tmp_path):
        # Two levels on one token are two separate queues.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        for price, size in ((0.47, 1000.0), (0.48, 25.0)):
            write_queue_mark(db, ts=100.0, condition_id="0xabc",
                             market_slug="m", token_id="tok", price=price,
                             level_size=size, traded=0.0,
                             cancel_decay=None, queue_minutes=None)
        assert read_last_queue_mark(db, "tok", 0.47)["level_size"] == 1000.0
        assert read_last_queue_mark(db, "tok", 0.48)["level_size"] == 25.0


class TestSettleMarketRecords:
    """The recorder runs where the tape is, because the tape is consumed once.

    `markets.recent_trades` de-duplicates against a per-market `seen` set, so a
    second call in the same cycle returns nothing. The volume at our price can
    therefore only be observed inside `settle_market`.
    """

    def _registry_with_order(self, tmp_path, price=0.47, size=20.0):
        from core_brain.order_registry import OrderRecord, OrderRegistry

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        reg = OrderRegistry(db_path=db, run_id="shadow-testaaaaaaa")
        reg.create_order(OrderRecord(
            id="local-1", order_id="oid-1", condition_id="0xabc",
            token_id="tok-up", side="BUY", price=price, original_size=size,
            status="open", posted_ts=1, last_polled_ts=1))
        return reg, db

    def _book(self, level_size):
        def book_fn(_host, token_id):
            return {"token_id": token_id, "best_bid": 0.47, "best_ask": 0.49,
                    "bids": {0.47: level_size}, "asks": {0.49: 100.0}}
        return book_fn

    def test_nothing_is_recorded_without_a_book_source(self, tmp_path):
        reg, db = self._registry_with_order(tmp_path)
        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 100.0)
        assert _rows(db) == []

    def test_the_first_cycle_records_the_level_with_no_decay_yet(self, tmp_path):
        reg, db = self._registry_with_order(tmp_path)
        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 100.0, book_fn=self._book(1000.0))
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0]["level_size"] == 1000.0
        assert rows[0]["token_id"] == "tok-up"
        assert rows[0]["price"] == 0.47
        # Nothing to difference against yet -- a first mark that claimed zero
        # decay would understate the daily total by one cycle per level.
        assert rows[0]["cancel_decay"] is None
        assert rows[0]["queue_minutes"] is None

    def test_the_second_cycle_splits_the_drop_into_trades_and_cancels(
            self, tmp_path):
        reg, db = self._registry_with_order(tmp_path)
        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 100.0, book_fn=self._book(1000.0))
        # 60 seconds later the level is 400. The tape explains 250 of the 600
        # that left; the other 350 cancelled.
        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {"tok-up": {0.47: 250.0}},
                      seen=set(), now_fn=lambda: 160.0,
                      book_fn=self._book(400.0))
        rows = _rows(db)
        assert len(rows) == 2
        assert rows[1]["traded"] == 250.0
        assert rows[1]["cancel_decay"] == 350.0

    def test_the_second_cycle_records_time_to_clear_at_the_observed_rate(
            self, tmp_path):
        reg, db = self._registry_with_order(tmp_path)
        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 100.0, book_fn=self._book(1000.0))
        # 250 shares in 60 seconds = 250/min. 400 resting clears in 1.6 min.
        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {"tok-up": {0.47: 250.0}},
                      seen=set(), now_fn=lambda: 160.0,
                      book_fn=self._book(400.0))
        assert _rows(db)[1]["queue_minutes"] == pytest.approx(1.6)

    def test_a_level_that_never_trades_records_an_unmeasurable_clear_time(
            self, tmp_path):
        # Four of session B's ten markets were in this state. It must record as
        # unmeasurable, never as zero or as missing.
        reg, db = self._registry_with_order(tmp_path)
        for ts in (100.0, 160.0):
            settle_market(reg, FakeMarket(), db_path=db,
                          traded_fn=lambda cid, seen: {}, seen=set(),
                          now_fn=lambda t=ts: t, book_fn=self._book(1000.0))
        assert _rows(db)[1]["queue_minutes"] == float("inf")

    def test_a_level_that_grew_records_negative_decay(self, tmp_path):
        # Size joining behind us is real information; clamping it at zero would
        # make the daily total read as pure decay.
        reg, db = self._registry_with_order(tmp_path)
        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 100.0, book_fn=self._book(1000.0))
        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 160.0, book_fn=self._book(1200.0))
        assert _rows(db)[1]["cancel_decay"] == -200.0

    def test_a_book_read_that_fails_does_not_stop_the_cycle(self, tmp_path):
        # Recording is telemetry. It must degrade, never take the loop down.
        reg, db = self._registry_with_order(tmp_path)

        def exploding_book(_host, _token):
            raise RuntimeError("venue down")

        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 100.0, book_fn=exploding_book)
        assert _rows(db) == []


class TestMarksAreScopedToTheirRun:
    """A reused shadow store must not difference across runs.

    `data/shadow.db` is reused between rehearsals by design. Without this, the
    first cycle of a new run differences its level against the LAST cycle of an
    older run -- hours or days earlier -- and writes a `cancel_decay` and a
    `queue_minutes` computed across that gap. Both are fiction, and both land
    in the first rows an operator reads.
    """

    def test_the_previous_mark_is_looked_up_within_one_run(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        write_queue_mark(db, ts=100.0, condition_id="0xabc", market_slug="m",
                         token_id="tok", price=0.47, level_size=9000.0,
                         traded=0.0, cancel_decay=None, queue_minutes=None,
                         run_id="shadow-old")
        write_queue_mark(db, ts=200.0, condition_id="0xabc", market_slug="m",
                         token_id="tok", price=0.47, level_size=1000.0,
                         traded=0.0, cancel_decay=None, queue_minutes=None,
                         run_id="shadow-new")

        assert read_last_queue_mark(db, "tok", 0.47,
                                    run_id="shadow-new")["level_size"] == 1000.0
        assert read_last_queue_mark(db, "tok", 0.47,
                                    run_id="shadow-old")["level_size"] == 9000.0

    def test_a_new_run_has_no_previous_mark_of_its_own(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        write_queue_mark(db, ts=100.0, condition_id="0xabc", market_slug="m",
                         token_id="tok", price=0.47, level_size=9000.0,
                         traded=0.0, cancel_decay=None, queue_minutes=None,
                         run_id="shadow-old")
        assert read_last_queue_mark(db, "tok", 0.47,
                                    run_id="shadow-new") is None

    def test_a_reused_store_does_not_invent_decay_across_runs(self, tmp_path):
        """End to end: the older run's 9000-share level must not appear as
        8000 shares of decay in the new run's first cycle."""
        from core_brain.order_registry import OrderRecord, OrderRegistry

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        write_queue_mark(db, ts=1.0, condition_id="0xabc", market_slug="m",
                         token_id="tok-up", price=0.47, level_size=9000.0,
                         traded=0.0, cancel_decay=None, queue_minutes=None,
                         run_id="shadow-old")

        reg = OrderRegistry(db_path=db, run_id="shadow-new")
        reg.create_order(OrderRecord(
            id="local-1", order_id="oid-1", condition_id="0xabc",
            token_id="tok-up", side="BUY", price=0.47, original_size=20.0,
            status="open", posted_ts=1, last_polled_ts=1))

        def book_fn(_host, token_id):
            return {"token_id": token_id, "best_bid": 0.47, "best_ask": 0.49,
                    "bids": {0.47: 1000.0}, "asks": {0.49: 100.0}}

        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 500.0, book_fn=book_fn)

        fresh = [r for r in _rows(db) if r["run_id"] == "shadow-new"]
        assert len(fresh) == 1
        assert fresh[0]["cancel_decay"] is None, (
            "differenced against a previous run's mark")
        assert fresh[0]["queue_minutes"] is None
