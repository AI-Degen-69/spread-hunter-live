"""Market resolution reconciliation: when a market ends, say so in the registry.

Problem this solves
-------------------
The dashboard's QUOTING pill is a read-side derivation
(``quotes_count > 0`` from the append-only ``quotes`` ledger), so any market
the run ever quoted reads as QUOTING forever unless something writes a
terminal marker. Three terminal markers exist in the schema, but only two
were ever wired:

* ``closes.method='venue_sync'`` -- written by the LIVE ``venue_sync`` /
  account sweep. The dashboard's "Sync" button. Shadow runs never write it:
  ``shadow_sweep`` runs ``auto_manage_pairs`` (merge / single-buy-exit only)
  and nothing asks the venue whether the *market itself* ended.
* ``runtime/markets.json`` with ``days_to_resolve < 0`` -- computed by the
  ranker from the venue ``endDate``. The ranker *drops* ended markets from
  the feed entirely (its gamma query excludes ``closed`` markets), so a
  market that ended arrives here as ``days_to_resolve = None``, which the
  read side deliberately keeps (``None`` must never silently drop a market).
  Net: the feed-derived escape hatch never fires for dropped markets.
* ``resolutions`` table -- ``OrderRegistry.log_resolution`` writes it, but
  has **zero callers**. Dead code. Verified empty on every live shadow db.

So in a shadow run a baseball game that ended ~14h ago stays QUOTING in the
dashboard, because:

1. its ``quotes_count`` never decays (append-only ledger),
2. its ``days_to_resolve`` is ``None`` (dropped from the feed), and
3. the runtime-fallback funnel (used for every non-production / shadow db)
   rebuilds ``graduated`` from ``by_mkt`` *itself*, so the frontend's
   "left the funnel" FINISHED escape hatch can never fire for a quoted
   market -- it is forever "graduated".

This module closes the write-side gap. Each rotation the shadow sweep calls
``sweep_market_resolutions``: for every market the run touched that is no
longer in the current universe feed, it asks the public gamma API whether the
venue reports it closed (or its ``endDate`` passed). On confirmation it
cancels any still-resting rows for that condition id, records a
``resolutions`` row (finally wiring ``log_resolution``), and -- for shadow
stores only -- books the settlement PnL for held shares via
``book_shadow_settlement`` (method ``shadow_settlement``). ``winning_token``
is read from the venue's ``outcomePrices`` when determinable (price >= 0.99),
else kept ``None``. A failed read degrades (skips this rotation); it never
marks a market resolved on a guess.

Settlement PnL is deliberately SHADOW-ONLY. A shadow run models the wallet;
booking the redemption is what a validation report needs to measure the
strategy against real money. The LIVE loop must NOT fabricate closes -- live
redemption happens on-chain and ``venue_sync``/``reconcile`` are the authors
of truth there -- so ``sweep_market_resolutions`` defaults to
``book_settlement=False`` and only explicit shadow callers (``shadow_sweep``
and the backfill) turn it on.

The read side (``core_brain/kpi.py`` + ``dashboard/static/app.js``) consumes
the ``resolutions`` table directly: a market whose cid is in ``resolutions``
(or whose ``days_to_resolve < 0``) reads ``resolved=True`` and is excluded
from the runtime-fallback ``graduated`` list, so the dashboard's existing
FINISHED heuristic fires.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import urllib.request

# Public read endpoint (no signer, no key). Same host the ranker and the
# shadow book source already talk to; the shadow sweep already performs a
# public book read every rotation, so this adds no new network surface.
DEFAULT_GAMMA_HOST = "https://gamma-api.polymarket.com"

# A winning side's settled price. Polymarket binary outcomes settle at
# [1.0, 0.0]; a side priced >= this threshold is the resolved winner.
WINNER_PRICE_THRESHOLD = 0.99


@dataclass(frozen=True)
class MarketEndState:
    """What the public venue says about a market's end.

    ``resolved`` is the boolean the sweeper keys on: the venue has either
    marked the market ``closed``, or its stated ``end_date`` is in the past.
    The two are durable, venue-free facts once recorded; a transient
    "dropped from the feed" is NOT enough on its own (the ranker drops
    markets mid-game for funding/spread reasons, and re-quoting must resume
    when they reappear).
    """

    condition_id: str
    closed: Optional[bool] = None
    end_date_iso: Optional[str] = None
    end_date_passed: Optional[bool] = None
    winner_token: Optional[str] = None
    # The winning label ("Up" / "Down" / team name) is the human-readable
    # headline. ``winning_token_id`` is the venue token id (from
    # ``clobTokenIds`` aligned to ``outcomes``) the settlement bookkeeping
    # needs to tell which held shares redeem at $1.00.
    winning_token_id: Optional[str] = None
    winner_price: Optional[float] = None
    resolved: bool = False
    unreachable: bool = False


@dataclass
class SweepResult:
    """One market's outcome in a sweep pass."""

    condition_id: str
    action: str  # resolved_recorded | already_resolved | still_open | skipped_active | unreachable | partial_stranded
    winning_token: Optional[str] = None
    winning_token_id: Optional[str] = None
    cancelled_rows: int = 0
    partial_rows: int = 0
    settled_shares: float = 0.0
    settled_pnl: float = 0.0
    reason: str = ""


def parse_end_state(row: dict, now_ts: Optional[float] = None) -> Optional[MarketEndState]:
    """Classify a gamma ``/markets`` row into a ``MarketEndState``.

    Pure: no clock, no network. ``now_ts`` is the test seam (seconds). The
    ranker's own ``days_to_resolve`` uses the venue ``endDate`` with the
    same end_date_passed semantic, so this classification aligns 1:1 with the
    read-side escape hatches in ``kpi.py`` and ``app.js``.

    Returns ``None`` when the row is not a dict (gamma returned nothing for
    this condition id), which the caller treats as unreachable.
    """
    if not isinstance(row, dict):
        return None
    cid = str(row.get("condition_id") or row.get("conditionId") or "")
    if not cid:
        return None

    closed = row.get("closed")
    if closed is not None:
        try:
            closed = bool(closed)
        except Exception:
            closed = None

    end_iso = row.get("endDate") or row.get("end_date_iso") or row.get("endDateIso")
    end_passed: Optional[bool] = None
    if end_iso:
        try:
            from datetime import datetime, timezone
            end = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            now = datetime.fromtimestamp(now_ts if now_ts is not None else time.time(),
                                         tz=timezone.utc)
            end_passed = end.timestamp() <= now.timestamp()
        except Exception:
            end_passed = None

    resolved = bool((closed is True) or (end_passed is True))

    # Winner: Polymarket ships outcomes=["Up","Down"] (or question-dependent
    # labels) and outcomePrices=["1","0"] at settlement. The token id lives
    # in clobTokenIds, aligned to outcomes -- so when a side priced >= the
    # win threshold we also capture its venue token id for settlement.
    winner_token: Optional[str] = None
    winning_token_id: Optional[str] = None
    winner_price: Optional[float] = None
    try:
        outcomes = row.get("outcomes") or row.get("outcomeLabels")
        prices = row.get("outcomePrices") or row.get("outcome_prices")
        token_ids = row.get("clobTokenIds") or row.get("clob_token_ids")
        # gamma ships these as JSON-encoded STRINGS (e.g. '"["1","0"]"') rather
        # than arrays; normalise both spellings so the winner is found either way.
        def _as_list(v):
            if isinstance(v, list):
                return v
            if isinstance(v, str):
                try:
                    parsed = json.loads(v)
                except Exception:
                    return []
                return parsed if isinstance(parsed, list) else []
            return []

        outcomes = _as_list(outcomes)
        prices = _as_list(prices)
        token_ids = _as_list(token_ids)
        if len(outcomes) == len(prices):
            for idx, (label, p) in enumerate(zip(outcomes, prices)):
                try:
                    pf = float(p)
                except (TypeError, ValueError):
                    continue
                if pf >= WINNER_PRICE_THRESHOLD:
                    winner_token = str(label)
                    winner_price = pf
                    if idx < len(token_ids):
                        winning_token_id = str(token_ids[idx]) or None
                    break
    except Exception:
        winner_token = None
        winning_token_id = None

    return MarketEndState(
        condition_id=cid,
        closed=closed,
        end_date_iso=str(end_iso) if end_iso else None,
        end_date_passed=end_passed,
        winner_token=winner_token,
        winning_token_id=winning_token_id,
        winner_price=winner_price,
        resolved=resolved,
        unreachable=False,
    )


def fetch_market_end_state(
    gamma_host: str,
    condition_id: str,
    *,
    timeout: float = 10.0,
    now_ts: Optional[float] = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> MarketEndState:
    """Public gamma read for one market's end state.

    ``GET {gamma_host}/markets?condition_ids={cid}&closed=true`` is read-only,
    no key. A User-Agent header is required (gamma 403s without one), and
    ``closed=true`` is required to see ends markets at all. Any failure
    (network, malformed payload, empty result) returns an ``unreachable``
    state -- the caller degrades and retries next rotation. A confirmed read
    with no resolution signal returns ``resolved=False``, which is distinct
    from unreachable: the market is genuinely still open.
    """
    cid = (condition_id or "").strip()
    if not cid:
        return MarketEndState(condition_id=condition_id, unreachable=True)
    # Two things make ended markets findable by gamma:
    #  1. A User-Agent -- gamma returns 403 without one.
    #  2. `closed=true` -- the default excludes closed markets, so an ended
    #     market would come back as an empty list (indistinguishable, to us,
    #     from "not confirmed"). Asking for the closed side explicitly is what
    #     lets a resolution sweeper actually see the market is done.
    url = (f"{gamma_host.rstrip('/')}/markets"
           f"?condition_ids={cid}&closed=true")
    headers = {"User-Agent": "spread-hunter/0.1 (market resolution sweeper)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return MarketEndState(condition_id=condition_id, unreachable=True)
    rows = None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("markets")
    if not isinstance(rows, list) or not rows:
        # No row is "unreachable" for our purposes: we could not confirm, so
        # we do NOT mark resolved. Distinct from "confirmed still open".
        return MarketEndState(condition_id=condition_id, unreachable=True)
    state = parse_end_state(rows[0], now_ts=now_ts)
    if state is None:
        return MarketEndState(condition_id=condition_id, unreachable=True)
    return state


def _held_shares_by_token(registry, condition_id: str, run_id: str) -> tuple[dict[str, float], dict[str, float]]:
    """Net held shares and their cost basis per token for a run's position.

    Reads the ``fills`` table joined to ``orders`` (the same shape the
    registry's ``get_all_fills`` returns) and only rows the named run owns.
    A BUY fill adds shares and cost; a SELL fill subtracts cost at its sale
    price (netting out cash already received). Only ``max(0, net)`` is held
    inventory -- a token the run fully sold carries nothing to redeem.

    Returns ``({token_id: shares}, {token_id: cost_basis})``.
    """
    shares: dict[str, float] = {}
    cost: dict[str, float] = {}
    try:
        with registry._conn() as conn:
            rows = conn.execute(
                """
                SELECT o.side, o.token_id, f.size, f.price
                FROM fills f
                JOIN orders o ON o.id = f.order_uuid
                WHERE lower(o.condition_id) = lower(?)
                  AND (o.run_id = ? OR (? IS NULL AND o.run_id IS NULL))
                """,
                (condition_id, run_id, run_id),
            ).fetchall()
        for r in rows:
            token_id = r["token_id"] or "?"
            sz = float(r["size"] or 0.0)
            px = float(r["price"] or 0.0)
            side = (r["side"] or "BUY").upper()
            if side == "SELL":
                shares[token_id] = shares.get(token_id, 0.0) - sz
                cost[token_id] = cost.get(token_id, 0.0) - sz * px
            else:
                shares[token_id] = shares.get(token_id, 0.0) + sz
                cost[token_id] = cost.get(token_id, 0.0) + sz * px
    except Exception:
        return {}, {}
    return shares, cost


def book_shadow_settlement(
    registry,
    condition_id: str,
    state: MarketEndState,
    *,
    run_id: str,
    market_slug: Optional[str] = None,
    now_fn: Callable[[], float] = time.time,
) -> Optional[dict[str, float]]:
    """Book the redemption PnL for a shadow run's held shares at resolution.

    A shadow run models a wallet: at market end, shares the run still holds
    are worth their final price -- the winning side redeems at $1.00, the
    losing side at $0.00. Without this a validation report books the cost as
    CAPEX and never realises it, so the strategy's whole profit -- the pair
    cost below $1.00 -- never shows up as earned.

    ``state.winning_token_id`` must be known (the venue's settled-price
    signal), else we cannot tell which held shares redeem -- in that case we
    book nothing and return ``None`` rather than fabricate a guess. A market
    with no held shares books nothing either.

    Writes a single ``closes`` row with ``method='shadow_settlement'`` and
    returns ``{shares, proceeds, cost_basis, realized_pnl}`` for the sweep's
    log/Dashboard summary.
    """
    from core_brain.order_registry import CloseRecord

    winner_id = state.winning_token_id
    if not winner_id:
        return None

    # Do not double-count. A `merge`/`shadow_merge`/`single_buy_exit` close for
    # this condition already realised (some of) the position's PnL; booking a
    # settlement on top would call the same shares' profit earned twice. The
    # dominant hold-to-resolution case has NO closes -- that is the one we
    # settle. A condition that saw any close is skipped entirely rather than
    # guessing how much of it remains unrealised.
    try:
        with registry._conn() as conn:
            existing = conn.execute(
                "SELECT 1 FROM closes WHERE lower(condition_id) = lower(?) LIMIT 1",
                (condition_id,),
            ).fetchone()
        if existing:
            return None
    except Exception:
        return None

    held, held_cost = _held_shares_by_token(registry, condition_id, run_id)
    winning_held = max(0.0, float(held.get(winner_id, 0.0)))
    if winning_held < 1e-9:
        return None

    proceeds = winning_held * 1.0
    cost_basis = sum(max(0.0, float(held_cost.get(t, 0.0))) for t in held)
    realized_pnl = proceeds - cost_basis
    now = now_fn()
    try:
        registry.log_close(CloseRecord(
            ts=now,
            condition_id=condition_id,
            market_slug=market_slug,
            method="shadow_settlement",
            shares=winning_held,
            cost_basis=round(cost_basis, 6),
            proceeds=round(proceeds, 6),
            realized_pnl=round(realized_pnl, 6),
            run_id=run_id,
        ))
    except Exception:
        return None
    return {
        "shares": round(winning_held, 4),
        "proceeds": round(proceeds, 4),
        "cost_basis": round(cost_basis, 4),
        "realized_pnl": round(realized_pnl, 4),
    }


def _current_universe_cids(markets: list) -> set[str]:
    """Extract condition ids from the rotation's market list (live or shadow)."""
    out: set[str] = set()
    for m in markets or []:
        cid = None
        if hasattr(m, "cid"):
            cid = getattr(m, "cid", None)
        elif isinstance(m, dict):
            cid = m.get("cid") or m.get("condition_id")
        if cid:
            out.add(str(cid).lower())
    return out


def _touched_cids(registry) -> set[str]:
    """Condition ids this registry has ever seen (orders or quotes).

    Unfiltered by run_id on purpose: a market stuck QUOTING in a reused
    shadow store belongs to whatever run wrote it; the terminal marker must
    still land. The per-run DBs the statistical launcher mints are single-run
    anyway, so this scope is a no-op there and a help in the legacy reused
    store.
    """
    cids: set[str] = set()
    try:
        with registry._conn() as conn:
            for r in conn.execute(
                "SELECT DISTINCT condition_id FROM orders WHERE condition_id IS NOT NULL"
            ).fetchall():
                if r and r["condition_id"]:
                    cids.add(str(r["condition_id"]).lower())
            for r in conn.execute(
                "SELECT DISTINCT condition_id FROM quotes WHERE condition_id IS NOT NULL"
            ).fetchall():
                if r and r["condition_id"]:
                    cids.add(str(r["condition_id"]).lower())
    except Exception:
        pass
    return cids


def _already_resolved_cids(registry) -> set[str]:
    out: set[str] = set()
    try:
        for r in registry.get_all_resolutions():
            cid = r.get("condition_id") if isinstance(r, dict) else None
            if cid:
                out.add(str(cid).lower())
    except Exception:
        pass
    return out


def sweep_market_resolutions(
    registry,
    db_path,
    *,
    markets: list,
    gamma_host: str = DEFAULT_GAMMA_HOST,
    run_id: Optional[str] = None,
    now_fn: Callable[[], float] = time.time,
    fetch_state: Optional[Callable[[str, str], MarketEndState]] = None,
    book_settlement: bool = False,
    cancel_resting: bool = True,
) -> list[SweepResult]:
    """One resolution sweep: confirm externally-ended markets, record them.

    Called once per rotation by ``shadow_sweep`` (and reusable by any loop).
    For each touched cid that left the current universe feed:

    * ask the public gamma endpoint whether the venue reports it closed or
      its ``endDate`` passed;
    * on confirmation, cancel any still-resting ``open``/``pending`` rows
      for that cid (a safety net -- ``_cancel_dropped_markets`` already
      cancels ``open`` rows the rotation a market drops; this catches rows
      orphaned at run start or by a feed race), report ``partial`` rows as
      ``partial_stranded`` (their shares already bought; at market end there
      is nothing to hedge -- a stranded partial is reported, not exited,
      because shadow has no settlement path), and record a ``resolutions``
      row finally wiring ``OrderRegistry.log_resolution``;
    ``cancel_resting``: when True (shadow), still-resting rows are cancelled
    as a safety net. Live callers pass False -- ``_cancel_dropped_markets``
    owns cancellation there (via the venue), and a registry-only cancel could
    desync the row from a live order.

    Degrade, do not stop: any failure (network, registry error) skips that
    cid this rotation and retries next rotation. A market is NEVER marked
    resolved on a failed read.
    """
    from core_brain.order_registry import ResolutionRecord

    fetch = fetch_state or (
        lambda gh, cid: fetch_market_end_state(gh, cid, now_ts=now_fn())
    )

    universe = _current_universe_cids(markets)
    touched = _touched_cids(registry)
    already = _already_resolved_cids(registry)

    results: list[SweepResult] = []
    now = now_fn()
    r_id = run_id or registry._run_id()

    def _settle(state: MarketEndState, partial_rows: int = 0) -> tuple[float, float]:
        """Book settlement PnL for a resolved state; returns (shares, pnl)."""
        if not book_settlement:
            return 0.0, 0.0
        slug = None
        try:
            with registry._conn() as conn:
                row = conn.execute(
                    "SELECT market_slug FROM quotes "
                    "WHERE lower(condition_id) = lower(?) "
                    "AND market_slug IS NOT NULL LIMIT 1",
                    (state.condition_id,),
                ).fetchone()
                slug = row["market_slug"] if row else None
        except Exception:
            slug = None
        booked = book_shadow_settlement(
            registry, state.condition_id, state, run_id=r_id,
            market_slug=slug, now_fn=lambda: now,
        )
        if booked:
            return float(booked["shares"]), float(booked["realized_pnl"])
        return 0.0, 0.0

    for cid in sorted(touched - universe):
        # Already recorded: only possible remaining work is settlement, which
        # runs independently of when the resolution row was written (a re-run
        # or a later rotation may be the first time the winning token is
        # determinable).
        if cid in already:
            results.append(SweepResult(
                condition_id=cid, action="already_resolved"))
            if book_settlement:
                state = fetch(gamma_host, cid)
                if (not state.unreachable) and state.resolved:
                    shares, pnl = _settle(state)
                    results[-1].settled_shares = shares
                    results[-1].settled_pnl = pnl
            continue

        state = fetch(gamma_host, cid)
        if state.unreachable:
            results.append(SweepResult(
                condition_id=cid, action="unreachable",
                reason="gamma read failed; retry next rotation"))
            continue
        if not state.resolved:
            results.append(SweepResult(
                condition_id=cid, action="still_open",
                reason="dropped from feed but venue still open; do not mark"))
            continue

        # Confirmed ended. Cancel resting rows as a safety net (shadow only;
        # live leaves cancellation to the venue-authoritative dropped-market
        # cleanup already running every cycle).
        cancelled_rows = 0
        partial_rows = 0
        if cancel_resting:
            try:
                now_ms = int(now * 1000)
                active = [
                    o for o in registry.get_active_orders()
                    if (o.condition_id or "").lower() == cid
                ]
                for o in active:
                    st = getattr(o, "status", "")
                    if st in ("open", "pending"):
                        try:
                            registry.update_order_status(
                                o.id, status="cancelled", last_polled_ts=now_ms)
                            cancelled_rows += 1
                        except Exception:
                            pass
                    elif st == "partial":
                        partial_rows += 1
            except Exception:
                pass

        # Finally wire the dead code: record the terminal marker.
        try:
            registry.log_resolution(ResolutionRecord(
                condition_id=cid,
                winning_token=state.winner_token,
                resolved_ts=now,
                run_id=r_id,
            ))
        except Exception:
            pass

        # Shadow-only settlement PnL: redeem held shares at the winning side's
        # $1.00. Live callers leave book_settlement=False; live redemption
        # happens on-chain and is not this module's job.
        settled_shares, settled_pnl = _settle(state)

        action = "resolved_recorded"
        if partial_rows:
            action = "partial_stranded"
        results.append(SweepResult(
            condition_id=cid, action=action,
            winning_token=state.winner_token,
            winning_token_id=state.winning_token_id,
            cancelled_rows=cancelled_rows, partial_rows=partial_rows,
            settled_shares=settled_shares, settled_pnl=settled_pnl,
            reason="venue closed" if state.closed else "endDate passed"))

    return results
