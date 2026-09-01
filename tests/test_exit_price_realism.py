"""The rehearsal must measure the exit it would really get.

Two defects made the single-leg exit -- the most expensive path in the
strategy -- unmeasurable in a rehearsal, which is the only surface on which
it is safe to measure anything:

1. `shadow_run` pinned `single_buy_grace_sec` to 0.0 unconditionally, so the
   configured grace (45s in the operator's `.env`) never reached `_route_pair`.
   Every stranded leg was dumped on the first pass after the fill, and the knob
   could not be swept.

2. The rehearsal's market SELL echoed back the limit floor it was handed
   (`best_bid - MAX_SELL_SLIPPAGE`, a flat 2c) as though that were the fill.
   A real market SELL rests against the bid ladder and takes the top of the
   book first. Booking the floor charged every exit a 2c haircut that the
   venue never took, which is most of the "2-3c per stranded leg" the run
   reports.
"""
import pytest

from core_brain.order_registry import OrderRegistry, init_db


@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    return OrderRegistry(db_path=db), db


# --- 1. the configured grace must survive into the rehearsal --------------

def test_shadow_run_keeps_the_configured_single_buy_grace(monkeypatch):
    """A rehearsal that discards the grace cannot sweep the grace."""
    monkeypatch.setenv("HUNTER_SINGLE_BUY_GRACE_SEC", "45")

    from core_brain.config import load
    from core_brain.shadow_run import shadow_cfg

    assert load().single_buy_grace_sec == 45.0
    assert shadow_cfg().single_buy_grace_sec == 45.0


def test_shadow_run_grace_still_defaults_to_zero_when_unset(monkeypatch):
    """Unset stays the old baseline, so existing rehearsals do not move."""
    monkeypatch.delenv("HUNTER_SINGLE_BUY_GRACE_SEC", raising=False)

    from core_brain.shadow_run import shadow_cfg

    assert shadow_cfg().single_buy_grace_sec == 0.0


# --- 2. the rehearsed SELL must fill against the book ---------------------

def _args(token_id, amount, price):
    from py_clob_client_v2.clob_types import MarketOrderArgsV2
    return MarketOrderArgsV2(token_id=token_id, amount=amount, side="SELL",
                             price=price)


def _client(registry, book):
    from core_brain.shadow_exec import ShadowExecutionClient, ensure_shadow_tables
    reg, db = registry
    ensure_shadow_tables(db)
    return ShadowExecutionClient(reg, db, book_fn=lambda h, t: book)


def test_rehearsed_sell_takes_the_touch_not_the_slippage_floor(registry):
    """5 shares into 20 at the touch fill at the touch, not at the floor."""
    client = _client(registry, {"bids": {0.66: 20.0, 0.65: 30.0}, "asks": {}})

    resp = client.create_and_post_market_order(_args("tok-dn", 5.0, 0.64))

    assert resp["size"] == 5.0
    assert resp["price"] == pytest.approx(0.66)


def test_rehearsed_sell_walks_down_the_ladder_when_the_touch_is_thin(registry):
    """Depth short at the touch pays a real, weighted concession."""
    client = _client(registry, {"bids": {0.66: 2.0, 0.65: 10.0}, "asks": {}})

    resp = client.create_and_post_market_order(_args("tok-dn", 5.0, 0.64))

    # 2 @ 0.66 + 3 @ 0.65 = 3.27 over 5 shares
    assert resp["price"] == pytest.approx(3.27 / 5.0)


def test_rehearsed_sell_never_fills_below_the_floor_it_was_given(registry):
    """The floor is a limit. Depth beneath it is not ours to take."""
    client = _client(registry, {"bids": {0.66: 1.0, 0.60: 100.0}, "asks": {}})

    resp = client.create_and_post_market_order(_args("tok-dn", 5.0, 0.64))

    assert resp["size"] == pytest.approx(1.0)
    assert resp["price"] == pytest.approx(0.66)


def test_rehearsed_sell_falls_back_to_the_floor_without_a_book(registry):
    """No book means no better information -- keep the conservative floor."""
    client = _client(registry, {"bids": {}, "asks": {}})

    resp = client.create_and_post_market_order(_args("tok-dn", 5.0, 0.64))

    assert resp["price"] == pytest.approx(0.64)
    assert resp["size"] == pytest.approx(5.0)


# --- 3. the close must record what the venue reported --------------------

def test_exit_close_records_the_achieved_price_when_the_venue_reports_one():
    """Booking the floor when the response carries a better fill understates
    proceeds, and the understatement is the headline of every rehearsal."""
    from core_brain.single_buy_saver import _exit_fill_price

    assert _exit_fill_price({"price": 0.66, "size": 5.0}, 0.64) == pytest.approx(0.66)


def test_exit_close_keeps_the_floor_when_the_venue_reports_no_price():
    """The live SDK response carries no fills, so the floor stays the record."""
    from core_brain.single_buy_saver import _exit_fill_price

    assert _exit_fill_price({"success": True}, 0.64) == pytest.approx(0.64)
    assert _exit_fill_price(None, 0.64) == pytest.approx(0.64)


def test_exit_close_never_records_a_price_better_than_reported_is_absurd():
    """A response price at or below the floor is still the truth: record it."""
    from core_brain.single_buy_saver import _exit_fill_price

    assert _exit_fill_price({"price": 0.63}, 0.64) == pytest.approx(0.63)


def test_exit_close_records_a_short_fill_as_short():
    """Retiring shares the venue never sold leaves a naked leg reading closed."""
    from core_brain.single_buy_saver import _exit_fill_size

    assert _exit_fill_size({"size": 1.0}, 5.0) == pytest.approx(1.0)


def test_exit_close_never_records_more_than_it_asked_to_sell():
    """An oversell is the one error on this path that cannot be undone."""
    from core_brain.single_buy_saver import _exit_fill_size

    assert _exit_fill_size({"size": 9.0}, 5.0) == pytest.approx(5.0)


def test_exit_close_keeps_the_requested_size_when_the_venue_is_silent():
    from core_brain.single_buy_saver import _exit_fill_size

    assert _exit_fill_size({"success": True}, 5.0) == pytest.approx(5.0)
    assert _exit_fill_size(None, 5.0) == pytest.approx(5.0)
