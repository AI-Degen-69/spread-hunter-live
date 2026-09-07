"""The price tape records forward what the venue stops serving backwards.

Journeys under test:
1. As the Owner, a minute already recorded is not recorded twice, so a pass that
   repeats, runs late, or restarts after downtime neither loses minutes nor
   double-counts them.
2. As the Owner, the market metadata needed to interpret a tape is stored with
   it, because a bare token id says nothing about what was being priced.
3. As the Owner, the venue's history payload is turned into ticks, and a point
   the venue mangled is dropped rather than taking the pass down with it.
4. As the Owner, a market under the volume floor is never recorded, because a
   tape nobody traded answers no question.
5. As the Owner, one market whose fetch fails does not abort the pass; the rest
   of the universe is still recorded.
6. As the Owner, a resolution is stamped onto the tape only when the venue
   reports a clean binary outcome, because an ambiguous one cannot score a
   position.
7. As the Owner, the store reports how much tape has been collected, because
   the whole point is knowing when the sample is finally big enough.
"""
from __future__ import annotations

import json

import pytest
import requests

from core_brain.price_tape import (
    TapeMarket,
    TapeStore,
    backfill,
    discover_markets,
    parse_history,
    poll_once,
    refresh_resolutions,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class _Session:
    """Stands in for requests.Session; conftest blocks real sockets."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        for fragment, payload in self.routes.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                if callable(payload):
                    return _Response(payload(params or {}))
                return _Response(payload)
        raise AssertionError(f"unrouted url {url}")


def _market(token_id="tok-up", **over):
    fields = {
        "token_id": token_id,
        "condition_id": "0xabc",
        "question": "Minnesota Twins vs. Chicago White Sox: O/U 8.5",
        "slug": "twins-white-sox-ou",
        "volume_24h": 470_372.0,
    }
    fields.update(over)
    return TapeMarket(**fields)


def _row(**over):
    row = {
        "conditionId": "0xabc",
        "slug": "twins-white-sox-ou",
        "question": "Minnesota Twins vs. Chicago White Sox: O/U 8.5",
        "volume24hr": "470372",
        "clobTokenIds": json.dumps(["tok-up", "tok-down"]),
    }
    row.update(over)
    return row


def _store(tmp_path):
    return TapeStore(tmp_path / "price_tape.db")


# ------------------------------------------------------------------ dedup


def test_records_ticks_for_a_tracked_market(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())

    written = store.append_ticks("tok-up", [(1_700_000_060, 0.47), (1_700_000_120, 0.48)])

    assert written == 2
    assert store.summary().ticks == 2


def test_does_not_record_the_same_minute_twice(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.append_ticks("tok-up", [(1_700_000_060, 0.47), (1_700_000_120, 0.48)])

    written = store.append_ticks(
        "tok-up", [(1_700_000_120, 0.48), (1_700_000_180, 0.49)]
    )

    assert written == 1, "the overlapping minute must not be stored again"
    assert store.summary().ticks == 3


def test_re_recording_a_market_keeps_one_row(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.record_market(_market(volume_24h=999_999.0))

    assert store.summary().markets == 1


# ------------------------------------------------------------------ metadata


def test_keeps_the_metadata_needed_to_interpret_a_tape(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())

    stored = store.get_market("tok-up")

    assert stored.condition_id == "0xabc"
    assert stored.question.startswith("Minnesota Twins")
    assert stored.volume_24h == pytest.approx(470_372.0)


# ------------------------------------------------------------------ parsing


def test_parses_the_venue_history_payload():
    ticks = parse_history({"history": [{"t": 1_700_000_060, "p": 0.47},
                                       {"t": 1_700_000_120, "p": "0.48"}]})

    assert ticks == [(1_700_000_060, 0.47), (1_700_000_120, 0.48)]


def test_drops_a_mangled_point_instead_of_losing_the_pass():
    ticks = parse_history(
        {"history": [{"t": 1_700_000_060, "p": 0.47},
                     {"t": "not-a-time", "p": 0.48},
                     {"p": 0.49},
                     "garbage",
                     {"t": 1_700_000_180, "p": 0.50}]}
    )

    assert ticks == [(1_700_000_060, 0.47), (1_700_000_180, 0.50)]


def test_parses_an_unusable_payload_as_no_ticks():
    assert parse_history(None) == []
    assert parse_history({"history": "nope"}) == []


def test_backfill_stores_what_the_venue_returned(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"prices-history": {"history": [
        {"t": 1_700_000_060, "p": 0.47}, {"t": 1_700_000_120, "p": 0.48}]}})

    written = backfill(store, "tok-up", session=session, hours=6)

    assert written == 2
    assert store.summary().ticks == 2


def test_backfill_asks_for_minute_fidelity(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"prices-history": {"history": []}})

    backfill(store, "tok-up", session=session, hours=6)

    _url, params = session.calls[0]
    assert params["fidelity"] == 1, "a coarser tape cannot answer the drift question"
    assert params["market"] == "tok-up"


# ------------------------------------------------------------------ discovery


def test_discovery_skips_a_market_under_the_volume_floor():
    session = _Session({"/markets": [
        _row(),
        _row(conditionId="0xthin", clobTokenIds=json.dumps(["thin-up", "thin-down"]),
             volume24hr="120"),
    ]})

    markets = discover_markets(session=session, min_volume=20_000.0, limit=50)

    assert [m.token_id for m in markets] == ["tok-up"]


def test_discovery_skips_a_row_the_venue_mangled():
    session = _Session({"/markets": [
        _row(clobTokenIds="not-json"),
        _row(clobTokenIds=json.dumps(["only-one"])),
        _row(conditionId=""),
        "garbage",
        _row(),
    ]})

    markets = discover_markets(session=session, min_volume=20_000.0, limit=50)

    assert [m.token_id for m in markets] == ["tok-up"]


def test_discovery_stops_at_the_limit():
    rows = [_row(conditionId=f"0x{i}", clobTokenIds=json.dumps([f"up-{i}", f"dn-{i}"]))
            for i in range(10)]
    session = _Session({"/markets": rows})

    markets = discover_markets(session=session, min_volume=0.0, limit=3)

    assert len(markets) == 3


# ------------------------------------------------------------------ one pass


def test_a_pass_records_every_market_it_discovered(tmp_path):
    store = _store(tmp_path)
    session = _Session({
        "/markets": [_row()],
        "prices-history": {"history": [{"t": 1_700_000_060, "p": 0.47}]},
    })

    result = poll_once(store, session=session, min_volume=20_000.0, limit=50, hours=6)

    assert result.markets == 1
    assert result.ticks == 1
    assert result.failures == 0
    assert store.summary().markets == 1


def test_one_failed_fetch_does_not_abort_the_pass(tmp_path):
    store = _store(tmp_path)
    rows = [_row(conditionId="0xa", clobTokenIds=json.dumps(["good-up", "good-dn"])),
            _row(conditionId="0xb", clobTokenIds=json.dumps(["bad-up", "bad-dn"]))]

    def history(params):
        if params.get("market") == "bad-up":
            raise requests.ConnectionError("venue dropped it")
        return {"history": [{"t": 1_700_000_060, "p": 0.47}]}

    session = _Session({"/markets": rows, "prices-history": history})

    result = poll_once(store, session=session, min_volume=0.0, limit=50, hours=6)

    assert result.failures == 1
    assert result.ticks == 1, "the healthy market is still recorded"
    assert store.summary().markets == 2


def test_a_repeated_pass_adds_no_duplicate_minutes(tmp_path):
    store = _store(tmp_path)
    session = _Session({
        "/markets": [_row()],
        "prices-history": {"history": [{"t": 1_700_000_060, "p": 0.47}]},
    })

    poll_once(store, session=session, min_volume=0.0, limit=50, hours=6)
    second = poll_once(store, session=session, min_volume=0.0, limit=50, hours=6)

    assert second.ticks == 0
    assert store.summary().ticks == 1


# ------------------------------------------------------------------ resolution


def test_stamps_a_clean_binary_resolution_onto_the_tape(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"/markets": [
        _row(closed=True, outcomePrices=json.dumps(["1", "0"]))]})

    stamped = refresh_resolutions(store, session=session)

    assert stamped == 1
    assert store.get_market("tok-up").up_wins is True


def test_records_a_no_resolution_as_such(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"/markets": [
        _row(closed=True, outcomePrices=json.dumps(["0", "1"]))]})

    refresh_resolutions(store, session=session)

    assert store.get_market("tok-up").up_wins is False


def test_refuses_to_stamp_an_ambiguous_outcome(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"/markets": [
        _row(closed=True, outcomePrices=json.dumps(["0.5", "0.5"]))]})

    stamped = refresh_resolutions(store, session=session)

    assert stamped == 0
    assert store.get_market("tok-up").up_wins is None


def test_leaves_an_open_market_unresolved(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"/markets": [
        _row(closed=False, outcomePrices=json.dumps(["1", "0"]))]})

    stamped = refresh_resolutions(store, session=session)

    assert stamped == 0
    assert store.get_market("tok-up").up_wins is None


def test_a_resolved_market_is_no_longer_tracked(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.record_market(_market(token_id="other-up", condition_id="0xother"))
    store.mark_resolved("tok-up", True)

    assert store.tracked_tokens() == ["other-up"]


# ------------------------------------------------------------------ summary


def test_summary_reports_what_has_been_collected(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.record_market(_market(token_id="other-up", condition_id="0xother"))
    store.append_ticks("tok-up", [(1_700_000_060, 0.47), (1_700_000_120, 0.48)])
    store.append_ticks("other-up", [(1_700_000_180, 0.51)])
    store.mark_resolved("tok-up", False)

    summary = store.summary()

    assert summary.markets == 2
    assert summary.resolved == 1
    assert summary.ticks == 3
    assert summary.first_ts == 1_700_000_060
    assert summary.last_ts == 1_700_000_180


def test_summary_of_an_empty_store_is_not_an_error(tmp_path):
    summary = _store(tmp_path).summary()

    assert summary.markets == 0
    assert summary.ticks == 0
    assert summary.first_ts is None


# ------------------------------------------------------------------ deep backfill
# The venue honours minute fidelity only inside a bounded window, so reaching
# back past a day means walking the request back one day at a time. 20 live
# markets hold 438 market-days of minute tape right now; not pulling it was the
# reason the drift question looked like it needed weeks of forward recording.


def test_backfill_walks_back_one_day_at_a_time(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"prices-history": {"history": []}})

    backfill(store, "tok-up", session=session, hours=72, now=1_700_000_000)

    windows = [(p["startTs"], p["endTs"]) for _u, p in session.calls]
    assert windows == [
        (1_700_000_000 - 86_400, 1_700_000_000),
        (1_700_000_000 - 172_800, 1_700_000_000 - 86_400),
        (1_700_000_000 - 259_200, 1_700_000_000 - 172_800),
    ]
    assert all(p["fidelity"] == 1 for _u, p in session.calls)


def test_backfill_of_under_a_day_stays_one_request(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"prices-history": {"history": []}})

    backfill(store, "tok-up", session=session, hours=6, now=1_700_000_000)

    assert len(session.calls) == 1


def test_backfill_sums_the_ticks_from_every_window(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())

    def history(params):
        return {"history": [{"t": params["startTs"] + 60, "p": 0.47}]}

    session = _Session({"prices-history": history})

    written = backfill(store, "tok-up", session=session, hours=72, now=1_700_000_000)

    assert written == 3


def test_a_failed_window_keeps_the_windows_already_stored(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())

    def history(params):
        if params["startTs"] < 1_700_000_000 - 172_800:
            raise requests.ConnectionError("venue dropped it")
        return {"history": [{"t": params["startTs"] + 60, "p": 0.47}]}

    session = _Session({"prices-history": history})

    with pytest.raises(requests.ConnectionError):
        backfill(store, "tok-up", session=session, hours=72, now=1_700_000_000)

    assert store.summary().ticks == 2, "the windows fetched before the failure survive"


# ------------------------------------------------------------------ bad prices
# A binary share is worth between $0.00 and $1.00. `float()` happily accepts
# -1, 2, NaN and inf, and a tape holding one of those silently corrupts every
# statistic computed from it later.


def test_rejects_a_price_outside_the_zero_to_one_range():
    ticks = parse_history({"history": [
        {"t": 1_700_000_060, "p": -0.01},
        {"t": 1_700_000_120, "p": 1.01},
        {"t": 1_700_000_180, "p": 0.47},
    ]})

    assert ticks == [(1_700_000_180, 0.47)]


def test_rejects_nan_and_infinity():
    ticks = parse_history({"history": [
        {"t": 1_700_000_060, "p": "NaN"},
        {"t": 1_700_000_120, "p": "inf"},
        {"t": 1_700_000_180, "p": "-inf"},
        {"t": 1_700_000_240, "p": 0.5},
    ]})

    assert ticks == [(1_700_000_240, 0.5)]


def test_keeps_the_boundary_prices():
    ticks = parse_history({"history": [{"t": 1, "p": 0.0}, {"t": 2, "p": 1.0}]})

    assert ticks == [(1, 0.0), (2, 1.0)]


# ------------------------------------------------------------------ pagination
# The venue caps a market page at 100 rows however large a limit is asked for.
# Requesting 500 and reading the reply as the whole universe silently truncates
# it: a --limit of 250 returned 100 markets on 2026-09-07.


class _PagedSession:
    """Serves market rows a page at a time, honouring `offset`."""

    def __init__(self, rows, page_size=100, other=None):
        self.rows = rows
        self.page_size = page_size
        self.other = other or {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        for fragment, payload in self.other.items():
            if fragment in url:
                return _Response(payload)
        offset = int(params.get("offset", 0))
        return _Response(self.rows[offset:offset + self.page_size])


def _rows(count, prefix="m"):
    return [_row(conditionId=f"0x{prefix}{i}",
                 clobTokenIds=json.dumps([f"{prefix}-up-{i}", f"{prefix}-dn-{i}"]))
            for i in range(count)]


def test_discovery_reads_past_the_first_page():
    session = _PagedSession(_rows(250), page_size=100)

    markets = discover_markets(session=session, min_volume=0.0, limit=250)

    assert len(markets) == 250, "the venue caps a page at 100 rows"
    assert len(session.calls) >= 3


def test_discovery_stops_when_a_page_comes_back_empty():
    session = _PagedSession(_rows(120), page_size=100)

    markets = discover_markets(session=session, min_volume=0.0, limit=500)

    assert len(markets) == 120


def test_discovery_does_not_page_past_the_limit():
    session = _PagedSession(_rows(250), page_size=100)

    markets = discover_markets(session=session, min_volume=0.0, limit=50)

    assert len(markets) == 50
    assert len(session.calls) == 1, "one page already covered the limit"


def test_resolution_finds_a_tracked_market_on_a_later_page(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market(token_id="late-up-4", condition_id="0xlate4"))
    rows = _rows(100, prefix="early") + [
        _row(conditionId="0xlate4",
             clobTokenIds=json.dumps(["late-up-4", "late-dn-4"]),
             closed=True, outcomePrices=json.dumps(["1", "0"]))]
    session = _PagedSession(rows, page_size=100)

    stamped = refresh_resolutions(store, session=session)

    assert stamped == 1
    assert store.get_market("late-up-4").up_wins is True


def test_resolution_stops_once_every_tracked_market_is_stamped(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market(token_id="early-up-0", condition_id="0xearly0"))
    rows = [_row(conditionId="0xearly0",
                 clobTokenIds=json.dumps(["early-up-0", "early-dn-0"]),
                 closed=True, outcomePrices=json.dumps(["1", "0"]))] + _rows(400)
    session = _PagedSession(rows, page_size=100)

    refresh_resolutions(store, session=session)

    assert len(session.calls) == 1, "nothing tracked is left to look for"


def test_resolution_with_nothing_tracked_asks_the_venue_nothing(tmp_path):
    store = _store(tmp_path)
    session = _PagedSession(_rows(100))

    assert refresh_resolutions(store, session=session) == 0
    assert session.calls == []
