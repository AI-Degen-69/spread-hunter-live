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

Measured over the paper run: 476 merge closes returned **+$1,172.35** at an average pair
cost of **$0.96006** — roughly 4c per pair. That is the strategy, and it is what the
name "spread hunter" refers to.

`max_pair_cost = 0.995` is therefore the **profit condition**, not a side constraint.
The merge is the exit and the P&L event, not housekeeping.

### Maker rebates are extra, not the income

Polymarket's liquidity-reward program pays for resting size, and the bot qualifies for
it. Over the same measured run it accrued about **$0.22/day against $566 committed** —
four hundredths of a percent. Rebates are a side accrual, estimated and reported
separately, never blended into trading PnL (`config.maker_fee`, `config.rebate_rate`).

Describe this bot as a **pair-assembly arbitrage** that earns rebates while it waits.

### The universe: liquid markets inside a 30-day horizon

The **Market Filter** (`scripts/filter_markets.py`, run continuously by
`scripts/filter_loop.py`) funnels the venue — 24h volume, top-3 bid depth on both sides,
book spread, horizon — and writes the survivors to `run/markets.json`. The **Trader**
(`engine/trader_loop.py`) quotes **only** that list, via `engine/market_feed.py`.

`scripts/rank_markets.py` and `scripts/rerank_loop.py` are backward-compatible aliases
that import from the filter modules; new work targets the filter names.

- Horizon cap is **30 days** (`config.select_max_days_to_resolve`), which admits liquid
  sports, esports, macro and political markets.
- The measured traded universe is **tennis, baseball and esports** — not crypto.
- Graduated markets carry `source: "spread"` with `daily: 0.00`. They pay no rewards at
  all, and they are the markets that actually trade.
- BTC 5-minute binaries are out of scope: `config.series_slug = "btc-up-or-down-5m"`
  is a **legacy field**, read only by
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
`allocation_budget` and `max_market_frac` belong to the allocator in `scoring/allocate.py`
and are read by nothing in `engine/` or `dashboard/`. `quotes.reward_score()` formats a log
string and feeds no sizing decision.

### The two failure modes

A pair assembled **over $1.00** is a booked loss on an instrument that pays exactly
$1.00. A **one-sided fill** is a directional bet nobody decided to take. Everything in
`risk.py`, `unhedged_stop_loss.py` and `single_buy_saver.py` exists to prevent those two states.

## Layout

```
spread-hunter-live/
  engine/                 Core trading & execution engine
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
    market_feed.py        Reads the market filter's graduated universe (run/markets.json)
    markets.py            Venue market lookup
    cycle_stream.py       Append-only telemetry ring + cycle_intent rows
    account.py            Wallet balance & float marks
    kpi.py                Live performance metrics & markouts
    audit.py              3-way reconciliation (Registry vs Venue vs Chain)
    config.py             Live tuning configuration
  dashboard/
    server.py             Operations dashboard (:8799)
    static/               Dashboard SPA (index.html, app.js, styles.css)
  scripts/
    filter_markets.py     Fetch, filter and score PolyMarket candidate pairs
    filter_loop.py        Continuous filter loop (every 10 minutes)
    guardrail_watch.py    Watchdog: over-cap pairs & repeat single-buy exits
    audit_settlement.py   Settlement & balance verification
    spread-hunter-menu.ps1 Interactive operations menu
  data/
    orders.db             THE primary order and fill registry
  run/
    markets.json          The filtered universe the Trader quotes
    cycle_events.jsonl    Ring buffer of operational events
  scoring/                Scoring, allocation and selection rules the Market Filter uses
  tests/                  Full hermetic unit & integration test suite
```

## Glossary — current names

The stack was renamed for clarity. Use the right-hand column everywhere: code, commits,
issues, dashboard copy and operator instructions.

| Was | Now | Where it lives |
| --- | --- | --- |
| Screener / ranker | **Market Filter** | `scripts/filter_markets.py`, loop in `scripts/filter_loop.py` |
| Engine Poll Loop (5s) | **Order Manager** (0.5s) | `engine/order_manager.py`, `poll --interval 0.5` |
| Fleet / Quoting Fleet | **Trader** | `engine/trader_loop.py` |
| Naked leg / one-sided fill | **Single buy** | `engine/single_buy_saver.py` |
| `dash/live_dash.py` | **Dashboard server** | `dashboard/server.py` |
| `run/live.db` | **Orders DB** | `data/orders.db` |
| `scripts/live-spread-hunter-menu.ps1` | **Operations menu** | `scripts/spread-hunter-menu.ps1` |

Old module paths (`scripts/rank_markets.py`, `scripts/rerank_loop.py`,
`scripts/live-spread-hunter-menu.ps1`) survive only as thin forwarders for anything still
calling them. Do not add to them.

## Commands

```bash
# Check status and balance
python -m engine.order_manager status

# Launch the operations dashboard on http://127.0.0.1:8799
python -m dashboard.server

# Start or stop the whole stack (Market Filter, Order Manager, Trader, Guardrail)
.\scripts\spread-hunter-menu.ps1 start
.\scripts\spread-hunter-menu.ps1 status

# Run full test suite
python -m pytest -q
```

## Safety Rails

1. **LIVE is the default:** This is the real-money execution repo. Every subcommand reaches the venue by default. Use `--no-live` for dry-run preview.
2. **Closing commands are pre-approved:** `exit`, `complete`, `merge`, `redeem`, `cancel`, `cancel-all` reduce exposure.
3. **Opening commands require explicit supervision:** `quote` and the Trader loop rest real funds. Propose the command; the operator runs it.
4. **Limits:** `MAX_ORDER_USD = 25.0`, `MAX_TOTAL_USD = 100.0` (`engine/config.py`).
5. **`data/orders.db` is production state.** Read it; never rewrite or delete it.

## Verifying a change

Every change ships with two proofs. Both are required before reporting the work done.

**1. Automated — run it yourself.** `python -m pytest -q` green, and every changed
behaviour covered by a test that fails without the change. Paste the real output.
Sizing, fill attribution, and merge paths always land with a test.

**2. Manual — write it out for the operator.** End the work with a short
**"How to verify"** block the operator can follow without reading the code. Pick the
cheapest route that actually proves the change:

- **Terminal, read-only:** the exact command plus the line to look for.
  Example: `python -m engine.order_manager status` → the `open_notional` row reads `$0.00`.
- **Dashboard:** the click path and the value that should differ.
  Example: start `python -m dashboard.server`, open `http://127.0.0.1:8799`, the
  **Trader** card → poll cadence reads `0.5s`.
- **Live, with real money:** this repo trades for real, and a change to quoting,
  filling or merging is only proven when a real order behaves. Say so, and give the
  smallest test that settles it: one couple at the venue minimum, inside
  `MAX_ORDER_USD` / `MAX_TOTAL_USD`, on a graduated market from `run/markets.json`.

Rules for the block:

1. Name the file or screen, one expected value per step, five steps or fewer.
2. Say what a **failed** check looks like, not only a passing one.
3. Every live step carries its undo on the next line, and the undo has to match what
   actually happened:
   - **Nothing filled:** `cancel`, `cancel-market` or `cancel-all` pulls the resting
     orders. This does *not* close a leg that already filled.
   - **One leg filled:** `complete <pair_id> --live` buys the missing side; if it
     refuses, `exit <pair_id> --live` sells the leg you are holding.
   - **Both legs filled:** `merge <condition_id>` turns the pair back into USDC.
   - Then confirm: the market is no longer `NAKED` on the dashboard and holds no
     single-buy shares.
4. State the money at risk in dollars before the first live step.
5. The operator runs the order-placing commands. Agents run read-only and closing
   commands, and may run an opening command only when the operator says so in that
   session.

## Git and GitHub

Treat this repository as your own: keep the history clean, the branch current, and every
change reviewed. Repo: `AI-Degen-69/spread-hunter-live` (`origin`), default branch `main`.
CodeRabbit reviews every pull request; the conventions below match its configuration, so
its title check passes on the first try.

**Tags.** One vocabulary, used for the PR title, the branch name and the commit type:

| Tag | Use it for | Branch prefix | Commit type |
| --- | --- | --- | --- |
| `[ADD]` | new capability on top of what exists | `add/` | `feat` |
| `[CREATE]` | a new file, module or service | `create/` | `feat` |
| `[FIX]` | wrong behaviour corrected | `fix/` | `fix` |
| `[IMPROVE]` | same behaviour, better | `improve/` | `refactor` |
| `[REFACTOR]` | moved or renamed, behaviour unchanged | `refactor/` | `refactor` |
| `[OPTIMIZE]` | faster or cheaper | `optimize/` | `perf` |
| `[TEST]` | tests only | `test/` | `test` |
| `[DOCUMENT]` | docs only | `document/` | `docs` |
| `[FORMAT]` | whitespace, layout, lint | `format/` | `style` |
| `[UPDATE]` | dependency or data refresh | `update/` | `chore` |
| `[CONFIGURE]` | settings, workflows, tooling | `configure/` | `chore` |
| `[REVERT]` | undo a previous change | `revert/` | `revert` |

**Commits.** Conventional commits, imperative, one logical change per commit
(`fix(engine): size pair completion against the asks ladder`). Never commit `.env`,
keys, `data/*.db`, or logs. Commit as work completes rather than batching a day's edits.

**Branches.** Never commit straight to `main`. One branch per change, named
`<prefix><short-slug>` from the table — `fix/pair-completion-sizing`.

**Pull requests.** Push the branch and open the PR with `gh pr create`. The title is
`[TAG] short plain-English title a high-schooler understands` — no jargon, no module
paths, under about 50 characters after the tag. The body states what changed, why, the
test output, and the same **How to verify** block given to the operator.

**Review by CodeRabbit.** After opening the PR, request the review:

```bash
gh pr comment <number> --body "@coderabbitai review"
```

Then wait for it and work the feedback:

1. Poll for the review: `gh pr view <number> --comments` (CodeRabbit usually replies
   within a few minutes).
2. Read every comment and judge it. Implement the ones that are correct and worth it.
3. Push the fixes to the same branch, then reply on each thread — what you changed, or
   why you declined. Declining is fine when the suggestion is wrong or out of scope;
   say so plainly.
4. Re-request review after pushing fixes: `@coderabbitai full review`, which covers
   the fix commit rather than only the newest changes.
5. Repeat until CodeRabbit raises nothing new and CI (`.github/workflows/tests.yml`) is
   green on both ubuntu and windows.

Write PR bodies and review replies the way CodeRabbit is configured to write: plain
English, no abbreviations, key point first, technical terms explained in one sentence.
Call out anything that risks a pair over $1.00 or a single unmatched buy.

**Merging.** Routine changes — docs, tests, tooling, dashboard cosmetics — may be merged
once CI is green and CodeRabbit is clear. Anything touching order sizing, fill
attribution, risk limits, the merge path, or live execution waits for operator sign-off,
even when every check passes.

## Agent skills

### Issue tracker

Issues live as GitHub issues in `AI-Degen-69/spread-hunter-live`, driven via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root, created lazily. See `docs/agents/domain.md`.
