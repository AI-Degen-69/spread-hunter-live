"""Safety guard for shadow runs.

A shadow run rehearses the full loop against the live venue and must be
structurally incapable of spending money. Two mechanisms, both in this module:

1. `ReadOnlyVenue` -- a deny-by-default proxy around the CLOB client. Only reads
   we have vetted pass through; everything else raises. Not an allowlist of
   writes: that would be a list we must keep in sync with an SDK we do not
   control, and the day it adds a new submit spelling the list is already wrong.
2. `ShadowSafetyViolation` -- a BaseException, so the loop's `except Exception`
   error isolation cannot downgrade a safety breach to a logged warning.

The credential rule is stronger here than in `core_brain/venue.py`. That module
reads a key and passes it on in a single expression. This one never reads one at
all: a shadow run holds nothing it could authenticate a write with.
"""
from __future__ import annotations

#: CLOB client reads the loop needs. Vetted one by one; nothing is here because
#: it looked harmless. Adding to this set is a safety decision, not plumbing.
READ_METHODS = frozenset({
    "get_order_book",
    "get_order_books",
    "get_market",
    "get_markets",
    "get_midpoint",
    "get_midpoints",
    "get_price",
    "get_prices",
    "get_last_trade_price",
    "get_spread",
    "get_spreads",
    "get_tick_size",
    "get_neg_risk",
    "get_sampling_markets",
})


class ShadowSafetyViolation(BaseException):
    """Raised when a shadow run reaches a path that could move money.

    Derives from BaseException, not Exception, and the reason is the same one
    that shaped `tests/conftest.py:ProductionRegistryWriteError`: the loop this
    guard runs inside isolates reconcile, sweep and every market visit behind
    `except Exception` (`core_brain/trader_loop.py`). A violation caught by one
    of those handlers would be logged as a warning while the run carried on,
    which is precisely the outcome the guard exists to make impossible.
    """


class ReadOnlyVenue:
    """Deny-by-default proxy around a CLOB client.

    Attribute access succeeds only for names in `READ_METHODS`. Everything else
    -- submits, cancels, and anything the SDK grows that we have not vetted --
    raises `ShadowSafetyViolation` before the inner client is touched.
    """

    def __init__(self, inner: object) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str):
        if name in READ_METHODS:
            return getattr(object.__getattribute__(self, "_inner"), name)
        raise ShadowSafetyViolation(
            f"shadow run reached {name!r}, which is not a vetted read. "
            f"No credential is loaded, so this call could not have been signed -- "
            f"but it must not be attempted either."
        )

    def __setattr__(self, name: str, value) -> None:
        raise ShadowSafetyViolation(
            f"shadow run tried to set {name!r} on the venue client. "
            f"Mutating the client is how credentials get attached."
        )


DEFAULT_CLOB_HOST = "https://clob.polymarket.com"


def _build_unauthenticated_client(host: str):
    """A CLOB client with no key and no API credentials.

    `core_brain/venue.py:client` passes `key=`, then either sets L2 creds from
    the environment or derives them. Neither happens here. The reads shadow mode
    needs -- books, prices, market metadata -- are public.

    Imported lazily so this module stays importable without the SDK, matching
    how `core_brain/venue.py` guards its User-Agent patch.
    """
    from py_clob_client_v2.client import ClobClient

    return ClobClient(host, chain_id=137)


def shadow_client(build_fn=None, host: str | None = None) -> ReadOnlyVenue:
    """The venue client a shadow run gets: unauthenticated, read-only, denying.

    `build_fn` is injectable so tests can drive the proxy without the SDK or a
    network, mirroring how `core_brain/trader_loop.py:VenueSeam` injects every
    venue-touching port.
    """
    import os

    build = build_fn or _build_unauthenticated_client
    return ReadOnlyVenue(build(host or os.environ.get("CLOB_HOST", DEFAULT_CLOB_HOST)))


def _uri_to_path(raw: str) -> str | None:
    """Resolve a SQLite `file:` URI to the path it actually opens.

    Returns None when the URI does not name a local file. Mirrors
    `tests/conftest.py:_uri_to_path`, and for the same reason: SQLite decodes
    percent escapes and accepts a `localhost` authority, so comparing raw URI
    text lets `file:data/orders%2edb` and `file://localhost/<abs>` walk past a
    guard that only strips the scheme.
    """
    import re
    import urllib.parse

    parsed = urllib.parse.urlparse(raw)
    if parsed.netloc not in ("", "localhost"):
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if "memory" in query.get("mode", ()):
        return None
    path = urllib.parse.unquote(parsed.path)
    # `file:///C:/x` parses to `/C:/x`; drop the slash in front of the drive.
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path or None


def assert_not_production_registry(db_path) -> None:
    """Refuse `data/orders.db` as a shadow run's store.

    AGENTS.md: the production registry is read, never rewritten. A shadow run
    fabricates fills, so a shadow row in that file is corruption of the real
    order history -- and unlike a bad trade, nothing downstream would flag it.

    Only the write target is refused. Reading the production registry is a
    separate decision and is not blocked here.
    """
    import os
    from pathlib import Path

    from core_brain.order_registry import DEFAULT_DB_PATH

    if not isinstance(db_path, (str, os.PathLike)):
        return
    raw = os.fspath(db_path)
    if not raw or raw == ":memory:":
        return
    if raw.startswith("file:"):
        resolved = _uri_to_path(raw)
        if resolved is None:
            return
        raw = resolved
    try:
        same = Path(raw).resolve() == DEFAULT_DB_PATH.resolve()
    except (OSError, ValueError):
        return
    if same:
        raise ShadowSafetyViolation(
            f"shadow run pointed at the production registry {DEFAULT_DB_PATH}. "
            f"A shadow run fabricates fills; those rows must never enter the "
            f"real order history. Use data/shadow.db or another path."
        )
