"""The completable-cost gate: bid + hedge ask, the price a real pair assembles at.

`max_pair_cost` asks what a pair costs if BOTH legs fill as resting bids. On a
binary market UP + DOWN = 1.00, so the legs are anti-correlated (-0.9989 on
shadow run run-2809a7161de1) and that outcome is rare by construction. These
tests cover the other question: what the pair costs when one leg fills as maker
and the other has to be TAKEN at its ask.
"""
import os
from unittest import mock

from core_brain import risk
from core_brain.config import MakerConfig, load


class TestConfigKnobs:
    def test_defaults_gate_at_one_dollar_with_a_three_cent_dead_band(self):
        cfg = MakerConfig()
        assert cfg.max_completable_pair_cost == 1.00
        assert cfg.enforce_completable_pair_cost is True
        assert cfg.requote_dead_band == 0.03

    def test_completable_cap_is_overridable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HUNTER_COMPLETABLE_CAP": "0.98"}):
            assert load().max_completable_pair_cost == 0.98

    def test_dead_band_is_overridable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_DEAD_BAND": "0.0"}):
            assert load().requote_dead_band == 0.0
