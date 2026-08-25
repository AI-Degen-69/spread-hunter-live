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
    SIZE_EPS, CloseRecord, FillRecord, OrderRecord, OrderRegistry, get_connection,
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


def _book_side_to_levels(side, *, reverse: bool) -> list:
    """A price-keyed dict side (`markets.parse_book`'s canonical shape) ->
    a list of `{"price", "size"}` levels, sorted the way the venue returns
    them.

    `single_buy_saver._book_levels` iterates `bids`/`asks` expecting a list;
    handed the canonical dict instead it iterates the dict's float keys, and
    every one is discarded (`getattr(0.51, "price", None)` is `None`), so
    `best_ask`/`best_bid` silently come back `None` for every book. Bids sort
    best-first (highest price), asks best-first (lowest price) -- the same
    order the CLOB's own `/book` endpoint returns.

    A side that is not a dict (already a list, or absent) passes through
    unchanged: `book_fn` need not always be `markets.parse_book`, and a
    caller already handing over levels should not be reshaped.
    """
    if not isinstance(side, dict):
        return list(side or [])
    prices = sorted(side.keys(), reverse=reverse)
    return [{"price": p, "size": side[p]} for p in prices]


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
        """The book, adapted for `single_buy_saver._book_levels`.

        `book_fn` (in production, `seam.fetch_books`, ultimately
        `markets.parse_book`) returns the canonical shape this repo uses
        everywhere else: `bids`/`asks` as price-keyed dicts, which is what
        `queue_ahead_at` (this store's own queue-position math) wants.
        `single_buy_saver._book_levels` wants a list of `{"price", "size"}`
        levels per side instead. Both are real consumers of the same book
        source, so the adaptation happens here, on the way out to the one
        that needs it -- `markets.parse_book` and the production book source
        stay untouched.
        """
        book = self._book_fn(self._clob_host, str(token_id))
        adapted = dict(book)
        adapted["bids"] = _book_side_to_levels(book.get("bids"), reverse=True)
        adapted["asks"] = _book_side_to_levels(book.get("asks"), reverse=False)
        return adapted

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

    def _naked_pair_for_token(self, token_id: str) -> tuple[str, str] | None:
        """(condition_id, pair_id) of the oldest pair that is currently
        naked on this token, or `None` if no pair on this token is naked.

        `MarketOrderArgsV2` carries no condition id or pair id -- only
        `token_id`, `amount`, `side`, `price` -- so a completion buy has no
        way to name its pair directly; this reconstructs it.

        Scoping this to "the most recent order row on this token" (an
        earlier version) is wrong whenever a token carries rows under more
        than one `pair_id`, which is ordinary here: `record_submit` mints a
        fresh `pair_id` every call, and a market re-quoted across rotations
        accumulates several. If an older pair is still naked when a newer
        one posts fresh orders on the same tokens, "most recent row" attaches
        the completion to the newer pair, leaves the older one naked, and
        the pairs pass buys it again next cycle -- a repeat-buy loop that
        does not stop until `pairs_exit_window_sec` ages the fill out.

        This instead reproduces the slice of `load_pair`'s heavy/light math
        needed to tell WHICH pair(s) on this token are actually naked, and
        picks the oldest by `posted_ts`: `auto_manage_pairs` discovers pairs
        by fill age (oldest fills age out of the window first), so the
        oldest naked pair is also the one it would have reached first.
        """
        with closing(get_connection(self._db_path)) as conn:
            rows = conn.execute(
                """
                SELECT o.pair_id AS pair_id, MIN(o.condition_id) AS condition_id,
                       o.token_id AS token_id, MIN(o.posted_ts) AS posted_ts,
                       COALESCE(SUM(f.size), 0) AS matched
                FROM orders o
                LEFT JOIN fills f ON f.order_uuid = o.id
                WHERE o.pair_id IN (
                    SELECT DISTINCT pair_id FROM orders
                    WHERE token_id = ? AND pair_id IS NOT NULL
                )
                GROUP BY o.pair_id, o.token_id
                """,
                (str(token_id),),
            ).fetchall()

        by_pair: dict[str, dict] = {}
        for r in rows:
            pid = r["pair_id"]
            slot = by_pair.setdefault(pid, {
                "condition_id": r["condition_id"], "legs": {},
                "posted_ts": r["posted_ts"],
            })
            slot["legs"][r["token_id"]] = float(r["matched"])
            slot["posted_ts"] = min(slot["posted_ts"], r["posted_ts"])

        target = str(token_id)
        naked_candidates = []
        for pid, info in by_pair.items():
            legs = info["legs"]
            this_matched = legs.get(target, 0.0)
            other_matched = max(
                (v for k, v in legs.items() if k != target), default=0.0)
            naked = other_matched - this_matched
            if naked > SIZE_EPS:
                naked_candidates.append((info["posted_ts"], pid, info["condition_id"]))

        if not naked_candidates:
            return None
        naked_candidates.sort(key=lambda c: c[0])
        _, pid, cid = naked_candidates[0]

        if not cid:
            raise ShadowOrderRefused(
                f"cannot record completion buy for {str(token_id)[:12]}: pair "
                f"{pid} has empty condition_id, which would make the "
                f"completion row invisible to inventory tracking")

        return str(cid), pid

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

        found = self._naked_pair_for_token(token_id)
        if found is None:
            raise ShadowOrderRefused(
                f"cannot record completion buy for {token_id[:12]}: no pair "
                f"on this token reads as naked in the store, so there is "
                f"nothing to attach the fill to")
        condition_id, pair_id = found

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


def record_shadow_merges(
    registry: OrderRegistry,
    db_path: Path | str,
    *,
    now_fn: Callable[[], float] = time.time,
) -> list[str]:
    """Close every balanced pair at exactly $1.00 a share.

    The merge itself is a signed on-chain transaction and does not happen here:
    a shadow run has no key. What is recorded is its arithmetic, which is the
    part a rehearsal can honestly reproduce -- a merged pair pays $1.00, so the
    result is `shares * (1.00 - pair cost)`, and `method='shadow_merge'` says
    where the row came from.

    Tracks merged shares per pair rather than per-pair boolean, allowing
    multiple merges as a pair fills incrementally. If a pair filled 10/10
    shares and merged, then fills another 10/10, a second close records for
    the newly merged 10 shares.
    """
    with closing(get_connection(Path(db_path))) as conn:
        # Current fills per pair per leg
        rows = conn.execute(
            """
            SELECT o.pair_id AS pair_id, o.condition_id AS condition_id,
                   o.token_id AS token_id,
                   COALESCE(SUM(f.size), 0) AS shares,
                   COALESCE(SUM(f.size * f.price), 0) AS cost
            FROM orders o LEFT JOIN fills f ON f.order_uuid = o.id
            WHERE o.pair_id IS NOT NULL
            GROUP BY o.pair_id, o.token_id
            """
        ).fetchall()

        # Already-merged shares and cost per pair: tx_hash is prefixed pair_id
        merged_rows = conn.execute(
            """
            SELECT tx_hash AS pair_tx, COALESCE(SUM(shares), 0) AS merged_shares,
                   COALESCE(SUM(cost_basis), 0) AS merged_cost
            FROM closes
            WHERE method = 'shadow_merge' AND tx_hash IS NOT NULL
            GROUP BY tx_hash
            """
        ).fetchall()

    by_pair: dict[str, list] = {}
    for r in rows:
        by_pair.setdefault(str(r["pair_id"]), []).append(r)

    # Build maps from pair_id to already-merged shares and cost. tx_hash is formatted
    # as "pair-xxx" for the first merge, "pair-xxx:2" for the second, etc.
    already_merged: dict[str, float] = {}
    already_cost: dict[str, float] = {}
    for r in merged_rows:
        tx = str(r["pair_tx"])
        # Extract pair_id from tx_hash (everything before the optional ":N" suffix)
        pair_id = tx.split(":")[0] if ":" in tx else tx
        already_shares = float(r["merged_shares"])
        already_cb = float(r["merged_cost"])
        already_merged[pair_id] = already_merged.get(pair_id, 0.0) + already_shares
        already_cost[pair_id] = already_cost.get(pair_id, 0.0) + already_cb

    merged: list[str] = []
    for pair_id, legs in by_pair.items():
        if len(legs) != 2:
            continue
        min_fills = min(float(leg["shares"]) for leg in legs)
        if min_fills <= 0:
            continue
        already_shares = already_merged.get(pair_id, 0.0)
        mergeable = min_fills - already_shares
        if mergeable <= SIZE_EPS:
            continue
        # Cost basis for newly merged shares: compute target cost (apportioned cost
        # of all min_fills shares using current prices), subtract what was already
        # recorded in previous closes. This ensures the newly merged shares are
        # charged their actual incremental cost.
        target_cost = sum(
            float(leg["cost"]) * (min_fills / float(leg["shares"])) for leg in legs
        )
        already_cb = already_cost.get(pair_id, 0.0)
        cost_basis = target_cost - already_cb
        proceeds = mergeable * 1.0
        # Version the tx_hash to ensure uniqueness within (condition_id, tx_hash)
        # constraint: first merge uses pair_id as-is, second uses "pair_id:2", etc.
        # Count existing closes for this pair with tx_hash starting with pair_id
        with closing(get_connection(Path(db_path))) as conn:
            merge_count_row = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM closes
                WHERE method = 'shadow_merge' AND tx_hash LIKE ?
                """,
                (f"{pair_id}%",),
            ).fetchone()
        merge_count = int(merge_count_row["cnt"]) if merge_count_row else 0
        tx_hash = pair_id if merge_count == 0 else f"{pair_id}:{merge_count + 1}"
        registry.log_close(CloseRecord(
            ts=now_fn(), condition_id=str(legs[0]["condition_id"]),
            method="shadow_merge", shares=mergeable, cost_basis=cost_basis,
            proceeds=proceeds, fee=0.0, gas=0.0,
            realized_pnl=proceeds - cost_basis,
            # Cost has to leave the inventory with the shares, or the decision
            # keeps paying for a position it no longer holds. Split evenly
            # across the legs exactly as the live merge does
            # (`core_brain/order_manager.py`, the `method="merge"` close): the
            # closes table has no token column, so an even split is the only
            # attribution either path can record, and a rehearsal must not
            # invent a more precise one than live keeps.
            up_cost_removed=cost_basis / 2.0 if cost_basis else 0.0,
            dn_cost_removed=cost_basis / 2.0 if cost_basis else 0.0,
            # The pair id rides in tx_hash, versioned to satisfy the unique
            # constraint on (condition_id, tx_hash). Multiple closes reference
            # the same logical pair by sharing a common prefix in tx_hash.
            tx_hash=tx_hash,
        ))
        merged.append(pair_id)

    return merged
