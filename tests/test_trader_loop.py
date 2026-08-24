"""The live fleet loop: decide -> submit -> reconcile, reusing the risk gates.

`engine.trader_loop` runs the live rotation through
`engine.quotes.evaluate_market_quote`, which wraps `decide_quotes`. The strategy that
trades is `engine.quotes.decide_quotes` -- already proven in the paper run and
already wired to the live risk gates -- so this module must add NOTHING new to
the decision. Its only new jobs are:

1. `plan_orders`: turn "what we want resting" into "what to cancel / submit"
   without resubmitting an order already resting at the desired price.
2. The rotation loop, which must never let one market's error stop the others,
   and must never touch the venue at all in dry-run.

These tests drive exactly those two seams. Everything that talks to the venue
(fetch market, fetch books, decide, submit, cancel, reconcile, sweep) is
injected, so the loop's behavior is tested without a network.
"""

from __future__ import annotations

import pytest

from core_brain.trader_loop import LiveFleetResult, VenueSeam, plan_orders, run
from core_brain.quotes import QuoteIntent


def _intent(side="UP", token="tok-up", price=0.60, size=5):
    return QuoteIntent(side=side, token_id=token, price=price, size=size,
                       mid=price + 0.01, edge_vs_mid=0.01)


def _open(token="tok-up", price=0.60, oid="o1", status="open"):
    return {"token_id": token, "price": price, "order_id": oid,
            "side": "BUY", "status": status}


class TestPlanOrders:
    def test_keeps_matching_and_submits_nothing_when_fully_resting(self):
        open_orders = [_open()]
        intents = [_intent()]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == []
        assert to_submit == []

    def test_cancels_stale_order_and_submits_new_price(self):
        open_orders = [_open(price=0.61)]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == open_orders
        assert [i.price for i in to_submit] == [0.60]

    def test_submits_intent_with_no_open_order(self):
        open_orders = []
        intents = [_intent()]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == []
        assert to_submit == intents

    def test_cancels_orders_for_a_side_we_no_longer_quote(self):
        # UP is still wanted; the DOWN leg is gone from the intents.
        open_orders = [_open("tok-up", 0.60, "o1"), _open("tok-dn", 0.40, "o2")]
        intents = [_intent(side="UP", token="tok-up", price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert [o["order_id"] for o in to_cancel] == ["o2"]
        assert to_submit == []

    def test_price_epsilon_treats_sub_tick_repricing_as_same(self):
        # A venue rounding jitter below a tick must not churn cancel+resubmit.
        open_orders = [_open(price=0.6005)]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents, price_eps=0.001)
        assert to_cancel == []
        assert to_submit == []


class FakeMarket:
    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False


class TestRunLoop:
    def _run(self, live, intents, fetch_raises=False, once=True):
        calls = {"submitted": [], "cancelled": [], "reconciled": 0, "swept": 0}

        def fake_fetch_market(cid):
            if fetch_raises:
                raise RuntimeError("venue down")
            return FakeMarket(cid)

        def fake_books(clob_host, token):
            return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                    "bids": {0.59: 100}, "asks": {0.61: 100}}

        def fake_decide(cfg, up, dn, inv, t_rem, wf):
            return list(intents), ("" if intents else "declined")

        def fake_submit(client, registry, market, intents, cfg):
            calls["submitted"].append([i.side for i in intents])
            return 0

        def fake_cancel(client, registry, orders):
            calls["cancelled"].append([o["order_id"] for o in orders])
            return len(orders)

        def fake_reconcile(client, registry, maker):
            calls["reconciled"] += 1
            return None

        def fake_sweep():
            calls["swept"] += 1

        seam = VenueSeam(
            client=object(),
            fetch_market=fake_fetch_market,
            fetch_books=fake_books,
            decide=fake_decide,
            submit_fn=fake_submit,
            cancel_fn=fake_cancel,
            reconcile_fn=fake_reconcile,
            sweep_fn=fake_sweep,
        )
        results = run(
            seam, interval=0.0, once=once, live=live,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        return results, calls

    def test_dry_run_decides_but_never_submits_or_cancels(self):
        results, calls = self._run(live=False, intents=[_intent(), _intent("DOWN", "tok-dn", 0.40)])
        assert results[0].status == "DRY_RUN"
        assert calls["submitted"] == []
        assert calls["cancelled"] == []
        assert calls["reconciled"] == 1  # reconcile is read-only; still safe in dry-run

    def test_live_submits_decided_intents(self):
        results, calls = self._run(live=True, intents=[_intent(), _intent("DOWN", "tok-dn", 0.40)])
        assert results[0].status == "QUOTED"
        assert calls["submitted"] == [["UP", "DOWN"]]

    def test_live_cancels_stale_when_decide_returns_nothing(self):
        # no intents -> every open order is stale. Exercise via a fake open set.
        results, calls = self._run(live=True, intents=[])
        assert results[0].status == "DECLINED"
        # No open orders were injected, so nothing to cancel; the point is the
        # loop still completed without raising.
        assert calls["submitted"] == []

    def test_one_market_error_does_not_stop_the_rotation(self):
        results, calls = self._run(live=True, intents=[_intent()], fetch_raises=True)
        assert results[0].status == "ERROR"
        assert calls["reconciled"] == 1

    def test_sweep_runs_on_first_cycle(self):
        results, calls = self._run(live=False, intents=[])
        assert calls["swept"] == 1

    def test_submit_error_does_not_stop_the_rotation(self):
        # A submit failure must degrade the market to ERROR, not kill the loop:
        # the second market is still visited and reconcile still runs.
        calls = {"reconciled": 0}

        def fetch_market(cid):
            return FakeMarket(cid)

        def fetch_books(clob_host, token):
            return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                    "bids": {0.59: 100}, "asks": {0.61: 100}}

        def decide(cfg, up, dn, inv, t_rem, wf):
            return [_intent()], ""

        def submit_fn(client, registry, market, intents, cfg):
            raise RuntimeError("venue rejected")

        def cancel_fn(client, registry, orders):
            return 0

        def reconcile_fn(client, registry, maker):
            calls["reconciled"] += 1

        seam = VenueSeam(
            client=object(), fetch_market=fetch_market, fetch_books=fetch_books,
            decide=decide, submit_fn=submit_fn, cancel_fn=cancel_fn,
            reconcile_fn=reconcile_fn, sweep_fn=lambda: None,
        )
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xa"), FakeMarket("0xb")],
            sleep_fn=lambda s: None,
        )
        assert [r.status for r in results] == ["ERROR", "ERROR"]
        assert calls["reconciled"] == 1

    def test_fleet_state_is_injected_into_decide_cfg(self):
        seen = {}

        def decide(cfg, up, dn, inv, t_rem, wf):
            seen["fleet_naked_usd"] = cfg.fleet_naked_usd
            return [], "declined"

        def fetch_market(cid):
            return FakeMarket(cid)

        def fetch_books(clob_host, token):
            return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                    "bids": {0.59: 100}, "asks": {0.61: 100}}

        run(
            VenueSeam(
                client=object(),
                fetch_market=fetch_market, fetch_books=fetch_books, decide=decide,
                submit_fn=lambda *a, **k: 0, cancel_fn=lambda *a, **k: 0,
                reconcile_fn=lambda *a, **k: None, sweep_fn=lambda: None,
                fleet_state_fn=lambda r: {"fleet_naked_usd": 12.5},
            ),
            interval=0.0, once=True, live=False,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert seen["fleet_naked_usd"] == 12.5

    def test_open_orders_fn_includes_open_and_partial_orders(self):
        from unittest.mock import MagicMock
        from core_brain.trader_loop import _make_open_orders_fn

        registry = MagicMock()
        o_open = MagicMock(condition_id="0xabc", status="open", token_id="tok-up", price=0.55, order_id="v1", id="row1", side="BUY")
        o_partial = MagicMock(condition_id="0xabc", status="partial", token_id="tok-dn", price=0.45, order_id="v2", id="row2", side="BUY")
        o_filled = MagicMock(condition_id="0xabc", status="filled", token_id="tok-up", price=0.55, order_id="v3", id="row3", side="BUY")
        o_cancelled = MagicMock(condition_id="0xabc", status="cancelled", token_id="tok-dn", price=0.45, order_id="v4", id="row4", side="BUY")
        o_other_mkt = MagicMock(condition_id="0xdef", status="open", token_id="tok-dn", price=0.45, order_id="v5", id="row5", side="BUY")

        registry.get_active_orders.return_value = [o_open, o_partial, o_filled, o_cancelled, o_other_mkt]

        fn = _make_open_orders_fn(registry)
        market = FakeMarket("0xabc")
        res = fn(market)

        assert len(res) == 2
        assert {r["order_id"] for r in res} == {"v1", "v2"}
        assert {r["status"] for r in res} == {"open", "partial"}

    def test_run_reloads_markets_via_markets_fn_each_cycle(self):
        call_count = [0]
        m1 = FakeMarket("0x1")
        m2 = FakeMarket("0x2")

        def market_supplier():
            call_count[0] += 1
            return [m1] if call_count[0] == 1 else [m2]

        visited = []

        def fake_fetch_market(cid):
            visited.append(cid)
            return FakeMarket(cid)

        seam = VenueSeam(
            client=object(),
            fetch_market=fake_fetch_market,
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

        cycles = [0]
        def sleep_stop(s):
            cycles[0] += 1
            if cycles[0] >= 2:
                raise KeyboardInterrupt()

        run(
            seam, interval=0.0, once=False, live=False,
            markets_fn=market_supplier,
            sleep_fn=sleep_stop,
        )

        assert visited == ["0x1", "0x2"]

    def test_run_cancels_orders_for_dropped_markets(self):
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_dropped_open = MagicMock(condition_id="0xdropped", status="open", token_id="tok-up", price=0.55, order_id="v_drop_open", id="row_drop_1", side="BUY")
        o_dropped_partial = MagicMock(condition_id="0xdropped", status="partial", token_id="tok-dn", price=0.45, order_id="v_drop_partial", id="row_drop_2", side="BUY")
        registry.get_active_orders.return_value = [o_dropped_open, o_dropped_partial]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        seam = VenueSeam(
            client=object(),
            registry=registry,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=fake_cancel,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xactive")],
            sleep_fn=lambda s: None,
        )

        # Only the `open` leg is cancelled. The `partial` has already bought
        # shares; cancelling it would strand them as a naked single buy that this
        # loop has no exit path for.
        assert len(cancelled_calls) == 1
        order_ids = {o["order_id"] for o in cancelled_calls[0]}
        assert order_ids == {"v_drop_open"}
        assert any(r.condition_id == "0xdropped"
                   and r.status == "CANCELLED"
                   and r.why == "dropped_market_cancelled" for r in results)
        assert any(r.condition_id == "0xdropped"
                   and r.status == "WARNED"
                   and r.why == "dropped_market_partial_retained" for r in results)

    def test_markets_fn_error_retains_last_successful_markets(self):
        from unittest.mock import MagicMock

        call_count = [0]
        m_dynamic = FakeMarket("0xdynamic")

        def failing_market_supplier():
            call_count[0] += 1
            if call_count[0] == 1:
                return [m_dynamic]
            raise RuntimeError("API fetch error")

        visited = []
        def fake_fetch_market(cid):
            visited.append(cid)
            return FakeMarket(cid)

        registry = MagicMock()
        o_dynamic = MagicMock(condition_id="0xdynamic", status="open", token_id="tok-up", price=0.55, order_id="v_dyn", id="row_dyn", side="BUY")
        registry.get_active_orders.return_value = [o_dynamic]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        seam = VenueSeam(
            client=object(),
            registry=registry,
            fetch_market=fake_fetch_market,
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=fake_cancel,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

        cycles = [0]
        def sleep_stop(s):
            cycles[0] += 1
            if cycles[0] >= 2:
                raise KeyboardInterrupt()

        run(
            seam, interval=0.0, once=False, live=True,
            markets=[FakeMarket("0xinitial")],
            markets_fn=failing_market_supplier,
            sleep_fn=sleep_stop,
        )

        # 0xdynamic visited in both cycles; because it remained current in cycle 2, its order was never cancelled as dropped
        assert visited == ["0xdynamic", "0xdynamic"]
        assert cancelled_calls == []

    def test_dropped_market_cancellation_isolates_errors(self):
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_drop1 = MagicMock(condition_id="0xdrop1", status="open", token_id="tok-up", price=0.55, order_id="v1", id="r1", side="BUY")
        o_drop2 = MagicMock(condition_id="0xdrop2", status="open", token_id="tok-dn", price=0.45, order_id="v2", id="r2", side="BUY")
        registry.get_active_orders.return_value = [o_drop1, o_drop2]

        cancelled_cids = []
        def fake_cancel(client, reg, orders):
            cid = orders[0]["order_id"]
            if cid == "v1":
                raise RuntimeError("venue reject on drop1")
            cancelled_cids.append(cid)
            return len(orders)

        seam = VenueSeam(
            client=object(),
            registry=registry,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=fake_cancel,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xactive")],
            sleep_fn=lambda s: None,
        )

        # drop2 cancelled despite drop1 failing
        assert cancelled_cids == ["v2"]
        assert any(r.condition_id == "0xdrop1" and r.status == "ERROR" for r in results)
        assert any(r.condition_id == "0xdrop2" and r.status == "CANCELLED" for r in results)

    @staticmethod
    def _cleanup_seam(registry, cancel_fn):
        return VenueSeam(
            client=object(),
            registry=registry,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=cancel_fn,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

    def test_empty_markets_refresh_is_ignored_and_keeps_the_book(self):
        """A ranker cycle that graduates nothing must not cancel the whole book.

        `load_graduated_markets` raises on a missing, stale or malformed feed but
        returns cleanly for a well-formed `[]`. Adopting that empties the active
        universe, and every resting order then looks dropped.
        """
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_live = MagicMock(condition_id="0xlive", status="open", token_id="tok-up",
                           price=0.55, order_id="v_live", id="row_live", side="BUY")
        registry.get_active_orders.return_value = [o_live]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        visited = []
        seam = self._cleanup_seam(registry, fake_cancel)
        seam.fetch_market = lambda cid: (visited.append(cid) or FakeMarket(cid))

        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xlive")],
            markets_fn=lambda: [],
            sleep_fn=lambda s: None,
        )

        assert visited == ["0xlive"]
        assert cancelled_calls == []
        assert all(r.why != "dropped_market_cancelled" for r in results)

    def test_pending_orders_on_a_dropped_market_are_left_to_reconcile(self):
        """A pending row may have no venue id yet; orphan adoption owns it."""
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_pending = MagicMock(condition_id="0xdropped", status="pending",
                              token_id="tok-up", price=0.55, order_id=None,
                              id="row_pending", side="BUY")
        registry.get_active_orders.return_value = [o_pending]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        results = run(
            self._cleanup_seam(registry, fake_cancel),
            interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xactive")],
            sleep_fn=lambda s: None,
        )

        assert cancelled_calls == []
        assert all(r.condition_id != "0xdropped" for r in results)

    def test_registry_read_failure_in_cleanup_surfaces_as_a_result(self):
        """A cleanup that can no-op invisibly is worse than none."""
        from unittest.mock import MagicMock

        registry = MagicMock()
        registry.get_active_orders.side_effect = RuntimeError("db locked")

        results = run(
            self._cleanup_seam(registry, lambda *a, **k: 0),
            interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xactive")],
            sleep_fn=lambda s: None,
        )

        err = [r for r in results if r.why == "dropped_market_cleanup_failed"]
        assert len(err) == 1
        assert err[0].status == "ERROR"
        assert "db locked" in err[0].error
