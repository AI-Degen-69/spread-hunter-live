"""How often both legs of a pair fill, and how big the fills are.

Every measurement so far has answered a question about the BOOK. This one asks
the question about US, and it is the last one left: the spread hunter earns
`1.00 - pair_cost` only when BOTH legs fill as resting bids, and no run in this
project's history has ever recorded that happening.

Two numbers come out, and both are needed to say whether the strategy works.

  * **The double-maker rate.** Of the markets we quoted two-sidedly, in how
    many did both sides fill? A single leg is not a partial success -- it is a
    directional position the strategy exists to avoid, closed by the safety
    path at a loss.
  * **The fill-size distribution.** Gas is a per-TRANSACTION cost
    ($0.01138 median, measured on-chain), so it amortises over the shares
    merged. On a 0.1c-tick book a merge needs 11.4 shares to break even and
    the observed fills have been five and six. A high double-maker rate made
    of small fills is not a business.

Reads a shadow store; writes nothing.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
from pathlib import Path
from typing import Optional

MERGE_GAS_USD = 0.01138          # median of 12 on-chain mergePositions txs


def _pct(n: float, d: float) -> str:
    return f"{100.0 * n / d:6.2f}%" if d else "     --"


def quoted_markets(conn: sqlite3.Connection) -> list[tuple]:
    """(market, sides quoted, orders, filled orders) per market."""
    return conn.execute(
        "SELECT market_slug,"
        "  COUNT(DISTINCT side) AS sides,"
        "  COUNT(*) AS orders,"
        "  SUM(CASE WHEN filled > 0 THEN 1 ELSE 0 END) AS filled_orders,"
        "  COUNT(DISTINCT CASE WHEN filled > 0 THEN side END) AS filled_sides"
        " FROM quotes GROUP BY market_slug ORDER BY market_slug"
    ).fetchall()


def fill_sizes(conn: sqlite3.Connection) -> list[float]:
    rows = conn.execute("SELECT size FROM fills WHERE size > 0").fetchall()
    return [float(r[0]) for r in rows]


def merges(conn: sqlite3.Connection) -> list[tuple]:
    """Completed merge closes: the only rows that are the strategy working."""
    try:
        return conn.execute(
            "SELECT market_slug, shares, cost_basis, proceeds, realized_pnl"
            " FROM closes WHERE method LIKE '%merge%'"
        ).fetchall()
    except sqlite3.Error:
        return []


def exits(conn: sqlite3.Connection) -> list[tuple]:
    try:
        return conn.execute(
            "SELECT market_slug, shares, realized_pnl FROM closes"
            " WHERE method LIKE '%exit%'"
        ).fetchall()
    except sqlite3.Error:
        return []


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the store read-only, so a report can never alter what it reports.

    This reads live shadow stores while a rehearsal is still writing them, and
    `data/orders.db` is a legal target too. A reporting tool holding a
    read-write handle on the production registry is one typo away from being
    the thing that corrupts it, and SQLite will happily create an empty
    database if the path is wrong -- which would make a mistyped `--db` look
    like a run that recorded nothing.
    """
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def report(db_path: Path) -> str:
    conn = open_read_only(db_path)
    try:
        markets = quoted_markets(conn)
        sizes = fill_sizes(conn)
        merged = merges(conn)
        exited = exits(conn)
        n_quotes = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        n_fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    finally:
        conn.close()

    out = [f"pair-fill report -- {db_path}",
           f"{n_quotes} quotes, {n_fills} fills, {len(markets)} markets", ""]

    two_sided = [m for m in markets if m[1] >= 2]
    one_leg = [m for m in two_sided if m[4] == 1]
    both_legs = [m for m in two_sided if m[4] >= 2]

    out.append("THE DOUBLE-MAKER RATE")
    out.append("  a single leg is not half a pair -- it is a directional")
    out.append("  position the safety path closes at a loss")
    out.append(f"  markets quoted two-sidedly : {len(two_sided)}")
    out.append(f"  no leg filled              : "
               f"{len(two_sided) - len(one_leg) - len(both_legs)}")
    out.append(f"  ONE leg filled             : {len(one_leg):<5}"
               f"{_pct(len(one_leg), len(two_sided))}")
    out.append(f"  BOTH legs filled           : {len(both_legs):<5}"
               f"{_pct(len(both_legs), len(two_sided))}")
    out.append("")

    out.append("FILL SIZE -- gas is per merge, so size decides if it clears")
    if sizes:
        srt = sorted(sizes)
        med = statistics.median(srt)
        out.append(f"  n={len(srt)}  min {min(srt):.0f}  median {med:.0f}  "
                   f"max {max(srt):.0f}")
        for tick, edge, label in ((0.001, 0.001, "0.1c-tick book"),
                                  (0.01, 0.01, "1c-tick book")):
            need = MERGE_GAS_USD / edge
            clears = sum(1 for s in srt if s >= need)
            out.append(f"  {label:<16} needs {need:>5.1f} shares to clear "
                       f"${MERGE_GAS_USD:.5f} gas -- "
                       f"{clears}/{len(srt)} fills do ({_pct(clears, len(srt))})")
    else:
        out.append("  no fills recorded")
    out.append("")

    out.append("WHAT ACTUALLY CLOSED")
    out.append(f"  merges : {len(merged)}")
    for slug, shares, basis, proceeds, pnl in merged:
        gross = (proceeds or 0) - (basis or 0)
        out.append(f"    {str(slug)[:34]:<36}{shares or 0:>7.0f}sh  "
                   f"gross ${gross:>7.4f}  net of gas "
                   f"${gross - MERGE_GAS_USD:>7.4f}")
    out.append(f"  single-leg exits : {len(exited)}")
    for slug, shares, pnl in exited:
        out.append(f"    {str(slug)[:34]:<36}{shares or 0:>7.0f}sh  "
                   f"pnl ${pnl or 0:>7.4f}")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="shadow store to read")
    args = ap.parse_args(argv)
    print(report(Path(args.db)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
