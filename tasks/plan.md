# Implementation Plan: Issue #195

## Context

The shadow client already walks bid depth for SELL exits in `_sell_into_bids`, but its BUY completion branch still does `shares = amount / price` and records the full request at the touch. The implementation should mirror the SELL-side realism pattern while respecting that BUY `amount` is notional dollars.

## Task 1: Add bounded ask-depth helper [x]

- **Files:** `core_brain/shadow_exec.py`
- **Change:** Add `MAX_BUY_SLIPPAGE = 0.02` near the module constants. Add `ShadowExecutionClient._buy_from_asks(token_id, amount, *, price)`.
- **Acceptance:** The helper reads `get_order_book`, walks positive ask levels from lowest price through `price + MAX_BUY_SLIPPAGE`, caps each fill by remaining notional, returns actual shares plus weighted average price, and falls back to `(amount / price, price)` when no shares are taken.
- **Check:** `python -m pytest -q tests/test_exit_price_realism.py -k 'buy or sell'` after the regression test exists.

## Task 2: Wire BUY persistence to walked values [x]

- **Files:** `core_brain/shadow_exec.py`
- **Change:** Replace the BUY branch's flat `amount / price` calculation with `_buy_from_asks`. Use returned `shares` and `avg` for the completion `OrderRecord`, `FillRecord`, markout, and response. Leave naked-pair lookup and `pessimistic_completion_price` unchanged.
- **Acceptance:** No requested notional or raw touch price is recorded when ask depth produces a different fill; the existing SELL branch and pair-selection behavior remain unchanged.
- **Check:** `python -m pytest -q tests/test_shadow_exec.py tests/test_exit_price_realism.py`

## Task 3: Add the BUY realism regression test [x]

- **Files:** `tests/test_exit_price_realism.py`
- **Change:** Reuse the existing registry and client helpers, seed an in-window naked pair using the established `record_submit` and `settle_market` pattern, and submit a BUY against a thin ask ladder. Assert a short fill and the hand-computed weighted average with `pytest.approx`.
- **Acceptance:** The test fails before the implementation because the old path fills the full amount at touch, and passes after the helper is wired.
- **Check:** `python -m pytest -q tests/test_exit_price_realism.py`

## Task 4: Full regression verification [x]

- **Files:** No source changes.
- **Change:** Run the complete hermetic suite and inspect failures without weakening the quality bar.
- **Acceptance:** `python -m pytest -q` passes with no skipped or removed assertions caused by this change.
- **Check:** `python -m pytest -q`

## How to verify by hand

1. Run a shadow rehearsal and open its generated report or shadow registry output.
2. Find a completion BUY against a thin ask ladder; it should show fewer shares than
	the requested notional divided by the touch price.
3. Compare the recorded completion price with the ask-level weighted average; it should
	be worse than the touch when the ladder walks and should never use asks above the
	two-cent ceiling.
4. A completion with no usable ask depth should retain the touch-price fallback.
