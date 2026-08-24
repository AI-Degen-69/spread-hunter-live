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
from core_brain.shadow_fills import (
    ShadowFill, ShadowRestingOrder, credit_fills, queue_ahead_at,
)

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
) -> list[ShadowFill]:
    """Credit this market's resting rows from the tape, then persist the result.

    One `seen` set per market for the whole session: `markets.recent_trades`
    de-duplicates by trade identity against it, and a fresh set every cycle
    would re-credit the same trades until the order looked full.
    """
    traded = traded_fn(market.condition_id, seen)

    resting = [
        o for o in registry.get_active_orders()
        if o.condition_id == market.condition_id and o.status in ("open", "partial")
    ]
    if not resting:
        return []

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


class ShadowExecutionClient:
    """The four client methods `single_buy_saver` calls, backed by the store.

    Deliberately narrow: an SDK method this class does not implement raises
    `AttributeError` at the call site -- ordinary Python attribute lookup on a
    class that never defined it -- which is a loud failure rather than a
    silent no-op that would make a rehearsal look successful.

    A market order here is a fill, immediately, at the price the caller
    already chose (the book's best bid or ask, applied by `single_buy_saver`
    before this is ever called). That is the optimistic end of what a taker
    gets, and it is stated rather than hidden: a completion that clears in a
    rehearsal is not evidence that the same completion clears live.
    """

    def __init__(self, registry: OrderRegistry, db_path: Path | str, *,
                 book_fn: Callable[[str, str], dict],
                 clob_host: str = "https://clob.polymarket.com") -> None:
        self._registry = registry
        self._db_path = Path(db_path)
        self._book_fn = book_fn
        self._clob_host = clob_host

    def get_order_book(self, token_id: str) -> dict:
        return self._book_fn(self._clob_host, str(token_id))

    def get_order(self, order_id: str) -> dict:
        """Venue-shaped order state, read by venue order id.

        Looked up with `get_order_by_venue_id`, which queries by id
        regardless of status -- not `get_active_orders`, which filters to
        pending/open/partial. `single_buy_saver` always calls this right
        after `cancel_order` on the same order, so by the time it asks the
        row is already `cancelled`; a status-filtered lookup would see
        nothing and the caller would refuse the whole pass believing the
        venue returned an unrecognisable response.
        """
        order = self._registry.get_order_by_venue_id(str(order_id))
        if order is None:
            return {}
        return {
            "id": order.order_id or order.id,
            "status": order.status,
            "size_matched": _filled_size(self._db_path, order.id),
            "original_size": order.original_size,
            "price": order.price,
        }

    def cancel_order(self, payload) -> dict:
        target = (getattr(payload, "orderID", None)
                  or getattr(payload, "order_id", None))
        order = self._registry.get_order_by_venue_id(str(target)) if target else None
        if order is None:
            return {"success": False, "orderID": target}
        now_ms = int(time.time() * 1000)
        self._registry.update_order_status(
            order.id, status="cancelled", last_polled_ts=now_ms)
        return {"success": True, "orderID": target}

    def _source_row_for_token(self, token_id: str) -> tuple[str, str | None]:
        """condition_id and pair_id for a token, taken from its latest order row.

        `MarketOrderArgsV2` carries no condition id, only `token_id`, `amount`,
        `side` and `price` -- so a completion buy has nowhere else to source
        one. The light leg always has an earlier row (`record_submit` rested
        it, `cancel_order` above may have just closed it) to read this from.
        """
        with closing(get_connection(self._db_path)) as conn:
            row = conn.execute(
                "SELECT condition_id, pair_id FROM orders WHERE token_id = ? "
                "ORDER BY posted_ts DESC LIMIT 1",
                (str(token_id),),
            ).fetchone()
        if row is None:
            return "", None
        return str(row["condition_id"] or ""), row["pair_id"]

    def create_and_post_market_order(self, order_args) -> dict:
        """Cross the book in the rehearsal: called with one positional
        `MarketOrderArgsV2(token_id=..., amount=..., side=..., price=...)`.

        `amount` is not shares on both sides. On a BUY it is notional dollars
        -- the maker amount the SDK's `get_market_order_amounts` treats as
        what we give -- so shares received are `amount / price`. On a SELL
        `amount` already is shares. Reading a BUY's dollar amount as a share
        count is exactly the bug `single_buy_saver.py:905-915` documents:
        the guards that validated the dollar figure would not catch it.

        A SELL writes nothing to the store: `single_buy_saver._record_exit_close`
        records the close as the sole ledger entry for that trade (the
        docstring on `exit_single_buy` explains why -- a taker SELL has no
        resting row for reconcile to adopt), and a fill row here would
        double-count the same exit. A BUY writes both an order and a fill row
        itself: a shadow run has no poll loop or reconcile to pick a
        completion fill up otherwise.
        """
        token_id = str(getattr(order_args, "token_id", "") or "")
        amount = float(getattr(order_args, "amount", 0.0) or 0.0)
        side = str(getattr(order_args, "side", "") or "").upper()
        raw_price = getattr(order_args, "price", None)
        price = float(raw_price) if raw_price is not None else None

        if not token_id or amount <= 0 or price is None or price <= 0:
            raise ShadowOrderRefused(
                f"cannot cross {token_id[:12] or '?'} for amount={amount}: "
                f"missing token, amount or price")

        if side == "SELL":
            return {
                "success": True,
                "orderID": f"{SHADOW_ORDER_PREFIX}{uuid.uuid4().hex[:12]}",
                "status": "matched", "price": price, "size": amount,
            }

        if side != "BUY":
            raise ShadowOrderRefused(
                f"unrecognised order side {side!r} for {token_id[:12]}")

        shares = amount / price

        condition_id, pair_id = self._source_row_for_token(token_id)
        if not condition_id:
            raise ShadowOrderRefused(
                f"cannot record completion buy for {token_id[:12]}: no prior "
                f"order row on this token to source a condition_id from")

        now_ms = int(time.time() * 1000)
        local_id = str(uuid.uuid4())
        self._registry.create_order(OrderRecord(
            id=local_id,
            order_id=f"{SHADOW_ORDER_PREFIX}{uuid.uuid4().hex[:12]}",
            condition_id=condition_id, token_id=token_id, side="BUY",
            price=price, original_size=shares, status="filled",
            posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id,
        ))
        self._registry.record_fill(FillRecord(
            trade_id=f"{SHADOW_TRADE_PREFIX}{uuid.uuid4().hex[:16]}",
            order_uuid=local_id, size=shares, price=price,
            venue_ts=now_ms, recorded_ts=now_ms,
        ))
        return {"success": True, "orderID": local_id, "status": "matched",
                "price": price, "size": shares}


def shadow_positions(registry: OrderRegistry, db_path: Path | str) -> dict[str, float]:
    """Positions as the shadow store knows them, shaped like `fetch_positions`.

    Passed to `auto_manage_pairs` explicitly so the pass never reaches for the
    Data API. That read fails the pass closed on any error, and failing closed
    on every cycle would keep the pairs pass from ever running in a rehearsal
    that has no funder to read a live position from.
    """
    out: dict[str, float] = {}
    with closing(get_connection(Path(db_path))) as conn:
        rows = conn.execute(
            """
            SELECT o.token_id AS token_id, COALESCE(SUM(f.size), 0) AS shares
            FROM orders o LEFT JOIN fills f ON f.order_uuid = o.id
            GROUP BY o.token_id
            """
        ).fetchall()
    for r in rows:
        shares = float(r["shares"])
        if shares > 0:
            out[str(r["token_id"])] = shares
    return out
