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


def build_shadow_seam(
    *,
    db_path,
    client_fn: Optional[Callable[[], Any]] = None,
    intents_sink: Optional[list] = None,
    decide_fn: Optional[Callable] = None,
    fetch_market: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
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
    from functools import partial

    from core_brain.cycle_stream import emit as _emit_cycle_event
    from core_brain.order_registry import OrderRegistry, init_db
    from core_brain.quotes import decide_quotes
    from core_brain.shadow_guard import (
        assert_not_production_registry,
        shadow_client,
    )
    from core_brain.trader_loop import VenueSeam, _make_inventory_fn, _make_open_orders_fn

    assert_not_production_registry(db_path)

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

    def record_submit(_client, _registry, market, intents, _cfg) -> int:
        for qi in intents:
            intents_sink.append(ShadowIntent.from_quote(
                qi, market.condition_id))
        return len(intents)

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
        submit_fn=record_submit,
        cancel_fn=record_cancel,
        reconcile_fn=noop_reconcile,
        sweep_fn=noop_sweep,
        inventory_fn=_make_inventory_fn(registry, Path(db_path)),
        open_orders_fn=_make_open_orders_fn(registry),
        emit_fn=partial(_emit_cycle_event, db_path=db_path),
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

    markets = (markets_fn or _default_markets_fn())()

    intents_sink: list = []
    seam = build_shadow_seam(
        db_path=db_path,
        client_fn=client_fn,
        intents_sink=intents_sink,
        decide_fn=decide_fn,
        fetch_market=_lookup_fetch_market(markets),
        fetch_books=fetch_books,
        cfg=cfg,
    )
    # Fleet aggregates recomputed per cycle off the SHADOW store, so the gates
    # see the same shape of numbers they would live.
    seam.fleet_state_fn = lambda r: _fleet_state(r, cfg)

    deadline_ts = time.time() + max(0.0, minutes * 60.0)
    results = loop_run(
        seam,
        interval=interval,
        once=False,
        live=True,  # safe: submit/cancel are recorders, the client cannot sign
        markets=markets,
        sleep_fn=make_deadline_sleep(deadline_ts),
    )
    return ShadowResult(
        results=results,
        intents=list(intents_sink),
        # Deliberately skipped, and reported as such -- they reconcile against
        # venue positions a shadow run does not have. Never "passed silently".
        skipped_stages=("reconcile", "sweep"),
    )


def _default_markets_fn() -> Callable[..., list]:
    """The graduated market list, exactly as the live loop resolves it."""
    from core_brain.trader_loop import _market_specs
    return _market_specs


def _default_fetch_market() -> Callable[[str], Any]:
    """The live loop's real market resolver (network)."""
    from core_brain.trader_loop import _fetch_market
    return _fetch_market


def _lookup_fetch_market(markets: list) -> Callable[[str], Any]:
    """Resolve a cid first against the session's own market objects.

    A session handed fully-formed market objects (tests, or a caller that
    resolved markets itself) must not re-fetch them. Anything not in the
    session's list falls through to the live loop's real resolver.
    """
    from core_brain.trader_loop import _cid, _fetch_market

    by_cid = {_cid(m): m for m in markets}

    def fetch(cid: str):
        m = by_cid.get(cid)
        return m if m is not None else _fetch_market(cid)

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
        fetch_books=fetch_books,
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
