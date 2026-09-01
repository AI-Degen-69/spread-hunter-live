# Run 145: where the tape actually prints, 2026-09-01

The experiment issue [#145](https://github.com/AI-Degen-69/spread-hunter-live/issues/145)
asked two questions the `reward_offset` sweep could not answer: how much volume a
resting bid at a given distance from mid can ever be reached by, and what the
cheapest pair two resting bids can assemble actually costs. Everything below is
measured from the three stores this run wrote. Where a figure could not be
measured it says so.

Read [the reward_offset sweep](2026-09-01-reward-offset-sweep.md) first. It
closed by recording that neither its lever nor #88's moved the fill rate, and
that nothing above an offset of 0.020 had been tested. This run does not test a
third offset. It measures the venue instead, which answers every offset at once.

**Result in one line: essentially all tape prints within one tick of mid, and
nothing at all printed two or more ticks below it — which is exactly where the
quoter rests.**

---

## Setup

| | |
| --- | --- |
| Ran | 2026-09-01 07:51 to 09:06 local |
| Signer | none — the recorder builds no client that can sign, and `data/orders.db` is refused by name |
| Rehearsal | `core_brain.shadow_run`, 5 s rotation, on the fleet's own universe |
| Recorders | `scripts.book_tape_recorder`, one on the fleet universe and one on a high-volume universe |

Three stores:

| Store | What it holds |
| --- | --- |
| `data/13_shadow_01-09_07-51.db` | the rehearsal's decision path: 8 orders, 8 quotes, 0 fills |
| `data/13_booktape_01-09_07-51.db` | book and tape for the fleet's universe: 31 samples, 0 live tape |
| `data/13_booktape_wide_01-09_08-04.db` | book and tape for 14 high-volume markets: 1,828 samples, 145 live tape rows |

The wide universe exists because the fleet's did not survive contact. See
"The universe emptied" below.

No order reached a venue and no money moved.

## The measurement, and the one thing it cannot separate

`recent_trades` de-duplicates against a `seen` set that starts empty, so a
market's first pass returns the venue's whole recent window — hundreds of
trades printed over hours — and every one gets stamped against the mid read at
that moment. The first live pass produced buckets spanning 27 ticks either side
of mid on a market whose spread was one tick. Those 723 rows are flagged
`is_bootstrap` and excluded from every figure below; the 145 live rows are the
result.

**What the bucketing cannot do is separate a print at the bid from one at the
ask when the spread is one tick.** On such a book mid sits exactly between two
lattice points, so both are half a tick away and the rounding is decided by
float noise. Every market in this run had a one-tick spread. The consequence
is that the split between the `0` and `+1` buckets is arbitrary; the split
between "at or below mid" and "above mid" is not, and that is the line the
conclusions rest on.

## Reachability: the tape does not go where we quote

A resting BUY can only ever be reached by volume printing at or below mid.
Pooled across the 8 high-volume markets that stayed readable, 66,853 shares
over 63 prints:

| Distance from mid | Volume | Share of tape | Reaches a bid resting here |
| --- | --- | --- | --- |
| above mid | 43,957.3 | 65.75% | — |
| at mid | 17,900.7 | 26.78% | 34.25% |
| 1 tick below | 4,995.5 | 7.47% | 7.47% |
| **2 ticks below** | **0.0** | **0.00%** | **0.00%** |
| **3 ticks below** | **0.0** | **0.00%** | **0.00%** |
| **4 or more ticks below** | **0.0** | **0.00%** | **0.00%** |

The last column is a tail sum — everything at that distance or deeper — because
a resting bid is reached only by prints at or past its own price. A print
nearer mid filled a better-priced bid.

Split by tick size, because a tick means two different things here:

| | 1c-tick markets | 0.001-tick markets |
| --- | --- | --- |
| Volume | 46,748 | 20,106 |
| Prints | 43 | 20 |
| Above mid | 89.48% | 10.57% |
| At mid | 0.13% | 88.74% |
| 1 tick below | 10.39% | 0.69% |
| **2+ ticks below** | **0.00%** | **0.00%** |
| **Reachable by a bid at mid** | **10.52%** | **89.43%** |
| **Reachable by a bid at the touch** | **10.39%** | **0.69%** |

The bid/ask asymmetry between the two columns is the rounding limitation
above, not a finding — on a one-tick book the `0` bucket holds whichever side
float noise put there. What survives it is the last row of each column and the
zero above it.

**The quoter rests at `mid - reward_offset`.** At the shipped 0.020, that is
two to three ticks under mid on a 1c book and twenty to thirty-five ticks under
on a 0.001 book. Every quote this run's rehearsal posted realised exactly
0.025 from mid — 2.5 ticks. Measured volume at that distance, across 66,853
shares: **zero**.

This is the mechanism behind three runs of near-zero fills. It is not queue
position, it is not the gates, and it is not the hour. Nothing trades there.

## `spread_capture_frac` is wrong by roughly two orders of magnitude

`scoring/config.py:326` sets `spread_capture_frac = 0.25`, and that constant
produces the `est_income` and `return_pct_day` columns the market filter ranks
on — the figures that put `mlb-bal-col` at 11.65%/day.

Reachability is a **tail**: a bid resting `k` ticks under mid is reached only
by prints at `k` or deeper, because a print nearer mid filled a better-priced
bid and never came to us. Measured that way, pooled:

| Bid resting at | Reachable share of all tape |
| --- | --- |
| mid | 34.25% |
| 1 tick under mid | 7.47% |
| **2 ticks under mid** | **0.00%** |
| 3 or more ticks under | 0.00% |

And per tick regime:

| Bid resting at | 1c-tick markets | 0.001-tick markets |
| --- | --- | --- |
| mid | 10.52% | 89.43% |
| 1 tick under (the touch) | 10.39% | 0.69% |
| **2 ticks under** | **0.00%** | **0.00%** |

The best case anywhere is resting at mid, where the pair costs $1.0000 and
there is no edge to capture at all. At the touch on a 1c market — the deepest
place any edge exists — it is 10.39%, against the asserted 25%.

At the offsets the quoter actually uses, the measured reachable fraction is
**0.00%**. The assumption is not merely optimistic; at the shipped
configuration the quantity it estimates is zero.

This is an upper bound in a second sense too: reaching a price level is
necessary for a fill, not sufficient. Clearing the queue ahead is the other
condition, and this run does not model it.

## The touch pair, on 1,247 samples

`best_bid(UP) + best_bid(DOWN)` is the cheapest pair two resting bids can
assemble — the floor under every offset, not the price we post at.

| Touch pair cost | Samples | Share | Against `max_pair_cost = 0.99` |
| --- | --- | --- | --- |
| $0.9900 | 467 | 37.45% | refused (`>=`) |
| $0.9990 | 780 | 62.55% | refused (`>=`) |
| | **1,247** | **100.00%** | **all refused** |

$0.9900 is the 1c-tick markets, $0.9990 the 0.001-tick ones. **A finer tick
buys less edge, not more**, and the finest-tick markets here are also the
deepest — 557,097 shares resting at the UP touch on the Fed decrease market,
219,669 on the DOWN.

Every sample in the run sits at or above the cap. Not most: all of them.

## The mid-sum identity holds everywhere

`mid(UP) + mid(DOWN)` = **1.0000 in 1,247 of 1,247 samples — 100.00%**.

The markets: MLB, Premier League, UEFA, ATP and WTA tennis, three Fed rate
decisions, the Russian legislative election, the Brazilian presidential
election. Shadow-05 and shadow-06 saw this on US Open tennis, the #138 sweep
saw it on overnight MLB, and both wrote it down as a per-slate observation.
It is not a slate property. It is what the venue enforces, and it means the
pair has no intrinsic edge at mid on any market this project has looked at.

The one exception in the whole run came from the other store, and it is worth
recording: `mlb-bal-col` was sampled ten times while its spread widened to
2.7c/2.9c, and in that window the touch pair printed $0.85 and $0.96, with one
mid-sum of 1.0500. **Wide books are where the edge is.** They are also what
`quotes` refuses: the rehearsal's own log shows
`hedge token DOWN not tradeable (book too wide 13.0c > 6.0c)`. Ten samples is
an anecdote, and it is pointed at the gate rather than at the offset.

## The universe emptied

The fleet's market list went to `[]` during the run and stayed there. The
filter's own census, from `runtime/rerank.log`:

```
scored 105, rejected 105 (NO: top-3 bid depth=5, YES spread=9,
YES: top-3 bid depth=55, carries a submarket group label=1, horizon=1,
income=1, pre-start=1, volume=32), wrote top 0 -> runtime/markets.json
gates: primary/main-line only, blocked submarkets/live; 24h volume >= $125,000,
YES+NO top-3 bid depth >= $500 each, spread <= 0.06, resolves within 30d,
income >= $1.50/day
```

Sixty of the 105 rejections are the **top-3 bid depth floor of $500**, which is
a requirement that the book be *thick*. Thickness is the same property that
put the #138 sweep's orders behind a median of 1,063 to 18,990 shares. The
selector does not neglect queue depth — it mandates that it be large.

Nine more are the **spread ceiling of 6c**, which rejects the wide books the
paragraph above identifies as the only place edge appeared.

The rehearsal that ran through this held one market, `mlb-bal-col`, which then
closed underneath it. It posted 8 orders, every one at a realised 0.025 from
mid, and booked 0 fills before its market went away. Its cancel reasons were
`not_quoted` (4), `regate_pair_cost` (3) and `price_moved` (1).

## Conclusions

1. **The offset knob is bounded by arithmetic on both sides, and #145 closes
   it.** Toward mid, the touch pair is $0.99 or $0.999 in 100% of samples and
   the cap refuses all of them. Away from mid, measured tape volume beyond one
   tick under mid is zero across 66,853 shares. There is no offset that both
   clears the cap and reaches the tape.
2. **`spread_capture_frac = 0.25` should be treated as unfounded.** The
   measured upper bound is 34.25% for a bid resting at mid, 10.39% at the
   touch on 1c markets, and 0.00% at the shipped offset. Every `est_income`
   and `return_pct_day` in the filter inherits the error.
3. **`mid(UP) + mid(DOWN)` = 1.0000 is a venue property, not a slate
   observation.** 1,247 of 1,247 samples across eight market families.
4. **A finer tick is worse, not better.** The 0.001-tick markets price the
   touch pair at $0.9990 — a tenth of a cent — and carry the deepest books.
5. **The two filter gates that reject the most both point away from edge.**
   The $500 depth floor requires the thickness that buries our queue position;
   the 6c spread ceiling rejects the width where the only sub-$0.99 touch pair
   of the run appeared.

## What this run does NOT establish

- **Nothing about fills.** The rehearsal booked zero, on one market, over the
  minutes before that market closed. That is not a fill rate.
- **The bid/ask split within one tick of mid.** Every market had a one-tick
  spread, so mid sat at a half-tick and the rounding between the `0` and `+1`
  buckets is float noise. Only the at-or-below-mid aggregate is sound.
- **Nothing about the wide-book regime.** The $0.85 and $0.96 touch pairs come
  from ten samples on one market. They are the reason to run the next
  experiment, not a result.
- **Nothing about whether a $0.99 pair is profitable.** One cent of gross edge
  against merge gas was not measured, and the cap decides it today by a `>=`
  comparison rather than by measurement.
- **Nothing about the queue.** Reaching a price level is necessary for a fill,
  not sufficient. The reachable fractions above are upper bounds that ignore
  the size resting ahead of us.
- **Nothing about other hours.** 07:51 to 09:06 local on a Sunday morning, with
  the filter's eligible universe at zero for most of it. The tape figures come
  from 63 prints; the direction is unambiguous but the percentages are not
  precise.
- **Nothing about long-horizon adverse selection.** No fill was booked, so no
  markout row was opened.

## Reproducing the figures

```powershell
python -m scripts.booktape_report --db data\13_booktape_wide_01-09_08-04.db
python -m scripts.booktape_report --db data\13_booktape_01-09_07-51.db
```

Reachability is `tape_buckets.volume` grouped by `ticks_from_mid` with
`is_bootstrap = 0`. The tick-size split joins `tape_buckets` to the distinct
`(condition_id, tick)` pairs in `book_samples`. The touch-pair and mid-sum
tables group `book_samples.touch_pair_cost` and `book_samples.mid_sum`. The
rehearsal's quotes and cancel reasons are `quotes` and `orders` in
`data/13_shadow_01-09_07-51.db`.

The high-volume market list the wide recorder followed is local, under
`runtime/`, which this repo does not publish. It was built by taking every
active market over $50,000 of 24h volume, top 14 by volume:

```powershell
python -c "import json,requests; rows=requests.get('https://gamma-api.polymarket.com/markets', params={'active':'true','closed':'false','order':'volume24hr','ascending':'false','limit':60}, timeout=30).json(); out=[{'cid':m['conditionId'],'slug':m.get('slug',''),'tick':float(m.get('orderPriceMinTickSize') or 0.01)} for m in rows if m.get('conditionId') and float(m.get('volume24hr') or 0)>=50000][:14]; json.dump(out, open('runtime/run145/wide_universe.json','w'), indent=1)"
```

The list is volume-ranked at the moment it runs, so a later rebuild will not
reproduce these eight markets. The `condition_id` of every market actually
sampled is in `book_samples` in the store, which is the durable record.
Console logs for all three processes are in `runtime/run145/`.
