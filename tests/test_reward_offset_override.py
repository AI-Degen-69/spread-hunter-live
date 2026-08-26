"""`reward_offset` must be settable for one run without editing the default.

The measured reason: over 24 hours across the four markets in the recorded
slate, the book spread is exactly 10 ticks on every two-sided token, so the
best bid sits at `mid - 5 ticks`. 35% of all below-mid volume prints at exactly
that price and almost none prints past it -- on the two MLB markets, 98% and
100% of their below-mid volume was at the touch and nothing beyond it. Resting
at `mid - 0.005` reaches 18.0% of all traded volume; resting at the shipped
`reward_offset = 0.020` reaches 1.4%.

Testing that is one variable on one rehearsal, so it belongs in the
environment, not in the default. `reward_offset` stays 0.020 in code: this
override exists so a shadow run can answer whether tape at the touch turns
into fills without a commit that would also move live behaviour.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from core_brain.config import MakerConfig, load


class TestRewardOffsetOverride:
    def test_the_shipped_default_is_unchanged(self):
        # The override must not become a silent way to move live quoting.
        assert MakerConfig().reward_offset == 0.020
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HUNTER_REWARD_OFFSET", None)
            assert load().reward_offset == 0.020

    def test_the_environment_can_set_it_to_the_touch(self):
        with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": "0.005"}):
            assert load().reward_offset == 0.005

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-0.01", "1.5",
                                     "abc", ""])
    def test_a_non_finite_negative_or_oversized_offset_is_refused(self, bad):
        # An unparseable or non-finite offset must not read as "no override".
        # `float('nan')` would sail through every comparison downstream and
        # quote at a price nothing can be said about.
        if bad == "":
            with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": bad}):
                assert load().reward_offset == 0.020
            return
        with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": bad}):
            with pytest.raises(ValueError, match="HUNTER_REWARD_OFFSET"):
                load()

    def test_both_ends_of_the_bound_are_accepted(self):
        for raw, want in (("0", 0.0), ("1.0", 1.0)):
            with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": raw}):
                assert load().reward_offset == want

    def test_the_floor_still_wins_over_a_smaller_override(self):
        # `quote_resting_price` clamps to `min_reward_offset`, and that floor
        # is NOT overridable here. Band risk is zeroed so the clamp is the only
        # thing under test.
        from dataclasses import replace

        from core_brain.quotes import Inventory, quote_resting_price

        book = {"best_bid": 0.495, "best_ask": 0.505}
        with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": "0.001"}):
            cfg = replace(load(), price_risk_widen=0.0, coinflip_halfwidth=0.0)
        assert cfg.reward_offset == 0.001
        price, _, _, _ = quote_resting_price(cfg, Inventory(), "UP", book)
        assert price == pytest.approx(0.495), (
            "the floor, not the override, must set the nearest legal price")

    def test_the_override_alone_does_NOT_reach_the_touch(self):
        """The trap this test exists to mark.

        `quote_resting_price` does not rest at `mid - reward_offset`. It rests
        at `mid - (reward_offset + skew + band_risk.extra_offset)`, and
        `risk.band_risk_factor` adds `price_risk_widen * (w + price)` -- about
        15 ticks anywhere near 0.50. Setting the offset to the touch and
        expecting to quote at the best bid is therefore wrong, and it is wrong
        silently: the run completes and measures a price nobody chose.

        Reaching the touch needs `price_risk_widen` as well, which is a risk
        control rather than a tuning knob. Pinning the arithmetic here so the
        next person to try this reads it before spending a rehearsal on it.
        """
        from core_brain.quotes import Inventory, quote_resting_price

        book = {"best_bid": 0.495, "best_ask": 0.505}   # mid 0.500
        with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": "0.005"}):
            cfg = load()
        price, provisional, band, _ = quote_resting_price(
            cfg, Inventory(), "UP", book)

        assert provisional == pytest.approx(0.495), "provisional IS mid-offset"
        assert band.extra_offset > 0.010, "band risk dominates a 5-tick offset"
        assert price == pytest.approx(0.480)
        assert price < book["best_bid"], (
            "still well behind the touch despite a touch-sized offset")

    def test_zeroing_band_risk_is_what_reaches_the_touch(self):
        # The scenario a touch-resting rehearsal actually needs. Kept beside
        # the test above so the pair reads as one statement: the offset is
        # necessary and not sufficient.
        from dataclasses import replace

        from core_brain.quotes import Inventory, quote_resting_price

        book = {"best_bid": 0.495, "best_ask": 0.505}
        with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": "0.005"}):
            cfg = replace(load(), price_risk_widen=0.0)
        price, _, _, _ = quote_resting_price(cfg, Inventory(), "UP", book)
        assert price == pytest.approx(book["best_bid"])
