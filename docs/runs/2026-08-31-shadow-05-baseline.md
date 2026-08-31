# Baseline run: shadow-05, 2026-08-31

The reference run for the depth/volume gate trial (#88). Everything below is
measured from `data/04_shadow_31-08_20-43.db` and the registry it wrote — no
estimates, no projections. Where a figure could not be measured it says so.

**Read this before starting a trial run.** A trial is only interpretable
against a baseline taken under the same conditions, and this is that baseline.

---

## Setup

| | |
| --- | --- |
| Run id | `shadow-05` |
| Store | `data/04_shadow_31-08_20-43.db` |
| Started | 2026-08-31 20:43 local |
| Duration | 75 minutes (`--minutes 75`) |
| Rotation interval | 5 s (`--interval 5`) → ~900 rotations |
| Signer | none — `core_brain.shadow_run` builds a key-free, credential-free client |
| Universe | 4 markets from `runtime/markets.json`: three US Open tennis, one CS2 |
| Gates in force | the shipped bars: depth ≥ $500, 24h volume ≥ $125,000, spread ≤ 0.06, mid in [0.20, 0.80], horizon ≤ 30d |

Everything is a **rehearsal**. Fills are credited only from real trade tape at
our own price (`core_brain/shadow_fills.py`); the merge is arithmetic, not a
venue execution. The dollar figures below are not earnings.

## Headline result

```
orders placed      367
quotes logged      366
cancels            361
fills                2
merged pairs         1
realized PnL     +$0.30   (simulated)
```

Fill rate: **2 / 367 = 0.5%** of resting orders.

## The one completed round trip

```
20:44:36   UP    6 sh @ 0.70
20:44:37   DOWN  6 sh @ 0.25      ← one second apart
           pair cost $0.95
           merged 6 sh: $5.70 in → $6.00 out
           realized +$0.30 = (1.00 − 0.95) × 6
```

Market: `Cs2 Mouz Fal2 2026-08-31`. Both legs filled as makers, one second
apart, and the pair merged on the following sweep. This is the strategy
executing end to end, and it is the only time it did so in 75 minutes.

## Cancels, by reason

The attribution added in #131. This is the first run where the reasons exist.

| Reason | Count | Share | Median queue ahead | Judgement |
| --- | --- | --- | --- | --- |
| `price_moved` | 159 | 44% | 3,125 | the only class the queue hold can keep |
| `not_quoted` | 110 | 30% | 5,274 | gate working as designed |
| `regate_pair_cost` | 89 | 25% | 5,528 | gate working as designed — prevented pairs over $1.00 |
| `market_dropped` | 2 | <1% | unmeasured | gate working as designed |
| unattributed | 1 | <1% | — | a path that records no reason |

**56% of all cancelling is the safety system doing its job.** Only the 44%
`price_moved` group is potentially recoverable.

## Queue depth — the binding constraint

Shares resting ahead of our order, on the 159 `price_moved` cancels:

| percentile | shares ahead |
| --- | --- |
| p0 | 0 |
| p10 | 105 |
| p25 | 979 |
| **p50** | **3,125** |
| p75 | 11,372 |
| p90 | 18,975 |
| max | 69,431 |

A typical order needed **3,125 shares to trade at its exact price** before it
would be served. Across the whole run, tape at our own price levels was
negligible.

The distribution is barbelled: 10 orders were cancelled while sitting at
**zero shares ahead** — first in line — for a price move.

## Quote lifetimes

| percentile | lifetime |
| --- | --- |
| p10 | 8 s |
| p25 | 16 s |
| **p50** | **30 s** |
| p75 | 57 s |
| p90 | 122 s |

One cancel every 12.5 seconds across the run.

## What the queue hold would have kept

Modelled against this run's `cancel_queue_ahead` values
(`requote_hold_queue_shares`, currently 0.0 = off):

| threshold | holds | share of price-move cancels |
| --- | --- | --- |
| 25 | 11 | 6.9% |
| **50** | **16** | **10.1%** |
| 200 | 21 | 13.2% |
| 500 | 35 | 22.0% |
| 1,000 | 41 | 25.8% |

**50 shares** captures the entire front-of-queue cluster while holding nothing
beyond ~100 shares, where this run shows fills do not happen anyway.

## Conclusions

1. **The pipeline is sound.** Screen → quote both legs → fill → merge → book
   profit all executed correctly. The economics behaved exactly as designed:
   $0.95 in, $1.00 out.
2. **Volume, not logic, is the limit.** 0.5% fill rate, with a median 3,125
   shares ahead. Nothing about pricing, gating or timing explains this; queue
   depth does.
3. **The gates are not the problem here.** Of 361 cancels, 201 were gates
   protecting the position. The 44% price-move group is the only room to
   improve, and it is worth ~10% of itself.
4. **A faster interval would not help and would likely hurt.** Re-quoting sends
   an order to the back of its queue; at 5 s a quote is already re-decided ~6
   times in its 30 s median life. The constraint is queue position, which
   polling does not change.
5. **The next real lever is which queues we stand in** — the depth/volume bars,
   i.e. #88.

## What this baseline does NOT establish

- **Nothing about profitability.** One merged pair is one observation. The
  distribution panel correctly reports no confidence interval at N=1.
- **Nothing about live fills.** Shadow fills are modelled from real tape, but a
  live maker order also faces cancellation risk, venue latency and competition
  the model does not simulate.
- **Nothing about adverse selection.** No markout horizon matured during the
  run, so post-fill drift is unmeasured.

## Reproducing the figures

```powershell
python -m core_brain.cancel_report --db data/04_shadow_31-08_20-43.db
```

Queue percentiles and hold-threshold modelling are computed from
`orders.cancel_queue_ahead` where `cancel_reason = 'price_moved'`.
