"""START preflight: the dashboard shows what START would launch before it fires."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from core_brain.order_registry import SCHEMA
from dashboard.server import (
    _start_stack_commands,
    app,
    build_start_preview,
    resolve_sweep_interval,
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


def test_preview_lists_the_three_start_bot_commands():
    preview = build_start_preview(
        bot_state="STOPPED",
        db_is_production=True,
        db_mode="LIVE",
        registry_unreadable=False,
        sweep_interval_sec=None,
    )
    assert preview["can_start"] is True
    assert preview["blockers"] == []
    assert preview["commands"] == [
        "python -m scripts.filter_loop",
        "python -m core_brain.order_manager poll --interval 0.5",
        "python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5 --max-markets 1",
    ]


def test_preview_carries_the_configured_sweep_interval():
    preview = build_start_preview(
        bot_state="STOPPED",
        db_is_production=True,
        db_mode="LIVE",
        registry_unreadable=False,
        sweep_interval_sec=30.0,
    )
    assert "--sweep-interval 30" in preview["commands"][1]


def test_preview_blocks_a_running_stack():
    preview = build_start_preview(
        bot_state="RUNNING",
        db_is_production=True,
        db_mode="LIVE",
        registry_unreadable=False,
        sweep_interval_sec=None,
    )
    assert preview["can_start"] is False
    assert any("already RUNNING" in b for b in preview["blockers"])


def test_preview_blocks_a_shadow_view():
    preview = build_start_preview(
        bot_state="STOPPED",
        db_is_production=False,
        db_mode="SHADOW",
        registry_unreadable=False,
        sweep_interval_sec=None,
    )
    assert preview["can_start"] is False
    assert any("production registry" in b for b in preview["blockers"])


def test_preview_blocks_an_unreadable_registry():
    preview = build_start_preview(
        bot_state="UNKNOWN",
        db_is_production=True,
        db_mode="LIVE",
        registry_unreadable=True,
        sweep_interval_sec=None,
    )
    assert preview["can_start"] is False
    assert any("unreadable" in b for b in preview["blockers"])


def test_unknown_state_blocks_even_with_a_readable_registry():
    # Isolates the `or bot_state == "UNKNOWN"` arm: deleting it must fail.
    preview = build_start_preview(
        bot_state="UNKNOWN",
        db_is_production=True,
        db_mode="LIVE",
        registry_unreadable=False,
        sweep_interval_sec=None,
    )
    assert preview["can_start"] is False
    assert any("unreadable" in b for b in preview["blockers"])


def test_stacked_blockers_all_surface():
    preview = build_start_preview(
        bot_state="RUNNING",
        db_is_production=False,
        db_mode="SHADOW",
        registry_unreadable=False,
        sweep_interval_sec=None,
    )
    assert preview["can_start"] is False
    assert len(preview["blockers"]) == 2


def test_preview_commands_come_from_the_same_source_as_start_bot():
    # Structural parity: the display strings are rendered from the argv
    # start_bot spawns, so a flag change cannot move one without the other.
    for sweep in (None, 30.0):
        preview = build_start_preview(
            bot_state="STOPPED",
            db_is_production=True,
            db_mode="LIVE",
            registry_unreadable=False,
            sweep_interval_sec=sweep,
        )
        assert preview["commands"] == ["python " + " ".join(a) for a in _start_stack_commands(sweep)]
    assert "global_stop_loss" not in " ".join(preview["commands"])


def test_preview_echoes_the_sweep_interval():
    preview = build_start_preview(
        bot_state="STOPPED",
        db_is_production=True,
        db_mode="LIVE",
        registry_unreadable=False,
        sweep_interval_sec=30.0,
    )
    assert preview["sweep_interval_sec"] == 30.0


def test_preview_reports_credential_presence_only(monkeypatch):
    def _preview(**env):
        for key in ("POLY_FUNDER", "POLY_PRIVATE_KEY", "POLY_KEY"):
            monkeypatch.delenv(key, raising=False)
        for key, val in env.items():
            monkeypatch.setenv(key, val)
        return build_start_preview(
            bot_state="STOPPED",
            db_is_production=True,
            db_mode="LIVE",
            registry_unreadable=False,
            sweep_interval_sec=None,
        )

    assert _preview()["has_funder"] is False
    assert _preview()["has_signing_key"] is False
    assert _preview(POLY_FUNDER="0xabc")["has_funder"] is True
    assert _preview(POLY_PRIVATE_KEY="secret")["has_signing_key"] is True
    assert _preview(POLY_KEY="secret")["has_signing_key"] is True


def test_non_finite_sweep_interval_falls_back_to_every_tick(monkeypatch):
    monkeypatch.setenv("LIVE_SWEEP_INTERVAL", "inf")
    assert resolve_sweep_interval() is None


def test_status_payload_carries_the_preview(client):
    res = client.get("/api/system/status")
    assert res.status_code == 200
    preview = res.json().get("start_preview")
    assert isinstance(preview, dict)
    assert len(preview.get("commands", [])) == 3
    assert "can_start" in preview and "blockers" in preview
    assert "has_funder" in preview and "has_signing_key" in preview
