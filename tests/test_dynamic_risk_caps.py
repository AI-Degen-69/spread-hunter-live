"""Tests for dynamic portfolio-scaled risk caps and max_pair_cost = $0.99."""
import pytest
from unittest.mock import MagicMock

from core_brain.config import MakerConfig, derive_dynamic_caps, load as load_config
from core_brain.quotes import Inventory
from core_brain import risk
from core_brain.trader_loop import _fleet_state


def test_default_config_risk_parameters():
    """Verify default risk percentages and max_pair_cost."""
    cfg = MakerConfig()
    assert cfg.max_pair_cost == 0.99
    assert cfg.naked_risk_pct == 0.06
    assert cfg.order_risk_pct == 0.25
    assert cfg.bankroll_ceiling_pct == 0.90
    assert cfg.max_naked_usd == 6.0
    assert cfg.max_order_usd == 25.0
    assert cfg.max_total_usd == 90.0


def test_derive_dynamic_caps_100_usd():
    """At $100 portfolio value, caps match expected baseline."""
    cfg = MakerConfig(bankroll_usd=100.0)
    caps = derive_dynamic_caps(cfg, 100.0)
    assert caps["bankroll_usd"] == 100.0
    assert caps["max_naked_usd"] == 6.00   # 6% of $100
    assert caps["max_order_usd"] == 25.00  # 25% of $100
    assert caps["max_total_usd"] == 90.00  # 90% of $100


def test_derive_dynamic_caps_90_usd():
    """At $90 portfolio value, caps scale down proportionally."""
    cfg = MakerConfig()
    caps = derive_dynamic_caps(cfg, 90.0)
    assert caps["bankroll_usd"] == 90.0
    assert caps["max_naked_usd"] == 5.40   # 6% of $90
    assert caps["max_order_usd"] == 22.50  # 25% of $90
    assert caps["max_total_usd"] == 81.00  # 90% of $90


def test_derive_dynamic_caps_50_usd():
    """At $50 portfolio value, caps scale down to 50% baseline."""
    cfg = MakerConfig()
    caps = derive_dynamic_caps(cfg, 50.0)
    assert caps["bankroll_usd"] == 50.0
    assert caps["max_naked_usd"] == 3.00   # 6% of $50
    assert caps["max_order_usd"] == 12.50  # 25% of $50
    assert caps["max_total_usd"] == 45.00  # 90% of $50


def test_derive_dynamic_caps_fallback():
    """When portfolio value is None or non-positive, fallback to cfg.bankroll_usd."""
    cfg = MakerConfig(bankroll_usd=100.0)
    caps_none = derive_dynamic_caps(cfg, None)
    assert caps_none["bankroll_usd"] == 100.0
    assert caps_none["max_naked_usd"] == 6.00

    caps_zero = derive_dynamic_caps(cfg, 0.0)
    assert caps_zero["bankroll_usd"] == 100.0
    assert caps_zero["max_naked_usd"] == 6.00


def test_fleet_state_adopts_dynamic_caps_from_account_mark():
    """_fleet_state queries registry and derives dynamic caps for the cycle."""
    cfg = MakerConfig()
    mock_reg = MagicMock()
    mock_reg.get_latest_account_mark.return_value = {
        "account_value_usd": 90.0,
        "collateral_usd": 70.0,
        "positions_value_usd": 20.0,
    }

    state = _fleet_state(mock_reg, cfg)
    assert state["max_naked_usd"] == 5.40
    assert state["max_order_usd"] == 22.50
    assert state["max_total_usd"] == 81.00


def test_max_pair_cost_enforcement_at_99_cents():
    """risk.hard_block enforces the $0.99 ceiling on pair cost."""
    cfg = MakerConfig(max_pair_cost=0.99)
    # Inventory holding 10 UP shares at 0.50 avg cost
    inv = Inventory(up_shares=10, up_cost=5.0)
    book = {"best_bid": 0.48, "best_ask": 0.52}

    # DOWN bid at 0.495 -> combined pair cost = 0.50 + 0.495 = 0.995 >= 0.99 -> BLOCKED
    blocked_reason = risk.hard_block(cfg, inv, "DOWN", 0.495, book, book)
    assert blocked_reason is not None
    assert "max_pair_cost" in blocked_reason or "pair" in blocked_reason or "0.99" in blocked_reason

    # DOWN bid at 0.485 -> combined pair cost = 0.50 + 0.485 = 0.985 < 0.99 -> ALLOWED
    allowed_reason = risk.hard_block(cfg, inv, "DOWN", 0.485, book, book)
    assert allowed_reason is None


def test_dynamic_max_naked_usd_risk_utilization():
    """Risk utilization and sizing ladder adapt to dynamically lowered max_naked_usd."""
    cfg_90 = MakerConfig(max_naked_usd=5.40)
    inv = Inventory(up_shares=10, up_cost=5.40)  # exactly $5.40 naked on UP
    book = {"best_bid": 0.48, "best_ask": 0.52}

    util = risk.risk_utilization(cfg_90, inv, "UP")
    assert util == pytest.approx(1.0)

    # Hard block fires when naked reaches max_naked_usd
    block = risk.hard_block(cfg_90, inv, "UP", 0.54, book, book)
    assert block is not None
    assert "naked" in block
