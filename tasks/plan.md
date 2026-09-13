# Implementation Plan: Issue #205 — Stray-Order Guard

## Context
Give the bot an ongoing stray-order guard: detect single-side (unhedged) orders and positions, adopt complementary detached legs into a proper pair when the combined cost is under the cap, and cancel strays that have no hope of becoming a profitable pair — so the account always trends toward merged pairs costing less than $1.00.

- **Size Tier**: Standard (3 core files: `core_brain/stray_guard.py`, `core_brain/order_registry.py`, `core_brain/order_manager.py` + `tests/test_stray_guard.py`)
- **Task Type**: Code (Safety, Risk & Order Lifecycle)

## Task 1: Core Stray Classifier & Data Structures (`core_brain/stray_guard.py`) [x]
- **Domain:** `[Backend/Logic]`
- **Files:** `core_brain/stray_guard.py`, `tests/test_stray_guard.py`
- **Helper Skill:** `test-driven-development`, `api-and-interface-design`
- **Details:**
  - Define `StrayClassification` enum / dataclass: `REGISTRY_PAIRED`, `COMPLEMENTARY_DETACHED`, `HOPELESS_STRAY`, `UNHEDGED_POSITION`.
  - Implement `classify_market_orders(orders: list[OrderRecord], positions: dict[str, float], books: dict[str, dict], max_pair_cost: float) -> dict`.
- **Verification:** Unit test `tests/test_stray_guard.py::test_classify_market_orders`

## Task 2: Detached Leg Adoption Mechanism (`core_brain/stray_guard.py`, `core_brain/order_registry.py`) [x]
- **Domain:** `[Backend/Logic]`
- **Files:** `core_brain/stray_guard.py`, `core_brain/order_registry.py`, `tests/test_stray_guard.py`
- **Helper Skill:** `test-driven-development`, `incremental-implementation`
- **Details:**
  - Add `adopt_orders_into_pair(order_ids: list[str], pair_id: str)` to `OrderRegistry` (updates `pair_id` atomically in SQLite).
  - Implement `adopt_detached_legs(registry, detached_pairs, live=True)`.
  - Ensure subsequent calls to `single_buy_saver.load_pair(pair_id)` return a valid 2-leg pair.
- **Verification:** Unit test `tests/test_stray_guard.py::test_adopt_detached_legs_idempotent`

## Task 3: Cancellation of Hopeless Resting Orders (`core_brain/stray_guard.py`) [x]
- **Domain:** `[Backend/Logic]`
- **Files:** `core_brain/stray_guard.py`, `tests/test_stray_guard.py`
- **Helper Skill:** `test-driven-development`
- **Details:**
  - For orders classified as `HOPELESS_STRAY` (no partner leg, or opposite best ask + resting price >= `max_pair_cost`):
    - When `live=True`: issue cancel via `client.cancel_order()`.
    - When `live=False` (dry run / shadow): log `would_cancel`.
  - Respect dynamic caps (`order_risk_pct`, `bankroll_ceiling_pct`).
- **Verification:** Unit test `tests/test_stray_guard.py::test_cancel_hopeless_orders`

## Task 4: Unhedged Position Remediation (`core_brain/stray_guard.py`) [x]
- **Domain:** `[Backend/Logic]`
- **Files:** `core_brain/stray_guard.py`, `tests/test_stray_guard.py`
- **Helper Skill:** `test-driven-development`
- **Details:**
  - Evaluate held positions with no paired counter-order or partner inventory.
  - Route through `exit_pair` / best bid taker exit when holding is unprofitable or unhedged.
- **Verification:** Unit test `tests/test_stray_guard.py::test_remediate_stray_position`

## Task 5: Integration & Order Manager CLI (`core_brain/order_manager.py`, `core_brain/order_registry.py`) [x]
- **Domain:** `[Backend/CLI]`
- **Files:** `core_brain/order_manager.py`, `core_brain/order_registry.py`, `tests/test_stray_guard.py`
- **Helper Skill:** `incremental-implementation`
- **Details:**
  - Integrate `run_stray_guard()` into `reconcile_orders()` as an automated step.
  - Expose CLI command `python -m core_brain.order_manager stray-guard [--no-live]`.
  - Support structured logging for each action taken.
- **Verification:** CLI test `tests/test_stray_guard.py::test_cli_stray_guard`

## Task 6: Full Verification Gate [x]
- **Domain:** `[Gate]`
- **Helper Skill:** `test-driven-development`
- **Details:** Run complete test suite silently (`python -m pytest -q`).
- **Verification:** 2,017+ tests passed, 0 failures.

## How to verify by hand (Operator)
1. Run stray guard preview in dry-run mode:
   `python -m core_brain.order_manager stray-guard --no-live`
2. Run against a shadow or live database and observe the logged actions:
   - Any detached complementary legs are adopted into a unified pair.
   - Any hopeless lone orders are reported or cancelled.
   - Idempotent: running it a second time reports 0 new actions needed.
