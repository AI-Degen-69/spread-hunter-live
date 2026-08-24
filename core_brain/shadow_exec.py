"""The shadow store's writers: rest, fill, and close, without a venue.

Everything here writes `data/shadow.db` through the same `OrderRegistry` the
live path uses, so the rows downstream stages read have the shape those stages
expect. What differs is where the facts come from -- a model instead of a venue
-- and every row says so: order ids start `shadow-`, trade ids start `shadow-`,
closes carry a `shadow_` method.

`core_brain/shadow_guard.py` keeps this honest from the other side: the venue
object a shadow run holds cannot sign, and `data/orders.db` is refused as a
store before any of these functions can be reached.
"""
from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable

from core_brain.order_registry import (
    FillRecord, OrderRecord, OrderRegistry, get_connection,
)
from core_brain.shadow_fills import ShadowRestingOrder, queue_ahead_at

_log = logging.getLogger(__name__)

SHADOW_ORDER_PREFIX = "shadow-"
SHADOW_TRADE_PREFIX = "shadow-"


class ShadowOrderRefused(RuntimeError):
    """A simulated order broke a live cap and was not written."""


def ensure_shadow_tables(db_path: Path | str) -> None:
    """Create the queue-position table beside the registry's own schema.

    Queue position is a property of the model, not of the venue, so it does not
    belong in `orders`. Keeping it in its own table also means a shadow store
    opened by live code reads as a registry with some odd ids, never as a
    registry with columns that do not exist.
    """
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_queue (
                local_id TEXT PRIMARY KEY,
                queue_ahead REAL NOT NULL
            )
            """
        )
        conn.commit()


def write_queue_ahead(db_path: Path | str, local_id: str, queue_ahead: float) -> None:
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            "INSERT INTO shadow_queue (local_id, queue_ahead) VALUES (?, ?) "
            "ON CONFLICT(local_id) DO UPDATE SET queue_ahead = excluded.queue_ahead",
            (local_id, float(queue_ahead)),
        )
        conn.commit()


def read_queue_ahead(db_path: Path | str, local_id: str) -> float:
    with closing(get_connection(Path(db_path))) as conn:
        row = conn.execute(
            "SELECT queue_ahead FROM shadow_queue WHERE local_id = ?", (local_id,)
        ).fetchone()
    return float(row["queue_ahead"]) if row else 0.0


def record_submit(
    _client,
    registry: OrderRegistry,
    market,
    intents: list,
    cfg,
    *,
    db_path: Path | str,
    book_fn: Callable[[str, str], dict],
    clob_host: str = "https://clob.polymarket.com",
    now_fn: Callable[[], float] = time.time,
) -> int:
    """Rest each decided intent as an order row, under the live caps.

    Mirrors `core_brain/trader_loop.py:_submit_intents` in everything that is
    not the venue call: the same per-leg `MAX_ORDER_USD` check, the same shared
    `pair_id`, the same `max_pair_cost_at_post` stamp. A rehearsal under looser
    caps rehearses something we do not ship.
    """
    from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD

    if not intents:
        return 0

    for i in intents:
        if i.price * i.size > MAX_ORDER_USD:
            raise ShadowOrderRefused(
                f"leg {i.side} ${i.price * i.size:.2f} exceeds "
                f"MAX_ORDER_USD ${MAX_ORDER_USD:.2f}")

    open_usd = sum(o.price * o.original_size for o in registry.get_active_orders())
    total_cost = sum(i.price * i.size for i in intents)
    if open_usd + total_cost > MAX_TOTAL_USD:
        raise ShadowOrderRefused(
            f"open ${open_usd:.2f} + ${total_cost:.2f} exceeds "
            f"MAX_TOTAL_USD ${MAX_TOTAL_USD:.2f}")

    now_ms = int(now_fn() * 1000)
    pair_id = f"pair-{uuid.uuid4().hex[:12]}"
    max_pair_cost = float(getattr(cfg, "max_pair_cost", 0.995))

    placed = 0
    created_local_ids: list[str] = []
    try:
        for i in intents:
            local_id = str(uuid.uuid4())
            registry.create_order(OrderRecord(
                id=local_id,
                order_id=f"{SHADOW_ORDER_PREFIX}{uuid.uuid4().hex[:12]}",
                condition_id=market.condition_id, token_id=str(i.token_id),
                side="BUY", price=i.price, original_size=i.size, status="open",
                posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id,
                max_pair_cost_at_post=max_pair_cost,
            ))
            created_local_ids.append(local_id)
            try:
                book = book_fn(clob_host, str(i.token_id))
            except (OSError, ValueError):
                # Degrade, do not stop. An unread book means the queue is unknown,
                # and a queue of zero is the optimistic direction, so assume a level
                # as deep as this order instead: that never over-credits.
                book = {"bids": {round(float(i.price), 4): float(i.size)}}
            write_queue_ahead(db_path, local_id, queue_ahead_at(book, i.price))
            placed += 1
    except Exception:
        # Any exception mid-loop: cancel all created orders and re-raise.
        for local_id in created_local_ids:
            try:
                registry.update_order_status(local_id, status="cancelled", last_polled_ts=now_ms)
            except (KeyError, sqlite3.Error) as e:
                _log.exception(f"Failed to cancel order {local_id}: {e}")
        raise

    return placed


def _filled_size(db_path: Path | str, local_id: str) -> float:
    with closing(get_connection(Path(db_path))) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS s FROM fills WHERE order_uuid = ?",
            (local_id,),
        ).fetchone()
    return float(row["s"]) if row else 0.0


def settle_market(
    registry: OrderRegistry,
    market,
    *,
    db_path: Path | str,
    traded_fn: Callable[[str, set], dict],
    seen: set,
    now_fn: Callable[[], float] = time.time,
) -> list:
    """Credit this market's resting rows from the tape, then persist the result.

    One `seen` set per market for the whole session: `markets.recent_trades`
    de-duplicates by trade identity against it, and a fresh set every cycle
    would re-credit the same trades until the order looked full.
    """
    from core_brain.shadow_fills import credit_fills

    resting = [
        o for o in registry.get_active_orders()
        if o.condition_id == market.condition_id and o.status in ("open", "partial")
    ]
    if not resting:
        return []

    traded = traded_fn(market.condition_id, seen)

    orders = [
        ShadowRestingOrder(
            local_id=o.id, token_id=o.token_id, price=o.price,
            size=o.original_size, filled=_filled_size(db_path, o.id),
            queue_ahead=read_queue_ahead(db_path, o.id),
        )
        for o in resting
    ]
    fills, queues = credit_fills(orders, traded)

    for local_id, queue_ahead in queues.items():
        write_queue_ahead(db_path, local_id, queue_ahead)

    now_ms = int(now_fn() * 1000)
    by_id = {o.local_id: o for o in orders}
    for f in fills:
        registry.record_fill(FillRecord(
            trade_id=f"{SHADOW_TRADE_PREFIX}{uuid.uuid4().hex[:16]}",
            order_uuid=f.local_id, size=f.size, price=f.price,
            venue_ts=now_ms, recorded_ts=now_ms,
        ))
        order = by_id[f.local_id]
        total_filled = order.filled + f.size
        status = "filled" if total_filled >= order.size - 1e-9 else "partial"
        registry.update_order_status(f.local_id, status=status,
                                     last_polled_ts=now_ms)

    return fills
