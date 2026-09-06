"""The markout chart has to be able to draw the thing it is named after.

`displacement_bps` is signed: it is `(later_mid - fill_price) / fill_price` in
bps, so a NEGATIVE value is the mid falling away from a leg we just bought --
adverse selection, the panel's whole subject. The chart scaled every bar off a
positive-only maximum and set `height` to the signed result, so an adverse
horizon produced `height="-51.5"`, which SVG rejects: the bar was never drawn,
its label read `+-51.5 bps`, and the browser logged

    Error: <rect> attribute height: A negative value is not valid.

Observed live on 2026-09-06 with two adverse horizons in `data/orders.db`.

Journeys under test:
1. As the Owner, an adverse horizon draws a bar instead of vanishing.
2. As the Owner, the bar hangs below the zero line and reads as a loss.
3. As the Owner, a favourable run looks exactly as it did before.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "markout_chart_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _render(intervals: list[dict]) -> dict:
    payload = {"statistical_analytics": {"markout": {"intervals": intervals}}}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)


def _interval(horizon: str, bps: float, samples: int = 4) -> dict:
    return {"horizon": horizon, "displacement_bps": bps, "samples": samples}


@requires_node
def test_an_adverse_horizon_is_drawn_not_dropped():
    # Arrange — the two values the live dashboard could not draw.
    rendered = _render([_interval("1m", -51.538), _interval("5m", -63.301)])

    # Assert — SVG rejects a negative height outright, so a bar with one is a
    # bar the operator never sees.
    assert rendered["heights"], "the chart drew no bars at all"
    assert all(h >= 0 for h in rendered["heights"]), rendered["heights"]
    assert rendered["labels"] == ["-51.5 bps", "-63.3 bps"]


@requires_node
def test_an_adverse_bar_hangs_below_the_zero_line():
    # Arrange
    rendered = _render([_interval("1m", 20.0), _interval("5m", -40.0)])
    good, bad = rendered["rects"][0], rendered["rects"][1]
    zero = rendered["zeroLineY"]

    # Assert — y grows downward in SVG. A favourable bar starts above the zero
    # line and ends on it; an adverse one starts on it and hangs below.
    assert good["y"] < zero
    assert good["y"] + good["height"] == pytest.approx(zero, abs=0.01)
    assert bad["y"] == pytest.approx(zero, abs=0.01)
    assert bad["height"] > 0

    # ...and it is not painted the colour of a win.
    assert "248, 113, 113" in bad["fill"]
    assert "16, 185, 129" in good["fill"]


@requires_node
def test_a_favourable_run_still_sits_on_the_floor_of_the_plot():
    # Arrange — with nothing adverse the chart was already right, and moving
    # its baseline would be a regression dressed as a fix. `padT + plotH` is
    # 18 + 142: the floor, where it has always been.
    rendered = _render([_interval("1m", 12.0), _interval("5m", 30.0)])

    # Assert
    assert rendered["zeroLineY"] == pytest.approx(160.0, abs=0.01)
    assert rendered["labels"] == ["+12.0 bps", "+30.0 bps"]
    for rect in rendered["rects"]:
        assert rect["height"] > 0
        assert rect["y"] + rect["height"] == pytest.approx(160.0, abs=0.01)


@requires_node
def test_an_all_adverse_run_hangs_from_the_top_of_the_plot():
    # Arrange — the mirror of the case above: nothing favourable, so the zero
    # line is the ceiling and every bar hangs from it.
    rendered = _render([_interval("1m", -20.0), _interval("5m", -45.0)])

    # Assert — `padT` is 18.
    assert rendered["zeroLineY"] == pytest.approx(18.0, abs=0.01)
    for rect in rendered["rects"]:
        assert rect["y"] == pytest.approx(18.0, abs=0.01)
        assert rect["height"] > 0


@requires_node
def test_a_flat_run_is_not_magnified_into_a_curve():
    # Arrange — rounding noise around zero is not a finding. The span has a
    # floor so three near-zero horizons do not render as a dramatic decay.
    rendered = _render([_interval("1m", 0.2), _interval("5m", -0.1),
                        _interval("30m", 0.05)])

    # Assert — every bar is a sliver, not half the panel.
    assert max(r["height"] for r in rendered["rects"]) < 15.0

