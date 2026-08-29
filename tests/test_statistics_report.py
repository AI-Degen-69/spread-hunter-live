from __future__ import annotations

from pathlib import Path

import pytest

from core_brain.order_registry import CloseRecord, OrderRegistry
from core_brain.statistics_report import write_statistics_report


def test_reporter_api_exists():
    assert callable(write_statistics_report)


def test_mode_validation_rejects_invalid(tmp_path: Path):
    with pytest.raises(ValueError):
        write_statistics_report(tmp_path / "registry.db", "run-1", "paper")


def test_reporter_does_not_call_run_shadow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("run_shadow must not be called")

    import core_brain.shadow_run

    monkeypatch.setattr(core_brain.shadow_run, "run_shadow", fail_if_called)
    result = write_statistics_report(tmp_path / "registry.db", "run-1", "shadow", tmp_path)
    assert called is False
    assert result["mode"] == "shadow"


def test_shadow_vs_live_disclaimer(tmp_path: Path):
    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    reg.log_close(CloseRecord(
        ts=1.0,
        condition_id="condition",
        market_slug="market",
        method="shadow_merge",
        shares=1.0,
        cost_basis=0.95,
        proceeds=1.0,
        realized_pnl=0.05,
        run_id="run-1",
    ))

    shadow = write_statistics_report(db, "run-1", "shadow", tmp_path / "shadow-out")
    live = write_statistics_report(db, "run-1", "live", tmp_path / "live-out")

    shadow_text = Path(shadow["report_path"]).read_text(encoding="utf-8")
    live_text = Path(live["report_path"]).read_text(encoding="utf-8")
    assert "rehearsal, not results" in shadow_text
    assert "observational, read-only, live caveats" in live_text
    assert Path(shadow["report_path"]).name.endswith("_shadow_run-1_statistics_report.md")
    assert Path(live["report_path"]).name.endswith("_live_run-1_statistics_report.md")
