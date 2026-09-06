"""A settled market is worth its outcome, not the book it no longer has.

Nobody quotes a race that is over. Marking a settled market off quotes gives
the operator `--` beside shares that redeem at a dollar each, so the report has
to say which leg the settlement paid. The winning LABEL ("Up", a team name) is
for a human to read; the venue token id is the only field that names a leg, and
until now the registry threw it away on the way in.

Journeys under test:
1. As the Owner, a settled market's winning token id survives into the store.
2. As the Owner, a registry written before this change gains the column.
3. As the Owner, the report names which of my two legs won.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from core_brain import kpi as kpi_mod
from core_brain.kpi import report
from core_brain.order_registry import (
    SCHEMA, FillRecord, OrderRecord, OrderRegistry, ResolutionRecord,
)

RUN = "run-settled"
CID = "0xsettled"
# The report reads the UP leg as the first token id in sort order.
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


def _fill_leg(reg: OrderRegistry, uid: str, token: str, shares: float,
              price: float) -> None:
    now = int(time.time())
    reg.create_order(OrderRecord(
        id=uid, condition_id=CID, token_id=token, side="BUY", price=price,
        original_size=shares, status="filled", posted_ts=now,
        last_polled_ts=now, order_id=f"venue-{uid}", pair_id="pair-1",
        run_id=RUN,
    ))
    reg.record_fill(FillRecord(trade_id=f"trade-{uid}", order_uuid=uid,
                               size=shares, price=price,
                               venue_ts=now * 1000, run_id=RUN))


def test_the_winning_token_id_survives_into_the_store(temp_db):
    # Arrange / Act
    reg = OrderRegistry(temp_db)
    reg.log_resolution(ResolutionRecord(
        condition_id=CID, winning_token="Up", resolved_ts=1.0, run_id=RUN,
        winning_token_id=TOK_UP,
    ))

    # Assert -- the label alone cannot say which held leg redeems at $1.00.
    row = reg.get_all_resolutions()[0]
    assert row["winning_token"] == "Up"
    assert row["winning_token_id"] == TOK_UP


def test_a_registry_written_before_this_change_gains_the_column(tmp_path):
    # Arrange -- a store whose `resolutions` table predates the token id. The
    # production registry is never rebuilt, so the column has to arrive by
    # migration or the sweeper's next write fails on it.
    db_file = tmp_path / "old.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.execute("DROP TABLE resolutions")
    con.execute("CREATE TABLE resolutions (condition_id TEXT PRIMARY KEY, "
                "winning_token TEXT, resolved_ts REAL, run_id TEXT)")
    con.commit()
    con.close()

    # Act
    reg = OrderRegistry(db_file)
    reg.log_resolution(ResolutionRecord(
        condition_id=CID, winning_token="Up", resolved_ts=1.0, run_id=RUN,
        winning_token_id=TOK_UP,
    ))

    # Assert
    assert reg.get_all_resolutions()[0]["winning_token_id"] == TOK_UP


def test_the_report_names_the_leg_the_settlement_paid(temp_db):
    # Arrange -- five shares on each leg and a settlement that paid DOWN.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 5.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 5.0, 0.50)
    reg.log_resolution(ResolutionRecord(
        condition_id=CID, winning_token="Down", resolved_ts=1.0, run_id=RUN,
        winning_token_id=TOK_DN,
    ))

    # Act
    market = report(db_path=str(temp_db), run_id="all")["by_market"][CID]

    # Assert
    assert market["winning_leg"] == "dn"


def test_an_unrecorded_winner_names_no_leg_rather_than_guessing(temp_db):
    # Arrange -- the sweeper wrote a label but no token id, which is every row
    # written before this change. A leg guessed from "Down" would be a venue
    # fact the store does not hold.
    reg = OrderRegistry(temp_db)
    _fill_leg(reg, "o-up", TOK_UP, 5.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 5.0, 0.50)
    reg.log_resolution(ResolutionRecord(
        condition_id=CID, winning_token="Down", resolved_ts=1.0, run_id=RUN,
    ))

    # Act
    market = report(db_path=str(temp_db), run_id="all")["by_market"][CID]

    # Assert
    assert market["winning_leg"] is None
