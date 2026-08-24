"""Shadow run: the whole loop, against the live book, spending nothing.

`python -m core_brain.shadow_run --minutes 5`

What this is for: watching the machine work. Every other way to see the full
loop -- screener through quoting through fills through the merge path -- costs
real money, because `--no-live` on `core_brain.order_manager` prints one
subcommand's intent and exits before any of it happens.

What it changes, and it is only three things:

1. **The client cannot sign.** `core_brain.shadow_guard.shadow_client` builds a
   CLOB client with no private key and no API credentials, wrapped in a
   deny-by-default proxy. Submission is not disabled by a flag; there is
   nothing loaded to sign with.
2. **The store is not the production registry.** `data/shadow.db` by default,
   and `data/orders.db` is refused outright.
3. **The run stops on a wall clock.** Driven from the injected `sleep_fn`, so
   `core_brain/trader_loop.py` needs no edit.

Everything else is the live path unchanged: the same `MakerConfig`, the same
`MAX_ORDER_USD` and `MAX_TOTAL_USD`, the same gates, the same
`decide_quotes`. A rehearsal run under a relaxed configuration would be a
rehearsal of something we do not ship.

Unauthenticated reads: books, prices and market metadata on the CLOB are
public endpoints. If one starts requiring authentication, that is a finding
to report, never a reason to load credentials into a shadow run.

The numbers a shadow run produces are rehearsal, not results.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("shadow_run")

DEFAULT_SHADOW_DB = Path("data/shadow.db")


class _Deadline(KeyboardInterrupt):
    """Raised from the shadow `sleep_fn` when the time box expires.

    Subclasses KeyboardInterrupt deliberately. `core_brain/trader_loop.py`
    wraps its sleep call in `except KeyboardInterrupt: break` and nothing else,
    and that break is the loop's designed clean exit -- it returns the last
    rotation's results. A plain Exception would propagate out of `run` uncaught
    and lose them; a bare BaseException subclass would not be caught by that
    handler at all and would escape the same way.
    """


def make_deadline_sleep(
    deadline_ts: float,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[float], None]:
    """A `sleep_fn` for `trader_loop.run` that ends the run at `deadline_ts`.

    Raises `_Deadline` once the clock is at or past the deadline. Otherwise
    sleeps, clamped to the time actually remaining, so a long rotation interval
    cannot overshoot a short time box.

    `clock` and `sleep` are injected so the time box is testable without
    spending the wall-clock time it measures.
    """
    def sleep_fn(seconds: float) -> None:
        remaining = deadline_ts - clock()
        if remaining <= 0:
            raise _Deadline()
        sleep(max(0.0, min(seconds, remaining)))

    return sleep_fn


@dataclass
class ShadowIntent:
    """A decided intent, stamped with the market it belongs to.

    `QuoteIntent` has no condition_id (it travels beside the market object in
    the live loop); a recorded intent outlives that pairing, so the recorder
    attaches it.
    """
    condition_id: str
    side: str
    token_id: str
    price: float
    size: int
    mid: float = 0.0
    edge_vs_mid: float = 0.0
    reason: str = ""
    crossed: bool = False

    @classmethod
    def from_quote(cls, qi: Any, condition_id: str) -> "ShadowIntent":
        return cls(
            condition_id=condition_id,
            side=qi.side,
            token_id=qi.token_id,
            price=qi.price,
            size=qi.size,
            mid=getattr(qi, "mid", 0.0),
            edge_vs_mid=getattr(qi, "edge_vs_mid", 0.0),
            reason=getattr(qi, "reason", ""),
            crossed=bool(getattr(qi, "crossed", False)),
        )


@dataclass
class ShadowResult:
    """What a shadow session produced. Rehearsal numbers, not results."""
    results: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    skipped_stages: tuple = ()


def _make_logging_emit(db_path) -> Callable[..., None]:
    """`emit`, plus one INFO line per market visit.

    A shadow run exists to be watched, and before this the only thing it wrote
    to the terminal was the banner and, ten minutes later, the summary. Every
    decision went to the ring file and the store, where reading it means
    querying SQLite beside a running loop.

    Only the `quoting/decide` event logs: that is the one event per market visit
    that carries both the intent count and the rationale. Logging every phase
    would bury the decisions in rotation bookkeeping. Telemetry is unchanged --
    the wrapper calls `emit` first and adds to it, never in place of it.
    """
    from core_brain.cycle_stream import emit as _emit_cycle_event

    def emit_fn(cycle, phase, action, **kw) -> None:
        _emit_cycle_event(cycle, phase, action, db_path=db_path, **kw)

        if phase != "quoting" or action != "decide":
            return

        extra = kw.get("extra") or {}
        count = int(extra.get("intent_count", 0) or 0)
        reason = (kw.get("reason") or "").strip()
        log.info(
            "cycle=%s %s intents=%d%s",
            cycle,
            kw.get("market_slug") or extra.get("condition_id") or "?",
            count,
            f" -- {reason}" if reason else "",
        )

    return emit_fn


def build_shadow_seam(
    *,
    db_path,
    client_fn: Optional[Callable[[], Any]] = None,
    intents_sink: Optional[list] = None,
    decide_fn: Optional[Callable] = None,
    fetch_market: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
    traded_fn: Optional[Callable[[str, set], dict]] = None,
    cfg=None,
    registry=None,
):
    """The seam a shadow run rotates over: live reads, recorded writes.

    Identical to the live seam except at exactly two ports:

    - `client` comes from `shadow_guard.shadow_client` (no signer, denying
      proxy) unless `client_fn` injects one -- and an injected client is
      wrapped in the same proxy, so the seam never holds a raw client.
    - `submit_fn`/`cancel_fn` are recorders: submit appends each decided intent
      (stamped with its condition id) to `intents_sink` and returns the count;
      cancel records nothing and returns 0. Milestone 2 gives them their full
      shape.

    `reconcile_fn`/`sweep_fn` are no-ops on purpose -- they reconcile against
    venue positions a shadow run does not have. Callers must report them as
    deliberately skipped (`ShadowResult.skipped_stages`), never as stages that
    silently passed.

    Without an injected `fetch_books` the session rehearses decisions against
    empty books and says so in the log; the entrypoint wires the real book
    source. Degrade, do not stop.
    """
    from core_brain.order_registry import OrderRegistry, init_db
    from core_brain.quotes import decide_quotes
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )
    from core_brain.shadow_guard import (
        assert_not_production_registry,
        shadow_client,
    )
    from core_brain.trader_loop import VenueSeam, _make_inventory_fn, _make_open_orders_fn

    assert_not_production_registry(db_path)
    ensure_shadow_tables(db_path)

    if intents_sink is None:
        intents_sink = []
    if registry is None:
        init_db(db_path)
        registry = OrderRegistry(db_path=Path(db_path))

    from core_brain.shadow_guard import ReadOnlyVenue

    client = client_fn() if client_fn else shadow_client()
    # An injected client is an unwrapped object by definition -- tests hand in
    # plain stubs. Whatever arrives leaves through the deny-by-default proxy,
    # so no wiring path can put a raw signing-capable client on the seam.
    if not isinstance(client, ReadOnlyVenue):
        client = ReadOnlyVenue(client)
    decide = decide_fn or decide_quotes

    empty_book = {"bids": {}, "asks": {}}

    def shadow_fetch_books(clob_host, token_id):
        if fetch_books is None:
            log.warning(
                "no book source wired: deciding %s... against an empty book",
                str(token_id)[:12])
            return dict(empty_book)
        return fetch_books(clob_host, token_id)

    resolved_traded_fn = traded_fn or _default_traded_fn()
    seen_by_market: dict[str, set] = {}
    base_inventory_fn = _make_inventory_fn(registry, Path(db_path))

    def settling_inventory_fn(market):
        """Settle this market, then report what it now holds.

        Order matters: a decision taken against a pre-settle inventory is a
        decision taken against a position the market has already left.
        """
        seen = seen_by_market.setdefault(market.condition_id, set())
        try:
            settle_market(registry, market, db_path=db_path,
                          traded_fn=resolved_traded_fn, seen=seen)
        except (sqlite3.Error, OSError, ValueError) as e:
            # Degrade, do not stop: an unsettled visit decides against a stale
            # inventory, which the next visit corrects. Raising here would take
            # the market out of the rotation entirely.
            log.warning("settle failed for %s: %s", market.condition_id[:12], e)
        return base_inventory_fn(market)

    def shadow_submit(client, reg, market, intents, cfg_in) -> int:
        for qi in intents:
            intents_sink.append(ShadowIntent.from_quote(qi, market.condition_id))
        return record_submit(client, reg, market, intents, cfg_in,
                             db_path=db_path, book_fn=shadow_fetch_books)

    def record_cancel(_client, _registry, orders) -> int:
        return 0

    def noop_reconcile(*_a) -> None:
        return None

    def noop_sweep() -> None:
        return None

    return VenueSeam(
        client=client,
        registry=registry,
        base_cfg=cfg,
        clob_host=os.environ.get("CLOB_HOST", "https://clob.polymarket.com"),
        fetch_market=fetch_market or _default_fetch_market(),
        fetch_books=shadow_fetch_books,
        decide=decide,
        submit_fn=shadow_submit,
        cancel_fn=record_cancel,
        reconcile_fn=noop_reconcile,
        sweep_fn=noop_sweep,
        inventory_fn=settling_inventory_fn,
        open_orders_fn=_make_open_orders_fn(registry),
        emit_fn=_make_logging_emit(db_path),
    )


def run_shadow(
    *,
    minutes: float,
    db_path,
    markets_fn: Optional[Callable[..., list]] = None,
    client_fn: Optional[Callable[[], Any]] = None,
    decide_fn: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
    interval: float = 5.0,
    funder: Optional[str] = None,
) -> ShadowResult:
    """One shadow session: rotate until `minutes` elapse, record, spend nothing.

    The guard runs before anything is constructed, so even a wrong `db_path`
    fails before a table exists. Config loads through the same
    `core_brain.config.load()` the live loop uses -- same gates, same caps --
    with the live bankroll read attempted and config bankroll kept on failure,
    exactly as `trader_loop.main` does.
    """
    from dataclasses import replace as dc_replace

    from core_brain.config import load
    from core_brain.shadow_guard import assert_not_production_registry
    from core_brain.trader_loop import _fleet_state
    from core_brain.trader_loop import run as loop_run

    # Between argv and any database handle.
    assert_not_production_registry(db_path)

    cfg = load()

    # Same open question, same answer as the live loop: attempt the real
    # balance read (it needs only the public funder address), fall back to the
    # configured bankroll on any failure.
    maker = funder or os.environ.get("POLY_FUNDER")
    if maker:
        try:
            from core_brain.account import fetch_live_balance
            live_bal = fetch_live_balance(maker)
            if live_bal is not None and live_bal > 0:
                cfg = dc_replace(cfg, bankroll_usd=live_bal)
        except Exception as e:  # noqa: BLE001 - degrade, do not stop
            log.warning("live balance read failed, using config bankroll: %s", e)

    resolved_markets_fn = markets_fn or _default_markets_fn()
    markets = resolved_markets_fn()
    markets_holder = [markets]

    def dynamic_markets_fn():
        current = resolved_markets_fn()
        markets_holder[0] = current
        return current

    intents_sink: list = []
    seam = build_shadow_seam(
        db_path=db_path,
        client_fn=client_fn,
        intents_sink=intents_sink,
        decide_fn=decide_fn,
        fetch_market=_lookup_fetch_market(lambda: markets_holder[0]),
        fetch_books=fetch_books,
        cfg=cfg,
    )
    # Fleet aggregates recomputed per cycle off the SHADOW store, so the gates
    # see the same shape of numbers they would live.
    seam.fleet_state_fn = lambda r: _fleet_state(r, cfg)

    def shadow_sweep() -> None:
        """The Order Manager's U35 pass, rehearsed. Closing actions only.

        Runs `auto_manage_pairs` against the shadow store instead of the
        venue: `ShadowExecutionClient` supplies the four calls that module
        makes, and `shadow_positions` stands in for the Data API read so an
        unreachable funder never fails the pass closed. Uses `seam.fetch_books`
        -- the same book source the rest of this seam decides against -- so a
        session (or test) that injects `fetch_books` drives this pass too,
        rather than always hitting the live default underneath it.
        """
        from core_brain.shadow_exec import ShadowExecutionClient, shadow_positions
        from core_brain.single_buy_saver import auto_manage_pairs

        exec_client = ShadowExecutionClient(
            seam.registry, db_path, book_fn=seam.fetch_books)
        try:
            for pr in auto_manage_pairs(
                exec_client, seam.registry, cfg,
                venue_positions=shadow_positions(seam.registry, db_path),
            ):
                action = pr.get("action", "?")
                if action not in ("hold", "balanced"):
                    log.info("pairs %s %s", pr.get("pair_id") or "?", action)
        except (sqlite3.Error, OSError, ValueError) as e:
            log.warning("shadow pairs pass failed: %s", e)

    seam.sweep_fn = shadow_sweep

    deadline_ts = time.time() + max(0.0, minutes * 60.0)
    results = loop_run(
        seam,
        interval=interval,
        once=False,
        live=True,  # safe: submit/cancel are recorders, the client cannot sign
        markets=markets,
        markets_fn=dynamic_markets_fn,
        sleep_fn=make_deadline_sleep(deadline_ts),
    )
    return ShadowResult(
        results=results,
        intents=list(intents_sink),
        # Deliberately skipped, and reported as such -- reconcile compares
        # against venue positions a shadow run does not have. Never "passed
        # silently". `sweep` no longer belongs here: `shadow_sweep` above
        # wires it to run the single-buy pairs pass every rotation, and
        # listing a stage that ran as "skipped" is the same lie in the other
        # direction -- reporting something that happened as something that
        # did not.
        skipped_stages=("reconcile",),
    )


def _default_markets_fn() -> Callable[..., list]:
    """The graduated market list, exactly as the live loop resolves it."""
    from core_brain.trader_loop import _market_specs
    return _market_specs


def _default_fetch_books() -> Callable[[str, str], dict]:
    """The live loop's real book source: `GET {clob_host}/book`.

    Public endpoint, no key and no API credentials, and it does not travel
    through the CLOB client the deny-by-default proxy guards -- so wiring it
    here spends nothing and loads nothing that could sign. Without it the
    session decides every market against an empty book, which is a rehearsal of
    a venue that does not exist.

    `full_book` is imported per call so the entrypoint reads the module
    attribute at call time rather than binding it at wiring time.
    """
    def fetch(clob_host: str, token_id: str) -> dict:
        from core_brain.markets import full_book
        return full_book(clob_host, token_id)

    return fetch


def _default_traded_fn() -> Callable[[str, set], dict]:
    """The live tape reader. Public endpoint, no credentials, no signer."""
    def traded(condition_id: str, seen: set) -> dict:
        from core_brain.markets import recent_trades
        return recent_trades(condition_id, seen)

    return traded


def _default_fetch_market() -> Callable[[str], Any]:
    """The live loop's real market resolver (network)."""
    from core_brain.trader_loop import _fetch_market
    return _fetch_market


def _lookup_fetch_market(markets_source: Any) -> Callable[[str], Any]:
    """Resolve a cid first against the session's own market objects.

    A session handed fully-formed market objects (tests, or a caller that
    resolved markets itself) must not re-fetch them. Anything not in the
    session's list or lacking tradeable tokens falls through to the live
    loop's real resolver.
    """
    from core_brain.trader_loop import _cid, _fetch_market

    def fetch(cid: str):
        mkts = markets_source() if callable(markets_source) else markets_source
        by_cid = {_cid(m): m for m in (mkts or [])}
        m = by_cid.get(cid)
        if m is not None:
            up = getattr(m, "up_token", None) or (m.get("up_token") if isinstance(m, dict) else None)
            dn = getattr(m, "down_token", None) or (m.get("down_token") if isinstance(m, dict) else None)
            if up and dn:
                return m
        return _fetch_market(cid)

    return fetch


# --- entrypoint ---------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None):
    ap = argparse.ArgumentParser(
        description="SHADOW fleet: the full loop against the live book, "
                    "spending nothing. No signer is loaded.")
    ap.add_argument("--minutes", type=float, default=5.0,
                    help="time box in minutes (default: 5.0)")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="rotation cadence in seconds (default: 5.0)")
    ap.add_argument("--db", default=str(DEFAULT_SHADOW_DB),
                    help="shadow store path (default: data/shadow.db). "
                         "data/orders.db is refused.")
    ap.add_argument("--max-markets", type=int, default=None,
                    help="cap the number of markets rotated (default: all)")
    ap.add_argument("--funder", default=None,
                    help="funder address for the live balance read "
                         "(default: POLY_FUNDER)")
    return ap.parse_args(argv)


def main(
    argv: Optional[list[str]] = None,
    *,
    markets_fn: Optional[Callable[..., list]] = None,
    client_fn: Optional[Callable[[], Any]] = None,
    decide_fn: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
) -> int:
    """The shadow entrypoint: argparse, banner, time box, clean exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    a = _parse_args(argv)

    db = Path(a.db)
    log.warning(
        "SHADOW RUN starting: mode=shadow minutes=%s interval=%ss store=%s "
        "max_markets=%s -- NO SIGNER LOADED: this process cannot place, cancel "
        "or merge anything. Numbers below are rehearsal, not results.",
        a.minutes, a.interval, db, a.max_markets)

    result = run_shadow(
        minutes=a.minutes,
        db_path=db,
        markets_fn=(
            markets_fn or
            (lambda max_markets=None: _default_markets_fn()(a.max_markets))
        ),
        client_fn=client_fn,
        decide_fn=decide_fn,
        fetch_books=fetch_books or _default_fetch_books(),
        interval=a.interval,
        funder=a.funder,
    )

    quoted = sum(1 for r in result.results if r.status == "QUOTED")
    declined = sum(1 for r in result.results if r.status == "DECLINED")
    errors = sum(1 for r in result.results if r.status == "ERROR")
    log.warning(
        "SHADOW RUN finished: rotations_returned=%d quoted=%d declined=%d "
        "errors=%d intents_recorded=%d skipped=%s",
        len(result.results), quoted, declined, errors,
        len(result.intents), ",".join(result.skipped_stages))
    return 0 if result.results else 1


if __name__ == "__main__":
    sys.exit(main())
