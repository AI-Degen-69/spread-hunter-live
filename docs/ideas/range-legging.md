# Range legging — measured, and it loses

## The idea

Assemble the pair across time instead of across the book. Buy UP at 0.47 when
price dips, buy DOWN at 0.47 when it recovers, merge for $1.00. The pair does
not need to be cheap at one instant; the edge is the oscillation amplitude
rather than the bid-ask spread:

    pair cost = p_low + (1 - p_high) = 1 - (p_high - p_low)
    edge      = p_high - p_low

## Why it looked good

70 live markets, 24h of 1-minute price history. Rest a bid at anchor - w/2 and
an offer at anchor + w/2, re-anchor after each completed pair:

| band | legs opened | pairs completed | median leg1 to leg2 | gross from pairs |
|---|---|---|---|---|
| 4c | 82 | 48 (59%) | 88 min | +$1.92 / day |
| 6c | 59 | 29 (49%) | 95 min | +$1.74 / day |
| 10c | 42 | 15 (36%) | 89 min | +$1.50 / day |

The oscillations are real. 16 of 70 markets completed at least one pair a day
at the 6c band.

## Why it loses

That table marks every stranded leg at **zero**, which is not a result, it is a
missing number. Scoring them requires markets whose outcome is known.

60 **resolved** markets over $20k volume, tapes rebuilt at 1-minute fidelity
via `startTs`/`endTs` (`interval=max` silently ignores `fidelity`), 484,122
points. Pairs earn w; a leg closed on a timeout is marked at market minus a 2c
cross; a leg still open when price leaves [0.03, 0.97] is scored against the
real resolution:

| band | exit | pairs | timeouts | stranded | pair $ | timeout $ | strand $ | **NET** |
|---|---|---|---|---|---|---|---|---|
| 2c | 60m | 579 | 1262 | 36 | +11.58 | -52.71 | -5.79 | **-46.92** |
| 2c | 240m | 399 | 500 | 37 | +7.98 | -28.59 | -4.60 | **-25.21** |
| 2c | hold | 129 | 0 | 48 | +2.58 | 0 | -15.49 | **-12.91** |
| 4c | 60m | 202 | 829 | 27 | +8.08 | -37.63 | -4.40 | **-33.95** |
| 4c | 240m | 178 | 380 | 34 | +7.12 | -23.07 | -4.20 | **-20.15** |
| 4c | hold | 89 | 0 | 47 | +3.56 | 0 | -14.55 | **-10.99** |
| 6c | 60m | 107 | 628 | 27 | +6.42 | -29.94 | -3.81 | **-27.33** |
| 6c | 240m | 80 | 310 | 33 | +4.80 | -18.98 | -4.62 | **-18.80** |
| 6c | hold | 68 | 0 | 44 | +4.08 | 0 | -13.54 | **-9.46** |
| 10c | 60m | 32 | 387 | 20 | +3.20 | -18.59 | -4.07 | **-19.46** |
| 10c | 240m | 39 | 204 | 30 | +3.90 | -13.44 | -4.71 | **-14.25** |
| 10c | hold | 45 | 0 | 39 | +4.50 | 0 | -12.48 | **-7.98** |

**Twelve of twelve configurations lose.** 20% of markets are individually
positive, median -0.084 per market. At an earlier 10-minute pass the highest
scoring markets had completed *zero* pairs and won purely on a stranded leg
landing right, so the winners are not the strategy working.

## The arithmetic that kills it

At the 6c band, holding to resolution:

    pair       +$0.06 each,  68 of them
    strand     -$0.31 each,  44 of them
    break-even needs 5.1 pairs per strand; reality delivers 1.5

Stranded legs win 14% of the time. That is not a fair bet, it is adverse
selection: a leg strands **precisely because** price left and never came back,
which means the market trended to its resolution against the entry. Resting a
passive bid makes you the counterparty to informed flow, and the flow is right.

Both exits are closed. Cutting early realises the adverse move (worse at every
horizon from 60 to 240 minutes). Holding to resolution pays the full 31c.

## Same failure as the spread version

`core_brain/pair_scanner.py` already records that `edge_per_pair` IS the
bid-ask spread. This measurement adds the matching fact for the range version:
both are market-making edges, and on this venue adverse selection is roughly
3-5x the edge either way. Changing the selection metric from spread to
volatility does not change the sign.

## The backtest is optimistic, not pessimistic

Every fill is assumed to happen the moment price touches our level — no queue,
no partial fills. Real quoting fills *less* often on the profitable side (we
wait behind resting size) and just as often on the adverse side (informed flow
comes to us). Live results would be worse than the table above.

## Not doing

- **Chasing volatile markets.** The oscillation is real and still unprofitable.
- **Timeouts or stop-losses on the naked leg.** Negative at every horizon tested.
- **Ranking the scanner on realised range.** The metric predicts pairs/day
  correctly and pairs/day is not the number that decides profit.


---

# Follow-up: is there any exit? Four hypotheses, then a drift test

## Four attempts to rescue the strategy

Tested on 92 resolved markets, 750,299 1-minute points, full accounting:

| hypothesis | rule | best net |
|---|---|---|
| wider band | 6c -> 40c | -10.21 -> -4.50, approaches zero **from below**, never crosses |
| mid-book only | open only while 0.35 <= p <= 0.65 | -10.03 |
| quiet regime only | open only if the last 60min ranged < 1c | -10.28 (**worse**) |
| momentum | buy the side price moves toward | +3.29 at a 20c trigger, 71% win rate |

Only the sign flip helped, and its sample is 51 positions with binary payoffs:
mean +6.4c, sd 0.43, **t = 1.07**. That is noise, not an edge.

## The drift test — the decisive one

Scoring on resolution caps the sample at one observation per market. Scoring on
price *continuation* does not. For every moment where price had moved >= w over
a lookback, the signed forward return over a horizon was recorded, sampled
**non-overlapping** so the t-statistic is not inflated by autocorrelation:

    signed_return = sign(move) * (p[t+H] - p[t])

| trigger | lookback -> horizon | n | mean | t | net of 2c |
|---|---|---|---|---|---|
| 3c | 60m -> 60m | 1264 | -0.094c | -0.46 | -2.09c |
| 3c | 240m -> 60m | 2069 | -0.105c | -0.83 | -2.10c |
| 5c | 240m -> 60m | 1288 | +0.021c | +0.11 | -1.98c |
| 10c | 240m -> 60m | 514 | +0.001c | +0.00 | -2.00c |
| 10c | 60m -> 240m | 138 | +3.316c | **+2.46** | +1.32c |
| 5c | 240m -> 1440m | 125 | +2.870c | +1.91 | +0.87c |
| 3c | 240m -> 1440m | 170 | +2.336c | +1.89 | +0.34c |

Full grid: 18 configurations, 17 read "no signal".

## What it means

1. **Mean reversion does not exist here.** Every reversion estimate is within
   0.1c of zero at t between -0.3 and -0.8. The backtest loss was not bad luck
   and no filter could have fixed it — there is simply nothing to harvest.
2. **At a 60-minute horizon the price is a martingale to three decimals**
   (-0.094c, -0.105c, +0.021c, +0.001c; every t under 1). Prices are efficient
   at exactly the horizons where trading is cheap.
3. **The one significant cell is multiple testing.** With 18 tests, p<0.05 needs
   |t| > 3.0 after Bonferroni; the best cell reads 2.46 on n=138.
4. **The 2c crossing cost exceeds every drift estimate but the longest-horizon
   ones.** Net of cost, 12 of 18 configurations are negative.

## The standing conclusion for this repo

Passive two-sided quoting on Polymarket binaries has now been measured to fail
on three independent axes:

* **spread** — `edge_per_pair` IS the bid-ask spread (`pair_scanner.py`), and
  the wide-spread markets do not fill;
* **dislocation** — impossible by construction, one book serves both token ids;
* **range / volatility** — measured above: no reversion, and adverse selection
  runs 3-5x the edge.

The common cause is that the price is efficient at tradeable horizons. Changing
which markets are selected does not change that, so no scanner metric fixes it.

## The only lead the data leaves open

Long-horizon drift is the one consistently positive, consistently
non-significant reading (+2.3c to +3.4c at a 1440m hold, t ~ 1.9 across three
independent cells). It cannot be resolved from history: `/prices-history`
serves usable tapes for only 92 of 925 resolved markets, so the sample is
capped by the venue, not by effort.

Settling it needs forward-collected data. Recording our own minute tape from
now on costs nothing and risks nothing, and in 4-8 weeks would give the sample
that history cannot.

## How the open question gets settled

`core_brain/price_tape.py` records the forward tape this analysis could not
obtain, for exactly the reason named above: the venue serves a dense tape for a
live market and stops serving one after it resolves, so the sample has to be
captured before the market closes and scored after.

    python -m core_brain.price_tape --once      # one recording pass
    python -m core_brain.price_tape             # keep recording, every 30 min
    python -m core_brain.price_tape --status    # markets, resolutions, ticks

It reads two public endpoints, writes only `data/price_tape.db`, and places no
orders. Once a few hundred recorded markets have resolved, re-run the drift
test against that store instead of `/prices-history`: the estimator is the same,
only the sample changes.

The bar to clear is stated in advance so the answer cannot be fitted after the
fact: **|t| > 3.0**, which is p<0.05 after Bonferroni across the 18-cell grid.
Anything under that is the same noise this document already records.

---

# Addendum: the drift grid re-run on 1,962 market-days

The first grid ran on 552 market-days because `/prices-history` was only asked
for a few hours at a time. It serves far more than that for a market that is
still open: 20 live markets held 438 market-days of minute tape already
available on 2026-09-07, most long-dated ones serving the full 30 days.
`core_brain/price_tape.py` now walks the request back a day at a time, and one
seeding pass collected **103 markets, 2,825,483 minute points, 1,962
market-days** -- 3.5x the original sample.

## Every cell reversed sign

Same estimator, non-overlapping, on markets that are still **open**:

| trigger | lookback -> horizon | n | mean | t |
|---|---|---|---|---|
| 3c | 60m -> 60m | 1401 | -0.711c | **-3.33** |
| 3c | 240m -> 60m | 2560 | -0.215c | -1.99 |
| 5c | 60m -> 60m | 730 | -1.186c | -2.99 |
| 5c | 60m -> 1440m | 167 | -3.949c | **-3.24** |
| 5c | 240m -> 1440m | 196 | -2.102c | -2.20 |
| 10c | 60m -> 1440m | 87 | -4.256c | -2.55 |

**All 18 cells negative.** Two clear the pre-registered |t| > 3.0 bar. Mean
reversion is real, measurable, and present in live markets -- the opposite of
what the resolved-market grid said.

## Which is the same estimator on the same venue

| cell | live markets | resolved markets |
|---|---|---|
| 3c 60m -> 60m | -0.094c... **-0.711c**, t=-3.33 | -0.094c, t=-0.46 |
| 5c 60m -> 1440m | **-3.949c**, t=-3.24 | **+2.600c**, t=+1.49 |
| 5c 240m -> 1440m | **-2.102c**, t=-2.20 | **+2.870c**, t=+1.91 |

Identical code, opposite signs. The only difference between the two samples is
whether the market's ending is inside it.

## What that means, and why it is not a green light

The live sample is every top-volume market that is **still open today**. It
therefore excludes, by construction, every market that ended during the window
— which is exactly where the terminal convergence to 0 or 1 lives. Measuring
reversion on markets that have not yet made their terminal move and calling it
an edge is survivorship, and the resolved-market backtest already priced what
that move costs: **-31c per stranded leg, won 14% of the time.**

So the two results are not in conflict. Short-horizon reversion exists at
roughly 0.7c to 4c. The terminal move costs 31c. Fading the move earns the
first and eventually pays the second, which is precisely the -$9.46 the full
accounting measured.

Cost closes what is left. Fading the strongest **significant** cell earns
3.9c gross against a 2c cross, and the strongest short-horizon cell earns 0.7c
against the same 2c. One of eighteen cells is both significant and net-positive
after cost, in the sample that is biased toward saying so.

A second explanation is not excluded: the two samples also differ in
composition, the live one holding long-dated politics and crypto while the
resolved one holds short-lived events. Nothing here separates survivorship from
composition.

## The test that would separate them

Re-run this grid on these same 103 markets **after they resolve**. Same tapes,
same estimator, terminal move now included. If the sign flips back to the
resolved-market reading, it was survivorship; if it holds negative, the
reversion is real and composition explained the earlier grid.

That one does need weeks, and it is the reason the recorder keeps running. The
bar stays where it was set: **|t| > 3.0**.

---

# Settled: it was survivorship, and it took minutes, not weeks

The addendum above said separating survivorship from composition needed the 103
recorded markets to resolve first. That was wrong. The counterfactual can be
built from markets that have **already** resolved, by deleting their endings.

Take the 92 resolved tapes -- endings known -- and cut the last N days off each.
That manufactures a "still open" sample out of markets whose outcome is in hand.
Composition is held fixed by construction: same markets, same venue, same
window. Only the ending moves.

## Pooled across five cells, 92 markets

| tape | n | mean | t |
|---|---|---|---|
| full, ending included | 1705 | **+0.712c** | **+2.75** |
| last 1 day cut | 1328 | -0.135c | -0.63 |
| last 2 days cut | 1043 | -0.380c | -1.88 |
| last 3 days cut | 738 | **-0.671c** | **-3.30** |

Monotonic in how much of the ending is removed, and the sign flips. Nothing
about the markets changed; only whether their last three days are in the sample.

## The number that closes it

    real live-market sample        -0.711c   t = -3.33
    resolved markets, ending cut   -0.671c   t = -3.30

The live sample behaves exactly like a resolved sample with its ending
amputated, because that is what it is. Every market in it still has its
terminal move ahead of it.

The sharpest cell makes the mechanism visible. `3c, 60m -> 60m` reads -0.09c at
t=-0.46 on full tapes -- nothing at all -- and -0.90c at **t=-6.17** once three
days are cut. Deleting the ending does not merely weaken the momentum reading,
it **manufactures** a highly significant reversion reading out of noise.

## The standing trap

**Any backtest run on live, unresolved prediction markets is biased toward
mean reversion, and the bias is large enough to invent significance that is not
there.** A binary market's price must end at 0 or 1; a sample of markets that
have not ended is a sample with that move systematically excluded. The bias is
not a small correction — here it moved a pooled estimate by 1.4c and flipped
its sign.

Any future strategy measured on open markets in this repo has to be re-measured
on resolved ones before its numbers mean anything. The reversion is real in the
sample and unbankable in life, because every market eventually joins the other
sample.

## What this does not need

It does not need the recorder to wait for anything. `core_brain/price_tape.py`
is still worth running -- a bigger resolved sample sharpens every future test --
but the survivorship question is closed, on data already in hand.
