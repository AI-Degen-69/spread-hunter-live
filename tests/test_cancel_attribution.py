"""Why an order was cancelled, and the queue it gave up (#131).

On shadow-02 the loop posted 25 orders in 20 minutes, cancelled 23, and filled
none. Median quote lifetime 37 s, minimum 6 s. The best queue position of the
run — a bid with 25 shares ahead of it — was re-quoted away six seconds after it
was posted. Reading the registry afterwards, that cancel is indistinguishable
from one that saved us from a pair over $1.00: `orders` recorded a status and
nothing else.

So every cancel now records its reason and the queue it held. The hold rule is
the payoff, and it ships OFF (`requote_hold_queue_shares = 0.0`) precisely
because choosing the threshold is what the record is for.
"""
from __future__ import annotations

import sqlite3

import pytest

from core_brain.config import MakerConfig
from core_brain.order_registry import SCHEMA, OrderRegistry, OrderRecord
from core_brain.quotes import QuoteIntent
from core_brain.trader_loop import (
    CANCEL_NOT_QUOTED,
    CANCEL_PRICE_MOVED,
    CANCEL_REGATE_PAIR_COST,
    plan_orders,
)

UP = "tok-up"
DOWN = "tok-down"


def _order(order_id: str, token: str, price: float) -> dict:
    return {"id": order_id, "order_id": order_id, "token_id": token,
            "price": price, "side": "BUY"}


def _intent(token: str, price: float) -> QuoteIntent:
    return QuoteIntent(side="BUY", token_id=token, price=price, size=5,
                       mid=price + 0.02, edge_vs_mid=0.02)


# --- attribution ------------------------------------------------------------

def test_a_price_move_is_recorded_as_such():
    # Arrange — the desired price walked outside the band.
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(orders, [_intent(UP, 0.40)], dead_band=0.03,
                               reasons=reasons)

    # Assert
    assert to_cancel == orders
    assert reasons == {"o1": CANCEL_PRICE_MOVED}


def test_a_token_we_no_longer_quote_is_recorded_separately():
    # Arrange — no intent for this token at all. That is a different event
    # from a price move, and conflating them would hide a market dropping out
    # of the universe inside the churn count.
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(orders, [_intent(DOWN, 0.40)], reasons=reasons)

    # Assert
    assert to_cancel == orders
    assert reasons == {"o1": CANCEL_NOT_QUOTED}


def test_a_regate_cancel_is_recorded_as_the_gate_it_was():
    # Arrange — the price is fine, but holding this bid against the other
    # leg's ask would assemble a pair over max_pair_cost.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.60)]
    reasons: dict = {}

    # Act — hedge ask makes the completable pair 0.60 + 0.60 = 1.20.
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.60)], cfg=cfg,
        hedge_asks={UP: 0.60}, reasons=reasons)

    # Assert
    assert to_cancel == orders
    assert reasons == {"o1": CANCEL_REGATE_PAIR_COST}


def test_an_order_that_is_kept_records_no_reason():
    # Arrange
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(orders, [_intent(UP, 0.50)], reasons=reasons)

    # Assert
    assert to_cancel == []
    assert reasons == {}


def test_the_reasons_map_is_optional():
    # Arrange / Act — every existing caller passes nothing and must keep working.
    to_cancel, to_submit = plan_orders([_order("o1", UP, 0.50)],
                                       [_intent(UP, 0.40)])

    # Assert
    assert len(to_cancel) == 1
    assert len(to_submit) == 1


# --- the hold ---------------------------------------------------------------

def test_a_near_front_order_survives_a_price_move():
    # Arrange — 25 shares ahead, the position shadow-02 threw away.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, reasons=reasons,
        queue_ahead={"o1": 25.0}, hold_queue_shares=200.0)

    # Assert — kept, and no cancel reason because there was no cancel.
    assert to_cancel == []
    assert reasons == {}


def test_an_order_deep_in_the_queue_is_still_re_quoted():
    # Arrange — 12,930 shares ahead: holding a stale price buys nothing.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, reasons=reasons,
        queue_ahead={"o1": 12930.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == orders
    assert reasons == {"o1": CANCEL_PRICE_MOVED}


def test_the_hold_never_overrides_the_pair_cost_gate():
    # Arrange — front of the queue, but holding would carry the pair over the
    # cap. Queue position is not worth a booked loss.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.60)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.60}, reasons=reasons,
        queue_ahead={"o1": 1.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == orders


def test_the_hold_stands_down_when_nothing_is_checking_the_economics():
    # Arrange — no cfg/hedge_asks means the re-gate is not armed, so holding a
    # stale price would be an unmeasured bet.
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, reasons=reasons,
        queue_ahead={"o1": 1.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == orders
    assert reasons == {"o1": CANCEL_PRICE_MOVED}


def test_an_unmeasured_queue_is_never_held():
    # Arrange — no entry for this order. An unknown position is not a good one.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, queue_ahead={}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == orders


def test_the_hold_is_off_by_default():
    # Arrange — the shipped config disables it, so behaviour is unchanged until
    # someone chooses a threshold from the recorded evidence.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, queue_ahead={"o1": 1.0},
        hold_queue_shares=cfg.requote_hold_queue_shares)

    # Assert
    assert cfg.requote_hold_queue_shares == 0.0
    assert to_cancel == orders


def test_a_token_we_no_longer_quote_is_never_held():
    # Arrange — front of the queue, but there is nothing left to hold for.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(DOWN, 0.40)], cfg=cfg, hedge_asks={UP: 0.40},
        reasons=reasons, queue_ahead={"o1": 1.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == orders
    assert reasons == {"o1": CANCEL_NOT_QUOTED}


# --- persistence ------------------------------------------------------------

@pytest.fixture
def registry(tmp_path) -> OrderRegistry:
    db = tmp_path / "live.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return OrderRegistry(db)


def _seed_order(registry: OrderRegistry, local_id: str = "o1") -> None:
    registry.create_order(OrderRecord(
        id=local_id, order_id=local_id, condition_id="0xmarket",
        token_id=UP, side="BUY", price=0.50, original_size=5.0,
        status="open", posted_ts=1, last_polled_ts=1, pair_id="pair-1",
        max_pair_cost_at_post=0.99,
    ))


def _row(registry: OrderRegistry, local_id: str = "o1") -> dict:
    con = sqlite3.connect(str(registry.db_path))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT status, cancel_reason, cancel_queue_ahead FROM orders WHERE id = ?",
        (local_id,)).fetchone()
    con.close()
    return dict(row)


def test_the_reason_and_queue_are_persisted_with_the_cancel(registry):
    # Arrange
    _seed_order(registry)

    # Act
    registry.update_order_status("o1", status="cancelled", last_polled_ts=2,
                                 cancel_reason=CANCEL_PRICE_MOVED,
                                 cancel_queue_ahead=25.0)

    # Assert
    row = _row(registry)
    assert row["status"] == "cancelled"
    assert row["cancel_reason"] == CANCEL_PRICE_MOVED
    assert row["cancel_queue_ahead"] == pytest.approx(25.0)


def test_a_later_status_change_does_not_erase_why_it_was_cancelled(registry):
    # Arrange
    _seed_order(registry)
    registry.update_order_status("o1", status="cancelled", last_polled_ts=2,
                                 cancel_reason=CANCEL_REGATE_PAIR_COST,
                                 cancel_queue_ahead=1058.0)

    # Act — a later write that knows nothing about the cancel.
    registry.update_order_status("o1", status="cancelled", last_polled_ts=3)

    # Assert
    row = _row(registry)
    assert row["cancel_reason"] == CANCEL_REGATE_PAIR_COST
    assert row["cancel_queue_ahead"] == pytest.approx(1058.0)


def test_an_order_that_was_never_cancelled_carries_no_reason(registry):
    # Arrange / Act
    _seed_order(registry)

    # Assert
    row = _row(registry)
    assert row["cancel_reason"] is None
    assert row["cancel_queue_ahead"] is None


# --- reading the evidence back ----------------------------------------------

def test_the_report_separates_churn_from_the_gates_doing_their_job(registry):
    # Arrange — three price moves (candidates for the hold), one re-gate and
    # one universe drop (both correct, and not the hold's business).
    from core_brain.cancel_report import format_report, summarize

    for i, (reason, queue) in enumerate([
        (CANCEL_PRICE_MOVED, 25.0),
        (CANCEL_PRICE_MOVED, 1705.0),
        (CANCEL_PRICE_MOVED, 12930.0),
        (CANCEL_REGATE_PAIR_COST, 40.0),
        (CANCEL_NOT_QUOTED, 900.0),
    ]):
        _seed_order(registry, f"o{i}")
        registry.update_order_status(f"o{i}", status="cancelled",
                                     last_polled_ts=2, cancel_reason=reason,
                                     cancel_queue_ahead=queue)

    # Act
    summary = summarize(registry.db_path)

    # Assert
    assert summary["cancelled"] == 5
    assert summary["reasons"][CANCEL_PRICE_MOVED]["count"] == 3
    assert summary["reasons"][CANCEL_PRICE_MOVED]["median_queue_ahead"] == pytest.approx(1705.0)
    assert summary["reasons"][CANCEL_PRICE_MOVED]["min_queue_ahead"] == pytest.approx(25.0)
    assert summary["reasons"][CANCEL_REGATE_PAIR_COST]["count"] == 1

    text = format_report(summary)
    assert "60% of cancels were price moves" in text


def test_a_cancel_with_no_recorded_reason_is_counted_apart(registry):
    # Arrange — rows written before attribution existed. Folding them into a
    # named reason would invent evidence.
    from core_brain.cancel_report import summarize

    _seed_order(registry, "old")
    registry.update_order_status("old", status="cancelled", last_polled_ts=2)

    # Act
    summary = summarize(registry.db_path)

    # Assert
    assert summary["unattributed"] == 1
    assert summary["reasons"] == {}


def test_an_unreadable_registry_is_not_zero_cancels(tmp_path):
    # Arrange / Act
    from core_brain.cancel_report import format_report, summarize

    summary = summarize(tmp_path / "does-not-exist.db")

    # Assert
    assert summary["readable"] is False
    assert "UNREAD" in format_report(summary)
    assert "not zero cancels" in format_report(summary)


def test_a_store_written_before_attribution_counts_its_cancels_anyway(tmp_path):
    # Arrange — the pre-#131 schema: cancels exist, the columns do not.
    from core_brain.cancel_report import summarize

    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT)")
    con.executemany("INSERT INTO orders (id, status) VALUES (?, 'cancelled')",
                    [("a",), ("b",), ("c",)])
    con.commit()
    con.close()

    # Act
    summary = summarize(db)

    # Assert — readable, counted, and honestly unattributed.
    assert summary["readable"] is True
    assert summary["cancelled"] == 3
    assert summary["unattributed"] == 3


def test_a_market_leaving_the_universe_is_its_own_reason():
    # Arrange — `_cancel_dropped_markets` builds the order dicts it hands the
    # canceller. That path is neither churn nor a gate, and a cancel with no
    # recorded reason reads as either.
    from core_brain.trader_loop import CANCEL_MARKET_DROPPED
    import inspect
    import core_brain.trader_loop as tl

    source = inspect.getsource(tl._cancel_dropped_markets)

    # Act / Assert
    assert "cancel_reason" in source
    assert "CANCEL_MARKET_DROPPED" in source
    assert CANCEL_MARKET_DROPPED == "market_dropped"
