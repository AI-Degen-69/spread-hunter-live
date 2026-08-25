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
        # is NOT overridable here. An override of 0.001 must still rest at the
        # touch, not one tick off mid.
        from core_brain.quotes import Inventory, quote_resting_price

        book = {"best_bid": 0.495, "best_ask": 0.505}
        with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": "0.001"}):
            cfg = load()
        assert cfg.reward_offset == 0.001
        price, _, _, _ = quote_resting_price(cfg, Inventory(), "UP", book)
        assert price == pytest.approx(0.495), (
            "the floor, not the override, must set the nearest legal price")

    def test_the_touch_override_rests_at_the_best_bid(self):
        # The whole point of the run this override exists for: mid 0.500,
        # 10-tick spread, 0.005 offset -> rest at 0.495, the best bid.
        from core_brain.quotes import Inventory, quote_resting_price

        book = {"best_bid": 0.495, "best_ask": 0.505}
        with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": "0.005"}):
            cfg = load()
        price, _, _, _ = quote_resting_price(cfg, Inventory(), "UP", book)
        assert price == pytest.approx(0.495)
