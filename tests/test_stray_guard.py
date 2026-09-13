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


def test_adopt_detached_legs_idempotent(tmp_path):
    """Adopting detached complementary legs welds them under one pair_id in registry."""
    from core_brain.order_registry import OrderRegistry
    from core_brain.single_buy_saver import load_pair
    from core_brain.stray_guard import adopt_detached_legs

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    cond = "0xcond_adopt"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.70, size=5.0, pair_id="pair-stray-1")
    o2 = make_order("o2", cond, tok_dn, 0.25, size=5.0, pair_id="pair-stray-2")
    reg.create_order(o1)
    reg.create_order(o2)

    active = reg.get_active_orders()
    res = classify_market_orders(active, books={}, max_pair_cost=0.99)
    assert len(res.complementary_detached) == 1

    # Adopt
    adopted = adopt_detached_legs(reg, res.complementary_detached)
    assert len(adopted) == 1
    shared_pid = adopted[0]
    assert shared_pid in ("pair-stray-1", "pair-stray-2")

    # Verify both orders in registry now share the same pair_id
    o1_fresh = reg.get_order("o1")
    o2_fresh = reg.get_order("o2")
    assert o1_fresh is not None and o2_fresh is not None
    assert o1_fresh.pair_id == shared_pid
    assert o2_fresh.pair_id == shared_pid

    # Verify single_buy_saver.load_pair sees both legs
    pair = load_pair(reg, shared_pid)
    assert len(pair["legs"]) == 2
    assert pair["condition_id"] == cond

    # Re-classification must be idempotent (now registry_paired, 0 detached)
    active_after = reg.get_active_orders()
    res_after = classify_market_orders(active_after, books={}, max_pair_cost=0.99)
    assert len(res_after.registry_paired) == 1
    assert len(res_after.complementary_detached) == 0

    # Re-adopting does nothing
    adopted_again = adopt_detached_legs(reg, res_after.complementary_detached)
    assert len(adopted_again) == 0


class MockCancelClient:
    def __init__(self):
        self.cancelled_ids = []

    def cancel_order(self, payload_or_id):
        target = getattr(payload_or_id, "orderID", None) or payload_or_id
        self.cancelled_ids.append(str(target))
        return {"success": True, "canceled": [str(target)]}


def test_cancel_hopeless_orders_dry_run_and_live(tmp_path):
    """Hopeless orders are reported in dry-run, and cancelled on venue and registry in live mode."""
    from core_brain.order_registry import OrderRegistry
    from core_brain.stray_guard import cancel_hopeless_orders

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    cond = "0xcond_hopeless"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.78, order_id="v-o1", pair_id="pair-lone")
    reg.create_order(o1)

    books = {tok_dn: {"asks": [[0.25, 100.0]]}}  # 0.78 + 0.25 = 1.03 >= 0.99
    res = classify_market_orders(
        reg.get_active_orders(),
        books=books,
        max_pair_cost=0.99,
        market_tokens={cond: (tok_up, tok_dn)},
    )
    assert len(res.hopeless_strays) == 1

    mock_client = MockCancelClient()

    # 1. Dry run: live=False
    actions_dry = cancel_hopeless_orders(mock_client, reg, res.hopeless_strays, live=False)
    assert len(actions_dry) == 1
    assert actions_dry[0]["action"] == "would_cancel"
    assert len(mock_client.cancelled_ids) == 0
    assert reg.get_order("o1").status == "open"

    # 2. Live run: live=True
    actions_live = cancel_hopeless_orders(mock_client, reg, res.hopeless_strays, live=True)
    assert len(actions_live) == 1
    assert actions_live[0]["action"] == "cancelled"
    assert mock_client.cancelled_ids == ["v-o1"]

    o1_fresh = reg.get_order("o1")
    assert o1_fresh.status == "cancelled"
    assert "hopeless_stray" in (o1_fresh.cancel_reason or "")


