"""Every rehearsal gets its own run_id, and never the live session's.

`order_registry._resolve_run_id` reuses `runtime/.current_run_id` for 12 hours
so the fleet, the dashboard and the exec process all tag one live session the
same way. That is right for live and wrong for a rehearsal: two shadow runs
3.85 hours apart welded themselves into `run-2809a7161de1`, so the 30-minute
baseline and the 30-minute re-run shared one bucket and could only be told
apart by spotting the gap in `posted_ts`. A rehearsal that cannot be selected
on its own is a rehearsal whose numbers cannot be compared to anything.
"""
from __future__ import annotations

import os
import re
from unittest import mock

from core_brain.shadow_run import shadow_run_id


class TestShadowRunId:
    def test_every_call_mints_a_fresh_id(self, monkeypatch):
        monkeypatch.delenv("SH_RUN_ID", raising=False)
        assert shadow_run_id() != shadow_run_id()

    def test_the_generated_id_has_the_documented_shape(self, monkeypatch):
        # `shadow-` plus 12 lowercase hex. Pinned because the prefix is what
        # keeps a rehearsal out of the live run selector, and the width is what
        # makes a collision between two same-second sessions implausible.
        monkeypatch.delenv("SH_RUN_ID", raising=False)
        assert re.fullmatch(r"shadow-[0-9a-f]{12}", shadow_run_id())

    def test_the_id_is_marked_as_a_rehearsal(self, monkeypatch):
        monkeypatch.delenv("SH_RUN_ID", raising=False)
        # A shadow id must never be mistakable for a live one in the dashboard's
        # run selector, or a rehearsal's zero fills read as a live session's.
        rid = shadow_run_id()
        assert rid.startswith("shadow-")
        assert not rid.startswith("run-")

    def test_an_explicit_override_is_honoured(self):
        # The operator continuing one rehearsal across a restart is the one
        # case where sharing an id is the point.
        with mock.patch.dict(os.environ, {"SH_RUN_ID": "shadow-deadbeef"}):
            assert shadow_run_id() == "shadow-deadbeef"

    def test_the_live_lock_file_is_never_consulted(self, tmp_path, monkeypatch):
        # The failure this exists to stop: a rehearsal adopting the live
        # session's run_id because the lock file is under 12 hours old.
        from core_brain import order_registry

        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SH_RUN_ID", None)
            rid = shadow_run_id()
        assert rid != "run-liveone"
        assert rid.startswith("shadow-")


class FakeMarket:
    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False


def _book(_clob_host, token_id):
    return {"token_id": token_id, "bids": {0.50: 500.0}, "asks": {0.51: 500.0},
            "best_bid": 0.50, "best_ask": 0.51, "malformed": 0}


def _one_session(tmp_path, monkeypatch, db_name, run_id_env=None):
    """One real shadow session over a stub market; returns the run_ids written."""
    import core_brain.markets as markets_mod
    from core_brain.order_registry import OrderRegistry
    from core_brain.quotes import QuoteIntent
    from core_brain.shadow_run import _Deadline, run_shadow

    seen = {"n": 0}

    def counting_sleep(_seconds):
        seen["n"] += 1
        if seen["n"] >= 2:
            raise _Deadline()

    monkeypatch.setattr("core_brain.shadow_run.make_deadline_sleep",
                        lambda *a, **k: counting_sleep)
    monkeypatch.setattr(markets_mod, "recent_trades", lambda cid, s: {})
    if run_id_env is None:
        monkeypatch.delenv("SH_RUN_ID", raising=False)
    else:
        monkeypatch.setenv("SH_RUN_ID", run_id_env)

    db = tmp_path / db_name
    run_shadow(
        minutes=1.0, db_path=db,
        markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
        client_fn=lambda: object(),
        decide_fn=lambda *a, **k: (
            [QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                         mid=0.5, edge_vs_mid=0.0)], "quoting"),
        fetch_books=_book,
        interval=0.0,
    )
    reg = OrderRegistry(db_path=db)
    return {o["run_id"] for o in reg.get_all_orders()}


class TestRunShadowUsesIt:
    def test_orders_are_tagged_with_a_shadow_id(self, tmp_path, monkeypatch):
        ids = _one_session(tmp_path, monkeypatch, "a.db")
        assert ids, "the session wrote no orders, so it proves nothing"
        assert all(i.startswith("shadow-") for i in ids), ids

    def test_two_sessions_do_not_share_an_id(self, tmp_path, monkeypatch):
        # The exact failure: two 30-minute rehearsals 3.85 hours apart both
        # wrote as run-2809a7161de1 and could only be separated by eye.
        first = _one_session(tmp_path, monkeypatch, "first.db")
        second = _one_session(tmp_path, monkeypatch, "second.db")
        assert first and second
        assert first.isdisjoint(second), f"{first} overlaps {second}"


class TestRunIdIsSessionScoped:
    """CodeRabbit round 1: a process-wide id leaks out of the session.

    `set_run_id` mutated `order_registry._CURRENT_RUN_ID`, which every write
    path reads. Two consequences, both worse than the bug this branch set out
    to fix: a second shadow session starting while a first is alive silently
    re-tags the first one's later rows, and the shadow id stayed installed
    after `run_shadow` returned -- so a live order created afterwards in the
    same process would be stamped `shadow-...`. A rehearsal id on a real order
    is a worse lie than two rehearsals sharing one id.
    """

    def test_the_process_wide_id_is_untouched_by_a_session(
            self, tmp_path, monkeypatch):
        from core_brain import order_registry

        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")
        ids = _one_session(tmp_path, monkeypatch, "scoped.db")
        assert all(i.startswith("shadow-") for i in ids), ids
        # The live id must survive the rehearsal untouched.
        assert order_registry.get_run_id() == "run-liveone"

    def test_two_registries_on_one_process_keep_their_own_ids(self, tmp_path):
        from core_brain.order_registry import OrderRegistry, OrderRecord

        a = OrderRegistry(db_path=tmp_path / "a.db", run_id="shadow-aaaaaaaaaaaa")
        b = OrderRegistry(db_path=tmp_path / "b.db", run_id="shadow-bbbbbbbbbbbb")
        for reg, tok in ((a, "tok-a"), (b, "tok-b")):
            reg.create_order(OrderRecord(
                id=f"local-{tok}", order_id=f"oid-{tok}", condition_id="0xabc",
                token_id=tok, side="BUY", price=0.47, original_size=20,
                status="open", posted_ts=1, last_polled_ts=1))
        assert {o["run_id"] for o in a.get_all_orders()} == {"shadow-aaaaaaaaaaaa"}
        assert {o["run_id"] for o in b.get_all_orders()} == {"shadow-bbbbbbbbbbbb"}

    def test_a_registry_without_an_id_still_uses_the_process_one(
            self, tmp_path, monkeypatch):
        from core_brain import order_registry
        from core_brain.order_registry import OrderRegistry, OrderRecord

        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")
        reg = OrderRegistry(db_path=tmp_path / "c.db")
        reg.create_order(OrderRecord(
            id="local-1", order_id="oid-1", condition_id="0xabc",
            token_id="tok", side="BUY", price=0.47, original_size=20,
            status="open", posted_ts=1, last_polled_ts=1))
        assert {o["run_id"] for o in reg.get_all_orders()} == {"run-liveone"}


class TestOverrideReachesTheRows:
    def test_an_explicit_override_tags_every_row(self, tmp_path, monkeypatch):
        # The override is only useful if it survives all the way to the store.
        ids = _one_session(tmp_path, monkeypatch, "ovr.db",
                           run_id_env="shadow-operatorpin")
        assert ids == {"shadow-operatorpin"}
