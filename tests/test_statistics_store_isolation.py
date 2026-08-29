from __future__ import annotations

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
