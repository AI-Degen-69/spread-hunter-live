"""tests/test_pipeline_snapshot_gates.py - every gate bar reaches the snapshot.

The dashboard's kanban hero blocks state the contract each stage screened
against. They read those numbers out of runtime/pipeline.json, so the snapshot
has to carry every bar -- not just depth and volume. A bar missing here shows
up on the operator's screen as a stale, confident number.
"""

from __future__ import annotations

import json

import scripts.filter_markets as fm


def test_snapshot_carries_every_gate_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "RUN", tmp_path)

    fm._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census="", gates="", attempted=0, rejected=0,
        depth_gate_usd=1000.0, volume_gate_usd=250000.0,
    )

    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))

    assert snap["depth_gate_usd"] == 1000.0
    assert snap["volume_gate_usd"] == 250000.0
    assert snap["spread_gate"] == fm.MAX_BOOK_SPREAD
    assert snap["horizon_gate_days"] == fm.MAX_DAYS_TO_RESOLVE
    assert snap["min_income_usd_day"] == fm.MIN_PAYOUT * fm.FLOOR_MULTIPLE
    assert snap["max_pair_cost"] == getattr(fm._CFG, "max_pair_cost", 0.995)
