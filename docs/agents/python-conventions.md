# Python conventions

Python 3.12. FastAPI + uvicorn for `dashboard/server.py`. Venue client:
`py-clob-client-v2`, `eth-account` (Polymarket CLOB). Tests: pytest
(`pytest.ini`: `testpaths = tests`, `pythonpath = .`).

These rules override `.claude/rules/ecc/common/coding-style.md`, which is written for
TypeScript.

## Naming

| Element | Convention | Evidence |
| --- | --- | --- |
| Files | `snake_case` | all modules |
| Functions | `snake_case` | 249 defs, 0 camelCase |
| Classes | `PascalCase` | `OrderRegistry`, `LiveFillEngine` |
| Constants | `SCREAMING_SNAKE_CASE` | `MIN_ORDER_SHARES`, `DEFAULT_TICK_SIZE` |

## Imports

Absolute, rooted at the repo. `pytest.ini` sets `pythonpath = .`.

```python
from core_brain.order_registry import OrderRegistry, DEFAULT_DB_PATH
from core_brain.quotes import Inventory, QuoteIntent
```

No relative imports (0 in the codebase). No wildcard imports in new code — the only two
are `scripts/rank_markets.py` and `scripts/rerank_loop.py`, legacy forwarders that re-export
their replacements. Modules open with `from __future__ import annotations` (20 of 21 in
`core_brain/`; only `__init__.py` omits it).

## Error handling

Raise domain-specific exceptions that name the refusal; do not swallow errors.

```python
class PairCompletionRefused(RuntimeError):
    ...

if size < MIN_ORDER_SHARES:
    raise PairCompletionRefused(
        f"Completable size {size:.4f} is below the venue minimum of "
        f"{MIN_ORDER_SHARES}."
    )
```

Narrow `except` clauses only (`except sqlite3.Error`, `except OSError`). A bare
`except Exception: pass` in an execution path hides money-losing state.

## Tests

- pytest, file pattern `test_*.py` in `tests/`.
- `tests/conftest.py` is hermetic: it scrubs credentials from `os.environ` and blocks
  every non-loopback socket. Opt out per test with `@pytest.mark.allow_network`.
- Platform-specific tests carry `@pytest.mark.skipif(sys.platform ...)`.
- CI runs the suite on ubuntu and windows via `.github/workflows/tests.yml`.

## Engine-specific traps

- **Never close a connection inside `with get_connection(...)`.** The context manager
  commits on exit; use `contextlib.closing`.
- `complete_pair` sizes against `ask_depth` (the asks ladder). `depth_at_or_above` reads
  **bids** and will oversize a buy.
- Fill attribution must not silently drop fills. Under-counting a position invites fresh
  exposure on top of it.
