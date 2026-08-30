"""The AI Studio dashboard design must be present in dashboard/static.

Every later task (bridge static serving, token injection) depends on these
files and on the control-token placeholder surviving the import.
"""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "dashboard" / "static"


def test_design_files_present():
    for name in ("app.js", "index.html", "styles.css", "strategy_explainer.html"):
        assert (STATIC / name).is_file(), f"missing {name}"


def test_control_token_placeholder_present():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'const CONTROL_TOKEN = "__LIVE_DASH_CONTROL_TOKEN__";' in html


def test_frontend_calls_cycle_stream():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "api/cycle-stream" in app