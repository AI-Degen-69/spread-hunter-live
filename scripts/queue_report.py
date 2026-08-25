"""Read the queue_marks a shadow run recorded and say what the queues are doing.

    python scripts/queue_report.py                     # data/shadow.db, latest run
    python scripts/queue_report.py --minutes 45        # only the last 45 minutes

Read-only. Opens the store with `mode=ro` and never writes to it.

Three columns decide whether the spread-hunter strategy has room on a market:

  * DECAY RATE -- shares per minute leaving our level, split into the part the
    tape explains (trades) and the residual (cancels). The residual is the
    number the shadow fill model cannot see: it credits queue progress only
    from trades, so on a 13,000-share queue it is blind to the mechanism most
    likely to move us up it.
  * CLEAR (trades) -- minutes to clear the queue counting trades only. This is
    what the fill model believes, and it is an upper bound on the wait.
  * CLEAR (all) -- minutes to clear counting cancels too. This is the honest
    estimate, and the gap between the two columns is exactly how wrong three
    zero-fill rehearsals have been.

A 30-45 minute read is DIRECTIONAL, not conclusive. It is enough to separate
"under an hour" from "effectively never", which is the decision that matters;
it is not enough to trust a number in the ambiguous middle.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics as st
from pathlib import Path

AMBIGUOUS_LOW_MIN = 60.0      # under an hour: the queue is joinable
AMBIGUOUS_HIGH_MIN = 720.0    # over 12 hours: effectively never


def _rows(db_path: Path, minutes: float | None) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        # A store written before this telemetry existed has no such table.
        # That is not an error, it is an older run -- say so rather than
        # showing the operator a stack trace.
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='queue_marks'").fetchone()
        if not exists:
            return []
        sql = "SELECT * FROM queue_marks WHERE cancel_decay IS NOT NULL"
        args: tuple = ()
        if minutes:
            cutoff = conn.execute(
                "SELECT MAX(ts) FROM queue_marks").fetchone()[0]
            if cutoff is not None:
                sql += " AND ts >= ?"
                args = (float(cutoff) - minutes * 60.0,)
        return [dict(r) for r in conn.execute(sql + " ORDER BY ts", args)]
    finally:
        conn.close()


def _verdict(clear_all: float) -> str:
    if clear_all <= AMBIGUOUS_LOW_MIN:
        return "JOINABLE"
    if clear_all >= AMBIGUOUS_HIGH_MIN or clear_all == math.inf:
        return "NEVER"
    return "ambiguous"


def report(db_path: Path, minutes: float | None) -> int:
    rows = _rows(db_path, minutes)
    if not rows:
        print(f"No queue marks with a previous observation in {db_path}.")
        print("Either this store predates the telemetry, or the run has not")
        print("yet seen two cycles on one level -- nothing can be differenced")
        print("until it has. If a run is in flight, wait one cycle.")
        return 1

    span_min = (max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 60.0
    by_market: dict[str, list[dict]] = {}
    for r in rows:
        by_market.setdefault(r["market_slug"] or "?", []).append(r)

    print(f"queue marks: {len(rows)} rows over {span_min:.1f} minutes, "
          f"{len(by_market)} markets")
    print(f"DIRECTIONAL at this span -- enough to separate 'under an hour' "
          f"from 'effectively never', not enough to trust the middle.\n")

    head = (f"{'market':<32} {'levels':>6} {'med queue':>10} "
            f"{'trade/min':>10} {'cancel/min':>11} "
            f"{'clear(trade)':>13} {'clear(all)':>11}  verdict")
    print(head)
    print("-" * len(head))

    out = []
    for name, marks in by_market.items():
        span = (max(m["ts"] for m in marks) - min(m["ts"] for m in marks)) / 60.0
        if span <= 0:
            continue
        sizes = [m["level_size"] for m in marks]
        traded = sum(m["traded"] for m in marks)
        cancels = sum(max(0.0, m["cancel_decay"] or 0.0) for m in marks)
        med = st.median(sizes)
        t_rate, c_rate = traded / span, cancels / span
        clear_t = med / t_rate if t_rate > 0 else math.inf
        clear_a = med / (t_rate + c_rate) if (t_rate + c_rate) > 0 else math.inf
        out.append((clear_a, name, len(marks), med, t_rate, c_rate, clear_t))

    for clear_a, name, n, med, t_rate, c_rate, clear_t in sorted(out):
        ct = f"{clear_t:13.0f}" if clear_t != math.inf else "          inf"
        ca = f"{clear_a:11.0f}" if clear_a != math.inf else "        inf"
        print(f"{name:<32} {n:>6} {med:>10.0f} {t_rate:>10.1f} "
              f"{c_rate:>11.1f} {ct} {ca}  {_verdict(clear_a)}")

    total_t = sum(m["traded"] for m in rows)
    total_c = sum(max(0.0, m["cancel_decay"] or 0.0) for m in rows)
    share = 100.0 * total_c / (total_t + total_c) if (total_t + total_c) else 0.0
    print(f"\ncancel share of all queue movement: {share:.0f}% "
          f"({total_c:.0f} cancelled vs {total_t:.0f} traded)")
    print("A high share means the fill model -- which counts only the traded "
          "half -- understates real queue progress by roughly that much.")

    joinable = [n for c, n, *_ in out if c <= AMBIGUOUS_LOW_MIN]
    middle = [n for c, n, *_ in out
              if AMBIGUOUS_LOW_MIN < c < AMBIGUOUS_HIGH_MIN]
    print(f"\njoinable now: {joinable or 'none'}")
    print(f"keep collecting (ambiguous middle): {middle or 'none'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/shadow.db")
    ap.add_argument("--minutes", type=float, default=None,
                    help="only the last N minutes of marks")
    a = ap.parse_args(argv)
    return report(Path(a.db), a.minutes)


if __name__ == "__main__":
    raise SystemExit(main())
