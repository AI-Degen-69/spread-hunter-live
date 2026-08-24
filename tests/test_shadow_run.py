"""The shadow-run entrypoint: a watchable full-loop rehearsal that cannot spend.

`core_brain.shadow_run` reuses `core_brain.trader_loop.run` through the same
`VenueSeam` the live loop uses. It changes three things and nothing else: the
client cannot sign, the store is not the production registry, and the run stops
on a wall-clock deadline.

The tests that matter here are the ones about what shadow mode must NOT do.
"""
from __future__ import annotations

import pytest

from core_brain.trader_loop import VenueSeam, run
from core_brain.quotes import QuoteIntent


class FakeMarket:
    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False


def _books(clob_host, token):
    return {"token_id": token, "best_bid": 0.47, "best_ask": 0.49,
            "bids": {0.47: 100}, "asks": {0.49: 100}}


def _seam(**overrides):
    base = dict(
        client=object(),
        fetch_market=lambda cid: FakeMarket(cid),
        fetch_books=_books,
        decide=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
        submit_fn=lambda *a, **k: 0,
        cancel_fn=lambda *a, **k: 0,
        reconcile_fn=lambda *a, **k: None,
        sweep_fn=lambda: None,
    )
    base.update(overrides)
    return VenueSeam(**base)


class TestDeadline:
    """The time box.

    `trader_loop.run` has no deadline and must not grow one -- editing the live
    loop to serve a rehearsal is how a rehearsal feature reaches live. The stop
    is driven entirely from the injected `sleep_fn`.
    """

    def test_the_loop_stops_once_the_deadline_passes(self):
        from core_brain.shadow_run import make_deadline_sleep

        now = [1000.0]
        slept = []

        def clock():
            return now[0]

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        sleep_fn = make_deadline_sleep(
            deadline_ts=1000.0 + 12.0, clock=clock, sleep=sleep)

        results = run(
            _seam(), interval=5.0, once=False, live=False,
            markets=[FakeMarket("0xabc")], sleep_fn=sleep_fn,
        )

        # 5 + 5 sleeps land at t=1010; the third check is past t=1012 only after
        # the clamped final sleep, so the loop stops without overshooting.
        assert sum(slept) <= 12.0, f"slept past the deadline: {slept}"
        assert results, "the loop must return the last rotation, not nothing"

    def test_the_deadline_returns_results_rather_than_escaping_as_an_error(self):
        """Why `_Deadline` subclasses KeyboardInterrupt.

        `trader_loop.py` wraps the sleep call in `except KeyboardInterrupt:
        break` and nothing else. A plain Exception would propagate out of `run`
        uncaught and lose the rotation's results; a bare BaseException subclass
        would not be caught by that handler at all and would escape the same
        way. Subclassing KeyboardInterrupt walks through the loop's own
        designed clean exit.
        """
        from core_brain.shadow_run import make_deadline_sleep

        sleep_fn = make_deadline_sleep(
            deadline_ts=0.0, clock=lambda: 1.0, sleep=lambda s: None)

        results = run(
            _seam(), interval=5.0, once=False, live=False,
            markets=[FakeMarket("0xabc")], sleep_fn=sleep_fn,
        )

        # Returned, not raised: the rotation's results survive the deadline.
        # DECLINED because the fake `decide` returns no intents -- what matters
        # here is that `run` handed results back at all.
        assert [r.status for r in results] == ["DECLINED"]

    def test_the_deadline_signal_survives_a_blanket_except_exception(self):
        from core_brain.shadow_run import _Deadline

        with pytest.raises(_Deadline):
            try:
                raise _Deadline()
            except Exception:  # noqa: BLE001 - the point of the test
                pytest.fail("a blanket except Exception swallowed the deadline")

    def test_a_sleep_before_the_deadline_is_clamped_to_the_remaining_time(self):
        """A 5s interval with 2s left must not overshoot the time box by 3s."""
        from core_brain.shadow_run import make_deadline_sleep

        slept = []
        sleep_fn = make_deadline_sleep(
            deadline_ts=102.0, clock=lambda: 100.0, sleep=slept.append)

        sleep_fn(5.0)

        assert slept == [2.0]


class TestRunShadow:
    """The session: read-only client, own store, live config, nothing sent."""

    def _markets(self, n=1):
        return lambda max_markets=None: [FakeMarket(f"0x{i}") for i in range(n)]

    def test_the_production_registry_is_refused_as_the_store(self, tmp_path):
        """AGENTS.md: `data/orders.db` is read, never rewritten.

        A shadow run fabricates fills. Those rows in the real registry would be
        corruption that nothing downstream flags.
        """
        from core_brain.order_registry import DEFAULT_DB_PATH
        from core_brain.shadow_guard import ShadowSafetyViolation
        from core_brain.shadow_run import run_shadow

        with pytest.raises(ShadowSafetyViolation, match="production registry"):
            run_shadow(
                minutes=0.0, db_path=DEFAULT_DB_PATH,
                markets_fn=self._markets(), client_fn=lambda: object(),
            )

    def test_decided_intents_are_recorded_and_never_submitted(self, tmp_path):
        """The submission boundary. The loop decides; nothing leaves the process."""
        from core_brain.shadow_run import run_shadow

        intent = QuoteIntent(side="UP", token_id="tok-up", price=0.48, size=2,
                             mid=0.49, edge_vs_mid=0.01)

        result = run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(),
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([intent], ""),
        )

        assert len(result.intents) == 1
        recorded = result.intents[0]
        assert recorded.side == "UP"
        assert recorded.price == 0.48
        assert recorded.size == 2
        assert recorded.condition_id == "0x0"

    def test_the_client_is_the_denying_proxy_by_default(self, tmp_path,
                                                         monkeypatch):
        """No injected client means the real one -- which cannot sign."""
        import core_brain.shadow_guard as sg
        from core_brain.shadow_guard import ReadOnlyVenue
        from core_brain.shadow_run import build_shadow_seam

        monkeypatch.setattr(
            sg, "_build_unauthenticated_client", lambda host: object())

        seam = build_shadow_seam(
            db_path=tmp_path / "shadow.db",
            intents_sink=[],
        )

        assert isinstance(seam.client, ReadOnlyVenue)

    def test_an_injected_raw_client_is_wrapped_in_the_denying_proxy(
            self, tmp_path):
        """client_fn is an injection seam, not a way to hand shadow mode a
        raw signer. Whatever arrives unwrapped leaves through the proxy, so a
        future wiring change cannot put a signing-capable object on the seam.
        """
        from core_brain.shadow_guard import ReadOnlyVenue, ShadowSafetyViolation
        from core_brain.shadow_run import build_shadow_seam

        class RawClient:
            def post_order(self, *a, **k):
                return {"orderID": "should-never-happen"}

        seam = build_shadow_seam(
            db_path=tmp_path / "shadow.db",
            client_fn=lambda: RawClient(),
            intents_sink=[],
        )

        assert isinstance(seam.client, ReadOnlyVenue)
        with pytest.raises(ShadowSafetyViolation, match="post_order"):
            seam.client.post_order({"price": 0.48})

    def test_live_caps_are_used_unchanged(self, tmp_path):
        """Same config as live, or the rehearsal rehearses something we do not ship."""
        from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD
        from core_brain.shadow_run import run_shadow

        seen = {}

        def decide(cfg, up, dn, inv, t_rem, wf):
            seen["max_order_usd"] = cfg.max_order_usd
            seen["max_total_usd"] = cfg.max_total_usd
            return [], "declined"

        run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(), client_fn=lambda: object(),
            decide_fn=decide,
        )

        assert seen["max_order_usd"] == MAX_ORDER_USD
        assert seen["max_total_usd"] == MAX_TOTAL_USD

    def test_reconcile_and_sweep_are_skipped_not_silently_passed(self, tmp_path):
        """They reconcile against venue positions a shadow run does not have.

        Milestone 6's loop-health report must be able to say these were
        deliberately skipped rather than that they ran clean, so the result
        records it.
        """
        from core_brain.shadow_run import run_shadow

        result = run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(), client_fn=lambda: object(),
        )

        assert "reconcile" in result.skipped_stages
        assert "sweep" in result.skipped_stages


class TestSettleWiring:
    """Rest, then fill, then decide -- in that order, inside one visit."""

    def test_the_seam_settles_before_it_reports_inventory(self, tmp_path):
        from core_brain.shadow_run import build_shadow_seam

        seen_markets = []
        seam = build_shadow_seam(
            db_path=tmp_path / "shadow.db",
            client_fn=lambda: object(),
            traded_fn=lambda cid, seen: seen_markets.append(cid) or {},
        )
        seam.inventory_fn(FakeMarket("0xabc"))

        assert seen_markets == ["0xabc"]

    def test_submitted_intents_become_rows_in_the_shadow_store(self, tmp_path):
        from core_brain.order_registry import OrderRegistry
        from core_brain.shadow_run import build_shadow_seam

        db = tmp_path / "shadow.db"
        seam = build_shadow_seam(
            db_path=db, client_fn=lambda: object(),
            fetch_books=lambda h, t: {"bids": {0.47: 10.0}},
        )
        placed = seam.submit_fn(
            seam.client, seam.registry, FakeMarket("0xabc"),
            [QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                        mid=0.5, edge_vs_mid=0.0)],
            seam.base_cfg,
        )

        assert placed == 1
        assert len(OrderRegistry(db_path=db).get_active_orders()) == 1

    def test_the_production_registry_is_still_refused(self):
        """The new writers must not have moved the guard off the front door."""
        from core_brain.order_registry import DEFAULT_DB_PATH
        from core_brain.shadow_guard import ShadowSafetyViolation
        from core_brain.shadow_run import build_shadow_seam

        with pytest.raises(ShadowSafetyViolation):
            build_shadow_seam(db_path=DEFAULT_DB_PATH)


class TestLookupFetchMarket:
    def test_lookup_falls_back_to_fetch_market_when_tokens_missing(self, monkeypatch):
        from core_brain.shadow_run import _lookup_fetch_market

        fetched = []
        def mock_fetch_market(cid):
            fetched.append(cid)
            return FakeMarket(cid)

        monkeypatch.setattr("core_brain.trader_loop._fetch_market", mock_fetch_market)

        # Incomplete market spec lacking up_token / down_token
        spec_dict = {"cid": "0x123", "title": "Incomplete"}
        resolver = _lookup_fetch_market([spec_dict])

        res = resolver("0x123")
        assert res.condition_id == "0x123"
        assert res.up_token == "tok-up"
        assert fetched == ["0x123"]

    def test_lookup_uses_cached_market_when_tokens_present(self, monkeypatch):
        from core_brain.shadow_run import _lookup_fetch_market

        fetched = []
        monkeypatch.setattr("core_brain.trader_loop._fetch_market", lambda cid: fetched.append(cid))

        complete_market = FakeMarket("0x456")
        resolver = _lookup_fetch_market([complete_market])

        res = resolver("0x456")
        assert res == complete_market
        assert fetched == []

    def test_lookup_resolves_refreshed_markets_dynamically(self, monkeypatch):
        from core_brain.shadow_run import _lookup_fetch_market

        fetched = []
        monkeypatch.setattr("core_brain.trader_loop._fetch_market", lambda cid: fetched.append(cid))

        current_box = [[FakeMarket("0x1")]]
        resolver = _lookup_fetch_market(lambda: current_box[0])

        assert resolver("0x1").condition_id == "0x1"

        # Dynamically refresh to cycle 2 markets
        current_box[0] = [FakeMarket("0x2")]
        assert resolver("0x2").condition_id == "0x2"
        assert fetched == []


class TestProgressLog:
    """What the operator sees while a rehearsal runs.

    The point of a shadow run is watching the decision, so the decision has to
    reach the terminal. Before this, a wired-up run printed the banner and then
    nothing for the whole time box -- the per-cycle detail existed only in the
    ring file and the store.
    """

    def _emit(self, tmp_path):
        from core_brain.shadow_run import _make_logging_emit
        return _make_logging_emit(tmp_path / "shadow.db")

    def test_a_decision_with_intents_logs_the_market_and_the_count(
            self, tmp_path, caplog):
        import logging

        emit = self._emit(tmp_path)
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            emit(7, "quoting", "decide", market_slug="dota-2026",
                 reason="pair cost 0.985", extra={"intent_count": 2})

        line = caplog.text
        assert "cycle=7" in line
        assert "dota-2026" in line
        assert "intents=2" in line
        assert "pair cost 0.985" in line

    def test_a_declined_market_logs_the_reason_it_was_skipped(
            self, tmp_path, caplog):
        import logging

        emit = self._emit(tmp_path)
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            emit(7, "quoting", "decide", market_slug="lol-2026",
                 reason="UP: 0.905 outside band 0.10-0.90",
                 extra={"intent_count": 0})

        assert "intents=0" in caplog.text
        assert "outside band" in caplog.text

    def test_phases_that_are_not_a_decision_stay_quiet(self, tmp_path, caplog):
        """One line per market visit. A rotation that logged every phase would
        bury the decisions it exists to show."""
        import logging

        emit = self._emit(tmp_path)
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            emit(7, "settling", "pairs_completed", market_slug="dota-2026")

        assert caplog.text.strip() == ""

    def test_the_ring_and_store_still_receive_every_event(self, tmp_path):
        """The log is added beside the telemetry, never instead of it."""
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        db = tmp_path / "shadow.db"
        init_db(db)
        emit = _make_logging_emit(db)
        emit(7, "quoting", "decide", market_slug="dota-2026",
             reason="pair cost 0.985", extra={"intent_count": 2})

        import sqlite3
        con = sqlite3.connect(db)
        rows = con.execute(
            "select cycle, market_slug, intent_count from cycle_intent").fetchall()
        con.close()
        assert rows == [(7, "dota-2026", 2)]


class TestMain:
    """The command line: `python -m core_brain.shadow_run --minutes N`."""

    def test_argument_defaults_match_the_plan(self):
        from pathlib import Path

        from core_brain.shadow_run import _parse_args

        a = _parse_args([])

        assert a.minutes == 5.0
        assert a.interval == 5.0
        assert Path(a.db) == Path("data/shadow.db")
        assert a.max_markets is None

    def test_main_refuses_the_production_registry_via__db(self):
        """The guard sits between argv and the registry, not inside a flag."""
        from core_brain.order_registry import DEFAULT_DB_PATH
        from core_brain.shadow_guard import ShadowSafetyViolation
        from core_brain.shadow_run import main

        with pytest.raises(ShadowSafetyViolation, match="production registry"):
            main(["--minutes", "0", "--db", str(DEFAULT_DB_PATH)])

    def test_main_logs_a_banner_naming_mode_store_timebox_and_no_signer(
            self, tmp_path, caplog):
        """An operator must be able to tell a shadow process from a live one
        in a shared terminal, before any output that looks like results."""
        import logging

        from core_brain.shadow_run import main

        db = tmp_path / "shadow.db"
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            main(
                ["--minutes", "0", "--db", str(db)],
                markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
                client_fn=lambda: object(),
                decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
                fetch_books=_books,
            )
        text = caplog.text.lower()
        assert "shadow" in text
        assert "no signer" in text
        assert str(db).lower() in text
        assert "minutes=0" in text  # the time box is named too

    def test_main_returns_0_when_the_time_box_expires_with_results(
            self, tmp_path):
        from core_brain.shadow_run import main

        def markets(max_markets=None):
            return [FakeMarket("0xabc")]

        rc = main(
            ["--minutes", "0", "--db", str(tmp_path / "shadow.db")],
            markets_fn=markets,
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
            fetch_books=_books,
        )

        assert rc == 0

    def test_main_wires_the_real_book_source_when_none_is_injected(
            self, tmp_path, monkeypatch):
        """A rehearsal against empty books rehearses nothing.

        `/book` is a public endpoint -- no key, no API credentials, and not on
        the CLOB client the deny-by-default proxy guards -- so the entrypoint
        reads it exactly as the live loop does. Unwired, every market decides
        against `{"bids": {}, "asks": {}}` and declines with "no two-sided
        book", which reads on the dashboard as a quiet venue rather than as a
        seam nobody connected.
        """
        import core_brain.markets as markets_mod
        from core_brain.shadow_run import main

        calls = []

        def fake_full_book(clob_host, token_id):
            calls.append((clob_host, token_id))
            return _books(clob_host, token_id)

        monkeypatch.setattr(markets_mod, "full_book", fake_full_book)

        main(
            ["--minutes", "0", "--db", str(tmp_path / "shadow.db")],
            markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
        )

        assert {t for _, t in calls} == {"tok-up", "tok-dn"}
        assert all(h.startswith("http") for h, _ in calls)
