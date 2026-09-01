"""The recorder's pure core, and the refusals that keep it out of the money path."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import book_tape_recorder as btr


# --- distance from mid --------------------------------------------------------


def test_ticks_from_mid_counts_levels_below_mid():
    # Arrange
    mid, tick = 0.505, 0.01

    # Act / Assert -- 0.50 is the touch, half a tick under a 1c-spread mid
    assert btr.ticks_from_mid(mid, 0.50, tick) == 1
    assert btr.ticks_from_mid(mid, 0.49, tick) == 2
    assert btr.ticks_from_mid(mid, 0.48, tick) == 3


def test_ticks_from_mid_is_negative_above_mid():
    # A resting BUY can never be reached by volume printing above mid, so the
    # sign is what separates reachable tape from the rest. On a 1c-spread book
    # the best ask sits half a tick over mid and lands in the first bucket
    # above it.
    assert btr.ticks_from_mid(0.505, 0.51, 0.01) == -1
    assert btr.ticks_from_mid(0.505, 0.52, 0.01) == -2


def test_ticks_from_mid_returns_none_without_a_mid():
    assert btr.ticks_from_mid(None, 0.50, 0.01) is None


def test_ticks_from_mid_returns_none_on_a_zero_tick():
    assert btr.ticks_from_mid(0.505, 0.50, 0.0) is None


# --- the touch pair -----------------------------------------------------------


def test_touch_pair_cost_sums_the_two_best_bids():
    # Arrange -- the shipped universe: 1c tick, 1c spread, mids summing to 1.0000
    up = {"best_bid": 0.42, "best_ask": 0.43}
    down = {"best_bid": 0.57, "best_ask": 0.58}

    # Act
    cost = btr.touch_pair_cost(up, down)

    # Assert -- $0.99, exactly the cap `risk.hard_block` refuses with `>=`
    assert cost == 0.99


def test_touch_pair_cost_returns_none_when_a_side_has_no_bid():
    assert btr.touch_pair_cost({"best_bid": None}, {"best_bid": 0.57}) is None


# --- tape bucketing -----------------------------------------------------------


def test_bucket_trades_labels_each_print_with_its_distance():
    # Arrange
    traded = {"tok_up": {0.50: 120.0, 0.49: 30.0}}
    mids = {"tok_up": 0.505}

    # Act
    rows = btr.bucket_trades(traded, mids, 0.01)

    # Assert
    by_price = {r["price"]: r for r in rows}
    assert by_price[0.50]["ticks_from_mid"] == 1
    assert by_price[0.50]["volume"] == 120.0
    assert by_price[0.49]["ticks_from_mid"] == 2


def test_bucket_trades_keeps_volume_when_the_mid_is_unreadable():
    # A one-sided book must not erase real tape -- that under-counts the venue
    # on exactly the markets whose books were briefly broken.
    rows = btr.bucket_trades({"tok": {0.50: 5.0}}, {}, 0.01)

    assert len(rows) == 1
    assert rows[0]["ticks_from_mid"] is None
    assert rows[0]["volume"] == 5.0


def test_bucket_trades_drops_zero_volume_levels():
    assert btr.bucket_trades({"tok": {0.50: 0.0}}, {"tok": 0.505}, 0.01) == []


def test_reachable_fraction_measures_volume_within_the_offset():
    # Arrange -- 100 shares at the touch, 400 above mid where a bid never reaches
    rows = [
        {"ticks_from_mid": 1, "volume": 100.0},
        {"ticks_from_mid": -1, "volume": 400.0},
    ]

    # Act / Assert
    assert btr.reachable_fraction(rows, max_ticks=3) == pytest.approx(0.2)


def test_reachable_fraction_is_zero_on_an_empty_tape():
    assert btr.reachable_fraction([], max_ticks=3) == 0.0


# --- the refusal --------------------------------------------------------------


def test_open_store_refuses_the_production_registry(tmp_path):
    with pytest.raises(SystemExit) as e:
        btr.open_store(tmp_path / "orders.db")

    assert "production registry" in str(e.value)


def test_open_store_creates_both_tables(tmp_path):
    conn = btr.open_store(tmp_path / "booktape.db")
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()

    assert {"book_samples", "tape_buckets"} <= names


# --- the universe -------------------------------------------------------------


def test_load_universe_keeps_only_rows_carrying_a_condition_id(tmp_path):
    path = tmp_path / "markets.json"
    path.write_text(json.dumps([
        {"cid": "0xabc", "slug": "mlb-a-b"},
        {"slug": "no-cid"},
        "not a dict",
    ]), encoding="utf-8")

    got = btr.load_universe(path)

    assert [m["cid"] for m in got] == ["0xabc"]


def test_load_universe_survives_a_missing_file(tmp_path):
    assert btr.load_universe(tmp_path / "absent.json") == []


# --- one pass, end to end -----------------------------------------------------


class _Market:
    up_token = "tok_up"
    down_token = "tok_dn"


def _books(_host, token_id):
    if token_id == "tok_up":
        return {"token_id": token_id, "best_bid": 0.42, "best_ask": 0.43,
                "bids": {0.42: 800.0}, "asks": {0.43: 900.0}}
    return {"token_id": token_id, "best_bid": 0.57, "best_ask": 0.58,
            "bids": {0.57: 700.0}, "asks": {0.58: 600.0}}


def test_run_records_a_sample_and_its_tape(tmp_path):
    # Arrange
    markets = tmp_path / "markets.json"
    markets.write_text(json.dumps(
        [{"cid": "0xabc", "slug": "mlb-a-b", "tick": 0.01}]), encoding="utf-8")
    db = tmp_path / "booktape.db"
    clock = iter([0.0, 0.0, 10.0])       # start, one pass, then past the deadline

    # Act
    rc = btr.run(
        minutes=0.1, interval=0.0, db_path=db, run_id="booktape-test",
        markets_path=markets, clob_host="https://clob.example",
        fetch_market=lambda cid: _Market(),
        fetch_book=_books,
        fetch_tape=lambda cid, seen: {"tok_up": {0.42: 250.0}},
        now=lambda: next(clock), sleep=lambda s: None)

    # Assert
    assert rc == 0
    conn = sqlite3.connect(db)
    try:
        sample = conn.execute(
            "SELECT touch_pair_cost, mid_sum, touch_size_up, run_id"
            " FROM book_samples").fetchone()
        tape = conn.execute(
            "SELECT side, ticks_from_mid, volume FROM tape_buckets").fetchall()
    finally:
        conn.close()

    assert sample == (0.99, 1.0, 800.0, "booktape-test")
    assert tape == [("UP", 1, 250.0)]


def test_run_skips_a_market_whose_book_will_not_fetch(tmp_path):
    # One unreachable market must not end the recording -- the hour is the
    # expensive part, and a raise here would spend it for nothing.
    markets = tmp_path / "markets.json"
    markets.write_text(json.dumps([{"cid": "0xbad", "slug": "broken"}]),
                       encoding="utf-8")
    db = tmp_path / "booktape.db"
    clock = iter([0.0, 0.0, 10.0])

    def boom(_host, _token):
        raise ConnectionError("venue down")

    rc = btr.run(
        minutes=0.1, interval=0.0, db_path=db, run_id="r", markets_path=markets,
        clob_host="https://clob.example", fetch_market=lambda cid: _Market(),
        fetch_book=boom, fetch_tape=lambda cid, seen: {},
        now=lambda: next(clock), sleep=lambda s: None)

    assert rc == 0
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM book_samples").fetchone()[0] == 0
    finally:
        conn.close()


def test_sample_market_returns_none_without_two_tokens():
    class _Half:
        up_token = "tok_up"
        down_token = None

    got = btr.sample_market({"cid": "0xabc"}, lambda cid: _Half(), _books,
                            lambda cid, seen: {}, set(), "https://clob.example")

    assert got is None
