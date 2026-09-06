"""A losing number has to look like a loss.

Every card that carried a signed figure picked its colour from "do we have any
samples yet" -- `n > 0 ? 'positive' : ''` -- so once a single close existed the
whole panel went green. On 2026-09-06 the live board showed an expectancy of
-$0.34, a mean return of -13.85%, a Sharpe of -0.63 and a profit factor of
0.13x, all in the green of a win, beside a hero pill whose chevron pointed up
at a $2.40 loss. The run banner said "net loss" in green too, because the CSS
had a rule for `profit` and none for `loss`.

The amounts read `$-2.40`, which is the minus in the wrong place: the sign
belongs in front of the amount, not inside the currency.

Journeys under test:
1. As the Owner, a negative figure is red wherever it appears.
2. As the Owner, the arrow beside a loss points down.
3. As the Owner, a loss reads `-$2.40`.
4. As the Owner, the Monte Carlo end value is inside the card, not clipped.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core_brain.kpi import _signed_usd

HARNESS = Path(__file__).resolve().parent / "js" / "sign_colours_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _render(realized: float = -2.40, *, expectancy: float = -0.343,
            sharpe: float = -0.63, profit_factor: float = 0.13,
            mc_end: float | None = None) -> dict:
    stats = {
        "trade_analytics": {
            "n_closes": 7, "expectancy_usd": expectancy,
            "mean_return_pct": -13.85, "sharpe_ratio": sharpe,
            "sortino_ratio": -0.53, "profit_factor": profit_factor,
            "payoff_ratio": 0.0, "win_rate": 0.286,
            "var_95_usd": 0.0, "cvar_95_usd": 0.0,
            "kelly_fraction": 0.0, "half_kelly": 0.0,
        },
    }
    payload = {
        "kpi": {
            "portfolio": {"starting_capital": 79.78, "realized_pnl": realized,
                          "total_value": 81.28, "cash_usd": 81.28},
            "statistical_analytics": stats,
        },
        "status": {},
        "statistical_analytics": stats,
    }
    if mc_end is not None:
        stats["monte_carlo"] = {
            "steps": [
                {"cycle": 0, "p01": 100, "p10": 100, "p50": 100,
                 "p90": 100, "p99": 100},
                {"cycle": 100, "p01": mc_end - 20, "p10": mc_end - 10,
                 "p50": mc_end, "p90": mc_end + 10, "p99": mc_end + 20},
            ],
            "paths": 1000, "prob_positive_return": 0.0,
            "worst_case_drawdown_pct": -56.05,
        }
        payload["cycles"] = 100
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)


@requires_node
def test_a_loss_is_red_everywhere_it_is_shown():
    # Arrange — the figures the live board was showing in green.
    rendered = _render()

    # Assert
    assert "negative" in rendered["pnl_pill_class"]
    assert "negative" in rendered["spread_class"]
    assert rendered["quant_expectancy"] == "negative"
    assert rendered["quant_sharpe"] == "negative"


@requires_node
def test_a_profit_factor_below_one_is_not_a_win():
    # Arrange — 0.13x means the losses are eight times the wins. It is never
    # negative, so a sign test alone would keep painting it green.
    assert _render(profit_factor=0.13)["quant_profit_factor"] == "negative"
    assert _render(profit_factor=1.8)["quant_profit_factor"] == "positive"


@requires_node
def test_a_win_rate_is_not_coloured_as_a_verdict():
    # Arrange — 28.6% is a rate, not a gain. Green claimed it was good; the
    # break-even rate this strategy needs is far higher.
    assert _render()["quant_win_rate"] == ""


@requires_node
def test_the_arrow_beside_a_loss_points_down():
    # Arrange — the chevron was fixed markup, so it pointed up at every number.
    down = _render(realized=-2.40)["pnl_arrow"]
    up = _render(realized=+2.40)["pnl_arrow"]

    # Assert — in this 24x24 chevron the middle point is the tip: y=9 above the
    # ends (up), y=15 below them (down).
    assert down == "18 9 12 15 6 9"
    assert up == "18 15 12 9 6 15"


@requires_node
def test_the_minus_goes_in_front_of_the_dollar():
    # Arrange
    rendered = _render(realized=-2.40)

    # Assert — `$-2.40` reads as an odd currency; `-$2.40` reads as a loss.
    assert rendered["pnl_text"] == "-$2.40"
    assert rendered["spread_text"] == "-$2.40"
    assert _render(realized=2.40)["pnl_text"] == "+$2.40"


@requires_node
def test_a_gain_still_reads_as_a_gain():
    rendered = _render(realized=2.40, expectancy=0.34, sharpe=1.2)
    assert "positive" in rendered["pnl_pill_class"]
    assert "positive" in rendered["spread_class"]
    assert rendered["quant_expectancy"] == "positive"
    assert rendered["quant_sharpe"] == "positive"


@requires_node
def test_the_monte_carlo_end_value_stays_inside_the_card():
    # Arrange — the badge sat at `w - padR + 2` anchored at its start, so it
    # ran off the right edge of the viewBox and the card clipped it.
    rendered = _render(mc_end=57.93)

    # Assert — inside the plot, right-anchored, and signed from the number
    # rather than with a hardcoded `+` in front of a negative.
    assert rendered["mc_end_anchor"] is True
    assert rendered["mc_end_x"] <= 480.0
    assert rendered["mc_end_label"] == "-$42.07"
    assert "Median Return: -$42.07" in rendered["mc_footer"]


@requires_node
def test_a_monte_carlo_gain_keeps_its_plus():
    rendered = _render(mc_end=142.07)
    assert rendered["mc_end_label"] == "+$42.07"


def test_the_run_verdict_puts_the_minus_in_front_of_the_dollar():
    """The banner read `-$2.40 net loss · $-2.40 realized` -- both spellings of
    the same number in one line, and one of them wrong."""
    assert _signed_usd(-2.40) == "-$2.40"
    assert _signed_usd(2.40) == "+$2.40"
    assert _signed_usd(0.0) == "+$0.00"
