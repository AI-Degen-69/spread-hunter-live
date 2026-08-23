"""One order registry, resolved one way, by every module that touches it.

Until 2026-08-23 `engine/order_registry.py` chose between `data/orders.db` and
`run/live.db` at import time, and `engine/cycle_stream.py` hardcoded the second
one. Which database a process bound to therefore depended on start order and on
what happened to be on disk, so orders could land in one file while the cycle
telemetry the dashboard renders landed in another. These tests keep that shut.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("core_brain", "dashboard", "scripts")


def test_default_db_path_is_data_orders_db():
    from core_brain.order_registry import DEFAULT_DB_PATH

    assert DEFAULT_DB_PATH == REPO_ROOT / "data" / "orders.db", DEFAULT_DB_PATH


def test_cycle_stream_shares_the_registry_path():
    """Telemetry writes where the orders are, or the dashboard shows a blank ring."""
    from core_brain import cycle_stream
    from core_brain.order_registry import DEFAULT_DB_PATH

    assert cycle_stream.DEFAULT_DB_PATH == DEFAULT_DB_PATH


def test_default_db_path_has_no_fallback():
    """A conditional path reintroduces the split this module exists to prevent."""
    source = (REPO_ROOT / "core_brain" / "order_registry.py").read_text(encoding="utf-8")
    assignment = re.search(r"^DEFAULT_DB_PATH = .*$", source, re.M)
    assert assignment, "DEFAULT_DB_PATH assignment not found"
    assert " if " not in assignment.group(0), assignment.group(0)


@pytest.mark.parametrize("directory", SOURCE_DIRS)
def test_no_module_hardcodes_the_legacy_database(directory):
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{n}"
        for path in (REPO_ROOT / directory).rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "live.db" in line and not line.lstrip().startswith(("#", "*", '"', "'"))
    ]
    assert not offenders, f"legacy run/live.db referenced in code: {offenders}"
