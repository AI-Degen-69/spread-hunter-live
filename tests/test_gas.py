"""Gas pricing for a close: the burn, the payer, and the difference between
"not measured" and "free"."""
from __future__ import annotations

import json
import io

import pytest

from core_brain import gas

FUNDER = "0xAbC0000000000000000000000000000000000001"


def _receipt(gas_used="0x52d09", price="0x5a3ba1a00", sender=FUNDER):
    return {"gasUsed": gas_used, "effectiveGasPrice": price, "from": sender}


# --- the burn -----------------------------------------------------------------


def test_parse_receipt_multiplies_gas_by_the_effective_price():
    # Arrange -- 339,209 gas at 24.2 gwei
    receipt = _receipt(gas_used=hex(339_209), price=hex(24_200_000_000))

    # Act
    cost = gas.parse_receipt(receipt, FUNDER)

    # Assert
    assert cost.gas_used == 339_209
    assert cost.pol == pytest.approx(339_209 * 24.2e9 / 1e18)


def test_parse_receipt_accepts_integer_quantities():
    # Some endpoints return ints rather than hex strings for these fields.
    cost = gas.parse_receipt(
        {"gasUsed": 100, "effectiveGasPrice": 10 ** 9, "from": FUNDER}, FUNDER)

    assert cost.gas_used == 100
    assert cost.pol == pytest.approx(1e-7)


def test_parse_receipt_returns_none_when_a_quantity_is_missing():
    # A receipt we cannot read must never be reported as free -- that is the
    # bug this module exists to fix.
    assert gas.parse_receipt({"gasUsed": "0x1", "from": FUNDER}, FUNDER) is None
    assert gas.parse_receipt({"effectiveGasPrice": "0x1"}, FUNDER) is None
    assert gas.parse_receipt("not a receipt", FUNDER) is None


def test_parse_receipt_returns_none_on_an_unparseable_quantity():
    assert gas.parse_receipt(
        {"gasUsed": "banana", "effectiveGasPrice": "0x1", "from": FUNDER},
        FUNDER) is None


# --- who paid -----------------------------------------------------------------


def test_we_paid_when_the_sender_is_our_funder():
    cost = gas.parse_receipt(_receipt(sender=FUNDER), FUNDER)

    assert cost.we_paid is True
    assert cost.usd(0.5) > 0


def test_the_relayer_paying_costs_us_nothing():
    # Merges are submitted through Polymarket's relayer, which sends from its
    # own address. Recording that burn as our expense understates PnL as badly
    # as a hardcoded zero overstates it.
    cost = gas.parse_receipt(_receipt(sender="0xRELAYER"), FUNDER)

    assert cost.we_paid is False
    assert cost.usd(0.5) == 0.0


def test_the_payer_check_is_case_insensitive():
    # Endpoints return addresses in different casings; an exact match would
    # call the same merge ours on one endpoint and not on another.
    cost = gas.parse_receipt(_receipt(sender=FUNDER.lower()), FUNDER.upper())

    assert cost.we_paid is True


def test_an_empty_funder_never_claims_we_paid():
    cost = gas.parse_receipt(_receipt(), funder="")

    assert cost.we_paid is False


# --- not measured is not free -------------------------------------------------


def test_close_gas_usd_is_none_without_a_price():
    assert gas.close_gas_usd("0xabc", FUNDER, None) is None


def test_close_gas_usd_is_none_when_the_receipt_will_not_load():
    assert gas.close_gas_usd("0xabc", FUNDER, 0.5, fetch=lambda h: None) is None


def test_close_gas_usd_is_zero_only_when_someone_else_paid():
    # None means "not measured"; 0.0 means "measured, and not ours". The
    # caller has to be able to tell them apart.
    got = gas.close_gas_usd("0xabc", FUNDER, 0.5,
                            fetch=lambda h: _receipt(sender="0xRELAYER"))

    assert got == 0.0


def test_close_gas_usd_prices_our_own_burn():
    got = gas.close_gas_usd(
        "0xabc", FUNDER, 0.09,
        fetch=lambda h: _receipt(gas_used=hex(339_209),
                                 price=hex(24_200_000_000)))

    # Rounded to 8 decimals, which is a hundredth of a cent -- far finer than
    # the ~$0.011 a real merge costs, and coarse enough to keep the column
    # readable.
    assert got == round(339_209 * 24.2e9 / 1e18 * 0.09, 8)


def test_fetch_receipt_returns_none_for_an_empty_hash():
    assert gas.fetch_receipt("") is None


def test_fetch_receipt_tries_the_next_endpoint_after_a_failure():
    calls = []

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def read(self):
            return json.dumps(self._p).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise OSError("endpoint down")
        return _Resp({"result": _receipt()})

    got = gas.fetch_receipt("0xabc", endpoints=["https://a", "https://b"],
                            opener=opener)

    assert got is not None
    assert len(calls) == 2


# --- the POL price ------------------------------------------------------------


def _round_data(answer: int) -> str:
    word = lambda v: format(v & (2 ** 256 - 1), "064x")     # noqa: E731
    return "0x" + word(1) + word(answer) + word(0) + word(0) + word(1)


def test_parse_latest_round_data_reads_eight_decimals():
    assert gas.parse_latest_round_data(_round_data(9_127_462)) == pytest.approx(
        0.09127462)


def test_parse_latest_round_data_refuses_a_negative_answer():
    # A negative price is a broken feed, not a cheap token. Passing it on would
    # turn a gas cost into a credit.
    assert gas.parse_latest_round_data(_round_data(-1)) is None


def test_parse_latest_round_data_refuses_a_zero_answer():
    assert gas.parse_latest_round_data(_round_data(0)) is None


def test_parse_latest_round_data_refuses_a_short_return():
    assert gas.parse_latest_round_data("0x1234") is None
    assert gas.parse_latest_round_data(None) is None


def test_pol_usd_price_is_none_when_no_endpoint_answers():
    def opener(req, timeout=None):
        raise OSError("down")

    assert gas.pol_usd_price(endpoints=["https://a"], opener=opener) is None


# --- the backfill -------------------------------------------------------------


def _closes_db(tmp_path, rows):
    import sqlite3

    db = tmp_path / "orders.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE closes (id INTEGER PRIMARY KEY, ts REAL, method TEXT,"
        " gas REAL, shares REAL, realized_pnl REAL, tx_hash TEXT)")
    for i, (gas_val, pnl, tx) in enumerate(rows):
        conn.execute(
            "INSERT INTO closes (id, ts, method, gas, shares, realized_pnl,"
            " tx_hash) VALUES (?,?,?,?,?,?,?)",
            (i + 1, float(i), "merge", gas_val, 100.0, pnl, tx))
    conn.commit()
    return conn


def test_unpriced_closes_skips_rows_already_priced_or_lacking_a_hash(tmp_path):
    conn = _closes_db(tmp_path, [
        (None, 1.0, "0xaaa"),      # waiting
        (0.011, 1.0, "0xbbb"),     # already priced
        (None, 1.0, None),         # no hash to look up
        (None, 1.0, ""),           # likewise
    ])

    got = gas.unpriced_closes(conn)

    assert [r["tx_hash"] for r in got] == ["0xaaa"]


def test_backfill_prices_a_close_and_subtracts_it_from_pnl(tmp_path):
    # `realized_pnl` is written gross by the merge path, so the gas has to be
    # taken off at the moment it becomes known -- in the same statement, or a
    # reader could see gas filled in against an unadjusted PnL and subtract it
    # a second time.
    conn = _closes_db(tmp_path, [(None, 1.0, "0xaaa")])

    stats = gas.backfill(conn, FUNDER, 0.09,
                         fetch=lambda h: _receipt(gas_used=hex(339_209),
                                                  price=hex(24_200_000_000)))

    row = conn.execute("SELECT gas, realized_pnl FROM closes").fetchone()
    expected = round(339_209 * 24.2e9 / 1e18 * 0.09, 8)
    assert stats["priced"] == 1
    assert row["gas"] == expected
    assert row["realized_pnl"] == pytest.approx(1.0 - expected)


def test_backfill_leaves_an_unreadable_receipt_null(tmp_path):
    # Pending, not free. Zeroing here would be the hardcoded assertion again.
    conn = _closes_db(tmp_path, [(None, 1.0, "0xaaa")])

    stats = gas.backfill(conn, FUNDER, 0.09, fetch=lambda h: None)

    row = conn.execute("SELECT gas, realized_pnl FROM closes").fetchone()
    assert stats == {"seen": 1, "priced": 0, "unreadable": 1, "usd": 0.0}
    assert row["gas"] is None
    assert row["realized_pnl"] == 1.0


def test_backfill_records_a_relayer_paid_merge_as_zero(tmp_path):
    # Measured and free is a real answer, and it must be distinguishable from
    # unmeasured -- so it writes 0.0 rather than staying NULL.
    conn = _closes_db(tmp_path, [(None, 1.0, "0xaaa")])

    gas.backfill(conn, FUNDER, 0.09,
                 fetch=lambda h: _receipt(sender="0xRELAYER"))

    row = conn.execute("SELECT gas, realized_pnl FROM closes").fetchone()
    assert row["gas"] == 0.0
    assert row["realized_pnl"] == 1.0


def test_backfill_does_nothing_without_a_price(tmp_path):
    # No price means no honest conversion; leave every row pending.
    conn = _closes_db(tmp_path, [(None, 1.0, "0xaaa")])

    stats = gas.backfill(conn, FUNDER, None, fetch=lambda h: _receipt())

    assert stats["seen"] == 0
    assert conn.execute("SELECT gas FROM closes").fetchone()["gas"] is None


def test_merge_close_records_gas_as_unknown_not_zero():
    # The hardcoded `gas=0.0` asserted the merge was free. NULL says it has
    # not been measured, which is the truth at the moment the close is written
    # -- the receipt is not mined yet.
    #
    # Read as text rather than imported: `core_brain.order_manager` is a
    # 150KB module whose import costs ~14 seconds here, and this assertion
    # needs the source, not the module.
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent.joinpath(
        "core_brain", "order_manager.py").read_text(encoding="utf-8")
    merge_close = src[src.index('method="merge"'):][:200]

    assert "gas=None" in merge_close
    assert "gas=0.0" not in merge_close
