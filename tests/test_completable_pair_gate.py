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


class TestCompletablePairBlock:
    def test_blocks_when_bid_plus_hedge_ask_reaches_the_cap(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        why = risk.completable_pair_block(cfg, 0.55, 0.45)
        assert why is not None
        assert "completable pair" in why
        assert "1.0000" in why

    def test_blocks_when_the_pair_would_cost_more_than_a_dollar(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is not None

    def test_allows_a_pair_that_completes_under_the_cap(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.52, 0.45) is None

    def test_has_no_opinion_when_the_hedge_book_has_no_ask(self):
        # book_health already refuses an unreadable book, with its own wording.
        # A second, differently-worded refusal for the same condition would
        # make the operator's reason string ambiguous.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.99, None) is None
        assert risk.completable_pair_block(cfg, 0.99, 0.0) is None

    def test_is_switchable_off(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00,
                          enforce_completable_pair_cost=False)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is None

    def test_a_zero_cap_disables_the_rule(self):
        # The same escape hatch max_naked_usd and max_fleet_naked_usd have.
        cfg = MakerConfig(max_completable_pair_cost=0.0)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is None

    def test_a_tighter_cap_refuses_a_pair_a_dollar_cap_would_allow(self):
        cfg = MakerConfig(max_completable_pair_cost=0.98)
        assert risk.completable_pair_block(cfg, 0.54, 0.45) is not None
