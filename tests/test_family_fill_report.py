"""A quotable moment only pays when the tape clears BOTH levels in time."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.family_fill_report import (
    DEFAULT_HORIZON_MIN,
    _level_state,
    _rate,
    load_rows,
    main,
    qualifies,
    simulate,
    simulate_moment,
    summarise,
)

BASE_TS = 1_700_000_000.0


def _sample(**over):
    """One probe sample: quotable, with a tape that clears both legs fast."""
    row = {
        "ts": BASE_TS, "run_id": "r", "cycle": 1,
        "condition_id": "0xcid", "family": "sports-x",
        "gate_pass": 1, "gate_reason": None,
        "best_bid_up": 0.40, "best_ask_up": 0.42,
        "best_bid_down": 0.58, "best_ask_down": 0.60,
        "spread_up": 0.02, "spread_down": 0.02,
        "touch_pair_cost": 0.98,
        "q_bid_up": 100.0, "q_ask_up": 100.0,
        "q_bid_down": 100.0, "q_ask_down": 100.0,
        "tape_span_min": 1.0,
        "vol_at_bid_up": 1000.0, "vol_at_ask_up": 1000.0,
        "qmin_bid_up": 0.1, "qmin_ask_up": 0.1, "qmin_worst": 0.1,
        "book_ok": 1, "is_bootstrap": 0,
    }
    row.update(over)
    return row


def _forward(count=2, minutes=6.0, **over):
    return [_sample(ts=BASE_TS + minutes * 60.0 * (i + 1), cycle=i + 2, **over)
            for i in range(count)]


# --- the entry condition ------------------------------------------------------


def test_qualifies_needs_queue_and_pair_together():
    assert qualifies(_sample(), 15.0, 0.99)
    assert not qualifies(_sample(qmin_worst=40.0), 15.0, 0.99)
    assert not qualifies(_sample(touch_pair_cost=0.995), 15.0, 0.99)


def test_qualifies_refuses_a_moment_with_no_readable_queue():
    # `queue_minutes_at` returns infinity when nothing traded at our level and
    # the probe stores that as NULL. Missing is a refusal, never a pass.
    assert not qualifies(_sample(qmin_worst=None), 15.0, 0.99)
    assert not qualifies(_sample(q_ask_up=None), 15.0, 0.99)
    assert not qualifies(_sample(best_bid_up=None), 15.0, 0.99)


# --- the fill walk ------------------------------------------------------------


def test_both_legs_fill_when_the_tape_clears_both_queues():
    moment = simulate_moment(_sample(), _forward(), DEFAULT_HORIZON_MIN,
                             15.0, count_adverse=False)
    assert moment.is_pair
    assert not moment.is_single
    assert round(moment.edge_usd, 4) == round(0.02 * (15.0 / 0.98), 4)


def test_a_leg_with_no_tape_at_its_level_never_fills():
    # 1000 shares/min on the bid, nothing on the ask: the UP leg fills, the
    # DOWN leg does not, and that is a single buy -- the failure mode the
    # whole strategy exists to avoid.
    moment = simulate_moment(_sample(), _forward(vol_at_ask_up=0.0),
                             DEFAULT_HORIZON_MIN, 15.0, count_adverse=False)
    assert moment.up_filled and not moment.down_filled
    assert moment.is_single
    assert round(moment.single_cost_usd(), 4) == round(0.02 * (15.0 / 0.98), 4)


def test_a_slow_queue_does_not_fill_inside_the_horizon():
    # 1 share/min against 100 resting: 100+ minutes to clear, horizon is 15.
    slow = _forward(count=3, vol_at_bid_up=1.0, vol_at_ask_up=1.0)
    moment = simulate_moment(_sample(), slow, DEFAULT_HORIZON_MIN, 15.0,
                             count_adverse=False)
    assert not moment.up_filled and not moment.down_filled
    assert not moment.is_pair and not moment.is_single


def test_our_own_size_must_clear_too_not_just_the_queue_ahead():
    # 106 shares of tape against 100 resting clears the queue but not our
    # 15.3 shares behind it. Filling on the queue alone would book a pair we
    # never got.
    forward = _forward(count=1, vol_at_bid_up=106.0, vol_at_ask_up=106.0,
                       tape_span_min=6.0)
    moment = simulate_moment(_sample(), forward, DEFAULT_HORIZON_MIN, 15.0,
                             count_adverse=False)
    assert not moment.is_pair


def test_a_level_the_market_leaves_behind_stops_draining():
    moment = simulate_moment(_sample(), _forward(best_bid_up=0.41),
                             DEFAULT_HORIZON_MIN, 15.0, count_adverse=False)
    assert not moment.up_filled
    assert not moment.adverse


def test_an_adverse_move_is_a_fill_only_under_count_adverse():
    forward = _forward(best_bid_up=0.39, vol_at_bid_up=0.0)
    strict = simulate_moment(_sample(), forward, DEFAULT_HORIZON_MIN, 15.0,
                             count_adverse=False)
    assert strict.adverse and not strict.up_filled
    loose = simulate_moment(_sample(), forward, DEFAULT_HORIZON_MIN, 15.0,
                            count_adverse=True)
    assert loose.up_filled and loose.is_pair


def test_the_down_leg_mirrors_the_up_ask():
    # A bid on DOWN is a resting offer on UP, so the ask RISING is the market
    # leaving us behind and the ask FALLING is the adverse case. Reading the
    # direction the UP way round would invert both.
    assert _level_state(_sample(best_ask_up=0.43), "down", 0.42) == "adverse"
    assert _level_state(_sample(best_ask_up=0.41), "down", 0.42) == "left"
    assert _level_state(_sample(), "down", 0.42) == "intact"


def test_fills_stop_at_the_horizon():
    # Cycles every 6 minutes, but the last one lands after the 15-minute
    # horizon: only the intervals inside the window may drain.
    slow = _forward(count=3, vol_at_bid_up=8.0, vol_at_ask_up=8.0,
                    tape_span_min=1.0)
    inside = simulate_moment(_sample(), slow[:2], DEFAULT_HORIZON_MIN, 15.0,
                             count_adverse=False)
    whole = simulate_moment(_sample(), slow, 60.0, 15.0, count_adverse=False)
    assert not inside.is_pair
    assert whole.is_pair


def test_a_hole_in_the_cycles_ends_the_moment_unobserved():
    # One sample 30 minutes later attests to its own instant, not to the 30
    # minutes nobody watched. Crediting the gap would book pairs from a hole.
    late = _forward(count=1, minutes=30.0)
    moment = simulate_moment(_sample(), late, 60.0, 15.0,
                             count_adverse=False)
    assert not moment.is_pair
    assert simulate_moment(_sample(), late, 60.0, 15.0, count_adverse=False,
                           max_gap_min=45.0).is_pair


def test_rate_is_zero_when_the_window_is_unmeasurable():
    assert _rate(100.0, 0.0) == 0.0
    assert _rate(None, 5.0) == 0.0
    assert _rate(100.0, 5.0) == 20.0


# --- one live quote per market ------------------------------------------------


def test_the_same_standing_opportunity_counts_once_per_cooldown():
    rows = [_sample(ts=BASE_TS + i * 360.0, cycle=i + 1) for i in range(6)]
    once = simulate(rows, 15.0, 0.99, 15.0, 15.0, 15.0, False)
    every = simulate(rows, 15.0, 0.99, 15.0, 0.0, 15.0, False)
    assert len(once) == 2       # 0 and 30 minutes in, not all six cycles
    assert len(every) == 6


# --- the report ---------------------------------------------------------------


def test_summarise_splits_gross_from_the_cost_of_singles():
    rows = ([_sample()] + _forward())
    paired = simulate(rows, 15.0, 0.99, 15.0, 15.0, 15.0, False)
    stats = summarise(paired, rows, span_days=0.5)
    assert len(stats) == 1
    assert stats[0].pairs == 1 and stats[0].singles == 0
    assert stats[0].pairs_per_day == 2.0            # one pair per half day
    assert stats[0].cost_per_day == 0.0
    assert stats[0].net_per_day > 0.0


def test_summarise_reports_the_gate_that_blocks_the_family_today():
    rows = [_sample(gate_pass=0, gate_reason="carries a submarket group label")]
    rows += _forward(gate_pass=0,
                     gate_reason="carries a submarket group label")
    moments = simulate(rows, 15.0, 0.99, 15.0, 15.0, 15.0, False)
    stats = summarise(moments, rows, span_days=1.0)
    assert stats[0].gate == "carries a submarket group label"


def _store(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    columns = ", ".join(rows[0].keys())
    marks = ", ".join("?" for _ in rows[0])
    conn.execute(f"CREATE TABLE probe_samples ({columns})")
    conn.executemany(f"INSERT INTO probe_samples VALUES ({marks})",
                     [tuple(r.values()) for r in rows])
    conn.commit()
    conn.close()


def test_load_rows_windows_on_the_newest_sample(tmp_path):
    path = tmp_path / "probe.db"
    _store(path, [_sample(), _sample(ts=BASE_TS + 7200.0, cycle=2)])
    assert len(load_rows(path, hours=None)) == 2
    assert len(load_rows(path, hours=1.0)) == 1


def test_main_reports_on_a_real_store(tmp_path, capsys):
    path = tmp_path / "probe.db"
    _store(path, [_sample()] + _forward())
    assert main(["--db", str(path)]) == 0
    out = capsys.readouterr().out
    assert "pairs/d" in out and "sports-x" in out
    assert "=== today's gate ===" in out


def test_main_refuses_a_store_that_is_not_there(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "missing.db")]) == 1
    assert "run scripts/family_probe.py first" in capsys.readouterr().out
