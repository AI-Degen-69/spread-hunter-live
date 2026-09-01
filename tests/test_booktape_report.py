"""The book/tape report reads only live tape, and names what the cap refuses."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts import book_tape_recorder as btr
from scripts import booktape_report as rpt


def _store(tmp_path: Path, samples: list[dict], tape: list[tuple]) -> Path:
    """A store with the given book samples and (ticks, volume, bootstrap) tape."""
    db = tmp_path / "booktape.db"
    conn = btr.open_store(db)
    try:
        for i, s in enumerate(samples):
            row = {"ts": float(i), "run_id": "r", "condition_id": "0xabc",
                   "market_slug": "mlb-a-b", "tick": 0.01,
                   "best_bid_up": 0.42, "best_ask_up": 0.43,
                   "best_bid_down": 0.57, "best_ask_down": 0.58,
                   "mid_up": 0.425, "mid_down": 0.575, "mid_sum": 1.0,
                   "touch_pair_cost": 0.99,
                   "touch_size_up": 800.0, "touch_size_down": 700.0}
            row.update(s)
            btr.write_sample(conn, row)
        for ticks, volume, boot in tape:
            conn.execute(
                "INSERT INTO tape_buckets (ts, run_id, condition_id, market_slug,"
                " token_id, side, price, ticks_from_mid, volume, is_bootstrap)"
                " VALUES (0,'r','0xabc','mlb-a-b','tok','UP',0.42,?,?,?)",
                (ticks, volume, boot))
        conn.commit()
    finally:
        conn.close()
    return db


def test_reachability_excludes_bootstrap_rows(tmp_path):
    # Arrange -- 900 shares of bootstrap tape at an impossible distance, and
    # 100 of live tape one tick under mid.
    db = _store(tmp_path, [{}], [(-27, 900.0, 1), (1, 100.0, 0)])

    # Act
    conn = sqlite3.connect(db)
    try:
        rows = rpt.reachability(conn)
    finally:
        conn.close()

    # Assert -- only the live row survives
    assert rows == [(1, 100.0, 1)]


def test_report_counts_bootstrap_rows_separately(tmp_path):
    db = _store(tmp_path, [{}], [(-27, 900.0, 1), (1, 100.0, 0)])

    text = rpt.report(db)

    assert "1 live tape rows, 1 bootstrap rows excluded" in text


def test_report_marks_a_touch_pair_at_the_cap_as_refused(tmp_path):
    # A $0.99 touch pair is the cheapest two resting bids can make on a 1c
    # book, and `risk.hard_block` refuses it with `>=`.
    db = _store(tmp_path, [{"touch_pair_cost": 0.99}], [])

    text = rpt.report(db)

    assert "$0.9900" in text
    assert "REFUSED by max_pair_cost" in text


def test_report_marks_a_cheaper_touch_pair_as_tradeable(tmp_path):
    db = _store(tmp_path, [{"touch_pair_cost": 0.85, "mid_sum": 0.96}], [])

    text = rpt.report(db)

    assert "$0.8500" in text
    assert "tradeable" in text


def test_reachable_share_counts_only_volume_at_or_below_mid(tmp_path):
    # 300 above mid can never reach a resting buy; 100 at one tick under can.
    db = _store(tmp_path, [{}], [(-1, 300.0, 0), (1, 100.0, 0)])

    text = rpt.report(db)

    assert "reachable at <= 1 tick(s) under mid:  25.00%" in text


def test_report_survives_a_store_with_no_tape(tmp_path):
    db = _store(tmp_path, [{}], [])

    text = rpt.report(db)

    assert "all tape:" in text
