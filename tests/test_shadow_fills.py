"""The shadow fill model: what a resting order would have got, and nothing more.

Conservative by construction. Only volume the tape confirms at the order's own
price can credit a fill; the book-only rule ("level emptied, credit the
remainder") reported a 50% fill rate against a tape-confirmed 3% in the paper
run -- see `core_brain/markets.py:recent_trades`.
"""
from __future__ import annotations

from core_brain.shadow_fills import (
    ShadowFill, ShadowRestingOrder, credit_fills, queue_ahead_at,
)


def _order(**kw):
    base = dict(local_id="ord-1", token_id="tok-up", price=0.47,
                size=100.0, filled=0.0, queue_ahead=0.0)
    base.update(kw)
    return ShadowRestingOrder(**base)


def test_queue_ahead_is_the_size_resting_at_our_own_price():
    book = {"bids": {0.48: 500.0, 0.47: 250.0, 0.46: 10.0}, "asks": {}}
    assert queue_ahead_at(book, 0.47) == 250.0


def test_queue_ahead_is_zero_when_no_one_rests_at_our_price():
    book = {"bids": {0.48: 500.0}, "asks": {}}
    assert queue_ahead_at(book, 0.47) == 0.0


def test_traded_volume_fills_the_queue_before_it_fills_us():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.47: 100.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 40.0)]
    assert queues["ord-1"] == 0.0


def test_volume_smaller_than_the_queue_credits_nothing():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.47: 25.0}})

    assert fills == []
    assert queues["ord-1"] == 35.0


def test_a_fill_never_exceeds_what_is_left_of_the_order():
    orders = [_order(size=100.0, filled=90.0)]
    fills, _ = credit_fills(orders, {"tok-up": {0.47: 500.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 10.0)]


def test_volume_at_another_price_or_token_credits_nothing():
    orders = [_order()]
    fills, _ = credit_fills(orders, {"tok-up": {0.46: 999.0},
                                     "tok-dn": {0.47: 999.0}})

    assert fills == []


def test_two_orders_at_one_price_share_the_volume_in_post_order():
    """Earlier order is earlier in the queue. Splitting evenly would credit the
    younger order volume the older one stood in front of."""
    orders = [_order(local_id="old", size=50.0),
              _order(local_id="new", size=50.0)]
    fills, _ = credit_fills(orders, {"tok-up": {0.47: 70.0}})

    assert fills == [ShadowFill("old", "tok-up", 0.47, 50.0),
                     ShadowFill("new", "tok-up", 0.47, 20.0)]
