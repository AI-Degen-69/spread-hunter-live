"""DOM and copy contracts for the OLD analytics tab.

Every marker these four tests assert on (`analytics-hist-wrap`, `portfolioLine`,
"Required Observations", "Price Band", "Average Return") was present at
574269c and gone by aaa0098: the analytics tab was rewritten between those two
commits, and again in #94. The tests were not running at the time -- they were
parked outside `tests/` -- so nothing caught the drift.

They are skipped rather than deleted or rewritten. The surface they describe is
being redesigned under #90, and that is where the replacement contracts belong;
rewriting them against a panel scheduled for replacement would be work thrown
away twice. Skipped, they stay visible as a reminder of what the old panel
guaranteed.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="asserts the pre-rewrite analytics tab; replacement contracts belong "
           "to the #90 redesign")

from pathlib import Path


def test_tab2_chip_copy_and_semantic_classes_are_present():
    source = Path("dashboard/static/app.js").read_text(encoding="utf-8")
    assert "Required Observations" in source
    assert "Price Band" in source
    assert "n_required" not in source
    assert "? 'live' : 'warn'" in source
    assert "? 'live' : 'bad'" in source
    assert "winRate != null && winRate > 50 ? 'live' : 'bad'" in source


def test_histogram_ci_rail_contract_is_present():
    source = Path("dashboard/static/app.js").read_text(encoding="utf-8")
    css = Path("dashboard/static/styles.css").read_text(encoding="utf-8")
    assert 'class="analytics-hist-wrap"><div class="analytics-hist">${histogram}</div><div class="analytics-rail">' in source
    assert 'left:26%;width:46%' in source
    assert 'left:20%;width:58%' in source
    assert 'analytics-rail-mean' in source
    assert 'border-radius:10px 10px 0 0' in css
    assert 'border-radius:0 0 10px 10px' in css
    assert 'margin-top:0' in css


def test_portfolio_line_dom_layout_contract_is_present():
    source = Path("dashboard/static/app.js").read_text(encoding="utf-8")
    css = Path("dashboard/static/styles.css").read_text(encoding="utf-8")
    assert 'svg id="portfolioLine"' in source
    assert source.count('<path d="${line}') >= 2
    assert 'Latest observations' in source and 'Unmeasured trend' in source
    assert 'hasMeasuredPortfolio' in source
    assert '$${esc' in source
    assert 'points = [100,101,100.5,102,101.8,103,104,103.6,105,106]' not in source
    assert '.analytics-line' in css and 'min-height:150px' in css
    assert 'grid-template-columns:1fr 1fr' in css
    assert 'analytics-portfolio-card' in source


def test_squares_and_gates_use_human_language():
    source = Path("dashboard/static/app.js").read_text(encoding="utf-8")
    for title in ("Average Profit Per Close", "Average Return", "Win Rate", "Observations"):
        assert title in source
    assert "Gate</th><th>What It Measures</th><th>Needs</th><th>This Run</th><th>Result" in source
    assert "Total Profit Stays Above 1%" in source
    assert "90% Confidence Lower Bound Is" in source
    for threshold in ("Above 1.00%", "More Than Half Wins", "More Than 120 Observations", "At Least 25 Matured Samples"):
        assert threshold in source
    assert "GO" in source and "NO-GO" in source and "Inconclusive" in source
    assert "n_required" not in source
