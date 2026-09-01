"""The merge floor charges no gas, because Polymarket's relayer pays it.

Polymarket's docs are explicit (`/trading/wallets-auth`, "Execute Gasless
Transactions"): with a Relayer or Builder API key you approve token spending,
transfer funds and manage positions from the account wallet without paying gas,
and `/trading/positions/manage` lists split, merge and redeem as exactly those
position lifecycle methods. `merge()` already prints "MERGE (gasless via
Polymarket Relayer)" on the line above the one that used to subtract $0.05.

The number is not small against this strategy. A maker-only pair earns one tick
-- a cent on a $0.99 pair -- so a phantom five cents is five pairs of edge, and
it sat on the economic gate that decides GO.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account

import core_brain.order_manager as le
from core_brain.kpi import compute_pessimistic_analytics
from statistical_validation_run.artifacts import build_gate_rows
from tests.test_order_manager_merge import make_live_env

COND_ID = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"


def test_merge_preview_pays_no_gas_so_net_equals_expected(tmp_path, capsys):
    """The dry-run preview reports the full $1.00 per pair as net collateral.

    Merging 1.00 share returns exactly 1.00 pUSD and nothing is deducted on the
    way out. Reporting $0.95 understated every merge by 5%.
    """
    mock_client = MagicMock()
    mock_client.get_balance_allowance.return_value = {"balance": "10000000"}
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"

    with patch.dict(os.environ, make_live_env(acc, funder), clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "client", return_value=mock_client), \
         patch.object(le, "get_payout_denominator", return_value=0), \
         patch("urllib.request.urlopen") as mock_urlopen:
        le.merge(COND_ID, amount=1.0, live=False)

    mock_urlopen.assert_not_called()
    out = capsys.readouterr().out
    assert "MERGE (gasless via Polymarket Relayer)" in out
    assert "expected_usdc   $1.00" in out
    assert "net_collateral  $1.00" in out
    assert "estimated_gas" not in out
    assert "merge_gas_usd" not in out


def test_config_no_longer_carries_a_merge_gas_figure():
    """No `merge_gas_usd` on either config.

    Leaving the field at 0.0 would be worse than removing it: a reader takes a
    zero as "gas measured at nothing" rather than "gas is not ours to pay".
    `core_brain.gas` stays the honest path -- it reads the receipt and
    attributes the burn to whoever actually sent the transaction.
    """
    from core_brain.config import MakerConfig as CoreConfig
    from scoring.config import MakerConfig as ScoringConfig

    assert not hasattr(CoreConfig(), "merge_gas_usd")
    assert not hasattr(ScoringConfig(), "merge_gas_usd")


def test_economic_gate_asks_for_positive_expectancy_not_gas():
    """The GO gate asks whether the edge is positive, not whether it clears gas.

    A 2c expectancy failed the old gate and passes this one -- which is the
    whole point: it was never losing money, it was losing to a cost that does
    not exist.
    """
    from core_brain.config import load as load_cfg

    rows = build_gate_rows(
        closes=[],
        kpi={"trade_analytics": {"expectancy_usd": 0.02},
             "portfolio": {"starting_capital": 100.0}},
        cfg=load_cfg(),
        target_closes=None,
        matured_markouts=None,
        min_markouts=None,
    )

    economic = [r for r in rows if r["metric"] == "expectancy_usd"]
    assert len(economic) == 1
    assert economic[0]["threshold"] == "> $0.00"
    assert economic[0]["passed"] is True
    assert "gas" not in economic[0]["gate"].lower()
    assert "gas" not in economic[0]["rationale"].lower()


def test_pessimistic_variant_charges_only_the_tick():
    """The pessimistic column keeps its tick penalty and charges no gas.

    Crossing one tick worse is a real risk the fill model cannot rule out; a
    conversion gas is not. Callers that want to model some other per-close cost
    still pass `gas` explicitly.
    """
    closes = [
        {"method": "shadow_merge", "cost_basis": 99.0, "realized_pnl": 1.0,
         "shares": 100.0, "proceeds": 100.0}
        for _ in range(5)
    ]

    result = compute_pessimistic_analytics(closes, tick=0.01)

    # 100 shares * $0.01 = $1.00 of tick penalty against $1.00 of PnL, and
    # nothing else. With gas still charged this lands at -0.05% instead.
    assert result["gas_per_close"] == pytest.approx(0.0)
    assert result["mean_return_pct"] == pytest.approx(0.0)


def test_a_gate_missing_from_gate_order_raises_instead_of_vanishing():
    """Renaming a gate without updating GATE_ORDER must fail, not shrink the memo.

    This is how the economic gate went missing while the rename was being made:
    `build_gate_rows` filters by name, so an unlisted gate is dropped and the
    report still renders as if complete.
    """
    import statistical_validation_run.artifacts as art
    from core_brain.config import load as load_cfg

    with patch.object(art, "GATE_ORDER", [g for g in art.GATE_ORDER
                                          if g != "Economic (expectancy)"]):
        with pytest.raises(ValueError) as exc:
            art.build_gate_rows(
                closes=[],
                kpi={"trade_analytics": {"expectancy_usd": 0.02},
                     "portfolio": {"starting_capital": 100.0}},
                cfg=load_cfg(),
                target_closes=None,
                matured_markouts=None,
                min_markouts=None,
            )

    assert "Economic (expectancy)" in str(exc.value)
    assert "GATE_ORDER" in str(exc.value)
