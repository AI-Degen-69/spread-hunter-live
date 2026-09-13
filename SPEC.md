# SPEC: Issue #205 — Stray-Order Guard

## Goal
Give the bot an ongoing stray-order guard: detect single-side (unhedged) orders and positions, adopt complementary detached legs into a proper pair when the combined cost is under the cap, and cancel strays that have no hope of becoming a profitable pair — so the account always trends toward merged pairs costing less than $1.00.

## Background & Problem
Legs can lose their `pair_id` link (e.g. after restarts, venue desync, re-posts, or network glitches). When this happens:
1. Complementary legs (UP at $0.73 and DOWN at $0.23 on the same market = $0.96 pair) sit in the registry with different or missing `pair_id`s. The bot fails to acknowledge them as a pair, refusing to merge them.
2. Lone resting orders (single legs with no counter-leg) sit on the book indefinitely even when the market has drifted so far that completing a sub-$1.00 pair is impossible. They tie up bankroll and risk adverse fills.
3. Unhedged stray positions (filled legs with no pair link) remain exposed rather than being exited or paired.

## Scope

### In Scope
1. **Detection & Classification (`core_brain/stray_guard.py`)**:
   - Classify all active orders and open positions into:
     - `registry_paired`: Healthy pair with both UP and DOWN legs under the same `pair_id`.
     - `complementary_detached`: Two legs on the same `condition_id` (one UP, one DOWN) with mismatched or missing `pair_id`s whose combined cost <= `max_pair_cost`.
     - `hopeless_stray`: A resting order with no complementary leg, or whose price + opposite best ask >= `max_pair_cost` (or `max_completable_pair_cost`).
     - `unhedged_stray_position`: Held position without a paired leg.
2. **Adoption Mechanism**:
   - Link detached complementary legs under a unified `pair_id` in `orders` registry.
   - Idempotent: Subsequent passes see them as `registry_paired` and take no action.
   - Seamlessly managed by existing `single_buy_saver.auto_manage_pairs()` and `merge`.
3. **Cancellation Mechanism**:
   - Cancel hopeless resting strays on the venue.
   - Respect dynamic risk caps (`order_risk_pct`, `naked_risk_pct`, `bankroll_ceiling_pct`).
   - Dry-run (`--no-live` / shadow) logs `would_cancel` without touching the venue.
4. **Position Remediation**:
   - Exit or hedge unhedged stray positions via `single_buy_saver` / `exit_pair` primitives.
5. **Operator Visibility & CLI**:
   - Clear structured logging per action: `ADOPT_STRAY`, `CANCEL_STRAY`, `EXIT_STRAY`.
   - CLI entrypoint: `python -m core_brain.order_manager stray-guard [--no-live]`.
   - Integration into `reconcile_orders()` pass.

### Out of Scope
- Dashboard UI changes (already implemented in #204 / commit `b028f68`).
- Modifying market selection or quoting pricing formulas (`quotes.py`).
- Adding new venue API endpoints.

## Acceptance Criteria
- [ ] Detached complementary legs on same condition with price <= `max_pair_cost` adopted into single `pair_id`.
- [ ] Hopeless resting orders cancelled safely with logged rationale.
- [ ] Stray positions exited via market bid to prevent unhedged settlement exposure.
- [ ] Fully non-destructive in `--no-live` / dry-run mode.
- [ ] Idempotent across repeated executions.
- [ ] 100% green test suite on `python -m pytest -q`.
