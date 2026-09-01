"""The wide-book trial knob, and the refusal that keeps it off the money path.

`HUNTER_WIDE_BOOK_TRIAL` widens `max_book_spread`, which is not a preference
but the gate that refuses a book where a fill may leave a leg that cannot be
closed. The two config modules carry independent copies of the parse and the
refusal -- the ranker loads `scoring.config`, the fleet loads
`core_brain.config` -- so both are pinned here, together, to the same
behaviour.
"""
from __future__ import annotations

import importlib

import pytest

from core_brain import config as core_config
from scoring import config as scoring_config

BOTH = pytest.mark.parametrize(
    "mod", [core_config, scoring_config], ids=["core_brain", "scoring"])


# --- the refusal --------------------------------------------------------------


@BOTH
def test_trial_is_refused_while_a_private_key_is_loaded(mod):
    with pytest.raises(ValueError) as e:
        mod.resolve_wide_book_trial("0.15", env={"POLY_PRIVATE_KEY": "0xdead"})

    assert "refused while a signing key is loaded" in str(e.value)


@BOTH
def test_trial_is_refused_for_the_alternate_key_variable(mod):
    # `venue.signed_client` accepts either name, so either must refuse.
    with pytest.raises(ValueError):
        mod.resolve_wide_book_trial("0.15", env={"POLY_KEY": "0xdead"})


@BOTH
def test_a_blank_key_variable_does_not_refuse(mod):
    # An exported-but-empty variable is a configured machine that is not armed.
    assert mod.resolve_wide_book_trial(
        "0.15", env={"POLY_PRIVATE_KEY": "  "}) == 0.15


@BOTH
def test_credentials_that_cannot_sign_do_not_refuse(mod):
    # A funder address or an API key cannot sign an order on its own; refusing
    # on them would block the trial on every configured machine.
    env = {"POLY_FUNDER": "0xabc", "POLY_API_KEY": "k", "POLY_SECRET": "s"}

    assert mod.resolve_wide_book_trial("0.15", env=env) == 0.15


# --- parsing ------------------------------------------------------------------


@BOTH
def test_unset_trial_is_none(mod):
    assert mod.resolve_wide_book_trial("", env={}) is None
    assert mod.resolve_wide_book_trial("   ", env={}) is None


@BOTH
def test_trial_refuses_a_value_past_the_bound(mod):
    # Thirty cents apart is not a wide book, it is an absent one.
    with pytest.raises(ValueError) as e:
        mod.resolve_wide_book_trial("0.45", env={})

    assert "outside" in str(e.value)


@BOTH
def test_trial_refuses_a_non_finite_value(mod):
    # NaN poisons every comparison it touches: a ceiling set to NaN reads as
    # enforced while permitting anything.
    with pytest.raises(ValueError):
        mod.resolve_wide_book_trial("nan", env={})


@BOTH
def test_trial_refuses_a_non_number(mod):
    with pytest.raises(ValueError):
        mod.resolve_wide_book_trial("wide", env={})


# --- what load() does with it -------------------------------------------------


def _load_with(mod, monkeypatch, **env):
    for name in ("POLY_PRIVATE_KEY", "POLY_KEY", "HUNTER_MARKET",
                 "HUNTER_MAX_SPREAD", "HUNTER_WIDE_BOOK_TRIAL"):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(mod).load()


@BOTH
def test_load_moves_all_three_ceilings_together(mod, monkeypatch):
    # Any one of the three left at its shipped value makes the trial measure
    # nothing: the ranker never offers the market, `book_health` refuses to
    # quote it, or the reward window refuses the price the bid cap produces.
    cfg = _load_with(mod, monkeypatch, HUNTER_WIDE_BOOK_TRIAL="0.15")

    assert cfg.wide_book_trial == 0.15
    assert cfg.max_book_spread == 0.15
    assert cfg.select_max_book_spread == 0.15
    assert cfg.max_spread_from_mid == 0.15


@BOTH
def test_load_leaves_the_ceilings_alone_when_unset(mod, monkeypatch):
    cfg = _load_with(mod, monkeypatch)

    assert cfg.wide_book_trial is None
    assert cfg.max_book_spread == 0.06
    assert cfg.select_max_book_spread == 0.06
    assert cfg.max_spread_from_mid == 0.045


@BOTH
def test_a_narrow_trial_never_tightens_the_reward_window(mod, monkeypatch):
    # The window is widened only if the trial is wider than it already is. A
    # trial NARROWER than 4.5c is a narrower BOOK ceiling, and must not
    # quietly shrink where we are allowed to quote.
    cfg = _load_with(mod, monkeypatch, HUNTER_WIDE_BOOK_TRIAL="0.02")

    assert cfg.max_book_spread == 0.02
    assert cfg.max_spread_from_mid == 0.045


@BOTH
def test_load_refuses_outright_with_a_key_present(mod, monkeypatch):
    # Refusing at config load stops the process before a client is built and
    # long before a share is bought.
    with pytest.raises(ValueError):
        _load_with(mod, monkeypatch,
                   HUNTER_WIDE_BOOK_TRIAL="0.15", POLY_PRIVATE_KEY="0xdead")


@BOTH
def test_trial_widens_a_venue_supplied_window_rather_than_replacing_it(
        mod, monkeypatch):
    # `HUNTER_MAX_SPREAD` carries the venue's own reward window in cents. The
    # trial takes the wider of the two, so a venue window of 20c survives a
    # 15c trial.
    cfg = _load_with(mod, monkeypatch, HUNTER_MARKET="0xabc",
                     HUNTER_MAX_SPREAD="20", HUNTER_WIDE_BOOK_TRIAL="0.15")

    assert cfg.max_book_spread == 0.15
    assert cfg.max_spread_from_mid == 0.20


# --- the gate it exists to move -----------------------------------------------


def test_book_health_refuses_a_wide_book_at_the_shipped_ceiling():
    from core_brain import risk

    cfg = core_config.MakerConfig()
    book = {"best_bid": 0.435, "best_ask": 0.565, "bids": {0.435: 5000.0}}

    health = risk.book_health(book, cfg)

    assert not health.ok
    assert "book too wide 13.0c > 6.0c" in health.reason


def test_book_health_admits_the_same_book_under_the_trial(monkeypatch):
    # The observed refusal in run 145 was exactly this book width. With the
    # trial in force the same book passes, which is the whole point.
    from core_brain import risk

    for name in ("POLY_PRIVATE_KEY", "POLY_KEY", "HUNTER_MARKET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HUNTER_WIDE_BOOK_TRIAL", "0.15")
    cfg = importlib.reload(core_config).load()
    book = {"best_bid": 0.435, "best_ask": 0.565, "bids": {0.435: 5000.0}}

    health = risk.book_health(book, cfg)

    assert health.ok, health.reason


@pytest.fixture(autouse=True)
def _restore_config_modules():
    """Reload both config modules from a clean environment after each test.

    `load()` reads `os.environ` at call time but these tests reload the module
    itself, so a test that left a widened ceiling in a module-level constant
    would leak into everything after it -- including `scripts.filter_markets`,
    which snapshots `select_max_book_spread` at import.
    """
    yield
    importlib.reload(core_config)
    importlib.reload(scoring_config)
