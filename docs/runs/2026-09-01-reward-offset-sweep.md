# Sweep: `reward_offset`, how far from mid we rest, 2026-09-01

The experiment issue [#138](https://github.com/AI-Degen-69/spread-hunter-live/issues/138)
asked for. Everything below is measured from the six stores the sweep wrote and
from the console logs beside them — no estimates, no projections. Where a figure
could not be measured it says so.

Read [the shadow-06 trial](2026-09-01-shadow-06-trial88.md) first, and through it
[the shadow-05 baseline](2026-08-31-shadow-05-baseline.md). #88 closed with the
gates found innocent and one parameter left untested: the distance from mid at
which we rest. That is what this sweep varies.

**Result in one line: standing closer to mid did not buy fills — it bought a
queue roughly eighteen times deeper, and the nearest-to-mid setting the config
can express cannot place an order at all.**

---

## Setup

| | |
| --- | --- |
| Design | 6 blocks of 25 minutes, cycling control / A / B twice |
| Ran | 2026-09-01 01:34:47 to 04:02:56 local |
| Rotation interval | 5 s |
| Signer | none — `core_brain.shadow_run` builds a key-free, credential-free client |
| Gates | shipped bars throughout; no `HUNTER_*` gate variable was set |
| Universe | 5–6 markets per block: 3 MLB moneylines, 1–2 ATP/WTA, 1 long-dated MLB |

The three arms, and the two knobs each moves together:

| Arm | `HUNTER_REWARD_OFFSET` | `HUNTER_PRICE_RISK_WIDEN` | Blocks |
| --- | --- | --- | --- |
| control | unset (0.020 default) | unset (0.010 default) | 07, 10 |
| A | 0.010 | 0.005 | 08, 11 |
| B | 0.005 | 0.000 | 09, 12 |

| Block | Arm | Window | Store |
| --- | --- | --- | --- |
| 07 | control | 01:34:47–01:59:51 | `data/07_shadow_01-09_01-34.db` |
| 08 | A | 01:59:54–02:24:58 | `data/08_shadow_01-09_01-59.db` |
| 09 | B | 02:25:00–02:50:03 | `data/09_shadow_01-09_02-24.db` |
| 10 | control | 02:50:05–03:12:43 | `data/10_shadow_01-09_02-50.db` |
| 11 | A | 03:12:45–03:37:51 | `data/11_shadow_01-09_03-12.db` |
| 12 | B | 03:37:53–04:02:56 | `data/12_shadow_01-09_03-37.db` |

Interleaving was chosen over three contiguous 75-minute blocks specifically to
separate arm from hour, which is the confound that weakened #88. It did not
work, and the section on what this sweep does not establish says why.

Everything is a rehearsal. No order reached a venue and no money moved. The
dollar figures below are not earnings or losses.

## The prerequisite: markouts now exist

#88 closed with adverse selection unmeasured — `markouts` was empty in both
prior stores. The cause was structural, not a horizon that never matured:
`shadow_exec` credited a fill by writing a `fills` row and nothing else, and the
only sampler that matures a horizon, `markout.MarkoutWorker`, is started
exclusively by `core_brain.order_manager` on the live poll path. A rehearsal
opened no row and sampled none.

Both halves were closed before the sweep ran
([PR #139](https://github.com/AI-Degen-69/spread-hunter-live/pull/139)). This
sweep is the first run in the project's history to produce markout rows, and
they are reported below.

`markout_horizons` is `(300, 3600, 21600, 900)` — 5 minutes, 1 hour, 6 hours,
15 minutes. In a 25-minute block the 5-minute horizon matures for any fill, and
the 15-minute horizon for a fill booked in the first 10 minutes. **The 1-hour
and 6-hour horizons cannot mature inside a block and did not.** Long-horizon
adverse selection remains unmeasured.

## Headline result

Pooled per arm across both of its blocks:

| | control | A | B |
| --- | --- | --- | --- |
| Configured `reward_offset` | 0.020 | 0.010 | 0.005 |
| **Realised offset per leg (median)** | **0.0250** | **0.0150** | — |
| Orders placed | 110 | 154 | **0** |
| Quotes logged | 109 | 154 | 0 |
| Fills | 3 | 0 | 0 |
| Fill rate | 2.7% | 0.0% | — |
| Merged pairs | 1 | 0 | 0 |
| Cancels carrying queue data | 86 | 134 | 0 |
| **Median queue ahead (all cancels)** | **1,063** | **18,990** | — |
| Markout rows opened | 3 | 0 | 0 |
| Realized PnL (simulated) | +$0.12 | $0.00 | $0.00 |

## Arm B does not exist as a configuration

Arm B placed no order across its two blocks: 464 rotations, 2,320 market
visits, 2,320 declines. Every one was refused at the completable-pair-cost
gate, with the same shape of message on every market:

```
[SKIPPED] mlb-nym-tb-2026-08-31 | UP: completable pair 0.340+0.660=$1.0000
          >= $1.000 cap -- the second leg cannot be bought at a profit
[SKIPPED] mlb-sd-cin-2026-08-31 | UP: completable pair 0.480+0.520=$1.0000
          >= $1.000 cap -- the second leg cannot be bought at a profit
```

This is arithmetic, not a sample-size problem, and it does not depend on the
cap's value. `min_reward_offset = 0.005` (`core_brain/config.py:98`) floors the
resting offset, so 0.005 is the nearest-to-mid point the config can express.
Resting the floor on both legs means resting at mid on both legs, and the
shadow-06 measurement — `mid_UP + mid_DOWN` at exactly 1.0000 in the median,
confirmed again below — makes such a pair cost exactly $1.00. Zero edge. The
gate refuses it at `max_completable_pair_cost = 1.00`, and would refuse it just
as firmly at the `max_pair_cost = 0.99` both-maker cap.

**The floor and the book meet exactly at zero edge.** There is no configuration
between "some edge" and "resting at mid"; the floor *is* resting at mid.

## Queue depth: closer to mid stood in a deeper queue

Median shares that had to trade at our exact price before our order could fill,
at the moment we cancelled it:

| Cancel reason | control n | control median | A n | A median | Change |
| --- | --- | --- | --- | --- | --- |
| `not_quoted` | 30 | 1,214 | 76 | 18,990 | 15.6× deeper |
| `regate_pair_cost` | 34 | 1,025 | 46 | 20,278 | 19.8× deeper |
| `price_moved` | 22 | 1,313 | 12 | 6,188 | 4.7× deeper |
| **All cancels** | **86** | **1,063** | **134** | **18,990** | **17.9× deeper** |

Percentile spread, all cancels with queue data:

| | control | A |
| --- | --- | --- |
| p25 | 254 | 5,291 |
| p50 | 1,063 | 18,990 |
| p75 | 6,413 | 43,371 |

Unlike the #88 trial, where the p25 was unchanged and only the tail moved, here
the whole distribution shifts: arm A's p25 of 5,291 is five times control's
*median*. Moving one cent nearer mid moved us behind more resting size at every
percentile.

The direction is the opposite of the intuition #138 was built on, and it has a
plain mechanism: resting size concentrates near the touch. A price one cent
closer to mid is a price more of the book has already chosen.

## Realised price, and the book we quoted into

The resting price passes through `skew_offset`, `band_risk_factor`, the
`min_reward_offset` floor and a `best_bid + tick` cap
(`core_brain/quotes.py:108-134`), so the configured offset is not the realised
one. Measured from the quotes ledger:

| | control | A |
| --- | --- | --- |
| Quote observations | 109 | 154 |
| Realised offset per leg — p25 | 0.0250 | 0.0150 |
| Realised offset per leg — **median** | **0.0250** | **0.0150** |
| Realised offset per leg — p75 | 0.0300 | 0.0150 |
| Paired observations | 35 | 51 |
| **Pair cost (median)** | **0.9500** | **0.9700** |
| Pair cost min / max | 0.9300 / 0.9500 | 0.9700 / 0.9700 |
| `mid_UP + mid_DOWN` median | 1.0000 | 1.0000 |
| Edge demanded (1.00 − pair cost) | 0.0500 | 0.0300 |

Two things to read here.

The configured knobs landed where they were aimed: 0.020 gave a realised 0.025
per leg and a $0.95 pair, 0.010 gave a realised 0.015 and a $0.97 pair. Arm A's
offset is tighter than control's at every percentile — the p25, median and p75
are all 0.0150 — so the arms are cleanly separated in the thing they were meant
to vary.

And the shadow-06 mid-sum finding reproduces exactly, on a completely different
market family. Overnight MLB moneylines price the pair at $1.0000 at the median
just as US Open tennis did. Nothing about the sum being exactly fair was
particular to that slate.

## The fills, and the first markouts

Control booked three fills. All three are single legs; two of them were the two
legs of one market and were merged.

| Fill | Market | Leg price | Outcome |
| --- | --- | --- | --- |
| 1 | `0x6924…dc2c6` | 0.72 | single-leg safety exit at 0.71, −$0.06 |
| 2 | `0x2308…aac6f` | 0.24 | merged with fill 3 |
| 3 | `0x2308…aac6f` | 0.73 | merged with fill 2 |

The merge is the sweep's one complete trade: 6 shares at a pair cost of $0.97,
merged back at $1.00, **+$0.18 realized**. Net of the −$0.06 exit, control's
simulated PnL is +$0.12.

Note that the merged pair cost $0.97, not the $0.95 the median control quote
demanded. The pair that actually completed is the one where we accepted less
edge.

The three markout rows, the first this project has recorded. Drift is the
market's mid minus the price we paid, so positive is favourable to a buy:

| Fill price | mid at 5 min | drift | mid at 15 min | drift |
| --- | --- | --- | --- | --- |
| 0.72 | 0.785 | **+0.065** | 0.915 | **+0.195** |
| 0.24 | 0.205 | −0.035 | — | — |
| 0.73 | 0.795 | +0.065 | — | — |

Two observations, both with n small enough to name as anecdote rather than
result.

The two legs of the merged pair drift in opposite directions and their mids sum
to 0.205 + 0.795 = **1.0000** five minutes after the fills — the same identity
that holds before the fill. For a pair that will be merged, per-leg markout
carries no information: the pair is worth exactly $1.00 by construction, which
is the whole point of the strategy. **Markouts matter for single legs, not for
completed pairs.**

The single leg is where it does matter, and the one observation is unflattering.
We bought at 0.72, the safety path exited at 0.71 for −$0.06, and the mid was
0.785 five minutes later and 0.915 after fifteen. The exit gave up 19.5 cents of
subsequent move on that leg. One observation proves nothing about the exit
policy, but this is the measurement that was impossible before this sweep, and
it is pointed at the single-buy exit rather than at the entry.

## Conclusions

1. **#138 is answered, negatively, for the direction it proposed.** Moving one
   cent nearer mid took the median queue from 1,063 to 18,990 and produced zero
   fills against control's three. The trade the issue framed — fill rate rising
   by more than edge falls — did not occur; both moved the wrong way at once.
2. **The nearest-to-mid setting is not a setting.** At `reward_offset = 0.005`
   the quoter placed no order in 464 rotations, because the floor coincides
   exactly with a pair cost of $1.00. The parameter's usable range is bounded
   below by arithmetic, not by preference.
3. **Resting size concentrates near the touch.** Arm A's p25 exceeded control's
   median fivefold. This is a whole-distribution shift, which is a different and
   stronger signature than #88's tail-only move.
4. **The mid-sum identity is not slate-specific.** `mid_UP + mid_DOWN` = 1.0000
   at the median on overnight MLB moneylines, exactly as on US Open tennis. Two
   market families, two runs, same number.
5. **Adverse selection is measurable now, and it points at the exit.** The only
   informative markout in the sweep is the single leg the safety path closed at
   −$0.06 that was worth 19.5 cents more fifteen minutes later. Merged pairs
   cannot produce an informative markout at all.
6. **Neither #88's lever nor #138's lever is the answer.** Gates were measured
   and found innocent; distance from mid has now been measured and is worse in
   the direction proposed and unusable in the extreme. The fill rate is not
   controlled by either.

## What this sweep does NOT establish

- **The time confound was not eliminated, despite the design.** The interleaving
  was supposed to give each arm two separated windows. It did not: blocks 10 and
  11 each placed 10 orders that stayed resting for the whole block and produced
  **zero cancels**, so they contributed no queue observations at all. Every
  queue figure above comes from block 07 (control, 01:34–01:59) and block 08
  (arm A, 01:59–02:24) — two consecutive windows. The gap is 25 minutes on a
  near-identical market list rather than #88's three hours on a partly different
  one, which is tighter, but arm and hour are still not separated.
- **Why the late blocks went quiet is not established.** All 10 orders in each
  stayed `open` with no re-quote and no cancel across 179 rotations, which is
  consistent with books that stopped moving after ~02:50, but this sweep did not
  measure book update rates and cannot say so as fact.
- **Nothing about fill rates.** 3 fills against 0. Both numbers are noise, and
  the 2.7% against 0.0% in the headline table must not be read as a rate
  comparison.
- **Nothing about profitability.** One merged pair, +$0.18 simulated, against
  one exit at −$0.06.
- **Nothing about long-horizon adverse selection.** Only the 5-minute and
  15-minute horizons matured; the 1-hour and 6-hour horizons cannot mature
  inside a 25-minute block, and no row is `done`.
- **Nothing about intermediate offsets.** Three points were measured: 0.020,
  0.010, and an unplaceable 0.005. Nothing between 0.020 and 0.010 was tested,
  and nothing above 0.020 was tested at all — the sweep looked only toward mid.
- **Nothing about this universe versus the prior runs'.** These blocks quoted
  5–6 overnight MLB and tennis markets; shadow-05 and shadow-06 quoted 7 US Open
  tennis markets in the evening. The queue medians here (1,063 for control) are
  well below both prior runs (3,911 and 7,271) and that difference is not
  attributed.

## Reproducing the figures

Per-store counts and cancel distributions:

```powershell
python -m core_brain.cancel_report --db data/07_shadow_01-09_01-34.db
python -m core_brain.cancel_report --db data/08_shadow_01-09_01-59.db
```

Queue percentiles come from `orders.cancel_queue_ahead` grouped by
`orders.cancel_reason`. Realised offset per leg is `quotes.mid - quotes.price`.
The pair-cost and mid-sum tables pair `quotes.price` and `quotes.mid` for the
`UP` and `DOWN` sides of the same `condition_id` within the same second, the
same construction the shadow-06 write-up used. Markout drift is
`markouts.mid_h0` and `markouts.mid_h3` minus `markouts.fill_price`.

Arm B's refusal is in the console logs beside the stores, which are UTF-16:

```powershell
Select-String -Path runtime\sweep138\09_B.console.log -Pattern "completable pair" -Encoding unicode | Select-Object -First 3
```

The per-block manifest — arm, store, run id, environment variables and window
for each of the six blocks — is `runtime/sweep138/manifest.jsonl`.
