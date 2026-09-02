"""Every grace value at once, from a single run held at the longest one.

A four-arm sweep of `single_buy_grace_sec` would split an already thin fill
count four ways and reproduce the confound the #138 sweep could not remove:
arm and hour never separated. This reads one run held at the MAXIMUM grace and
recovers every shorter value from it, because a longer grace CONTAINS the
shorter ones. If the companion leg fills 30s after the first, then grace=45 and
grace=120 both caught it and grace=15 did not — that is a filter on a recorded
distribution, not a second experiment.

Two things decide the answer and this reports both:

  * **Rescue rate.** Of the legs that filled alone, how many had their
    companion fill within the horizon? Those are pairs a longer grace converts
    from a loss into a merge.
  * **The cost of waiting.** A leg held 120s exits at the 120s bid, not the
    15s one. Holding longer rescues more pairs AND books a worse exit on the
    ones it fails to rescue. Reporting the rescue rate alone would recommend
    an unbounded grace.

The exit side is repriced from a `book_tape_recorder` store covering the same
window, so the bid at each horizon is measured rather than modelled. Without
one, the cost column is reported as unmeasured rather than guessed.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Optional

# The rotation cadence is the real floor under this knob. `_route_pair` exits
# on the first poll AFTER the grace expires, so a grace below the interval is
# indistinguishable from a grace equal to it: a companion that arrives 0.9s
# after its first leg is already there when the 5s poll looks. A `grace=0` row
# would read as "dump instantly" and recommend against a knob the engine
# cannot actually set that low.
POLL_INTERVAL_SEC = 5.0
HORIZONS = (POLL_INTERVAL_SEC, 15.0, 45.0, 120.0)
MERGE_GAIN_PER_SHARE = 0.01      # a pair earns one tick; merges are gasless (#152)


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


MS_PER_SEC = 1000.0


def leg_events(conn: sqlite3.Connection) -> list[dict]:
    """Every fill, keyed by the PAIR the engine assigned it, time in seconds.

    Four joins-worth of care, each of which returned silent nonsense first:

      * `fills.order_uuid` carries the registry's `orders.id`, not its
        `orders.order_id` -- the latter is the venue-side handle
        (`shadow-...`). Joining on `order_id` matches zero rows and the report
        announces there is nothing to sweep.
      * `orders.side` is the ORDER side: every row reads `BUY`. The UP/DOWN
        leg lives on the token, so the side comes from the quotes ledger.
      * `fills.recorded_ts` is in MILLISECONDS while `quotes.ts` and the book
        recorder's `ts` are in seconds. Left alone a 1.4-second wait reads as
        1,400 and no horizon matches.
      * **Pairing is read from `orders.pair_id`, not reconstructed.** Matching
        the next opposite-side fill in the same market looks equivalent and is
        not: a market can carry several pairs at once, and first-in-first-out
        across them married legs the engine never grouped. On this run that
        produced a phantom UP 0.42 + DOWN 0.73 = $1.15 pair -- over the dollar
        the instrument pays, and impossible, because `risk.hard_block` had
        refused exactly that. The engine already recorded which leg belongs to
        which pair; second-guessing it invented a gate failure that never
        happened.
    """
    rows = conn.execute(
        "SELECT o.pair_id AS pair_id, f.recorded_ts / ? AS ts,"
        "       f.size AS size, f.price AS price,"
        "       o.condition_id AS cid, q.side AS side"
        " FROM fills f"
        " JOIN orders o ON o.id = f.order_uuid"
        " LEFT JOIN (SELECT DISTINCT token_id, side FROM quotes) q"
        "   ON q.token_id = o.token_id"
        " WHERE f.size > 0 AND o.pair_id IS NOT NULL"
        " ORDER BY f.recorded_ts",
        (MS_PER_SEC,)
    ).fetchall()
    return [dict(r) for r in rows]


def companion_gaps(events: list[dict]) -> list[dict]:
    """Per pair: the first leg, and how long the second took to arrive.

    `gap` is None when only one leg of that pair ever filled -- the leg was
    stranded for good and no grace value rescues it. Those rows are the
    denominator that keeps the rescue rate honest.
    """
    by_pair: dict[str, list[dict]] = {}
    for e in events:
        by_pair.setdefault(e["pair_id"], []).append(e)

    out: list[dict] = []
    for pair_id, evs in by_pair.items():
        evs.sort(key=lambda e: e["ts"])
        first = evs[0]
        companion = next((e for e in evs[1:] if e["side"] != first["side"]), None)
        out.append({"pair_id": pair_id, "cid": first["cid"],
                    "side": first["side"], "ts": first["ts"],
                    "size": first["size"], "price": first["price"],
                    "gap": (companion["ts"] - first["ts"]) if companion else None})
    return out


def bid_at(book: Optional[sqlite3.Connection], cid: str, side: str,
           ts: float) -> Optional[float]:
    """The best bid on `side` at or just after `ts`, from the book recorder.

    "Just after" rather than "nearest": an exit at t+15s is filled by the book
    as it stands then, and a sample taken before the horizon would price the
    exit on information the exit did not have.
    """
    if book is None:
        return None
    col = "best_bid_up" if side == "UP" else "best_bid_down"
    row = book.execute(
        f"SELECT {col} AS bid FROM book_samples"
        f" WHERE condition_id = ? AND ts >= ? AND {col} IS NOT NULL"
        f" ORDER BY ts LIMIT 1", (cid, ts)).fetchone()
    return float(row["bid"]) if row else None


def sweep(store: Path, book_store: Optional[Path]) -> str:
    conn = _ro(store)
    book = _ro(book_store) if book_store else None
    try:
        events = leg_events(conn)
        legs = companion_gaps(events)
        out = [f"grace sweep -- {store}",
               f"{len(events)} fills, {len(legs)} legs that opened a position",
               ""]
        if not legs:
            out.append("no legs recorded; nothing to sweep")
            return "\n".join(out)

        rescued_ever = [l for l in legs if l["gap"] is not None]
        out.append("COMPANION WAIT -- how long the second leg took")
        if rescued_ever:
            gaps = sorted(l["gap"] for l in rescued_ever)
            out.append(f"  paired at all : {len(rescued_ever)}/{len(legs)}")
            out.append(f"  wait seconds  : min {gaps[0]:.1f}  "
                       f"median {gaps[len(gaps)//2]:.1f}  max {gaps[-1]:.1f}")
        else:
            out.append(f"  paired at all : 0/{len(legs)} -- no companion ever filled")
        out.append("")

        out.append("EVERY GRACE VALUE, FROM THE ONE RUN")
        out.append("  rescued = companion filled inside the horizon, so the pair merges")
        out.append(f"  the {POLL_INTERVAL_SEC:.0f}s row is the floor: the loop cannot "
                   f"exit sooner than its own poll")
        out.append("  exit    = the rest, sold at the bid as it stood at the horizon")
        out.append(f"  {'grace':>7}{'rescued':>10}{'rate':>9}"
                   f"{'merge $':>10}{'exit $':>10}{'net $':>10}")
        for h in HORIZONS:
            rescued = [l for l in legs if l["gap"] is not None and l["gap"] <= h]
            stranded = [l for l in legs if l not in rescued]
            merge_usd = sum(l["size"] * MERGE_GAIN_PER_SHARE for l in rescued)
            exit_usd, priced = 0.0, 0
            for l in stranded:
                bid = bid_at(book, l["cid"], l["side"], l["ts"] + h)
                if bid is None:
                    continue
                exit_usd += (bid - l["price"]) * l["size"]
                priced += 1
            cell = f"{exit_usd:>10.4f}" if priced else f"{'unmeasured':>10}"
            net = f"{merge_usd + exit_usd:>10.4f}" if priced else f"{'--':>10}"
            out.append(f"  {h:>7.0f}{len(rescued):>10}"
                       f"{100.0*len(rescued)/len(legs):>8.1f}%"
                       f"{merge_usd:>10.4f}{cell}{net}")
        if book is None:
            out.append("")
            out.append("  exit column unmeasured: pass --book-db with a "
                       "book_tape_recorder store covering the same window.")
        return "\n".join(out)
    finally:
        conn.close()
        if book is not None:
            book.close()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="shadow store held at max grace")
    ap.add_argument("--book-db", default=None,
                    help="book_tape_recorder store covering the same window")
    args = ap.parse_args(argv)
    print(sweep(Path(args.db),
                Path(args.book_db) if args.book_db else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
