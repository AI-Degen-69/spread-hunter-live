"""`/api/scan-state` during a shadow rehearsal.

A shadow run has no live poll loop, so it writes no `live_poll_heartbeat.json`
and its per-cycle events go to `runtime/shadow-<run_id>.jsonl`, not the live
`cycle_events.jsonl` the screener appends to. Before this, the dashboard read
the live ring (no rehearsal events) and the absent heartbeat, so the scan pill
showed STALLED for a healthy rehearsal.

Now `resolve_ring_path()` follows the rehearsal's ring when the page is on a
shadow store, and `get_scan_state()` falls back to the shadow heartbeat.
"""
from __future__ import annotations

import datetime
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from core_brain.order_registry import SCHEMA
from dashboard import server as srv
from dashboard.server import (
    app,
    set_db_override,
    set_heartbeat_override,
    set_ring_override,
)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "01_shadow.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


@pytest.fixture
def client(temp_db):
    set_db_override(temp_db)
    yield TestClient(app)
    set_db_override(None)


def _fresh_quoting_ring(tmp_path):
    ring = tmp_path / "shadow-01.jsonl"
    ring.write_text(
        json.dumps({
            "ts": _now_iso(), "service": "decide", "cycle": 42,
            "phase": "quoting", "action": "decide", "market_slug": "atp-x",
            "reason": "", "latency_ms": 0.0, "pid": 1, "extra": {},
        }) + "\n",
        encoding="utf-8",
    )
    return ring


def test_scan_state_falls_back_to_the_shadow_heartbeat(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "heartbeat_age_sec": 4.0, "run_id": "shadow-01"},
    )
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    assert res.status_code == 200
    data = res.json()
    # Fresh quoting event + a live shadow heartbeat -> SCANNING, not STALLED.
    assert data["scan_state"] == "SCANNING"
    assert data["seconds_since_heartbeat"] is not None
    assert data["seconds_since_heartbeat"] == pytest.approx(4.0, abs=2.0)


def test_scan_state_stays_stalled_with_no_heartbeat_and_no_shadow(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(srv, "read_shadow_run", lambda *a, **k: None)
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    assert res.json()["scan_state"] == "STALLED"


def test_scan_state_ignores_an_ended_shadow_run(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": False, "heartbeat_age_sec": 900.0, "run_id": "shadow-01"},
    )
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    assert res.json()["scan_state"] == "STALLED"


# --- ring resolution --------------------------------------------------------


def test_resolve_shadow_ring_points_at_the_run_scoped_file(tmp_path, monkeypatch):
    ring = tmp_path / "shadow-01.jsonl"
    ring.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "run_id": "shadow-01"},
    )
    monkeypatch.setattr(srv, "resolve_runtime_file", lambda name, root=None: tmp_path / name)

    assert srv._resolve_shadow_ring_path() == ring


def test_resolve_shadow_ring_is_none_when_the_ring_is_not_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "run_id": "shadow-01"},
    )
    monkeypatch.setattr(srv, "resolve_runtime_file", lambda name, root=None: tmp_path / name)

    assert srv._resolve_shadow_ring_path() is None


def test_resolve_shadow_ring_is_none_without_a_running_rehearsal(monkeypatch):
    monkeypatch.setattr(srv, "read_shadow_run", lambda *a, **k: None)

    assert srv._resolve_shadow_ring_path() is None


def test_resolve_ring_path_prefers_an_explicit_override(tmp_path, monkeypatch):
    override = tmp_path / "explicit.jsonl"
    override.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "run_id": "shadow-01"},
    )
    set_ring_override(override)
    try:
        assert srv.resolve_ring_path() == override
    finally:
        set_ring_override(None)
