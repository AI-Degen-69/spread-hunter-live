# Design Spec: Spread Hunter Live Operations & Analytics Dashboard

**Date:** 2026-08-21  
**Project:** Spread Hunter Live (`spread-hunter-live`)  
**Status:** Approved for Implementation Planning  

---

## 1. Overview & Problem Statement

The previous dashboard suffered from visual noise, specifically flickering/pulsating CSS breathing animations, ambiguous status indicators when the bot was stopped (e.g. alarming "STALLED" tags with stale timestamps), and mixed responsibilities between real-time bot operations and historical trade analytics.

This redesign establishes a clean, high-contrast, professional **Two-Tab Operations & Performance Interface**:
- **Tab 1: Live Operations & Process Manager** — Complete transparency into active background processes, manual toggle controls, floating explanation tooltips with raw CLI commands, parameter triggers/actions, and an expandable screener funnel.
- **Tab 2: Trade Analytics & Performance** — Hero KPIs (Net Portfolio Value, Realized/Unrealized PnL with starting capital baseline, Sharpe, Drawdown, Hedged vs Single-sided fill counts), return distribution charts, category mix donuts, and an interactive depth inspection table.

---

## 2. Global Architecture & Layout

### 2.1 Top Navigation Bar
- **App Branding:** `SPREAD HUNTER LIVE` + status badge.
- **Wallet & Balances:** Live funder address (`0xeE3B...1624`), available USDC cash, and marked collateral value.
- **Global Action:** Prominent **EMERGENCY CANCEL ALL** button with confirmation modal.
- **Tab Switcher:**
  - `[ 🚀 TAB 1: LIVE OPERATIONS ]`
  - `[ 📊 TAB 2: PERFORMANCE & ANALYTICS ]`
- **Visual Aesthetic:** Zero CSS breathing or flickering animations. Solid status pills with high contrast (Green for `ACTIVE`, Slate Gray for `STOPPED`, Amber for `RECONNECTING`, Crimson for `ERROR`).

---

## 3. Tab 1: Live Operations & Process Manager

### 3.1 Service Cards & Individual Controls
Four independent cards represent the background architecture:

1. **Screener (`scripts.rerank_loop` / `engine.market_feed`)**
   - *Purpose:* Scans 500+ Polymarket binary markets and screens down to 8 graduated pairs.
   - *Controls:* Independent `[ START ]` / `[ STOP ]` toggle switch.
   - *Status Display:* PID, Uptime, Markets Filtered Count, Last Run Timestamp.
   - *Info Bubble (?):* Explains functionality + raw CLI command: `python -m scripts.rerank_loop` with one-click copy.
   - *Expandable Kanban Funnel:*
     `Raw Polymarket Feed (500+) → Min Liquidity (>$500) → Spread Check (<4.5%) → Price Band (0.10-0.90) → Graduated Markets (8)`.

2. **Engine Poll Loop (`engine.order_manager poll`)**
   - *Purpose:* Polls Polymarket CLOB every 0.5s, reconciles order fills against SQLite database, and executes account sweeps.
   - *Controls:* Independent `[ START ]` / `[ STOP ]` toggle switch.
   - *Status Display:* PID, Uptime, Venue Latency (ms), Fills Reconciled, DB Sync Status.
   - *Info Bubble (?):* Explains functionality + raw CLI command: `python -m core_brain.order_manager poll --interval 0.5`.

3. **Quoting Fleet (`engine.live_fleet loop`)**
   - *Purpose:* Evaluates two-sided pricing, computes size ladders ($3/leg budget), and submits maker bids to Polymarket.
   - *Controls:* Independent `[ START ]` / `[ STOP ]` toggle switch.
   - *Status Display:* PID, Active Quoting Markets Count, Resting Notional ($), Total Orders Resting.
   - *Info Bubble (?):* Explains functionality + raw CLI command: `python -m core_brain.live_fleet loop`.
   - *Expandable Rotation Queue:* Shows the sequential rotation order of active markets and quoting calculations.

4. **Guardrail Watchdog (`scripts.global_stop_loss`)**
   - *Purpose:* Continuous risk monitor enforcing hard exposure and inventory limits.
   - *Controls:* Independent `[ START ]` / `[ STOP ]` toggle switch.
   - *Status Display:* PID, Heartbeat health, Active Exposure ($ vs $100 cap), Max Naked Loss ($ vs $6 cap).
   - *Info Bubble (?):* Explains functionality + raw CLI command: `python -m scripts.global_stop_loss`.

### 3.2 Strategy Parameters & Trigger Rules Panel
An interactive settings panel displaying active bot constraints with clear trigger/action mappings:

| Parameter | Current Value | Trigger Condition (What triggers it?) | Action Taken (What happens?) |
|---|---|---|---|
| `max_naked_usd` | `$6.00` | One leg fills while the opposing leg is unfilled, creating unhedged exposure > $6 | Stops quoting new orders on that market; prepares emergency exit / merge |
| `max_order_usd` | `$25.00` | Order sizing calculation generates a single order > $25 | Clamps size to $25 floor to prevent accidental capital overcommitment |
| `max_total_usd` | `$100.00` | Sum of all open notional across fleet reaches $100 | Refuses all new quotes across all markets until existing orders settle or cancel |
| `min_quote_shares` | `5 shares` | Calculated order size falls below Polymarket venue minimum | Refuses single-sided quote or scales up to 5 shares if budget permits |
| `sweep_interval` | `30s` | 30 seconds elapsed since last wallet balance query | Fetches fresh on-chain USDC balance and updates float marks |

### 3.3 Live Event Ticker (Decision Stream)
- Clean, searchable stream at the bottom of Tab 1.
- Shows timestamped events from `cycle_events.jsonl` (e.g. `[19:15:02] [FLEET] Evaluated atp-miami: Rested YES @ 0.485 (12 shares), NO @ 0.505 (12 shares)`).

---

## 4. Tab 2: Performance & Trade Analytics

### 4.1 Hero Section: Trading Account Performance KPIs
- **Net Portfolio Value ($):** Total USDC cash + marked value of held outcome tokens.
- **Starting Capital ($):** Locked snapshot of account equity at the moment the bot is toggled ON.
- **Realized PnL ($ and %):**
  $$\text{Realized PnL } \$ = \text{Total Closed Gains} - \text{Total Closed Losses}$$
  $$\text{Realized PnL } \% = \frac{\text{Realized PnL } \$}{\text{Starting Capital } \$} \times 100$$
- **Unrealized PnL ($ and %):** Mark-to-market drift of open, unsettled inventory.
- **Risk Ratios:**
  - **Max Drawdown (%):** Peak-to-trough decline in portfolio equity.
  - **Sharpe Ratio:** Annualized risk-adjusted return metric.
  - **Risk-Reward Ratio:** Average profit on winning markets vs average loss on unbalanced markets.
  - **Win Rate (%):** Percentage of resolved markets finishing profitable (with 95% Wilson confidence bounds).
- **Fill Execution Quality:**
  - **Total Filled Volume ($)**
  - **Two-Sided Hedged Fills Count** (both YES and NO bids matched, locking in spread capture).
  - **Single-Sided Fills Count** (only one leg filled, incurring inventory risk).

### 4.2 Return Distribution & Visual Exposure Charts

1. **Settled Returns Distribution Chart:**
   - Canvas/SVG rendered bell curve fitted with real mean ($\mu$) and standard deviation ($\sigma$) of settled market returns.
   - 90% confidence lower bound indicator and shaded $\pm 1\sigma$ region.
   - PnL histogram overlay of historical trade outcomes.

2. **Category Mix & Concentration Donut:**
   - Donut chart displaying capital commitment by category (e.g. Sports 70%, E-Sports 25%, Crypto 5%).
   - Color-coded vs maximum category concentration limit (e.g., 80% category cap).
   - Calendar indicator showing average days-to-resolution (e.g. `12.4 / 14 d`).

3. **Adverse Selection Markouts:**
   - Multi-horizon line chart showing price drift after fill at **+5 min, +15 min, +1 hour, +6 hours**.
   - Proves whether maker spread capture exceeds market price drift.

### 4.3 Detailed Market Inspection Table
Interactive table with 3 view modes:
`[ ACTIVE MARKETS ]` / `[ CLOSED HISTORY ]` / `[ SELECTION FUNNEL ]`

#### Columns:
1. **Market:** Title, slug, category badge (`Sports`, `E-Sports`), and direct Polymarket link.
2. **Order Depth / Mid Bar:** Visual depth bar showing:
   - Green bar for YES bid depth and price.
   - Center label for MID price.
   - Red bar for NO bid depth and price.
3. **Commit ($):** Total capital committed to resting quotes on this market.
4. **Unrealized P&L ($ / %):** Mark-to-market drift for open inventory.
5. **Realized P&L ($):** Booked profit from resolved or merged positions.
6. **Fills:** Count of fills with visual tag (`Hedged Pair` vs `One-Sided`).
7. **Status:** High-contrast badge (`QUOTING`, `BLOCKED ONE_SIDED`, `RESOLVED`, `SETTLED`).

#### Row Expansion (Drawer):
Clicking any row opens a drill-down drawer showing:
- Real-time book snapshot (Top 5 bids/asks).
- Complete fill history for this specific market.
- On-chain token holdings and redemption status.

---

## 5. Technical Implementation & Data Flow

### 5.1 Backend Routes (FastAPI in `dash/live_dash.py`)
- `GET /` — Serves the modular, modern Two-Tab HTML/CSS/JS frontend.
- `GET /api/status` — Returns overall bot status, service PIDs, uptimes, and starting capital baseline.
- `POST /api/service/{name}/{action}` — Starts or stops individual background services (`screener`, `engine`, `fleet`, `watchdog`, `all`).
- `GET /api/parameters` — Returns active strategy settings, trigger thresholds, and action descriptions.
- `GET /api/kpi` — Returns aggregate portfolio analytics, Sharpe, Drawdown, win rates, and return distributions.
- `GET /api/active-markets` — Returns graduated and currently quoted markets with order depth and PnL.
- `GET /api/closed-markets` — Returns historical closed/settled markets and booked PnL.
- `GET /api/cycle-stream` — SSE stream of real-time operational events from `runtime/cycle_events.jsonl`.

### 5.2 Frontend Technology & Styling
- Pure semantic HTML5 + Vanilla CSS (clean CSS design system with custom CSS variables for dark-mode palette, crisp cards, flex/grid layouts, no heavy external CSS libraries).
- Modern Vanilla JS with reactive SSE subscriptions and atomic DOM updates.
- Canvas/SVG rendering for distribution bell curves, category donuts, and orderbook depth bars.
- Zero pulsating animation classes.

---

## 6. Testing & Quality Assurance Plan
1. **API Unit Tests (`tests/test_live_dash.py`):**
   - Test starting capital tracking across bot ON/OFF transitions.
   - Test service start/stop endpoint execution and PID tracking.
   - Test `/api/parameters` endpoint schema and values.
   - Test `/api/active-markets` and `/api/closed-markets` filtering.
2. **Visual & UI Verification:**
   - Test in browser on port `:8799`.
   - Verify tab switching, process toggle interactions, info tooltips, and row expansion drawers.
   - Verify responsiveness on standard desktop resolutions.

---

## NOT in scope

- **Decoupling guardrail watcher from poll loop** — The watcher is a child of `engine/live_exec.py:poll()` (spawned by `_spawn_global_stop_losser` at line 1686, supervised by `_supervise_watcher`). Making it a true independent service requires engine changes. Deferred — the 4th card stays read-only.
- **Full independent START for each service** — `start_bot()` launches all 3 services atomically with an interprocess lock. Only individual STOP (screener, fleet) is in scope. Independent START risks partial-starts where the fleet submits orders against stale engine-reconciled state.
- **New `/api/analytics` endpoint for Tab 2** — Tab 2 reuses the existing `GET /api/kpi` response. No parallel computation path.
- **Rebuilding existing API routes under new names** — The existing `/api/system/*` routes already have CSRF tokens, PID recycling protection, and 20+ tests. Only genuinely new routes (`/api/parameters`, `/api/active-markets`, `/api/closed-markets`, `/api/system/cancel-all`) are added.
- **CI/CD pipeline for the dashboard** — No new artifact type is introduced; the dashboard is served by the existing FastAPI app on `:8799`.

## What already exists (reuse, do not rebuild)

1. `GET /api/system/status` (dash/live_dash.py:766) — returns supervisor + 4 sub-services + bot_state. Service cards read this.
2. `POST /api/system/start` + `/stop` (lines 772, 779) — control bot stack with CSRF tokens, PID recycling, interprocess locking.
3. `GET /api/kpi` (line 846) — returns portfolio, equity series, trade_analytics (Sharpe, drawdown, win rate, PnL distribution), funnel, by_market, float_marks. Tab 2 reads this.
4. `GET /api/cycle-stream` (line 1220) — SSE stream with rotation detection. Live Event Ticker reads this.
5. `GET /api/guardrail-health` (line 1208) — watcher PID, heartbeat, alert count. 4th card reads this.
6. `GET /api/scan-state` (line 996) — SCANNING/IDLE/STALLED + skip/pass rationale. Screener funnel reads this.
7. `GET /api/pairs-activity` (line 1138) — auto-pairs counts per cycle and per pair.
8. `GET /api/guardrail-alerts` (line 1181) — active guardrail violations, newest-first. Banner reads this.
9. `engine/kpi.py:compute_trade_analytics()` (line 49) — all Tab 2 analytics (Sharpe, Sortino, drawdown, win rate with Wilson CI, risk-reward, profit factor, PnL distribution).
10. `engine/live_exec.py:cancel_all()` (line 2357) — the venue cancel-all function (closing command, pre-approved per AGENTS.md).
11. `controlFetch()` + `_authorize_control()` (dash/live_dash.py:2455, 262) — the CSRF token + origin check pattern all control endpoints use.

## Failure modes

1. **Cancel-all without CSRF** (Issue 1) — a cross-origin form POST cancels all open orders on a live bot. Test: `test_control_endpoints_reject_untokened_posts` pattern. Error handling: `_authorize_control()` raises 403. User sees: 403 forbidden. **Covered by plan.**
2. **Starting capital uses config bankroll instead of real snapshot** (Issue 4) — PnL% measured against $100 nobody deposited. Test: verify `live_procs.json` has `starting_account_value` after start. Error handling: None if venue balance unavailable — shows fallback label. User sees: "simulated baseline" label. **Covered by plan.**
3. **Static file extraction breaks 7 PAGE_HTML tests** (Issue 6) — test suite goes red. Tests rewritten to read from `dash/static/`. **Covered by plan (CRITICAL regression).**
4. **Individual STOP kills wrong PID** (Issue 2) — PID recycling could target the wrong process. Test: verify PID + started_at match before kill. Error handling: `_is_pid_alive()` with `started_at` tolerance. User sees: service stays STOPPED. **Covered by plan.**
5. **Frontend loses 4 data sources** (Issue 9, Codex) — screener state, guardrail banner, pairs activity go dark. Test: verify Tab 1 fetches all 12 endpoints. **Covered by plan.**

**Critical gaps: 0** — all failure modes have planned tests and error handling.

## Worktree parallelization strategy

| Step | Modules touched | Depends on |
|------|----------------|------------|
| Extract frontend to static files (D3) | dash/static/, dash/live_dash.py | — |
| Consolidate safety limits into MakerConfig (Issue 3) | engine/config.py, engine/venue.py, engine/live_exec.py, engine/live_fleet.py | — |
| Add new API routes (Issues 1, 5, 9) | dash/live_dash.py | Issue 3 (parameters endpoint reads config) |
| Starting capital snapshot (Issue 4) | dash/live_dash.py (start_bot), engine/kpi.py | — |
| Individual service STOP (Issue 2) | dash/live_dash.py (stop_bot) | — |
| Frontend rewrite (two-tab layout) | dash/static/ | D3 extraction, all API routes |
| Rewrite 7 broken tests + 16 new tests (Issues 6, 7) | tests/test_live_dash.py | D3 extraction, all API routes |

**Lane A:** Issue 3 (config consolidation) → new API routes (Issues 1, 5, 9) → starting capital (Issue 4) — sequential, all touch engine/ or dash/
**Lane B:** D3 extraction → frontend rewrite — sequential, touches dash/static/ and dash/live_dash.py
**Lane C:** Test rewrite (Issue 6) + new tests (Issue 7) — sequential, touches tests/

Launch A + B + C in parallel. Merge A first (routes must exist before frontend can consume them). Merge B after A. Merge C last (tests verify the final shape).

**Conflict flags:** Lanes A and B both touch `dash/live_dash.py` — potential merge conflict. Consider doing A's route additions and B's extraction in sequence, not parallel.

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~2h / CC: ~15min)** — dash/live_dash.py — Add `POST /api/system/cancel-all` with CSRF protection
  - Surfaced by: Architecture Review — Issue 1 (cancel-all security)
  - Files: dash/live_dash.py, engine/live_exec.py (import cancel_all)
  - Verify: `pytest tests/test_live_dash.py -k cancel`
- [ ] **T2 (P1, human: ~1h / CC: ~10min)** — engine/kpi.py, dash/live_dash.py — Implement real starting capital snapshot
  - Surfaced by: Code Quality Review — Issue 4 (starting capital is config bankroll, not real snapshot)
  - Files: dash/live_dash.py (start_bot writes starting_account_value to live_procs.json), engine/kpi.py (reads it instead of _CFG.bankroll_usd)
  - Verify: `pytest tests/test_live_dash.py -k starting_capital`
- [ ] **T3 (P1, human: ~1h / CC: ~10min)** — tests/test_live_dash.py — Rewrite 7 broken PAGE_HTML tests for static file extraction
  - Surfaced by: Test Review — Issue 6 (CRITICAL regression: 7 tests reference PAGE_HTML which will not exist after D3)
  - Files: tests/test_live_dash.py
  - Verify: `pytest tests/test_live_dash.py -k "milestone8 or level1 or dashboard_script or escaped or status_bar or bot_brains or control_token"`
- [ ] **T4 (P2, human: ~30min / CC: ~5min)** — dash/live_dash.py — Add individual service STOP (screener, fleet only)
  - Surfaced by: Architecture Review — Issue 2 (per-service start/stop not implementable as written; atomic start + individual stop)
  - Files: dash/live_dash.py (stop_bot modified to target individual services by name)
  - Verify: `pytest tests/test_live_dash.py -k individual_stop`
- [ ] **T5 (P2, human: ~30min / CC: ~5min)** — engine/config.py, engine/venue.py — Consolidate MAX_ORDER_USD and MAX_TOTAL_USD into MakerConfig
  - Surfaced by: Architecture Review — Issue 3 (safety limits scattered across two files)
  - Files: engine/config.py, engine/venue.py, engine/live_exec.py, engine/live_fleet.py (import sites)
  - Verify: `pytest tests/test_live_exec.py tests/test_live_fleet.py`
- [ ] **T6 (P2, human: ~1h / CC: ~10min)** — dash/live_dash.py — Add GET /api/parameters, GET /api/active-markets, GET /api/closed-markets
  - Surfaced by: Scope Challenge — D2 (only 2 genuinely new routes; parameters reads consolidated MakerConfig)
  - Files: dash/live_dash.py
  - Verify: `pytest tests/test_live_dash.py -k "parameters or active_markets or closed_markets"`
- [ ] **T7 (P2, human: ~1h / CC: ~10min)** — dash/live_dash.py — Add 4 missing endpoints to route list documentation
  - Surfaced by: Outside Voice (Codex) — Issue 9 (plan omits /api/scan-state, /api/pairs-activity, /api/guardrail-alerts, /api/guardrail-health)
  - Files: docs/superpowers/specs/2026-08-21-dashboard-redesign-design.md (update §5.1), dash/live_dash.py (ensure frontend fetches them)
  - Verify: frontend fetches all 12 endpoints in Tab 1
- [ ] **T8 (P2, human: ~30min / CC: ~5min)** — dash/live_dash.py — Keep index() as template for token injection after static extraction
  - Surfaced by: Performance Review — Issue 8 (control token can't be baked into static files)
  - Files: dash/live_dash.py (index route reads dash/static/index.html, .replace() token)
  - Verify: `pytest tests/test_live_dash.py -k control_token`
- [ ] **T9 (P2, human: ~2h / CC: ~15min)** — dash/static/ — Extract PAGE_HTML to dash/static/{index.html, styles.css, app.js} + mount StaticFiles
  - Surfaced by: Scope Challenge — D3 (monolithic file growth past 5000 lines)
  - Files: dash/static/index.html, dash/static/styles.css, dash/static/app.js, dash/live_dash.py (mount StaticFiles, remove PAGE_HTML)
  - Verify: `pytest tests/test_live_dash.py`
- [ ] **T10 (P2, human: ~4h / CC: ~30min)** — dash/static/ — Rewrite frontend as two-tab layout consuming existing API responses
  - Surfaced by: Scope Challenge — D3 + Code Quality — Issue 5 (Tab 2 reuses /api/kpi, not new computation)
  - Files: dash/static/index.html, dash/static/styles.css, dash/static/app.js
  - Verify: manual browser test on :8799, `node --check dash/static/app.js`
- [ ] **T11 (P2, human: ~2h / CC: ~15min)** — tests/test_live_dash.py — Add 16 new tests for all new codepaths
  - Surfaced by: Test Review — Issue 7 (design doc QA plan incomplete)
  - Files: tests/test_live_dash.py
  - Verify: `pytest tests/test_live_dash.py -q`

## Design Review Findings (plan-design-review)

### Pass 1: Information Architecture — 4/10 → 7/10
- **Issue 1 (P1):** Card visual priority — guardrail card first, alert-colored borders, default to Tab 1 on load. Resolved: A (define visual priority).

### Pass 2: Interaction State Coverage — 2/10 → 7/10
- **Issue 2 (P1):** All interaction states unspecified. Resolved: A (loading skeletons, empty states with warmth + primary action, error states with retry, partial states with NULL labels).

### Pass 3: User Journey & Emotional Arc — 5/10 → 8/10
- **Issue 3 (P2):** Exposure indicator buried in guardrail card. Resolved: A (global exposure indicator in top nav: '$X/$Y committed' with color-coded progress bar).

### Pass 4: AI Slop Risk — 6/10 → 8/10
- **Issue 4 (P2):** Emoji in tab labels (blacklist #7), colored left-border on cards (blacklist #8). Resolved: A (remove emoji, use text-only labels; drop colored left-border pattern).

### Pass 5: Design System Alignment — 5/10 → 8/10
- **Issue 5 (P2):** No DESIGN.md, CSS variables unspecified. Resolved: A (reuse existing CSS variables + font stack, add ~5 new tokens for two-tab layout).

### Pass 6: Responsive & Accessibility — 2/10 → 8/10
- **Issue 6 (P1):** Zero a11y or responsive specs. Resolved: A (keyboard nav, ARIA live regions, 44px touch targets, mobile/tablet breakpoints, WCAG AA contrast).

### Pass 7: Unresolved Design Decisions — 4 decisions resolved
- SSE reconnection: 'reconnecting...' banner + stale timestamp
- Cancel-all modal: shows order count + notional, requires typing 'CANCEL'
- Info bubbles: click-triggered (not hover, accessible on mobile)
- Tab state: persists via localStorage on reload

### NOT in scope (design)
- **Visual mockup generation** — gstack designer needs OpenAI API key. Run `~/.claude/skills/gstack/design/dist/design setup` to enable.
- **DESIGN.md creation** — recommend running /design-consultation separately to document the design system.
- **Mobile-first redesign** — the responsive spec covers mobile/tablet breakpoints, but the dashboard is desktop-first by design (operator tool).

### What already exists (design)
1. CSS variable system: 20+ custom properties (--bg-base, --signal, --loss, --text-primary, etc.) in dash/live_dash.py:1255-1290
2. Font stack: Big Shoulders Display (headers), Inter (body), JetBrains Mono (data) — strong, specific choice
3. Card pattern: .card class with backdrop-filter blur, --bg-card background
4. esc() function: XSS defense for all DB values reaching innerHTML
5. controlFetch() pattern: CSRF token injection for all control endpoints
6. Status pill pattern: .pill class with green/red variants (already solid, no animation needed)

## Implementation Tasks (Design Review)

- [ ] **DT1 (P1, human: ~1h / CC: ~10min)** — dash/static/ — Add card visual priority: guardrail first, alert-colored borders, default Tab 1
  - Surfaced by: Pass 1 — Issue 1 (information hierarchy)
  - Files: dash/static/index.html, dash/static/styles.css, dash/static/app.js
  - Verify: browser test — guardrail card is first, red border when alert active
- [ ] **DT2 (P1, human: ~2h / CC: ~15min)** — dash/static/ — Add all interaction states: loading skeletons, empty states, error retry, NULL labels
  - Surfaced by: Pass 2 — Issue 2 (zero interaction states specified)
  - Files: dash/static/styles.css, dash/static/app.js
  - Verify: browser test — empty db shows friendly empty states, not blank charts
- [ ] **DT3 (P2, human: ~30min / CC: ~5min)** — dash/static/ — Add global exposure indicator to top nav bar
  - Surfaced by: Pass 3 — Issue 3 (exposure buried in card)
  - Files: dash/static/index.html, dash/static/styles.css, dash/static/app.js
  - Verify: browser test — '$X/$Y committed' visible in top nav with color bar
- [ ] **DT4 (P2, human: ~15min / CC: ~2min)** — dash/static/ — Remove emoji from tab labels, use text-only labels
  - Surfaced by: Pass 4 — Issue 4 (AI slop blacklist #7)
  - Files: dash/static/index.html
  - Verify: browser test — tab labels are 'LIVE OPERATIONS' / 'PERFORMANCE & ANALYTICS'
- [ ] **DT5 (P2, human: ~30min / CC: ~5min)** — dash/static/styles.css — Reuse existing CSS variables, add new tokens for two-tab layout
  - Surfaced by: Pass 5 — Issue 5 (no DESIGN.md, variables unspecified)
  - Files: dash/static/styles.css
  - Verify: grep for --bg-base, --signal, --loss in styles.css (existing tokens preserved)
- [ ] **DT6 (P1, human: ~2h / CC: ~15min)** — dash/static/ — Add full a11y: keyboard nav, ARIA live regions, 44px touch targets, responsive breakpoints
  - Surfaced by: Pass 6 — Issue 6 (zero a11y or responsive specs)
  - Files: dash/static/index.html, dash/static/styles.css, dash/static/app.js
  - Verify: keyboard tab through all controls, mobile breakpoint shows stacked cards
- [ ] **DT7 (P2, human: ~1h / CC: ~10min)** — dash/static/ — Resolve 4 design decisions: SSE reconnect banner, cancel-all typed confirmation, click info bubbles, localStorage tab persistence
  - Surfaced by: Pass 7 — Issue 7 (unresolved design decisions)
  - Files: dash/static/index.html, dash/static/styles.css, dash/static/app.js
  - Verify: cancel-all modal requires typing 'CANCEL', SSE shows reconnect banner on drop

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | 4 findings (3 confirmed by review, 1 new gap adopted) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 9 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | issues_open | 7 issues, score: 5/10 → 8/10, 4 decisions made |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**CODEX:** Found 4 issues — 3 confirmed existing review findings (per-service controls, starting capital, CSRF scope), 1 genuinely new gap (missing 4 data-dependency endpoints). All adopted into the plan.

**CROSS-MODEL:** Strong agreement on architecture. Codex's missing-endpoints finding (Issue 9) was the only gap the eng review missed. Resolution: add 4 existing endpoints to the plan's route list.

**VERDICT:** ENG + DESIGN reviewed — 9 eng issues + 7 design issues, all resolved. 0 critical gaps. Ready to implement with scope decisions, eng issues, and design decisions applied.

NO UNRESOLVED DECISIONS
