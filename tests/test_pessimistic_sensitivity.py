"""Pessimistic sensitivity (Issue #55): the base/pessimistic side-by-side
`ci90_lower_pct` column and the GO-survives-pessimism verdict.

The fill model is conservative by construction, but a completion is still priced
at the ask -- the optimistic end of a taker. So on top of the base column the
report re-prices every taker resolution one tick worse plus a fixed `gas`, and
only gives GO when BOTH the base and the pessimistic `ci90_lower_pct` sit at or
above the threshold. The report never invents rebate income (`rebate_est = None`
in both variants).
"""
from __future__ import annotations

import pytest

from core_brain.kpi import (
    PESSIMISTIC_CONVERSION_GAS,
    build_sensitivity,
    compute_pessimistic_analytics,
    evaluate_stat_gate,
)


def _merge_closes(pnl: float, cost: float, shares: float, count: int = 5,
                  method: str = "shadow_merge") -> list[dict]:
    """`count` identical taker-resolved closes, so the CI has zero variance and
    the verdict follows the mean deterministically."""
    return [
        {"method": method, "cost_basis": cost, "realized_pnl": pnl,
         "shares": shares, "proceeds": cost + pnl}
        for _ in range(count)
    ]


def test_pessimistic_completion_price_is_one_tick_worse_than_base():
    from core_brain.shadow_exec import pessimistic_completion_price

    base, tick = 0.51, 0.01
    pessimistic = pessimistic_completion_price(base, tick)
    assert pessimistic == pytest.approx(0.52)
    assert pessimistic >= base + tick


def test_pessimistic_analytics_with_no_penalty_equals_base():
    closes = _merge_closes(pnl=0.30, cost=9.8, shares=10)
    base = evaluate_stat_gate(closes, 0.0, threshold_pct=1.0)
    pess = compute_pessimistic_analytics(closes, tick=0.0, gas=0.0)
    assert pess["ci90_lower_pct"] == pytest.approx(base["ci90_lower_pct"])
    assert pess["rebate_est"] is None


def test_pessimistic_analytics_recosts_taker_resolutions_one_tick_plus_gas():
    closes = _merge_closes(pnl=0.40, cost=19.6, shares=20)
    base = evaluate_stat_gate(closes, 0.0, threshold_pct=1.0)
    pess = compute_pessimistic_analytics(closes, tick=0.01, gas=0.05)

    # 0.40 / 19.6 = 2.04%; minus shares*tick (0.20) and gas (0.05) -> 0.15/19.6.
    expected = (0.15 / 19.6) * 100
    assert pess["ci90_lower_pct"] == pytest.approx(expected)
    assert pess["ci90_lower_pct"] < base["ci90_lower_pct"]
    assert pess["gas_per_close"] == 0.05
    assert pess["tick_per_share"] == 0.01


def test_non_taker_close_is_not_penalised():
    closes = [
        {"method": "return_capital", "cost_basis": 10.0, "realized_pnl": 0.5,
         "shares": 10, "proceeds": 10.5},
        {"method": "return_capital", "cost_basis": 10.0, "realized_pnl": 0.5,
         "shares": 10, "proceeds": 10.5},
    ]
    pess = compute_pessimistic_analytics(closes, tick=0.01, gas=0.05)
    assert pess["ci90_lower_pct"] == pytest.approx(5.0)
    assert pess["mean_return_pct"] == pytest.approx(5.0)


def test_verdict_is_go_only_when_both_variants_pass():
    # Wide enough edge that even one tick worse plus gas stays >= 1.0%.
    closes = _merge_closes(pnl=0.30, cost=9.8, shares=10)
    s = build_sensitivity(closes, tick=0.01, gas=0.05, threshold_pct=1.0)

    assert s["base"]["ci90_lower_pct"] >= 1.0
    assert s["pessimistic"]["ci90_lower_pct"] >= 1.0
    assert s["verdict"] == "GO"
    assert s["base"]["rebate_est"] is None
    assert s["pessimistic"]["rebate_est"] is None


def test_verdict_is_no_go_when_pessimism_breaks_the_edge():
    # Base passes but the pessimistic column falls below the threshold.
    closes = _merge_closes(pnl=0.40, cost=19.6, shares=20)
    s = build_sensitivity(closes, tick=0.01, gas=0.05, threshold_pct=1.0)

    assert s["base"]["ci90_lower_pct"] >= 1.0
    assert s["pessimistic"]["ci90_lower_pct"] < 1.0
    assert s["verdict"] == "NO-GO"
    assert "does not survive pessimism" in s["verdict_reason"]


def test_verdict_is_inconclusive_when_ci_cannot_be_computed():
    s = build_sensitivity([], tick=0.01, gas=0.05, threshold_pct=1.0)
    assert s["verdict"] == "INCONCLUSIVE"
    assert s["base"]["ci90_lower_pct"] is None
    assert s["pessimistic"]["ci90_lower_pct"] is None


def test_constant_matches_the_seeded_merge_gas_usd():
    from core_brain.config import load as load_cfg

    cfg = load_cfg()
    assert PESSIMISTIC_CONVERSION_GAS == pytest.approx(cfg.merge_gas_usd)