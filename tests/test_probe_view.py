"""The dashboard's read-only view over a family-probe store.

The page this feeds exists to be watched while a probe runs, so the two things
under test are: it never lies about whether the probe is alive, and its numbers
are the SAME numbers `scripts/family_fill_report.py` prints. A page that
computed its own variant of "pairs per day" would be a second answer to argue
with, which is exactly what the probe exists to remove.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from core_brain.probe_view import (
    DEFAULT_QUEUE_BAR,
    LIVE_VOLUME_BAR,
    RefusedStore,
    STALE_AFTER_SEC,
    probe_report,
    probe_status,
    resolve_probe_db,
)
from scripts.family_fill_report import _span_days, simulate, summarise

BASE_TS = 1_700_000_000.0

_COLUMNS = (
    "ts", "run_id", "cycle", "condition_id", "family", "slug", "question",
    "series_title", "event_title", "market_group", "category",
    "tick", "volume_24h", "days_to_resolve", "gate_pass", "gate_reason",
    "best_bid_up", "best_ask_up", "best_bid_down", "best_ask_down",
    "spread_up", "spread_down", "touch_pair_cost",
    "q_bid_up", "q_ask_up", "q_bid_down", "q_ask_down",
    "tape_span_min", "tape_window_min", "tape_prints", "tape_prints_new",
    "vol_at_bid_up", "vol_at_ask_up",
    "qmin_bid_up", "qmin_ask_up", "qmin_worst", "book_ok", "is_bootstrap",
)


def _sample(**over) -> dict:
    """One quotable sample whose tape clears both legs inside a cycle."""
    row = {name: None for name in _COLUMNS}
    row.update({
        "ts": BASE_TS, "run_id": "probe-v3-vol25k", "cycle": 1,
        "condition_id": "0xcid", "family": "sports-x",
        "slug": "sports-x-match", "question": "Who wins?",
        "volume_24h": 50_000.0, "days_to_resolve": 1.0,
        "gate_pass": 0, "gate_reason": "volume 50000 < 125000",
        "best_bid_up": 0.40, "best_ask_up": 0.42,
        "best_bid_down": 0.58, "best_ask_down": 0.60,
        "spread_up": 0.02, "spread_down": 0.02,
        "touch_pair_cost": 0.98,
        "q_bid_up": 100.0, "q_ask_up": 100.0,
        "q_bid_down": 100.0, "q_ask_down": 100.0,
        "tape_span_min": 1.0, "tape_window_min": 60.0,
        "tape_prints": 10, "tape_prints_new": 5,
        "vol_at_bid_up": 1000.0, "vol_at_ask_up": 1000.0,
        "qmin_bid_up": 0.1, "qmin_ask_up": 0.1, "qmin_worst": 0.1,
        "book_ok": 1, "is_bootstrap": 0,
    })
    row.update(over)
    return row


def _timeline(count: int = 3, minutes: float = 6.0, **over) -> list[dict]:
    """An entry plus the later cycles the fill walk reads."""
    return [_sample(ts=BASE_TS + minutes * 60.0 * i, cycle=i + 1, **over)
            for i in range(count)]


def _store(path: Path, rows: list[dict], cycles: bool = True) -> Path:
    """Write a probe store with the real column list, in the real order."""
    conn = sqlite3.connect(path)
    columns = ", ".join(_COLUMNS)
    marks = ", ".join("?" for _ in _COLUMNS)
    conn.execute(f"CREATE TABLE probe_samples (id INTEGER PRIMARY KEY "
                 f"AUTOINCREMENT, {columns})")
    conn.executemany(
        f"INSERT INTO probe_samples ({columns}) VALUES ({marks})",
        [tuple(row[name] for name in _COLUMNS) for row in rows])
    conn.execute("CREATE TABLE probe_cycles (cycle INTEGER PRIMARY KEY, "
                 "run_id TEXT NOT NULL, ts REAL NOT NULL, tape_prints INTEGER,"
                 " markets_traded INTEGER, candidates INTEGER, "
                 "sampled INTEGER, seconds REAL)")
    if cycles:
        seen: dict[int, dict] = {}
        for row in rows:
            seen[int(row["cycle"])] = row
        conn.executemany(
            "INSERT INTO probe_cycles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(cycle, row["run_id"], row["ts"], 7000, 1000, 150, 150, 61.0)
             for cycle, row in sorted(seen.items())])
    conn.commit()
    conn.close()
    return path


# --- which store the page is allowed to open ---------------------------------


def test_the_production_registry_is_refused_by_name():
    # The probe itself refuses `orders.db` by name; a viewer pointed at the
    # live registry would read a money store with a schema it does not have,
    # and the failure would look like "the probe found nothing".
    with pytest.raises(RefusedStore):
        resolve_probe_db("data/orders.db")
    with pytest.raises(RefusedStore):
        resolve_probe_db(Path("/somewhere/else/ORDERS.DB"))


def test_an_explicit_path_wins_over_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SHL_PROBE_DB", str(tmp_path / "from_env.db"))
    assert resolve_probe_db(tmp_path / "explicit.db").name == "explicit.db"
    assert resolve_probe_db().name == "from_env.db"


# --- is the probe alive ------------------------------------------------------


def test_a_store_that_is_not_there_reads_as_missing_not_as_an_error(tmp_path):
    status = probe_status(tmp_path / "nothing.db")
    assert status["state"] == "MISSING"
    assert status["samples"] == 0
    assert status["run_id"] is None


def test_an_empty_store_reads_as_empty(tmp_path):
    path = _store(tmp_path / "probe.db", [_sample()])
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM probe_samples")
    conn.execute("DELETE FROM probe_cycles")
    conn.commit()
    conn.close()
    assert probe_status(path)["state"] == "EMPTY"


def test_a_fresh_cycle_reads_as_running(tmp_path):
    now = time.time()
    rows = [_sample(ts=now - 600.0, cycle=1), _sample(ts=now - 60.0, cycle=2)]
    path = _store(tmp_path / "probe.db", rows)
    status = probe_status(path, target_hours=6.0, now=now)
    assert status["state"] == "RUNNING"
    assert status["cycles"] == 2
    assert status["samples"] == 2
    assert status["age_sec"] == pytest.approx(60.0, abs=1.0)
    assert 0.0 < status["progress_pct"] < 100.0


def test_a_cycle_older_than_the_stale_bar_reads_as_stale(tmp_path):
    now = time.time()
    rows = [_sample(ts=now - STALE_AFTER_SEC - 600.0, cycle=1),
            _sample(ts=now - STALE_AFTER_SEC - 60.0, cycle=2)]
    path = _store(tmp_path / "probe.db", rows)
    assert probe_status(path, target_hours=6.0, now=now)["state"] == "STALE"


def test_reaching_the_target_reads_as_done_even_when_the_tape_went_quiet(tmp_path):
    # A finished run stops writing, so its last cycle is always older than the
    # stale bar. Calling that STALE would report every completed probe as dead.
    now = time.time()
    rows = [_sample(ts=now - 6.5 * 3600.0, cycle=1),
            _sample(ts=now - 3600.0, cycle=2)]
    path = _store(tmp_path / "probe.db", rows)
    status = probe_status(path, target_hours=5.0, now=now)
    assert status["state"] == "DONE"
    assert status["progress_pct"] == 100.0


def test_status_reports_the_run_id_and_the_last_cycle(tmp_path):
    now = time.time()
    path = _store(tmp_path / "probe.db", [_sample(ts=now - 30.0, cycle=9)])
    status = probe_status(path, now=now)
    assert status["run_id"] == "probe-v3-vol25k"
    assert status["last_cycle"]["cycle"] == 9
    assert status["last_cycle"]["sampled"] == 150


# --- the numbers must be the report's numbers --------------------------------


def test_the_page_reports_exactly_what_the_fill_report_computes(tmp_path):
    rows = _timeline(3)
    path = _store(tmp_path / "probe.db", rows)
    report = probe_report(path, queue_bar=DEFAULT_QUEUE_BAR)

    loaded = [dict(r) for r in rows]
    moments = simulate(loaded, DEFAULT_QUEUE_BAR, 0.99, 15.0, 15.0, 15.0,
                       False)
    expected = summarise(moments, loaded, _span_days(loaded))
    assert report["moments"] == len(moments)
    assert report["families"] == len(expected)
    assert report["totals"]["pairs_per_day"] == pytest.approx(
        sum(s.pairs_per_day for s in expected))
    assert report["totals"]["net_per_day"] == pytest.approx(
        sum(s.net_per_day for s in expected))
    assert report["rows"][0]["family"] == expected[0].family
    assert report["rows"][0]["gate"] == expected[0].gate


def test_the_rate_basis_is_carried_through_verbatim(tmp_path):
    path = _store(tmp_path / "probe.db", _timeline(3))
    assert probe_report(path)["rate_basis"] == "last 60min of tape"


def test_counting_adverse_moves_never_lowers_the_pair_count(tmp_path):
    path = _store(tmp_path / "probe.db", _timeline(3))
    strict = probe_report(path, count_adverse=False)
    upper = probe_report(path, count_adverse=True)
    assert upper["totals"]["pairs_per_day"] >= strict["totals"]["pairs_per_day"]


def test_a_store_with_no_qualifying_moment_still_answers(tmp_path):
    # 0 moments is the finding, not an error: it is what v2 reported.
    path = _store(tmp_path / "probe.db", _timeline(3, qmin_worst=9_000.0))
    report = probe_report(path)
    assert report["ok"] is True
    assert report["moments"] == 0
    assert report["rows"] == []
    assert report["rate_basis"] == "last 60min of tape"


def test_a_missing_store_answers_instead_of_raising(tmp_path):
    report = probe_report(tmp_path / "nothing.db")
    assert report["ok"] is False
    assert report["moments"] == 0


# --- the question v3 asks ----------------------------------------------------


def test_markets_are_split_at_the_live_volume_bar(tmp_path):
    rows = _timeline(3)
    rows += _timeline(3, condition_id="0xbig", volume_24h=900_000.0,
                      gate_pass=1, gate_reason=None)
    path = _store(tmp_path / "probe.db", rows)
    bands = {b["label"]: b for b in probe_report(path)["bands"]}
    under = bands["$25k-$125k"]
    over = bands["$125k+"]
    assert under["markets"] == 1 and over["markets"] == 1
    assert under["moments"] >= 1 and over["moments"] >= 1
    assert over["lo"] == LIVE_VOLUME_BAR


def test_the_refusal_histogram_counts_markets_not_just_samples(tmp_path):
    rows = _timeline(4)
    rows += _timeline(4, condition_id="0xother")
    path = _store(tmp_path / "probe.db", rows)
    refusals = probe_report(path)["refusals"]
    assert refusals[0]["markets"] == 2
    assert refusals[0]["samples"] == 8


def test_one_refusal_does_not_shatter_into_its_own_measurements(tmp_path):
    # The selector writes the number it read into the refusal, so counted
    # verbatim the single biggest reason the universe is empty ranks below its
    # own fragments, each with one market.
    rows = _timeline(2, gate_reason="24h volume 0 under bar 25,000")
    rows += _timeline(2, condition_id="0xb",
                      gate_reason="24h volume 19,509 under bar 25,000")
    rows += _timeline(2, condition_id="0xc",
                      gate_reason="24h volume 2 under bar 25,000")
    rows += _timeline(3, condition_id="0xd",
                      gate_reason="carries a submarket group label")
    path = _store(tmp_path / "probe.db", rows)
    refusals = probe_report(path)["refusals"]
    assert refusals[0]["reason"] == "24h volume N under bar N"
    assert refusals[0]["markets"] == 3
    assert refusals[0]["variants"] == 3
    assert refusals[0]["example"].startswith("24h volume ")
    assert refusals[1]["reason"] == "carries a submarket group label"


def test_the_measurement_window_in_a_refusal_is_not_blanked_out(tmp_path):
    # "24h" names the window the volume was measured over; blanking it would
    # merge refusals that are about different measurements.
    from core_brain.probe_view import normalise_reason

    assert normalise_reason("24h volume 19,509 under bar 25,000") == (
        "24h volume N under bar N")
    assert normalise_reason("horizon 235.4d over 30d") == "horizon Nd over Nd"
    assert normalise_reason("blocked dynamic/submarket keyword") == (
        "blocked dynamic/submarket keyword")


def test_the_verdict_refuses_to_widen_the_universe_on_zero_pairs(tmp_path):
    path = _store(tmp_path / "probe.db", _timeline(3, qmin_worst=9_000.0))
    verdict = probe_report(path)["verdict"]
    assert verdict["answer"] == "NO"
    assert "125" in verdict["recommendation"]


def test_the_verdict_names_the_family_when_one_actually_drains(tmp_path):
    path = _store(tmp_path / "probe.db", _timeline(3))
    verdict = probe_report(path)["verdict"]
    assert verdict["answer"] == "MAYBE"
    assert "sports-x" in verdict["headline"]
    assert "second" in verdict["recommendation"].lower()


# --- what the page is served ------------------------------------------------


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from dashboard.server import app

    # `?db=` is only allowed to name a store under the probe search root; the
    # tests keep theirs in tmp_path, so that is the root here.
    monkeypatch.setenv("SHL_PROBE_ROOT", str(tmp_path))
    return TestClient(app)


# --- an untrusted `?db=` is not a file reader --------------------------------


def test_a_requested_store_must_live_under_the_probe_root(monkeypatch, tmp_path):
    from core_brain.probe_view import resolve_request_db

    monkeypatch.setenv("SHL_PROBE_ROOT", str(tmp_path))
    inside = tmp_path / "runs" / "family_probe_v3.db"
    assert resolve_request_db(str(inside)).name == "family_probe_v3.db"
    with pytest.raises(RefusedStore):
        resolve_request_db(str(tmp_path.parent / "elsewhere.db"))
    with pytest.raises(RefusedStore):
        resolve_request_db(str(tmp_path / ".." / "escape.db"))


def test_a_requested_store_must_be_a_db_file(monkeypatch, tmp_path):
    from core_brain.probe_view import resolve_request_db

    monkeypatch.setenv("SHL_PROBE_ROOT", str(tmp_path))
    with pytest.raises(RefusedStore):
        resolve_request_db(str(tmp_path / ".env"))
    with pytest.raises(RefusedStore):
        resolve_request_db(str(tmp_path / "polymarket.key"))


def test_no_requested_store_falls_back_to_the_configured_one(monkeypatch, tmp_path):
    from core_brain.probe_view import resolve_request_db

    monkeypatch.setenv("SHL_PROBE_DB", str(tmp_path / "configured.db"))
    assert resolve_request_db(None).name == "configured.db"
    assert resolve_request_db("").name == "configured.db"


def test_the_probe_page_is_served_on_its_own_path(client):
    response = client.get("/probe")
    assert response.status_code == 200
    assert "Family Probe" in response.text
    # It is a research surface: nothing on it can start, stop or price
    # anything, so it carries no control token to leak.
    assert "__LIVE_DASH_CONTROL_TOKEN__" not in response.text


def test_the_status_endpoint_reads_the_store_it_is_pointed_at(client, tmp_path):
    now = time.time()
    path = _store(tmp_path / "probe.db", [_sample(ts=now - 30.0, cycle=3)])
    body = client.get("/api/probe/status", params={"db": str(path)}).json()
    assert body["state"] == "RUNNING"
    assert body["run_id"] == "probe-v3-vol25k"
    assert body["cycles"] == 1


def test_the_findings_endpoint_serves_both_modes(client, tmp_path):
    path = _store(tmp_path / "probe.db", _timeline(3))
    strict = client.get("/api/probe/findings",
                        params={"db": str(path)}).json()
    upper = client.get("/api/probe/findings",
                       params={"db": str(path), "adverse": 1}).json()
    assert strict["ok"] and upper["ok"]
    assert strict["count_adverse"] is False
    assert upper["count_adverse"] is True
    assert upper["totals"]["pairs_per_day"] >= strict["totals"]["pairs_per_day"]


def test_a_missing_store_is_a_200_that_says_missing(client, tmp_path):
    # The page is opened WHILE a probe runs. An operator who opens it before
    # launching one must see "not started", not a red error that reads like a
    # broken dashboard.
    body = client.get("/api/probe/status",
                      params={"db": str(tmp_path / "none.db")}).json()
    assert body["state"] == "MISSING"


def test_the_endpoints_refuse_to_be_pointed_at_the_live_registry(client):
    for route in ("/api/probe/status", "/api/probe/findings"):
        response = client.get(route, params={"db": "data/orders.db"})
        assert response.status_code == 400
        assert "registry" in response.json()["detail"]
