"""No grid on the dashboard may be wider than the screen showing it.

A CSS grid track whose minimum is a fixed length cannot shrink below it, so on
a phone the whole page grows a horizontal scrollbar and the operator has to
drag sideways to read a number. #170 fixed one instance of this on the Tier 1
row; measuring the page at a 320px viewport found the same defect one
container up, in the chart grid the Tier 1 row sits above:

    #analytics-charts-matrix   offsetWidth 296px, computed track 320.77px

The track overshoots because grid items default to `min-width: auto`, so a
chart's own minimum content width props the column open past its container.
`minmax(0, 1fr)` lets the track shrink; `minmax(min(<len>, 100%), 1fr)` does
the same for an `auto-fit` grid that wants a preferred minimum.

Journeys under test:
1. As the Owner, on a narrow screen no panel forces the page sideways.
2. As the Owner, the chart decks shrink with the viewport rather than clipping.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STYLES = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "styles.css"


@pytest.fixture(scope="module")
def styles_css() -> str:
    return STYLES.read_text(encoding="utf-8")


def _declaration(css: str, selector: str) -> str:
    """The `grid-template-columns` value declared for one selector."""
    match = re.search(
        rf'(?<![\w.-]){re.escape(selector)}\s*(?:,[^{{]*)?{{[^}}]*?grid-template-columns\s*:\s*([^;}}]+)',
        css,
    )
    assert match, f"{selector} declares no grid-template-columns"
    return match.group(1).strip()


# Grids whose items are wide content -- charts, tables, cards with their own
# minimum widths -- and which therefore need a track that can shrink.
SHRINKABLE_GRIDS = (
    ".analytics-chart-grid",
    ".tier1-decision-row",
    ".card-grid",
)


@pytest.mark.parametrize("selector", SHRINKABLE_GRIDS)
def test_no_grid_track_has_a_floor_it_cannot_go_below(styles_css, selector):
    # Arrange
    value = _declaration(styles_css, selector)

    # Assert -- either the track floor is 0, or it is wrapped in min(..., 100%)
    # so the container width wins on a screen narrower than the preference.
    fixed_minima = re.findall(r"minmax\(\s*(\d+)px", value)
    assert not fixed_minima, (
        f"{selector} declares minmax({fixed_minima[0]}px, ...): the track cannot "
        f"shrink below {fixed_minima[0]}px and overflows a narrower viewport"
    )


def test_the_chart_grid_stays_shrinkable_when_it_collapses_to_one_column(styles_css):
    # Arrange -- the max-width:900px rule restacks the chart grid. A bare `1fr`
    # there still resolves against the items' own min-content, which is how a
    # 296px container ended up with a 320.77px track.
    match = re.search(
        r"@media\s*\(max-width:\s*900px\)\s*{[^}]*\.analytics-chart-grid\s*{([^}]*)}",
        styles_css,
    )
    assert match, "no max-width:900px rule for .analytics-chart-grid"

    # Assert
    assert "minmax(0" in match.group(1).replace(" ", "").replace("minmax(0,", "minmax(0,"), \
        "the single-column track must be minmax(0, 1fr), not a bare 1fr"


TAPE_PAGE = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "tape.html"

# The tape page carries its own inline stylesheet rather than styles.css, so
# the fix above does not reach it. Its two grids hold the same wide content --
# tile decks and tables -- and would grow the same sideways scrollbar.
TAPE_GRIDS = (".grid", ".split")


@pytest.fixture(scope="module")
def tape_css() -> str:
    return TAPE_PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("selector", TAPE_GRIDS)
def test_the_tape_page_grids_shrink_with_the_screen(tape_css, selector):
    # Arrange
    value = _declaration(tape_css, selector)

    # Assert
    fixed_minima = re.findall(r"minmax\(\s*(\d+)px", value)
    assert not fixed_minima, (
        f"tape.html {selector} declares minmax({fixed_minima[0]}px, ...): the "
        f"track cannot shrink below {fixed_minima[0]}px and overflows a "
        f"narrower viewport"
    )


def test_the_tape_page_grid_items_do_not_prop_their_tracks_open(tape_css):
    # Arrange -- a grid item defaults to `min-width: auto`, so a table wide
    # enough to need its own scroll container pushes the track past the page.
    match = re.search(r"\.split\s*>\s*\*,\s*\.grid\s*>\s*\*\s*{([^}]*)}", tape_css)
    assert match, "tape.html declares no min-width reset for its grid items"

    # Assert
    assert re.search(r"min-width\s*:\s*0", match.group(1)), \
        "tape.html grid items must set min-width: 0"


def test_the_chart_cards_may_shrink_below_their_content(styles_css):
    # Arrange -- belt and braces on the item side: the cards the chart grid
    # actually holds carry `stats-chart-card`, and only `analytics-chart-card`
    # had the `min-width: 0` that keeps an item from propping its track open.
    match = re.search(r"\.stats-chart-card\s*{([^}]*)}", styles_css)
    assert match, ".stats-chart-card has no rule of its own"

    # Assert
    assert re.search(r"min-width\s*:\s*0", match.group(1)), \
        ".stats-chart-card must set min-width: 0 or it props its grid track open"


def test_the_tape_page_cards_do_not_prop_the_page_open(tape_css):
    # Arrange -- `.wrap` is a grid as well, and the charts inside the cards
    # declare a min-width so their axes stay readable. Without the same reset a
    # card grows to the chart's width and the whole page scrolls sideways;
    # measured at a 585px viewport the cards rendered 658px wide.
    match = re.search(r"\.wrap\s*>\s*\*[^{]*{([^}]*)}", tape_css)
    assert match, "tape.html declares no min-width reset for its cards"

    # Assert
    assert re.search(r"min-width\s*:\s*0", match.group(1)),         "tape.html cards must set min-width: 0"
