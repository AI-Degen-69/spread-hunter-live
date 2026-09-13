# Constraints: Issue #205 — Stray-Order Guard

## Quality & Tests
- Zero regressions on existing test suite: all 2,017 tests in `python -m pytest -q` must remain green.
- Every new behavior (adopt, cancel, dry-run, idempotent pass) must have automated tests.
- Anti-Cheat: Strictly forbid skipping tests (`@pytest.mark.skip`), deleting assertions, or bypassing dynamic caps.
- Tests must use isolated temporary SQLite DB fixtures (`tmp_path`) and mock venue clients; never touch `data/orders.db`.

## Venue Safety & Risk
- Live orders reach real financial venue:
  - `--no-live` / dry-run must NEVER place or cancel venue orders.
  - Cancellation must respect dynamic risk caps (`order_risk_pct`, `naked_risk_pct`, `bankroll_ceiling_pct`).
  - Reconcile lock discipline: stray guard actions must be synchronized and must never fight an active quoting cycle.
- Never cancel an order that belongs to a healthy `registry_paired` pair.

## Architectural Integrity
- Reusable primitives: reuse `OrderRegistry`, `single_buy_saver.load_pair`, `order_manager.cancel_single_order`.
- Separation of concerns: encapsulate stray detection and policy in `core_brain/stray_guard.py` rather than overloading `order_registry.py` with venue/pricing logic.
- Idempotence: Running multiple stray-guard passes sequentially must make zero redundant mutations or cancellations.
