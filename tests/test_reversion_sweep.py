"""The sweep replays the recorded quote tape instead of waiting for new games.

`core_brain.reversion_watch` answers one question -- fade a 3c jump, hold
fifteen minutes -- and takes half a day to answer it once. The same watch also
writes every book it reads into `quotes`, one row per market per minute, bid
and ask. That tape already contains the answer to the whole grid of questions
the single run was never asked, so this module reads it back.

Journeys under test:
1. As the Owner, FOLLOW is charged the spread on BOTH legs, because scoring it
   as the negation of the fade hands it the spread instead of paying it and
   turns a losing trade into a winner on paper.
2. As the Owner, the raw mid drift is reported next to the round-trip cost,
   because a signal smaller than the two crossings is not a trade.
3. As the Owner, a cell whose drift is real but smaller than the cost reads
   PAID_AWAY rather than PAYS, because the verdict has to name the reason.
4. As the Owner, one move is counted once: overlapping jumps inside a single
   hold are collapsed, or a slow grind is scored as a dozen winners.
5. As the Owner, the same gates the forward test used still apply -- price
   band, spread ceiling, look-back window -- because a number measured over
   different moments cannot be compared with the one already recorded.
6. As the Owner, a cell with too few trades reads THIN rather than reporting a
   mean that is really a sample size.
7. As the Owner, the production registry is refused by name here too, because
   a third entry point onto the same store is a third way to open it.
8. As the Owner, leagues stay apart, because Dota and LoL measured opposite
   signs and one pooled number is true of neither.
9. As the Owner, twelve jumps inside ONE match are one observation and not
   twelve, because dividing the error by the square root of twelve reports a
   certainty the tape never earned.
10. As the Owner, certainty is measured across matches, so a single game that
    ran away cannot carry a cell over the bar on its own.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core_brain.price_tape import SIGNIFICANCE_T
from core_brain.reversion_sweep import (
    DEFAULT_HORIZONS,
    DEFAULT_JUMPS,
    Quote,
    RefusedStore,
    load_tape,
    replay,
    summarise_cell,
    sweep,
)
from core_brain.reversion_watch import open_store


def _tape(*rows: tuple[int, float, float]) -> list[Quote]:
    return [Quote(ts, bid, ask) for ts, bid, ask in rows]


def _store(tmp_path: Path, slug: str, rows: list[Quote]) -> Path:
    path = tmp_path / "sweep.db"
    conn = open_store(path)
    conn.executemany("INSERT OR IGNORE INTO quotes VALUES (?,?,?,?)",
                     [(slug, q.ts, q.bid, q.ask) for q in rows])
    conn.commit()
    conn.close()
    return path


def _rise(start_ts: int = 0) -> list[Quote]:
    """Flat at 0.50, then a 4c step up, then a further 3c continuation."""
    rows = [(start_ts + 60 * i, 0.495, 0.505) for i in range(6)]
    rows.append((start_ts + 360, 0.535, 0.545))          # the jump
    rows.append((start_ts + 660, 0.565, 0.575))          # continued
    return _tape(*rows)


# 1 -------------------------------------------------------------------------
def test_follow_pays_the_spread_on_both_legs_like_the_fade_does():
    outcomes = replay({"lol-a-b": _rise()}, jump=0.03, horizon=300)
    assert len(outcomes) == 1
    done = outcomes[0]
    # Jump: mid 0.500 -> 0.540. Entry book 0.535/0.545, exit book 0.565/0.575.
    # FADE up   = sell 0.535, buy back 0.575  -> -4.0c
    # FOLLOW up = buy  0.545, sell     0.565  -> +2.0c
    assert done.fade_c == pytest.approx(-4.0)
    assert done.follow_c == pytest.approx(2.0)
    # The negation shortcut would have called follow +4.0c: a full spread of
    # free money that nobody could have traded.
    assert done.follow_c != pytest.approx(-done.fade_c)


# 2 -------------------------------------------------------------------------
def test_raw_drift_is_reported_beside_what_the_round_trip_costs():
    done = replay({"lol-a-b": _rise()}, jump=0.03, horizon=300)[0]
    # Mid 0.540 -> 0.570, the move continued by 3c with no spread paid.
    assert done.drift_c == pytest.approx(3.0)
    # Book is 1c wide, so crossing it twice costs 2c of that 3c.
    assert done.spread_c == pytest.approx(1.0)
    assert done.cost_c == pytest.approx(2.0)


def test_drift_is_signed_so_a_continued_fall_reads_positive():
    rows = [(60 * i, 0.495, 0.505) for i in range(6)]
    rows.append((360, 0.455, 0.465))                     # fell 4c
    rows.append((660, 0.425, 0.435))                     # kept falling
    done = replay({"lol-a-b": _tape(*rows)}, jump=0.03, horizon=300)[0]
    assert done.move_c < 0
    assert done.drift_c == pytest.approx(3.0)


# 3 -------------------------------------------------------------------------
def test_a_real_signal_smaller_than_the_crossings_reads_paid_away():
    cell = summarise_cell(_sample(drift=1.0, spread=1.0), jump=0.03,
                          horizon=300)
    assert cell["drift_c"] == pytest.approx(1.0)
    assert cell["cost_c"] == pytest.approx(2.0)
    assert cell["verdict"] == "PAID_AWAY"


def test_a_signal_clear_of_the_crossings_reads_tradable():
    cell = summarise_cell(_sample(drift=6.0, spread=1.0), jump=0.03,
                          horizon=300)
    assert cell["verdict"] == "TRADABLE"


def test_a_drift_that_cannot_be_told_from_noise_reads_no_signal():
    outcomes = [_outcome(drift=jitter, spread=1.0, slug=f"lol-m{index}-x")
                for index, jitter in enumerate((12.0, -11.0) * 20)]
    cell = summarise_cell(outcomes, jump=0.03, horizon=300)
    assert cell["trades"] == 40
    assert cell["verdict"] == "NO_SIGNAL"


def test_a_move_that_comes_back_reads_reverts_not_paid_away():
    cell = summarise_cell(_sample(drift=-3.0, spread=1.0), jump=0.03,
                          horizon=300)
    assert cell["verdict"] == "REVERTS"


# 4 -------------------------------------------------------------------------
def test_overlapping_jumps_inside_one_hold_are_counted_once():
    # A steady grind: every minute is 1c higher, so every read from the sixth
    # on looks like a 5c jump over the previous five minutes.
    rows = [(60 * i, 0.495 + 0.01 * i, 0.505 + 0.01 * i) for i in range(20)]
    outcomes = replay({"lol-a-b": _tape(*rows)}, jump=0.03, horizon=600)
    stamps = [done.ts for done in outcomes]
    assert stamps == sorted(stamps)
    assert all(later - earlier >= 600
               for earlier, later in zip(stamps, stamps[1:])), stamps


# 5 -------------------------------------------------------------------------
def test_a_book_wider_than_the_forward_test_allowed_is_not_traded():
    rows = [(60 * i, 0.49, 0.51) for i in range(6)]      # 2c wide
    rows.append((360, 0.53, 0.55))
    rows.append((660, 0.56, 0.58))
    assert replay({"lol-a-b": _tape(*rows)}, jump=0.03, horizon=300) == []


def test_a_price_outside_the_band_is_not_traded():
    rows = [(60 * i, 0.925, 0.935) for i in range(6)]
    rows.append((360, 0.965, 0.975))
    rows.append((660, 0.985, 0.995))
    assert replay({"lol-a-b": _tape(*rows)}, jump=0.03, horizon=300) == []


def test_a_jump_with_no_quote_a_full_horizon_later_is_dropped():
    rows = [(60 * i, 0.495, 0.505) for i in range(6)]
    rows.append((360, 0.535, 0.545))
    rows.append((420, 0.565, 0.575))                     # only 60s later
    assert replay({"lol-a-b": _tape(*rows)}, jump=0.03, horizon=300) == []


# 6 -------------------------------------------------------------------------
def test_a_cell_with_a_handful_of_trades_reads_thin():
    cell = summarise_cell([_outcome(drift=9.0, spread=1.0) for _ in range(4)],
                          jump=0.03, horizon=300)
    assert cell["trades"] == 4
    assert cell["verdict"] == "THIN"
    assert cell["drift_t"] is None


# 7 -------------------------------------------------------------------------
def test_the_order_registry_is_refused_by_name(tmp_path: Path):
    registry = tmp_path / "orders.db"
    registry.write_bytes(b"")
    with pytest.raises(RefusedStore):
        sweep(registry)


def test_a_store_that_does_not_exist_yet_reports_rather_than_raises(
        tmp_path: Path):
    report = sweep(tmp_path / "absent.db")
    assert report["state"] == "MISSING"
    assert report["cells"] == []


# 8 -------------------------------------------------------------------------
def test_leagues_are_reported_apart(tmp_path: Path):
    path = _store(tmp_path, "lol-a-b", _rise())
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT OR IGNORE INTO quotes VALUES (?,?,?,?)",
        [("cs2-c-d", q.ts, q.bid, q.ask) for q in _rise(start_ts=10_000)])
    conn.commit()
    conn.close()

    tape = load_tape(path)
    assert set(tape) == {"lol-a-b", "cs2-c-d"}
    report = sweep(path, jumps=(0.03,), horizons=(300,))
    assert report["state"] == "READY"
    leagues = {row["league"] for row in report["by_league"]}
    assert leagues == {"lol", "cs2"}


def test_the_default_grid_covers_both_the_short_and_the_long_hold():
    assert 300 in DEFAULT_HORIZONS and 3600 in DEFAULT_HORIZONS
    assert 0.03 in DEFAULT_JUMPS


# helpers -------------------------------------------------------------------
def _sample(*, drift: float, spread: float, size: int = 40):
    """A cell's worth of moves whose mean is exactly `drift`.

    The jitter is deliberate: a sample with no variance at all has no standard
    error either, which is a fixture rather than anything the venue produces.
    """
    return [_outcome(drift=drift + (0.5 if index % 2 else -0.5), spread=spread,
                     slug=f"lol-m{index}-x")
            for index in range(size)]


def _outcome(*, drift: float, spread: float, slug: str = "lol-a-b"):
    from core_brain.reversion_sweep import Outcome
    return Outcome(slug=slug, league=slug.split("-")[0], ts=0, move_c=4.0,
                   spread_c=spread, entry_mid=0.50,
                   fade_c=-drift - 2 * spread, follow_c=drift - 2 * spread,
                   drift_c=drift)


# 9 -------------------------------------------------------------------------
def test_many_moves_inside_one_match_do_not_count_as_many_trades():
    """Twelve reads of one game are one observation, not twelve.

    A single CS2 match produced twelve qualifying 5c jumps on the recorded
    tape. Scoring them as twelve independent samples divides the standard
    error by the square root of twelve and reports a certainty the tape never
    earned. The cell has to count MATCHES.
    """
    outcomes = [_outcome(drift=6.0 + index * 0.1, spread=1.0, slug="cs2-a-b")
                for index in range(12)]
    cell = summarise_cell(outcomes, jump=0.05, horizon=300)
    assert cell["trades"] == 12
    assert cell["matches"] == 1
    assert cell["verdict"] == "THIN"


def test_certainty_is_measured_across_matches_not_across_moves():
    # Ten matches. Nine are flat; one runs away with twenty big moves.
    outcomes = [_outcome(drift=0.2, spread=1.0, slug=f"cs2-m{index}-x")
                for index in range(9)]
    outcomes += [_outcome(drift=12.0, spread=1.0, slug="cs2-runaway-y")
                 for _ in range(20)]
    cell = summarise_cell(outcomes, jump=0.05, horizon=300)
    assert cell["matches"] == 10
    # Per move the runaway match is most of the sample and the mean looks sure.
    assert cell["drift_t_moves"] > SIGNIFICANCE_T
    # Per match it is one observation out of ten, and the cell says so.
    assert cell["drift_t"] < SIGNIFICANCE_T
    assert cell["verdict"] == "NO_SIGNAL"


def test_a_cell_spread_over_enough_matches_still_reaches_tradable():
    outcomes = [_outcome(drift=6.0 + (0.5 if index % 2 else -0.5),
                         spread=1.0, slug=f"cs2-m{index}-x")
                for index in range(12)]
    cell = summarise_cell(outcomes, jump=0.05, horizon=300)
    assert cell["matches"] == 12
    assert cell["verdict"] == "TRADABLE"


# 11 ------------------------------------------------------------------------
def test_a_held_out_run_excludes_whole_games_not_whole_jumps():
    """A game already on the tape is excluded entirely, not from the cut on.

    Peeking at a growing sample and re-asking whether it crossed the bar is
    what manufactures a crossing. The fix is to score only games that started
    AFTER the question was fixed -- and a game that was already running when
    the cut was taken is not one of them, however many of its jumps land after
    the timestamp. Cutting by jump would let a game that was already read
    contribute to its own held-out test.
    """
    early = _rise(start_ts=0)                 # first quote at t=0, jump at 360
    late = _rise(start_ts=10_000)             # first quote at 10_000
    tape = {"cs2-early-a": early, "cs2-late-b": late}

    kept = replay(tape, jump=0.03, horizon=300, games_from=10_000)
    assert [done.slug for done in kept] == ["cs2-late-b"]

    # The early game's own jump lands at t=360, well after a cut of 300, and it
    # is still excluded: the cut is on the game, not on the moment. Cutting by
    # jump would have kept it and let a game already read into its own test.
    still_kept = replay(tape, jump=0.03, horizon=300, games_from=300)
    assert [done.slug for done in still_kept] == ["cs2-late-b"]


def test_without_a_cut_every_game_on_the_tape_is_scored():
    tape = {"cs2-early-a": _rise(start_ts=0),
            "cs2-late-b": _rise(start_ts=10_000)}
    assert len(replay(tape, jump=0.03, horizon=300)) == 2


def test_the_report_says_which_cut_it_was_scored_under(tmp_path: Path):
    path = _store(tmp_path, "cs2-a-b", _rise())
    report = sweep(path, jumps=(0.03,), horizons=(300,), games_from=10_000)
    assert report["games_from"] == 10_000
    assert report["markets"] == 0
    assert report["state"] == "NO_TAPE"
