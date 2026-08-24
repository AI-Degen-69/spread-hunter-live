"""Append-only cycle telemetry stream and intent logger for the live engine.

Writes a compact NDJSON ring to `live/runtime/cycle_events.jsonl` (max 500 lines with atomic
rotation) and logs cycle decisions to `cycle_intent` in `orders.db`.

Designed for zero latency impact on the core loop:
- Fire-and-forget: never raises an exception out of `emit()`.
- O_APPEND append: one os.write per event, atomic at EOF across processes.
- Atomic rotation via tempfile + os.fsync + os.replace owned by the engine process.
"""
from __future__ import annotations

import atexit
import datetime
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# Serializes appends within one process. Cross-process atomicity comes from
# O_APPEND (a single os.write lands at EOF as one syscall), not from this lock.
_APPEND_LOCK = threading.Lock()

LIVE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RING_PATH = LIVE_ROOT / "runtime" / "cycle_events.jsonl"

# One registry, one path. This module used to hardcode data/orders.db while every
# other caller resolved data/orders.db through order_registry, so cycle_intent
# rows landed in a database the dashboard never read.
from core_brain.order_registry import DEFAULT_DB_PATH  # noqa: E402
from core_brain.runtime_paths import resolve_runtime_file  # noqa: E402

MAX_LINES = 500
KEEP_LINES = 400

CYCLE_INTENT_KEEP_ROWS = 200

_CREATE_CYCLE_INTENT = """
    CREATE TABLE IF NOT EXISTS cycle_intent (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        cycle INTEGER NOT NULL,
        market_slug TEXT NOT NULL,
        condition_id TEXT,
        intent_count INTEGER NOT NULL DEFAULT 0,
        submitted INTEGER NOT NULL DEFAULT 0,
        cancelled INTEGER NOT NULL DEFAULT 0,
        top_skip_reason TEXT,
        top_pass_reason TEXT,
        latency_ms REAL,
        run_id TEXT NOT NULL
    )
"""

# The cycle_intent connection, kept open between events. Connecting costs a
# few milliseconds, and emit() runs on the engine's hot path once per market
# visit, so rebuilding the handle per event was pure overhead. One entry: the
# engine writes a single registry for its whole life, while tests walk through
# temporary files, so a cache of one never grows and never strands a handle.
_DB_LOCK = threading.RLock()
_DB_CACHE: dict[str, sqlite3.Connection] = {}


def close_intent_connections() -> None:
    """Close the cached cycle_intent handle.

    The engine never needs this -- the handle lives as long as the process --
    but a test that wants a fresh connection, or a caller shutting down before
    the interpreter exits, does.
    """
    with _DB_LOCK:
        for key in list(_DB_CACHE):
            conn = _DB_CACHE.pop(key)
            try:
                conn.close()
            except Exception:
                pass


atexit.register(close_intent_connections)


def _intent_connection(path: Path) -> sqlite3.Connection:
    """The open connection for `path`, building it on first use.

    `check_same_thread=False` plus `_DB_LOCK` rather than a per-thread handle:
    the engine emits from more than one thread, and serialising the writes is
    what SQLite wants anyway. Callers must hold `_DB_LOCK`.
    """
    key = str(path)
    existing = _DB_CACHE.get(key)
    if existing is not None:
        return existing
    close_intent_connections()
    conn = sqlite3.connect(key, timeout=1.0, check_same_thread=False)
    conn.execute(_CREATE_CYCLE_INTENT)
    conn.commit()
    _DB_CACHE[key] = conn
    return conn


def _with_intent_connection(path: Path, work):
    """Run `work(conn)` on the cached connection, rebuilding it once on error.

    A handle can go bad on its own -- the file replaced underneath it, the
    process resumed from a suspend -- and a cached bad handle would turn one
    failure into every future one. So a `sqlite3.Error` drops the handle and
    retries exactly once; a second failure belongs to the caller's warning.
    """
    with _DB_LOCK:
        try:
            return work(_intent_connection(path))
        except sqlite3.Error:
            close_intent_connections()
            return work(_intent_connection(path))


def _resolve_run_id() -> str:
    """The current run id, resolved the same way for decide and submit events."""
    try:
        from core_brain.order_registry import get_run_id
        return get_run_id()
    except Exception:
        return "live"


def _write_cycle_intent(
    cycle: int,
    market_slug: str,
    condition_id: Optional[str] = None,
    intent_count: int = 0,
    top_skip_reason: Optional[str] = None,
    top_pass_reason: Optional[str] = None,
    latency_ms: float = 0.0,
    db_path: Path | None = None,
    run_id: Optional[str] = None,
) -> None:
    """Fire-and-forget INSERT into cycle_intent table, pruning older than 200 rows."""
    p = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)

    r_id = run_id or _resolve_run_id()

    now_ts = time.time()

    def insert(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO cycle_intent (
                ts, cycle, market_slug, condition_id, intent_count,
                submitted, cancelled, top_skip_reason, top_pass_reason,
                latency_ms, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_ts,
                cycle,
                market_slug,
                condition_id,
                intent_count,
                0,  # submitted/cancelled are filled by the later submit event
                0,
                top_skip_reason,
                top_pass_reason,
                latency_ms,
                r_id,
            ),
        )
        # Pruned on every insert, not in batches: the retention window is what
        # the dashboard reads, so the table must never be seen over it.
        conn.execute(
            """
            DELETE FROM cycle_intent
            WHERE id NOT IN (
                SELECT id FROM cycle_intent ORDER BY id DESC LIMIT ?
            )
            """,
            (CYCLE_INTENT_KEEP_ROWS,),
        )
        conn.commit()

    try:
        _with_intent_connection(p, insert)
    except Exception as exc:
        # Non-blocking / fire-and-forget
        print(f"WARNING: cycle_intent insert failed: {exc}", file=sys.stderr)


def _update_cycle_intent(
    market_slug: str,
    cycle: int,
    run_id: str,
    submitted: int = 0,
    cancelled: int = 0,
    db_path: Path | None = None,
) -> None:
    """Fire-and-forget UPDATE of the cycle_intent row for one market visit.

    Matching on market_slug alone would let a later decide event for the same
    market (before this submit arrives) capture this submit's outcome. cycle +
    run_id + market_slug identify the exact visit the decide event inserted.
    """
    p = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not p.exists():
        return

    def update(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE cycle_intent SET submitted = ?, cancelled = ?
            WHERE id = (
                SELECT id FROM cycle_intent
                WHERE market_slug = ? AND cycle = ? AND run_id = ?
                ORDER BY id DESC LIMIT 1
            )
            """,
            (submitted, cancelled, market_slug, cycle, run_id),
        )
        conn.commit()

    try:
        _with_intent_connection(p, update)
    except Exception as exc:
        # Non-blocking / fire-and-forget
        print(f"WARNING: cycle_intent update failed: {exc}", file=sys.stderr)


def _append_line(path: Path, line: str) -> None:
    """Append one NDJSON line atomically.

    os.write on an O_APPEND fd appends at EOF in a single syscall, so writers
    in other processes (fleet, screener) can interleave whole lines but never
    corrupt or lose one. A plain open("a") does seek-then-write, which on
    Windows loses a line whenever two writers hit the same end offset -- the
    concurrent-append test caught that race.
    """
    with _APPEND_LOCK:
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)


def _rotate_ring_file(ring_path: Path) -> None:
    """Atomic rotation of the ring file keeping the last KEEP_LINES.

    A concurrent writer (fleet/screener) can append between our read and the
    replace. A fully atomic rotation would need a cross-process lock shared
    with those decoupled writers (they must not import core_brain.*), so instead
    we re-stat after reading and skip this rotation when the file grew. The
    residual window (append after the re-stat, before os.replace) can drop at
    most one telemetry line in a rare race; the next emit re-checks.
    """
    try:
        if not ring_path.exists():
            return
        size_before = ring_path.stat().st_size
        with open(ring_path, "r", encoding="utf-8", errors="replace") as rf:
            lines = rf.readlines()
        if len(lines) > MAX_LINES:
            if ring_path.stat().st_size != size_before:
                # A concurrent append landed during our read; don't drop it.
                return
            kept = lines[-KEEP_LINES:]
            tmp_path = ring_path.with_name(f"{ring_path.name}.tmp.{uuid.uuid4()}")
            with open(tmp_path, "w", encoding="utf-8") as tf:
                tf.writelines(kept)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp_path, ring_path)
    except Exception as exc:
        print(f"WARNING: cycle_stream rotation failed: {exc}", file=sys.stderr)


def emit(
    cycle: int,
    phase: str,
    action: str,
    *,
    service: str = "query",
    market_slug: str = "",
    reason: str = "",
    latency_ms: float = 0.0,
    extra: dict | None = None,
    ring_path: Path | None = None,
    db_path: Path | None = None,
    can_rotate: bool | None = None,
) -> None:
    """Append one NDJSON event to the ring file and log cycle intent if relevant.

    Never raises into caller.
    """
    try:
        target_ring = Path(ring_path) if ring_path else DEFAULT_RING_PATH
        target_ring.parent.mkdir(parents=True, exist_ok=True)

        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record: dict[str, Any] = {
            "ts": now_iso,
            "service": service,
            "cycle": cycle,
            "phase": phase,
            "action": action,
            "market_slug": market_slug,
            "reason": reason,
            "latency_ms": round(float(latency_ms), 2) if latency_ms else 0.0,
            "pid": os.getpid(),
            "extra": extra or {},
        }

        _append_line(target_ring, json.dumps(record) + "\n")

        # Rotation check (default True for query service)
        should_rotate = (service in ("query", "engine")) if can_rotate is None else can_rotate
        if should_rotate:
            _rotate_ring_file(target_ring)

        # Database intent recording: the decide event INSERTs the row (with the
        # skip/pass rationale), the submit event of the same visit UPDATEs the
        # submitted/cancelled counts onto it. Anything else never touches SQL.
        if phase == "quoting" and action == "decide":
            _write_cycle_intent(
                cycle=cycle,
                market_slug=market_slug,
                condition_id=(extra or {}).get("condition_id"),
                intent_count=int((extra or {}).get("intent_count", 0)),
                top_skip_reason=(extra or {}).get("top_skip_reason")
                                or (reason if not (extra or {}).get("intent_count") else None),
                top_pass_reason=(extra or {}).get("top_pass_reason")
                                or (reason if (extra or {}).get("intent_count") else None),
                latency_ms=latency_ms,
                db_path=db_path,
            )
        elif phase == "quoting" and action in ("submit", "market_error"):
            # submit carries the outcome of a successful decide; market_error on
            # the submit/cancel path carries the partial counts that must not be
            # left at zero. Either way update the same row the decide inserted.
            _update_cycle_intent(
                market_slug=market_slug,
                cycle=cycle,
                run_id=_resolve_run_id(),
                submitted=int((extra or {}).get("submitted", 0)),
                cancelled=int((extra or {}).get("cancelled", 0)),
                db_path=db_path,
            )
    except Exception as exc:
        print(f"WARNING: cycle_stream emit failed: {exc}", file=sys.stderr)


def read_ring(ring_path: Path | None = None, tail: int = 100) -> list[dict]:
    """Read the last `tail` parsed JSON events from the ring file.

    `emit()` always writes `runtime/`, but a reader with no explicit path
    resolves the pre-rename `run/cycle_events.jsonl` while only that one
    exists -- otherwise the guardrail watcher reads an empty ring right
    after the rename and misses a repeat-exit alert.
    """
    p = Path(ring_path) if ring_path else resolve_runtime_file(
        DEFAULT_RING_PATH.name, root=LIVE_ROOT)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail_lines = lines[-tail:] if tail > 0 else lines
        events = []
        for line in tail_lines:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                events.append(json.loads(line_str))
            except Exception:
                continue
        return events
    except Exception as exc:
        print(f"WARNING: cycle_stream read_ring failed: {exc}", file=sys.stderr)
        return []
