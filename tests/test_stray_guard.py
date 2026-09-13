"""Tests for core_brain/stray_guard.py — Stray-order guard (Issue #205)."""
from __future__ import annotations

import time
import pytest
from core_brain.order_registry import OrderRecord
from core_brain.stray_guard import (
    StrayClassificationType,
    ClassifiedOrder,
    classify_market_orders,
)


def make_order(
    local_id: str,
    condition_id: str,
    token_id: str,
    price: float,
    size: float = 5.0,
    side: str = "BUY",
    status: str = "open",
    pair_id: str | None = None,
    order_id: str | None = None,
) -> OrderRecord:
    now_ms = int(time.time() * 1000)
    return OrderRecord(
        id=local_id,
        condition_id=condition_id,
        token_id=token_id,
        side=side,
        price=price,
        original_size=size,
        status=status,
        posted_ts=now_ms,
        last_polled_ts=now_ms,
        pair_id=pair_id,
        order_id=order_id or f"venue-{local_id}",
    )


def test_classify_registry_paired():
    """Two orders on same market with same pair_id and different tokens are REGISTRY_PAIRED."""
    cond = "0xcond1"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.70, pair_id="pair-abc")
    o2 = make_order("o2", cond, tok_dn, 0.25, pair_id="pair-abc")

    res = classify_market_orders(
        orders=[o1, o2],
        books={},
        max_pair_cost=0.99,
    )

    assert len(res.registry_paired) == 1
    pair_id, legs = res.registry_paired[0]
    assert pair_id == "pair-abc"
    assert {legs[0].id, legs[1].id} == {"o1", "o2"}
    assert len(res.complementary_detached) == 0
    assert len(res.hopeless_strays) == 0


def test_classify_complementary_detached():
    """Two orders on same market with different pair_ids summing <= max_pair_cost are COMPLEMENTARY_DETACHED."""
    cond = "0xcond1"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.73, pair_id="pair-1")
    o2 = make_order("o2", cond, tok_dn, 0.23, pair_id="pair-2")

    res = classify_market_orders(
        orders=[o1, o2],
        books={},
        max_pair_cost=0.99,
    )

    assert len(res.registry_paired) == 0
    assert len(res.complementary_detached) == 1
    detached = res.complementary_detached[0]
    assert {detached.leg1.id, detached.leg2.id} == {"o1", "o2"}
    assert detached.combined_cost == pytest.approx(0.96)
    assert len(res.hopeless_strays) == 0


def test_classify_hopeless_stray_when_price_and_ask_exceed_cap():
    """Lone resting order whose price + opposing best ask >= max_pair_cost is a HOPELESS_STRAY."""
    cond = "0xcond1"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.75, pair_id="pair-lone")

    # Opposing token ask is 0.26 -> 0.75 + 0.26 = 1.01 >= 0.99
    books = {
        tok_dn: {"asks": [[0.26, 100.0]]}
    }

    res = classify_market_orders(
        orders=[o1],
        books=books,
        max_pair_cost=0.99,
        market_tokens={cond: (tok_up, tok_dn)},
    )

    assert len(res.registry_paired) == 0
    assert len(res.complementary_detached) == 0
    assert len(res.hopeless_strays) == 1
    stray = res.hopeless_strays[0]
    assert stray.order.id == "o1"
    assert "cost_exceeds_cap" in stray.reason


def test_classify_viable_lone_order_not_hopeless():
    """Lone resting order whose price + opposing best ask < max_pair_cost is viable (not hopeless)."""
    cond = "0xcond1"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.70, pair_id="pair-lone")

    # Opposing token ask is 0.24 -> 0.70 + 0.24 = 0.94 < 0.99
    books = {
        tok_dn: {"asks": [[0.24, 100.0]]}
    }

    res = classify_market_orders(
        orders=[o1],
        books=books,
        max_pair_cost=0.99,
        market_tokens={cond: (tok_up, tok_dn)},
    )

    assert len(res.registry_paired) == 0
    assert len(res.complementary_detached) == 0
    assert len(res.hopeless_strays) == 0
    assert len(res.viable_strays) == 1
