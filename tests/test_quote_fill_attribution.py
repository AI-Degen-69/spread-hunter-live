"""A quote row must learn what became of the order it posted.

`quotes` carries `filled`, `fill_ts` and `cancelled`, and every fill-rate
number the KPI layer and the dashboard report is derived from them -- including
the fill-rate-by-queue-depth bucketing that decides whether a maker strategy is
reaching the front of any queue.

Those three columns were written once, at post time, and never updated.
`OrderRegistry.update_quote_fill` existed with no production caller, so across
every database in this repo `quotes.filled` is 0 -- including a live run
carrying 93 recorded fills, all 93 of which join to a quote row on
`fills.order_uuid = quotes.local_id`. Every fill-rate readout was a flat zero
that no amount of running the bot could move.

Attribution belongs in the registry rather than in each caller: `order_manager`,
`trader_loop` and `shadow_exec` all log quotes, and a rule enforced in one of
three places is a rule that drifts.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from core_brain.order_registry import (
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
)


def _registry(tmp_path):
    return OrderRegistry(tmp_path / "orders.db")


def _post(reg, *, local_id: str, price: float = 0.47, size: float = 5.0):
    """Post one order and its quote row the way the live paths do."""
    reg.create_order(
        OrderRecord(
            id=local_id,
            order_id=f"0x{local_id[:8]}",
            condition_id="0xcond",
            token_id="tok-1",
            side="BUY",
            price=price,
            original_size=size,
            status="open",
            posted_ts=int(time.time() * 1000),
            last_polled_ts=int(time.time() * 1000),
            pair_id="pair-1",
        )
    )
    reg.log_quote(
        QuoteRecord(
            ts=time.time(),
            condition_id="0xcond",
            token_id="tok-1",
            side="BUY",
            price=price,
            size=size,
            order_id=f"0x{local_id[:8]}",
            local_id=local_id,
            queue_ahead=1200.0,
        )
    )


def _quote(reg, local_id: str) -> sqlite3.Row:
    rows = [q for q in reg.get_all_quotes() if q["local_id"] == local_id]
    assert len(rows) == 1, f"expected one quote for {local_id}, got {len(rows)}"
    return rows[0]


def test_recording_a_fill_marks_the_quote_filled(tmp_path):
    # Arrange
    reg = _registry(tmp_path)
    _post(reg, local_id="order-a", size=5.0)

    # Act
    reg.record_fill(
        FillRecord(trade_id="t1", order_uuid="order-a", size=5.0,
                   price=0.47, venue_ts=1700000000000)
    )

    # Assert
    q = _quote(reg, "order-a")
    assert q["filled"] == pytest.approx(5.0)
    assert q["fill_ts"] is not None


def test_partial_fills_accumulate_on_the_quote(tmp_path):
    # Arrange
    reg = _registry(tmp_path)
    _post(reg, local_id="order-b", size=10.0)

    # Act — two partials against the same order.
    reg.record_fill(
        FillRecord(trade_id="t1", order_uuid="order-b", size=4.0,
                   price=0.47, venue_ts=1700000000000)
    )
    reg.record_fill(
        FillRecord(trade_id="t2", order_uuid="order-b", size=3.0,
                   price=0.47, venue_ts=1700000005000)
    )

    # Assert — the quote carries the running total, not the last leg.
    q = _quote(reg, "order-b")
    assert q["filled"] == pytest.approx(7.0)


def test_the_first_fill_timestamp_is_kept_not_the_latest(tmp_path):
    # Arrange
    reg = _registry(tmp_path)
    _post(reg, local_id="order-c", size=10.0)

    # Act
    reg.record_fill(
        FillRecord(trade_id="t1", order_uuid="order-c", size=4.0,
                   price=0.47, venue_ts=1700000000000)
    )
    reg.record_fill(
        FillRecord(trade_id="t2", order_uuid="order-c", size=3.0,
                   price=0.47, venue_ts=1700000009000)
    )

    # Assert — time-to-fill is measured from when the queue first cleared.
    q = _quote(reg, "order-c")
    assert q["fill_ts"] == pytest.approx(1700000000000)


def test_a_replayed_fill_still_attributes_an_unattributed_quote(tmp_path):
    """The case that leaves historical databases stuck at zero.

    Every fill already persisted before attribution existed sits behind the
    `trade_id` idempotency check. If the duplicate path returns before
    attributing, those quotes stay at `filled = 0` for good, and re-running the
    bot cannot repair them because the venue never replays an old trade twice.
    """
    # Arrange — a fill row that exists while its quote still reads unfilled,
    # exactly the shape of every database written before this change.
    reg = _registry(tmp_path)
    _post(reg, local_id="order-h", size=5.0)
    fill = FillRecord(trade_id="t-replay", order_uuid="order-h", size=5.0,
                      price=0.47, venue_ts=1700000000000)
    assert reg.record_fill(fill) is True
    with sqlite3.connect(tmp_path / "orders.db") as raw:
        raw.execute("UPDATE quotes SET filled = 0, fill_ts = NULL")

    # Act — the venue replays the same trade.
    assert reg.record_fill(fill) is False

    # Assert — the duplicate is still refused, but the quote is repaired.
    q = _quote(reg, "order-h")
    assert q["filled"] == pytest.approx(5.0)
    assert q["fill_ts"] == pytest.approx(1700000000000)


def test_a_replay_naming_a_different_order_attributes_the_stored_one(tmp_path):
    """A mismatched replay must not recompute an unrelated order's quote.

    `trade_id` is the identity in `fills`. A replay that carries the same
    `trade_id` under a different `order_uuid` is a mismatched report, and
    trusting the replayed value would leave the order that actually holds the
    fill stale while rewriting one that does not.
    """
    # Arrange — A holds the fill; B never traded.
    reg = _registry(tmp_path)
    _post(reg, local_id="order-A", size=5.0)
    _post(reg, local_id="order-B", size=5.0)
    reg.record_fill(
        FillRecord(trade_id="t-shared", order_uuid="order-A", size=5.0,
                   price=0.47, venue_ts=1700000000000)
    )
    with sqlite3.connect(tmp_path / "orders.db") as raw:
        raw.execute("UPDATE quotes SET filled = 0, fill_ts = NULL")

    # Act — the same trade replayed against the wrong order.
    assert reg.record_fill(
        FillRecord(trade_id="t-shared", order_uuid="order-B", size=5.0,
                   price=0.47, venue_ts=1700000000000)
    ) is False

    # Assert — A is repaired, B is untouched.
    assert _quote(reg, "order-A")["filled"] == pytest.approx(5.0)
    assert _quote(reg, "order-B")["filled"] == pytest.approx(0.0)


def test_opening_a_legacy_database_backfills_its_quotes(tmp_path):
    """A database written before attribution existed repairs itself on open.

    `data/orders.db` and every archived run hold fills whose quotes read zero.
    Nothing will ever replay those trades, so without a backfill the historical
    fill rate stays permanently invisible.
    """
    # Arrange — build the legacy shape, then blank the attribution the way
    # every pre-existing database already is.
    db = tmp_path / "orders.db"
    reg = _registry(tmp_path)
    _post(reg, local_id="order-i", size=5.0)
    reg.record_fill(
        FillRecord(trade_id="t-old", order_uuid="order-i", size=5.0,
                   price=0.47, venue_ts=1700000000000)
    )
    with sqlite3.connect(db) as raw:
        raw.execute("UPDATE quotes SET filled = 0, fill_ts = NULL")
        assert raw.execute(
            "SELECT COUNT(*) FROM quotes WHERE filled > 0").fetchone()[0] == 0

    # Act — simply opening the registry again.
    reopened = OrderRegistry(db)

    # Assert
    q = _quote(reopened, "order-i")
    assert q["filled"] == pytest.approx(5.0)
    assert q["fill_ts"] == pytest.approx(1700000000000)


def test_the_backfill_repairs_only_the_quotes_that_actually_filled(tmp_path):
    """Both halves of the backfill's contract, in one database.

    Asserting the fill-less quote alone would pass without the backfill --
    `filled` starts at 0 and `fill_ts` at NULL either way. Pairing it with a
    quote that must be repaired is what makes the case depend on the change,
    while still proving an empty aggregate is never written as a 0.0.
    """
    # Arrange — one quote with a historical fill, one with none, then blank
    # the attribution the way every pre-existing database already is.
    db = tmp_path / "orders.db"
    reg = _registry(tmp_path)
    _post(reg, local_id="order-filled", size=5.0)
    _post(reg, local_id="order-j", size=5.0)
    reg.record_fill(
        FillRecord(trade_id="t-hist", order_uuid="order-filled", size=5.0,
                   price=0.47, venue_ts=1700000000000)
    )
    with sqlite3.connect(db) as raw:
        raw.execute("UPDATE quotes SET filled = 0, fill_ts = NULL")

    # Act
    reopened = OrderRegistry(db)

    # Assert — the one with fills is repaired...
    repaired = _quote(reopened, "order-filled")
    assert repaired["filled"] == pytest.approx(5.0)
    assert repaired["fill_ts"] == pytest.approx(1700000000000)

    # ...and the one without is left alone rather than handed the 0.0 an empty
    # aggregate returns.
    untouched = _quote(reopened, "order-j")
    assert untouched["filled"] == pytest.approx(0.0)
    assert untouched["fill_ts"] is None


def test_a_duplicate_fill_does_not_double_count(tmp_path):
    # Arrange
    reg = _registry(tmp_path)
    _post(reg, local_id="order-d", size=5.0)
    fill = FillRecord(trade_id="t1", order_uuid="order-d", size=5.0,
                      price=0.47, venue_ts=1700000000000)

    # Act — the venue replays a trade; record_fill is idempotent by trade_id.
    assert reg.record_fill(fill) is True
    assert reg.record_fill(fill) is False

    # Assert
    q = _quote(reg, "order-d")
    assert q["filled"] == pytest.approx(5.0)


def test_cancelling_an_order_marks_its_quote_cancelled(tmp_path):
    # Arrange
    reg = _registry(tmp_path)
    _post(reg, local_id="order-e")

    # Act
    reg.update_order_status("order-e", "cancelled", int(time.time() * 1000))

    # Assert
    q = _quote(reg, "order-e")
    assert q["cancelled"] == 1


def test_an_unattributed_order_marks_its_quote_cancelled(tmp_path):
    # Arrange — `unattributed` is the second terminal-unfilled status: an
    # order the venue never acknowledged, which never traded either.
    reg = _registry(tmp_path)
    _post(reg, local_id="order-k")

    # Act
    reg.update_order_status("order-k", "unattributed", int(time.time() * 1000))

    # Assert
    q = _quote(reg, "order-k")
    assert q["cancelled"] == 1


def test_a_non_terminal_status_leaves_the_quote_open(tmp_path):
    # Arrange
    reg = _registry(tmp_path)
    _post(reg, local_id="order-f")

    # Act
    reg.update_order_status("order-f", "partial", int(time.time() * 1000))

    # Assert — only a cancel closes the quote out.
    q = _quote(reg, "order-f")
    assert q["cancelled"] == 0


def test_a_fill_for_an_order_with_no_quote_row_is_not_an_error(tmp_path):
    # Arrange — the completer crosses the missing leg without ever resting a
    # quote, so there is no quote row to attribute the fill to.
    reg = _registry(tmp_path)
    reg.create_order(
        OrderRecord(
            id="order-g", order_id="0xdeadbeef", condition_id="0xcond",
            token_id="tok-2", side="BUY", price=0.53, original_size=5.0,
            status="open", posted_ts=int(time.time() * 1000),
            last_polled_ts=int(time.time() * 1000), pair_id="pair-1",
        )
    )

    # Act / Assert — recording the fill still succeeds.
    assert reg.record_fill(
        FillRecord(trade_id="t9", order_uuid="order-g", size=5.0,
                   price=0.53, venue_ts=1700000000000)
    ) is True
