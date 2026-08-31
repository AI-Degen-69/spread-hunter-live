"""The sidebar-pages frame prototype (#95).

The dashboard's three horizontal tabs stopped scaling: live ops, analytics,
screener and the strategy explainer are four concerns crammed into three
panels plus a separate URL. This is the frame for a sidebar-driven layout —
the rail, five named pages, and empty containers waiting for their content to
move in. No data is wired, and no render logic moved out of `app.js`.

It is served at `/prototype` rather than replacing `/`: this dashboard is the
control surface for a loop that places real orders, and an empty scaffold
standing where the live page used to be would take that surface away while the
layout is still being judged. Swapping it in is a one-line change to `index()`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"
HARNESS = Path(__file__).resolve().parent / "js" / "prototype_nav_harness.cjs"

PAGES = ("home", "data-markets", "strategy", "trades", "reports")

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _nav(clicks: list[str] | None = None, stored: str | None = None,
         toggle_nav: bool = False) -> dict:
    payload = {"clicks": clicks or [], "stored": stored, "toggleNav": toggle_nav}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def test_the_frame_has_one_container_per_page():
    # Arrange / Act
    html = (_STATIC / "prototype.html").read_text(encoding="utf-8")

    # Assert
    for page in PAGES:
        assert f'id="page-{page}"' in html
        assert f'data-page="{page}"' in html


def test_the_pages_start_empty():
    # Arrange — the deliverable is the frame; wiring data is a later change.
    html = (_STATIC / "prototype.html").read_text(encoding="utf-8")

    # Act / Assert — placeholders, and no fetch of any kind.
    assert html.count('class="proto-placeholder"') >= len(PAGES)
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")
    assert "fetch(" not in js
    assert "/api/" not in js


@requires_node
def test_exactly_one_page_is_visible_at_a_time():
    # Arrange / Act
    rendered = _nav(clicks=["strategy"])

    # Assert
    assert rendered["visible"] == ["strategy"]
    assert rendered["current"] == ["strategy"]


@requires_node
def test_home_is_the_page_a_first_visit_lands_on():
    # Arrange / Act
    rendered = _nav()

    # Assert
    assert rendered["visible"] == ["home"]


@requires_node
def test_the_chosen_page_survives_a_reload():
    # Arrange — the previous visit ended on Reports.
    rendered = _nav(stored="reports")

    # Act / Assert
    assert rendered["visible"] == ["reports"]
    assert rendered["current"] == ["reports"]


@requires_node
def test_an_unknown_stored_page_falls_back_to_home():
    # Arrange — stored state is data, not a command: a page name that no
    # longer exists must not hide every panel.
    rendered = _nav(stored="page-that-was-renamed")

    # Act / Assert
    assert rendered["visible"] == ["home"]
    assert rendered["stored"] == "home"


@requires_node
def test_choosing_a_page_closes_the_narrow_screen_drawer():
    # Arrange — on a narrow window the rail sits over the page, so a chosen
    # page that leaves it open hides the thing just selected.
    opened = _nav(toggle_nav=True)
    assert opened["nav_open"] is True
    assert opened["toggle_expanded"] == "true"

    # Act
    rendered = _nav(clicks=["trades"], toggle_nav=False)

    # Assert
    assert rendered["nav_open"] is False


def test_the_drawer_has_a_narrow_screen_rule():
    # Arrange / Act
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Assert — the rail becomes a drawer rather than squeezing the page.
    assert "@media (max-width: 900px)" in css
    assert ".proto-hamburger" in css
    assert ".proto-body.proto-nav-open .proto-sidebar" in css


def test_the_frame_inherits_the_dashboard_theme():
    # Arrange — a scaffold with its own colours drifts from the live page the
    # moment either changes.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")
    html = (_STATIC / "prototype.html").read_text(encoding="utf-8")

    # Act / Assert
    assert "/static/styles.css" in html
    assert "var(--bg-card)" in css
    assert "var(--border-subtle)" in css


def test_the_live_dashboard_is_still_served_at_the_root():
    # Arrange — the prototype must not displace the control surface.
    server = (Path(__file__).resolve().parent.parent / "dashboard" / "server.py"
              ).read_text(encoding="utf-8")

    # Act / Assert
    assert '@app.get("/", response_class=HTMLResponse)' in server
    assert '@app.get("/prototype", response_class=HTMLResponse)' in server


def test_the_prototype_is_reachable_from_the_live_page():
    # Arrange / Act
    index = (_STATIC / "index.html").read_text(encoding="utf-8")

    # Assert
    assert 'href="/prototype"' in index
