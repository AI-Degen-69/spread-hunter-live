"""Does the confidence interval on our average close include a loss?

Issue #90 names this the core GO/NO-GO question, and the board could not
answer it. `trade_analytics` carried a one-sided 90% lower bound on return %
and a two-sided 95% band on the same figure -- two different shapes, on a
percentage, neither of which says in dollars whether the interval we would bet
on still contains a loss.

`mean_pnl_ci` puts both levels on one quantity, the mean realized PnL per
close, and states outright whether the band reaches below zero and by how much.

Journeys under test:
1. As the Owner, I read the 90% and 95% band on the average close, in dollars.
2. As the Owner, I am told explicitly when the band still includes a loss, and
   how deep that loss goes.
3. As the Owner, a sample too small to support a band says so instead of
   showing a fabricated zero.
"""
from __future__ import annotations

import pytest

from core_brain.kpi import compute_trade_analytics


def _closes(pnls: list[float], cost: float = 5.0) -> list[dict]:
    """One `closes` row per PnL figure, each on the same cost basis."""
    return [
        {"ts": float(i), "condition_id": f"0x{i}", "method": "shadow_merge",
         "realized_pnl": pnl, "cost_basis": cost}
        for i, pnl in enumerate(pnls)
    ]


def _ci(pnls: list[float]) -> dict:
    ta = compute_trade_analytics(_closes(pnls), starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    return ta["mean_pnl_ci"]


def _level(ci: dict, level: int) -> dict:
    for row in ci["levels"]:
        if row["level"] == level:
            return row
    raise AssertionError(f"level {level} missing from {[r['level'] for r in ci['levels']]}")


# -- Both bands, on one quantity ---------------------------------------------

def test_both_the_90_and_95_band_are_reported_on_the_mean_close():
    # Arrange -- a spread of closes around a positive mean.
    ci = _ci([0.10, 0.20, -0.05, 0.15, 0.30, 0.05])

    # Assert -- one quantity, two levels, in dollars.
    assert [row["level"] for row in ci["levels"]] == [90, 95]
    assert ci["mean_usd"] == pytest.approx(0.125)
    assert ci["n"] == 6


def test_the_95_band_is_wider_than_the_90_band():
    # Arrange -- the same sample; only the multiplier differs.
    ci = _ci([0.10, 0.20, -0.05, 0.15, 0.30, 0.05])

    # Assert -- a stricter confidence level cannot produce a tighter interval.
    b90, b95 = _level(ci, 90), _level(ci, 95)
    assert b95["lower"] < b90["lower"]
    assert b95["upper"] > b90["upper"]


# -- The question the Owner actually asks ------------------------------------

def test_a_band_that_reaches_below_zero_says_so_and_says_how_deep():
    # Arrange -- a mean that is positive but noisy enough that the band still
    # contains a loss. That is a NO-GO dressed as a win, and it has to read
    # as one.
    ci = _ci([1.00, -0.80, 0.90, -0.70, 0.60, -0.50])

    # Act
    b95 = _level(ci, 95)

    # Assert
    assert b95["lower"] < 0
    assert b95["includes_negative"] is True
    assert b95["negative_depth_usd"] == pytest.approx(abs(b95["lower"]))
    assert ci["verdict"] == "spans_zero"


def test_a_band_clear_of_zero_carries_no_negative_depth():
    # Arrange -- tightly clustered wins: the whole 95% band sits above zero.
    ci = _ci([0.30, 0.32, 0.31, 0.29, 0.30, 0.31, 0.30, 0.32])

    # Act
    b95 = _level(ci, 95)

    # Assert
    assert b95["lower"] > 0
    assert b95["includes_negative"] is False
    assert b95["negative_depth_usd"] is None
    assert ci["verdict"] == "positive"


def test_a_band_entirely_below_zero_reads_as_a_losing_run():
    # Arrange -- consistent losses. The band never touches zero, and calling
    # that "spans zero" would soften a verdict the sample has earned.
    ci = _ci([-0.30, -0.32, -0.31, -0.29, -0.30, -0.31, -0.30, -0.32])

    # Assert
    assert _level(ci, 95)["upper"] < 0
    assert ci["verdict"] == "negative"


# -- What cannot be measured is not invented ---------------------------------

def test_a_single_close_supports_no_band_at_all():
    # Arrange -- one observation has no standard error, so no interval.
    ci = _ci([0.25])

    # Assert -- the mean is real, the band is not.
    assert ci["mean_usd"] == pytest.approx(0.25)
    assert ci["n"] == 1
    assert ci["levels"] == []
    assert ci["verdict"] is None


def test_a_run_with_no_closes_reports_nothing_rather_than_zero():
    # Arrange -- a run that has not closed anything has no mean. Zero would
    # read as "breaks even", which is a measurement it has not made.
    ci = _ci([])

    # Assert
    assert ci["mean_usd"] is None
    assert ci["n"] == 0
    assert ci["levels"] == []
    assert ci["verdict"] is None
