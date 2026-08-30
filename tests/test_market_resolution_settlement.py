"""Regression tests for shadow settlement accounting gaps.

The settlement PnL in `book_shadow_settlement` used to book only the clean
winning-hold case: a run left holding only the LOSING side never wrote a loss
(proceeds 0), and a condition that had seen *any* close (a merge or exit) was
skipped entirely, so genuinely-remaining leftover shares stayed unattributed.
The wins/losses gap made an overnight validation verdict rosier than the true
PnL. These guard the fix.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from core_brain.market_resolution import (
    MarketEndState,
    book_shadow_settlement,
    sweep_market_resolutions,
)
from core_brain.order_registry import (
    CloseRecord,
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
)

RUN = "test-run"


def _registry(tmp_path: Path) -> OrderRegistry:
    return OrderRegistry(tmp_path / "shadow.db", run_id=RUN)


def _buy(reg: OrderRegistry, cid: str, token: str, size: float, price: float,
         pair_id: str | None = None) -> None:
    oid = str(uuid.uuid4())
    reg.create_order(OrderRecord(
        id=oid, condition_id=cid, token_id=token, side="BUY",
        price=price, original_size=size, status="filled",
        posted_ts=1, last_polled_ts=1, pair_id=pair_id,
    ))
    reg.record_fill(FillRecord(
        trade_id="shadow-" + uuid.uuid4().hex[:12],
        order_uuid=oid, size=size, price=price,
    ))


def _quote(reg: OrderRegistry, cid: str, token: str, side: str) -> None:
    # side must be the OUTCOME ("UP"/"DOWN"), not the book side: that is what
    # maps any held token to a winner/loser at settlement.
    reg.log_quote(QuoteRecord(
        ts=1, condition_id=cid, token_id=token, side=side, price=1.0, size=0.0,
    ))


def _merges(reg: OrderRegistry, cid: str, shares: float,
            up_cost_removed: float, dn_cost_removed: float) -> None:
    reg.log_close(CloseRecord(
        ts=2, condition_id=cid, method="shadow_merge", shares=shares,
        up_price=1.0, up_cost_removed=up_cost_removed,
        dn_cost_removed=dn_cost_removed, cost_basis=up_cost_removed + dn_cost_removed,
        proceeds=shares * 1.0, realized_pnl=(shares * 1.0) - (up_cost_removed + dn_cost_removed),
        run_id=RUN,
    ))


def _state(winner_token: str) -> MarketEndState:
    return MarketEndState(
        condition_id="c1", resolved=True,
        winner_token="UP" if winner_token != "up" else "UP",
        winning_token_id=winner_token,
    )


def _closes(reg: OrderRegistry) -> list[dict]:
    return reg.get_all_closes()


def _settlement_closes(reg: OrderRegistry) -> list[dict]:
    return [c for c in reg.get_all_closes() if c.get("method") == "shadow_settlement"]


def test_loser_only_books_a_realized_loss(tmp_path: Path):
    """A run left holding only the losing side must write a realized loss."""
    reg = _registry(tmp_path)
    _quote(reg, "c1", "up", "UP")
    _quote(reg, "c1", "dn", "DOWN")
    # Buy only the LOSING (dn) side: 10 shares @ 0.60 -> $6.00 cost.
    _buy(reg, "c1", "dn", 10.0, 0.60)

    booked = book_shadow_settlement(reg, "c1", _state("up"), run_id=RUN)

    assert booked is not None, "a pure loser must still be settled, not skipped"
    assert booked["realized_pnl"] == -6.0
    closes = _settlement_closes(reg)
    assert len(closes) == 1
    assert closes[0]["realized_pnl"] == -6.0
    assert closes[0]["proceeds"] == 0.0


def test_winner_only_books_profit(tmp_path: Path):
    reg = _registry(tmp_path)
    _quote(reg, "c1", "up", "UP")
    _quote(reg, "c1", "dn", "DOWN")
    _buy(reg, "c1", "up", 10.0, 0.55)  # winner, 10 @ 0.55 -> cost $5.50

    booked = book_shadow_settlement(reg, "c1", _state("up"), run_id=RUN)

    assert booked is not None
    assert booked["realized_pnl"] == 4.5  # 10 * 1.00 - 5.50
    assert booked["proceeds"] == 10.0


def test_leftover_after_merge_is_still_settled(tmp_path: Path):
    """A pair that merged PARTIALLY still settles the remainder, not skipped."""
    reg = _registry(tmp_path)
    _quote(reg, "c1", "up", "UP")
    _quote(reg, "c1", "dn", "DOWN")
    _buy(reg, "c1", "up", 10.0, 0.55)     # $5.50
    _buy(reg, "c1", "dn", 10.0, 0.40)     # $4.00
    # Merge 5 shares of each leg: up cost removed 2.75, dn cost removed 2.00.
    # Remaining net held = up 5 (cost 2.75) + dn 5 (cost 2.00) = 4.75.
    _merges(reg, "c1", 5.0, 2.75, 2.00)

    booked = book_shadow_settlement(reg, "c1", _state("up"), run_id=RUN)

    # Winner side up still holds 5 -> proceeds 5.00; cost of ALL still held = 4.75.
    assert booked is not None, "leftover shares after a partial merge must settle"
    assert booked["realized_pnl"] == 5.0 - 4.75


def test_fully_merged_books_nothing_and_does_not_double_count(tmp_path: Path):
    """Once every share is merged away, settlement must book nothing."""
    reg = _registry(tmp_path)
    _quote(reg, "c1", "up", "UP")
    _quote(reg, "c1", "dn", "DOWN")
    _buy(reg, "c1", "up", 10.0, 0.55)
    _buy(reg, "c1", "dn", 10.0, 0.40)
    _merges(reg, "c1", 10.0, 5.50, 4.00)  # merge the entire position

    booked = book_shadow_settlement(reg, "c1", _state("up"), run_id=RUN)

    assert booked is None, "a fully merged position has nothing left to redeem"
    assert len(_settlement_closes(reg)) == 0


def test_settlement_is_idempotent(tmp_path: Path):
    """Booking a settlement dissolves the position so it is never re-booked."""
    reg = _registry(tmp_path)
    _quote(reg, "c1", "up", "UP")
    _quote(reg, "c1", "dn", "DOWN")
    _buy(reg, "c1", "up", 10.0, 0.55)

    first = book_shadow_settlement(reg, "c1", _state("up"), run_id=RUN)
    second = book_shadow_settlement(reg, "c1", _state("up"), run_id=RUN)

    assert first is not None
    assert second is None, "the settled position must not be booked a second time"
    assert len(_settlement_closes(reg)) == 1


def test_unknown_winner_books_nothing(tmp_path: Path):
    """No winner determinable -> cannot price redemption -> no fabricated close."""
    reg = _registry(tmp_path)
    _quote(reg, "c1", "up", "UP")
    _quote(reg, "c1", "dn", "DOWN")
    _buy(reg, "c1", "up", 10.0, 0.55)

    state = MarketEndState(condition_id="c1", resolved=True, winning_token_id=None)
    assert book_shadow_settlement(reg, "c1", state, run_id=RUN) is None
    assert len(_settlement_closes(reg)) == 0


def test_single_leg_exit_keeps_other_leg_inventory(tmp_path: Path):
    """A single-buy-exit on the UP leg must not discard the DOWN loser.

    Only the UP token carries a quote mapping; the DOWN token has fills but no
    quoted side. A one-leg exit with up_price set removes only UP, so the still-
    held DOWN loser must survive to settlement (a loser that is booked), not be
    wiped by an over-eager close-netting guard.
    """
    reg = _registry(tmp_path)
    _quote(reg, "c1", "up", "UP")           # only UP is mapped in quotes
    _buy(reg, "c1", "up", 10.0, 0.55)        # $5.50 -> exited below
    _buy(reg, "c1", "dn", 10.0, 0.40)        # $4.00 DOWN loser, still held
    # Exit the whole UP leg (single_buy_exit names it via up_price).
    reg.log_close(CloseRecord(
        ts=2, condition_id="c1", method="single_buy_exit", shares=10.0,
        up_price=0.55, up_cost_removed=5.5, cost_basis=5.5, proceeds=5.5,
        realized_pnl=0.0, run_id=RUN,
    ))

    booked = book_shadow_settlement(reg, "c1", _state("up"), run_id=RUN)

    # Winner is UP but UP was fully exited: only the DOWN loser remains,
    # worth $0.00 -> a realized loss of -$4.00, booked (not discarded).
    assert booked is not None
    assert booked["realized_pnl"] == -4.0


def test_unreachable_market_is_backed_off_not_re_fetched(tmp_path: Path):
    """A gamma failure must not be hammered every rotation."""
    reg = _registry(tmp_path)
    _buy(reg, "c1", "up", 10.0, 0.55)

    calls = {"n": 0}

    def _fetch(gh: str, cid: str) -> MarketEndState:
        calls["n"] += 1
        return MarketEndState(condition_id=cid, unreachable=True)

    t = [1000.0]

    def _now() -> float:
        return t[0]

    db = str(reg.db_path)
    # First rotation: real fetch attempt -> unreachable -> enters backoff.
    r1 = sweep_market_resolutions(
        reg, db, markets=[], run_id=RUN, fetch_state=_fetch, now_fn=_now,
    )
    assert r1[0].action == "unreachable"
    assert calls["n"] == 1

    # Second rotation, still inside the backoff window: no gamma call at all.
    r2 = sweep_market_resolutions(
        reg, db, markets=[], run_id=RUN, fetch_state=_fetch, now_fn=_now,
    )
    assert calls["n"] == 1, "unreachable cid must not be re-fetched inside backoff"
    assert r2[0].action == "unreachable"

    # After the backoff window elapses, the retry proceeds.
    t[0] += 61.0
    r3 = sweep_market_resolutions(
        reg, db, markets=[], run_id=RUN, fetch_state=_fetch, now_fn=_now,
    )
    assert calls["n"] == 2, "after backoff the market is retried"
    assert r3[0].action == "unreachable"


def test_already_resolved_without_inventory_is_not_refetched(tmp_path: Path):
    """A resolved market whose position is fully gone needs no gamma re-read."""
    from core_brain.order_registry import ResolutionRecord
    reg = _registry(tmp_path)
    _quote(reg, "c1", "up", "UP")
    _quote(reg, "c1", "dn", "DOWN")
    _buy(reg, "c1", "up", 10.0, 0.55)
    _buy(reg, "c1", "dn", 10.0, 0.40)
    _merges(reg, "c1", 10.0, 5.50, 4.00)  # entire position merged away
    reg.log_resolution(ResolutionRecord(condition_id="c1", winning_token="Up", resolved_ts=1, run_id=RUN))

    calls = {"n": 0}

    def _fetch(gh: str, cid: str) -> MarketEndState:
        calls["n"] += 1
        return MarketEndState(condition_id=cid, resolved=True, winning_token_id="up")

    results = sweep_market_resolutions(
        reg, str(reg.db_path), markets=[], run_id=RUN,
        fetch_state=_fetch, now_fn=lambda: 2.0, book_settlement=True,
    )
    assert len(results) == 1
    assert results[0].action == "already_resolved"
    assert calls["n"] == 0, "empty position must not trigger a gamma re-read"