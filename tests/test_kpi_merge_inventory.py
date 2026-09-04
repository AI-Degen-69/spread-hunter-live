"""A merged pair is gone from the book, and the board has to say so.

The whole strategy is: buy UP and DOWN for under $1.00, then merge the pair
back into exactly $1.00 of USDC. The merge takes both legs off the book and
books its proceeds as realized PnL.

`inventory_from_registry` -- what the engine sizes its next quote against --
has always subtracted a `merge` / `shadow_merge` close from both legs. The KPI
report the dashboard reads did not: it handled only the one-sided exits. Two
readers of one store disagreed on what the account holds, and the board was
the one that was wrong -- a market whose every pair had merged still showed
its whole merged value as an open position marked at par, counting the same
shares once as realized and again as unrealized.

Journeys under test:
1. As the Owner, a market whose pairs have all merged shows no position.
2. As the Owner, a partly merged pair shows only the shares that did not merge.
3. As the Owner, the board and the engine never disagree on what is held.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from core_brain import kpi as kpi_mod
from core_brain.kpi import report
from core_brain.order_registry import (
    SCHEMA, CloseRecord, FillRecord, OrderRecord, OrderRegistry,
    inventory_from_registry,
)

RUN = "run-merge"
CID = "0xmerged"
# The report reads the UP leg as the first token id in sort order, so the
# names carry the leg they stand for rather than relying on insertion order.
TOK_UP = "tok-a-up"
TOK_DN = "tok-b-dn"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


def _fill_leg(reg: OrderRegistry, uid: str, token: str, shares: float, price: float,
              pair_id: str = "pair-1") -> None:
    """One order, filled in full, on one leg of the pair."""
    now = int(time.time())
    reg.create_order(OrderRecord(
        id=uid, condition_id=CID, token_id=token, side="BUY", price=price,
        original_size=shares, status="filled", posted_ts=now, last_polled_ts=now,
        order_id=f"venue-{uid}", pair_id=pair_id, run_id=RUN,
    ))
    reg.record_fill(FillRecord(trade_id=f"trade-{uid}", order_uuid=uid, size=shares,
                            price=price, venue_ts=now * 1000, run_id=RUN))


def _merge(reg: OrderRegistry, shares: float, cost_basis: float,
           method: str = "shadow_merge", tx: str = "pair-1") -> None:
    """A merge close: both legs leave at $1.00 a share, cost split evenly --
    the same row shape `record_shadow_merges` and the live `merge` verb write.
    """
    reg.log_close(CloseRecord(
        ts=time.time(), condition_id=CID, method=method, shares=shares,
        cost_basis=cost_basis, proceeds=shares * 1.0, fee=0.0, gas=0.0,
        realized_pnl=shares - cost_basis,
        up_cost_removed=cost_basis / 2.0, dn_cost_removed=cost_basis / 2.0,
        tx_hash=tx, run_id=RUN,
    ))


def _market(db_file) -> dict:
    return report(db_path=str(db_file), run_id="all")["by_market"][CID]


# -- A merged pair is not a position -----------------------------------------

def test_a_fully_merged_pair_leaves_no_position(temp_db):
    # Arrange -- 10 UP at $0.48 and 10 DOWN at $0.50, merged as one pair.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)
    _merge(reg, shares=10.0, cost_basis=9.80)

    # Act
    m = _market(temp_db)

    # Assert -- the shares went back to the venue and came out as USDC.
    assert m["up_sh"] == pytest.approx(0.0)
    assert m["dn_sh"] == pytest.approx(0.0)
    assert m["total_sh"] == pytest.approx(0.0)


def test_a_fully_merged_pair_gives_up_its_cost_with_its_shares(temp_db):
    # Arrange -- cost has to leave the inventory with the shares, or the board
    # keeps pricing a position it no longer holds. $9.80 went in; the merge
    # takes back a cost basis of $9.80.
    #
    # What is left is the even-split residual, not a real position: `closes`
    # has no token column, so both the live `merge` verb and the rehearsal
    # charge each leg half the basis. The UP leg cost $4.80 and is charged
    # $4.90, clamps at zero, and the ten cents it could not absorb stay on
    # DOWN. `inventory_from_registry` carries exactly the same residual -- the
    # next test pins the two readers together.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)
    _merge(reg, shares=10.0, cost_basis=9.80)

    # Act
    m = _market(temp_db)

    # Assert -- $9.70 of the $9.80 is gone, and nothing can be marked without
    # shares to mark.
    assert m["total_cost"] == pytest.approx(0.10)
    assert m["total_sh"] == pytest.approx(0.0)


def test_a_partly_merged_pair_shows_only_what_did_not_merge(temp_db):
    # Arrange -- 10 on each leg, 6 of them merged.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)
    _merge(reg, shares=6.0, cost_basis=5.88)

    # Act
    m = _market(temp_db)

    # Assert -- four shares a side are still held.
    assert m["up_sh"] == pytest.approx(4.0)
    assert m["dn_sh"] == pytest.approx(4.0)


def test_a_live_merge_retires_shares_like_a_rehearsal_one(temp_db):
    # Arrange -- `merge` is the venue's spelling, `shadow_merge` the
    # rehearsal's. The row-level labels differ on purpose; the arithmetic they
    # record is the same, so the board must honour both.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)
    _merge(reg, shares=10.0, cost_basis=9.80, method="merge")

    # Act / Assert
    assert _market(temp_db)["total_sh"] == pytest.approx(0.0)


def test_a_merge_and_a_one_sided_exit_both_retire_their_shares(temp_db):
    # Arrange -- 10 a side: 6 merged as a pair, and 4 of the UP leg dumped as a
    # single buy. Handling one and not the other is what produced the phantom.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)
    _merge(reg, shares=6.0, cost_basis=5.88)
    reg.log_close(CloseRecord(
        ts=time.time(), condition_id=CID, method="single_buy_exit", shares=4.0,
        up_price=0.49, cost_basis=1.92, proceeds=1.96, realized_pnl=0.04,
        up_cost_removed=1.92, dn_cost_removed=0.0, tx_hash="exit-1", run_id=RUN,
    ))

    # Act
    m = _market(temp_db)

    # Assert -- the UP leg is empty, the DOWN leg keeps its unmerged four.
    assert m["up_sh"] == pytest.approx(0.0)
    assert m["dn_sh"] == pytest.approx(4.0)


# -- The board and the engine read one store ---------------------------------

def test_the_board_and_the_engine_agree_on_what_is_held(temp_db):
    # Arrange -- `inventory_from_registry` is what the engine sizes its next
    # quote against. Two readers of one store that disagree is the bug, and a
    # test on the board alone would not have caught which one was wrong.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)
    _merge(reg, shares=6.0, cost_basis=5.88)

    # Act
    m = _market(temp_db)
    inv = inventory_from_registry(CID, TOK_UP, TOK_DN, db_path=temp_db)

    # Assert
    assert m["up_sh"] == pytest.approx(inv.up_shares)
    assert m["dn_sh"] == pytest.approx(inv.down_shares)
    assert m["up_cost"] == pytest.approx(inv.up_cost)
    assert m["dn_cost"] == pytest.approx(inv.down_cost)


def test_an_unmerged_pair_is_still_a_position(temp_db):
    # Arrange -- the subtraction must not fire on a market that never merged.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)

    # Act
    m = _market(temp_db)

    # Assert -- held, priced and balanced, exactly as before this change.
    assert m["up_sh"] == pytest.approx(10.0)
    assert m["dn_sh"] == pytest.approx(10.0)
    assert m["total_cost"] == pytest.approx(9.80)
    assert m["pair_cost"] == pytest.approx(0.98)
    assert m["balance"] == pytest.approx(1.0)
