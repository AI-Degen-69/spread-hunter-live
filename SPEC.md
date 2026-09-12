# SPEC: Issue #197

## Goal

Add a 3-way realized PnL attribution split (`maker_merged`, `taker_completed`, `single_buy_exit`) across the statistical validation report, KPI payloads, and the dashboard UI, matching the hand-calculated 25/35/41 ratio on the shadow rehearsal benchmark.

## Scope

### In Scope
1. **Core KPI Calculation (`core_brain/kpi.py`)**:
   - Helper to identify taker-completed pairs from orders table: any `(pair_id, token_id)` with >1 orders represents an original resting order replaced by a taker completion order.
   - Helper `pnl_by_fill_path(closes, taker_pairs)` that classifies realized PnL into:
     - `maker_merged`: closes with method in `MERGE_METHODS` whose resolved `pair_id` (from `tx_hash.split(":")[0]`) is NOT taker-completed.
     - `taker_completed`: closes with method in `MERGE_METHODS` whose resolved `pair_id` IS taker-completed.
     - `single_buy_exit`: closes with method in `("single_buy_exit", "naked_exit")`.
   - Returns structured dict:
     ```python
     {
         "total": float,
         "by_path": {
             "maker_merged": float,
             "taker_completed": float,
             "single_buy_exit": float,
         },
         "pct": {
             "maker_merged": Optional[float],
             "taker_completed": Optional[float],
             "single_buy_exit": Optional[float],
         },
     }
     ```
   - Exposed in `trade_analytics["pnl_by_fill_path"]`, `run_profitability["pnl_by_fill_path"]`, and top-level KPI `pnl_by_fill_path`.

2. **Validation Reports & Artifacts (`statistical_validation_run/artifacts.py`)**:
   - Include `pnl_by_fill_path` in `stat_validation["pnl_by_fill_path"]` inside `write_artifacts()`.
   - Update `render_report_md()` under `## Rescue & failure modes` to output the dollar and percentage breakdown for each of the 3 paths, with a clear disclaimer that this is a shadow-rehearsal estimate.

3. **Dashboard UI (`dashboard/static/app.js`)**:
   - Surface the 3-way breakdown clearly beside realized PnL in run profitability and analytics.

4. **Testing (`tests/test_pnl_by_fill_path.py`)**:
   - Seeded SQLite database asserting exact totals and percentages for the 25/35/41 split using `pytest.approx`.
   - Edge cases: 0 closes (total 0.0, percentages None), only maker merges, only single-buy exits.

### Out of Scope
- Modifying live order execution, fill models, or trading loops.
- Altering markout calculation or adverse selection.
