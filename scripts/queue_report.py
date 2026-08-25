"""Read the queue_marks a shadow run recorded and say what the queues are doing.

    python scripts/queue_report.py                     # data/shadow.db, all marks
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

EVERY RATE IS COMPUTED PER LEVEL, FROM DELTAS. An earlier version summed a
market's movement and divided by the wall-clock span between its first and last
retained mark. That inflated every rate twice over -- once because dropping the
undifferenced first mark left N intervals of movement over an N-1 span, and
once because it divided a combined-level rate into a single level's queue. Both
errors shortened clear times, which for a report whose only job is to say
whether a queue is joinable is the one direction that must not be wrong.

## How to verify

    python -m pytest -q tests/test_queue_report.py

Expected: 27 passed.

    python scripts/queue_report.py --minutes 45

Against a store with no marks yet (or one written before this telemetry
existed) it prints an explanation and exits 1. Against a live rehearsal it
prints one row per market with its median level's clear times and a
JOINABLE / ambiguous / NEVER verdict.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics as st
from dataclasses import dataclass
from pathlib import Path

JOINABLE_MAX_MIN = 60.0       # at or under an hour: the queue is joinable
NEVER_MIN_MIN = 720.0         # at or over 12 hours: effectively never


@dataclass(frozen=True)
class Delta:
    """One level's movement over the interval it actually covers."""
    ts: float
    run_id: str
    market_slug: str
    token_id: str
    price: float
    level_size: float
    traded: float
    cancel_decay: float
    dt_min: float


@dataclass(frozen=True)
class LevelStat:
    market_slug: str
    token_id: str
    price: float
    observations: int
    median_size: float
    trade_rate: float
    cancel_rate: float
    clear_trade: float
    clear_all: float


@dataclass(frozen=True)
class MarketStat:
    market_slug: str
    levels: int
    observations: int
    median_size: float
    trade_rate: float
    cancel_rate: float
    clear_trade: float
    clear_all: float


def load_deltas(db_path: Path | str, minutes: float | None) -> list[Delta]:
    """Every recorded movement, paired within one level AND one run.

    A delta is the interval between two consecutive marks on the same
    (run, token, price). Pairing across runs is what a reused `data/shadow.db`
    would otherwise do: the first cycle of a new rehearsal differencing against
    the last cycle of an older one, hours earlier, producing a decay and a
    clear time computed across that gap.

    `dt_min` is carried on the delta rather than reconstructed later, so a
    caller can only ever divide movement by the time it actually took.

    `minutes` filters by the delta's END timestamp and keeps whole deltas: a
    partially-included interval would put movement over the wrong divisor,
    which is the bug this whole module was rewritten to remove. `None` means
    everything; `0` means nothing; negative is refused rather than silently
    producing a future cutoff and an empty report.
    """
    if minutes is not None:
        if minutes < 0:
            raise ValueError(f"--minutes must not be negative, got {minutes}")
        if minutes == 0:
            return []

    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        # A store written before this telemetry existed has no such table.
        # That is an older run, not an error.
        if not conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='queue_marks'").fetchone():
            return []
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM queue_marks ORDER BY run_id, token_id, price, ts, id")]
        newest = conn.execute("SELECT MAX(ts) FROM queue_marks").fetchone()[0]
    finally:
        conn.close()

    cutoff = None
    if minutes is not None and newest is not None:
        cutoff = float(newest) - minutes * 60.0

    out: list[Delta] = []
    prev_key = None
    prev_ts = None
    for r in rows:
        key = (r["run_id"], r["token_id"], round(float(r["price"]), 4))
        if key == prev_key and prev_ts is not None and r["cancel_decay"] is not None:
            dt_min = (float(r["ts"]) - prev_ts) / 60.0
            if dt_min > 0 and (cutoff is None or float(r["ts"]) >= cutoff):
                out.append(Delta(
                    ts=float(r["ts"]), run_id=r["run_id"] or "",
                    market_slug=r["market_slug"] or "?",
                    token_id=r["token_id"], price=round(float(r["price"]), 4),
                    level_size=float(r["level_size"]),
                    traded=float(r["traded"] or 0.0),
                    cancel_decay=float(r["cancel_decay"]),
                    dt_min=dt_min))
        prev_key, prev_ts = key, float(r["ts"])
    return out


def _clear(size: float, rate: float) -> float:
    """Minutes to clear `size` at `rate` shares/min. No rate means never."""
    return size / rate if rate > 0 else math.inf


def level_stats(deltas: list[Delta]) -> list[LevelStat]:
    """One row per (market, token, price): its own rates over its own time.

    Negative `cancel_decay` -- size joining the level behind us -- is recorded
    honestly by the collector but contributes zero here. It is real information
    about the book filling in, and it is not progress toward the front; letting
    it net against cancels elsewhere would shorten a clear time on the strength
    of orders arriving.
    """
    grouped: dict[tuple, list[Delta]] = {}
    for d in deltas:
        grouped.setdefault((d.market_slug, d.token_id, d.price), []).append(d)

    out: list[LevelStat] = []
    for (slug, token, price), ds in grouped.items():
        span = sum(d.dt_min for d in ds)
        if span <= 0:
            continue
        traded = sum(d.traded for d in ds)
        cancelled = sum(max(0.0, d.cancel_decay) for d in ds)
        median_size = st.median([d.level_size for d in ds])
        t_rate, c_rate = traded / span, cancelled / span
        out.append(LevelStat(
            market_slug=slug, token_id=token, price=price,
            observations=len(ds), median_size=median_size,
            trade_rate=t_rate, cancel_rate=c_rate,
            clear_trade=_clear(median_size, t_rate),
            clear_all=_clear(median_size, t_rate + c_rate)))
    return out


def market_stats(levels: list[LevelStat]) -> list[MarketStat]:
    """Aggregate levels to markets by the MEDIAN level, not the pooled total.

    Pooling would add up volume across levels and divide it into one level's
    queue, which is how the first version of this report made everything look
    joinable. The median level is the one a quote actually lands on.
    """
    grouped: dict[str, list[LevelStat]] = {}
    for lv in levels:
        grouped.setdefault(lv.market_slug, []).append(lv)

    out: list[MarketStat] = []
    for slug, lvs in grouped.items():
        out.append(MarketStat(
            market_slug=slug, levels=len(lvs),
            observations=sum(lv.observations for lv in lvs),
            median_size=st.median([lv.median_size for lv in lvs]),
            trade_rate=st.median([lv.trade_rate for lv in lvs]),
            cancel_rate=st.median([lv.cancel_rate for lv in lvs]),
            clear_trade=st.median([lv.clear_trade for lv in lvs]),
            clear_all=st.median([lv.clear_all for lv in lvs])))
    return out


def verdict(clear_all: float) -> str:
    if clear_all <= JOINABLE_MAX_MIN:
        return "JOINABLE"
    if clear_all >= NEVER_MIN_MIN:
        return "NEVER"
    return "ambiguous"


def _fmt(minutes: float, width: int) -> str:
    return f"{minutes:{width}.0f}" if minutes != math.inf else "inf".rjust(width)


def report(db_path: Path, minutes: float | None) -> int:
    deltas = load_deltas(db_path, minutes)
    if not deltas:
        print(f"No queue movement recorded in {db_path}.")
        print("Either this store predates the telemetry, or the run has not")
        print("yet seen two cycles on one level -- nothing can be differenced")
        print("until it has. If a run is in flight, wait one cycle.")
        return 1

    levels = level_stats(deltas)
    markets = market_stats(levels)
    observed_min = sum(d.dt_min for d in deltas)
    wall_min = (max(d.ts for d in deltas) - min(d.ts for d in deltas)) / 60.0

    print(f"queue marks: {len(deltas)} deltas across {len(levels)} levels in "
          f"{len(markets)} markets")
    print(f"wall clock {wall_min:.1f} min, {observed_min:.1f} level-minutes observed")
    print("DIRECTIONAL at this span -- enough to separate 'under an hour' from")
    print("'effectively never', not enough to trust a number in the middle.\n")

    head = (f"{'market':<32} {'lvls':>5} {'marks':>6} {'med queue':>10} "
            f"{'trade/min':>10} {'cancel/min':>11} "
            f"{'clear(trade)':>13} {'clear(all)':>11}  verdict")
    print(head)
    print("-" * len(head))
    for m in sorted(markets, key=lambda m: m.clear_all):
        print(f"{m.market_slug:<32} {m.levels:>5} {m.observations:>6} "
              f"{m.median_size:>10.0f} {m.trade_rate:>10.1f} "
              f"{m.cancel_rate:>11.1f} {_fmt(m.clear_trade, 13)} "
              f"{_fmt(m.clear_all, 11)}  {verdict(m.clear_all)}")

    traded = sum(d.traded for d in deltas)
    cancelled = sum(max(0.0, d.cancel_decay) for d in deltas)
    moved = traded + cancelled
    share = 100.0 * cancelled / moved if moved else 0.0
    print(f"\ncancel share of all queue movement: {share:.0f}% "
          f"({cancelled:.0f} cancelled vs {traded:.0f} traded)")
    print("A high share means the fill model -- which counts only the traded")
    print("half -- understates real queue progress by roughly that much.")

    joinable = [m.market_slug for m in markets if verdict(m.clear_all) == "JOINABLE"]
    middle = [m.market_slug for m in markets if verdict(m.clear_all) == "ambiguous"]
    print(f"\njoinable now: {joinable or 'none'}")
    print(f"keep collecting (ambiguous middle): {middle or 'none'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Report recorded queue behaviour.")
    ap.add_argument("--db", default="data/shadow.db")
    ap.add_argument("--minutes", type=float, default=None,
                    help="only deltas ending in the last N minutes")
    a = ap.parse_args(argv)
    try:
        return report(Path(a.db), a.minutes)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
