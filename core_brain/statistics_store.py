from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from core_brain.shadow_guard import assert_not_production_registry


class StatisticsStore:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def create(cls, data_dir: Path | str, timestamp: str, run_id: str) -> "StatisticsStore":
        data = Path(data_dir)
        if data.name == "orders.db":
            raise ValueError("production registry cannot be used as a statistics store")
        path = data / f"stats_{timestamp}_{_sanitize(run_id)}.db"
        assert_not_production_registry(path)
        data.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    ts REAL NOT NULL,
                    mode TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    kpi_json TEXT NOT NULL,
                    gate_rows_json TEXT NOT NULL
                )
            """)
        return cls(path)

    def append_snapshot(
        self,
        mode: str,
        run_id: str,
        verdict: str,
        kpi: dict[str, Any],
        gate_rows: list[dict[str, Any]],
    ) -> None:
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT INTO snapshots VALUES (strftime('%s','now'), ?, ?, ?, ?, ?)",
                (mode, run_id, verdict, json.dumps(kpi, default=str), json.dumps(gate_rows, default=str)),
            )


def _sanitize(run_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id)).strip("._")
    return token or "run"
