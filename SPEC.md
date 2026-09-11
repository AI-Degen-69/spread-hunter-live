# SPEC: Issue #195

## Goal

Make the shadow rehearsal's taker BUY completion path model ask-side market depth instead of crediting the full requested notional at the caller's touch price.

## In scope

- Add a bounded ask-ladder walk inside `ShadowExecutionClient`.
- Use a module-level `MAX_BUY_SLIPPAGE = 0.02` ceiling above the caller-supplied price.
- Treat BUY `amount` as USDC notional, matching `MarketOrderArgsV2` and `complete_pair`.
- Return a depth-weighted average fill price and the shares actually acquired.
- Allow a short fill when eligible ask depth is insufficient.
- Preserve the existing no-book fallback: requested notional divided by the caller price at that price.
- Record the walked size and price consistently in the completion order, fill, markout, and response.
- Add a focused regression test in `tests/test_exit_price_realism.py`.

## Out of scope

- Changes to the live completion path in `single_buy_saver.py`.
- Changes to `_sell_into_bids`.
- Changes to `pessimistic_completion_price` or reporting recosts.
- Changes to pair discovery, naked-pair selection, registry schema, or external dependencies.

## Contract

```python
MAX_BUY_SLIPPAGE: float = 0.02

ShadowExecutionClient._buy_from_asks(
    token_id: str,
    amount: float,
    *,
    price: float,
) -> tuple[float, float]
```

The helper fetches the adapted book through `get_order_book(token_id)`, walks positive-size asks from lowest price upward while `ask_price <= price + MAX_BUY_SLIPPAGE`, and spends no more than `amount`. It returns `(shares_taken, dollars_spent / shares_taken)` when any depth is available. If the book is empty or unavailable, it returns `(amount / price, price)`.

`create_and_post_market_order` keeps `_naked_pair_for_token` unchanged. For BUY orders it uses the helper result for `OrderRecord.price`, `OrderRecord.original_size`, `FillRecord.price`, `FillRecord.size`, `_log_shadow_markout`, and the response dictionary.

## Acceptance criteria

- A thin ask ladder below the ceiling produces a short BUY fill.
- The recorded and returned price is the depth-weighted average, not the touch price.
- Ask levels above the ceiling are ignored.
- Empty or unavailable book data preserves the existing fallback behavior.
- The new focused test fails against the current implementation and passes after the change.
- `python -m pytest -q` remains green.
