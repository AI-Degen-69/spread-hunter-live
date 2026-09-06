"""The pair scanner ranks live venue markets by how much a merged pair earns.

Journeys under test:
1. As the Owner, I see the profit a pair earns when BOTH legs fill at the touch,
   because that -- not the ask -- is the only price this strategy ever pays.
2. As the Owner, a market whose two best bids already sum to $1.00 or more is
   dropped, because assembling a pair there is a booked loss.
3. As the Owner, the queue resting ahead of me is reported as a cost, not as
   available size: a deep touch is money I have to wait behind, not money I get.
4. As the Owner, the ranking prefers the market whose queue actually turns over,
   so a 1c edge behind $45,000 of queue does not outrank 2c behind $600.
5. As the Owner, a market the venue returned malformed is skipped rather than
   taking the whole scan down with it.
6. As the Owner, the scan records what taking both legs would have cost, so the
   maker-only nature of the strategy is visible in the output.
"""
from __future__ import annotations

import json

import pytest

from core_brain.pair_scanner import (
    PairQuote,
    build_quote,
    parse_candidate,
    rank,
    scan,
)


def _book(bids, asks):
    return {
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
    }


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


# ---------------------------------------------------------------- parse_candidate


def test_parses_a_well_formed_market_row():
    c = parse_candidate(_row())

    assert c is not None
    assert c.condition_id == "0xabc"
    assert c.up_token == "tok-up"
    assert c.down_token == "tok-down"
    assert c.volume_24h == pytest.approx(470372.0)


def test_market_without_exactly_two_clob_tokens_is_skipped():
    assert parse_candidate(_row(clobTokenIds=json.dumps(["only-one"]))) is None
    assert parse_candidate(_row(clobTokenIds="not json at all")) is None
    assert parse_candidate(_row(clobTokenIds=None)) is None


def test_market_without_a_condition_id_is_skipped():
    assert parse_candidate(_row(conditionId="")) is None


def test_slug_is_sanitised_before_it_reaches_a_report():
    c = parse_candidate(_row(slug='twins"><script>'))

    assert c is not None
    assert c.slug == "twinsscript"


# ---------------------------------------------------------------- build_quote


def test_edge_is_one_dollar_minus_the_two_best_bids():
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.60, 1000)], asks=[(0.61, 1000)]),
        _book(bids=[(0.39, 1000)], asks=[(0.40, 1000)]),
    )

    assert q is not None
    assert q.bid_pair == pytest.approx(0.99)
    assert q.edge_per_pair == pytest.approx(0.01)


def test_pair_at_or_above_a_dollar_is_dropped():
    cand = parse_candidate(_row())

    exactly_a_dollar = build_quote(
        cand,
        _book(bids=[(0.60, 100)], asks=[(0.61, 100)]),
        _book(bids=[(0.40, 100)], asks=[(0.41, 100)]),
    )
    over_a_dollar = build_quote(
        cand,
        _book(bids=[(0.62, 100)], asks=[(0.63, 100)]),
        _book(bids=[(0.40, 100)], asks=[(0.41, 100)]),
    )

    assert exactly_a_dollar is None
    assert over_a_dollar is None


def test_queue_ahead_is_the_thinner_leg_in_dollars():
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.60, 1000)], asks=[(0.61, 10)]),   # $600 resting
        _book(bids=[(0.39, 100)], asks=[(0.40, 10)]),    # $39 resting
    )

    assert q is not None
    # A pair needs both legs. The leg with less queue is not the constraint --
    # the pair can only be assembled as fast as the SLOWER leg clears, and the
    # slower leg is the one with more money in front of us.
    assert q.queue_ahead_usd == pytest.approx(600.0)


def test_ask_pair_records_what_taking_both_legs_would_cost():
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.60, 100)], asks=[(0.61, 100)]),
        _book(bids=[(0.39, 100)], asks=[(0.40, 100)]),
    )

    assert q is not None
    assert q.ask_pair == pytest.approx(1.01)
    assert q.taker_pair_is_profitable is False


def test_an_empty_book_side_is_skipped():
    cand = parse_candidate(_row())

    assert build_quote(cand, _book(bids=[], asks=[(0.61, 10)]),
                       _book(bids=[(0.39, 10)], asks=[(0.40, 10)])) is None
    assert build_quote(cand, _book(bids=[(0.60, 10)], asks=[]),
                       _book(bids=[(0.39, 10)], asks=[(0.40, 10)])) is None
    assert build_quote(cand, None, _book(bids=[(0.39, 10)], asks=[(0.40, 10)])) is None


def test_unparseable_book_levels_do_not_take_the_scan_down():
    cand = parse_candidate(_row())
    junk = {"bids": [{"price": "n/a", "size": "n/a"}], "asks": [{"price": "0.4", "size": "5"}]}

    assert build_quote(cand, junk, _book(bids=[(0.39, 10)], asks=[(0.40, 10)])) is None


# ---------------------------------------------------------------- rank


def _quote(cid, edge, queue, volume):
    return PairQuote(
        condition_id=cid,
        slug=cid,
        question=cid,
        up_token="u",
        down_token="d",
        volume_24h=volume,
        bid_pair=1.0 - edge,
        ask_pair=1.0 + edge,
        queue_ahead_usd=queue,
    )


def test_turnover_score_prefers_the_queue_that_actually_clears():
    # 1c behind $45,000 of queue on $333k of daily flow: the queue clears ~7x.
    deep = _quote("fed", edge=0.01, queue=45_000.0, volume=333_761.0)
    # 2c behind $600 of queue on $172k of daily flow: the queue clears ~287x.
    shallow = _quote("alcaraz", edge=0.02, queue=600.0, volume=172_436.0)

    assert shallow.turnover_score > deep.turnover_score
    assert [q.condition_id for q in rank([deep, shallow])] == ["alcaraz", "fed"]


def test_a_market_with_no_flow_scores_zero_however_wide_the_edge():
    dead = _quote("dead", edge=0.04, queue=28.0, volume=0.0)

    assert dead.turnover_score == 0.0


def test_an_empty_queue_does_not_divide_by_zero():
    empty = _quote("empty", edge=0.01, queue=0.0, volume=1000.0)

    assert empty.turnover_score > 0.0


# ---------------------------------------------------------------- scan


class _FakeSession:
    """Stands in for requests.Session; serves gamma rows then CLOB books."""

    def __init__(self, rows, books):
        self.rows = rows
        self.books = books
        self.book_calls = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        if "/markets" in url:
            return _FakeResponse(self.rows)
        self.book_calls.append(params.get("token_id"))
        return _FakeResponse(self.books.get(params.get("token_id")))


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_scan_returns_only_profitable_pairs_ranked():
    rows = [
        _row(conditionId="0xwin", slug="win", clobTokenIds=json.dumps(["w-up", "w-down"])),
        _row(conditionId="0xlose", slug="lose", clobTokenIds=json.dumps(["l-up", "l-down"])),
    ]
    books = {
        "w-up": _book(bids=[(0.60, 100)], asks=[(0.62, 100)]),
        "w-down": _book(bids=[(0.38, 100)], asks=[(0.40, 100)]),
        "l-up": _book(bids=[(0.62, 100)], asks=[(0.63, 100)]),
        "l-down": _book(bids=[(0.40, 100)], asks=[(0.41, 100)]),
    }
    session = _FakeSession(rows, books)

    out = scan(min_volume_24h=1000.0, limit=10, session=session)

    assert [q.condition_id for q in out] == ["0xwin"]
    assert out[0].edge_per_pair == pytest.approx(0.02)


def test_scan_skips_books_for_markets_under_the_volume_bar():
    rows = [_row(conditionId="0xthin", volume24hr="10")]
    session = _FakeSession(rows, {})

    out = scan(min_volume_24h=50_000.0, limit=10, session=session)

    assert out == []
    assert session.book_calls == []
