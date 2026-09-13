# Plan — Issue #194: Decide loop quotes 1 market while the filter graduates 3

## Scope: Small
Single-surface defect fix in the dashboard's START flow. No trader code changes.

## CONSTRAINTS.md (locked)
- **Behavior change:** the dashboard must stop passing `--max-markets 1` to
  `core_brain.trader_loop` so the Decide loop quotes every graduated market.
- **No trader edits:** `core_brain/trader_loop.py` already defaults to "all markets"
  and already rotates multiple markets safely. The cap lives only in the dashboard.
- **Single source of truth:** `dashboard/server.py::_start_stack_commands()` builds
  both the START button commands and the preflight preview. Remove the flag there.
- **Display parity:** the `decide` entry in `SERVICE_DEFS` (`dashboard/static/app.js`)
  is operator-facing display text; it must match the real command exactly.
- **No other flags change:** keep `--live --no-reconcile --no-sweep --interval 5`.
- **Docs:** `docs/agents/first-run.md` lines 105/149 reference `--max-markets N` as a
  trader flag with default "all" — already correct; no edit needed (verified).
- **Safety:** this widens live quoting to more markets. Dynamic Caps in
  `core_brain/config.py` scale with account value and bound exposure regardless of
  market count; opening the cap does not bypass any cap.

## Tasks (TDD order)
1. **Test first:** add `test_start_bot_decide_loop_has_no_market_cap` in
   `tests/test_dashboard_server.py` asserting the `core_brain.trader_loop` command
   from `start_bot` spawns contains no `--max-markets` flag. Confirm it fails on
   current code.
2. **Fix server:** remove the `"--max-markets", "1"` pair from the trader_loop entry
   in `_start_stack_commands()` in `dashboard/server.py`.
3. **Fix display:** remove `--max-markets 1` from the `decide` `cmd` string in
   `SERVICE_DEFS` in `dashboard/static/app.js`.
4. **Verify:** rerun the new test plus the existing dashboard/service-toggle suites,
   then the full `python -m pytest -q`.

## How to verify (operator, hands-on)
1. Start the dashboard: `python -m dashboard.server` → open `http://127.0.0.1:8799`.
2. Look at the Decide service card's command preview — it should now read
   `python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5`
   with no `--max-markets 1` at the end.
3. Hover START to see the preflight command preview — the trader line must match
   the card exactly.
