# The Maker-Queue Selection Bar — Design

**Status:** proposed, 2026-08-25
**Evidence:** `data/shadow.db`, session B of `run-2809a7161de1` (2026-08-25 18:36–19:05 UTC,
30 min, 344 quotes, 10 markets, **0 fills**)

## The measurement that forces this

Session B rested 344 orders across 10 markets for 30 minutes and filled nothing. The
reason is not price and it is not patience:

| edge under mid | quotes | saw volume at their price | hit rate |
| --- | --- | --- | --- |
| 1.5–2.5c | 135 | 8 | 5.9% |
| 2.5–3.5c | 178 | 6 | 3.4% |
| 3.5–5.0c | 31 | 2 | 6.5% |

Flat. Quoting nearer the touch did not improve reach, so "quote closer" is refuted.

What did bind:

- median `queue_ahead` at post: **15,638 shares**
- total volume that reached *any* quoted level in 30 min: **1,724 shares**
- orders whose queue fully drained: **0 of 344**

Not one order in the session reached the front of its queue. Expressed per market, as
minutes of that market's own observed volume needed to clear the median queue:

| market | quotes | median queue | shares/min at our levels | minutes to clear |
| --- | ---: | ---: | ---: | ---: |
| atp-bonzi-halys | 48 | 22,081 | 32.2 | **686** |
| atp-bellucc-darderi | 46 | 13,005 | 15.5 | **837** |
| atp-kouame-basavar | 66 | 6,436 | 7.0 | **924** |
| atp-tomic-harri | 32 | 4,288 | 1.0 | 4,288 |
| atp-bolt-ruiz | 58 | 14,476 | 1.2 | 12,332 |
| atp-rocha-mmoh | 50 | 29,742 | 0.6 | 50,397 |
| atp-gaston-justo | 2 | 49,363 | 0.0 | ∞ |
| atp-onclin-sachko | 29 | 19,279 | 0.0 | ∞ |
| atp-zandsch-vallejo | 11 | 80,937 | 0.0 | ∞ |
| cs2-k271-sparta | 2 | 756 | 0.0 | ∞ |

The best market in the universe needs **11.4 hours** of its own observed volume to clear
the queue in front of one order. The config already says a naked leg older than 15
minutes is stale (`pairs_exit_window_sec = 900`). The mismatch is three orders of
magnitude.

## Why the existing gates do not cover this

Three bars already look like they might, and none of them measure this quantity.

**`select_min_volume_24h_usd`** measures market-wide tape over 24 hours, in dollars,
across every price level. A market can clear six figures a day while trading nothing at
`mid - 2.5c`, which is the only level a maker order of ours ever sits at. It answers "is
this market alive", not "does anything trade where we rest".

**`select_min_top3_depth_usd`** measures resting size near mid — and from the maker's
side, that resting size **is the queue ahead of us**. It is a *floor* on the exact
quantity we now need a *ceiling* on. It was written for a different and legitimate
purpose: depth is what lets you exit a position without moving the book. Both readings
are correct, and they point in opposite directions.

**`book_health`** (`max_book_spread`, `min_book_depth_sh`) has the same shape: it rejects
a book too thin to absorb an exit. Again a floor, again on our own queue.

So this is not a case of tightening a bar. **Do not tighten any of the three.** Each is
protecting something real, and raising them makes the queue problem worse, not better.
What is missing is a bar in the opposite direction, and it does not exist anywhere in
`scoring/` or `core_brain/`.

Stated plainly: **depth is good for exiting and bad for queueing, and the selector
currently only knows the first half.**

## The bar

Reject a market when the resting size at our intended quote level would take longer than
`select_max_queue_minutes` of that market's own recent traded volume *at that same level*
to clear.

```
queue_minutes = resting_size_at(level) / (traded_volume_at(level) / window_minutes)
reject when   queue_minutes > select_max_queue_minutes
```

Both terms are measured **at the level we would actually quote at** — `mid - reward_offset`
on each token — not market-wide, and not near mid. That is the whole point of the bar: it
is the one place where our own price is the thing being measured.

### Config

Alongside `select_min_top3_depth_usd`, in the market-selection block:

```python
select_max_queue_minutes: float = 15.0
enforce_max_queue_minutes: bool = True
```

**15 minutes**, anchored to `pairs_exit_window_sec = 900` — the window the pairs rule
already treats as the outer edge of a healthy one-sided fill. A queue that cannot clear
inside the window in which a naked leg is still considered fresh is a queue we should
never join.

Switchable, like `enforce_price_band` and `enable_pairs_rule`, so the bar can be
attributed a result on its own. `0` disables, matching every other limit in `MakerConfig`.

An unmeasurable market — no observed volume at the level, so the rate is zero and the
quotient is infinite — is **rejected**, not passed. Four of the ten markets above are in
that state. "We could not measure any trading at our price" is the strongest possible
version of the finding this bar exists to catch, and treating it as missing data would
invert the rule exactly where it matters most.

## What the bar excludes, on this run

**All ten markets. Including all four that were quoting most.**

| threshold | markets passing |
| --- | --- |
| 15 min (proposed) | **0 of 10** |
| 60 min | 0 of 10 |
| 240 min | 0 of 10 |
| 720 min | 1 of 10 — bonzi-halys |
| 1440 min | 3 of 10 — bonzi-halys, bellucc-darderi, kouame-basavar |

This has to be said without softening: **a bar that rejects the entire universe is not a
filter, it is a verdict.** The proposed threshold would have stopped the bot from quoting
anything at all in session B.

That is the correct outcome for the run we measured — 344 orders, 1,936 shares committed,
zero fills, and the churn that produced. Quoting nothing would have been strictly better.
But it means the bar must ship with its consequence understood: on this market universe,
at this threshold, the bot goes quiet. Shipping it and then hunting for why the dashboard
shows no quotes would be a self-inflicted incident.

Two honest options for that, and they are the operator's to choose, not the code's:

1. Ship at 15 minutes and accept silence until the universe changes (different sport,
   different time of day, different market class). The bar then doubles as a search: the
   first market that passes is the first market worth quoting.
2. Ship at 15 minutes with `enforce_max_queue_minutes = False`, so it **records**
   `queue_minutes` per market without refusing anything, and run it for a day to see
   whether any market anywhere clears the bar before it starts refusing.

Option 2 is the better first move. It costs nothing, it turns the bar into the
measurement we do not currently have, and it cannot cause a silent outage.

## What this does not claim

The fill model drains queue **only on trades, never on cancels**. On a real venue an
order also advances when orders ahead of it are cancelled, and at queues of 13,000–80,000
shares, cancellation is very likely the dominant way position improves. The model ignores
that entirely.

So `queue_minutes` as computed here is an **upper bound on the wait, not an estimate of
it** — the true figure is lower, possibly by a lot, and nothing in `data/shadow.db` can
say by how much. The ranking between markets is more trustworthy than the absolute
numbers, because every market is measured the same wrong way.

This is the strongest argument for calibrating against one real order rather than
tightening the model further. Three zero-fill rehearsals have now produced no information
about queue behaviour, because the mechanism that actually moves a maker up the queue is
not modelled at all.

## Implementation notes

- Lives in `scoring/selector.py` next to `pair_books_allowed`, which already receives both
  books and is where the other selection bars are enforced.
- Needs traded volume at a price level. `markets.recent_trades` already returns
  `token -> price -> volume`, which is exactly the shape required; the selector does not
  currently call it.
- Pin the boundary in `tests/test_market_selection_bars.py`, matching how that file pins
  `select_min_top3_depth_usd`: one market just inside the bar, one just outside, and the
  zero-volume case asserted as a rejection rather than a pass.
- `data/orders.db` is untouched. No change to quoting, sizing or the merge path — this is
  a selection bar and refuses markets before any of that runs.
