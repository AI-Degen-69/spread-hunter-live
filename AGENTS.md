# AGENTS.md — Spread Hunter Live

## What this repo is

Dedicated real-money execution engine and operations dashboard for the Polymarket
**spread hunter** strategy.

## The objective

**Buy a complete outcome set for less than it pays, then merge it back to collateral.**

One UP share plus one DOWN share of the same binary market always redeems for exactly
$1.00. The bot screens the venue for markets whose book allows it, rests two-sided bids
*under mid* on both outcomes, waits for both legs to fill, and **merges** the assembled
pair back into $1.00 of USDC. The income is the difference:

```
profit per pair = 1.00 - (avg UP price + avg DOWN price)
```

Measured on `run/fleet.db`: 476 merge closes returned **+$1,172.35** at an average pair
cost of **$0.96006** — roughly 4c per pair. That is the strategy, and it is what the
name "spread hunter" refers to.

`max_pair_cost = 0.995` is therefore the **profit condition**, not a side constraint.
The merge is the exit and the P&L event, not housekeeping.

### Maker rebates are extra, not the income

Polymarket's liquidity-reward program pays for resting size, and the bot qualifies for
it. Over the same measured run it accrued about **$0.22/day against $566 committed** —
four hundredths of a percent. Rebates are a side accrual, estimated and reported
separately, never blended into trading PnL (`config.maker_fee`, `config.rebate_rate`).

Do not describe this bot as a rebate farm or a liquidity-mining bot. It is a
pair-assembly arbitrage that happens to earn rebates while it waits.

### The universe is NOT BTC 5-minute binaries

The ranker (`scripts/rank_markets.py`, in the simulation repo) runs a funnel over the
venue — 24h volume, top-3 bid depth on both sides, book spread, horizon — and writes the
survivors to `run/markets.json`. The live fleet quotes **only** that list, via
`engine/market_feed.py`.

- Horizon cap is **30 days** (`config.select_max_days_to_resolve`), which admits liquid
  sports, esports, macro and political markets.
- The measured traded universe is **tennis, baseball and esports** — not crypto.
- Graduated markets carry `source: "spread"` with `daily: 0.00`. They pay no rewards at
  all, and they are the markets that actually trade.
- `config.series_slug = "btc-up-or-down-5m"` is a **legacy field**, read only by
  `engine/markets.py` and the `probe` latency harness. The 5-minute BTC series was
  measured dead: *"Adverse selection, not fee level, is what killed 5-min BTC."*

### Pricing mode: spread_capture (formerly "rewards")

`config.objective` defaults to the string `"spread_capture"` (formerly `"rewards"`) and
`main_spread_hunter_loop._market_cfg` sets it on every market. It selects
`quotes._decide_quotes_from_mid`, which rests both legs at `mid - offset`. Because
`mid_UP + mid_DOWN ≈ 1.00`, that construction assembles the pair at `≈ 1.00 - 2*offset` —
precisely how a sub-$1.00 pair gets built. The mechanism is correct; "rewards" was a historical label.

The alternative branch (`objective="pair"`) prices off the **ask** and was measured dead:
it puts every quote half a spread *above* mid, so the pair costs `1.00 + spread` by
construction. It is unreachable in production and must stay that way.

No reward-economics knob reaches live code. `reward_min_payout_usd`,
`reward_floor_multiple`, `est_reward_pool_usd`, `rebate_rate`, `marginal_return_floor`,
`allocation_budget` and `max_market_frac` are inherited from the simulation's allocator
and are read by nothing in `engine/` or `dash/`. `quotes.reward_score()` formats a log
string and feeds no sizing decision.

### The two failure modes

A pair assembled **over $1.00** is a booked loss on an instrument that pays exactly
$1.00. A **one-sided fill** is a directional bet nobody decided to take. Everything in
`risk.py`, `unhedged_stop_loss.py` and `single_side_buy_saver.py` exists to prevent those two states.

## Layout

```
spread-hunter-live/
  engine/                 Core trading & execution engine
    quotes.py             THE decision layer: where to rest both legs, and why not to
    risk.py               Sizing ladder, inventory skew, dollar caps, hard blocks
    unhedged_stop_loss.py Per-market markout state machine + fleet posture
    main_spread_hunter_loop.py Multi-market rotation: decide -> plan -> submit/cancel
    live_exec.py          CLI: status, quote, poll, merge, redeem, exit, cancel
    single_side_buy_saver.py Naked-leg rescue (U35): complete the pair, or exit the leg
    merge_pairs.py        Gasless merge & redemption (ABI, alt-bn128, EIP-712)
    order_registry.py     SQLite order/fill tracking + reconcile (run/live.db)
    registry_state.py     Read side of the registry; what the dashboard renders
    market_feed.py        Reads the ranker's graduated universe (run/markets.json)
    markets.py            Venue market lookup (also the legacy BTC 5m discovery)
    cycle_stream.py       Append-only telemetry ring + cycle_intent rows
    account.py            Wallet balance & float marks
    kpi.py                Live performance metrics & markouts
    audit.py              3-way reconciliation (Registry vs Venue vs Chain)
    config.py             Live tuning configuration
  dash/
    live_dash.py          Operations dashboard (:8799)
    static/               Dashboard SPA (index.html, app.js, styles.css)
  scripts/
    guardrail_watch.py    Watchdog: over-cap pairs & repeat naked-leg exits
    audit_settlement.py   Settlement & balance verification
  run/
    live.db               THE primary order and fill registry
    markets.json          The graduated universe the fleet quotes (from the ranker)
    cycle_events.jsonl    Ring buffer of operational events
  tests/                  Full hermetic unit & integration test suite
```

## Commands

```bash
# Check status and balance
python -m engine.live_exec status

# Launch single-cycle operations dashboard
python -m dash.live_dash

# Run full test suite
pytest -q
```

## Safety Rails

1. **LIVE is the default:** This is the real-money execution repo. Every subcommand reaches the venue by default. Use `--no-live` for dry-run preview.
2. **Closing commands are pre-approved:** `exit`, `complete`, `merge`, `redeem`, `cancel`, `cancel-all` reduce exposure.
3. **Opening commands require explicit supervision:** `quote` and live fleet quoting rest real funds.
4. **Limits:** `MAX_ORDER_USD = 25.0`, `MAX_TOTAL_USD = 100.0`.

## Agent skills

### Issue tracker

Issues live as GitHub issues in `AI-Degen-69/spread-hunter-live`, driven via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root, created lazily. See `docs/agents/domain.md`.
