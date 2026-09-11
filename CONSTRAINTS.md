# Constraints: Issue #195

## Regression and tests

- Existing behavior outside shadow completion BUYs must remain unchanged.
- Add at least one regression test that fails against the current touch-price implementation.
- Run `python -m pytest -q` before implementation is considered complete.
- Do not skip, weaken, delete, or rewrite assertions merely to obtain a passing suite.
- Do not add tests that depend on live network access or real funds.

## Fill-model correctness

- BUY `amount` is USDC notional, never a share count.
- Never spend more than the requested notional.
- Never consume asks priced above `price + 0.02`.
- Ignore missing or non-positive ask sizes.
- Report the actual filled share count and depth-weighted average price consistently across the order row, fill row, markout, and response.
- A thin eligible ladder must produce a short fill rather than inventing depth.
- If no eligible depth is available, preserve the existing fallback `(amount / price, price)`.

## Performance and dependencies

- The ask walk must be a single linear pass over the returned ask levels: O(n) time and O(1) additional working memory.
- Do not add a runtime dependency or change the database schema.
- Keep the change within `core_brain/shadow_exec.py` and its focused test unless validation exposes a concrete contract issue.

## Safety

- Do not modify live venue execution or order sizing caps.
- Do not run commands that place real orders.
- Do not hardcode credentials, tokens, or private keys.
