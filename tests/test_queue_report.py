"""The queue report's arithmetic, which was wrong in the direction that matters.

The first version aggregated per MARKET: it summed every mark's movement and
divided by the wall-clock span between the first and last retained mark. Two
compounding errors, both inflating the rate:

  1. Marks whose `cancel_decay` is NULL were dropped, but the first retained
     delta still covers the interval back to the dropped one. Movement spanned
     N intervals while the divisor covered N-1.
  2. `traded` summed deltas from every level interleaved, while the queue it
     was divided into was a single level's median. A combined-level rate over a
     per-level queue is not a ratio of anything.

Both push the same way: rates too high, clear times too short, markets looking
more joinable than they are. For a report whose entire job is to say whether a
queue is joinable, that is the one bias that must not exist.

The fix is to work in DELTAS. Each mark carries the interval it actually
covers, deltas are paired only within one level and one run, and every rate is
computed per level before anything is aggregated to a market.
"""
from __future__ import annotations

import math

import pytest

from core_brain.shadow_exec import ensure_shadow_tables, write_queue_mark
from scripts.queue_report import (
    Delta, level_stats, load_deltas, market_stats, verdict,
)


def _mark(db, *, ts, price=0.47, size, traded, decay, run="shadow-a",
          token="tok-up", slug="m"):
    write_queue_mark(db, ts=ts, condition_id="0xabc", market_slug=slug,
                     token_id=token, price=price, level_size=size,
                     traded=traded, cancel_decay=decay, queue_minutes=None,
                     run_id=run)


class TestLoadDeltas:
    def test_each_delta_carries_the_interval_it_actually_covers(self, tmp_path):
        # Marks at 100, 160, 220. The 160 mark's movement covers 100->160 even
        # though 100 itself has no decay of its own. Dividing that movement by
        # the 160->220 span alone is the bug this test exists for.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=100.0, size=1000.0, traded=0.0, decay=None)
        _mark(db, ts=160.0, size=700.0, traded=100.0, decay=200.0)
        _mark(db, ts=220.0, size=500.0, traded=100.0, decay=100.0)

        deltas = load_deltas(db, minutes=None)
        assert [d.dt_min for d in deltas] == [1.0, 1.0]
        assert [d.traded for d in deltas] == [100.0, 100.0]
        assert sum(d.dt_min for d in deltas) == 2.0, (
            "two intervals of movement must divide by two minutes, not one")

    def test_deltas_never_pair_across_runs(self, tmp_path):
        # A reused shadow.db. The new run's first mark has no predecessor of
        # its own and must not borrow the old run's.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=100.0, size=9000.0, traded=0.0, decay=None, run="old")
        _mark(db, ts=5000.0, size=1000.0, traded=0.0, decay=None, run="new")
        _mark(db, ts=5060.0, size=900.0, traded=50.0, decay=50.0, run="new")

        deltas = load_deltas(db, minutes=None)
        assert len(deltas) == 1
        assert deltas[0].run_id == "new"
        assert deltas[0].dt_min == 1.0

    def test_deltas_never_pair_across_levels(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=100.0, price=0.47, size=1000.0, traded=0.0, decay=None)
        _mark(db, ts=110.0, price=0.48, size=20.0, traded=0.0, decay=None)
        _mark(db, ts=160.0, price=0.47, size=900.0, traded=50.0, decay=50.0)

        deltas = load_deltas(db, minutes=None)
        assert len(deltas) == 1
        assert deltas[0].price == 0.47
        assert deltas[0].dt_min == 1.0

    def test_the_minutes_filter_keeps_whole_deltas(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        for i, ts in enumerate((0.0, 60.0, 120.0, 180.0)):
            _mark(db, ts=ts, size=1000.0 - 10 * i, traded=5.0,
                  decay=None if i == 0 else 5.0)
        recent = load_deltas(db, minutes=1.5)
        assert [d.ts for d in recent] == [120.0, 180.0]

    def test_zero_minutes_is_not_treated_as_no_filter(self, tmp_path):
        # `if minutes:` made --minutes 0 report the entire database.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=1000.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=900.0, traded=50.0, decay=50.0)
        assert load_deltas(db, minutes=0.0) == []

    def test_a_negative_window_is_refused(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        with pytest.raises(ValueError, match="minutes"):
            load_deltas(db, minutes=-5.0)

    def test_a_store_without_the_table_reads_as_empty(self, tmp_path):
        import sqlite3

        db = tmp_path / "old.db"
        sqlite3.connect(db).close()
        assert load_deltas(db, minutes=None) == []


def _d(dt_min, traded, decay, price=0.47, slug="m", token="tok-up", size=600.0):
    return Delta(ts=0.0, run_id="r", market_slug=slug, token_id=token,
                 price=price, level_size=size, traded=traded,
                 cancel_decay=decay, dt_min=dt_min)


class TestLevelStats:
    def test_rates_divide_by_the_summed_interval(self):
        # 200 traded over two minutes is 100/min, not 200/min.
        stats = level_stats([_d(1.0, 100.0, 0.0), _d(1.0, 100.0, 0.0)])
        assert len(stats) == 1
        assert stats[0].trade_rate == pytest.approx(100.0)

    def test_clear_time_counts_trades_only_and_then_everything(self):
        # 600 resting; 100/min traded, 100/min cancelled.
        stats = level_stats([_d(1.0, 100.0, 100.0)])
        assert stats[0].clear_trade == pytest.approx(6.0)
        assert stats[0].clear_all == pytest.approx(3.0)

    def test_a_level_that_never_trades_is_unmeasurable_not_instant(self):
        stats = level_stats([_d(1.0, 0.0, 0.0)])
        assert stats[0].clear_trade == math.inf
        assert stats[0].clear_all == math.inf

    def test_negative_decay_does_not_credit_queue_progress(self):
        # Size joining behind us is recorded honestly by the collector, but it
        # is not progress toward the front and must not shorten a clear time.
        stats = level_stats([_d(1.0, 0.0, -500.0)])
        assert stats[0].clear_all == math.inf

    def test_levels_are_reported_separately(self):
        stats = level_stats([_d(1.0, 100.0, 0.0, price=0.47),
                             _d(1.0, 10.0, 0.0, price=0.48)])
        assert {round(s.price, 2) for s in stats} == {0.47, 0.48}


class TestMarketStats:
    def test_a_market_reports_its_median_level(self):
        # Three levels clearing in 2, 6 and 60 minutes -> the market reads 6.
        deltas = [_d(1.0, 300.0, 0.0, price=0.47),
                  _d(1.0, 100.0, 0.0, price=0.48),
                  _d(1.0, 10.0, 0.0, price=0.49)]
        markets = market_stats(level_stats(deltas))
        assert len(markets) == 1
        assert markets[0].clear_all == pytest.approx(6.0)

    def test_the_level_count_is_levels_not_observations(self):
        # The header said `levels` while counting mark rows across time, so a
        # market watched for ten cycles displayed ten levels.
        deltas = [_d(1.0, 100.0, 0.0, price=0.47) for _ in range(10)]
        markets = market_stats(level_stats(deltas))
        assert markets[0].levels == 1
        assert markets[0].observations == 10


class TestVerdict:
    @pytest.mark.parametrize("minutes,expected", [
        (5.0, "JOINABLE"), (59.9, "JOINABLE"), (60.0, "JOINABLE"),
        (61.0, "ambiguous"), (719.0, "ambiguous"),
        (720.0, "NEVER"), (math.inf, "NEVER"),
    ])
    def test_the_bands_are_pinned(self, minutes, expected):
        assert verdict(minutes) == expected
