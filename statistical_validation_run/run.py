"""Statistical validation harness: live pipeline execution with zero financial risk.

`python -m statistical_validation_run --target-closes 50 --max-hours 12.0`

Runs the live strategy against live CLOB books via a read-only client proxy into
an isolated SQLite database, stopping when either the target number of closes is
reached or the maximum time limit expires.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import os
import pathlib
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core_brain.config import MakerConfig, load as load_cfg
from core_brain.runtime_paths import resolve_runtime_file
from core_brain.shadow_guard import assert_not_production_registry
from core_brain.shadow_run import (
    _Deadline,
    _default_fetch_books,
    _default_markets_fn,
    run_shadow,
    shadow_run_id,
    ShadowResult,
)
from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD

log = logging.getLogger("statistical_validation_run")


def count_closes(db_path: Path | str, run_id: Optional[str] = None) -> int:
    """Return count of closed trades in the given database, filtered by run_id if provided.

    Returns 0 if the database file does not exist, the closes table is missing,
    the run_id column is missing when run_id is supplied, or the query encounters an error.
    """
    path = Path(db_path)
    if not path.is_file():
        return 0
    try:
        con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            con = sqlite3.connect(str(path))
        except sqlite3.Error:
            return 0

    try:
        with con:
            cur = con.cursor()
            table_check = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='closes'"
            ).fetchone()
            if not table_check:
                return 0
            if run_id:
                cols = {row[1] for row in cur.execute("PRAGMA table_info(closes)").fetchall()}
                if "run_id" not in cols:
                    return 0
                row = cur.execute(
                    "SELECT COUNT(*) FROM closes WHERE run_id = ?", (run_id,)
                ).fetchone()
            else:
                row = cur.execute("SELECT COUNT(*) FROM closes").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except (sqlite3.Error, OSError):
        return 0
    finally:
        try:
            con.close()
        except Exception:
            pass


def make_hybrid_deadline_sleep(
    deadline_ts: float,
    count_closes_fn: Callable[[], int],
    target_closes: Optional[int] = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[float], None]:
    """A sleep_fn for trader_loop.run that stops on either target closes or deadline_ts.

    Raises `_Deadline` once the close count reaches target_closes or once the
    clock reaches or exceeds deadline_ts. Otherwise sleeps, clamped to remaining time.
    """
    def sleep_fn(seconds: float) -> None:
        if target_closes is not None and target_closes > 0:
            current_closes = count_closes_fn()
            if current_closes >= target_closes:
                raise _Deadline()
        remaining = deadline_ts - clock()
        if remaining <= 0:
            raise _Deadline()
        sleep(max(0.0, min(seconds, remaining)))

    return sleep_fn


def snapshot_stat_validation_env(
    artifact_dir: Path,
    cfg: MakerConfig,
    *,
    root: Optional[Path] = None,
) -> None:
    """Create artifact directory and snapshot pipeline inputs and effective config."""
    artifact_dir.mkdir(parents=True, exist_ok=True)

    for fname in ("pipeline.json", "markets.json"):
        src = resolve_runtime_file(fname, root=root)
        if src.is_file():
            try:
                shutil.copy2(src, artifact_dir / fname)
            except OSError as e:
                log.warning("snapshot: failed to copy %s: %s", fname, e)
        else:
            log.warning("snapshot: source file %s missing, skipping", fname)

    cfg_path = artifact_dir / "config.json"
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(cfg), f, indent=2)
    except (OSError, TypeError) as e:
        log.warning("snapshot: failed to write config.json: %s", e)

    log.info(
        "STAT HARNESS config: max_order_usd=%.2f max_total_usd=%.2f objective=%s bankroll_usd=%.2f",
        MAX_ORDER_USD,
        MAX_TOTAL_USD,
        getattr(cfg, "objective", "balanced"),
        getattr(cfg, "bankroll_usd", 0.0),
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Statistical Validation Harness: run live pipeline with no-money ReadOnlyVenue."
    )
    ap.add_argument(
        "--target-closes",
        type=int,
        default=None,
        help="target number of completed closes before stopping",
    )
    ap.add_argument(
        "--max-hours",
        type=float,
        default=1.0,
        help="maximum execution time in hours (default: 1.0)",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="rotation cadence in seconds (default: 5.0)",
    )
    ap.add_argument(
        "--db",
        type=str,
        default=None,
        help="isolated shadow database path (default: data/shadow_stat_<ts>.db)",
    )
    ap.add_argument(
        "--max-markets",
        type=int,
        default=None,
        help="cap the number of markets rotated (default: all)",
    )
    ap.add_argument(
        "--funder",
        default=None,
        help="funder address for live balance read (default: POLY_FUNDER)",
    )
    return ap.parse_args(argv)


def main(
    argv: Optional[list[str]] = None,
    *,
    markets_fn: Optional[Callable[..., list]] = None,
    client_fn: Optional[Callable[[], Any]] = None,
    decide_fn: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    root: Optional[Path] = None,
) -> int:
    """The statistical validation harness entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    a = _parse_args(argv)

    run_id = shadow_run_id()
    ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    db_path = Path(a.db) if a.db else Path(f"data/shadow_stat_{ts_str}_{run_id}.db")

    # Fail fast if production registry is passed
    assert_not_production_registry(db_path)

    log.warning(
        "SHADOW RUN starting: mode=stat_validation target_closes=%s max_hours=%s interval=%ss store=%s run_id=%s "
        "-- NO SIGNER LOADED: this process cannot place, cancel or merge anything. Numbers below are rehearsal, not results.",
        a.target_closes,
        a.max_hours,
        a.interval,
        db_path,
        run_id,
    )

    artifact_dir = Path(f"data/shadow_stat_{ts_str}_{run_id}")

    from dataclasses import replace as dc_replace
    cfg = dc_replace(load_cfg(), single_buy_grace_sec=0.0)
    maker = a.funder or os.environ.get("POLY_FUNDER")
    if maker:
        try:
            from core_brain.account import fetch_live_balance
            live_bal = fetch_live_balance(maker)
            if live_bal is not None and live_bal > 0:
                cfg = dc_replace(cfg, bankroll_usd=live_bal)
        except Exception as e:  # noqa: BLE001 - degrade, do not stop
            log.warning("live balance read failed, using config bankroll: %s", e)

    snapshot_stat_validation_env(artifact_dir, cfg, root=root)

    deadline_ts = clock() + max(0.0, float(a.max_hours) * 3600.0)
    hybrid_sleep = make_hybrid_deadline_sleep(
        deadline_ts=deadline_ts,
        count_closes_fn=lambda: count_closes(db_path, run_id),
        target_closes=a.target_closes,
        clock=clock,
        sleep=sleep,
    )

    result: ShadowResult = run_shadow(
        minutes=float(a.max_hours) * 60.0,
        db_path=db_path,
        run_id=run_id,
        sleep_fn=hybrid_sleep,
        interval=a.interval,
        funder=a.funder,
        cfg=cfg,
        markets_fn=(
            markets_fn
            or (lambda max_markets=None: _default_markets_fn()(a.max_markets))
        ),
        client_fn=client_fn,
        decide_fn=decide_fn,
        fetch_books=fetch_books or _default_fetch_books(),
    )

    quoted = sum(1 for r in result.results if getattr(r, "status", None) == "QUOTED")
    declined = sum(1 for r in result.results if getattr(r, "status", None) == "DECLINED")
    errors = sum(1 for r in result.results if getattr(r, "status", None) == "ERROR")
    log.warning(
        "SHADOW RUN finished: rotations_returned=%d quoted=%d declined=%d "
        "errors=%d intents_recorded=%d skipped=%s",
        len(result.results),
        quoted,
        declined,
        errors,
        len(result.intents),
        ",".join(result.skipped_stages),
    )

    return 0 if result.results else 1
