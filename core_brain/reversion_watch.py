"""Forward paper test: does the measured esports reversion survive REAL quotes?

Recorded history says that after a price move of 3c or more inside five minutes,
an esports match-winner market comes back over the next fifteen -- 1007 cases
across six days of League of Legends tape, 823 of them in the 0.35-0.65 band at
-2.46c. Every one of those numbers came from a HISTORY price, which is not a
price anyone could have transacted at.

This module closes that gap. At each jump it stores the quote actually on the
book -- the bid you would hit to fade an up-move, the ask you would lift to fade
a down-move -- and the opposite quote fifteen minutes later. The difference is
what the trade would really have paid, the spread already taken out of it.

**It sends nothing.** No signer is loaded, no wallet is read, no order is built.
Two public read endpoints, and its own SQLite file, which is never
`data/orders.db` -- `reversion_view.resolve_reversion_db` refuses that name and
this module writes only where it is told.

The gates match the backward measurement exactly, because a forward number
counted over different markets would not be comparable to it: match-winner
markets only, price inside 0.10-0.90 so a decided game is not traded, and a book
no wider than 1.5c. Events are TAGGED by band rather than filtered to one, so
the tails stay visible instead of being assumed away.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

from core_brain import price_tape as pt

log = logging.getLogger(__name__)

#: A move this large is what the backward measurement called a jump.
JUMP = 0.03
#: The window the jump must happen inside, in seconds.
LOOK = 300
#: How long the fade is held before it is scored, in seconds.
FORWARD = 900
#: Seconds between book reads. The tape it is compared against is per-minute.
POLL = 60
#: Seconds between refreshes of the live market list. Games start and end.
REFRESH = 900

#: Only plain match-winner markets. Handicap lines and alternate totals were
#: never in the backward measurement, and counting them here would produce a
#: forward number that answers a different question.
KINDS = ("moneyline", "child_moneyline")
#: Outside this the game is effectively decided and the rest is resolution.
PRICE_LO, PRICE_HI = 0.10, 0.90
#: The band that carried 82% of the measured events, and the size with it.
MID_LO, MID_HI = 0.35, 0.65
#: A book wider than this is not the book the edge was measured against.
MAX_SPREAD_C = 1.5
#: Leagues watched by default: the esports that actually move.
DEFAULT_LEAGUES = "lol,cs2,dota2"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "reversion.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    slug        TEXT NOT NULL,
    league      TEXT,
    band        TEXT,
    in_game     INTEGER,
    direction   TEXT NOT NULL,
    mid_before  REAL,
    mid_now     REAL,
    entry_px    REAL NOT NULL,
    entry_size  REAL,
    spread_c    REAL,
    exit_ts     INTEGER,
    exit_px     REAL,
    exit_size   REAL,
    pnl_c       REAL
);
CREATE INDEX IF NOT EXISTS events_by_league ON events (league, band);
CREATE TABLE IF NOT EXISTS quotes (
    slug TEXT NOT NULL,
    ts   INTEGER NOT NULL,
    bid  REAL,
    ask  REAL,
    PRIMARY KEY (slug, ts)
);
"""


def open_store(path: str | Path) -> sqlite3.Connection:
    """Create or open the recorder's own store."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def band_of(mid: float) -> str:
    """Which half of the board this price sits in."""
    return "mid" if MID_LO <= mid <= MID_HI else "tail"


def has_started(start_raw: str, now: int) -> Optional[int]:
    """Has the game begun? The venue stamps `YYYY-MM-DD HH:MM:SS+00`.

    Returned as 1/0/None rather than a bool because an unstamped market is a
    third state: not "before the game", but "the venue never said".
    """
    if not start_raw[:4].isdigit():
        return None
    stamped = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
    return 1 if start_raw[:19] <= stamped else 0


def live_games(session: Any, leagues: set[str],
               gamma_host: str = pt.GAMMA_HOST) -> dict[str, dict[str, Any]]:
    """Every live match-winner market in the named leagues, with a book.

    League of Legends alone offers one or two eligible markets at a time, so a
    LoL-only watch collects almost nothing between fixtures. The other esports
    leagues are watched too and TAGGED separately -- more events per hour, while
    the leagues are still scored apart, because Dota measured a reversion of
    about zero where LoL measured 2-3c and pooling them would hide both.
    """
    found: dict[str, dict[str, Any]] = {}
    for offset in (0, 100, 200, 300):
        try:
            response = session.get(
                f"{gamma_host}/markets",
                params={"closed": "false", "active": "true",
                        "order": "volume24hr", "ascending": "false",
                        "limit": 100, "offset": offset},
                timeout=pt.HTTP_TIMEOUT)
            response.raise_for_status()
            page = response.json()
        except Exception as exc:            # noqa: BLE001 - one page, not the run
            log.warning("market listing failed at offset %d: %s", offset, exc)
            break
        if not isinstance(page, list) or not page:
            break
        for row in page:
            slug = str(row.get("slug") or "")
            league = slug.split("-")[0] if "-" in slug else ""
            if league not in leagues or not row.get("enableOrderBook"):
                continue
            if str(row.get("sportsMarketType") or "") not in KINDS:
                continue
            market = pt.parse_market_row(row)
            if market is None:
                continue
            found[slug] = {"slug": slug, "token": market.token_id,
                           "league": league,
                           "start_raw": str(row.get("gameStartTime") or "")}
    return found


def read_book(session: Any, token_id: str,
              clob_host: str = pt.CLOB_HOST) -> Optional[dict[str, float]]:
    """The top of one book, or None when it is not two-sided.

    A one-sided book has no mid to measure a move against and no quote to trade
    out of, so it is a hole rather than a price.
    """
    response = session.get(f"{clob_host}/book", params={"token_id": token_id},
                           timeout=15)
    response.raise_for_status()
    payload = response.json()
    bids = [(float(x["price"]), float(x["size"]))
            for x in (payload.get("bids") or [])]
    asks = [(float(x["price"]), float(x["size"]))
            for x in (payload.get("asks") or [])]
    if not bids or not asks:
        return None
    best_bid = max(bids, key=lambda x: x[0])
    best_ask = min(asks, key=lambda x: x[0])
    return {"bid": best_bid[0], "bid_size": best_bid[1],
            "ask": best_ask[0], "ask_size": best_ask[1],
            "mid": (best_bid[0] + best_ask[0]) / 2.0}


def fade_entry(book: dict[str, float], move: float) -> tuple[str, float, float]:
    """The side, price and size of the trade that fades this move.

    Fading an up-move means selling, which happens at the BID -- not at the mid
    the move was measured on. Using the mid here is the single mistake that
    would make the forward number agree with the backward one for free.
    """
    if move > 0:
        return "up", book["bid"], book["bid_size"]
    return "down", book["ask"], book["ask_size"]


def fade_exit(book: dict[str, float], direction: str) -> tuple[float, float]:
    """The quote the fade is closed at: the opposite side of the same book."""
    if direction == "up":
        return book["ask"], book["ask_size"]
    return book["bid"], book["bid_size"]


def realised_cents(direction: str, entry_px: float, exit_px: float) -> float:
    """Cents per share the round trip actually made, spread included."""
    if direction == "up":
        return (entry_px - exit_px) * 100.0
    return (exit_px - entry_px) * 100.0


def watch(conn: sqlite3.Connection, *, session: Any, leagues: set[str],
          minutes: float, now_fn=time.time, sleep_fn=time.sleep) -> int:
    """Run the watch until `minutes` are up. Returns events opened.

    Injected clock and sleep so a test can run the loop without waiting on one.
    """
    markets = live_games(session, leagues)
    refreshed = now_fn()
    log.info("watching %d live match-winner markets in %s",
             len(markets), sorted(leagues))
    history: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
    open_trades: list[dict[str, Any]] = []
    deadline = now_fn() + minutes * 60
    opened = 0

    while now_fn() < deadline:
        loop_start = now_fn()
        if loop_start - refreshed > REFRESH:
            markets = live_games(session, leagues)
            refreshed = loop_start

        now = int(now_fn())
        for slug, market in list(markets.items()):
            try:
                book = read_book(session, market["token"])
            except Exception as exc:        # noqa: BLE001 - one market, not the run
                log.debug("book unreadable for %s: %s", slug, exc)
                continue
            if book is None:
                continue
            conn.execute("INSERT OR IGNORE INTO quotes VALUES (?,?,?,?)",
                         (slug, now, book["bid"], book["ask"]))
            mid = book["mid"]
            # Recorded either way; traded only inside the measured gates.
            if not PRICE_LO <= mid <= PRICE_HI:
                continue
            if (book["ask"] - book["bid"]) * 100.0 > MAX_SPREAD_C:
                continue
            earlier = [(t, p) for t, p in history[slug] if now - t >= LOOK - POLL]
            history[slug].append((now, mid))
            if not earlier:
                continue
            _, was = earlier[-1]
            move = mid - was
            if abs(move) < JUMP:
                continue
            direction, entry_px, entry_size = fade_entry(book, move)
            cursor = conn.execute(
                "INSERT INTO events (ts,slug,league,band,in_game,direction,"
                "mid_before,mid_now,entry_px,entry_size,spread_c) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (now, slug, market["league"], band_of(mid),
                 has_started(market["start_raw"], now), direction, was, mid,
                 entry_px, entry_size, (book["ask"] - book["bid"]) * 100.0))
            open_trades.append({"id": cursor.lastrowid, "token": market["token"],
                                "direction": direction, "entry_px": entry_px,
                                "due": now + FORWARD})
            opened += 1
            history[slug].clear()
            log.info("jump %s [%s] %s %.3f -> %.3f, entry %.3f",
                     slug, band_of(mid), direction, was, mid, entry_px)
        conn.commit()

        pending = []
        for trade in open_trades:
            if now_fn() < trade["due"]:
                pending.append(trade)
                continue
            try:
                book = read_book(session, trade["token"])
            except Exception as exc:        # noqa: BLE001 - retry next pass
                log.debug("exit book unreadable: %s", exc)
                pending.append(trade)
                continue
            if book is None:
                pending.append(trade)
                continue
            exit_px, exit_size = fade_exit(book, trade["direction"])
            conn.execute(
                "UPDATE events SET exit_ts=?, exit_px=?, exit_size=?, pnl_c=? "
                "WHERE id=?",
                (int(now_fn()), exit_px, exit_size,
                 realised_cents(trade["direction"], trade["entry_px"], exit_px),
                 trade["id"]))
        open_trades = pending
        conn.commit()
        sleep_fn(max(0.0, POLL - (now_fn() - loop_start)))
    return opened


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help="where the forward test writes")
    parser.add_argument("--minutes", type=float, default=720.0,
                        help="how long to watch")
    parser.add_argument("--leagues", default=DEFAULT_LEAGUES,
                        help="league slug prefixes, scored separately")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    conn = open_store(args.db)
    leagues = {part.strip() for part in args.leagues.split(",") if part.strip()}
    watch(conn, session=pt._new_session(), leagues=leagues,
          minutes=args.minutes)
    print(f"{'league':<8}{'band':<6}{'trades':>8}{'cents/share':>13}")
    for league, band, count, mean in conn.execute(
            "SELECT league, band, COUNT(*), AVG(pnl_c) FROM events "
            "WHERE pnl_c IS NOT NULL GROUP BY league, band "
            "ORDER BY band DESC, COUNT(*) DESC"):
        print(f"{league or '?':<8}{band or '?':<6}{count:>8}{mean or 0.0:>12.2f}c")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
