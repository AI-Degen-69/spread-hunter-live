# Implementation Plan: Issue #197

## Context
Report realized PnL attribution split by fill path:
1. `maker_merged`: Pairs merged where both legs were placed as resting maker orders.
2. `taker_completed`: Pairs merged where one leg was completed via a taker order (identified by >1 order under the same `(pair_id, token_id)`).
3. `single_buy_exit`: Positions exited via single-buy / rescue liquidation (`single_buy_exit`, `naked_exit`).

- **Size Tier**: Standard (~3 files: `core_brain/kpi.py`, `statistical_validation_run/artifacts.py`, `dashboard/static/app.js` + 1 new test file)
- **Task Type**: Code + Analytics

## Task 1: Core PnL attribution logic in `core_brain/kpi.py` [x]
- **Files:** `core_brain/kpi.py`
- **Change:**
  - Implement helper `find_taker_completed_pairs(orders: list[dict] | None, reg: OrderRegistry | None = None) -> set[str]` that finds pair IDs having multiple order rows for any `(pair_id, token_id)`.
  - Implement helper `pnl_by_fill_path(closes: list[dict], taker_pairs: set[str]) -> dict[str, Any]`.
    - Buckets: `maker_merged`, `taker_completed`, `single_buy_exit`.
    - `total`: sum of realized PnL.
    - `pct`: dictionary mapping each path to its share of total PnL. If `total == 0.0` or no closes, `pct` values are `None`.
  - Wire into `report()`:
    - Compute `pnl_by_fill_path` and attach to top-level KPI `pnl_by_fill_path`, `trade_analytics["pnl_by_fill_path"]`, and `run_profitability["pnl_by_fill_path"]`.
- **Acceptance:** Calling `kpi.report()` returns `pnl_by_fill_path` with correct totals and percentages across the 3 paths.
- **Check:** `python -m pytest tests/test_account_kpi.py -q`

## Task 2: Statistical Validation Report Artifacts in `statistical_validation_run/artifacts.py` [x]
- **Files:** `statistical_validation_run/artifacts.py`
- **Change:**
  - Update `write_artifacts()` to record `pnl_by_fill_path` in `stat_validation["pnl_by_fill_path"]` directly from `kpi["pnl_by_fill_path"]`.
  - Update `render_report_md()` in the `## Rescue & failure modes` section to output lines for Maker-merged, Taker-completed, and Single-buy exit PnL and percentage shares, noting it is a shadow rehearsal estimate.
- **Acceptance:** Generated `report.json` contains `pnl_by_fill_path` in `stat_validation`, and `report.md` formats the 3-way split clearly.
- **Check:** `python -m pytest tests/test_stat_validation_artifacts.py -q`

## Task 3: Dashboard UI display in `dashboard/static/app.js` [x]
- **Files:** `dashboard/static/app.js`
- **Change:**
  - In `renderRunProfitability(kpi)`: surface the 3-way PnL split in `detailsEl` or a subtitle (e.g. `maker $X (A%) · taker $Y (B%) · rescue $Z (C%)`).
- **Acceptance:** Dashboard renders the attribution without crashing or disturbing existing UI elements.
- **Check:** `python -m pytest tests/test_analytics_api.py -q`

## Task 4: Dedicated Unit Tests in `tests/test_pnl_by_fill_path.py` [x]
- **Files:** `tests/test_pnl_by_fill_path.py`
- **Change:**
  - Create test fixture with temporary SQLite DB and seeded data for maker-merged, taker-completed, and single-buy rescue.
  - Verify that realized PnL values matching 25% maker / 35% taker / 41% single exit yield exact expected totals and percentages via `pytest.approx`.
  - Test zero closes / empty store edge case returns None for percentages.
- **Acceptance:** All tests pass cleanly.
- **Check:** `python -m pytest tests/test_pnl_by_fill_path.py -q`

## Task 5: Full verification gate [x]
- **Files:** None
- **Change:** Run full test suite silently (`python -m pytest -q`) and verify 2,019+ tests pass.
- **Acceptance:** 100% green test suite.
- **Check:** `python -m pytest -q`

## How to verify by hand (Operator)
1. Run a shadow rehearsal or load an existing run database:
   `python -m core_brain.kpi`
2. Inspect the output payload or run `python -m statistical_validation_run.artifacts` to verify `pnl_by_fill_path` displays the 3-way attribution in the generated `report.md`.
3. Open the dashboard at `http://127.0.0.1:8799` and observe the Run Profitability card / Analytics displaying the Maker / Taker / Rescue PnL split.
