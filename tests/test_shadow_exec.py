"""Shadow submit: the decided couple rests in the shadow store, and only there."""
from __future__ import annotations

import pytest

from core_brain.order_registry import OrderRegistry, init_db
from core_brain.quotes import QuoteIntent


class FakeMarket:
    condition_id = "0xabc"
    up_token = "tok-up"
    down_token = "tok-dn"
    market_slug = "fake-market"
    tick_size = 0.01
    neg_risk = False


def _cfg():
    from core_brain.config import load
    return load()


def _intents():
    return [
        QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20, mid=0.5, edge_vs_mid=0.0),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=20, mid=0.5, edge_vs_mid=0.0),
    ]


def _books(_clob_host, token_id):
    return {"token_id": token_id, "bids": {0.47: 300.0, 0.51: 80.0},
            "asks": {}, "best_bid": 0.47, "best_ask": None}


@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    return OrderRegistry(db_path=db), db


def test_each_leg_becomes_an_open_order_row_labelled_shadow(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    placed = record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                           db_path=db, book_fn=_books)

    assert placed == 2
    rows = reg.get_active_orders()
    assert {r.token_id for r in rows} == {"tok-up", "tok-dn"}
    assert all(r.status == "open" for r in rows)
    assert all((r.order_id or "").startswith("shadow-") for r in rows)


def test_both_legs_share_one_pair_id(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=_books)

    pair_ids = {r.pair_id for r in reg.get_active_orders()}
    assert len(pair_ids) == 1
    assert pair_ids.pop().startswith("pair-")


def test_queue_position_is_captured_from_the_book_at_post_time(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead, record_submit,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=_books)

    by_token = {r.token_id: r.id for r in reg.get_active_orders()}
    assert read_queue_ahead(db, by_token["tok-up"]) == 300.0
    assert read_queue_ahead(db, by_token["tok-dn"]) == 80.0


def test_a_leg_over_max_order_usd_is_refused_before_any_row_is_written(registry):
    from core_brain.shadow_exec import (
        ShadowOrderRefused, ensure_shadow_tables, record_submit,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    huge = [QuoteIntent(side="UP", token_id="tok-up", price=0.90, size=1000, mid=0.5, edge_vs_mid=0.0)]

    with pytest.raises(ShadowOrderRefused, match="MAX_ORDER_USD"):
        record_submit(object(), reg, FakeMarket(), huge, _cfg(),
                      db_path=db, book_fn=_books)

    assert reg.get_active_orders() == []


def test_no_intents_writes_nothing(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    assert record_submit(object(), reg, FakeMarket(), [], _cfg(),
                         db_path=db, book_fn=_books) == 0
    assert reg.get_active_orders() == []


def test_mid_loop_failure_cancels_all_created_rows(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    def failing_book_fn(clob_host, token_id):
        # Succeed for UP leg, fail for DOWN leg
        if token_id == "tok-dn":
            raise RuntimeError("Simulated book fetch failure on DOWN leg")
        return _books(clob_host, token_id)

    with pytest.raises(RuntimeError, match="Simulated book fetch failure"):
        record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                      db_path=db, book_fn=failing_book_fn)

    # No rows should remain in active status (they should all be cancelled)
    assert reg.get_active_orders() == []


def test_oserror_on_second_leg_cancels_first_and_propagates(registry):
    from unittest.mock import patch
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    original_write_queue_ahead = None
    call_count = [0]  # Use list to allow modification in nested function

    def mock_write_queue_ahead(db_path, local_id, queue_ahead):
        call_count[0] += 1
        if call_count[0] == 2:  # Fail on second leg
            raise OSError("Simulated system error in write_queue_ahead")
        # Call original for first leg
        return original_write_queue_ahead(db_path, local_id, queue_ahead)

    import core_brain.shadow_exec
    original_write_queue_ahead = core_brain.shadow_exec.write_queue_ahead

    # OSError from write_queue_ahead should propagate and trigger cancellation
    with patch("core_brain.shadow_exec.write_queue_ahead", side_effect=mock_write_queue_ahead):
        with pytest.raises(OSError, match="Simulated system error"):
            record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                          db_path=db, book_fn=_books)

    # No rows should remain in active status (first order should be cancelled)
    assert reg.get_active_orders() == []


def test_settle_writes_a_fill_row_and_marks_the_order_filled(registry):
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    fills = settle_market(
        reg, FakeMarket(), db_path=db, seen=set(),
        traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
    )

    assert [f.token_id for f in fills] == ["tok-up"]
    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == 20.0
    assert inv.down_shares == 0.0
    assert [r.token_id for r in reg.get_active_orders()] == ["tok-dn"]


def test_partial_credit_leaves_the_order_partial(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 5.0}})

    up_row = next(r for r in reg.get_active_orders() if r.token_id == "tok-up")
    assert up_row.status == "partial"


def test_the_same_tape_volume_is_never_credited_twice(registry):
    """`recent_trades` de-duplicates by trade identity through `seen`; the
    settle step carries one `seen` set per market for the whole session."""
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    calls = {"n": 0}

    def traded_fn(cid, seen):
        calls["n"] += 1
        return {"tok-up": {0.47: 8.0}} if calls["n"] == 1 else {}

    seen = set()
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=traded_fn)
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=traded_fn)

    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == 8.0


def test_a_shrinking_queue_without_a_fill_is_remembered(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db,
                  book_fn=lambda h, t: {"bids": {0.47: 50.0, 0.51: 0.0}})

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 30.0}})

    up_id = next(r.id for r in reg.get_active_orders() if r.token_id == "tok-up")
    assert read_queue_ahead(db, up_id) == 20.0


def test_settle_calls_traded_fn_even_with_no_resting_orders(registry):
    """settle_market always calls traded_fn to keep `seen` current, even when
    there are no resting orders. Without this, a fresh order's first settle
    would credit tape volume from before it was posted."""
    from core_brain.shadow_exec import ensure_shadow_tables, settle_market

    reg, db = registry
    ensure_shadow_tables(db)

    calls = {"n": 0}

    def traded_fn(cid, seen):
        calls["n"] += 1
        return {}

    # Call settle with no resting orders — traded_fn must still be called to
    # update `seen`
    result = settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                           traded_fn=traded_fn)

    assert calls["n"] == 1
    assert result == []


def test_stale_tape_does_not_credit_to_fresh_orders(registry):
    """Sequence: settle with nothing resting (seen is updated), then
    record_submit a new order, then settle again. The second settle must only
    see what traded_fn returns on that call, not the stale volume from the
    first settle."""
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)

    tape_history = {"call": 0}

    def tape_feed(cid, seen):
        tape_history["call"] += 1
        # First call (no orders resting): return stale 50 units
        # Second call (order now resting): return only fresh 10 units
        if tape_history["call"] == 1:
            return {"tok-up": {0.47: 50.0}}
        else:
            return {"tok-up": {0.47: 10.0}}

    seen = set()

    # First settle with no resting orders — seen is updated with the 50 units
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=tape_feed)

    # Record a new order
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    # Second settle with the order now resting — only 10 units are available
    # because the earlier 50 units were already in `seen`
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=tape_feed)

    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == 10.0


def test_a_single_buy_is_completed_against_the_shadow_store(registry):
    """One leg filled, the other not: the pairs pass completes it, and the
    completing buy lands as a shadow fill rather than a venue order.

    tok-up fills 20 at 0.47, tok-dn rests unfilled. With the light ask at
    0.51 the pair costs 0.98 -- under the default max_pair_cost of 0.995 --
    so `auto_manage_pairs` routes this to `complete_pair`, not the exit. That
    makes `completed` the only correct outcome for this fixture; asserting a
    looser `("completed", "exited")` would hide a routing regression.
    """
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market, shadow_positions,
    )
    from core_brain.single_buy_saver import auto_manage_pairs

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    # `markets.parse_book`'s canonical shape -- bids/asks as PRICE-KEYED
    # DICTS -- exactly what `book_fn` returns in production
    # (`shadow_run._default_fetch_books` -> `markets.full_book` ->
    # `markets.parse_book`). `ShadowExecutionClient.get_order_book` is the
    # thing under test that adapts this into the list-of-levels shape
    # `single_buy_saver._book_levels` wants; feeding the client the already-
    # adapted shape here would test nothing about that adaptation.
    def book_fn(_clob_host, token_id):
        return {"token_id": token_id, "bids": {0.50: 500.0},
                "asks": {0.51: 500.0}, "best_bid": 0.50, "best_ask": 0.51,
                "malformed": 0}

    client = ShadowExecutionClient(reg, db, book_fn=book_fn)
    results = auto_manage_pairs(
        client, reg, _cfg(), venue_positions=shadow_positions(reg, db),
    )

    assert results, "auto_manage_pairs produced no result for the naked pair"
    assert results[0]["action"] == "completed", results

    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == pytest.approx(20.0)
    assert inv.down_shares == pytest.approx(20.0)

    # The completion writes a SECOND tok-dn row (status "filled"); the
    # ORIGINAL resting tok-dn row from record_submit is still there too, now
    # "cancelled", and was already `shadow-` labelled before the completion
    # ever ran -- asserting against whichever row `next()` happens to return
    # first (posted_ts ASC, so the original) would pass even if the new
    # completion row's id were unlabelled. Select the completion row
    # explicitly by its status.
    completion_order = next(
        r for r in reg.get_orders_by_pair(results[0]["pair_id"])
        if r.token_id == "tok-dn" and r.status == "filled"
    )
    assert (completion_order.order_id or "").startswith("shadow-")

    completion_fill = next(
        f for f in reg.get_all_fills()
        if f["order_uuid"] == completion_order.id
    )
    assert completion_fill["trade_id"].startswith("shadow-")


def test_completion_refuses_when_pair_has_empty_condition_id(registry):
    """A pair row with a NULL or empty condition_id cannot be completed: the
    condition_id is the key that ties an order to its market, and without it,
    inventory_from_registry has no way to track the position. Accepting an
    empty condition_id would produce a completion row invisible to the pair
    it belongs to.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.order_registry import OrderRecord, FillRecord
    from core_brain.shadow_exec import (
        ShadowExecutionClient, ShadowOrderRefused, ensure_shadow_tables,
    )

    reg, db = registry
    ensure_shadow_tables(db)

    # Hand-craft a pair with empty condition_id in the store.
    pair_id = "pair-empty-cid-test"
    local_id_up = str(__import__("uuid").uuid4())
    local_id_dn = str(__import__("uuid").uuid4())
    now_ms = int(__import__("time").time() * 1000)

    reg.create_order(OrderRecord(
        id=local_id_up,
        order_id="shadow-test-up",
        condition_id="", token_id="tok-up", side="BUY",
        price=0.47, original_size=20, status="open",
        posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id
    ))
    reg.create_order(OrderRecord(
        id=local_id_dn,
        order_id="shadow-test-dn",
        condition_id="", token_id="tok-dn", side="BUY",
        price=0.51, original_size=20, status="open",
        posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id
    ))

    # Record a fill on tok-up to make tok-dn the naked leg.
    reg.record_fill(FillRecord(
        trade_id="shadow-fill-up",
        order_uuid=local_id_up,
        size=20,
        price=0.47
    ))

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {})

    # Attempting to complete tok-dn should raise ShadowOrderRefused.
    with pytest.raises(Exception) as exc_info:
        client.create_and_post_market_order(
            MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                              price=0.51))

    # Verify it's the right exception and mentions the pair and token.
    from core_brain.shadow_exec import ShadowOrderRefused
    assert isinstance(exc_info.value, ShadowOrderRefused)
    assert "tok-dn" in str(exc_info.value) or "pair" in str(exc_info.value).lower()

    # Verify no new order was written to the store.
    all_orders = reg.get_all_orders()
    # Should still be the 2 we created (the tok-up and tok-dn naked pair).
    order_ids = [o["id"] for o in all_orders]
    assert len([oid for oid in order_ids if oid in (local_id_up, local_id_dn)]) == 2
    # Verify no new shadow- order was added.
    new_shadow_orders = [o for o in all_orders if o["id"] not in (local_id_up, local_id_dn)]
    assert len(new_shadow_orders) == 0


def test_a_completion_lands_on_the_older_naked_pair_when_two_pairs_share_a_token(registry):
    """Two pairs post rows on tok-dn, both currently naked; the completion
    under test is for the OLDER one.

    A version of this shim that resolves condition_id/pair_id by "the most
    recent order row on this token" (an earlier version of
    `ShadowExecutionClient.create_and_post_market_order` did exactly this)
    would attach the completion to the NEWER pair instead -- record_submit
    mints a fresh pair_id every call, so a market re-quoted across rotations
    always has one. That leaves the older pair naked, and the pairs pass buys
    it again next cycle: a repeat-buy loop that does not stop until the fill
    ages out of pairs_exit_window_sec.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)

    # Older pair: tok-up filled 20, tok-dn naked (open, unfilled).
    # Use explicit now_fn so posted_ts values differ deterministically.
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: 1000.0)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})
    older_pair_id = next(
        r.pair_id for r in reg.get_active_orders() if r.token_id == "tok-dn")

    # Newer pair on the SAME tokens, posted strictly after: also tok-up
    # filled 20, tok-dn naked. A posted_ts-only lookup on tok-dn finds this
    # one first.
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: 2000.0)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})
    newer_pair_id = next(
        r.pair_id for r in reg.get_active_orders()
        if r.token_id == "tok-dn" and r.pair_id != older_pair_id
    )
    assert newer_pair_id != older_pair_id

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {})
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                          price=0.51))

    completion_order = reg.get_order(resp["orderID"])
    assert completion_order is not None
    assert completion_order.pair_id == older_pair_id
    assert completion_order.pair_id != newer_pair_id


def test_the_shim_refuses_a_method_it_does_not_implement(registry):
    """A silent no-op for an unimplemented SDK call would make a rehearsal look
    successful where the live path would have done something."""
    from core_brain.shadow_exec import ShadowExecutionClient

    reg, db = registry
    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {})

    with pytest.raises(AttributeError):
        client.post_orders([])


def test_a_balanced_pair_is_merged_into_one_dollar_per_share(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}})

    merged = record_shadow_merges(reg, db)

    assert len(merged) == 1
    close = reg.get_all_closes()[0]
    assert close["method"] == "shadow_merge"
    assert close["shares"] == 20.0
    assert round(close["proceeds"], 2) == 20.00
    assert round(close["cost_basis"], 2) == round(20 * (0.47 + 0.51), 2)
    assert round(close["realized_pnl"], 2) == round(20 * (1.0 - 0.98), 2)


def test_an_unbalanced_pair_is_left_alone(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    assert record_shadow_merges(reg, db) == []
    assert reg.get_all_closes() == []


def test_a_merged_pair_is_not_merged_again(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}})

    assert len(record_shadow_merges(reg, db)) == 1
    assert record_shadow_merges(reg, db) == []
    assert len(reg.get_all_closes()) == 1
