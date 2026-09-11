# Implementation Plan: Issue #204

## Context
In the Orders & Trades table, resting orders that lost their `pair_id` render as two disconnected `Unpaired` rows even when they constitute a valid complementary pair on the same market. This plan implements display-side inferred pairing for both open orders and held positions with an informational secondary `Inferred` tag (`ot-tag is-info`).

- **Size Tier**: Standard (touches ~3 files: `app.js`, `styles.css`, `test_orders_trades_table.py`)
- **Task Type**: Design + Code (UI rendering logic, CSS tag styling, DOM assertions)

## Task 1: Second-pass inferred grouping in `groupOrdersByPair` [x]

- **Files:** `dashboard/static/app.js`
- **Change:**
  - Preserve pass 1: group orders with non-empty `pair_id` into their pair group. Keep leftover orders as single-order groups keyed `order:<order_id>`.
  - Add pass 2: collect single-order leftovers and group them by `condition_id`.
  - For each `condition_id`, if there is exactly 1 leftover UP order and exactly 1 leftover DOWN order (via `legForOrder`), merge them into a single group with `key: "inferred:" + condition_id` and `inferred: true`.
  - Sort merged group orders: UP before DOWN (`legRank`), then `posted_ts`.
  - Ensure every group has `inferred: Boolean` (`false` for native `pair_id` groups and unmatched single-order groups).
- **Acceptance:** `groupOrdersByPair` returns a single group for two detached complementary legs on the same market with `inferred: true` and `key: "inferred:<cid>"`. Unmatched single legs remain isolated with `inferred: false`.
- **Check:** `python -m pytest tests/test_orders_trades_table.py -q`

## Task 2: Visual styling and inferred tag in Open Orders & Positions [x]

- **Files:** `dashboard/static/styles.css`, `dashboard/static/app.js`
- **Change:**
  - In `styles.css`: Add `.ot-tag.is-info` styling with `background: var(--blue-bg)`, `border-color: var(--blue-border)`, and `color: #38bdf8`.
  - In `app.js`:
    - Extend `pairSummary(status, pairCost, inferred)` to render `<div class="ot-tag is-info">Inferred</div>` alongside the status tag and cost when `inferred` is true.
    - In `openOrdersRows`, pass `group.inferred` to `pairSummary(...)`.
    - In `ordersTradesRows`, pass `state` into `positionsRows(kpi, state)`.
    - In `positionsRows`, inspect `state.fills` for held markets (`up_sh > 0 && dn_sh > 0`). If fills lack a common non-null `pair_id` covering both UP and DOWN, mark `inferred = true` and pass to `pairSummary`.
    - Export any new helpers needed for test verification in `module.exports`.
- **Acceptance:** Both Open Orders and Positions render `<div class="ot-tag is-info">Inferred</div>` when detached legs are paired on the fly, without altering the primary `Paired`/`Partial` status.
- **Check:** `python -m pytest tests/test_orders_trades_table.py -q`

## Task 3: Regression and unit tests in `test_orders_trades_table.py` [x]

- **Files:** `tests/test_orders_trades_table.py`
- **Change:**
  - Add test: two resting orders on the same market with missing/different `pair_id` render as 1 group (`rowspan="2"`, `data-pair="inferred:<cid>"`), correct status, computed pair cost, and `.ot-tag.is-info` with text `Inferred`.
  - Add test: single resting order renders as 1 row, `Unpaired`, with no `Inferred` tag.
  - Add test: held position with fills missing a common `pair_id` renders `Inferred` tag.
  - Add test: held position with fills sharing `pair_id` renders without `Inferred` tag.
  - Audit existing assertions for row counts / unpaired tags and adjust if detached fixtures intentionally change.
- **Acceptance:** All new tests pass, validating grouping, tags, and edge cases.
- **Check:** `python -m pytest tests/test_orders_trades_table.py -q`

## Task 4: Full verification gate [x]

- **Files:** None (verification gate)
- **Change:** Run full test suite silently and confirm clean diff.
- **Acceptance:** `python -m pytest -q` is 100% green.
- **Check:** `python -m pytest -q`

## How to verify by hand (Operator)
1. Launch dashboard on `http://127.0.0.1:8799` (`python -m dashboard.server`).
2. Navigate to the **Orders & Trades** tab.
3. Observe resting orders where a market has both UP and DOWN orders: they are merged into a single 2-row table entry sharing the market title cell with an `Inferred` blue badge beside `Paired` or `Partial`.
4. Genuinely single-side resting orders remain distinct rows labeled `Unpaired` with no `Inferred` tag.

