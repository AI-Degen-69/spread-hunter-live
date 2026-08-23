# Architecture

## Layout

```
spread-hunter-live/
  core_brain/             Core trading & execution engine
    quotes.py             THE decision layer: where to rest both legs, and why not to
    risk.py               Sizing ladder, inventory skew, dollar caps, hard blocks
    unhedged_stop_loss.py Per-market markout state machine + trader posture
    trader_loop.py        Multi-market rotation: decide -> plan -> submit/cancel
    order_manager.py      CLI: status, quote, poll, merge, redeem, exit, cancel
    single_buy_saver.py   Single-buy rescue (U35): complete the pair, or exit the buy
    merge_pairs.py        Gasless merge & redemption (ABI, alt-bn128, EIP-712)
    order_registry.py     SQLite order/fill tracking + reconcile (data/orders.db)
    registry_state.py     Read side of the registry; what the dashboard renders
    live_fill_engine.py   Turns venue fills into registry rows
    markout.py            Post-fill mark-to-market used by the stop-loss
    venue.py              Venue client wiring + the MAX_ORDER_USD / MAX_TOTAL_USD caps
    market_feed.py        Reads the market filter's graduated universe (runtime/markets.json)
    markets.py            Venue market lookup
    cycle_stream.py       Append-only telemetry ring + cycle_intent rows
    account.py            Wallet balance & float marks
    kpi.py                Live performance metrics & markouts
    audit.py              3-way reconciliation (Registry vs Venue vs Chain)
    config.py             Live tuning configuration
    runtime_paths.py      Where a runtime state file lives, across the run/ rename
  dashboard/
    server.py             Operations dashboard (:8799), FastAPI + uvicorn
    static/               Dashboard SPA (index.html, app.js, styles.css)
  scripts/
    filter_markets.py     Fetch, filter and score PolyMarket candidate pairs
    filter_loop.py        Continuous filter loop (every 10 minutes)
    global_stop_loss.py   Watchdog: over-cap pairs & repeat single-buy exits
    audit_settlement.py   Settlement & balance verification
    spread-hunter-menu.ps1 Interactive operations menu
  scoring/                Scoring, allocation and selection rules the Market Filter uses
  strategy/               Signal and sizing logic
  data/
    orders.db             THE primary order and fill registry
  runtime/
    markets.json          The filtered universe the Trader quotes
    processes.json        PIDs of the running stack (filter / query / decide)
    cycle_events.jsonl    Ring buffer of operational events
  tests/                  Full hermetic unit & integration test suite
```

## Runtime state across the rename

`runtime/` holds state that is **not** in git: it is on the operator's disk, written by
processes that may still be running when new code starts. So readers resolve state files
through `core_brain/runtime_paths.py`, which prefers the `runtime/` path and falls back to
the pre-rename `run/` path while only that one exists. Writers always write `runtime/`,
which disarms the fallback as soon as the new file appears.

Two of those files are money, not cosmetics:

- **`processes.json`.** `start_bot()` refuses a second stack only when the status reads
  RUNNING, and that status comes from this file. A registry the code cannot find reads as
  STOPPED, and START then launches a second live Trader beside the running one.
- **`markets.json`.** The Trader quotes only what this file lists. A feed the code cannot
  find is an empty universe until the Market Filter regenerates it.

Add a state file: write it through `runtime_file(...)`, read it through
`resolve_runtime_file(...)`, and if you ever rename one, add the old name to
`LEGACY_FILE_NAMES` in the same commit.
