"""Stray-order guard: detect single-side orders and positions, adopt complementary detached

legs into proper pairs, and cancel/remediate hopeless strays (Issue #205).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core_brain.order_registry import OrderRecord

log = logging.getLogger("stray_guard")


class StrayClassificationType(str, Enum):
    REGISTRY_PAIRED = "registry_paired"
    COMPLEMENTARY_DETACHED = "complementary_detached"
    HOPELESS_STRAY = "hopeless_stray"
    VIABLE_STRAY = "viable_stray"
    UNHEDGED_POSITION = "unhedged_position"


@dataclass
class ClassifiedOrder:
    order: OrderRecord
    classification: StrayClassificationType
    reason: str = ""


@dataclass
class DetachedPair:
    leg1: OrderRecord
    leg2: OrderRecord
    combined_cost: float


@dataclass
class HopelessStray:
    order: OrderRecord
    reason: str
    opposing_ask: Optional[float] = None


@dataclass
class ClassificationResult:
    registry_paired: list[tuple[str, list[OrderRecord]]] = field(default_factory=list)
    complementary_detached: list[DetachedPair] = field(default_factory=list)
    hopeless_strays: list[HopelessStray] = field(default_factory=list)
    viable_strays: list[ClassifiedOrder] = field(default_factory=list)


def _best_ask_from_book(book: dict | None) -> Optional[float]:
    if not book:
        return None
    asks = book.get("asks") or []
    if not asks:
        return None
    top = asks[0]
    if isinstance(top, (list, tuple)) and len(top) >= 1:
        try:
            return float(top[0])
        except (ValueError, TypeError):
            return None
    elif isinstance(top, dict):
        p = top.get("price")
        if p is not None:
            try:
                return float(p)
            except (ValueError, TypeError):
                return None
    return None


def classify_market_orders(
    orders: list[OrderRecord],
    books: dict[str, Any] | None = None,
    max_pair_cost: float = 0.99,
    market_tokens: dict[str, tuple[str, str]] | None = None,
) -> ClassificationResult:
    """Classify a set of active orders into paired, detached complementary, hopeless, or viable."""
    books = books or {}
    market_tokens = market_tokens or {}
    result = ClassificationResult()

    # Group orders by condition_id
    by_condition: dict[str, list[OrderRecord]] = {}
    for o in orders:
        by_condition.setdefault(o.condition_id, []).append(o)

    for cond, cond_orders in by_condition.items():
        # First, group by pair_id to identify healthy registry-linked pairs
        by_pair: dict[str, list[OrderRecord]] = {}
        no_pair: list[OrderRecord] = []
        for o in cond_orders:
            if o.pair_id:
                by_pair.setdefault(o.pair_id, []).append(o)
            else:
                no_pair.append(o)

        unpaired_orders: list[OrderRecord] = list(no_pair)

        for pid, pair_orders in by_pair.items():
            distinct_tokens = {o.token_id for o in pair_orders}
            if len(distinct_tokens) >= 2 and len(pair_orders) == 2:
                result.registry_paired.append((pid, pair_orders))
            else:
                unpaired_orders.extend(pair_orders)

        if not unpaired_orders:
            continue

        # Check for complementary detached orders on this condition
        remaining_unpaired: list[OrderRecord] = []
        used_ids: set[str] = set()

        for i, o1 in enumerate(unpaired_orders):
            if o1.id in used_ids:
                continue
            matched_partner: Optional[OrderRecord] = None
            best_cost = 999.0

            for j, o2 in enumerate(unpaired_orders):
                if i == j or o2.id in used_ids:
                    continue
                if o1.token_id != o2.token_id:
                    cost = o1.price + o2.price
                    if cost <= max_pair_cost and cost < best_cost:
                        matched_partner = o2
                        best_cost = cost

            if matched_partner is not None:
                used_ids.add(o1.id)
                used_ids.add(matched_partner.id)
                result.complementary_detached.append(
                    DetachedPair(leg1=o1, leg2=matched_partner, combined_cost=best_cost)
                )
            else:
                remaining_unpaired.append(o1)

        # For remaining lone orders, check feasibility against opposing book ask
        for o in remaining_unpaired:
            tokens = market_tokens.get(cond)
            opposing_token: Optional[str] = None
            if tokens:
                t1, t2 = tokens
                opposing_token = t2 if o.token_id == t1 else t1
            else:
                # If market_tokens not given, check books for any token other than o.token_id
                for b_tok in books:
                    if b_tok != o.token_id:
                        opposing_token = b_tok
                        break

            opposing_book = books.get(opposing_token) if opposing_token else None
            ask = _best_ask_from_book(opposing_book)

            if ask is not None:
                if (o.price + ask) >= max_pair_cost:
                    result.hopeless_strays.append(
                        HopelessStray(
                            order=o,
                            reason=f"cost_exceeds_cap: price={o.price:.4f} + ask={ask:.4f} >= {max_pair_cost:.4f}",
                            opposing_ask=ask,
                        )
                    )
                else:
                    result.viable_strays.append(
                        ClassifiedOrder(order=o, classification=StrayClassificationType.VIABLE_STRAY)
                    )
            elif opposing_book is not None and len(opposing_book.get("asks", [])) == 0:
                result.hopeless_strays.append(
                    HopelessStray(order=o, reason="no_ask_on_opposing_book")
                )
            else:
                # No opposing book info available; keep as viable stray until book is read
                result.viable_strays.append(
                    ClassifiedOrder(order=o, classification=StrayClassificationType.VIABLE_STRAY)
                )

    return result


def adopt_detached_legs(
    registry: Any,
    detached_pairs: list[DetachedPair],
    live: bool = True,
) -> list[str]:
    """Adopt complementary detached legs under a shared pair_id in registry.

    Returns list of adopted pair_ids.
    """
    adopted: list[str] = []
    for dp in detached_pairs:
        # Determine shared pair_id: pick an existing valid pair_id, or mint fresh
        p1 = dp.leg1.pair_id
        p2 = dp.leg2.pair_id
        shared_pid: str
        if p1 and p1.startswith("pair-"):
            shared_pid = p1
        elif p2 and p2.startswith("pair-"):
            shared_pid = p2
        elif p1:
            shared_pid = p1
        elif p2:
            shared_pid = p2
        else:
            shared_pid = f"pair-{uuid.uuid4().hex[:12]}"

        order_ids = [dp.leg1.id, dp.leg2.id]
        if live:
            registry.adopt_orders_into_pair(order_ids, shared_pid)
        log.info(
            "ADOPT_STRAY: welded %s (%.4f) and %s (%.4f) under %s (combined=%.4f)",
            dp.leg1.id[:8],
            dp.leg1.price,
            dp.leg2.id[:8],
            dp.leg2.price,
            shared_pid,
            dp.combined_cost,
        )
        adopted.append(shared_pid)
    return adopted


def cancel_hopeless_orders(
    client: Any,
    registry: Any,
    hopeless_strays: list[HopelessStray],
    live: bool = True,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Cancel hopeless resting orders that have no prospect of forming a profitable pair.

    In dry-run (live=False), logs and returns would_cancel without mutating venue or registry.
    """
    now_ms = int((now if now is not None else time.time()) * 1000)
    actions: list[dict[str, Any]] = []

    for hs in hopeless_strays:
        o = hs.order
        if o.status not in ("open", "pending"):
            continue

        target_venue_id = o.order_id or o.id

        if not live:
            log.info(
                "DRY RUN -- would cancel hopeless stray order %s (venue %s) on %s: %s",
                o.id[:8],
                o.order_id,
                o.condition_id[:10],
                hs.reason,
            )
            actions.append({
                "action": "would_cancel",
                "order_id": target_venue_id,
                "local_id": o.id,
                "reason": hs.reason,
            })
            continue

        # LIVE cancellation
        cancel_success = False
        try:
            from py_clob_client_v2.clob_types import OrderPayload
            client.cancel_order(OrderPayload(orderID=target_venue_id))
            cancel_success = True
        except Exception:
            try:
                client.cancel_order(target_venue_id)
                cancel_success = True
            except Exception as exc:
                log.warning("Failed to cancel hopeless order %s: %s", target_venue_id, exc)
                actions.append({
                    "action": "error",
                    "order_id": target_venue_id,
                    "local_id": o.id,
                    "error": str(exc),
                })
                continue

        if cancel_success:
            registry.update_order_status(
                o.id,
                status="cancelled",
                last_polled_ts=now_ms,
                cancel_reason=f"hopeless_stray: {hs.reason}",
            )
            log.info(
                "CANCEL_STRAY: cancelled order %s (venue %s) on %s: %s",
                o.id[:8],
                o.order_id,
                o.condition_id[:10],
                hs.reason,
            )
            actions.append({
                "action": "cancelled",
                "order_id": target_venue_id,
                "local_id": o.id,
                "reason": hs.reason,
            })

    return actions


def remediate_stray_positions(
    client: Any,
    registry: Any,
    stray_orders_or_positions: list[OrderRecord],
    max_pair_cost: float = 0.99,
    live: bool = True,
    venue_positions: Optional[dict[str, float]] = None,
) -> list[dict[str, Any]]:
    """Remediate unhedged stray filled positions by routing them to exit.

    In dry run (live=False), logs and returns would_exit.
    In live run, assigns a pair_id if missing, then invokes exit_single_buy.
    """
    from core_brain.single_buy_saver import exit_single_buy

    results: list[dict[str, Any]] = []
    for order in stray_orders_or_positions:
        matched = registry.get_size_matched(order.id)
        if matched <= 1e-6:
            continue

        pair_id = order.pair_id
        if not pair_id:
            pair_id = f"pair-stray-{order.id[:8]}"
            registry.adopt_orders_into_pair([order.id], pair_id)

        try:
            exit_res = exit_single_buy(
                client,
                registry,
                pair_id,
                max_pair_cost=max_pair_cost,
                live=live,
                venue_positions=venue_positions,
            )
            results.append(exit_res)
        except Exception as exc:
            log.warning("Failed to exit stray position for pair %s: %s", pair_id, exc)
            results.append({
                "action": "error",
                "pair_id": pair_id,
                "error": str(exc),
            })

    return results




