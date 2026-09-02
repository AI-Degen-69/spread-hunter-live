# Run 153: the companion leg arrives in a second, or never — 2026-09-02

[Run 151](2026-09-01-run151-touch-pair-and-the-exit-floor.md) found the exit path
had never been measured, fixed three harness defects, and left one knob newly
reachable: `single_buy_grace_sec`, which `shadow_run` had been pinning to 0.0 so
that every stranded leg was dumped on the first poll after its fill. Its closing
recommendation was to sweep that knob.

This run sweeps it.

**Result in one line: the second leg arrives within 1.5 seconds or it never
arrives at all, so every grace value from 5s to 120s rescues exactly the same
pairs — and each extra second only degrades the exit on the ones it cannot
rescue.**

---

## Setup

| | |
| --- | --- |
| Ran | 2026-09-02, 122 minutes (stopped early; the answer did not need the rest) |
| Universe | 13 markets seen, 5 at a time — US Open in progress, the widest slate this investigation has had |
| Signer | none; `core_brain.shadow_run` builds a client that cannot sign |

```
HUNTER_REWARD_OFFSET=0.005       rest at the touch
HUNTER_PRICE_RISK_WIDEN=0        stop the widen term pushing us off it
HUNTER_COMPLETABLE_CAP=0         disable the taker-completion gate
HUNTER_PAIR_COST_CAP=0.995       permit the $0.99 touch pair (#150)
HUNTER_SINGLE_BUY_GRACE_SEC=120  hold at the MAXIMUM
```

| Store | |
| --- | --- |
| `data/16_shadow_grace120.db` | 634 quotes, 17 fills, 13 closes |
| `data/16_booktape_grace.db` | 3,453 book samples across 13 markets |

## Why one arm instead of four

A four-way sweep would have split 13 closes into arms of three, and reproduced
the confound the [#138 sweep](2026-09-01-reward-offset-sweep.md) could not
remove — its own conclusions section records that arm and hour were never
separated.

A longer grace **contains** the shorter ones. If the companion fills 30s after
the first leg, then 45 and 120 both caught it and 15 did not. So the run is held
at the maximum and every shorter value is recovered as a filter on the recorded
distribution — one arm, no splitting, no confound, and the exit side repriced
from a book recorder covering the same window rather than modelled.

## The answer

```
COMPANION WAIT -- how long the second leg took
  paired at all : 4/13
  wait seconds  : min 0.9  median 1.5  max 1.5
```

Four of thirteen legs ever saw their companion. All four arrived **inside 1.5
seconds**. Nothing arrived later — the distribution has no tail at all.

| grace | rescued | rate | merge $ | exit $ | net $ |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 5s (floor) | 4 | 30.8% | +0.2200 | +0.0142 | **+0.2342** |
| 15s | 4 | 30.8% | +0.2200 | +0.0642 | **+0.2842** |
| 45s | 4 | 30.8% | +0.2200 | −0.1658 | +0.0542 |
| 120s | 4 | 30.8% | +0.2200 | −0.1808 | +0.0392 |

**The rescue column does not move.** Not by one pair, across a twenty-four-fold
range of grace. Waiting longer rescues nothing and costs 0.4c per share on the
nine legs it fails to rescue.

The 5s row is the floor rather than zero, and that distinction matters. The
routing exits on the first poll AFTER the grace expires, and the rotation is 5
seconds — so a companion that arrives 0.9s after its first leg is already there
when the poll looks. A `grace=0` row would read as "dump instantly" and
recommend against a setting the engine cannot make.

## What this means for the knob

Set it small. Anywhere in 5–15s captures every rescue this run saw; past that it
is pure cost. The shipped default of 0.0 is very nearly right, and is right in
practice, because the poll cadence floors it at 5s regardless.

The gap between the 5s and 15s rows (+0.0142 against +0.0642) is exit-price
movement on nine legs and should not be read as a preference between them.

## What the run also recorded

| | |
| --- | --- |
| Merges | 4, `pair_cost` 0.89 / 0.947 / 0.98 / 0.99 | 
| Merge PnL | +$1.128 |
| Single-leg exits | 9, −$0.4758 |
| **Run net as booked** | **+$0.6522** |

A positive run, and the first in this investigation. Two things changed from run
151: the slate was 5 markets rather than 3, and run 151's exit-price fix is in
force, so a stranded leg costs what the book charges rather than a flat 2c
harness floor.

## The reporting bug this run surfaced

`scripts/grace_sweep_report.py` returned "nothing to sweep" on its first read,
then, once joined correctly, reported a pair of **UP 0.42 + DOWN 0.73 = $1.15** —
over the dollar the instrument pays, and impossible, because `risk.hard_block`
refuses exactly that.

The gate had not failed. All four merges carry a `pair_cost` at or under 0.99.
The report had reconstructed pairing by matching the next opposite-side fill in
the same market, and a market carrying several pairs at once married legs the
engine never grouped. The registry records `orders.pair_id` on every order; the
fix is to read it rather than to second-guess it.

Three smaller defects preceded it, all silent: `fills.order_uuid` holds
`orders.id` rather than `orders.order_id`, so the first join matched zero rows;
`orders.side` is always `BUY`, with the UP/DOWN leg carried by the token; and
`fills.recorded_ts` is in milliseconds while the book recorder's `ts` is in
seconds, so a 1.4-second wait read as 1,400.

Each of these produced a plausible-looking wrong answer rather than an error.
That is the argument for reading an interim rather than collecting for three
hours and trusting the total.

## What this run does NOT establish

- **Nothing about why the companion is instant or absent.** Both legs are posted
  in the same cycle, so a market that moves through both touches at once fills
  both; one that moves through one touch fills one. That is a plausible
  mechanism and this run did not test it.
- **Nothing about the 30.8% rate itself.** Thirteen legs. The rate is the thing
  the strategy lives or dies on and this is not a measurement of it.
- **Nothing about longer horizons than 120s.** `pairs_exit_window_sec` caps grace
  at 900s and nothing above 120s was held.
- **Nothing about profitability.** +$0.6522 over 122 minutes on five markets is
  one draw, in a US Open slate that is not typical of the universe this filter
  usually returns (1–2 markets).
- **Nothing about the exit-price differences between rows.** Nine legs.

## Reproducing

```powershell
python -m scripts.grace_sweep_report --db data\16_shadow_grace120.db --book-db data\16_booktape_grace.db
```

The sweep reads `orders.pair_id` for pairing, `quotes.token_id` for the UP/DOWN
leg, and prices each horizon's exit at the first book sample at or after it —
never before, which would price an exit on information it did not have.
