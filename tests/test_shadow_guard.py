"""Tests for the shadow-run safety guard.

A shadow run exists so the operator can watch the whole loop without spending
money. That guarantee is worth nothing if it rests on a boolean: this repo runs
LIVE by default, and `except Exception` is everywhere in the loop's error
isolation. So the guard raises a BaseException subclass from a client that never
holds a credential, and the tests below are the proof.
"""
from __future__ import annotations

import pytest

from core_brain.order_registry import DEFAULT_DB_PATH as _PROD_DB
from core_brain.shadow_guard import (
    ReadOnlyVenue,
    ShadowSafetyViolation,
    assert_not_production_registry,
)


class _StubClient:
    """Stands in for the CLOB client. Records what the proxy let through."""

    def __init__(self):
        self.calls = []

    def get_order_book(self, token_id):
        self.calls.append(("get_order_book", token_id))
        return {"bids": [], "asks": []}

    def post_order(self, *a, **k):
        self.calls.append(("post_order", a))
        return {"orderID": "should-never-happen"}

    def create_and_post_market_order(self, *a, **k):
        self.calls.append(("create_and_post_market_order", a))
        return {"orderID": "should-never-happen"}

    def some_future_sdk_method(self, *a, **k):
        self.calls.append(("some_future_sdk_method", a))
        return "should-never-happen"


def test_violation_is_not_swallowed_by_a_blanket_except_exception():
    """The regression this class exists to prevent.

    `core_brain/trader_loop.py` isolates reconcile, sweep and every market visit
    behind `except Exception` so one failure degrades the cycle instead of
    stopping it. A safety violation caught by one of those handlers would be
    logged as a warning and the run would carry on -- exactly the outcome the
    guard is meant to make impossible. Same reasoning as
    `tests/conftest.py:ProductionRegistryWriteError`.
    """
    with pytest.raises(ShadowSafetyViolation):
        try:
            raise ShadowSafetyViolation("reached a signing path")
        except Exception:  # noqa: BLE001 - the point of the test
            pytest.fail("a blanket except Exception swallowed the safety violation")


def test_an_allowlisted_read_reaches_the_client():
    """Shadow mode is useless if it cannot read the book."""
    inner = _StubClient()
    venue = ReadOnlyVenue(inner)

    book = venue.get_order_book("token-1")

    assert book == {"bids": [], "asks": []}
    assert inner.calls == [("get_order_book", "token-1")]


def test_post_order_raises_and_never_reaches_the_client():
    """The whole point. A submit attempt must die at the proxy, not at the venue."""
    inner = _StubClient()
    venue = ReadOnlyVenue(inner)

    with pytest.raises(ShadowSafetyViolation, match="post_order"):
        venue.post_order({"price": 0.48, "size": 2})

    assert inner.calls == []


def test_market_order_submission_raises_and_never_reaches_the_client():
    """`single_buy_saver` reaches for this one when a leg is stranded."""
    inner = _StubClient()
    venue = ReadOnlyVenue(inner)

    with pytest.raises(ShadowSafetyViolation, match="create_and_post_market_order"):
        venue.create_and_post_market_order()

    assert inner.calls == []


def test_an_unknown_method_is_denied_rather_than_passed_through():
    """Deny by default.

    An allowlist of writes would be a list we have to keep in sync with an SDK
    we do not control: the day py_clob_client adds a new submit spelling, an
    allowlist of writes silently lets it through. Only reads we have vetted are
    permitted; everything else is a violation, including methods that are
    perfectly harmless.
    """
    inner = _StubClient()
    venue = ReadOnlyVenue(inner)

    with pytest.raises(ShadowSafetyViolation, match="some_future_sdk_method"):
        venue.some_future_sdk_method()

    assert inner.calls == []


class _RecordingEnv(dict):
    """An os.environ stand-in that remembers every key anyone looked at."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.read_keys = []

    def __getitem__(self, key):
        self.read_keys.append(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read_keys.append(key)
        return super().get(key, default)

    def __contains__(self, key):
        # `if "POLY_KEY" in os.environ:` is a read too, and it reaches neither
        # __getitem__ nor get. Without this, a builder could branch on a
        # credential's presence while the recording said it read nothing.
        self.read_keys.append(key)
        return super().__contains__(key)


def test_shadow_client_never_reads_a_credential(monkeypatch):
    """The structural guarantee, asserted rather than assumed.

    `core_brain/venue.py:client` reads POLY_PRIVATE_KEY and derives L2 creds.
    The shadow client must read neither -- not the signing key, and not the API
    creds either. A run that holds no credential cannot authenticate a write no
    matter what bug it contains.
    """
    import os

    env = _RecordingEnv({
        "POLY_PRIVATE_KEY": "0xdeadbeef",
        "POLY_KEY": "0xdeadbeef",
        "POLY_API_KEY": "api-key",
        "POLY_API_SECRET": "api-secret",
        "POLY_API_PASSPHRASE": "api-passphrase",
        "POLY_FUNDER": "0xfunder",
    })
    monkeypatch.setattr(os, "environ", env)

    from core_brain.shadow_guard import shadow_client

    built = []

    def fake_build(host):
        built.append(host)
        return _StubClient()

    venue = shadow_client(build_fn=fake_build)

    forbidden = {
        "POLY_PRIVATE_KEY", "POLY_KEY", "PRIVATE_KEY",
        "POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE",
    }
    leaked = forbidden.intersection(env.read_keys)
    assert not leaked, f"shadow client read credential env vars: {sorted(leaked)}"
    assert len(built) == 1, "the client should be built exactly once"


def test_shadow_client_returns_a_denying_proxy(monkeypatch):
    """Whatever the builder hands back, callers get it wrapped."""
    from core_brain.shadow_guard import shadow_client

    inner = _StubClient()
    venue = shadow_client(build_fn=lambda _host: inner)

    assert isinstance(venue, ReadOnlyVenue)
    with pytest.raises(ShadowSafetyViolation):
        venue.post_order({"price": 0.48})
    assert inner.calls == []


def test_setting_an_attribute_on_the_venue_is_denied():
    """Attaching a credential to the client is the bypass this blocks."""
    inner = _StubClient()
    venue = ReadOnlyVenue(inner)

    with pytest.raises(ShadowSafetyViolation, match="creds"):
        venue.creds = {"key": "leaked"}

    assert not hasattr(inner, "creds")


def test_the_real_builder_reads_no_credential(monkeypatch):
    """`_build_unauthenticated_client` is the one function every injected
    test skips, and it is the one that constructs the real client. Prove it
    builds without a key, a signature type or a funder, and reads no
    credential env var doing it -- otherwise both guarantees fail for the
    first time during an operator's live shadow run.
    """
    pytest.importorskip("py_clob_client_v2")
    import os

    import py_clob_client_v2.client as clob_client_module

    env = _RecordingEnv({
        "POLY_PRIVATE_KEY": "0xdeadbeef",
        "POLY_KEY": "0xdeadbeef",
        "POLY_API_KEY": "api-key",
        "POLY_API_SECRET": "api-secret",
        "POLY_API_PASSPHRASE": "api-passphrase",
        "POLY_FUNDER": "0xfunder",
    })
    monkeypatch.setattr(os, "environ", env)

    captured = {}

    def fake_clob_client(host, **kwargs):
        captured["host"] = host
        captured["kwargs"] = kwargs
        return _StubClient()

    monkeypatch.setattr(clob_client_module, "ClobClient", fake_clob_client)

    from core_brain.shadow_guard import shadow_client

    shadow_client()  # no build_fn: the real builder runs

    # Allowlists, not denylists. Naming the bad keys only catches the ones we
    # thought of: a credential arriving as `creds`, `api_key` or `private_key`
    # would walk past a list of three forbidden names. Naming what the builder
    # is *allowed* to touch fails closed on anything new instead.
    allowed_env = {"CLOB_HOST"}
    unexpected = set(env.read_keys) - allowed_env
    assert not unexpected, (
        f"the real builder read env vars beyond {sorted(allowed_env)}: "
        f"{sorted(unexpected)}")

    allowed_kwargs = {"chain_id"}
    extra = set(captured["kwargs"]) - allowed_kwargs
    assert not extra, (
        f"the real builder passed kwargs beyond {sorted(allowed_kwargs)}: "
        f"{sorted(extra)}")


# --- shadow store guard ------------------------------------------------------
#
# `data/orders.db` is the production registry. A shadow run must never open it
# as a write target. The bypasses below are the ones `tests/conftest.py`
# already learned about the hard way: sqlite percent-decodes a URI path and
# accepts a `localhost` authority, so a guard that only strips the scheme is
# not a guard.

_ABS_ORDERS_DB = str(_PROD_DB).replace("\\", "/")


@pytest.mark.parametrize("target", [
    "data/orders.db",
    r"data\orders.db",
    str(_PROD_DB),
    "./data/orders.db",
    "data/../data/orders.db",
    "file:data/orders.db",
    "file:data/orders.db?mode=rwc",
    "file:data/orders%2edb",
    "file:data%2Forders.db",
    "file:///" + _ABS_ORDERS_DB,
    "file://localhost/" + _ABS_ORDERS_DB,
])
def test_production_registry_is_refused_as_a_shadow_store(target):
    with pytest.raises(ShadowSafetyViolation, match="production registry"):
        assert_not_production_registry(target)


def test_one_spelling_failing_to_resolve_does_not_disable_the_guard(monkeypatch):
    """A resolution failure must not swallow the other candidate's match.

    The guard checks `data\\orders.db` and its slash-normalised twin. Resolving
    them under one `any()` meant a symlink loop on the first spelling aborted
    the whole check and let the production registry through.
    """
    import pathlib

    real_resolve = pathlib.Path.resolve
    calls = {"n": 0}

    def flaky_resolve(self, *args, **kwargs):
        # Call 1 is DEFAULT_DB_PATH, call 2 the first candidate spelling.
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(40, "Too many levels of symbolic links")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", flaky_resolve)

    with pytest.raises(ShadowSafetyViolation, match="production registry"):
        assert_not_production_registry(r"data\orders.db")


def test_a_store_that_cannot_be_resolved_is_refused(monkeypatch):
    """Unresolvable means unprovable, and the guard refuses what it cannot prove."""
    import pathlib

    real_resolve = pathlib.Path.resolve

    def exploding_resolve(self, *args, **kwargs):
        if self.name == "loop.db":
            raise OSError(40, "Too many levels of symbolic links")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", exploding_resolve)

    with pytest.raises(ShadowSafetyViolation, match="could not be resolved"):
        assert_not_production_registry("data/loop.db")


def test_the_default_shadow_store_is_accepted():
    assert_not_production_registry("data/shadow.db")


def test_an_arbitrary_temp_db_is_accepted(tmp_path):
    assert_not_production_registry(tmp_path / "shadow.db")


def test_a_differently_named_db_in_the_data_directory_is_accepted():
    """The guard names one file, not a directory.

    Refusing all of `data/` would be a false positive that makes the shadow
    store hard to place, and the invariant is about one file.
    """
    assert_not_production_registry("data/orders_shadow.db")
