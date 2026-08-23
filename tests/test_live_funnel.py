"""live/tests/test_live_funnel.py - Level 2 funnel sourced from the screener snapshot.

The live Level 2 funnel must reuse the ranker's own gate names and counts so a
refusal compares 1:1 with the paper-run scan (server/fleet_dash.py). These tests pin
that mapping against a hermetic fixture rather than the repo's live
runtime/pipeline.json.
"""

from __future__ import annotations

import json

from core_brain.kpi import _funnel_from_pipeline


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_funnel_from_pipeline_reuses_screener_gate_names(tmp_path):
    """RAW/FILTERS/FINAL come from pipeline.json with the screener's exact cause labels."""
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {
            "funded": 999, "spread_universe": 16,
            "eligible": 3, "picked": 3, "rejected": 187,
        },
        "rejections": [
            {"cause": "YES: top-3 bid depth", "n": 101,
             "examples": [{"title": "A", "reason": "YES: top-3 bid depth $4 < $1,000"}]},
            {"cause": "volume", "n": 54,
             "examples": [{"title": "B", "reason": "24h volume $50,000 < $250,000"}]},
            {"cause": "horizon", "n": 1, "examples": []},
        ],
    })
    markets = _write(tmp_path / "markets.json", [
        {"cid": "0xabc", "slug": "m1", "title": "Tigers vs Pirates"},
        {"cid": "0xdef", "slug": "m2", "title": "Jodar vs Flavio"},
    ])
    by_mkt = {"0xabc": {"fills_count": 2, "realized_pnl": 0.5}}

    f = _funnel_from_pipeline(by_mkt, pipeline_path=pipeline, markets_path=markets)

    assert f is not None
    assert f["source"] == "screener"
    # RAW = reward pool + gamma liquid pool, matching the paper-run scan's 1015.
    assert f["raw_count"] == 1015
    assert f["final_count"] == 3
    assert [g["cause"] for g in f["filters"]] == ["YES: top-3 bid depth", "volume", "horizon"]
    assert f["filters"][0]["n"] == 101
    assert f["filters"][0]["examples"][0]["title"] == "A"
    # GRADUATED = the ranker's picks, annotated with live fills/PnL.
    assert len(f["graduated"]) == 2
    assert f["graduated"][0]["condition_id"] == "0xabc"
    assert f["graduated"][0]["fills"] == 2
    assert f["graduated"][0]["pnl"] == 0.5
    assert f["graduated"][1]["fills"] == 0
    assert f["counts"]["funded"] == 999
    assert "raw" in f
    assert "final" in f
    assert "picked" in f


def test_funnel_carries_near_miss_counters(tmp_path):
    """would_fund/traps reach the dashboard so the near-miss footer can render."""
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 4, "spread_universe": 0, "eligible": 1},
        "rejections": [
            {"cause": "volume", "n": 9, "would_fund": 3, "traps": 1, "examples": []},
            {"cause": "horizon", "n": 2, "examples": []},
        ],
    })

    f = _funnel_from_pipeline({}, pipeline_path=pipeline,
                              markets_path=tmp_path / "missing.json")

    assert f is not None
    assert f["filters"][0]["would_fund"] == 3
    assert f["filters"][0]["traps"] == 1
    # A bucket the snapshot wrote without counters reads as zero, not missing.
    assert f["filters"][1]["would_fund"] == 0
    assert f["filters"][1]["traps"] == 0


def test_funnel_carries_every_gate_bar(tmp_path):
    """The spread, horizon, payout and pair-cost bars reach the dashboard.

    The kanban hero blocks state the contract the screener gated on. Reading
    them from the snapshot is what stops the panel claiming a stale pair-cost
    cap after the config changes.
    """
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 1, "spread_universe": 0, "eligible": 0},
        "rejections": [],
        "depth_gate_usd": 1000.0,
        "volume_gate_usd": 250000.0,
        "spread_gate": 0.06,
        "horizon_gate_days": 30.0,
        "reward_min_income_usd_day": 1.5,
        "spread_min_income_usd_day": 0.0,
        "max_pair_cost": 0.995,
    })

    f = _funnel_from_pipeline({}, pipeline_path=pipeline,
                              markets_path=tmp_path / "missing.json")

    assert f is not None
    assert f["spread_gate"] == 0.06
    assert f["horizon_gate_days"] == 30.0
    assert f["reward_min_income_usd_day"] == 1.5
    assert f["spread_min_income_usd_day"] == 0.0
    assert f["max_pair_cost"] == 0.995


def test_funnel_from_pipeline_returns_none_when_absent(tmp_path):
    assert _funnel_from_pipeline({}, pipeline_path=tmp_path / "missing.json") is None


def test_funnel_from_pipeline_returns_none_on_malformed(tmp_path):
    bad = tmp_path / "pipeline.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _funnel_from_pipeline({}, pipeline_path=bad) is None


def test_funnel_from_pipeline_defaults_to_repo_run_dir(monkeypatch, tmp_path):
    """With no explicit path the helper reads the repo's runtime/pipeline.json."""
    import core_brain.kpi as kpi_mod

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    _write(runtime_dir / "pipeline.json", {
        "counts": {"funded": 10, "spread_universe": 5, "eligible": 2},
        "rejections": [{"cause": "volume", "n": 1, "examples": []}],
    })
    _write(runtime_dir / "markets.json", [{"cid": "0x1", "slug": "m", "title": "T"}])
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)

    f = _funnel_from_pipeline({})
    assert f is not None
    assert f["raw_count"] == 15
    assert [g["cause"] for g in f["filters"]] == ["volume"]
    assert f["graduated"][0]["condition_id"] == "0x1"
