"""A read-only window onto the recorded price tape, for the dashboard.

`core_brain.price_tape` records a 1-minute tape and answers one question of it:
after price moved, did it keep going or come back? Both halves are terminal
programs, and the recording half runs for weeks, so there is nothing to look at
between the moment collection starts and the moment someone remembers to run the
report.

This module is those two answers, served while collection is still going:

* `tape_status` -- how much tape exists, over how long, how much of it has
  resolved. Three aggregate queries, cheap enough to poll.
* `tape_findings` -- the drift grid, read back exactly as `analyse` stored it.

**Nothing here computes a finding.** Loading the store took 33 seconds at 2.8M
ticks on 2026-09-07, so a page that recomputed would hang; and a page that
computed its own variant would be a second answer to argue with, which is the
thing a measured verdict exists to remove. The verdict is decided here against
`price_tape.SIGNIFICANCE_T`, the bar fixed before any of the grid was run, so
the page cannot render a friendlier threshold than the one that was promised.

READ-ONLY, AND OUTSIDE THE MONEY PATH. Every store is opened `mode=ro`, and
`data/orders.db` is refused by name at every layer including the environment --
an operator exporting `SHL_TAPE_DB=data/orders.db` to "see the real numbers" is
the exact mistake the refusal exists for. The production registry has a
different schema, so pointing this at it would not fail loudly, it would report
"the tape found nothing".
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from core_brain.price_tape import DEFAULT_TAPE_PATH, SIGNIFICANCE_T

#: Store names this viewer will never open, matched as a substring of the file
#: name so `data/orders.db` and a copy called `orders.db.bak` are both refused.
REFUSED_STORES = ("orders.db",)

SECONDS_PER_DAY = 86_400.0


class RefusedStore(ValueError):
    """The named store is not a price tape and will not be opened."""


def resolve_tape_db(custom: str | Path | None = None) -> Path:
    """Which tape to read: the argument, `SHL_TAPE_DB`, or the recorder's own."""
    raw = custom or os.environ.get("SHL_TAPE_DB") or DEFAULT_TAPE_PATH
    path = Path(raw)
    lowered = path.name.lower()
    for refused in REFUSED_STORES:
        if refused in lowered:
            raise RefusedStore(
                f"{path} is a live order registry, not a price tape; "
                f"this viewer reads recorded ticks only")
    return path


def _read_only(path: Path) -> sqlite3.Connection:
    """Open a store read-only, with the path escaped into the URI rather than
    pasted into it.

    `f"file:{path}?mode=ro"` is string concatenation into a URI, and a path
    holding `#` re-parses: the fragment swallows `mode=ro`, SQLite drops the
    read-only flag, and it then happily CREATES the truncated file it thinks it
    was asked for. `as_uri()` percent-encodes those characters, so the query
    survives whatever the file is called.
    """
    conn = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def tape_status(path: str | Path) -> dict[str, Any]:
    """How much tape exists. A store that is not there yet is a state, not an error."""
    path = Path(path)
    empty: dict[str, Any] = {
        "db": str(path), "exists": path.exists(), "state": "MISSING",
        "markets": 0, "resolved": 0, "ticks": 0,
        "first_ts": None, "last_ts": None, "span_days": 0.0,
    }
    if not path.exists():
        return empty
    try:
        with _read_only(path) as conn:
            markets, resolved = conn.execute(
                "SELECT COUNT(*), COUNT(up_wins) FROM markets").fetchone()
            ticks, first_ts, last_ts = conn.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM ticks").fetchone()
    except sqlite3.Error:
        return {**empty, "state": "EMPTY"}
    if not ticks:
        return {**empty, "state": "EMPTY", "markets": markets, "resolved": resolved}
    return {
        "db": str(path), "exists": True, "state": "READY",
        "markets": markets, "resolved": resolved, "ticks": ticks,
        "first_ts": first_ts, "last_ts": last_ts,
        "span_days": (last_ts - first_ts) / SECONDS_PER_DAY,
    }


def _verdict(t: float) -> str:
    if t >= SIGNIFICANCE_T:
        return "CONTINUES"
    if t <= -SIGNIFICANCE_T:
        return "COMES_BACK"
    return "NO_SIGNAL"


def tape_findings(path: str | Path) -> dict[str, Any]:
    """The stored drift grid, read back with each cell's verdict applied."""
    path = Path(path)
    empty: dict[str, Any] = {
        "db": str(path), "state": "MISSING", "cells": [],
        "computed_at": None, "significance_t": SIGNIFICANCE_T,
        "continues": 0, "comes_back": 0, "no_signal": 0, "samples": 0,
    }
    if not path.exists():
        return empty
    try:
        with _read_only(path) as conn:
            rows = conn.execute(
                "SELECT label, trigger, lookback_m, horizon_m, n, mean, t_stat, "
                "computed_at FROM findings "
                "ORDER BY trigger, lookback_m, horizon_m").fetchall()
    except sqlite3.Error:
        return {**empty, "state": "NOT_ANALYSED"}
    if not rows:
        return {**empty, "state": "NOT_ANALYSED"}

    cells = []
    tally = {"CONTINUES": 0, "COMES_BACK": 0, "NO_SIGNAL": 0}
    for row in rows:
        verdict = _verdict(row["t_stat"])
        tally[verdict] += 1
        cells.append({
            "label": row["label"],
            "trigger_c": row["trigger"] * 100.0,
            "lookback_min": row["lookback_m"],
            "horizon_min": row["horizon_m"],
            "n": row["n"],
            "mean": row["mean"],
            "mean_c": row["mean"] * 100.0,
            "t": row["t_stat"],
            "verdict": verdict,
        })
    return {
        "db": str(path), "state": "READY", "cells": cells,
        "computed_at": rows[0]["computed_at"],
        "significance_t": SIGNIFICANCE_T,
        "continues": tally["CONTINUES"],
        "comes_back": tally["COMES_BACK"],
        "no_signal": tally["NO_SIGNAL"],
        "samples": sum(c["n"] for c in cells),
    }
