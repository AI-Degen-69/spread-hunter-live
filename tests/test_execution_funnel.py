"""Where the bot falls off between a resting quote and a merged pair.

Issue #90 asks the analytics surface to answer one question in a single
glance: of everything we quoted, how much reached a merge, and at which step
did the rest disappear. The screener funnel already on Tab 3 answers a
different question -- which markets passed the volume, depth and spread gates
-- and says nothing about execution once a market is graduated.

`report()` already carries every ingredient (`quotes`, `fills`, `closes` and
the skip-reason census) but never assembles them into stages, so the board has
no way to say "we quoted 40 legs, filled 6, merged 1". These tests pin the
`execution_funnel` field that does.

Journeys under test:
1. As the Owner, I see how many legs reached each stage: quoted, filled,
   closed, merged.
2. As the Owner, I see the drop-off between consecutive stages, so the worst
   step names itself.
3. As the Owner, an accounting-only `venue_sync` row is not a trade and does
   not inflate the closed stage.
4. As the Owner, the reasons the engine declined to quote ride along with the
   funnel rather than living in a separate panel.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from core_brain import kpi as kpi_mod
from core_brain.kpi import report
from core_brain.order_registry import (
    SCHEMA, CloseRecord, FillRecord, MarketEventRecord, OrderRecord,
    OrderRegistry, QuoteRecord,
)

RUN = "run-funnel"
CID = "0xfunnel"
CID_B = "0xfunnel-b"
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


def _quote(reg: OrderRegistry, token: str, price: float, size: float,
           filled: float = 0.0, cid: str = CID) -> None:
    reg.log_quote(QuoteRecord(
        ts=time.time(), condition_id=cid, token_id=token, side="BUY",
        price=price, size=size, filled=filled, run_id=RUN,
    ))


def _fill_leg(reg: OrderRegistry, uid: str, token: str, shares: float,
              price: float, cid: str = CID, pair_id: str = "pair-1") -> None:
    now = int(time.time())
    reg.create_order(OrderRecord(
        id=uid, condition_id=cid, token_id=token, side="BUY", price=price,
        original_size=shares, status="filled", posted_ts=now, last_polled_ts=now,
        order_id=f"venue-{uid}", pair_id=pair_id, run_id=RUN,
    ))
    reg.record_fill(FillRecord(trade_id=f"trade-{uid}", order_uuid=uid,
                               size=shares, price=price, venue_ts=now * 1000,
                               run_id=RUN))


def _close(reg: OrderRegistry, method: str, shares: float, cost_basis: float,
           cid: str = CID, tx: str = "pair-1") -> None:
    reg.log_close(CloseRecord(
        ts=time.time(), condition_id=cid, method=method, shares=shares,
        cost_basis=cost_basis, proceeds=shares * 1.0, fee=0.0, gas=0.0,
        realized_pnl=shares - cost_basis,
        up_cost_removed=cost_basis / 2.0, dn_cost_removed=cost_basis / 2.0,
        tx_hash=tx, run_id=RUN,
    ))


def _funnel(db_file) -> dict:
    return report(db_path=str(db_file), run_id="all")["execution_funnel"]


def _stage(funnel: dict, key: str) -> dict:
    for stage in funnel["stages"]:
        if stage["key"] == key:
            return stage
    raise AssertionError(f"stage {key!r} missing from {[s['key'] for s in funnel['stages']]}")


# -- Stage counts ------------------------------------------------------------

def test_every_stage_from_quoted_to_merged_is_counted(temp_db):
    # Arrange -- four legs quoted, two of them filled, one pair merged.
    reg = OrderRegistry(temp_db)
    _quote(reg, TOK_UP, 0.48, 10.0, filled=10.0)
    _quote(reg, TOK_DN, 0.50, 10.0, filled=10.0)
    _quote(reg, TOK_UP, 0.47, 10.0)
    _quote(reg, TOK_DN, 0.49, 10.0)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)
    _close(reg, "shadow_merge", shares=10.0, cost_basis=9.80)

    # Act
    funnel = _funnel(temp_db)

    # Assert -- the stages read in pipeline order and count what reached each.
    assert [s["key"] for s in funnel["stages"]] == [
        "quoted", "filled", "closed", "merged",
    ]
    assert _stage(funnel, "quoted")["legs"] == 4
    assert _stage(funnel, "filled")["legs"] == 2
    assert _stage(funnel, "closed")["legs"] == 1
    assert _stage(funnel, "merged")["legs"] == 1


def test_each_stage_counts_the_markets_that_reached_it(temp_db):
    # Arrange -- two markets quoted, only one of them ever fills.
    reg = OrderRegistry(temp_db)
    _quote(reg, TOK_UP, 0.48, 10.0, filled=10.0)
    _quote(reg, TOK_UP, 0.47, 10.0, cid=CID_B)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)

    # Act
    funnel = _funnel(temp_db)

    # Assert
    assert _stage(funnel, "quoted")["markets"] == 2
    assert _stage(funnel, "filled")["markets"] == 1


# -- Drop-off ----------------------------------------------------------------

def test_the_drop_off_between_stages_names_the_worst_step(temp_db):
    # Arrange -- ten legs quoted, two filled, one closed as a merge. The
    # quoted-to-filled step loses eight legs and is the worst by far.
    reg = OrderRegistry(temp_db)
    for i in range(8):
        _quote(reg, TOK_UP if i % 2 == 0 else TOK_DN, 0.47, 10.0)
    _quote(reg, TOK_UP, 0.48, 10.0, filled=10.0)
    _quote(reg, TOK_DN, 0.50, 10.0, filled=10.0)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _fill_leg(reg, "o-dn", TOK_DN, 10.0, 0.50)
    _close(reg, "shadow_merge", shares=10.0, cost_basis=9.80)

    # Act
    funnel = _funnel(temp_db)

    # Assert -- one entry per consecutive pair, with what was lost and the
    # share of the earlier stage that survived.
    assert [(d["from"], d["to"]) for d in funnel["drop_off"]] == [
        ("quoted", "filled"), ("filled", "closed"), ("closed", "merged"),
    ]
    first = funnel["drop_off"][0]
    assert first["lost"] == 8
    assert first["retained_pct"] == pytest.approx(20.0)
    assert funnel["worst_step"]["from"] == "quoted"
    assert funnel["worst_step"]["to"] == "filled"
    assert funnel["worst_step"]["lost"] == 8


def test_a_stage_nothing_reached_reports_no_retention_rather_than_zero(temp_db):
    # Arrange -- nothing was ever quoted, so no percentage is defined. A run
    # that has not started must not read as a 0% funnel, which is a verdict.
    OrderRegistry(temp_db)

    # Act
    funnel = _funnel(temp_db)

    # Assert
    assert _stage(funnel, "quoted")["legs"] == 0
    assert funnel["drop_off"][0]["retained_pct"] is None
    assert funnel["worst_step"] is None


# -- What is not a trade -----------------------------------------------------

def test_an_accounting_sync_row_is_not_a_close(temp_db):
    # Arrange -- `venue_sync` closes are the account sweep's bookkeeping, not
    # an exit the strategy took. Counting them would report a closed stage on
    # a run that never closed anything.
    reg = OrderRegistry(temp_db)
    _quote(reg, TOK_UP, 0.48, 10.0, filled=10.0)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _close(reg, "venue_sync", shares=10.0, cost_basis=4.80, tx="sync-1")

    # Act
    funnel = _funnel(temp_db)

    # Assert
    assert _stage(funnel, "closed")["legs"] == 0
    assert _stage(funnel, "merged")["legs"] == 0


def test_a_one_sided_exit_closes_without_merging(temp_db):
    # Arrange -- a stranded leg dumped at the bid is a close, and explicitly
    # not a merge. The gap between the two stages is the single-buy rate.
    reg = OrderRegistry(temp_db)
    _quote(reg, TOK_UP, 0.48, 10.0, filled=10.0)
    _fill_leg(reg, "o-up", TOK_UP, 10.0, 0.48)
    _close(reg, "single_buy_exit", shares=10.0, cost_basis=4.80, tx="exit-1")

    # Act
    funnel = _funnel(temp_db)

    # Assert
    assert _stage(funnel, "closed")["legs"] == 1
    assert _stage(funnel, "merged")["legs"] == 0


# -- Why the engine declined -------------------------------------------------

def test_the_reasons_the_engine_declined_ride_with_the_funnel(temp_db):
    # Arrange -- two cycles blocked on the pair cost, one on the spread.
    reg = OrderRegistry(temp_db)
    now = time.time()
    for i, code in enumerate(("PAIR_TOO_EXPENSIVE", "PAIR_TOO_EXPENSIVE", "SPREAD_TOO_WIDE")):
        reg.log_market_event(MarketEventRecord(
            ts=now + i, condition_id=CID, kind="BLOCKED", reason_code=code,
            reason=code.replace("_", " ").title(), run_id=RUN,
        ))

    # Act
    funnel = _funnel(temp_db)

    # Assert -- ranked, worst first, so the top blocker reads at a glance.
    assert funnel["declined"][0]["reason"] == "PAIR_TOO_EXPENSIVE"
    assert funnel["declined"][0]["cycles"] == 2
    assert {d["reason"] for d in funnel["declined"]} == {
        "PAIR_TOO_EXPENSIVE", "SPREAD_TOO_WIDE",
    }
