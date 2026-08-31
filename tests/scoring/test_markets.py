"""Tests for scoring.markets timestamp parsing."""
from __future__ import annotations

from datetime import datetime, timezone

from scoring.markets import _iso_to_unix


def test_iso_to_unix_naive_defaults_to_utc():
    # Arrange
    raw_naive = "2026-08-30T12:34:56"
    raw_offset = "2026-08-30T12:34:56+00:00"
    raw_z = "2026-08-30T12:34:56Z"
    expected_epoch = 1788093296.0

    # Act
    ts_naive = _iso_to_unix(raw_naive)
    ts_offset = _iso_to_unix(raw_offset)
    ts_z = _iso_to_unix(raw_z)

    # Assert
    assert ts_naive == expected_epoch
    assert ts_offset == expected_epoch
    assert ts_z == expected_epoch
    assert ts_naive == ts_offset == ts_z


def test_iso_to_unix_epoch_zero():
    # Arrange
    raw_epoch_zero = "1970-01-01T00:00:00"
    raw_epoch_zero_z = "1970-01-01T00:00:00Z"
    expected_epoch = 0.0

    # Act
    ts_naive = _iso_to_unix(raw_epoch_zero)
    ts_z = _iso_to_unix(raw_epoch_zero_z)

    # Assert
    assert ts_naive == expected_epoch
    assert ts_z == expected_epoch
