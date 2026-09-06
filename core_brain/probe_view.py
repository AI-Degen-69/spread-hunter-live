"""A read-only window onto a running family probe, for the dashboard.

`scripts/family_probe.py` samples the open market universe every few minutes
and writes what it saw to its own SQLite store; `scripts/family_fill_report.py`
turns those samples into pairs per day and dollars per day. Both are terminal
programs that answer once, at the end. A six-hour run is therefore six hours of
nothing to look at, and the only way to know it is still alive is to tail a log.

This module is the same two answers, served while the run is still going:

* `probe_status` -- is the probe alive, how far in is it, what did the last
  cycle see. Cheap: two aggregate queries, safe to poll.
* `probe_report` -- the findings, computed by importing
  `scripts.family_fill_report` and calling ITS simulator. Nothing here
  re-implements a fill, an entry condition, or a per-day denominator. A page
  that computed its own variant would be a second answer to argue with, which
  is the thing the probe exists to remove.

READ-ONLY, AND OUTSIDE THE MONEY PATH. Every store is opened `mode=ro`, and
`data/orders.db` is refused by name -- the same refusal the probe makes, for
the same reason: the production registry has a different schema, so pointing
this at it would not fail loudly, it would report "the probe found nothing".
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

# A measured value as the selector writes one into a refusal: "0", "19,509",
# "235.4d". The lookahead is what keeps the fixed label "24h" out of it -- the
# window a volume is measured over is part of the refusal's NAME, and blanking
# it would merge refusals that are about different things.
_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?=d\b|\b)")

# live/, one level up from live/core_brain/.
LIVE_ROOT = Path(__file__).resolve().parent.parent

# The probe runs in its own worktree so a long sampling run cannot be disturbed
# by work on the checkout. Nothing about that path is guessable from inside
# this repo, so it is a default rather than a discovery, and `SHL_PROBE_DB`
# overrides it.
DEFAULT_PROBE_DB = LIVE_ROOT.parent / "shl-probe-v2" / "runtime" / "family_probe_v3.db"

# The probe cycles every ~6 minutes and does miss cycles. Two missed cycles is
# the shortest gap that means something went wrong rather than "the venue was
# slow", and it is the same 15 minutes the handoff calls stale.
STALE_AFTER_SEC: float = 900.0

# What `--hours` the watched run was launched with. Only the progress bar reads
# it; every finding divides by hours actually WATCHED, never by this.
DEFAULT_TARGET_HOURS: float = 6.0

# The report's own defaults, restated here so the page and the terminal print
# the same table. `family_fill_report` defaults to a 15-minute queue bar; the
# v3 runs are read at 10, which is what the handoff's report commands pass.
DEFAULT_QUEUE_BAR: float = 10.0
DEFAULT_PAIR_BAR: float = 0.99
DEFAULT_HORIZON_MIN: float = 15.0
DEFAULT_SIZE_USD: float = 15.0

# `select_min_volume_24h_usd` in scoring/config.py and core_brain/config.py.
# The live selector refuses everything below it; the v3 probe scores down to
# PROBE_VOLUME_BAR to find out whether that refusal is costing anything.
LIVE_VOLUME_BAR: float = 125_000.0
PROBE_VOLUME_BAR: float = 25_000.0

# Buckets the funnel audit already reports in, so the page's split can be read
# against the numbers in the handoff without re-bucketing them by hand.
VOLUME_BANDS: tuple[tuple[str, float, Optional[float]], ...] = (
    ("$0", 0.0, 1.0),
    ("$1-$1k", 1.0, 1_000.0),
    ("$1k-$10k", 1_000.0, 10_000.0),
    ("$10k-$25k", 10_000.0, PROBE_VOLUME_BAR),
    ("$25k-$125k", PROBE_VOLUME_BAR, LIVE_VOLUME_BAR),
    ("$125k+", LIVE_VOLUME_BAR, None),
)

# probe_v2, $125k bar, 5.9h watched. Frozen on purpose: it is the number v3 is
# read against, and a baseline that moved with the newest run would let a bad
# run redefine what "no better than before" means.
PROBE_V2_BASELINE: dict[str, Any] = {
    "label": "probe v2 ($125k bar, 5.9h)",
    "moments": 70,
    "families": 14,
    "pairs_per_day": 0.0,
    "net_per_day": -11.07,
    "adverse_pairs_per_day": 16.18,
    "adverse_net_per_day": -137.52,
}

# The probe refuses these by name and so does this. Substring, not equality:
# `data/orders.db`, an absolute path to it, and a copy beside it are all the
# production registry.
REFUSED_STORES: tuple[str, ...] = ("orders.db",)


class RefusedStore(ValueError):
    """The caller asked this read-only viewer to open a money store."""


def resolve_probe_db(custom: str | Path | None = None) -> Path:
    """Which probe store to read: the argument, `SHL_PROBE_DB`, or the default.

    Refuses the production registry at every layer, including the environment,
    because an operator exporting `SHL_PROBE_DB=data/orders.db` to "see the
    real numbers" is the exact mistake this refusal exists for.
    """
    raw = custom or os.environ.get("SHL_PROBE_DB") or DEFAULT_PROBE_DB
    path = Path(raw)
    lowered = path.name.lower()
    for refused in REFUSED_STORES:
        if refused in lowered:
            raise RefusedStore(
                f"{path} is a live order registry, not a probe store; "
                f"this viewer reads probe samples only")
    return path


def probe_search_root() -> Path:
    """The directory a `?db=` from the page is allowed to name a store inside.

    Probe worktrees are siblings of this repo, so the projects directory is the
    smallest root that reaches every store an operator would legitimately want
    to compare -- v2 beside v3, a re-run beside the original.
    """
    override = os.environ.get("SHL_PROBE_ROOT")
    return Path(override) if override else LIVE_ROOT.parent


def resolve_request_db(requested: str | None) -> Path:
    """Turn an untrusted `?db=` query parameter into a store path, or refuse.

    `SHL_PROBE_DB` and the default are operator configuration and are trusted;
    a query parameter is not. It arrives from whatever page is open in the
    browser, and this dashboard binds a loopback port that any of them can
    reach. Three locks, all of which have to hold:

      * the production registry is refused by name, as everywhere else here;
      * the path must end in `.db`, so it names a store rather than a key file
        or a log the operator would rather not serve;
      * it must resolve INSIDE `probe_search_root()`, which is what stops
        `?db=../../../../Windows/...` from turning a research page into an
        arbitrary-file reader.

    Nothing readable escapes even without these -- a file with no
    `probe_samples` table reads as EMPTY -- but "the leak was empty anyway" is
    not a defence worth relying on.
    """
    if not requested:
        return resolve_probe_db()
    path = resolve_probe_db(requested).expanduser()
    resolved = path if path.is_absolute() else (Path.cwd() / path)
    resolved = Path(os.path.normpath(str(resolved)))
    if resolved.suffix.lower() != ".db":
        raise RefusedStore(f"{requested} is not a .db probe store")
    root = Path(os.path.normpath(str(probe_search_root().expanduser())))
    if root not in resolved.parents:
        raise RefusedStore(
            f"{requested} is outside {root}; a probe store has to live beside "
            f"the repo, not anywhere on disk")
    return resolved


def _connect(db_path: Path) -> Optional[sqlite3.Connection]:
    """A read-only handle, or None when there is nothing readable there."""
    if not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _has_samples(conn: sqlite3.Connection) -> bool:
    """A store written by an interrupted first cycle has no table at all."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='probe_samples'").fetchone()
    return row is not None


def probe_status(db_path: str | Path,
                 target_hours: float = DEFAULT_TARGET_HOURS,
                 now: Optional[float] = None) -> dict[str, Any]:
    """Alive, stalled, or finished -- plus how far in and what it last saw.

    RUNNING, STALE and DONE are decided in that order of authority: a run that
    reached its target is DONE even though it stopped writing hours ago, and
    calling that STALE would report every completed probe as dead.
    """
    path = Path(db_path)
    now = time.time() if now is None else float(now)
    empty: dict[str, Any] = {
        "db": str(path), "exists": path.exists(), "state": "MISSING",
        "run_id": None, "cycles": 0, "samples": 0, "markets": 0,
        "families": 0, "first_ts": None, "last_ts": None, "age_sec": None,
        "elapsed_hours": 0.0, "watched_hours": 0.0,
        "target_hours": float(target_hours), "progress_pct": 0.0,
        "last_cycle": None, "stale_after_sec": STALE_AFTER_SEC,
    }
    conn = _connect(path)
    if conn is None:
        return empty
    try:
        if not _has_samples(conn):
            return {**empty, "state": "EMPTY"}
        head = conn.execute(
            "SELECT run_id FROM probe_samples ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if head is None:
            return {**empty, "state": "EMPTY"}
        run_id = str(head["run_id"])
        agg = conn.execute(
            "SELECT COUNT(*) AS samples, MIN(ts) AS first_ts, "
            "MAX(ts) AS last_ts, COUNT(DISTINCT cycle) AS cycles, "
            "COUNT(DISTINCT condition_id) AS markets, "
            "COUNT(DISTINCT family) AS families "
            "FROM probe_samples WHERE run_id = ?", (run_id,)).fetchone()
        last_cycle = _last_cycle(conn, run_id)
        watched = _watched_hours(conn, run_id)
    except sqlite3.Error:
        return {**empty, "state": "EMPTY"}
    finally:
        conn.close()

    first_ts, last_ts = agg["first_ts"], agg["last_ts"]
    elapsed_hours = 0.0 if first_ts is None else (last_ts - first_ts) / 3600.0
    age_sec = None if last_ts is None else max(0.0, now - float(last_ts))
    target = max(float(target_hours), 1e-9)
    progress = min(100.0, 100.0 * elapsed_hours / target)
    if progress >= 100.0:
        state = "DONE"
    elif age_sec is not None and age_sec > STALE_AFTER_SEC:
        state = "STALE"
    else:
        state = "RUNNING"
    return {
        **empty, "state": state, "run_id": run_id,
        "cycles": int(agg["cycles"]), "samples": int(agg["samples"]),
        "markets": int(agg["markets"]), "families": int(agg["families"]),
        "first_ts": first_ts, "last_ts": last_ts, "age_sec": age_sec,
        "elapsed_hours": elapsed_hours, "watched_hours": watched,
        "progress_pct": progress, "last_cycle": last_cycle,
    }


def _last_cycle(conn: sqlite3.Connection, run_id: str) -> Optional[dict]:
    """What the newest cycle swept, or None when the run wrote no cycle row."""
    try:
        row = conn.execute(
            "SELECT cycle, ts, tape_prints, markets_traded, candidates, "
            "sampled, seconds FROM probe_cycles WHERE run_id = ? "
            "ORDER BY cycle DESC LIMIT 1", (run_id,)).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else dict(row)


def _watched_hours(conn: sqlite3.Connection, run_id: str) -> float:
    """Hours the probe was sampling, holes excluded.

    Every per-day figure divides by this, and it is `family_fill_report`'s
    `_span_days` in hours -- imported rather than restated so the two cannot
    drift.
    """
    from scripts.family_fill_report import _span_days
    rows = [{"ts": row["ts"]} for row in conn.execute(
        "SELECT ts FROM probe_samples WHERE run_id = ? ORDER BY ts",
        (run_id,))]
    return _span_days(rows) * 24.0


def probe_report(db_path: str | Path,
                 queue_bar: float = DEFAULT_QUEUE_BAR,
                 count_adverse: bool = False,
                 size_usd: float = DEFAULT_SIZE_USD,
                 hours: Optional[float] = None) -> dict[str, Any]:
    """The fill report, as JSON, for the newest run in the store.

    Every number here comes out of `scripts.family_fill_report`. What this adds
    is the two cuts the v3 question needs and the terminal report does not
    print: which VOLUME BAND each moment came from, and which refusal each
    family most often recorded.
    """
    from scripts.family_fill_report import (
        _rate_basis, _span_days, load_rows, simulate, summarise,
    )

    path = Path(db_path)
    blank: dict[str, Any] = {
        "ok": False, "reason": f"no probe store at {path}",
        "db": str(path), "run_id": None, "samples": 0, "watched_hours": 0.0,
        "rate_basis": None, "moments": 0, "families": 0, "adverse_moments": 0,
        "queue_bar": float(queue_bar), "pair_bar": DEFAULT_PAIR_BAR,
        "size_usd": float(size_usd), "horizon_min": DEFAULT_HORIZON_MIN,
        "count_adverse": bool(count_adverse),
        "totals": _totals([]), "rows": [], "gate_split": {},
        "bands": _bands({}, {}), "refusals": [],
        "baseline": PROBE_V2_BASELINE,
        "verdict": {"answer": "PENDING", "headline": "אין עדיין דגימות",
                    "recommendation": "המתן שהפרוב יכתוב מחזור ראשון"},
    }
    if _connect(path) is None:
        return blank
    try:
        rows = load_rows(path, hours)
    except sqlite3.Error as exc:
        return {**blank, "reason": f"unreadable probe store: {exc}"}
    if not rows:
        return {**blank, "reason": "the store holds no samples yet",
                "ok": False}

    days = _span_days(rows)
    moments = simulate(rows, queue_bar, DEFAULT_PAIR_BAR, DEFAULT_HORIZON_MIN,
                       DEFAULT_HORIZON_MIN, size_usd, count_adverse)
    stats = summarise(moments, rows, days)
    volumes = _market_volumes(rows)
    report = {
        **blank,
        "ok": True, "reason": None,
        "run_id": str(rows[-1]["run_id"]),
        "samples": len(rows), "watched_hours": days * 24.0,
        "rate_basis": _rate_basis(rows),
        "moments": len(moments), "families": len(stats),
        "adverse_moments": sum(1 for m in moments if m.adverse),
        "totals": _totals(stats),
        "rows": [_family_row(stat) for stat in stats],
        "gate_split": _gate_split(moments, days),
        "bands": _bands(volumes, _moments_by_market(moments)),
        "refusals": _refusals(rows),
    }
    report["verdict"] = _verdict(report, stats)
    return report


def _family_row(stat) -> dict[str, Any]:
    return {
        "family": stat.family, "moments": stat.moments, "pairs": stat.pairs,
        "singles": stat.singles, "markets": stat.markets,
        "pair_pct": stat.pair_pct, "pairs_per_day": stat.pairs_per_day,
        "singles_per_day": stat.singles_per_day,
        "gross_per_day": stat.gross_per_day, "cost_per_day": stat.cost_per_day,
        "net_per_day": stat.net_per_day, "gate": stat.gate,
        "admitted": stat.gate == "admitted",
    }


def _totals(stats: list) -> dict[str, float]:
    return {
        "pairs_per_day": sum(s.pairs_per_day for s in stats),
        "singles_per_day": sum(s.singles_per_day for s in stats),
        "gross_per_day": sum(s.gross_per_day for s in stats),
        "cost_per_day": sum(s.cost_per_day for s in stats),
        "net_per_day": sum(s.net_per_day for s in stats),
    }


def _gate_split(moments: list, days: float) -> dict[str, dict[str, float]]:
    """What today's live gate would and would not have let us quote.

    This is the split the config decision turns on: moments the gate ADMITS
    that still pay nothing are evidence the bar is not what is stopping us.
    """
    out: dict[str, dict[str, float]] = {}
    denominator = max(days, 1e-9)
    for label, chosen in (("admitted", True), ("refused", False)):
        group = [m for m in moments if m.gate_pass is chosen]
        pairs = [m for m in group if m.is_pair]
        singles = [m for m in group if m.is_single]
        net = (sum(m.edge_usd for m in pairs)
               - sum(m.single_cost_usd() for m in singles)) / denominator
        out[label] = {
            "moments": len(group), "pairs": len(pairs),
            "singles": len(singles),
            "pairs_per_day": len(pairs) / denominator,
            "net_per_day": net,
        }
    return out


def _market_volumes(rows: list[dict]) -> dict[str, float]:
    """Each market's last readable 24h volume.

    Last, not max: volume is a rolling window that both rises and falls, and
    the newest reading is the one the live gate would be comparing against.
    """
    out: dict[str, float] = {}
    for row in rows:
        volume = row.get("volume_24h")
        if volume is not None:
            out[str(row["condition_id"])] = float(volume)
    return out


def _moments_by_market(moments: list) -> dict[str, dict[str, int]]:
    tally: dict[str, dict[str, int]] = {}
    for moment in moments:
        bucket = tally.setdefault(moment.condition_id,
                                  {"moments": 0, "pairs": 0, "singles": 0})
        bucket["moments"] += 1
        bucket["pairs"] += int(moment.is_pair)
        bucket["singles"] += int(moment.is_single)
    return tally


def _bands(volumes: dict[str, float],
           per_market: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    """Markets, moments and pairs per 24h-volume band.

    The v3 question in one table: the `$25k-$125k` row is the population the
    live bar refuses, and its `pairs` column is whether refusing it costs
    anything.
    """
    out = []
    for label, low, high in VOLUME_BANDS:
        markets = [cid for cid, volume in volumes.items()
                   if volume >= low and (high is None or volume < high)]
        counts = [per_market.get(cid, {}) for cid in markets]
        out.append({
            "label": label, "lo": low, "hi": high,
            "markets": len(markets),
            "moments": sum(c.get("moments", 0) for c in counts),
            "pairs": sum(c.get("pairs", 0) for c in counts),
            "singles": sum(c.get("singles", 0) for c in counts),
            "under_live_bar": high is not None and high <= LIVE_VOLUME_BAR,
            "in_probe_window": low >= PROBE_VOLUME_BAR
                               and (high is not None and high <= LIVE_VOLUME_BAR),
        })
    return out


def normalise_reason(reason: str) -> str:
    """Collapse the measured value out of a refusal so it groups.

    The selector writes the number it read into the refusal: "24h volume 0
    under bar 25,000", "24h volume 19,509 under bar 25,000", "horizon 235.4d
    over 30d". Tallied verbatim, one refusal shatters into a hundred rows of
    one market each and the histogram says nothing -- the single largest reason
    the universe is empty ranks below its own fragments.
    """
    return _NUMBER_RE.sub("N", reason)


def _refusals(rows: list[dict]) -> list[dict[str, Any]]:
    """Why the live gate said no, ranked by how many markets it said it to.

    Markets and samples are both reported: a long-lived market re-sampled for
    six hours contributes one market and seventy samples, and ranking on
    samples alone would put whatever happened to live longest at the top. The
    verbatim refusal is kept as `example` -- the numbers are what makes a
    refusal arguable, and a page that only showed the shape would hide how far
    under the bar these markets actually are.
    """
    markets: dict[str, set[str]] = {}
    samples: dict[str, int] = {}
    verbatim: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.get("gate_pass"):
            continue
        raw = str(row.get("gate_reason") or "refused")
        reason = normalise_reason(raw)
        markets.setdefault(reason, set()).add(str(row["condition_id"]))
        samples[reason] = samples.get(reason, 0) + 1
        seen = verbatim.setdefault(reason, {})
        seen[raw] = seen.get(raw, 0) + 1
    out = [{"reason": reason, "markets": len(cids),
            "samples": samples.get(reason, 0),
            "example": max(verbatim[reason], key=lambda k: verbatim[reason][k]),
            "variants": len(verbatim[reason])}
           for reason, cids in markets.items()]
    out.sort(key=lambda r: (-r["markets"], -r["samples"]))
    return out


def _verdict(report: dict[str, Any], stats: list) -> dict[str, str]:
    """The one sentence the run is read for, and what it does NOT license.

    Three answers, never two. NO is the finding v2 already produced and the
    one that closes the question. MAYBE names a family, and says out loud that
    one run is not enough to move a bar -- the handoff's rule, kept next to the
    number instead of in a document nobody opens beside the page.
    """
    payers = [s for s in stats if s.pairs > 0 and s.net_per_day > 0]
    if not report.get("ok"):
        return {"answer": "PENDING", "headline": "אין עדיין דגימות",
                "recommendation": "המתן שהפרוב יכתוב מחזור ראשון"}
    if not payers:
        moments = report["moments"]
        return {
            "answer": "NO",
            "headline": (f"{moments} רגעים ניתנים לציטוט, 0 משפחות משלמות: "
                         f"שום תור לא התנקז במחיר שלנו"),
            "recommendation": (
                f"רף הנפח ${LIVE_VOLUME_BAR:,.0f} אינו האילוץ הכובל — לסגור "
                f"את המשימה \"להרחיב את היקום\" ולא להוריד את "
                f"select_min_volume_24h_usd"),
        }
    best = max(payers, key=lambda s: s.net_per_day)
    return {
        "answer": "MAYBE",
        "headline": (f"{best.family} מראה {best.pairs} זוגות strict ברווח נטו "
                     f"${best.net_per_day:,.2f} ליום"),
        "recommendation": (
            "ריצה אחת היא רעש — הרץ פרוב מאשר שני לפני כל שינוי קונפיג; "
            "אל תכוונן על סמך זה"),
    }
