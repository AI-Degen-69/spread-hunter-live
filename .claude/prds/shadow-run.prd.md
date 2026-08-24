# Shadow Run — watchable, money-free full-loop demo

## Problem

The operator has no way to watch the spread hunter run end to end before trusting it with
real money. `--no-live` prints what a single subcommand would send and exits; it never
reaches fills, never reaches the merge path, and never shows the dashboard reacting.
`trader_loop` has a `DRY_RUN` status but is entered through a LIVE-by-default command.
The result is that every observation of the full loop — screener through merge — costs
real capital, so the loop's behaviour under real market conditions is confirmed only in
production, where the two failure modes that matter (a pair assembled over $1.00, and a
single filled leg) are expensive to discover.

## Evidence

- Operator, this session: "I don't have a reliable test or a dry run that I can watch
  being executed, like a demo of the bot running that will be as close as possible to live."
- `core_brain/order_manager.py:24` — `--no-live` "prints what it WOULD send and exits".
  It terminates before any fill, merge or dashboard state change.
- `core_brain/trader_loop.py` exposes `DRY_RUN` as a per-market result status, but `main`
  defaults `--live` to True; there is no non-live entrypoint for the rotation loop.
- `core_brain/audit.py` already reconciles Registry / Venue / Chain, so a self-audit
  verdict has a precedent to follow — but it audits real positions, not a rehearsal.
- Assumption — needs validation via first shadow run: that a full loop rehearsal actually
  surfaces defects a passing `pytest -q` does not.

## Users

- **Primary**: the operator of this repo, at two moments — (a) preflight, immediately
  before starting a live session, wanting confidence the loop is healthy today; (b) after
  changing quoting, sizing, gates or the dashboard, wanting to see the change behave
  against a real book before it spends money.
- **Secondary**: an agent working in this repo, which currently cannot verify loop-level
  behaviour without an operator-run live command, and which reads the end-of-run summary
  as evidence.
- **Not for**: automated CI. Shadow run reads the live venue, so it is not deterministic
  and is not a replacement for the unit suite. Not for backtesting historical periods.
  Not for anyone other than this repo's operator.

## Hypothesis

We believe **a shadow run — the complete loop against the live order book, halted at the
instant before order submission, with fills judged by watching the real book, and a
self-audit summary at the end** will **let the operator see and trust the whole machine
without risking capital** for **the operator at preflight and after any change to the
trading path**.

We'll know we're right when **the operator runs shadow mode before a live session instead
of starting live to find out, and at least one real defect is caught by a shadow run
before it reaches live**.

## Success Metrics

| Metric | Target | How measured |
|---|---|---|
| Loop stages exercised in one run | All stages: screener, gate evaluation, quote decision, intent formation, book watch, fill judgement, pair assembly, merge path | Loop-health section of the run summary names any stage that never executed |
| Real orders placed during any shadow run | 0, structurally impossible | No signing client is constructed in the shadow entrypoint; venue write calls abort the process if reached |
| Production registry mutated | 0 rows | `data/orders.db` untouched; shadow state lives in its own store |
| Operator time from command to visible loop activity | Under 60 seconds | Wall-clock from command to first dashboard update |
| Over-$1.00 pairs escaping notice | 0 | Every shadow pair's combined intended cost is reported with a hard flag over $1.00 |
| Preflight adoption | Shadow run precedes live starts | Operator behaviour, reviewed after two weeks of use |

## Scope

**MVP** — one operator command that runs the real rotation loop against the live venue in
read-only fashion for an operator-set number of minutes, and that:

1. Uses **live read-only venue data** — real screener output, real markets, real order
   books, real spreads. Reads only; no write path to the venue exists in this mode.
2. Runs the loop up to the **instant before submission**. For every order it would have
   sent, it records the intended side, token, limit price and share size, and surfaces
   that as the intent.
3. **Judges fills by watching the real book.** A recorded intent is marked filled when
   the live book moves such that a resting order at that price and size would have been
   taken. Partial fills and one-leg-only outcomes are recorded as they occur, not assumed.
4. Carries filled shadow legs through the **rest of the loop** — pair assembly, single-buy
   detection, and the merge path — so the stages after fill are exercised, not skipped.
5. Is **structurally incapable of spending money**: a separate entrypoint that never
   constructs a signing client. Submission is not disabled by a flag; there is no key
   loaded with which to sign.
6. Writes all state to a **separate shadow store**, not `data/orders.db`. The production
   registry is not written in any circumstance.
7. Is **watched on the dashboard**, carrying an unmissable shadow badge — a banner and a
   colour treatment that makes shadow data impossible to mistake for live at a glance.
8. **Stops on an operator-set time box** (`--minutes N`, short default), then finalises.
9. Ends with a **self-audit summary** verdicting on three things:
   - **Loop health** — which stages ran, which never fired, and why.
   - **Would-be economics** — per shadow pair: intended UP price, intended DOWN price,
     combined cost, profit per pair, and a hard flag on any pair over $1.00.
   - **Single-buy exposure** — every case where one leg would have filled and the other
     would not, how long the exposure lasted, and whether the single-buy saver would
     have fired.

**Out of scope**

- **Recorded replay and synthetic books** — deferred. Live read-only is closest to live,
  which is the whole point; determinism can come later if flakiness proves it necessary.
- **Dashboard fidelity auditing** — the summary does not verdict on whether the dashboard
  display matched underlying shadow state. Deferred; the operator watches the dashboard
  directly in MVP.
- **Terminal narration as a separate watch surface** — the dashboard is the watched
  surface. Logs exist, but a designed step-by-step console narration is not built.
- **Backtesting or historical replay** — different product, different value.
- **CI integration** — shadow run reads a live venue and is not deterministic.
- **Any change to the live trading path's behaviour** — shadow mode observes the existing
  decision logic; it does not alter quoting, sizing, gates or risk limits.
- **Simulating venue rejections, rate limits, or outages** — fills are judged from book
  movement only; adverse venue behaviour is not modelled.

## Delivery Milestones

<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->
<!-- Status: pending | in-progress | complete -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Money-free entrypoint | Operator can start a time-boxed shadow session that reads the live venue and cannot sign anything, writing to its own store | in-progress | `.claude/plans/shadow-run.plan.md` |
| 2 | Intents captured at the submission boundary | Every order the loop would have sent is recorded with side, token, limit price and size, instead of being sent | pending | — |
| 3 | Fills judged from the live book | Recorded intents become shadow fills, partial fills and one-leg outcomes based on real book movement | pending | — |
| 4 | Post-fill stages exercised | Shadow fills flow through pair assembly, single-buy detection and the merge path | pending | — |
| 5 | Dashboard shows the run, unmistakably shadow | Operator watches the loop live on the dashboard with a badge that cannot be confused for live data | pending | — |
| 6 | Self-audit summary | Run ends with a verdict on loop health, would-be economics and single-buy exposure | pending | — |

## Open Questions

- [ ] Where does the summary land? Assumed: written as a run artifact (readable by an
      agent afterwards) **and** shown on the dashboard at run end. The operator selected
      only "dashboard" as the watch surface, so the artifact half is an assumption.
- [x] **RESOLVED — what counts as "would have filled": trade-through.** A shadow buy at
      limit price P is inferred filled when the observed book/tape reaches P or below: a
      seller taking liquidity would have hit our bid first, because ours was the better
      price. Price rising through P is **not** a fill — the spread hunter places only buy
      orders, so a resting bid is taken by sellers coming down, never by buyers going up.
      Still open beneath the rule: whether shadow size is capped by observed traded volume,
      and whether queue position is modelled at all. Default until decided: no queue model,
      and size capped by observed volume at or through P, because the conservative choice
      is the one that does not manufacture confidence.
- [x] **RESOLVED — caps and config: identical to live.** `MAX_ORDER_USD`, `MAX_TOTAL_USD`
      and every gate apply unchanged. Live is the source of truth; shadow must not use a
      relaxed configuration for demo value.
- [ ] Does the merge path have anything to do in shadow, given no tokens exist on chain?
      Reaching the merge decision may be all that is achievable; the on-chain merge itself
      cannot occur.
- [ ] Which credentials does read-only mode need, and can it run with a read-only key or
      no key at all? This determines how strong the structural safety guarantee really is.
- [ ] How does the operator switch the dashboard between live and shadow state, and what
      happens if a live session and a shadow run are active at once?
- [ ] Default time box length — what number makes a demo long enough to see fills but
      short enough to actually watch?

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Shadow fill judgement is optimistic, so the run looks profitable and live does not | High | High — false confidence is worse than no rehearsal | State the fill rule explicitly, be conservative by default, and report the rule in every summary so results are read with it in view |
| Shadow data is mistaken for live on the dashboard | Medium | High — an operator could act on rehearsal numbers | Unmissable badge and colour treatment; separate store means shadow rows cannot appear in live views |
| A code path is shared with live and a change for shadow's sake alters live behaviour | Medium | Critical — this is the live trading path | Shadow observes; it does not modify decision logic. Any shared-path change needs a test that fails without it and operator sign-off |
| The run shows nothing because no market crosses in the time box | Medium | Medium — demo value collapses | Loop-health section names stages that never fired, so a quiet run is still informative; time box is operator-set |
| Structural safety is weaker than believed — some path still constructs a signer | Low | Critical — real money | Hard abort at the venue boundary if a shadow run reaches a signing call; verified by a test |
| Live venue read load or rate limits during a shadow run degrade a concurrent live session | Low | High | Decide whether concurrent live + shadow is permitted at all (open question) |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
