"""Account-wide venue history is not this run's trading record.

The dashboard's Sync button calls `venue_sync`, which reads the wallet's whole
Polymarket history -- every position the account ever closed, whoever opened it
and whenever -- and writes it into `closes`. Those rows already carry the
sentinel run_id `venue_sync` precisely so run analytics can tell them apart.

They were told apart only while the card showed ONE run. The card's default is
RUN #all, which slices nothing, so the sentinel rows landed in the run's
realized P&L, win rate, profit factor, expectancy and equity curve. On the live
registry that read as +$112.93 realized and a 67.1% win rate from 72 account
rows, while the bot's own seven closes summed to -$2.40.

The float mark the same sync writes had the matching defect the other way
round: it was stamped with the ACTIVE run id, so account-wide open positions
were reported as the run's own committed capital and unrealized P&L.

Journeys under test:
1. As the Owner, the P&L and win rate on the card describe what the BOT did,
   under every run filter including "all".
2. As the Owner, the account-wide figure is still available, named as what it
   is, so syncing does not lose information.
3. As the Owner, committed capital and unrealized P&L describe the run's own
   positions, not everything the wallet holds.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from core_brain import kpi as kpi_mod
from core_brain.kpi import report
from core_brain.order_registry import (
    SCHEMA,
    VENUE_SYNC_RUN_ID,
    CloseRecord,
    OrderRegistry,
)

RUN = "run-bot"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


@pytest.fixture
def mixed_db(temp_db):
    """The bot's own losing close, plus a big account-wide sync row."""
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600
    reg.log_close(CloseRecord(
        ts=t0 + 60, condition_id="0xbot", market_slug="bot-market",
        method="single_buy_exit", shares=5.0, cost_basis=2.75, proceeds=2.20,
        realized_pnl=-0.55, tx_hash="0xbot1", run_id=RUN,
    ))
    reg.log_close(CloseRecord(
        ts=t0 + 120, condition_id="0xwallet", market_slug="wallet-market",
        method="venue_sync", shares=40.0, up_price=0.30,
        realized_pnl=28.37, tx_hash="0xasset1", run_id=VENUE_SYNC_RUN_ID,
    ))
    return temp_db


# --------------------------------------------------------------------------
# Journey 1: the card describes the bot, under every filter
# --------------------------------------------------------------------------

@pytest.mark.parametrize("run_filter", [RUN, "all"])
def test_realized_pnl_excludes_account_wide_sync_rows(mixed_db, run_filter):
    """"all" must mean every RUN, not every row the wallet ever produced."""
    p = report(db_path=mixed_db, run_id=run_filter)["portfolio"]
    assert p["realized_pnl"] == pytest.approx(-0.55)
    assert p["closes_count"] == 1


def test_win_rate_is_not_inflated_by_the_wallets_history(mixed_db):
    # One close, and it lost: a 0% win rate is the truth. Counting the sync row
    # turns the same run into 50% wins and a positive profit factor.
    ta = report(db_path=mixed_db, run_id="all")["trade_analytics"]
    assert ta["n_closes"] == 1
    assert ta["win_rate"] == pytest.approx(0.0)


def test_equity_curve_steps_only_on_the_runs_own_closes(mixed_db):
    series = report(db_path=mixed_db, run_id="all")["equity_series"]
    steps = [pt for pt in series if pt.get("type") == "close"]
    assert len(steps) == 1


# --------------------------------------------------------------------------
# Journey 2: the account-wide figure is kept, and named
# --------------------------------------------------------------------------

def test_the_account_wide_total_is_reported_separately(mixed_db):
    """Syncing must not lose the number, only stop misattributing it."""
    p = report(db_path=mixed_db, run_id="all")["portfolio"]
    assert p["venue_realized_pnl"] == pytest.approx(28.37)


def test_venue_realized_is_zero_when_nothing_was_ever_synced(temp_db):
    reg = OrderRegistry(temp_db)
    reg.log_close(CloseRecord(
        ts=time.time() - 60, condition_id="0xbot", market_slug="bot-market",
        method="merge", shares=5.0, cost_basis=4.70, proceeds=5.00,
        realized_pnl=0.30, tx_hash="0xbot1", run_id=RUN,
    ))
    p = report(db_path=temp_db, run_id="all")["portfolio"]
    assert p["venue_realized_pnl"] == pytest.approx(0.0)
    assert p["realized_pnl"] == pytest.approx(0.30)


# --------------------------------------------------------------------------
# Journey 3: committed and unrealized describe the run's own book
# --------------------------------------------------------------------------

def test_a_sync_float_mark_is_not_the_runs_committed_capital(temp_db):
    """The sync's float mark carries the sentinel, so the run keeps its own.

    Without the sentinel the account-wide mark is the newest one in the run's
    window and wins, reporting the whole wallet's open positions as capital
    this run committed.
    """
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600
    reg.log_float_mark(unrealized_usd=1.25, committed_open_usd=9.60,
                       naked_usd=0.0, ts=t0 + 60, run_id=RUN)
    reg.log_float_mark(unrealized_usd=-24.42, committed_open_usd=24.42,
                       naked_usd=0.0, ts=t0 + 120, run_id=VENUE_SYNC_RUN_ID)

    p = report(db_path=temp_db, run_id="all")["portfolio"]
    assert p["open_committed_usd"] == pytest.approx(9.60)
    assert p["unrealized_usd"] == pytest.approx(1.25)


def test_venue_sync_stamps_its_float_mark_with_the_sentinel(monkeypatch, temp_db):
    """`venue_sync` writes account-wide state; it must say so on both tables.

    Its closes already carry the sentinel. The float mark was stamped with the
    ACTIVE run instead, which is how the wallet's open positions became the
    run's committed capital.
    """
    from core_brain import order_manager as om

    monkeypatch.setenv("POLY_FUNDER", "0xowner")
    monkeypatch.setattr(om, "fetch_live_balance", lambda who: 81.28)
    import core_brain.account as acct
    monkeypatch.setattr(acct, "read_account", lambda who, collateral_usd=None, **kw: {
        "collateral_usd": collateral_usd, "positions_value_usd": 0.0,
        "account_value_usd": collateral_usd, "open_positions_count": 1,
        "closed_positions_count": 0,
    })
    monkeypatch.setattr(acct, "fetch_closed_positions",
                        lambda who, **kw: [])
    monkeypatch.setattr(acct, "fetch_open_positions", lambda who, **kw: [
        {"conditionId": "0xwallet", "asset": "0xasset1", "size": 40.0,
         "avgPrice": 0.61, "initialValue": 24.42, "currentValue": 0.0,
         "cashPnl": -24.42},
    ])
    monkeypatch.setattr(om, "get_run_id", lambda: RUN, raising=False)

    om.venue_sync(funder="0xowner", db_path=temp_db, quiet=True)

    marks = OrderRegistry(temp_db).get_all_float_marks()
    assert marks, "venue_sync wrote no float mark"
    assert marks[-1]["run_id"] == VENUE_SYNC_RUN_ID
