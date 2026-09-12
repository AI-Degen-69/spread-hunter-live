"""Tests for PnL attribution split by fill path (Issue #197).

Proves the 3-way attribution:
- maker_merged: pairs merged via resting maker orders on both legs.
- taker_completed: pairs completed via a taker order (identified by >1 orders under (pair_id, token_id)).
- single_buy_exit: single-buy rescue / liquidation closes (single_buy_exit, naked_exit).

Target benchmark: 25% maker_merged / 35% taker_completed / 40% single_buy_exit (approx 25/35/41).
"""
from __future__ import annotations

import sqlite3
import time
import pytest
from pathlib import Path

from core_brain import kpi as kpi_mod
from core_brain.kpi import report, pnl_by_fill_path, find_taker_completed_pairs
from core_brain.order_registry import SCHEMA, CloseRecord, FillRecord, OrderRecord, OrderRegistry

RUN = "run-pnl-path"
CID_1 = "0xmarket1"
CID_2 = "0xmarket2"
CID_3 = "0xmarket3"
TOK_UP = "tok-up"
TOK_DN = "tok-dn"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    db_file = tmp_path / "orders.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


def _create_order(reg: OrderRegistry, uid: str, cid: str, token: str, price: float, size: float,
                  status: str, pair_id: str) -> None:
    now = int(time.time())
    reg.create_order(OrderRecord(
        id=uid, condition_id=cid, token_id=token, side="BUY", price=price,
        original_size=size, status=status, posted_ts=now, last_polled_ts=now,
        order_id=f"venue-{uid}", pair_id=pair_id, run_id=RUN,
    ))


def _log_close(reg: OrderRegistry, cid: str, method: str, shares: float, pnl: float,
               tx: str = "") -> None:
    reg.log_close(CloseRecord(
        ts=time.time(), condition_id=cid, method=method, shares=shares,
        cost_basis=shares - pnl, proceeds=shares, fee=0.0, gas=0.0,
        realized_pnl=pnl,
        up_cost_removed=(shares - pnl) / 2.0, dn_cost_removed=(shares - pnl) / 2.0,
        tx_hash=tx, run_id=RUN,
    ))


def test_pnl_by_fill_path_empty():
    res = pnl_by_fill_path([], set())
    assert res["total"] == pytest.approx(0.0)
    assert res["by_path"] == {
        "maker_merged": 0.0,
        "taker_completed": 0.0,
        "single_buy_exit": 0.0,
    }
    assert res["pct"] == {
        "maker_merged": None,
        "taker_completed": None,
        "single_buy_exit": None,
    }


def test_pnl_by_fill_path_benchmark_split(temp_db):
    reg = OrderRegistry(temp_db)

    # 1. Maker-merged pair: "pair-maker" has exactly 1 order per (pair_id, token_id)
    _create_order(reg, "o-m-up", CID_1, TOK_UP, 0.48, 10.0, "filled", "pair-maker")
    _create_order(reg, "o-m-dn", CID_1, TOK_DN, 0.48, 10.0, "filled", "pair-maker")
    # PnL: +$0.25 (25%)
    _log_close(reg, CID_1, "shadow_merge", 10.0, 0.25, tx="pair-maker")

    # 2. Taker-completed pair: "pair-taker" has 2 orders under (pair-taker, TOK_UP)
    # e.g., original resting cancelled + replacement taker completion filled
    _create_order(reg, "o-t-orig", CID_2, TOK_UP, 0.48, 10.0, "cancelled", "pair-taker")
    _create_order(reg, "o-t-taker", CID_2, TOK_UP, 0.51, 10.0, "filled", "pair-taker")
    _create_order(reg, "o-t-dn", CID_2, TOK_DN, 0.48, 10.0, "filled", "pair-taker")
    # PnL: +$0.35 (35%)
    _log_close(reg, CID_2, "shadow_merge", 10.0, 0.35, tx="pair-taker")

    # 3. Rescue exits: single_buy_exit / naked_exit
    # PnL: +$0.41 (41% approx)
    _log_close(reg, CID_3, "single_buy_exit", 10.0, 0.41, tx="pair-rescue")

    total_expected = 0.25 + 0.35 + 0.41  # 1.01

    rep = report(db_path=str(temp_db), run_id=RUN)
    
    assert "pnl_by_fill_path" in rep
    pnl_split = rep["pnl_by_fill_path"]
    assert pnl_split["total"] == pytest.approx(total_expected)
    assert pnl_split["by_path"]["maker_merged"] == pytest.approx(0.25)
    assert pnl_split["by_path"]["taker_completed"] == pytest.approx(0.35)
    assert pnl_split["by_path"]["single_buy_exit"] == pytest.approx(0.41)

    assert pnl_split["pct"]["maker_merged"] == pytest.approx(0.25 / total_expected, abs=0.01)
    assert pnl_split["pct"]["taker_completed"] == pytest.approx(0.35 / total_expected, abs=0.01)
    assert pnl_split["pct"]["single_buy_exit"] == pytest.approx(0.41 / total_expected, abs=0.01)

    # Also check presence in trade_analytics and run_profitability
    assert "pnl_by_fill_path" in rep["trade_analytics"]
    assert rep["trade_analytics"]["pnl_by_fill_path"]["by_path"]["maker_merged"] == pytest.approx(0.25)

    assert "pnl_by_fill_path" in rep["run_profitability"]
    assert rep["run_profitability"]["pnl_by_fill_path"]["by_path"]["taker_completed"] == pytest.approx(0.35)
