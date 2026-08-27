"""Unit tests for Strict Paired Inventory Accounting & Proportional Merge P&L."""
import io
import json
from unittest.mock import patch, MagicMock
import pytest

from core_brain.config import MakerConfig
from core_brain.quotes import Inventory, decide_quotes
from core_brain.risk import size_for, naked_side
from core_brain.order_registry import OrderRegistry, OrderRecord, FillRecord
from core_brain.order_manager import _submit_and_log


def test_flat_inventory_quotes_both_sides():
    """When flat, both UP and DOWN are quoted equally."""
    cfg = MakerConfig(strict_paired_inventory=True, quote_shares=10, min_quote_shares=5)
    inv = Inventory(up_shares=0, down_shares=0, up_cost=0.0, down_cost=0.0)
    up = {"best_bid": 0.48, "best_ask": 0.52, "token_id": "asset_id_up"}
    down = {"best_bid": 0.48, "best_ask": 0.52, "token_id": "asset_id_dn"}

    intents, why = decide_quotes(cfg, up, down, inv, 1e9, None)
    assert len(intents) == 2
    sides = {i.side for i in intents}
    assert sides == {"UP", "DOWN"}
    assert all(i.size >= 5 for i in intents)


def test_unhedged_heavy_side_strictly_blocked():
    """When UP-heavy (e.g. 10 UP, 0 DOWN), UP is strictly blocked and only DOWN is quoted."""
    cfg = MakerConfig(
        strict_paired_inventory=True,
        size_mode="shares",
        quote_shares=20,
        min_quote_shares=5,
        max_pair_cost=0.99,
    )
    inv = Inventory(up_shares=10, down_shares=0, up_cost=3.0, down_cost=0.0)  # avg UP = 0.30
    up = {"best_bid": 0.30, "best_ask": 0.32, "token_id": "asset_id_up"}
    down = {"best_bid": 0.65, "best_ask": 0.68, "token_id": "asset_id_dn"}  # 0.30 + 0.68 = 0.98 <= 1.00

    assert naked_side(inv) == "UP"
    assert size_for(cfg, inv, "UP", 0.30) == 0

    intents, why = decide_quotes(cfg, up, down, inv, 1e9, None)
    assert len(intents) == 1
    assert intents[0].side == "DOWN"
    assert intents[0].size == 10  # exactly matched to deficit
    # Price is capped at max_pair_cost - avg_up = 0.99 - 0.30 = 0.69
    assert intents[0].price <= 0.69


def test_pair_inversion_blocks_deficit_quote():
    """When market moves against unhedged position (heavy_avg + opp_ask > 1.00), quote is blocked."""
    cfg = MakerConfig(
        strict_paired_inventory=True,
        quote_shares=20,
        min_quote_shares=5,
        max_pair_cost=0.99,
    )
    inv = Inventory(up_shares=10, down_shares=0, up_cost=4.0, down_cost=0.0)  # avg UP = 0.40
    up = {"best_bid": 0.20, "best_ask": 0.25, "token_id": "asset_id_up"}
    down = {"best_bid": 0.72, "best_ask": 0.75, "token_id": "asset_id_dn"}  # 0.40 + 0.75 = 1.15 > 1.00

    intents, why = decide_quotes(cfg, up, down, inv, 1e9, None)
    assert intents == []
    assert "pair inverted" in why


def test_proportional_merge_cost_basis_math(tmp_path, monkeypatch):
    """Partial merge invokes production merge logging and records proportional cost basis."""
    db_file = tmp_path / "test_orders.db"
    reg = OrderRegistry(db_path=db_file)
    cond = "0xcond123"

    # Insert historical orders and fills: 20 UP shares @ 0.30 ($6.00), 5 DOWN shares @ 0.65 ($3.25)
    # Total historical spend = $9.25
    o1 = OrderRecord(id="u1", condition_id=cond, token_id="asset_id_up", side="BUY", price=0.30, original_size=20, status="filled", posted_ts=1, last_polled_ts=1, run_id="r1")
    o2 = OrderRecord(id="u2", condition_id=cond, token_id="asset_id_dn", side="BUY", price=0.65, original_size=5, status="filled", posted_ts=1, last_polled_ts=1, run_id="r1")
    reg.create_order(o1)
    reg.create_order(o2)
    reg.record_fill(FillRecord(trade_id="t1", order_uuid="u1", size=20, price=0.30, venue_ts=1, recorded_ts=1, run_id="r1"))
    reg.record_fill(FillRecord(trade_id="t2", order_uuid="u2", size=5, price=0.65, venue_ts=1, recorded_ts=1, run_id="r1"))

    # Construct merge call_data with 5 shares (5 * 10^6 = 5000000 = 0x00000000000000000000000000000000000000000000000000000000004c4b40)
    # call_data layout: 10 chars selector + 4 * 64 chars params + 5th 64-char param for amount
    prefix = "0xmerge000" + ("0" * 64) * 4
    amount_hex = f"{5000000:064x}"
    call_data = prefix + amount_hex

    # Mock relayer submission response and redirect OrderRegistry to reg
    mock_resp = io.BytesIO(json.dumps({"state": "STATE_EXECUTED", "transactionHash": "0xtxmerge123"}).encode("utf-8"))
    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", mock_urlopen), patch("core_brain.order_registry.OrderRegistry", return_value=reg):
        _submit_and_log(
            action="MERGE",
            condition_id=cond,
            funder="0xfunder",
            signer_addr="0xsigner",
            call_data=call_data,
            nonce=1,
            deadline=9999999999,
            payload={},
            headers={},
            relayer_url="http://mock-relayer",
        )

    # Assert persisted close row in sqlite closes table
    with reg._conn() as conn:
        row = conn.execute("SELECT * FROM closes WHERE condition_id = ?", (cond,)).fetchone()
        assert row is not None
        assert row["method"] == "merge"
        assert row["shares"] == pytest.approx(5.0)
        assert row["cost_basis"] == pytest.approx(4.75)  # 5 * (0.30 + 0.65)
        assert row["proceeds"] == pytest.approx(5.00)
        assert row["realized_pnl"] == pytest.approx(0.25)  # 5.00 - 4.75
        assert row["up_cost_removed"] == pytest.approx(1.50)  # 5 * 0.30
        assert row["dn_cost_removed"] == pytest.approx(3.25)  # 5 * 0.65
        assert row["tx_hash"] == "0xtxmerge123"
