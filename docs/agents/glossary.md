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

Old module paths (`scripts/rank_markets.py`, `scripts/rerank_loop.py`,
`scripts/live-spread-hunter-menu.ps1`) survive only as thin forwarders for anything still
calling them. Do not add to them.

Commit scopes follow the package name: `fix(core_brain): ...`, not `fix(engine): ...`.
