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


def test_started_at_falls_back_to_the_recorded_time_when_the_os_will_not_answer(monkeypatch):
    # Arrange — OS creation time unreadable (denied access, odd platform).
    # Stubbed rather than trusted: the test PID may exist on the host running
    # this suite, and then the real creation time would answer instead.
    monkeypatch.setattr(srv, "_process_start_time", lambda pid: None)
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


# --- the rendered header (#31) ---------------------------------------------
# Driven through node against the real dashboard/static/app.js, so these are
# the strings the header would actually print.

import json
import shutil
import subprocess

HARNESS = Path(__file__).resolve().parent / "js" / "filter_uptime_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _readings(payloads: list[dict]) -> list[str]:
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payloads)],
                         capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)["readings"]


def _status(running: bool, started_at: float | None = None,
            uptime_sec: float | None = None) -> dict:
    return {"services": {"filter": {"running": running,
                                    "started_at": started_at,
                                    "uptime_sec": uptime_sec}}}


@requires_node
def test_the_header_reads_the_uptime_as_a_stopwatch():
    # Arrange / Act
    readings = _readings([_status(True, 100.0, 3660.0)])

    # Assert
    assert readings == [" · up 1h 01m"]


@requires_node
def test_the_uptime_never_ticks_backwards_for_the_same_process():
    # Arrange — the second poll reports LESS elapsed time for the same
    # `started_at`, which is what a host clock correction looks like.
    payloads = [_status(True, 100.0, 90.0), _status(True, 100.0, 30.0)]

    # Act
    readings = _readings(payloads)

    # Assert
    assert readings == [" · up 1m 30s", " · up 1m 30s"]


@requires_node
def test_a_restarted_service_counts_from_its_new_start_time():
    # Arrange — stopped, then started again: a new `started_at`, so the
    # ratchet must not hold the old figure.
    payloads = [_status(True, 100.0, 900.0),
                _status(False),
                _status(True, 500.0, 5.0)]

    # Act
    readings = _readings(payloads)

    # Assert
    assert readings == [" · up 15m 00s", "", " · up 5s"]
