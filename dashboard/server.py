"""Single-market live execution monitor (:8799).

Watched during ONE supervised live cycle or unattended operation.
Lifts proven UI components from the paper-run dashboards:
- Level 1: Run-level Strategy KPI tile grid, tooltips, and bell curves from `server/spread_dash_html.py:1525-1567`
- Level 2: Selection funnel (RAW -> FILTERS -> FINAL -> GRADUATED) & refusal cards from `server/fleet_dash.py:1106-1180`
- Level 2: Market drill-down (quotes vs mid, 4 markout horizons, skip events, settlements) from `server/spread_dash.py:598`
- Level 3: Mechanics & system health (latency, reconcile lag, venue errors, 3-way divergences) from `server/spread_dash_html.py:1572`
- Req 4: Exposure over time (unrealized, committed, naked USD) from `server/spread_dash_html.py:1505-1509`
- Req 5: Run selector with multi-run isolation from `server/spread_dash.py:181`

Telemetry only: reads SQLite orders, fills, and reconcile_lock directly
from `data/orders.db` via read-only URI mode:
`sqlite3.connect('file:<path>?mode=ro', uri=True)`.

Zero venue network calls. Zero credentials needed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Generator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

# live/, one level up from live/dash/. Everything this page reads lives under it.
LIVE_ROOT = Path(__file__).resolve().parent.parent
# Launching this file by path (`python live/dash/live_dash.py`) puts live/dash/ on
# sys.path, not live/, so `import core_brain.kpi` fails at request time with a 500 that
# the live suite never sees -- it runs with live/ as the working directory.
if str(LIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVE_ROOT))

# Runtime state moved run/ -> runtime/ and some of its files were renamed. The
# page must still see a stack that was started before that move: a registry it
# cannot find reads as STOPPED, and START would then launch a second live
# Trader beside the running one. resolve_runtime_file falls back to the old
# path while only the old file exists. See core_brain/runtime_paths.py.
from core_brain.runtime_paths import (  # noqa: E402
    legacy_runtime_file,
    resolve_runtime_file,
    runtime_file,
    service_entry,
)

REPO_ROOT = LIVE_ROOT
DEFAULT_PORT = 8799
# The port actually bound. main() overwrites it when --port is given; the status
# payload must report where the page really is, not where it usually is.
_ACTIVE_PORT = DEFAULT_PORT
POLL_INTERVAL_MS = 2000

CYCLE_RING_NAME = "cycle_events.jsonl"
SSE_REPLAY_LINES = 50
SSE_POLL_SEC = 0.5
SSE_KEEPALIVE_SEC = 15.0
SCAN_STALL_THRESHOLD_SEC = 90.0

_ACTIVE_RING_OVERRIDE: Path | None = None
_ACTIVE_HEARTBEAT_OVERRIDE: Path | None = None
_ACTIVE_GUARDRAIL_HB_OVERRIDE: Path | None = None


def set_ring_override(path: Path | str | None) -> None:
    """Point the cycle-stream/scan-state endpoints at a specific ring file (tests)."""
    global _ACTIVE_RING_OVERRIDE
    _ACTIVE_RING_OVERRIDE = Path(path) if path else None


def set_heartbeat_override(path: Path | str | None) -> None:
    """Point scan-state at a specific heartbeat file (tests)."""
    global _ACTIVE_HEARTBEAT_OVERRIDE
    _ACTIVE_HEARTBEAT_OVERRIDE = Path(path) if path else None


def set_guardrail_heartbeat_override(path: Path | str | None) -> None:
    """Point the guardrail-health endpoint at a specific heartbeat file (tests)."""
    global _ACTIVE_GUARDRAIL_HB_OVERRIDE
    _ACTIVE_GUARDRAIL_HB_OVERRIDE = Path(path) if path else None


def resolve_ring_path() -> Path:
    if _ACTIVE_RING_OVERRIDE is not None:
        return _ACTIVE_RING_OVERRIDE
    return resolve_runtime_file(CYCLE_RING_NAME, root=LIVE_ROOT)


def resolve_heartbeat_path() -> Path:
    if _ACTIVE_HEARTBEAT_OVERRIDE is not None:
        return _ACTIVE_HEARTBEAT_OVERRIDE
    return resolve_runtime_file("live_poll_heartbeat.json", root=LIVE_ROOT)


def resolve_guardrail_heartbeat_path() -> Path:
    """The watcher's self-report file (live/scripts/global_stop_loss.py).

    Falls back to the pre-rename guardrail_watch_heartbeat.json so a watcher
    started before the rename does not read as a dead safety monitor.
    """
    if _ACTIVE_GUARDRAIL_HB_OVERRIDE is not None:
        return _ACTIVE_GUARDRAIL_HB_OVERRIDE
    return resolve_runtime_file("global_stop_loss_heartbeat.json", root=LIVE_ROOT)


def resolve_db_path(custom_path: str | Path | None = None) -> Path:
    """Find the live registry SQLite database path."""
    if custom_path:
        return Path(custom_path)
    env_path = os.environ.get("LIVE_DB_PATH")
    if env_path:
        return Path(env_path)
    from core_brain.order_registry import DEFAULT_DB_PATH
    return DEFAULT_DB_PATH


def resolve_db_identity(db_path: Path | str) -> dict:
    """Which registry the page is reading: the live one, or something else.

    Three answers, never two. LIVE is the production registry
    (`core_brain.order_registry.DEFAULT_DB_PATH`); SHADOW is any other store the
    page was pointed at with `--db` or `LIVE_DB_PATH`; UNKNOWN is a path the
    platform refuses to resolve.

    UNKNOWN reports `is_production=False`, and that direction is the whole
    point. START is gated on this flag, so a path we cannot resolve refuses a
    live stack rather than launching one on a guess. The mirror-image guard in
    `core_brain/shadow_guard.py:assert_not_production_registry` fails closed the
    other way -- it refuses a shadow *write* it cannot prove is safe -- because
    there the dangerous outcome is the opposite one.
    """
    from core_brain.order_registry import DEFAULT_DB_PATH

    raw = Path(db_path)
    try:
        same = raw.resolve() == DEFAULT_DB_PATH.resolve()
    except (OSError, ValueError, RuntimeError):
        return {"path": str(raw), "mode": "UNKNOWN", "is_production": False}
    return {
        "path": str(raw),
        "mode": "LIVE" if same else "SHADOW",
        "is_production": same,
    }


def resolve_sweep_interval() -> float | None:
    """Configured account-sweep cadence in seconds, or None for every tick.

    Read from LIVE_SWEEP_INTERVAL so the operator can throttle the venue
    reads the account card depends on without editing code. An absent,
    invalid, or non-positive value falls back to the every-tick default.
    """
    raw = (os.environ.get("LIVE_SWEEP_INTERVAL") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _env_file() -> Path | None:
    """The .env file core_brain.order_manager loads, found without importing it.

    Mirrors core_brain.order_manager._find_env_file: the nearest .env walking up
    from core_brain/, stopping at the AGENTS.md boundary. The dashboard never
    loads the whole file -- only LIVE_SWEEP_INTERVAL is read or written -- so
    the signing key and L2 credentials never enter this process.
    """
    curr = LIVE_ROOT / "core_brain"
    for _ in range(4):
        if (curr / ".env").is_file():
            return curr / ".env"
        if (curr / "AGENTS.md").is_file():
            break
        if curr.parent == curr:
            break
        curr = curr.parent
    return None


def read_sweep_interval_from_env_file(env_path: Path) -> float | None:
    """Parse LIVE_SWEEP_INTERVAL from a .env file, or None.

    Reads the file as text and looks only at that key, so no credential line
    is ever materialised anywhere it could be logged.
    """
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "LIVE_SWEEP_INTERVAL":
            continue
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = float(raw)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def write_sweep_interval_to_env_file(env_path: Path, value: float | None) -> bool:
    """Set or remove LIVE_SWEEP_INTERVAL in .env without disturbing the rest.

    Atomic temp-file + fsync + os.replace, carrying over the file's permission
    bits, because the file may hold POLY_PRIVATE_KEY and must never be
    truncated in place.
    """
    try:
        text = env_path.read_text(encoding="utf-8")
        try:
            mode = os.stat(env_path).st_mode
        except OSError:
            mode = None
    except OSError:
        return False

    lines = [
        ln for ln in text.splitlines()
        if ln.split("=", 1)[0].strip() != "LIVE_SWEEP_INTERVAL"
    ]
    if value is not None:
        lines += [
            "",
            "# Account-sweep cadence for the live dashboard (seconds).",
            f"LIVE_SWEEP_INTERVAL={value:g}",
        ]
    new_text = "\n".join(lines)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"

    tmp = env_path.with_name(f".env.tmp.{secrets.token_hex(4)}")
    try:
        with open(tmp, "w", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as fh:
            fh.write(new_text)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, env_path)
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _bootstrap_sweep_interval() -> None:
    """Seed LIVE_SWEEP_INTERVAL from .env once, without loading credentials.

    An explicit environment variable wins; only when it is absent do we read
    the single key back from the file the engine loads. Everything else in
    .env -- the signing key, L2 credentials -- stays on disk.
    """
    if "LIVE_SWEEP_INTERVAL" in os.environ:
        return
    env_file = _env_file()
    if env_file is None:
        return
    saved = read_sweep_interval_from_env_file(env_file)
    if saved is not None:
        os.environ["LIVE_SWEEP_INTERVAL"] = str(saved)


_bootstrap_sweep_interval()


app = FastAPI(title="Spread Hunter Live Monitor")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Mount static files (CSS/JS) for the extracted frontend.
# HTML is still templated by index() to inject the per-process control token.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Every /api/system/* route changes machine state: start spawns the live
# execution loop that signs real venue requests, reset-db deletes the registry,
# restart-dash ends this process. Loopback binding is not a defence -- a page
# open in the operator's browser can submit a cross-origin form POST to
# 127.0.0.1:8799 with no CORS preflight, and the side effect lands even though
# the attacker cannot read the reply.
#
# The token is generated per process and handed to the page that this process
# serves, so there is nothing for the operator to configure and no state to
# leak between runs. A simple form POST cannot set a custom header, which is
# what makes the header requirement a complete CSRF defence on its own; the
# Origin check is the second lock.
CONTROL_TOKEN = secrets.token_urlsafe(32)
CONTROL_TOKEN_PLACEHOLDER = "__LIVE_DASH_CONTROL_TOKEN__"


def _authorize_control(request: Request) -> None:
    """Reject cross-origin or untokened attempts to change machine state."""
    origin = request.headers.get("origin")
    if origin is not None:
        allowed = {f"http://{request.url.netloc}", f"https://{request.url.netloc}"}
        if origin not in allowed:
            raise HTTPException(status_code=403, detail="cross-origin control request refused")
    if request.headers.get("x-control-token") != CONTROL_TOKEN:
        raise HTTPException(status_code=403, detail="missing or stale control token")

_ACTIVE_DB_OVERRIDE: Path | None = None


def set_db_override(path: Path | str | None) -> None:
    global _ACTIVE_DB_OVERRIDE
    _ACTIVE_DB_OVERRIDE = Path(path) if path else None


@app.get("/api/state")
def get_state():
    """Return JSON state snapshot for the live execution dashboard."""
    from core_brain.registry_state import summarize_state
    return JSONResponse(summarize_state(resolve_db_path(_ACTIVE_DB_OVERRIDE)))


# How far a process's real creation time may sit from the time we recorded for
# it. The parent writes started_at immediately after Popen, so a genuine match
# is sub-second; this is slack for clock granularity, not for a different
# process. A recycled PID landing inside this window is not a case worth
# engineering around -- a PID that came back within a minute is still the same
# generation of work.
PID_START_TOLERANCE_S: float = 60.0


def _win_process_times(pid: int) -> tuple[float | None, float | None] | None:
    """(created, exited) as Unix timestamps for a Windows PID.

    `exited` is None while the process is still running. It is not always zero
    for a dead one: a process whose parent still holds an open handle stays
    queryable after exit, and only this field distinguishes it from a live one.

    Returns None when the process cannot be opened at all.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    # argtypes and restype are declared, not left to ctypes' defaults. A HANDLE
    # is pointer-sized, and the default `c_int` restype truncates it on 64-bit
    # Windows -- so a handle above 2**31 would come back as a different value,
    # be passed to GetProcessTimes as garbage, and then be closed as garbage.
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not k32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user)):
            return None

        def _unix(ft) -> float | None:
            # FILETIME counts 100ns intervals since 1601-01-01; the Unix epoch
            # is 11644473600 seconds later. Zero means "not set".
            ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            return None if ticks == 0 else ticks / 1e7 - 11644473600.0

        return _unix(creation), _unix(exited)
    finally:
        k32.CloseHandle(handle)


def _process_start_time(pid: int) -> float | None:
    """Unix timestamp the process was created, or None if it cannot be read."""
    try:
        if sys.platform == "win32":
            times = _win_process_times(int(pid))
            return None if times is None else times[0]
        # Linux: field 22 of /proc/<pid>/stat is starttime in clock ticks since
        # boot. The comm field can contain spaces and parentheses, so the split
        # starts after the last ')'.
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = stat[stat.rindex(")") + 2:].split()
        starttime_ticks = float(fields[19])
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/stat", encoding="utf-8") as fh:
            btime = next(float(line.split()[1])
                         for line in fh if line.startswith("btime "))
        return btime + starttime_ticks / hz
    except Exception:
        return None


def _is_pid_alive(pid: int | None, started_at: float | None = None) -> bool:
    """Is the process we recorded still running?

    A bare PID check is not enough. The OS recycles PIDs, and on this project it
    did: the bot exited, Windows handed 13052 to msedge, and the dashboard then
    reported the bot stack RUNNING forever -- which permanently refused every
    `Fresh DB` reset and would have refused a legitimate start. When we know
    when the process was supposed to have started, the creation time must agree.

    If the creation time cannot be read (unsupported platform, denied access),
    this falls back to the bare PID check rather than declaring the process
    dead: a false "stopped" would let a second bot stack launch alongside a
    live one, which AGENTS.md forbids outright.
    """
    if not pid or pid <= 0:
        return False
    created: float | None = None
    try:
        if sys.platform == "win32":
            times = _win_process_times(int(pid))
            if times is None:
                return False
            created, exited = times
            if exited is not None:
                # Queryable but finished -- a handle is still open somewhere.
                return False
        else:
            try:
                os.kill(int(pid), 0)
            except PermissionError:
                # EPERM means the process exists but belongs to another user.
                # Letting the bare `except` below turn that into False is the
                # false "stopped" this function exists to avoid -- it would let
                # a second bot stack launch beside a live one.
                pass
    except Exception:
        return False

    if started_at is None:
        return True
    if created is None:
        created = _process_start_time(int(pid))
    if created is None:
        return True
    return abs(created - float(started_at)) <= PID_START_TOLERANCE_S


def get_system_status() -> dict:
    """Return live running status for 3 sub-services (Market Filter, Query Polymarket, Decide & Execute) and Telemetry."""
    procs_file = resolve_runtime_file("processes.json", root=LIVE_ROOT)
    saved_procs: dict[str, Any] = {}
    # A registry we cannot read is NOT an empty registry. Reporting STOPPED for
    # a truncated or half-written processes.json is how START gets permission to
    # launch a second live Trader beside the running one, so this path fails
    # closed: bot_state becomes UNKNOWN and every control that guards on
    # "not RUNNING" refuses until the file is readable again.
    registry_unreadable = False
    if procs_file.exists():
        try:
            loaded = json.loads(procs_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            registry_unreadable = True
        else:
            if isinstance(loaded, dict):
                saved_procs = loaded
            else:
                registry_unreadable = True

    # service_entry accepts the pre-rename keys (screener/engine/fleet) so a
    # stack started before the rename is still reported as RUNNING. Without
    # that, start_bot's "already running" guard would wave through a second
    # live Trader.
    filter_info = service_entry(saved_procs, "filter")
    filter_pid = filter_info.get("pid")
    filter_running = _is_pid_alive(filter_pid, filter_info.get("started_at"))

    query_info = service_entry(saved_procs, "query")
    query_pid = query_info.get("pid")
    query_running = _is_pid_alive(query_pid, query_info.get("started_at"))

    decide_info = service_entry(saved_procs, "decide")
    decide_pid = decide_info.get("pid")
    decide_running = _is_pid_alive(decide_pid, decide_info.get("started_at"))

    configured_sweep_interval = resolve_sweep_interval()
    running_sweep_interval = query_info.get("sweep_interval_sec") if query_running else None

    dash_running = True
    dash_pid = os.getpid()

    bot_running = bool(filter_running or query_running or decide_running)

    db_identity = resolve_db_identity(resolve_db_path(_ACTIVE_DB_OVERRIDE))

    return {
        "services": {
            "filter": {
                "name": "Market Filter",
                "running": filter_running,
                "pid": filter_pid if filter_running else None,
            },
            "query": {
                "name": "Query Polymarket",
                "running": query_running,
                "pid": query_pid if query_running else None,
                "sweep_interval_sec": configured_sweep_interval,
                "running_sweep_interval_sec": running_sweep_interval,
            },
            "decide": {
                "name": "Decide & Execute",
                "running": decide_running,
                "pid": decide_pid if decide_running else None,
            },
            "dash": {
                "name": "Telemetry (dash)",
                "running": dash_running,
                "pid": dash_pid,
                "port": _ACTIVE_PORT,
            },
        },
        "bot_state": (
            "UNKNOWN" if registry_unreadable
            else ("RUNNING" if bot_running else "STOPPED")
        ),
        "registry_path": str(procs_file),
        "registry_unreadable": registry_unreadable,
        # Which store these numbers came from. The page renders identically
        # against the production registry and against a shadow rehearsal, so
        # the mode has to travel with the data rather than live in the operator's
        # memory of how they launched the server.
        "db_path": db_identity["path"],
        "db_mode": db_identity["mode"],
        "db_is_production": db_identity["is_production"],
        "starting_capital": get_starting_capital(),
        "timestamp": time.time(),
    }


def _capture_starting_capital() -> float | None:
    """Snapshot the real account equity at bot-start time.

    Returns the venue-reported account_value_usd, or None if the venue is
    unreachable (the dashboard falls back to the config bankroll label).
    Never raises -- a failed balance read at start time must not block the
    bot from launching.
    """
    try:
        from core_brain.order_manager import account_sweep
        result = account_sweep(quiet=True)
        if isinstance(result, dict) and result.get("account_value_usd") is not None:
            return float(result["account_value_usd"])
    except (Exception, SystemExit):
        # account_sweep exits with SystemExit when POLY_FUNDER is unset (a
        # missing funder must not block the bot from launching) and raises
        # RuntimeError from the test socket guard / venue failures otherwise.
        # Both are a failed balance read: skip the snapshot, keep starting.
        pass
    return None


def get_starting_capital() -> float | None:
    """Read the starting capital snapshot from processes.json.

    Returns the account_value_usd captured at bot-start time, or None if
    no snapshot exists (bot never started, or venue was unreachable).
    """
    procs_file = resolve_runtime_file("processes.json", root=LIVE_ROOT)
    if not procs_file.exists():
        return None
    try:
        saved = json.loads(procs_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Same rule as get_system_status: an unreadable registry is not an
        # empty one. There is no number to report, and the caller renders the
        # config bankroll label instead.
        return None
    if not isinstance(saved, dict):
        return None
    val = saved.get("starting_account_value")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def start_bot() -> dict:
    """Launch background Screener and Reconcile loop."""
    import subprocess
    import tempfile

    # The stack always writes the production registry, whatever this page is
    # reading. Started from a shadow view, real maker bids would rest behind a
    # page that cannot show a single one of them -- no orders, no fills, no
    # exposure bar -- which reads as "nothing happened". Refuse before the
    # start lock, so a refusal leaves nothing for a later live start to clear.
    db_identity = resolve_db_identity(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    if not db_identity["is_production"]:
        from core_brain.order_registry import DEFAULT_DB_PATH
        return {
            "ok": False,
            "message": (
                f"This dashboard is reading {db_identity['path']} "
                f"({db_identity['mode']}), not the production registry "
                f"{DEFAULT_DB_PATH}. START launches the live stack against the "
                f"production registry, so its orders would be invisible here. "
                f"Restart the dashboard without --db / LIVE_DB_PATH to start "
                f"the stack."
            ),
            "status": get_system_status(),
        }

    # One instance at a time (AGENTS.md): two stacks on one database sum their
    # independent inventories into silently invalid data. The page disables the
    # button while RUNNING, but a double click in the poll gap, a reload, or a
    # direct POST all bypass button state -- and live_procs.json only remembers
    # the newest PIDs, so stop_bot could never reach the first pair.
    # Interprocess lock prevents concurrent start_bot calls from racing.
    lock_file = runtime_file(".bot_start.lock", root=LIVE_ROOT)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    # Acquire exclusive lock by atomic file creation.
    lock_fd = None
    try:
        lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(lock_fd, f"{os.getpid()}\n".encode())
    except FileExistsError:
        # Another start_bot call holds the lock; check if it's stale.
        try:
            if not lock_file.exists():
                # Lock file disappeared between FileExistsError and this check; retry acquisition
                try:
                    lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.write(lock_fd, f"{os.getpid()}\n".encode())
                except Exception:
                    return {
                        "ok": False,
                        "message": "Failed to acquire startup lock after retry; another start may be running.",
                        "status": get_system_status(),
                    }
            else:
                lock_age = time.time() - lock_file.stat().st_mtime
                if lock_age > 30:  # Stale lock from crashed process
                    lock_file.unlink()
                    try:
                        lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        os.write(lock_fd, f"{os.getpid()}\n".encode())
                    except FileExistsError:
                        # Raced with another process; retry from the top
                        return {
                            "ok": False,
                            "message": "Failed to acquire startup lock after removing stale lock; another start won the race.",
                            "status": get_system_status(),
                        }
                else:
                    return {
                        "ok": False,
                        "message": "Another start_bot request is in progress; refusing concurrent start.",
                        "status": get_system_status(),
                    }
        except Exception:
            return {
                "ok": False,
                "message": "Failed to acquire startup lock; another start may be running.",
                "status": get_system_status(),
            }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Failed to acquire startup lock: {e}",
            "status": get_system_status(),
        }

    # Ensure lock_fd is set before proceeding
    if lock_fd is None:
        return {
            "ok": False,
            "message": "Failed to acquire startup lock",
            "status": get_system_status(),
        }

    launched_procs = []
    try:
        # Re-check status now that we hold the lock.
        current = get_system_status()
        # Anything but a confirmed STOPPED refuses. UNKNOWN means the process
        # registry could not be read, and a start on that state is exactly the
        # duplicate-stack case this guard exists to prevent.
        if current["bot_state"] != "STOPPED":
            message = (
                "Bot stack is already running; refusing to start a second instance."
                if current["bot_state"] == "RUNNING"
                else ("Cannot read the process file at "
                      f"{current.get('registry_path')}; refusing to start until it is "
                      "readable, because a second live stack cannot be ruled out.")
            )
            return {"ok": False, "message": message, "status": current}

        procs_file = runtime_file("processes.json", root=LIVE_ROOT)
        procs_file.parent.mkdir(parents=True, exist_ok=True)

        # Derive a stable run_id so fleet/exec/dash share one session id.
        # Without this, each process generates its own UUID at import time
        # and fills/orders are tagged to inconsistent run_ids, which makes
        # the dashboard default run selector show a misleading zeros grid.
        from core_brain.order_registry import get_run_id
        child_env = {**os.environ, "SH_RUN_ID": get_run_id()}

        # Launch Market Filter (filter_loop)
        p_scr = subprocess.Popen(
            [sys.executable, "-m", "scripts.filter_loop"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env,
        )
        launched_procs.append(p_scr)

        # Launch Query Polymarket loop (live_exec poll --interval 0.5). The account sweep
        # follows LIVE_SWEEP_INTERVAL when set; otherwise it runs every tick. Query
        # owns reconcile, the account sweep, and the markout sampler, and it keeps
        # the registry's open orders fresh for the decide loop below.
        sweep_interval = resolve_sweep_interval()
        poll_cmd = [sys.executable, "-m", "core_brain.order_manager", "poll", "--interval", "0.5"]
        if sweep_interval is not None:
            poll_cmd += ["--sweep-interval", str(sweep_interval)]
        p_eng = subprocess.Popen(
            poll_cmd,
            cwd=str(LIVE_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env,
        )
        launched_procs.append(p_eng)

        # Launch the Decide & Execute loop (decide -> submit). It reads open orders from the
        # registry rather than re-reconciling, so it runs with --no-reconcile and
        # --no-sweep: a second reconcile loop would contend on the reconcile lock
        # and double the venue reads poll already makes.
        p_fleet = subprocess.Popen(
            [sys.executable, "-m", "core_brain.trader_loop", "--live",
             "--no-reconcile", "--no-sweep", "--interval", "5"],
            cwd=str(LIVE_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env,
        )
        launched_procs.append(p_fleet)

        # Load existing procs file to preserve starting_account_value if present
        existing_starting_value = None
        if procs_file.exists():
            try:
                existing_data = json.loads(procs_file.read_text(encoding="utf-8"))
                existing_starting_value = existing_data.get("starting_account_value")
            except Exception:
                pass

        saved_procs = {
            "filter": {"pid": p_scr.pid, "started_at": time.time()},
            "query": {"pid": p_eng.pid, "started_at": time.time(),
                      "sweep_interval_sec": sweep_interval},
            "decide": {"pid": p_fleet.pid, "started_at": time.time()},
        }
        # Capture starting capital: a real snapshot of account equity at the
        # moment the bot is toggled ON. The kpi.py report uses
        # _CFG.bankroll_usd (a hardcoded config constant) as its baseline,
        # but the comment at kpi.py:889 explicitly says that's "a paper-run
        # constant that nobody deposited." This snapshot is the real number.
        # It may be None if the venue is unreachable at start time; the
        # dashboard shows a "estimated baseline" label in that case.
        starting_account_value = _capture_starting_capital()
        if starting_account_value is not None:
            saved_procs["starting_account_value"] = starting_account_value
        elif existing_starting_value is not None:
            # Preserve previously captured value if current capture fails
            saved_procs["starting_account_value"] = existing_starting_value
        procs_file.write_text(json.dumps(saved_procs, indent=2), encoding="utf-8")

        return {"ok": True, "message": "Bot stack started", "status": get_system_status()}
    except Exception as e:
        # Cleanup: terminate any children launched before the failure.
        for proc in launched_procs:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return {
            "ok": False,
            "message": f"Failed to start bot stack: {e}",
            "status": get_system_status(),
        }
    finally:
        # Release lock on all exit paths, but only if we actually acquired it.
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except Exception:
                pass
            # Only unlink if we successfully acquired the lock (lock_fd is not None means we own it)
            try:
                lock_file.unlink()
            except Exception:
                pass


def stop_bot() -> dict:
    """Terminate background Filter, Query, and Decide loops.

    Reads through resolve_runtime_file, so a stack recorded in the pre-rename
    run/live_procs.json is still reachable. The loop below walks whatever keys
    the file holds, which covers the old screener/engine/fleet names too.
    """
    import subprocess
    procs_file = resolve_runtime_file("processes.json", root=LIVE_ROOT)
    if procs_file.exists():
        try:
            saved_procs = json.loads(procs_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved_procs = None
        # A registry we cannot read is not an empty one. Treating it as {} kills
        # nothing, deletes the file, and reports "stopped" -- after which the
        # reset path archives the database while the original processes are
        # still writing to it. Keep the file and fail.
        if not isinstance(saved_procs, dict):
            return {
                "ok": False,
                "message": (
                    f"Cannot read the process file at {procs_file}; refusing to "
                    "report the stack stopped. No process was killed and the file "
                    "was left in place. Fix or remove it, then stop again."
                ),
                "status": get_system_status(),
            }

        for name, info in saved_procs.items():
            if not isinstance(info, dict):
                continue
            pid = info.get("pid")
            if pid and _is_pid_alive(pid, info.get("started_at")):
                try:
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                    else:
                        os.kill(int(pid), 15)
                except Exception:
                    pass
        try:
            procs_file.unlink()
        except Exception:
            pass

    return {"ok": True, "message": "Bot stack stopped", "status": get_system_status()}


def set_sweep_interval(raw: str | None) -> dict:
    """Apply and persist the account-sweep cadence.

    `raw` is None or empty to clear (revert to every tick), otherwise a
    positive number of seconds. Persists to the .env live_exec loads and
    updates this process's environment so the status payload reflects it
    immediately. A running engine keeps its launch-time cadence until the
    bot stack is restarted.
    """
    value: float | None = None
    if raw is not None and str(raw).strip() != "":
        try:
            value = float(str(raw).strip())
        except ValueError:
            return {"ok": False, "message": "sweep interval must be a number of seconds"}
        if value <= 0:
            return {"ok": False, "message": "sweep interval must be positive seconds"}

    env_file = _env_file()
    if env_file is None:
        return {"ok": False, "message": "no .env found; sweep interval was not persisted"}

    if not write_sweep_interval_to_env_file(env_file, value):
        return {"ok": False, "message": f"failed to write {env_file}"}

    if value is None:
        os.environ.pop("LIVE_SWEEP_INTERVAL", None)
    else:
        os.environ["LIVE_SWEEP_INTERVAL"] = str(value)

    return {
        "ok": True,
        "message": "sweep: every tick" if value is None else f"sweep interval set to {value:g}s",
        "sweep_interval_sec": value,
        "status": get_system_status(),
    }


def reset_database(custom_path: str | Path | None = None) -> dict:
    """Safely archive the existing orders.db and initialize a fresh, clean database."""
    from core_brain.order_registry import OrderRegistry
    import shutil
    import datetime

    target_db = resolve_db_path(custom_path or _ACTIVE_DB_OVERRIDE)
    archived_name = None

    # Unlinking the registry under a live writer loses every subsequent write:
    # the engine and screener keep their handles on the old inode while the page
    # reads a new empty file, so the run's telemetry splits across two files and
    # the dashboard reads empty for a bot that is still trading.
    bot_state = get_system_status()["bot_state"]
    if bot_state != "STOPPED":
        # UNKNOWN gets the same refusal as RUNNING: an unreadable process file
        # cannot rule out a live writer, and resetting under one loses every
        # write it makes afterwards.
        return {
            "ok": False,
            "message": (
                "Refusing to reset while the bot stack is running. Stop the bot first."
                if bot_state == "RUNNING"
                else "Refusing to reset while the bot stack state is unknown: the "
                     "process file cannot be read, so a running stack cannot be "
                     "ruled out. Fix the process file, then stop the bot."
            ),
            "archived_to": None,
            "db_path": str(target_db),
        }

    # This function archives-then-deletes whatever it is pointed at. Launched
    # with --db against an archived cycle for a post-mortem, an unguarded reset
    # would destroy the very record the operator opened the page to read -- and
    # nest a new archive/ inside the archive directory on the way out.
    if any(part.lower() == "archive" for part in target_db.resolve().parts):
        return {
            "ok": False,
            "message": (
                f"Refusing to reset {target_db.name}: it is an archived run, "
                "opened for reading. Archives are history and are never reset."
            ),
            "archived_to": None,
            "db_path": str(target_db),
        }

    if target_db.exists() and target_db.stat().st_size > 0:
        archive_dir = target_db.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = archive_dir / f"live_{ts_str}.db"
        shutil.copy2(target_db, archive_path)
        archived_name = archive_path.name
        try:
            target_db.unlink()
        except Exception:
            pass
        for extra in (f"{target_db}-wal", f"{target_db}-shm"):
            try:
                Path(extra).unlink(missing_ok=True)
            except Exception:
                pass

    # Initialize fresh database with all tables and schema
    reg = OrderRegistry(target_db)

    return {
        "ok": True,
        "message": f"Created fresh database at {target_db.name}" + (f" (archived previous to {archived_name})" if archived_name else ""),
        "archived_to": archived_name,
        "db_path": str(target_db),
    }


@app.get("/api/system/status")
def api_system_status():
    """Return process states for 3 sub-services."""
    return JSONResponse(get_system_status())


@app.post("/api/system/start")
def api_system_start(request: Request):
    """Start background bot stack."""
    _authorize_control(request)
    return JSONResponse(start_bot())


@app.post("/api/system/stop")
def api_system_stop(request: Request):
    """Stop background bot stack."""
    _authorize_control(request)
    return JSONResponse(stop_bot())


@app.post("/api/system/sweep-interval")
def api_system_set_sweep_interval(request: Request, seconds: str | None = None):
    """Set or clear the account-sweep cadence and persist it to .env."""
    _authorize_control(request)
    return JSONResponse(set_sweep_interval(seconds))


@app.post("/api/system/reset-db")
def api_system_reset_db(request: Request):
    """Archive current database and initialize a fresh clean orders.db."""
    _authorize_control(request)
    return JSONResponse(reset_database())


@app.post("/api/system/venue-sync")
def api_system_venue_sync(request: Request):
    """Trigger a one-time venue reconciliation.
    Reads the account from Polymarket and backfills closes/float_marks.
    Read-only at the venue; no exposure is opened or increased."""
    _authorize_control(request)
    from core_brain.order_manager import venue_sync
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    return JSONResponse(venue_sync(db_path=db_path, quiet=False))


def relaunch_argv() -> list[str]:
    """Build the command that starts a replacement dashboard process.

    The script path must be absolute. `sys.argv[0]` is whatever the operator
    typed, and .claude/launch.json types it relative ("live/dash/live_dash.py");
    replaying that under cwd=LIVE_ROOT would look for live/live/dash/live_dash.py,
    so the replacement would die on startup -- after the current instance has
    already called os._exit(0), leaving no dashboard at all.

    Everything after argv[0] is carried through, so --port and --db survive a
    restart and the page comes back on the same port against the same database.
    """
    return [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]


@app.post("/api/system/restart-dash")
def api_system_restart_dash(request: Request):
    """Restart only the dashboard web server process without touching engine/screener workers."""
    _authorize_control(request)
    import threading
    import subprocess

    def _do_restart():
        time.sleep(0.8)
        # Launch detached replacement dashboard process.
        subprocess.Popen(relaunch_argv(), cwd=str(LIVE_ROOT))
        # Exit current instance to immediately release port 8799. Engine and
        # screener are separate processes and keep running; they are only
        # orphaned, never signalled.
        os._exit(0)

    threading.Thread(target=_do_restart, daemon=True).start()
    return JSONResponse({"ok": True, "message": "Dashboard server restarting..."})


@app.post("/api/system/cancel-all")
def api_system_cancel_all(request: Request):
    """Cancel all open orders on the venue.

    A closing command (pre-approved per AGENTS.md safety rails), but the
    endpoint still requires the same CSRF defence as every other machine-state
    POST: control token + origin check. Without it, a cross-origin form POST
    to 127.0.0.1:8799 can cancel every resting order on a live bot.
    """
    _authorize_control(request)
    from core_brain.order_manager import cancel_all
    try:
        cancel_all(live=True)
        return JSONResponse({"ok": True, "message": "All open orders cancelled on the venue."})
    except SystemExit as e:
        return JSONResponse(
            {"ok": False, "message": f"Venue rejected cancel-all: {e}"},
            status_code=502,
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "message": f"Cancel-all failed: {e}"},
            status_code=500,
        )


@app.post("/api/system/reset")
def api_system_reset(request: Request):
    """Full reset: halt bot, cancel venue orders, wipe DB, snapshot wallet.

    This is the "clean run" button. It does, in order:
    1. Stop the bot stack (screener, engine, fleet, guardrail)
    2. Cancel all open orders on the venue (so nothing is resting)
    3. Archive + wipe orders.db (fresh registry: 0 PnL, 0 fills, 0 closes)
    4. Clear run state files (cycle_events, heartbeats, live_procs)
    5. Snapshot the live Polymarket wallet as starting capital
    6. Venue-sync open positions into the fresh DB (so the dashboard shows
       real positions immediately, not an empty page that fabricates zeros)

    Configuration (.env, config.py) is never touched. The Polymarket account
    is the source of truth: open positions are pulled in, open orders are
    cancelled, and the account value at reset time becomes the starting capital.
    """
    _authorize_control(request)
    import shutil
    from core_brain.order_manager import cancel_all, account_sweep

    steps = []

    # 1. Halt the bot. A stop that could not confirm the stack is down aborts
    #    the whole reset: cancelling orders and archiving the database under a
    #    live writer loses every write it makes afterwards.
    stop_result = stop_bot()
    steps.append(f"bot: {stop_result['message']}")
    if not stop_result.get("ok"):
        return JSONResponse(
            {
                "ok": False,
                "message": stop_result["message"],
                "steps": steps,
                "starting_capital": None,
                "status": stop_result.get("status"),
            },
            status_code=409,
        )

    # 2. Cancel all open orders on the venue (best-effort; if credentials
    #    are missing or the venue is down, the reset still proceeds)
    try:
        cancel_all(live=True)
        steps.append("venue: all open orders cancelled")
    except Exception as e:
        steps.append(f"venue: cancel-all skipped ({e})")

    # 3. Archive + wipe the database
    reset_result = reset_database()
    if not reset_result.get("ok"):
        return JSONResponse(reset_result, status_code=409)
    steps.append(f"db: {reset_result['message']}")

    # 4. Clear runtime state files (ring buffer, heartbeats, processes).
    #    Both the current runtime/ name and the pre-rename run/ name, or
    #    "clean run ready" would leave state the fallback reader still finds.
    for fname in ["cycle_events.jsonl", "live_poll_heartbeat.json",
                  "global_stop_loss_heartbeat.json", "live_orders.json",
                  "processes.json"]:
        for target in (runtime_file(fname, root=LIVE_ROOT),
                       legacy_runtime_file(fname, root=LIVE_ROOT)):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
    steps.append("run: state files cleared")

    # 5. Snapshot the live Polymarket wallet as starting capital.
    #    account_sweep reads collateral + positions + P&L from the venue
    #    and writes an account_mark + float_mark into the fresh DB.
    #    The account_value_usd at this moment becomes the baseline.
    starting_capital = None
    try:
        sweep_result = account_sweep(quiet=True,
                                     db_path=str(resolve_db_path(_ACTIVE_DB_OVERRIDE)))
        if isinstance(sweep_result, dict):
            starting_capital = sweep_result.get("account_value_usd")
            if starting_capital is not None:
                starting_capital = float(starting_capital)
                steps.append(f"wallet: starting capital = ${starting_capital:,.2f}")
            else:
                steps.append("wallet: venue returned null account value")
    except Exception as e:
        steps.append(f"wallet: snapshot failed ({e})")

    # Write starting capital to processes.json so get_starting_capital() can
    # read it on the next poll — even if the bot hasn't been started yet.
    procs_file = runtime_file("processes.json", root=LIVE_ROOT)
    procs_file.parent.mkdir(parents=True, exist_ok=True)
    procs_data = {}
    if starting_capital is not None:
        procs_data["starting_account_value"] = starting_capital
    procs_data["reset_at"] = time.time()
    procs_file.write_text(json.dumps(procs_data), encoding="utf-8")

    return JSONResponse({
        "ok": True,
        "message": "Reset complete. Clean run ready.",
        "steps": steps,
        "starting_capital": starting_capital,
        "status": get_system_status(),
    })


@app.get("/api/kpi")
def get_kpi(run_id: str | None = None):
    """Return live KPI report mirroring strategy/kpi.py with Level 1/2/3 diagnostics."""
    from core_brain.kpi import report as generate_kpi_report
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    try:
        data = generate_kpi_report(db_path=db_path, run_id=run_id)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def compute_scan_state(
    last_event_ts: Optional[float],
    hb_ts: Optional[float],
    now: float,
    active_phases: set[str],
    stall_threshold: float = SCAN_STALL_THRESHOLD_SEC,
) -> tuple[str, Optional[float]]:
    """Classify the fleet as SCANNING, IDLE, or STALLED.

    STALLED  -- the engine heartbeat has not advanced within `stall_threshold`
                (or is absent entirely): a real alarm, not an empty table.
    SCANNING -- heartbeat fresh AND some service did active-phase work
                (scanning/filtering/quoting/settling) in the recent window.
    IDLE     -- heartbeat fresh but no active-phase work in the window.
    """
    age = None
    if hb_ts is not None:
        age = max(0.0, now - hb_ts)
    if hb_ts is None or (age is not None and age > stall_threshold):
        return "STALLED", age
    if active_phases & {"scanning", "filtering", "quoting", "settling"}:
        return "SCANNING", age
    return "IDLE", age


def _parse_event_ts(ts: Any) -> Optional[float]:
    """Parse an ISO-8601 ring timestamp to a Unix timestamp, or None."""
    if not ts:
        return None
    try:
        dt = datetime.datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _last_per_service(events: list[dict]) -> dict[str, tuple[str, Optional[float]]]:
    """Latest (phase, unix ts) per service from ring events."""
    out: dict[str, tuple[str, Optional[float]]] = {}
    for ev in events:
        svc = str(ev.get("service") or "query")
        ts = _parse_event_ts(ev.get("ts"))
        if svc not in out or (
            ts is not None and (out[svc][1] is None or ts > out[svc][1])
        ):
            out[svc] = (str(ev.get("phase") or ""), ts)
    return out


def _read_engine_heartbeat() -> dict[str, Any]:
    """Read live/runtime/live_poll_heartbeat.json, returning {} when absent/invalid."""
    try:
        data = json.loads(resolve_heartbeat_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list) and data and isinstance(data[-1], dict):
        return data[-1]
    return {}


def _read_guardrail_heartbeat() -> dict[str, Any]:
    """Read the guardrail watcher's self-report, {} when absent/invalid."""
    try:
        data = json.loads(resolve_guardrail_heartbeat_path().read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list) and data and isinstance(data[-1], dict):
        return data[-1]
    return {}


def _guardrail_health() -> dict[str, Any]:
    """Watcher liveness (heartbeat) + alert totals (ring), one payload.

    Liveness: the watcher writes global_stop_loss_heartbeat.json every check
    (~5s). A heartbeat older than STALE_THRESHOLD_SEC means the watcher is
    down or dead -- the silent failure this endpoint exists to surface.
    Alerts: counted from `guardrail_alert` ring events, so the total survives
    watcher restarts (the heartbeat itself is per-process).
    """
    hb = _read_guardrail_heartbeat()
    ts = hb.get("ts")
    age_s = None
    if isinstance(ts, str):
        try:
            dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            age_s = (datetime.datetime.now(datetime.timezone.utc) - dt.replace(
                tzinfo=datetime.timezone.utc)).total_seconds()
        except ValueError:
            age_s = None

    ring_alerts = []
    try:
        from core_brain.cycle_stream import read_ring
        for ev in read_ring(resolve_ring_path(), tail=400):
            if ev.get("action") == "guardrail_alert":
                ring_alerts.append(ev)
    except Exception:
        pass
    ring_alerts.sort(key=lambda a: str(a.get("ts") or ""), reverse=True)
    newest = ring_alerts[0] if ring_alerts else {}

    STALE_THRESHOLD_SEC = 30.0
    return {
        "pid": hb.get("pid"),
        "started_at": hb.get("started_at"),
        "last_ts": ts,
        "cycle": hb.get("cycle"),
        "running": bool(hb) and age_s is not None and age_s <= STALE_THRESHOLD_SEC,
        "age_s": age_s,
        "alerts_total": len(ring_alerts),
        "last_alert_ts": newest.get("ts"),
        "last_alert_kind": newest.get("reason"),
    }


def _read_cycle_intent_rows(db_path: Path | str, limit: int = 200) -> list[dict]:
    """Last `limit` cycle_intent rows in read-only mode; [] when unavailable."""
    path = Path(db_path)
    if not path.exists():
        return []
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    con = None
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT ts, cycle, market_slug, top_skip_reason, top_pass_reason, "
            "intent_count, submitted, cancelled FROM cycle_intent "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


@app.get("/api/scan-state")
def get_scan_state():
    """SCANNING / IDLE / STALLED plus per-cycle skip/pass rationale (read-only)."""
    now = time.time()
    events = []
    try:
        from core_brain.cycle_stream import read_ring
        events = read_ring(resolve_ring_path(), tail=400)
    except Exception:
        events = []

    hb = _read_engine_heartbeat()
    hb_ts = (hb.get("ts") or 0) / 1000.0 if hb.get("ts") else None

    window = now - 60.0
    active_phases: set[str] = set()
    last_event_ts: Optional[float] = None
    last_scan_ts: Optional[float] = None
    for ev in events:
        ts = _parse_event_ts(ev.get("ts"))
        if ts is None:
            continue
        if last_event_ts is None or ts > last_event_ts:
            last_event_ts = ts
        if ts >= window:
            active_phases.add(str(ev.get("phase") or ""))
        if str(ev.get("service") or "") in ("filter", "screener") and (
            last_scan_ts is None or ts > last_scan_ts
        ):
            last_scan_ts = ts

    state, hb_age = compute_scan_state(last_event_ts, hb_ts, now, active_phases)

    rows = _read_cycle_intent_rows(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    skip_counts: dict[str, int] = {}
    pass_counts: dict[str, int] = {}
    for r in rows:
        sk = r.get("top_skip_reason")
        pk = r.get("top_pass_reason")
        if sk:
            skip_counts[sk] = skip_counts.get(sk, 0) + 1
        if pk:
            pass_counts[pk] = pass_counts.get(pk, 0) + 1

    return JSONResponse({
        "scan_state": state,
        "seconds_since_heartbeat": round(hb_age, 1) if hb_age is not None else None,
        "seconds_since_scan": (
            round(max(0.0, now - last_scan_ts), 1) if last_scan_ts is not None else None
        ),
        "last_scan_ts": last_scan_ts,
        "services": {
            svc: {"phase": phase, "last_ts": ts}
            for svc, (phase, ts) in _last_per_service(events).items()
        },
        "decisions_logged": len(rows),
        "skip_reasons": sorted(
            [{"reason": k, "count": v} for k, v in skip_counts.items()],
            key=lambda x: -x["count"],
        ),
        "pass_reasons": sorted(
            [{"reason": k, "count": v} for k, v in pass_counts.items()],
            key=lambda x: -x["count"],
        ),
    })


def _ring_file_key(st: Any) -> tuple:
    """Identity that changes when the engine rotates the ring via os.replace.

    st_ino distinguishes the replaced inode on POSIX; on Windows st_ino is 0,
    so fall back to creation time (st_ctime_ns), which os.replace changes.
    """
    if getattr(st, "st_ino", 0):
        return (st.st_dev, st.st_ino)
    return (st.st_dev, st.st_ctime_ns)


def _cycle_stream_sse(
    ring_path: Path,
    tail: int = SSE_REPLAY_LINES,
    poll_sec: float = SSE_POLL_SEC,
) -> Generator[str, None, None]:
    """Yield SSE frames for the cycle-telemetry ring: replay tail, then follow appends.

    The engine rotates the ring past 500 lines by atomically replacing the file.
    Replacement is detected by file identity (inode on POSIX, creation time on
    Windows) rather than size alone, so a replacement larger than the current
    read offset is still seen. On rotation we emit an ``event: rotate`` frame
    and re-sync from the new file's start.
    """
    last_keepalive = time.time()

    def _frame(line: str) -> str:
        return f"data: {line.strip()}\n\n"

    offset = 0
    file_key = None
    if ring_path.exists():
        try:
            with open(ring_path, "r", encoding="utf-8", errors="replace") as fh:
                tail_lines = fh.readlines()[-tail:]
                # Position actually consumed, not a later stat: an append in the
                # read-to-stat gap must not be silently skipped.
                offset = fh.tell()
                file_key = _ring_file_key(os.fstat(fh.fileno()))
            for line in tail_lines:
                if line.strip():
                    yield _frame(line)
        except OSError:
            pass

    while True:
        try:
            if not ring_path.exists():
                time.sleep(poll_sec)
                continue
            st = ring_path.stat()
            key = _ring_file_key(st)
            size = st.st_size
            if file_key is not None and (key != file_key or size < offset):
                offset = 0
                yield "event: rotate\ndata: {}\n\n"
            file_key = key
            if size > offset:
                with open(ring_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    for line in fh:
                        if line.strip():
                            yield _frame(line)
                    offset = fh.tell()
            if time.time() - last_keepalive >= SSE_KEEPALIVE_SEC:
                yield ": keepalive\n\n"
                last_keepalive = time.time()
            time.sleep(poll_sec)
        except OSError:
            time.sleep(poll_sec)


PAIRS_ACTION_PREFIX = "pairs_"


@app.get("/api/pairs-activity")
def pairs_activity():
    """Aggregate U35 auto-pairs activity from the cycle ring.

    Counts every pairs_* action (completed/exited/would_complete/would_exit/
    hold/balanced/error) overall and per latest cycle, plus each pair's most
    recent action with its timestamp. Read-only; the ring is the source.
    """
    from core_brain.cycle_stream import read_ring
    events = read_ring(resolve_ring_path(), tail=400)
    totals: dict[str, int] = {}
    per_cycle: dict[int, dict[str, int]] = {}
    per_pair: dict[str, dict] = {}
    for ev in events:
        a = str(ev.get("action") or "")
        if not a.startswith(PAIRS_ACTION_PREFIX):
            continue
        action = a[len(PAIRS_ACTION_PREFIX):]
        totals[action] = totals.get(action, 0) + 1
        cycle = ev.get("cycle")
        if cycle is not None:
            pc = per_cycle.setdefault(int(cycle), {})
            pc[action] = pc.get(action, 0) + 1
        pid = (ev.get("extra") or {}).get("pair_id")
        if pid:
            per_pair[str(pid)] = {
                "action": action,
                "ts": ev.get("ts"),
                "cycle": ev.get("cycle"),
            }
    last_cycle = max(per_cycle) if per_cycle else None
    return {
        "totals": totals,
        "last_cycle": last_cycle,
        "last_cycle_counts": per_cycle.get(last_cycle, {})
        if last_cycle is not None else {},
        "per_pair": [
            {"pair_id": pid, **info}
            for pid, info in sorted(per_pair.items())
        ],
    }


@app.get("/api/guardrail-alerts")
def guardrail_alerts():
    """Active guardrail violations as a visible banner payload.

    Reads the cycle ring for `guardrail_alert` events (emitted by
    live/scripts/global_stop_loss.py on a repeated pair exit or an over-cap
    pair) and returns them newest-first. The dashboard renders the most
    recent one as a red banner so a violation is visible, not just a log
    line. Read-only; the ring is the source.
    """
    from core_brain.cycle_stream import read_ring
    events = read_ring(resolve_ring_path(), tail=400)
    alerts = []
    for ev in events:
        if ev.get("action") != "guardrail_alert":
            continue
        alerts.append({
            "ts": ev.get("ts"),
            "cycle": ev.get("cycle"),
            "kind": ev.get("reason"),
            "subject": (ev.get("extra") or {}).get("subject"),
            "detail": (ev.get("extra") or {}).get("detail"),
        })
    alerts.sort(key=lambda a: str(a["ts"] or ""), reverse=True)
    return {"alerts": alerts}


@app.get("/api/guardrail-health")
def guardrail_health():
    """Watcher health: running pid, restart time, alert count.

    Reads the watcher's self-report heartbeat (global_stop_loss_heartbeat.json,
    written every check) for liveness and the ring for the cumulative alert
    count, so a dead watcher is visible on the dashboard instead of failing
    silently. Read-only; both files are sources of truth.
    """
    return _guardrail_health()


@app.get("/api/cycle-stream")
def cycle_stream_events():
    """Server-Sent-Events tail of live/runtime/cycle_events.jsonl."""
    return StreamingResponse(
        _cycle_stream_sse(resolve_ring_path()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/parameters")
def get_parameters():
    """Return active strategy settings, trigger thresholds, and action descriptions.

    Consolidates safety limits from core_brain.config:MakerConfig (max_naked_usd,
    min_quote_shares, max_order_usd, max_total_usd) and the sweep interval
    from the dashboard's own config. One config object, not two.
    """
    from core_brain.config import load as load_cfg
    cfg = load_cfg()
    sweep = resolve_sweep_interval()
    params = [
        {
            "name": "max_naked_usd",
            "value": f"${cfg.max_naked_usd:.2f}",
            "trigger": "One leg fills while the opposing leg is unfilled, creating unhedged exposure > ${:.0f}".format(cfg.max_naked_usd),
            "action": "Stops quoting new orders on that market; prepares emergency exit / merge",
        },
        {
            "name": "max_order_usd",
            "value": f"${cfg.max_order_usd:.2f}",
            "trigger": "Order sizing calculation generates a single order > ${:.0f}".format(cfg.max_order_usd),
            "action": "Clamps size to ${:.0f} floor to prevent accidental capital overcommitment".format(cfg.max_order_usd),
        },
        {
            "name": "max_total_usd",
            "value": f"${cfg.max_total_usd:.2f}",
            "trigger": "Sum of all open notional across fleet reaches ${:.0f}".format(cfg.max_total_usd),
            "action": "Refuses all new quotes across all markets until existing orders settle or cancel",
        },
        {
            "name": "min_quote_shares",
            "value": f"{cfg.min_quote_shares} shares",
            "trigger": "Calculated order size falls below Polymarket venue minimum",
            "action": "Refuses single-sided quote or scales up to {} shares if budget permits".format(cfg.min_quote_shares),
        },
        {
            "name": "sweep_interval",
            "value": f"{sweep:.0f}s" if sweep is not None else "every tick",
            "trigger": "{} elapsed since last wallet balance query".format(f"{sweep:.0f}s" if sweep is not None else "every poll cycle"),
            "action": "Fetches fresh on-chain USDC balance and updates float marks",
        },
    ]
    return JSONResponse({"parameters": params})


@app.get("/api/active-markets")
def get_active_markets():
    """Return graduated and currently quoted markets with order depth and PnL.

    Splits /api/state's market data into active-only (not resolved) for
    the Market Inspection table's ACTIVE MARKETS view.
    """
    from core_brain.registry_state import summarize_state
    state = summarize_state(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    # Filter to markets with active orders or fills
    active = [p for p in state.get("pairs", []) if p.get("status") in ("RESTING", "NAKED", "BALANCED")]
    return JSONResponse({"markets": active})


@app.get("/api/closed-markets")
def get_closed_markets():
    """Return historical closed/settled markets and booked PnL.

    Reads from the KPI report's by_market dict, filtering to markets that
    have closes (realized PnL booked).
    """
    from core_brain.kpi import report as generate_kpi_report
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    try:
        data = generate_kpi_report(db_path=db_path)
        closed = [
            {**m, "realized_pnl": m.get("realized_pnl", 0.0)}
            for m in data.get("by_market", {}).values()
            if m.get("settlements")
        ]
        return JSONResponse({"markets": closed})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# PAGE_HTML: backward-compat shim for tests that reference the constant.
# The actual HTML now lives in dash/static/index.html. Tests that assert on
# specific HTML strings should read from the static file directly.
_PAGE_HTML_FILE = Path(__file__).resolve().parent / "static" / "index.html"

def _load_page_html() -> str:
    """Read the static HTML file, or return empty string if missing."""
    try:
        return _PAGE_HTML_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""

# Kept as a module-level constant for backward compat with tests that import
# PAGE_HTML. Reads the file once at import time; index() reads fresh per
# request so file edits are picked up without restart.
PAGE_HTML = _load_page_html()


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the live operations dashboard.

    The HTML lives in dash/static/index.html. The per-process control token
    is injected at serve time via string replacement — the same pattern as
    the old inline PAGE_HTML, just reading from a file instead of a constant.
    CSS and JS are served cacheable via FastAPI StaticFiles at /static/.
    """
    html = _load_page_html()
    return HTMLResponse(
        html.replace(CONTROL_TOKEN_PLACEHOLDER, CONTROL_TOKEN),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )



def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Spread Hunter Live Execution Monitor")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (default: 8799)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--db", type=str, default=None, help="Path to orders.db SQLite file")
    args = parser.parse_args()

    if args.db:
        set_db_override(args.db)

    global _ACTIVE_PORT
    _ACTIVE_PORT = args.port

    print(f"Starting Live Execution Dashboard on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
