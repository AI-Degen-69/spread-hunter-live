"""tests/test_market_selection_bars.py - the permanent bars the market filter gates on.

`scripts/filter_markets.py` reads these two numbers once at import and prints
them in its `--help` output, so they are what an operator sees before deciding
to run a rank. They are also duplicated: `scoring/config.py` is what the filter
reads and `core_brain/config.py` carries the same fields for the Trader, and
nothing but a test stops the two drifting apart.

The trial knobs are pinned unset on purpose. A trial is a staged loosening an
operator opts into per run; a trial value left in the committed default would
make every rank a trial without saying so.
"""

from __future__ import annotations

from core_brain.config import MakerConfig
from scoring.config import MakerConfig as ScoringConfig

# The bars, lowered from $1,000 and $250,000 on 2026-08-25 by operator
# direction. A change here changes which markets the Trader is allowed to
# quote, so it is a decision, not a tuning detail.
MIN_TOP3_DEPTH_USD = 500.0
MIN_VOLUME_24H_USD = 125_000.0


def test_the_permanent_depth_bar_is_five_hundred_dollars():
    assert ScoringConfig().select_min_top3_depth_usd == MIN_TOP3_DEPTH_USD


def test_the_permanent_volume_bar_is_a_hundred_and_twenty_five_thousand():
    assert ScoringConfig().select_min_volume_24h_usd == MIN_VOLUME_24H_USD


def test_the_two_config_modules_carry_the_same_bars():
    """A filter gating on one bar while the Trader believes another is a
    universe the Trader did not choose."""
    scoring, maker = ScoringConfig(), MakerConfig()

    assert (scoring.select_min_top3_depth_usd
            == maker.select_min_top3_depth_usd == MIN_TOP3_DEPTH_USD)
    assert (scoring.select_min_volume_24h_usd
            == maker.select_min_volume_24h_usd == MIN_VOLUME_24H_USD)


def test_no_trial_bar_ships_set():
    """A committed trial value would make every rank a trial silently."""
    for cfg in (ScoringConfig(), MakerConfig()):
        assert cfg.select_min_top3_depth_usd_trial is None
        assert cfg.select_min_volume_24h_usd_trial is None


def test_the_help_screen_reports_the_bars_it_gates_on():
    """The operator reads `--help` before spending a few hundred requests."""
    import scripts.filter_markets as filter_markets

    assert filter_markets.MIN_TOP3_DEPTH_USD == MIN_TOP3_DEPTH_USD
    assert filter_markets.MIN_VOLUME_24H == MIN_VOLUME_24H_USD
