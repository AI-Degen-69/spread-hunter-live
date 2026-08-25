# Completable-Cost Gate and Re-Quote Dead Band — Design

**Status:** accepted, 2026-08-25
**Evidence:** shadow run `run-2809a7161de1`, `data/shadow.db`, 2026-08-25 17:15:23–17:45:29 (1806 s)

## Problem

The run posted 209 orders (1119 shares, $526.72 notional) over 30 minutes and got
**zero fills**. Two independent causes, in order of size.

### Cause 1 — quoting where nothing trades

201 of 209 orders (96.2%) saw **zero traded volume at their own price** for their entire
life. Only 8 orders ever had a print at their level. Give every order queue position 0 and
the counterfactual is still only 43 of 1119 shares filled — 3.8%.

The deeper reason is what the bot was gating on. Every order carried
`max_pair_cost_at_post = 0.995` — one distinct value across all 209 rows. That check is
`up_bid + down_bid < max_pair_cost`: a **both-maker** pair cost. It prices the outcome
where both legs fill as resting bids.

On a binary market that outcome is rare by construction:

```
corr(delta UP mid, delta DOWN mid) = -0.9989
opposite-sign steps                = 97 of 98
UP mid + DOWN mid                  = 1.000 median (min 0.995, max 1.015), 99 pairs
```

`UP + DOWN = 1.00` is close to a mechanical identity. The two legs are one instrument and
its complement. Almost every fill is a **single** fill, so the realistic way a pair
assembles is one maker fill plus a taker completion at the other leg's ask — and nothing
in the code ever checked that price. The bot was posting pairs that were only profitable
under the rarest outcome available to it.

### Cause 2 — churn resets queue position

- Median order lifetime **11.7 s** (p25 7.4, p75 17.4).
- **205 of 205** consecutive re-quotes changed price. The bot never rested the same level
  twice. Median move 3.0c, mean 3.8c, max 15c, across 47 distinct price levels per side.
- Median `queue_ahead` at post 1058.7; p75 11,837; max 46,302.8.

The churn is not fidgeting. UP mid walked 0.815 → 0.285 in 30 minutes (in-play tennis) and
every re-quote was a correct response to a real book move. It was also a correct response
that sent the order to the back of the queue at a new level.

## Requirements

### R1 — Completable-cost gate

A new resting BUY must be refused when the pair it would open cannot be completed under the
cap by **taking** the other leg:

```
block when   price + best_ask(hedge_token) >= max_completable_pair_cost
```

- `max_completable_pair_cost` default **1.00**. The pair pays exactly $1.00, so a
  completable cost at or above 1.00 is a booked loss or a zero-profit fill slot. `>=` not
  `>`, matching every other cap in `MakerConfig` ("a ceiling reached, not approached").
- Applies **only when we hold none of the hedge token** (`inv.avg(other) <= 0`). When we
  already hold the other leg, completion is not needed and the existing `max_pair_cost` arm
  governs.
- Missing or non-positive `best_ask` on the hedge book yields **no opinion**. A book that
  cannot be read is already refused by the `book_health` arm of `hard_block`; this rule
  must not invent a second, differently-worded refusal for the same condition.
- Switchable via `enforce_completable_pair_cost` so the rule can be measured on its own.

This does **not** replace `max_pair_cost`. The two ask different questions and both must
hold: `max_pair_cost` bounds the pair we are assembling from inventory we already own;
`max_completable_pair_cost` bounds the pair we would have to finish by crossing.

### R2 — Re-quote dead band

An order resting within `requote_dead_band` of the newly desired price is **kept**, not
cancelled and replaced.

- Default **0.03** (3c). Measured suppression on this run: 1c → 0/205 (0%), 2c → 74/205
  (36%), **3c → 98/205 (48%)**, 4c → 118/205 (58%), 5c → 139/205 (68%).
- 3c roughly doubles median order lifetime from 11.7 s to ~23 s. 2c leaves too much churn;
  4c and above hold a price the book has left behind, in a market whose mid moved 53c in
  30 minutes.
- Symmetric. The rule is about queue position, and queue position is lost in both
  directions.
- Independent of the existing `price_eps`, which exists for sub-tick venue rounding jitter.
  Both are tolerances for keeping an order, so the effective tolerance is the larger of the
  two; neither silently disables the other.

### R3 — Re-gate the orders the dead band keeps

An order held by R2 rests at its **own** price, up to `requote_dead_band` away from the
price R1 approved. It must be re-checked against R1 at its own price each cycle, and
cancelled when it no longer passes.

Without R3, the dead band is a hole in R1: a 3c-stale bid in a moving market can carry a
completable cost 3c worse than anything the gate ever approved. When R3 cancels a kept
order, the current cycle's intent for that token is submitted in its place, so the market
is re-quoted at a compliant price rather than left dark.

R3 is what makes R2 safe. They ship together.

## Non-goals

- **No taker state machine.** A drift-triggered second leg was considered and rejected:
  with `corr = -0.9989`, "one leg filled" and "the other leg's ask moved away" are the same
  event, so a drift detector degenerates into a timer with more code and less
  predictability. If a completion trigger is built later it gates on absolute pair cost
  (`filled_leg_avg + best_ask(other)`; cross at `<= 0.98`, rest to 1.00, abandon above),
  not on drift.
- **No TTL.** A resolution-aware TTL is the right shape, but `t_remaining` is NULL in all
  209 quote rows and `latency_ms` is unpopulated in all 209. The telemetry has to be fixed
  before the rule can exist.
- **No change to `single_buy_saver.py` or `unhedged_stop_loss.py`.** Both executed zero
  times this run. Changing untested paths on zero evidence is how the 0.995 cap got there.

## Decision rule after the next shadow run

Re-run the same 30 minutes with both rules on. If most of the 100 observed pairs fail the
R1 gate, the problem is price levels and no execution state machine will fix it. If most
pass, the state machine is worth building.

## Fill-model caveats that bound these conclusions

- Queue drains only on trades, not on cancels, so real fills come sooner for the 8 orders
  that saw volume. Changes nothing for the other 201. **The "no fills" verdict does not
  depend on this.**
- The tape carries no aggressor side, so all volume at a price was counted as ours. That
  makes the 3.8% counterfactual **optimistic**; the true ceiling is lower.
- Completions assume the best ask, instantly and in full. Never exercised — 0 fills.
- No venue latency, rejects, or partial cancels are modelled. 209 orders over 1806 s is a
  re-quote every ~9 s per side; real round-trips push effective uptime below the measured
  94.4%.
