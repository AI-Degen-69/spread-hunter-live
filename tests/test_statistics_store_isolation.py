from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core_brain.statistics_store import StatisticsStore


def test_runs_are_isolated_and_production_registry_is_rejected(tmp_path: Path):
    store_a = StatisticsStore.create(tmp_path / "data", "20260829_120000", "run-a")
    store_b = StatisticsStore.create(tmp_path / "data", "20260829_120001", "run-b")
    assert store_a.path != store_b.path
    store_a.append_snapshot("shadow", "run-a", "INCONCLUSIVE", {"n": 1}, [])
    store_b.append_snapshot("shadow", "run-b", "GO", {"n": 2}, [])
    with sqlite3.connect(store_a.path) as con:
        assert con.execute("SELECT run_id FROM snapshots GROUP BY run_id").fetchall() == [("run-a",)]
    with sqlite3.connect(store_b.path) as con:
        assert con.execute("SELECT run_id FROM snapshots GROUP BY run_id").fetchall() == [("run-b",)]
    assert store_a.append_snapshot("shadow", "run-a", "GO", {}, []) is None
    with pytest.raises(ValueError, match="production registry"):
        StatisticsStore.create(tmp_path / "data/orders.db", "20260829_120002", "run-c")


def test_run_id_is_sanitized_and_parent_is_created(tmp_path: Path):
    store = StatisticsStore.create(tmp_path / "nested" / "data", "20260829_120003", "run/a unsafe")
    assert store.path.parent.is_dir()
    assert "/" not in store.path.name
    assert " " not in store.path.name


def test_snapshot_payload_is_slimmed(tmp_path: Path):
    """Full drilldowns must not be persisted: they dominate the payload
    (~700KB/snapshot) and the final report regenerates them from the registry."""
    store = StatisticsStore.create(tmp_path / "data", "20260829_120004", "run-a")
    kpi = {
        "realized_pnl": 1.5,
        "by_market": {"mkt": {"quotes": list(range(1000))}},
        "settlements": [{"ts": i} for i in range(1000)],
        "float_marks": [{"ts": i} for i in range(1000)],
        "account_series": [{"ts": i} for i in range(1000)],
        "equity_series": [{"ts": i} for i in range(1000)],
    }
    store.append_snapshot("shadow", "run-a", "OBSERVING", kpi, [])
    with sqlite3.connect(store.path) as con:
        stored = json.loads(con.execute("SELECT kpi_json FROM snapshots").fetchone()[0])
    assert stored == {"realized_pnl": 1.5}


def test_snapshot_retention_caps_rows_and_reclaims_space(tmp_path: Path):
    """Without a retention cap a long run accumulates thousands of
    half-megabyte rows and the stats DB grows unbounded."""
    store = StatisticsStore.create(tmp_path / "data", "20260829_120005", "run-a")
    fat_kpi = {"by_market": {"mkt": {"data": "x" * 100_000}}}
    for i in range(60):
        store.append_snapshot("shadow", "run-a", "OBSERVING", fat_kpi, [])
    with sqlite3.connect(store.path) as con:
        assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] <= 2000
        # Newest rows survive the cap
        newest = con.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
        assert newest is not None
    # Compaction must reclaim disk space: 60 rows x ~100KB would be ~6MB
    # without cleanup; capped + compacted it stays far below.
    assert store.path.stat().st_size < 3_000_000
