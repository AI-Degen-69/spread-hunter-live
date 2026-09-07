"""Record a forward price tape, because the venue stops serving one backwards.

`/prices-history` returns a dense tape for a market that is still live -- 1441
points at 1-minute fidelity over a day -- and stops returning one once the
market resolves. Measured 2026-09-07: of 925 resolved markets over $15k of
volume, only 92 still served more than 30 points. That 90% loss is why the
question this module exists to answer cannot be settled from history at any
effort. The sample is capped by the venue, not by patience.

The question is whether a price move continues or comes back. Scored on real
resolutions the sample is one observation per market, which reached n=51 and
t=1.07 -- noise. The same test needs a few hundred resolved markets whose tape
survives, and the only way to have those is to record the tape while the
markets are open and stamp the resolution on afterwards.

Nothing here signs, quotes, or spends. It reads two public endpoints and
appends to its own SQLite file, which is never `data/orders.db`.

Recovery, not polling, is the design. Each pass backfills the last few hours at
1-minute fidelity and the store dedups on `(token_id, ts)`, so a pass that runs
late, runs twice, or restarts after a day of downtime loses no minutes and
stores no duplicates. A pass that cannot reach one market records the rest:
losing a market is a gap in one tape, and aborting is a gap in all of them.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import requests

log = logging.getLogger(__name__)

GAMMA_HOST = os.environ.get("GAMMA_HOST", "https://gamma-api.polymarket.com")
CLOB_HOST = os.environ.get("CLOB_HOST", "https://clob.polymarket.com")

#: The venue serves at most this many market rows in one page.
DISCOVERY_PAGE = 500

#: Minute fidelity is the point. A coarser tape smooths away the very moves the
#: drift test measures, and the venue silently ignores `fidelity` unless the
#: request carries an explicit `startTs`/`endTs` window.
TAPE_FIDELITY_MINUTES = 1

#: How far back each pass reaches. Comfortably longer than the poll interval so
#: an late or skipped pass still closes its own gap.
DEFAULT_BACKFILL_HOURS = 6.0

#: Default cadence. The store dedups, so polling more often only costs requests.
DEFAULT_POLL_SECONDS = 1800.0

DEFAULT_MIN_VOLUME = 15_000.0
DEFAULT_LIMIT = 120
HTTP_TIMEOUT = 25.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAPE_PATH = PROJECT_ROOT / "data" / "price_tape.db"

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS markets (
        token_id     TEXT PRIMARY KEY,
        condition_id TEXT NOT NULL,
        question     TEXT NOT NULL,
        slug         TEXT NOT NULL,
        volume_24h   REAL NOT NULL,
        first_seen   INTEGER NOT NULL,
        up_wins      INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ticks (
        token_id TEXT NOT NULL,
        ts       INTEGER NOT NULL,
        price    REAL NOT NULL,
        PRIMARY KEY (token_id, ts)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ticks_by_token ON ticks (token_id, ts)",
)


class TapeStoreError(RuntimeError):
    """The tape store could not be opened or written."""


@dataclass(frozen=True)
class TapeMarket:
    """A market whose tape is being recorded."""

    token_id: str
    condition_id: str
    question: str
    slug: str
    volume_24h: float
    up_wins: Optional[bool] = None


@dataclass(frozen=True)
class TapeSummary:
    """How much tape has been collected so far."""

    markets: int
    resolved: int
    ticks: int
    first_ts: Optional[int]
    last_ts: Optional[int]


@dataclass(frozen=True)
class PollResult:
    """What one pass managed to record."""

    markets: int
    ticks: int
    failures: int


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TapeStore:
    """Append-only SQLite tape, deduped on (token_id, ts)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                for statement in _SCHEMA:
                    conn.execute(statement)
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot open the price tape at {self.path}: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_market(self, market: TapeMarket) -> None:
        """Register a market, keeping the row already stored if there is one.

        A pass re-discovers the same markets every time; re-registering must not
        wipe a resolution already stamped on, so this inserts and does nothing
        on conflict rather than replacing.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO markets "
                    "(token_id, condition_id, question, slug, volume_24h, first_seen, up_wins) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL) "
                    "ON CONFLICT(token_id) DO NOTHING",
                    (market.token_id, market.condition_id, market.question,
                     market.slug, market.volume_24h, int(time.time())),
                )
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot record market {market.token_id}: {exc}") from exc

    def append_ticks(self, token_id: str, ticks: Iterable[tuple[int, float]]) -> int:
        """Store ticks, skipping every minute already held. Returns the count added."""
        rows = [(token_id, ts, price) for ts, price in ticks]
        if not rows:
            return 0
        try:
            with self._connect() as conn:
                before = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
                conn.executemany(
                    "INSERT INTO ticks (token_id, ts, price) VALUES (?, ?, ?) "
                    "ON CONFLICT(token_id, ts) DO NOTHING",
                    rows,
                )
                after = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot append ticks for {token_id}: {exc}") from exc
        return after - before

    def mark_resolved(self, token_id: str, up_wins: bool) -> None:
        try:
            with self._connect() as conn:
                conn.execute("UPDATE markets SET up_wins = ? WHERE token_id = ?",
                             (1 if up_wins else 0, token_id))
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot resolve {token_id}: {exc}") from exc

    def get_market(self, token_id: str) -> Optional[TapeMarket]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT token_id, condition_id, question, slug, volume_24h, up_wins "
                    "FROM markets WHERE token_id = ?", (token_id,)).fetchone()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot read market {token_id}: {exc}") from exc
        if row is None:
            return None
        return TapeMarket(
            token_id=row["token_id"], condition_id=row["condition_id"],
            question=row["question"], slug=row["slug"],
            volume_24h=row["volume_24h"],
            up_wins=None if row["up_wins"] is None else bool(row["up_wins"]),
        )

    def tracked_tokens(self) -> list[str]:
        """Tokens still worth polling: everything without a resolution."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT token_id FROM markets WHERE up_wins IS NULL "
                    "ORDER BY first_seen, token_id").fetchall()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot list tracked markets: {exc}") from exc
        return [row["token_id"] for row in rows]

    def summary(self) -> TapeSummary:
        try:
            with self._connect() as conn:
                markets = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
                resolved = conn.execute(
                    "SELECT COUNT(*) FROM markets WHERE up_wins IS NOT NULL").fetchone()[0]
                ticks, first_ts, last_ts = conn.execute(
                    "SELECT COUNT(*), MIN(ts), MAX(ts) FROM ticks").fetchone()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot summarise the tape: {exc}") from exc
        return TapeSummary(markets=markets, resolved=resolved, ticks=ticks,
                           first_ts=first_ts, last_ts=last_ts)


def parse_history(payload: Any) -> list[tuple[int, float]]:
    """The venue's history payload as ticks, dropping every point it mangled.

    A single unusable point is a gap of one minute; refusing the whole payload
    is a gap of hours, so the bad points are dropped and the rest is kept.
    """
    if not isinstance(payload, dict):
        return []
    history = payload.get("history")
    if not isinstance(history, list):
        return []
    ticks: list[tuple[int, float]] = []
    for point in history:
        if not isinstance(point, dict):
            continue
        ts = _as_int(point.get("t"))
        price = _as_float(point.get("p"))
        if ts is None or price is None:
            continue
        ticks.append((ts, price))
    return ticks


def parse_market_row(row: Any) -> Optional[TapeMarket]:
    """One gamma market row as a TapeMarket, or None when it is unusable."""
    if not isinstance(row, dict):
        return None
    condition_id = str(row.get("conditionId") or "")
    if not condition_id:
        return None
    raw_tokens = row.get("clobTokenIds")
    if isinstance(raw_tokens, str):
        try:
            tokens: Sequence[Any] = json.loads(raw_tokens)
        except (ValueError, TypeError):
            return None
    elif isinstance(raw_tokens, list):
        tokens = raw_tokens
    else:
        return None
    if not isinstance(tokens, list) or len(tokens) != 2:
        return None
    return TapeMarket(
        token_id=str(tokens[0]),
        condition_id=condition_id,
        question=str(row.get("question") or ""),
        slug=str(row.get("slug") or ""),
        volume_24h=_as_float(row.get("volume24hr")) or 0.0,
    )


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    for scheme in ("https://", "http://"):
        session.mount(scheme, requests.adapters.HTTPAdapter(
            pool_connections=8, pool_maxsize=8, max_retries=0))
    return session


def discover_markets(
    *,
    session: Any,
    min_volume: float = DEFAULT_MIN_VOLUME,
    limit: int = DEFAULT_LIMIT,
    gamma_host: str = GAMMA_HOST,
) -> list[TapeMarket]:
    """Live markets worth recording, most traded first."""
    response = session.get(
        f"{gamma_host}/markets",
        params={"closed": "false", "active": "true", "limit": DISCOVERY_PAGE,
                "order": "volume24hr", "ascending": "false"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        return []
    markets: list[TapeMarket] = []
    for row in rows:
        if len(markets) >= limit:
            break
        market = parse_market_row(row)
        if market is None or market.volume_24h < min_volume:
            continue
        markets.append(market)
    return markets


def backfill(
    store: TapeStore,
    token_id: str,
    *,
    session: Any,
    hours: float = DEFAULT_BACKFILL_HOURS,
    clob_host: str = CLOB_HOST,
    now: Optional[int] = None,
) -> int:
    """Pull the last `hours` of minute tape for one token. Returns ticks added.

    The window is explicit because `interval=max` silently ignores `fidelity`
    and hands back a coarse tape; only `startTs`/`endTs` honours minute detail.
    """
    end_ts = int(time.time()) if now is None else int(now)
    start_ts = end_ts - int(hours * 3600)
    response = session.get(
        f"{clob_host}/prices-history",
        params={"market": token_id, "startTs": start_ts, "endTs": end_ts,
                "fidelity": TAPE_FIDELITY_MINUTES},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return store.append_ticks(token_id, parse_history(response.json()))


def poll_once(
    store: TapeStore,
    *,
    session: Any = None,
    min_volume: float = DEFAULT_MIN_VOLUME,
    limit: int = DEFAULT_LIMIT,
    hours: float = DEFAULT_BACKFILL_HOURS,
    gamma_host: str = GAMMA_HOST,
    clob_host: str = CLOB_HOST,
) -> PollResult:
    """One recording pass: discover, then backfill each market's tape.

    A market the venue refuses is counted and skipped. Aborting the pass would
    turn one missing tape into a gap across every market being recorded.
    """
    session = session or _new_session()
    markets = discover_markets(session=session, min_volume=min_volume,
                               limit=limit, gamma_host=gamma_host)
    ticks = failures = 0
    for market in markets:
        store.record_market(market)
        try:
            ticks += backfill(store, market.token_id, session=session,
                              hours=hours, clob_host=clob_host)
        except (requests.RequestException, ValueError) as exc:
            failures += 1
            log.warning("tape backfill failed for %s: %s", market.token_id, exc)
    return PollResult(markets=len(markets), ticks=ticks, failures=failures)


def refresh_resolutions(
    store: TapeStore,
    *,
    session: Any = None,
    gamma_host: str = GAMMA_HOST,
) -> int:
    """Stamp resolutions onto tracked markets. Returns how many were stamped.

    Only a clean binary outcome is recorded. An ambiguous or partial resolution
    cannot score a position, so it is left unresolved rather than guessed at.
    """
    session = session or _new_session()
    tracked = set(store.tracked_tokens())
    if not tracked:
        return 0
    response = session.get(
        f"{gamma_host}/markets",
        params={"closed": "true", "limit": DISCOVERY_PAGE,
                "order": "endDate", "ascending": "false"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        return 0
    stamped = 0
    for row in rows:
        market = parse_market_row(row)
        if market is None or market.token_id not in tracked:
            continue
        if not row.get("closed"):
            continue
        raw_prices = row.get("outcomePrices")
        if isinstance(raw_prices, str):
            try:
                prices = json.loads(raw_prices)
            except (ValueError, TypeError):
                continue
        elif isinstance(raw_prices, list):
            prices = raw_prices
        else:
            continue
        if not isinstance(prices, list) or not prices:
            continue
        up_final = _as_float(prices[0])
        if up_final not in (0.0, 1.0):
            continue
        store.mark_resolved(market.token_id, up_final == 1.0)
        stamped += 1
    return stamped


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_TAPE_PATH),
                        help="where the tape is written")
    parser.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME,
                        help="24h volume a market must clear to be recorded")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="stop after this many markets per pass")
    parser.add_argument("--hours", type=float, default=DEFAULT_BACKFILL_HOURS,
                        help="how far back each pass reaches")
    parser.add_argument("--interval", type=float, default=DEFAULT_POLL_SECONDS,
                        help="seconds between passes")
    parser.add_argument("--once", action="store_true",
                        help="record a single pass and exit")
    parser.add_argument("--status", action="store_true",
                        help="print what has been collected and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = TapeStore(args.db)

    if args.status:
        s = store.summary()
        span = "" if s.first_ts is None else \
            f"  span {(s.last_ts - s.first_ts) / 86400.0:.1f}d"
        print(f"markets {s.markets} ({s.resolved} resolved)  ticks {s.ticks:,}{span}")
        return 0

    session = _new_session()
    while True:
        result = poll_once(store, session=session, min_volume=args.min_volume,
                           limit=args.limit, hours=args.hours)
        stamped = refresh_resolutions(store, session=session)
        s = store.summary()
        log.info("pass: %d markets, +%d ticks, %d failures, %d newly resolved "
                 "| total %d markets (%d resolved), %d ticks",
                 result.markets, result.ticks, result.failures, stamped,
                 s.markets, s.resolved, s.ticks)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(_main())
