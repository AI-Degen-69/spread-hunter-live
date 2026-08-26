# Screener Kanban: Sequential Waterfall & Near-Miss Diagnostics

## Problem Statement
How Might We transform the screener pipeline view into an intuitive, sequential waterfall Kanban board so the operator can instantly diagnose filter drop-offs, spot near-miss trading opportunities, and audit the quoting fleet?

## Recommended Direction
Build a 7-stage sequential waterfall Kanban board (`Discovery` → `Identity` → `Volume` → `Depth` → `Spread` → `Horizon` → `Quoting`) with:
1. **Mathematical Stage Progression**: Each column displays `entering` candidates, `dropped` count with reason metrics, and `advancing` survivors moving to the next gate.
2. **Robust Gate Aggregation**: Standardize gate key mapping in [app.js](file:///c:/Users/Tiger/Agents/Projects/spread-hunter-live/dashboard/static/app.js) so detailed rejection causes (e.g. `"YES: top-3 bid depth"`, `"NO spread"`) map cleanly to canonical gates rather than zeroing out.
3. **Enlarged 320px Diagnostic Containers**: Expand column widths from 220px to 320px and min-height to 560px with custom scrollbars, highlighting top rejected candidates with near-miss tags (`would_fund`, threshold delta) and rendering full condition details in the final `Quoting` column.

## Key Assumptions to Validate
- [ ] Rejection causes from `scripts/filter_markets.py` are comprehensively mapped to the 5 canonical filter keys without unhandled edge cases.
- [ ] Sequential waterfall stage progression numbers reconcile exactly with `pipeline.json` totals.
- [ ] 320px columns scroll smoothly with keyboard arrow navigation and carousel controls across standard desktop viewports.

## MVP Scope
- **Included**:
  - Canonical gate regex/keyword classifier (`categorizeGate()`).
  - Stage headers showing `entering -> dropped -> advancing` flow counts.
  - 320px column layout and 560px container height in [styles.css](file:///c:/Users/Tiger/Agents/Projects/spread-hunter-live/dashboard/static/styles.css).
  - Near-miss alert tags on candidate cards (`would clear allocator floor`).
  - Rich Quoting fleet cards with Condition ID, Fills, 24h Vol, Spread %, and Days to Resolve.
- **Excluded**:
  - In-browser filter parameter adjustment (remains read-only for safety).
  - Complex modal drilldown popups (capped at top 8 cards + counter badge for speed).

## Not Doing (and Why)
- **Live parameter tuning from UI**: Prevents accidental venue risk limit overrides from browser clicks.
- **Full DOM rendering of 200+ rejected cards**: Keeps dashboard poll cycle snappy and DOM memory low by capping preview cards at 8 per column.
- **Dynamic gate reordering**: Preserves fixed filter evaluation order matching `scripts/filter_markets.py` execution pipeline.

## Open Questions
- None blocking implementation.
