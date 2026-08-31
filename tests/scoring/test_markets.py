"""Tests for scoring.markets timestamp parsing."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from scoring.markets import _iso_to_unix

# A POSIX TZ string understood by both glibc and the Windows CRT, so the child
# process below is genuinely non-UTC on every platform CI runs on. Without this
# the assertions pass on a UTC host even with the naive-is-local bug present.
NON_UTC_TZ = "EST5EDT,M3.2.0,M11.1.0"

# 2026-08-30T12:34:56 UTC. Read as US/Eastern instead, it would be 4h larger.
INSTANT = "2026-08-30T12:34:56"
INSTANT_EPOCH = 1788093296.0

_CHILD = """
import json, sys
from scoring.markets import _iso_to_unix
print(json.dumps([_iso_to_unix(s) for s in sys.argv[1:]]))
"""


def _parse_under_non_utc_tz(*raw: str) -> list[float]:
    """Run _iso_to_unix in a child process pinned to a non-UTC timezone."""
    env = {**os.environ, "TZ": NON_UTC_TZ}
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, *raw],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=os.getcwd(),
    )
    return json.loads(result.stdout)


def test_iso_to_unix_treats_naive_as_utc_on_a_non_utc_host():
    # Arrange
    raw_naive = INSTANT
    raw_offset = f"{INSTANT}+00:00"
    raw_z = f"{INSTANT}Z"

    # Act
    ts_naive, ts_offset, ts_z = _parse_under_non_utc_tz(raw_naive, raw_offset, raw_z)

    # Assert
    assert ts_naive == INSTANT_EPOCH
    assert ts_offset == INSTANT_EPOCH
    assert ts_z == INSTANT_EPOCH
    assert ts_naive == ts_offset == ts_z


def test_iso_to_unix_epoch_zero_on_a_non_utc_host():
    # Arrange
    raw_naive = "1970-01-01T00:00:00"
    raw_z = "1970-01-01T00:00:00Z"

    # Act
    ts_naive, ts_z = _parse_under_non_utc_tz(raw_naive, raw_z)

    # Assert
    assert ts_naive == 0.0
    assert ts_z == 0.0


def test_iso_to_unix_matches_the_child_process_on_this_host():
    # Arrange / Act
    in_process = _iso_to_unix(f"{INSTANT}Z")

    # Assert — the offset-bearing form is host-independent, so the in-process
    # call and the pinned-TZ child must agree.
    assert in_process == INSTANT_EPOCH
