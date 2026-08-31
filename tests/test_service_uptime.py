"""Service uptime on the SCREENER header (#31).

`last scan: 17m ago` says when something last happened, not whether the process
behind it started two minutes or two days ago. The status payload now carries
each service's start time and elapsed uptime so the header can run a stopwatch,
and a stopped service reports neither -- an uptime for a dead process is a lie
the STOPPED pill beside it contradicts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard import server as srv

RUNNING_PID = 4242
STOPPED_PID = 777
OS_START = 1_788_000_000.0
RECORDED_START = 1_788_000_100.0

_STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"


@pytest.fixture
def status_with(tmp_path, monkeypatch):
    """Build a status payload from a synthetic processes.json."""

    def _build(procs: dict, now: float = OS_START + 3600.0):
        registry = tmp_path / "processes.json"
        registry.write_text(json.dumps(procs), encoding="utf-8")
        monkeypatch.setattr(srv, "resolve_runtime_file", lambda *a, **k: registry)
        monkeypatch.setattr(srv, "_is_pid_alive",
                            lambda pid, started_at=None: pid == RUNNING_PID)
        monkeypatch.setattr(srv, "_process_start_time",
                            lambda pid: OS_START if int(pid) == RUNNING_PID else None)
        monkeypatch.setattr(srv.time, "time", lambda: now)
        return srv.get_system_status()

    return _build


def test_status_reports_start_time_and_uptime_for_a_running_service(status_with):
    # Arrange
    procs = {"filter": {"pid": RUNNING_PID, "started_at": RECORDED_START}}

    # Act
    filter_service = status_with(procs)["services"]["filter"]

    # Assert — OS-reported creation time wins, and uptime is measured from it.
    assert filter_service["running"] is True
    assert filter_service["started_at"] == OS_START
    assert filter_service["uptime_sec"] == pytest.approx(3600.0)


def test_status_reports_no_uptime_for_a_stopped_service(status_with):
    # Arrange
    procs = {"filter": {"pid": STOPPED_PID, "started_at": RECORDED_START}}

    # Act
    filter_service = status_with(procs)["services"]["filter"]

    # Assert
    assert filter_service["running"] is False
    assert filter_service["started_at"] is None
    assert filter_service["uptime_sec"] is None


def test_started_at_falls_back_to_the_recorded_time_when_the_os_will_not_answer():
    # Arrange — OS creation time unreadable (denied access, odd platform).
    info = {"pid": RUNNING_PID, "started_at": RECORDED_START}

    # Act
    started = srv._service_started_at(RUNNING_PID, info, running=True)

    # Assert
    assert started == RECORDED_START


def test_uptime_never_reads_negative_when_the_clock_moves_backwards():
    # Arrange / Act
    uptime = srv._uptime_sec(OS_START, now=OS_START - 30.0)

    # Assert
    assert uptime == 0.0


def test_screener_header_has_a_slot_for_the_filter_uptime():
    # Arrange / Act
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Assert
    assert 'id="scan-filter-uptime"' in html
    assert "renderFilterUptime" in app_js
