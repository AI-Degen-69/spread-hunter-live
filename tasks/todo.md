# Issue #204 Tasks

- [x] T1: Implement detached order second-pass consolidation (`inferred:<condition_id>`) in `dashboard/static/app.js` (`groupOrdersByPair`).
- [x] T2: Add `.ot-tag.is-info` in `dashboard/static/styles.css` and update `pairSummary`, `openOrdersRows`, and `positionsRows` to support and render the `Inferred` tag.
- [x] T3: Add comprehensive regression tests in `tests/test_orders_trades_table.py` for open-orders and positions inferred pairing.
- [x] T4: Run targeted table tests and full test suite (`python -m pytest -q`) to verify 100% green without regressions.
