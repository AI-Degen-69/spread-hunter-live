"""The family probe measures families over a day, not markets in a snapshot."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.family_probe import (
    build_rows,
    family_key,
    gate_verdict,
    last_seen,
    measure,
    new_trades,
    next_cycle,
    open_store,
    rank_candidates,
    refuse_production_store,
    run,
    slug_skeleton,
    tape_at_touch,
    touch,
    write_samples,
)
from scripts.family_probe_report import (
    _passes,
    hour_coverage,
    load_rows,
    summarise,
)


def _book(bids, asks):
    return {"bids": [{"price": str(p), "size": str(s)} for p, s in bids],
            "asks": [{"price": str(p), "size": str(s)} for p, s in asks]}


def _trade(ts, price, size, side="BUY", outcome="Yes"):
    return {"timestamp": ts, "price": price, "size": size,
            "side": side, "outcome": outcome}


def _meta(**over):
    base = dict(condition_id="0xabc", tokens=["up", "down"],
                question="Team A vs Team B Game 2 Winner",
                slug="dota-2-team-a-vs-team-b-game-2-winner",
                category="esports", market_type="", market_group="",
                series_title="Dota 2", event_title="",
                tick=0.01, volume_24h=200_000.0, days_to_resolve=0.5)
    base.update(over)
    return base


# --- family classification ----------------------------------------------------


def test_names_the_submarket_template_not_the_game():
    assert family_key("Team A vs Team B Game 2 Winner",
                      "dota-2-a-vs-b-game-2-winner",
                      "Dota 2", "") == "esports-dota2-game-winner"


def test_map_submarket_is_its_own_family():
    assert family_key("A vs B Map 2 Winner", "cs2-a-vs-b-map-2-winner",
                      "CS2", "") == "esports-cs2-map-winner"


def test_two_instances_of_one_template_share_a_family():
    first = family_key("Alpha vs Beta Game 2 Winner",
                       "dota-2-alpha-vs-beta-game-2-winner", "Dota 2", "")
    second = family_key("Gamma vs Delta Game 2 Winner",
                        "dota-2-gamma-vs-delta-game-2-winner", "Dota 2", "")
    assert first == second


def test_the_esports_match_is_a_different_family_from_its_games():
    match = family_key("A vs B", "dota-2-a-vs-b-2026-09-03", "Dota 2", "")
    game = family_key("A vs B Game 2 Winner",
                      "dota-2-a-vs-b-2026-09-03-game2", "Dota 2", "")
    assert match == "esports-dota2-match"
    assert game != match


def test_the_venue_slug_form_of_a_five_minute_market_is_the_same_family():
    slug_form = family_key("", "btc-updown-5m-1788455100", "", "")
    long_form = family_key("Bitcoin Up or Down - 5 minute",
                           "bitcoin-up-or-down-5-minute", "", "")
    assert slug_form == long_form == "crypto-5min"


def test_five_minute_crypto_is_its_own_family():
    assert family_key("Bitcoin Up or Down - 5 minute",
                      "bitcoin-up-or-down-5-minute-2026-09-03",
                      "", "") == "crypto-5min"


def test_weather_is_its_own_family():
    assert family_key("Highest temperature in NYC today",
                      "highest-temperature-nyc-2026-09-03", "", "") == "weather"


def test_fallback_collapses_dates_and_digits():
    assert slug_skeleton("some-question-2026-09-03-v2") == "some-question-v#"


def _open(slug, volume):
    return dict(question="", slug=slug, series_title="", event_title="",
                volume_24h=volume)


def test_candidates_are_open_markets_ranked_by_tape_then_volume():
    open_markets = {"a": _open("weather-a", 10.0), "b": _open("weather-b", 900.0),
                    "c": _open("weather-c", 500.0)}
    # "z" traded hardest but is not open, so it is not a candidate at all.
    assert rank_candidates(open_markets, {"z": 99, "a": 5}, 3) == ["a", "b", "c"]


def test_unnamed_markets_share_one_stratum_so_they_cannot_crowd_out_a_family():
    """The `other:` fallback keys on the slug, so it makes families of one."""
    open_markets = {f"o{i}": _open(f"unrelated-question-{i}", 100.0)
                    for i in range(10)}
    open_markets["five"] = _open("btc-updown-5m-1788455100", 1.0)
    assert "five" in rank_candidates(open_markets, {}, 2)


def test_a_small_family_is_sampled_before_a_big_familys_second_member():
    """A live five-minute market is tiny; top-down ranking never reaches it."""
    open_markets = {
        "big1": _open("us-election-a", 5_000_000.0),
        "big2": _open("us-election-b", 4_000_000.0),
        "tiny": _open("btc-updown-5m-1788455100", 40.0),
    }
    assert rank_candidates(open_markets, {}, 2) == ["big1", "tiny"]


def test_a_closed_market_is_never_a_candidate_however_busy():
    assert rank_candidates({}, {"z": 999}, 5) == []


# --- gates --------------------------------------------------------------------


def test_records_which_gate_refuses_a_blocked_family():
    passed, reason = gate_verdict(_meta(), min_volume_usd=1.0, max_days=30.0)
    assert passed is False
    assert "blocked" in reason


def test_records_the_volume_bar_separately_from_the_block():
    meta = _meta(question="Rain in London tomorrow?", slug="rain-london",
                 category="weather", series_title="", volume_24h=1_000.0)
    passed, reason = gate_verdict(meta, min_volume_usd=125_000.0, max_days=30.0)
    assert passed is False
    assert "volume" in reason and "blocked" not in reason


def test_admits_a_market_that_clears_every_gate():
    meta = _meta(question="Rain in London tomorrow?", slug="rain-london",
                 category="weather", series_title="", volume_24h=500_000.0)
    assert gate_verdict(meta, 125_000.0, 30.0) == (True, "")


# --- measurement --------------------------------------------------------------


def test_touch_reads_best_levels_and_their_sizes():
    assert touch(_book([(0.40, 100), (0.39, 50)],
                       [(0.42, 70), (0.43, 20)])) == (0.40, 0.42, 100.0, 70.0)


def test_touch_refuses_a_one_sided_book():
    assert touch(_book([(0.40, 100)], [])) == (None, None, 0.0, 0.0)


def test_new_trades_drops_prints_an_earlier_cycle_already_counted():
    rows = [_trade(100, 0.4, 10), _trade(200, 0.4, 10), _trade(300, 0.4, 10)]
    assert [r["timestamp"] for r in new_trades(rows, 150)] == [200, 300]


def test_tape_at_touch_attributes_a_no_print_to_the_mirrored_level():
    # A NO buy at 0.58 is a YES sell at 0.42, which hits a 0.42 bid.
    rows = [_trade(0, 0.58, 25, side="BUY", outcome="No"),
            _trade(120, 0.58, 25, side="BUY", outcome="No")]
    vol_bid, vol_ask, span_min, prints = tape_at_touch(rows, 0.42, 0.44)
    assert (vol_bid, vol_ask, prints) == (50.0, 0.0, 2)
    assert span_min == pytest.approx(2.0)


def test_measure_reports_the_touch_pair_both_makers_could_assemble():
    row = measure(_book([(0.40, 100)], [(0.42, 100)]),
                  _book([(0.57, 80)], [(0.59, 80)]),
                  [_trade(0, 0.40, 50, side="SELL"),
                   _trade(60, 0.40, 50, side="SELL")])
    assert row["touch_pair_cost"] == pytest.approx(0.97)
    assert row["spread_up"] == pytest.approx(0.02)


def test_measure_records_no_queue_reading_when_nothing_traded_at_our_level():
    row = measure(_book([(0.40, 100)], [(0.42, 100)]),
                  _book([(0.57, 80)], [(0.59, 80)]),
                  # Inside the spread: the tape traded, but not at our level.
                  [_trade(0, 0.41, 50, side="SELL"),
                   _trade(60, 0.41, 50, side="SELL")])
    assert row["qmin_bid_up"] is None
    assert row["qmin_worst"] is None


def test_an_open_market_with_no_book_is_recorded_not_dropped():
    """"Listed and unquotable" and "absent" must not look the same."""
    metas = {"0xabc": _meta()}
    rows = build_rows(metas, {"up": _book([(0.40, 10)], []), "down": None},
                      {"0xabc": []}, {}, 1000.0, "r", 1, 125_000.0, 30.0)
    assert len(rows) == 1
    assert rows[0]["book_ok"] == 0
    assert rows[0]["qmin_worst"] is None


def test_measure_refuses_a_crossed_or_empty_book():
    assert measure(_book([(0.40, 10)], []), None, []) is None


# --- one cycle ----------------------------------------------------------------


def _cycle_inputs():
    metas = {"0xabc": _meta()}
    books = {"up": _book([(0.40, 100)], [(0.42, 100)]),
             "down": _book([(0.57, 80)], [(0.59, 80)])}
    tapes = {"0xabc": [_trade(0, 0.40, 50, side="SELL"),
                       _trade(60, 0.40, 50, side="SELL")]}
    return metas, books, tapes


def test_first_sight_of_a_market_is_marked_bootstrap():
    metas, books, tapes = _cycle_inputs()
    rows = build_rows(metas, books, tapes, {}, 1000.0, "r", 1, 125_000.0, 30.0)
    assert [r["is_bootstrap"] for r in rows] == [1]


def test_a_second_cycle_separates_new_prints_from_the_window_it_measures():
    metas, books, tapes = _cycle_inputs()
    tapes["0xabc"].append(_trade(120, 0.40, 50, side="SELL"))
    rows = build_rows(metas, books, tapes, {"0xabc": 90.0}, 1000.0, "r", 2,
                      125_000.0, 30.0)
    assert rows[0]["is_bootstrap"] == 0
    # One print is new; the queue is still measured on all three.
    assert rows[0]["tape_prints_new"] == 1
    assert rows[0]["tape_prints"] == 3


def test_a_cycle_row_carries_family_and_gate_together():
    metas, books, tapes = _cycle_inputs()
    row = build_rows(metas, books, tapes, {}, 1000.0, "r", 1,
                     125_000.0, 30.0)[0]
    assert row["family"] == "esports-dota2-game-winner"
    assert row["gate_pass"] == 0
    assert "blocked" in row["gate_reason"]


# --- store --------------------------------------------------------------------


def test_refuses_the_production_registry_by_name():
    with pytest.raises(SystemExit):
        refuse_production_store(Path("data/orders.db"))


def test_resume_reads_back_the_newest_sample_per_market(tmp_path):
    conn = open_store(tmp_path / "probe.db")
    metas, books, tapes = _cycle_inputs()
    write_samples(conn, build_rows(metas, books, tapes, {}, 1000.0, "r", 1,
                                   125_000.0, 30.0))
    assert last_seen(conn) == {"0xabc": 1000.0}
    conn.close()


def test_a_resumed_run_continues_the_cycle_count(tmp_path):
    db = tmp_path / "probe.db"
    calls = []

    def fake_get(url, params):
        calls.append(url)
        if url.endswith("/trades") and "market" not in params:
            return [{"conditionId": "0xabc", "transactionHash": "h",
                     "asset": "up", "timestamp": 1, "size": 1}]
        if url.endswith("/markets"):
            return [{"conditionId": "0xabc", "clobTokenIds": '["up","down"]',
                     "question": "A vs B Game 2 Winner",
                     "slug": "dota-2-a-vs-b-game-2-winner",
                     "volume24hr": 200000, "orderPriceMinTickSize": 0.01}]
        if url.endswith("/book"):
            return (_book([(0.40, 100)], [(0.42, 100)])
                    if params["token_id"] == "up"
                    else _book([(0.57, 80)], [(0.59, 80)]))
        return [_trade(0, 0.40, 50, side="SELL"),
                _trade(60, 0.40, 50, side="SELL")]

    for _ in range(2):
        run(hours=0.0, interval_min=0.0, db_path=db, run_id="r",
            candidates=10, pages=1, open_pages=1, per_second=0.0,
            min_volume_usd=125_000.0, max_days=30.0, get=fake_get,
            sleep=lambda _s: None)
    conn = sqlite3.connect(db)
    assert next_cycle(conn) == 3
    assert conn.execute("SELECT COUNT(*) FROM probe_samples").fetchone()[0] == 2
    conn.close()


def test_a_failing_cycle_does_not_kill_the_day(tmp_path):
    def broken_get(url, params):
        raise RuntimeError("venue down")

    assert run(hours=0.0, interval_min=0.0, db_path=tmp_path / "p.db",
               run_id="r", candidates=10, pages=1, open_pages=1,
               per_second=0.0, min_volume_usd=1.0, max_days=30.0,
               get=broken_get, sleep=lambda _s: None) == 0


# --- report -------------------------------------------------------------------


def _sample(**over):
    base = dict(family="f", condition_id="0x1", ts=0.0, qmin_worst=2.0,
                touch_pair_cost=0.97, spread_up=0.02, gate_pass=0, book_ok=1,
                gate_reason="blocked dynamic/submarket keyword")
    base.update(over)
    return base


def test_pass_needs_both_the_queue_and_the_pair_cost():
    assert _passes(_sample(), 15.0, 0.99) is True
    assert _passes(_sample(qmin_worst=40.0), 15.0, 0.99) is False
    assert _passes(_sample(touch_pair_cost=1.00), 15.0, 0.99) is False


def test_a_missing_queue_reading_is_a_refusal_not_a_pass():
    assert _passes(_sample(qmin_worst=None), 15.0, 0.99) is False


def test_summarise_separates_markets_from_samples():
    rows = [_sample(condition_id="0x1"), _sample(condition_id="0x1"),
            _sample(condition_id="0x2")]
    stat = summarise(rows, 15.0, 0.99)[0]
    assert (stat.samples, stat.markets) == (3, 2)


def test_summarise_reports_the_p90_not_only_the_median():
    rows = [_sample(qmin_worst=q) for q in range(1, 11)]
    stat = summarise(rows, 15.0, 0.99)[0]
    assert stat.queue_med == 5
    assert stat.queue_p90 == 9


def test_summarise_names_the_most_common_refusal():
    rows = [_sample(gate_reason="blocked dynamic/submarket keyword"),
            _sample(gate_reason="blocked dynamic/submarket keyword"),
            _sample(gate_reason="24h volume 1,000 under bar 125,000")]
    assert summarise(rows, 15.0, 0.99)[0].gate.startswith("blocked")


def test_a_family_that_is_always_listed_and_never_quotable_reads_as_such():
    rows = [_sample(book_ok=0, qmin_worst=None, touch_pair_cost=None)
            for _ in range(4)]
    stat = summarise(rows, 15.0, 0.99)[0]
    assert (stat.samples, stat.book_pct, stat.pass_pct) == (4, 0.0, 0.0)


def test_hour_coverage_counts_the_clock_hours_a_family_was_open():
    rows = [_sample(ts=0.0), _sample(ts=3600.0 * 5)]
    assert len(hour_coverage(rows)["f"]) == 2


def test_report_keeps_a_markets_first_sample(tmp_path):
    """A family whose members live under two cycles has only first samples."""
    db = tmp_path / "probe.db"
    conn = open_store(db)
    metas, books, tapes = _cycle_inputs()
    write_samples(conn, build_rows(metas, books, tapes, {}, 1000.0, "r", 1,
                                   125_000.0, 30.0))
    conn.close()
    assert len(load_rows(db, None)) == 1
