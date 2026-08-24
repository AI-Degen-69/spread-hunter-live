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
