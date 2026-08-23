# Screener Kanban Tab — Design & Plan

## Problem

The dashboard has two tabs: LIVE OPERATIONS (service cards, parameters, event ticker) and PERFORMANCE & ANALYTICS (KPIs, return distribution, market inspection). Neither shows the screener pipeline — the funnel through which raw Polymarket markets are fetched, filtered, scored, and graduated to the quoting fleet.

The operator has no visibility into:
- How many markets the screener is scanning
- Which markets passed each filter gate and which were rejected
- Which markets are currently "graduated" (being quoted by the bot)
- The real-time flow of markets through screening phases

The spread-hunter repo (`C:\Users\Tiger\Agents\Projects\AI Trading\spread-hunter`) already has this data in `runtime/pipeline.json` (written by `scripts/rank_markets.py`), and its `:8801` dashboard renders it as a funnel view. The live repo's `engine/kpi.py` already has `_funnel_from_pipeline()` that reads `runtime/pipeline.json` and returns structured funnel data via `/api/kpi`'s `funnel` field — but the frontend doesn't render it.

## Solution

Add a third tab "SCREENER" to the dashboard that renders the screening pipeline as a **kanban board** — each screening phase is a vertical bucket/column, and each market is a card that appears in the bucket for the phase it has reached.

### The Kanban Buckets (left to right)

1. **RAW FETCH** — Markets just fetched from the venue (gamma + CLOB sampling-markets). Shows total count and a sample of titles. This is the input universe.

2. **IDENTITY GATE** — Markets that passed the identity filter (primary/main-line markets only; submarkets, dynamic keywords blocked). Rejected markets show the rejection reason (e.g., "carries a submarket group label", "blocked dynamic/submarket keyword").

3. **VOLUME GATE** — Markets that passed the 24h volume floor. Rejected markets show measured volume vs bar (e.g., "24h volume $79,576 < $125,000").

4. **DEPTH GATE** — Markets that passed the top-3 bid depth check (both YES and NO sides). Rejected markets show measured depth vs bar (e.g., "YES: top-3 bid depth $227.66 <= $500.00").

5. **SPREAD GATE** — Markets that passed the book spread check. Rejected markets show the spread (e.g., "YES: spread 0.2970 > 0.0600").

6. **HORIZON GATE** — Markets that passed the days-to-resolve check. Rejected markets show measured days vs max (e.g., "horizon 109.2d > 30d").

7. **PASSED (QUOTING)** — The final graduated markets the bot is actually quoting. Each card shows: title, source (spread/rewards), est. income/day, est. capital, return %/day, live fleet status (fills, PnL).

### Card Design

Each market card in a bucket shows:
- Market title (truncated)
- The specific metric that was measured at this gate (volume, depth, spread, days)
- For PASSED bucket: income/day, capital, return %/day, live fills count
- For rejected buckets: the rejection reason with the measured value

### Rejection Buckets

Rejected markets are grouped by cause (volume, depth, spread, horizon, identity). Each rejection bucket shows:
- Count badge ("72 rejected")
- Top 4 example markets with their rejection reason
- A "would_fund" count (markets the allocator would have admitted — near-misses)

### Header

- Scan state pill (SCANNING / IDLE / STALLED) from `/api/scan-state`
- Snapshot age ("last scan: 45s ago")
- Census line ("scored 210, rejected 203, wrote top 7")
- Gates summary (volume bar, depth bar, spread max, horizon max)

## Data Sources

### Existing (already available, no new backend needed)

1. **`GET /api/kpi`** — returns `funnel` object with:
   - `raw_count`: total raw markets
   - `filters`: array of rejection buckets, each with `cause`, `n`, `examples[]`
   - `final_count`: eligible count
   - `graduated`: array of graduated markets with live fleet annotations
   - `snapshot_age`: seconds since last scan
   - `census`: human-readable census string
   - `gates`: human-readable gates string

2. **`GET /api/scan-state`** — returns `scan_state` (SCANNING/IDLE/STALLED), `seconds_since_heartbeat`, `skip_reasons[]`, `pass_reasons[]`

3. **`GET /api/active-markets`** — active markets from the registry (for cross-referencing graduated markets with live quoting status)

### Pipeline.json structure (already read by kpi.py)

```json
{
  "ts": 1787336111,
  "census": "scored 210, rejected 203 (...), wrote top 7",
  "gates": "gates: volume >= $125,000, depth >= $500, spread <= 0.06, ...",
  "counts": {"funded": 998, "spread_universe": 33, "attempted": 231, "scored": 210, "rejected": 203, "eligible": 7, "picked": 7},
  "raw": {"rewards": [...], "spread": [...]},
  "rejections": [{"cause": "volume", "n": 72, "would_fund": 58, "examples": [...]}],
  "final": [...],
  "picked": [...]
}
```

## Implementation Plan

### Files to create/modify

1. **`dash/static/index.html`** — Add 3rd tab button + tab section with kanban container
2. **`dash/static/styles.css`** — Add kanban board, bucket column, market card, rejection badge, gates header styles
3. **`dash/static/app.js`** — Add `renderScreener(kpi, scanState)` function that reads `kpi.funnel` and `scanState` to render the kanban board. Wire up tab switching for 3 tabs. Poll `/api/kpi` and `/api/scan-state` (already polled).
4. **`tests/test_live_dash.py`** — Add tests for the screener tab HTML, CSS, and JS structure

### No backend changes needed

The `/api/kpi` endpoint already returns the `funnel` data. The `/api/scan-state` endpoint already returns scan state. No new API endpoints or backend modifications required.

### Frontend implementation

The kanban board is rendered from `kpi.funnel`:
- `funnel.raw_count` → header counter for RAW FETCH bucket
- `funnel.filters[]` → rejection buckets (IDENTITY, VOLUME, DEPTH, SPREAD, HORIZON)
- `funnel.final_count` → counter for PASSED bucket
- `funnel.graduated[]` → cards in PASSED bucket (with live fills/pnl from registry)
- `funnel.snapshot_age` → freshness indicator
- `funnel.census` / `funnel.gates` → header text

The buckets flow left-to-right horizontally, with a scrollable container if the viewport is narrow. Each bucket is a vertical column with:
- Header: bucket name + count badge
- Body: list of market cards (or rejection examples for filter buckets)
- Footer: would_fund near-miss count (for rejection buckets)

### Polling

The existing `pollStatus()` function already fetches `/api/kpi` and `/api/scan-state` every 2s. The `renderScreener()` function will be called from the same poll loop, so the kanban updates in real-time with the same cadence.

### Accessibility

- Tab button has `role="tab"`, `aria-selected`, `aria-controls`
- Kanban columns have `role="list"` and `aria-label`
- Market cards have `role="listitem"`
- Keyboard navigation: tab through cards, Enter to expand details
- `aria-live="polite"` on the snapshot age indicator

## NOT in scope

- New API endpoints (funnel data already served by /api/kpi)
- Backend modifications (kpi.py already reads pipeline.json)
- Running rank_markets.py from the live repo (screener process is external)
- Real-time SSE push for screener events (polling is sufficient)
- Historical pipeline trends (would need a new time-series store)
- Near-miss tracker charts (exists in spread-hunter :8801, out of scope here)

## What already exists

1. `engine/kpi.py:_funnel_from_pipeline()` — reads `runtime/pipeline.json`, returns structured funnel
2. `/api/kpi` endpoint — includes `funnel` in its JSON response
3. `/api/scan-state` endpoint — returns SCANNING/IDLE/STALLED
4. `dash/static/app.js` — already polls both endpoints every 2s
5. `dash/static/styles.css` — existing card, pill, empty-state, and table styles to extend
6. `dash/static/index.html` — existing two-tab structure to extend to three
7. Spread-hunter `:8801` dashboard — reference implementation for pipeline visualization
8. `scripts/rank_markets.py` — the screener that writes `runtime/pipeline.json`

## Failure modes

1. **pipeline.json missing** — funnel is `null` in /api/kpi. Show empty state: "No screener data yet. The screener writes pipeline.json on each scan cycle."
2. **pipeline.json stale** — snapshot_age > 10min. Show amber warning: "Last scan was Xm ago — screener may be stopped."
3. **scan-state STALLED** — show red pill in header: "STALLED — screener heartbeat lost"
4. **Zero graduated markets** — empty PASSED bucket with: "No markets graduated. The screener hasn't found any qualifying markets."
5. **Zero rejections** — all filter buckets show: "All markets passed this gate."

## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale |
|---|-------|----------|----------------|-----------|-----------|
| 1 | CEO | Dedicated 3rd tab (not embedded) | Premise gate | P1+P2 | Kanban needs horizontal space; distinct operational concern |
| 2 | Design | Add loading skeleton for kanban | Auto-decide | P1 | Complete interaction state coverage |
| 3 | Design | Reuse existing CSS variables + fonts | Auto-decide | P4+P5 | DRY; explicit over clever |
| 4 | Design | Horizontal scroll on narrow viewports | Auto-decide | P1 | Responsive completeness |
| 5 | Design | Always show all 7 buckets (even empty) | Auto-decide | P5 | Explicit — empty buckets are signal |
| 6 | Design | 4 rejection examples per bucket | Auto-decide | P4 | DRY — matches pipeline.json's existing cap |
| 7 | Eng | No backend changes | Auto-decide | P4 | DRY — kpi.py already reads pipeline.json |

## Cross-Phase Themes

No cross-phase themes — this is a focused frontend feature with existing data infrastructure.

## GSTACK REVIEW REPORT

| Review | Runs | Status | Findings | Critical Gaps |
|--------|------|--------|----------|---------------|
| CEO | 1 | clean | 0 | 0 |
| Design | 1 | issues_open | 1 (loading skeleton) | 0 |
| Eng | 1 | clean | 0 | 0 |
| DX | 0 | skipped | — | — |
| Outside Voice | 0 | skipped | — | — |

VERDICT: APPROVED — 1 design issue auto-decided (loading skeleton), 0 critical gaps, 7 decisions in audit trail.

NO UNRESOLVED DECISIONS
