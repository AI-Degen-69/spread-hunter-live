# Glossary — current names

The stack was renamed for clarity. Use the right-hand column everywhere: code, commits,
issues, dashboard copy and operator instructions.

| Was | Now | Where it lives |
| --- | --- | --- |
| Screener / ranker | **Market Filter** | `scripts/filter_markets.py`, loop in `scripts/filter_loop.py` |
| Engine Poll Loop (5s) | **Order Manager** (0.5s) | `core_brain/order_manager.py`, `poll --interval 0.5` |
| Fleet / Quoting Fleet | **Trader** | `core_brain/trader_loop.py` |
| Naked leg / one-sided fill | **Single buy** | `core_brain/single_buy_saver.py` |
| `dash/live_dash.py` | **Dashboard server** | `dashboard/server.py` |
| `run/live.db` | **Orders DB** | `data/orders.db` |
| `scripts/live-spread-hunter-menu.ps1` | **Operations menu** | `scripts/spread-hunter-menu.ps1` |
| `engine/` | **`core_brain/`** | the execution package; import as `from core_brain.x import y` |
| `run/` | **`runtime/`** | on-disk state: `markets.json`, `processes.json`, the cycle ring |
| `scripts/guardrail_watch.py` | **Global Stop Loss** | `scripts/global_stop_loss.py` |
| `run/live_procs.json` | **Process file** | `runtime/processes.json` |
| process keys `screener` / `engine` / `fleet` | **`filter` / `query` / `decide`** | `runtime/processes.json`, `/api/system/status` |

Also fixed vocabulary: **single buy** (not naked leg, one-sided, or unhedged),
**pair cost**, **merge** as the exit, **graduated** for the markets listed in
`runtime/markets.json`.

## Order lifecycle

Three stages, named the same way in code, tests and dashboard copy. Each name means
one stage and no other:

| Term | Means | Does not mean |
| --- | --- | --- |
| **Active Market** | A market that graduated the Market Filter and is being quoted. Nothing is owned. | A market with a position in it |
| **Open Order** | An order resting on the book, unfilled or partly filled. No exposure, so no PnL. | A filled leg |
| **Position** | Shares the account holds after a leg filled. The only stage with PnL. | An order waiting to fill |
| **Unpaired** | An open order whose partner leg is not on the book. If it fills alone it becomes a **single buy**. | A filled single buy — that is already booked |
| **Pair Cost** | UP price + DOWN price. Under $1.00 the merge books a profit, exactly $1.00 books nothing, over $1.00 books a loss. | The cost of one leg |

`Unpaired` is the state of the *order*; `single buy` is the state of the *position* it
turns into. Do not use one for the other, and never write "one leg resting", "half a
pair", or "unhedged" for either of them.

## Dashboard copy

Operator-facing strings are sentence case with a full stop; column headers and tags are
Title Case. Name the component, never the old name: **Market Filter** (not screener or
ranker), **Trader**, **Order Manager**, **Global Stop Loss**. Status tags carry one of
three tones and each tone means one thing — good (the merge books a profit), warning
(a single buy is waiting to happen), alert (the merge books a loss).

Old module paths (`scripts/rank_markets.py`, `scripts/rerank_loop.py`,
`scripts/live-spread-hunter-menu.ps1`) survive only as thin forwarders for anything still
calling them. Do not add to them.

Commit scopes follow the package name: `fix(core_brain): ...`, not `fix(engine): ...`.
