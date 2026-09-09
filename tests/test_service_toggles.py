"""Each dashboard toggle drives exactly its own service.

Filter, Query and Decide start and stop alone: flipping Decide starts live
quoting without dragging Filter and Query up, and stopping Query leaves the
others running. The master START/STOP RUN buttons still move the whole stack.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import dashboard.server as dash_mod
from core_brain.order_registry import DEFAULT_DB_PATH, SCHEMA
from dashboard.server import (
    SERVICE_NAMES,
    _start_stack_commands,
    app,
    set_db_override,
)


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "live.db"
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


@pytest.fixture
def prod_client():
    """Point the page at the production registry path (resolved, never written).

    start_service refuses a shadow view, so the spawn tests read as LIVE.
    Nothing here opens the registry: spawning is faked and the account
    snapshot is stubbed; only the scratch runtime dir takes writes.
    """
    set_db_override(DEFAULT_DB_PATH)
    yield TestClient(app)
    set_db_override(None)


def _control():
    return {"X-Control-Token": dash_mod.CONTROL_TOKEN}


class _FakePopen:
    spawned: list = []

    def __init__(self, args, **kwargs):
        _FakePopen.spawned.append(list(args))
        self.pid = 12345

    def terminate(self):
        pass

    def wait(self, timeout=None):
        pass

    def kill(self):
        pass


@pytest.fixture
def no_spawn(monkeypatch, tmp_path):
    """Route the stack at a scratch runtime dir with spawning disabled."""
    _FakePopen.spawned = []
    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(dash_mod, "REPO_ROOT", tmp_path)
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    # Account snapshot must never reach the venue from a test.
    monkeypatch.setattr(dash_mod, "_capture_starting_capital", lambda: None)
    return tmp_path


def _status():
    procs = (dash_mod.LIVE_ROOT / "runtime" / "processes.json")
    return json.loads(procs.read_text(encoding="utf-8")) if procs.exists() else {}


def test_start_service_spawns_only_that_service(prod_client, no_spawn):
    res = prod_client.post("/api/system/service/start?service=filter", headers=_control())
    assert res.status_code == 200
    assert res.json()["ok"] is True

    assert len(_FakePopen.spawned) == 1
    assert _FakePopen.spawned[0][1:] == _start_stack_commands(None)[0]
    assert _status()["filter"]["pid"] == 12345


def test_start_second_service_keeps_the_first(prod_client, no_spawn):
    prod_client.post("/api/system/service/start?service=filter", headers=_control())
    res = prod_client.post("/api/system/service/start?service=decide", headers=_control())
    assert res.json()["ok"] is True

    assert len(_FakePopen.spawned) == 2
    saved = _status()
    assert set(saved) >= {"filter", "decide"}


def test_start_refuses_a_duplicate(prod_client, no_spawn, monkeypatch):
    monkeypatch.setattr(dash_mod, "_is_pid_alive", lambda pid, started=None: True)
    prod_client.post("/api/system/service/start?service=query", headers=_control())
    _FakePopen.spawned = []
    res = prod_client.post("/api/system/service/start?service=query", headers=_control())
    assert res.json()["ok"] is False
    assert "already running" in res.json()["message"]
    assert _FakePopen.spawned == []


def test_start_rejects_unknown_service(client, no_spawn):
    res = client.post("/api/system/service/start?service=watchdog", headers=_control())
    assert res.json()["ok"] is False
    assert _FakePopen.spawned == []


def test_start_refuses_a_shadow_view(client, no_spawn):
    # temp_db is a SHADOW store, not the production registry.
    res = client.post("/api/system/service/start?service=filter", headers=_control())
    assert res.json()["ok"] is False
    assert "production registry" in res.json()["message"]
    assert _FakePopen.spawned == []


def test_stop_service_kills_only_that_service(prod_client, no_spawn, monkeypatch):
    prod_client.post("/api/system/service/start?service=filter", headers=_control())
    prod_client.post("/api/system/service/start?service=decide", headers=_control())

    killed = []
    monkeypatch.setattr(dash_mod, "_is_pid_alive", lambda pid, started=None: True)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: killed.append(a))

    res = prod_client.post("/api/system/service/stop?service=filter", headers=_control())
    assert res.json()["ok"] is True

    saved = _status()
    assert "filter" not in saved
    assert "decide" in saved


def test_stop_idle_service_is_a_clean_noop(client, no_spawn):
    res = client.post("/api/system/service/stop?service=query", headers=_control())
    assert res.json()["ok"] is True
    assert "not running" in res.json()["message"]


def test_stop_clears_a_dead_entry_from_the_file(prod_client, no_spawn):
    procs_file = dash_mod.LIVE_ROOT / "runtime" / "processes.json"
    procs_file.parent.mkdir(parents=True, exist_ok=True)
    procs_file.write_text(json.dumps({
        "filter": {"pid": 99999999, "started_at": 1.0},
        "starting_account_value": 100.0,
    }), encoding="utf-8")

    res = prod_client.post("/api/system/service/stop?service=filter", headers=_control())
    assert res.json()["ok"] is True

    saved = json.loads(procs_file.read_text(encoding="utf-8"))
    assert "filter" not in saved
    assert saved["starting_account_value"] == 100.0


def test_master_stop_holds_the_ops_lock(client, no_spawn, monkeypatch):
    procs_file = dash_mod.LIVE_ROOT / "runtime" / "processes.json"
    procs_file.parent.mkdir(parents=True, exist_ok=True)
    procs_file.write_text(json.dumps({
        "starting_account_value": 100.0,
    }), encoding="utf-8")

    seen_locked = []
    real_acquire = dash_mod._acquire_ops_lock

    def _spy_acquire():
        fd, err = real_acquire()
        if fd is not None:
            seen_locked.append(True)
            assert (dash_mod.LIVE_ROOT / "runtime" / ".bot_start.lock").exists()
        return fd, err

    monkeypatch.setattr(dash_mod, "_acquire_ops_lock", _spy_acquire)
    res = client.post("/api/system/stop", headers=_control())
    assert res.json()["ok"] is True
    assert seen_locked, "stop_bot must take the ops lock"
    assert not (dash_mod.LIVE_ROOT / "runtime" / ".bot_start.lock").exists()


def test_stop_rejects_unknown_service(client, no_spawn):
    res = client.post("/api/system/service/stop?service=watchdog", headers=_control())
    assert res.json()["ok"] is False


def test_service_endpoints_reject_untokened_posts(client):
    assert client.post("/api/system/service/start?service=filter").status_code == 403
    assert client.post("/api/system/service/stop?service=filter").status_code == 403


def test_known_service_names_are_exactly_the_three_toggles():
    assert SERVICE_NAMES == ("filter", "query", "decide")


def test_non_finite_sweep_interval_falls_back_to_every_tick(monkeypatch):
    monkeypatch.setenv("LIVE_SWEEP_INTERVAL", "inf")
    assert dash_mod.resolve_sweep_interval() is None
