"""Recovering every grace value from one run held at the longest one."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import grace_sweep_report as gsr


def _shadow(tmp_path: Path, fills) -> Path:
    """fills: (ts_sec, cid, side, size, price, pair_id).

    Mirrors the real store on the three things the report has to get right:
    `fills.order_uuid` holds `orders.id`, the UP/DOWN leg is carried by the
    token rather than by `orders.side` (which is always BUY), and
    `recorded_ts` is in milliseconds.
    """
    db = tmp_path / "shadow.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE fills (order_uuid TEXT, size REAL, price REAL,"
                 " recorded_ts REAL)")
    conn.execute("CREATE TABLE orders (id TEXT, order_id TEXT,"
                 " condition_id TEXT, side TEXT, token_id TEXT, pair_id TEXT)")
    conn.execute("CREATE TABLE quotes (token_id TEXT, side TEXT)")
    seen_tokens = set()
    for i, row in enumerate(fills):
        ts, cid, side, size, price, pair_id = row
        oid, token = f"o{i}", f"{cid}-{side}"
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)",
                     (oid, f"shadow-{i}", cid, "BUY", token, pair_id))
        conn.execute("INSERT INTO fills VALUES (?,?,?,?)",
                     (oid, size, price, ts * 1000.0))
        if token not in seen_tokens:
            conn.execute("INSERT INTO quotes VALUES (?,?)", (token, side))
            seen_tokens.add(token)
    conn.commit()
    conn.close()
    return db


def _book(tmp_path: Path, samples) -> Path:
    """samples: (ts, cid, bid_up, bid_down)."""
    db = tmp_path / "book.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE book_samples (ts REAL, condition_id TEXT,"
                 " best_bid_up REAL, best_bid_down REAL)")
    conn.executemany("INSERT INTO book_samples VALUES (?,?,?,?)", samples)
    conn.commit()
    conn.close()
    return db


# --- pairing ---------------------------------------------------------------


def _ev(pair_id, ts, side, cid="m", size=5.0, price=0.66):
    return {"pair_id": pair_id, "ts": ts, "side": side, "cid": cid,
            "size": size, "price": price}


def test_a_companion_fill_is_matched_within_its_own_pair():
    legs = gsr.companion_gaps([_ev("p1", 100.0, "UP"), _ev("p1", 130.0, "DOWN")])

    assert len(legs) == 1
    assert legs[0]["side"] == "UP"
    assert legs[0]["gap"] == pytest.approx(30.0)


def test_a_leg_whose_companion_never_fills_has_no_gap():
    # These are the denominator. No grace value rescues them, and dropping
    # them would inflate every rescue rate.
    legs = gsr.companion_gaps([_ev("p1", 100.0, "UP")])

    assert legs[0]["gap"] is None


def test_legs_of_different_pairs_never_marry():
    # The bug this replaced: matching the next opposite-side fill in the same
    # market married legs the engine never grouped, and on the real run that
    # produced a phantom $1.15 pair -- over the dollar the instrument pays,
    # and impossible, because the risk gate had refused exactly that.
    legs = gsr.companion_gaps([
        _ev("p1", 100.0, "UP", price=0.42),
        _ev("p2", 101.0, "DOWN", price=0.73),
    ])

    assert len(legs) == 2
    assert all(l["gap"] is None for l in legs)


def test_two_markets_do_not_pair_across_each_other():
    legs = gsr.companion_gaps([
        _ev("p1", 100.0, "UP", cid="a"), _ev("p2", 110.0, "DOWN", cid="b")])

    assert sorted(l["cid"] for l in legs) == ["a", "b"]
    assert all(l["gap"] is None for l in legs)


def test_a_same_side_refill_is_not_a_companion():
    # Adding to the leg we already hold is not the other side of the pair.
    legs = gsr.companion_gaps([_ev("p1", 100.0, "UP"), _ev("p1", 110.0, "UP")])

    assert legs[0]["gap"] is None


# --- the horizons -------------------------------------------------------------


def test_a_longer_grace_contains_the_shorter_ones(tmp_path):
    # The whole reason one run answers every value: a companion at 30s is
    # rescued by 45 and 120, not by 15 or 0.
    db = _shadow(tmp_path, [
        (100.0, "m", "UP", 5.0, 0.66, "p1"),
        (130.0, "m", "DOWN", 5.0, 0.33, "p1"),
    ])

    text = gsr.sweep(db, None)

    lines = {int(float(l.split()[0])): l for l in text.splitlines()
             if l.strip()[:1].isdigit() and "rescued" not in l}
    # The 5s row is the poll floor, below which the loop cannot exit at all.
    assert lines[gsr.POLL_INTERVAL_SEC].split()[1] == "0"
    assert lines[15].split()[1] == "0"
    assert lines[45].split()[1] == "1"
    assert lines[120].split()[1] == "1"


def test_the_floor_row_is_the_poll_interval_not_zero(tmp_path):
    # `_route_pair` exits on the first poll AFTER grace expires, so a grace
    # below the rotation cadence is unreachable. A zero row would recommend
    # against a setting the engine cannot make.
    db = _shadow(tmp_path, [(100.0, "m", "UP", 5.0, 0.66, "p1")])

    text = gsr.sweep(db, None)

    assert gsr.HORIZONS[0] == gsr.POLL_INTERVAL_SEC
    assert 0.0 not in gsr.HORIZONS
    assert "the 5s row is the floor" in text


def test_the_exit_is_priced_at_the_bid_as_it_stood_at_the_horizon(tmp_path):
    # A leg held 120s exits at the 120s bid. Pricing it at the fill-time bid
    # would hide the cost of waiting, which is the whole trade-off.
    db = _shadow(tmp_path, [(100.0, "m", "UP", 10.0, 0.66, "p1")])
    book = _book(tmp_path, [
        (100.0, "m", 0.66, 0.33),
        (115.0, "m", 0.65, 0.34),
        (220.0, "m", 0.60, 0.39),
    ])

    text = gsr.sweep(db, book)

    rows = {int(float(l.split()[0])): l.split() for l in text.splitlines()
            if l.strip()[:1].isdigit() and "rescued" not in l}
    # at 15s the bid is 0.65 -> (0.65-0.66)*10 = -0.10
    assert rows[15][4] == "-0.1000"
    # at 120s the bid is 0.60 -> (0.60-0.66)*10 = -0.60
    assert rows[120][4] == "-0.6000"


def test_the_exit_column_is_unmeasured_without_a_book(tmp_path):
    # Guessing a bid would make the cost of waiting look like whatever the
    # guess was. Say so instead.
    db = _shadow(tmp_path, [(100.0, "m", "UP", 5.0, 0.66, "p1")])

    text = gsr.sweep(db, None)

    assert "unmeasured" in text
    assert "pass --book-db" in text


def test_bid_at_takes_the_first_sample_at_or_after_the_horizon(tmp_path):
    book = gsr._ro(_book(tmp_path, [
        (100.0, "m", 0.66, 0.33),
        (150.0, "m", 0.60, 0.39),
    ]))
    try:
        # A sample BEFORE the horizon would price the exit on information it
        # did not have.
        assert gsr.bid_at(book, "m", "UP", 120.0) == 0.60
        assert gsr.bid_at(book, "m", "UP", 100.0) == 0.66
        assert gsr.bid_at(book, "m", "UP", 999.0) is None
    finally:
        book.close()


def test_sweep_survives_a_run_with_no_fills(tmp_path):
    db = _shadow(tmp_path, [])

    text = gsr.sweep(db, None)

    assert "nothing to sweep" in text
