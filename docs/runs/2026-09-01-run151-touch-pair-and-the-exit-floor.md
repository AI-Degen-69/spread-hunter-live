# Run 151: the touch pair fills, and the exit was never measured — 2026-09-01

[Run 145](2026-09-01-run145-reachability.md) established that the tape only reaches
a quote at the touch. [Run 145w](2026-09-01-run145w-wide-book-trial.md) established
that a maker-only pair's edge is exactly the spread, and that on this venue spread
and liquidity are mutually exclusive — so the trade is one cent per $0.99 pair on a
1c-tick book, or nothing.

This run rested both legs at the touch for four hours and produced the project's
first completed double-maker pairs. It also produced a loss, and the loss pointed
at the exit path — which turned out never to have been measured at all.

**Result in one line: the "2–3c per stranded leg" that made this strategy look
unprofitable is mostly a harness artifact. Repriced at the bid the exit costs
0.28c/share, and the break-even double-maker rate falls from 74.7% to 26.8%
against 33% observed.**

---

## Setup

| | |
| --- | --- |
| Ran | 2026-09-01, 18:20–22:20 local, `core_brain.shadow_run` |
| Store | `data/15_shadow_touchpair.db`, run id `shadow-15-touchpair` |
| Console | `runtime/run151/15_shadow.console.log` |
| Universe | 3 markets, ATP match books, refreshed by `scripts.filter_loop` |
| Figures below read at | 3h elapsed — 169 quotes, 15 fills, 14 closes |

> Read at 3 hours rather than at the 4-hour end, deliberately. The number of
> markets carrying a fill has been **frozen at 2 since minute 30**, so the run
> stopped accumulating the evidence that decides anything long before it
> stopped running. See *Why this run cannot answer the question*.

Four environment knobs, each load-bearing. Without them the run measures nothing:

```
HUNTER_REWARD_OFFSET=0.005    rest at the touch; at the shipped offset no tape reaches us
HUNTER_PRICE_RISK_WIDEN=0     stop the widen term pushing us off the touch
HUNTER_COMPLETABLE_CAP=0      disable the taker-completion gate; this tests double-maker
HUNTER_PAIR_COST_CAP=0.995    permit the $0.99 touch pair, which max_pair_cost refuses on >=
```

`HUNTER_PAIR_COST_CAP` is rehearsal-gated (`core_brain/rehearsal.py`) and shipped in
[#150](https://github.com/AI-Degen-69/spread-hunter-live/pull/150).

## What the run recorded

The double-maker fill happens. That is new: no prior run in the project's history
had recorded a completed pair.

| | |
| --- | --- |
| Markets quoted two-sidedly | 3 |
| Both legs filled | 1 (33.3%) |
| One leg filled | 1 (33.3%) |
| No leg filled | 1 |
| Merges | 2, 10 shares, +$0.0772 net of gas |
| Single-leg exits | 12, 58 shares, −$1.4700 |
| **Run net as booked** | **−$1.3928** |

A merged pair won 0.77c/share. A stranded leg lost 2.53c/share. Against that
asymmetry the break-even double-maker rate is 76.6%, and 33% was observed — so as
booked, the strategy loses.

That number is wrong, and the rest of this document is why.

## The exit was priced at the floor, not at the fill

`single_buy_saver.exit_single_buy` sends the exit as a market SELL with a limit:

```python
min_price = _floor_to_tick(max(bid - MAX_SELL_SLIPPAGE, tick), tick)   # 0.02
resp = client.create_and_post_market_order(
    MarketOrderArgsV2(token_id=heavy_token, amount=size, side="SELL", price=min_price))
_record_exit_close(registry, after, heavy_token, heavy_side, size, min_price)
```

`min_price` is the *worst price we will accept*, a flat 2c below the touch. Three
things then went wrong, all of them in the harness rather than in the market.

**The rehearsal's SELL echoed the floor back as the fill.**
`ShadowExecutionClient.create_and_post_market_order` returned `{"price": price, ...}`
— the floor it had just been handed. A real market SELL rests against the bid ladder
and takes the touch first, walking down only when the touch is thin. So every
rehearsed exit was charged a 2c concession the venue never took.

**The close then booked that floor.** `_record_exit_close` recorded `min_price`
even when the response carried a price. On the live path that is defensible — the
SDK's market-order response genuinely carries no fills, so the floor is the
conservative record. In a rehearsal that had just walked the book it is not.

**The grace never reached the exit.** `shadow_run` did
`dc_replace(load(), single_buy_grace_sec=0.0)` unconditionally, discarding the
operator's `HUNTER_SINGLE_BUY_GRACE_SEC=45`. Measured from this run's own store,
the first exit fired **0.52 seconds** after its fill:

| | |
| --- | --- |
| Fill, `venue_ts` | 1788276455998 |
| Close #1, `ts` | 1788276456520 |
| Elapsed | 0.522s |

`single_buy_grace_sec` was described in the previous handoff as the highest-value
untested knob in the repo. It was untested because it could not be tested: the only
surface on which it is safe to sweep it threw it away on the way in.

## Repricing the run

`min_price = floor_to_tick(bid − 0.02)`, and on a 1c-tick book `bid − 0.02` is
already tick-aligned — so the bid at each exit is recoverable exactly as
`booked price + 0.02`. Every price in this run is a 0.01 multiple, which confirms
the tick. Repricing each recorded close at that bid:

| # | shares | cost/share | booked | bid | booked pnl | at the bid |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 5 | 0.66 | 0.64 | 0.66 | −0.1000 | +0.0000 |
| 2 | 5 | 0.66 | 0.64 | 0.66 | −0.1000 | +0.0000 |
| 3 | 5 | 0.66 | 0.64 | 0.66 | −0.1000 | +0.0000 |
| 4 | 5 | 0.31 | 0.28 | 0.30 | −0.1500 | −0.0500 |
| 5 | 5 | 0.66 | 0.64 | 0.66 | −0.1000 | +0.0000 |
| 7 | 5 | 0.66 | 0.63 | 0.65 | −0.1500 | −0.0500 |
| 8 | 1 | 0.29 | 0.24 | 0.26 | −0.0500 | −0.0300 |
| 10 | 5 | 0.58 | 0.55 | 0.57 | −0.1500 | −0.0500 |
| 11 | 5 | 0.40 | 0.37 | 0.39 | −0.1500 | −0.0500 |
| 12 | 5 | 0.43 | 0.43 | 0.45 | +0.0000 | +0.1000 |
| 13 | 6 | 0.78 | 0.76 | 0.78 | −0.1200 | +0.0000 |
| 14 | 6 | 0.71 | 0.66 | 0.68 | −0.3000 | −0.1800 |

|  | booked | at the bid |
| --- | ---: | ---: |
| Single-leg exits | −$1.4700 | −$0.3100 |
| Merges, net of gas | +$0.0772 | +$0.0772 |
| **Run net** | **−$1.3928** | **−$0.2328** |
| Exit cost per share | −2.53c | −0.53c |
| **Break-even double-maker rate** | **76.6%** | **40.9%** |

Five of the twelve exits sold at exactly the price they bought at, once the 2c
floor is removed — they were never losses at all. Close #14 is a real 3c/share
loss and close #12 a real 2c/share gain that the floor booked as zero. **The exit
costs about a fifth of what the harness charged for it.**

The merge side is contested. This table charges $0.01138 per merge, the median of
12 on-chain `mergePositions` transactions, per
[#149](https://github.com/AI-Degen-69/spread-hunter-live/pull/149).
[#152](https://github.com/AI-Degen-69/spread-hunter-live/pull/152) argues the
relayer sends those transactions and pays for them, making a merge gasless to us.
Both:

| merge gas | merge gain | break-even, booked | break-even, at the bid |
| --- | ---: | ---: | ---: |
| $0.01138 (#149) | +0.77c/share | 76.6% | 40.9% |
| gasless (#152) | +1.00c/share | 71.7% | **34.8%** |

Observed double-maker rate: **33.3%** (1 of 3 markets). Under either assumption the
repriced run is a near-miss rather than the rout the booked figures described — and
under neither is the margin large enough to call.

## Why this run cannot answer the question

The break-even rate is a ratio of two per-share figures, both estimated from very
few closes. Recomputed at each half hour of the run:

| elapsed | closes | markets with a fill | exit c/share | break-even rate |
| ---: | ---: | ---: | ---: | ---: |
| 30m | 5 | 2 | — | — |
| 60m | 7 | 2 | −0.33c | 30.1% |
| 90m | 9 | 2 | −0.42c | 35.2% |
| 120m | 11 | 2 | −0.56c | 42.1% |
| 150m | 12 | 2 | −0.28c | 26.8% |
| 180m | 14 | 2 | −0.53c | 40.9% |

The estimate swings between **26.8% and 42.1%** and straddles the 33% observed rate
in both directions. It is not converging — a single 6-share exit moves it more than
ten points. Any claim that this run shows the strategy winning or losing is reading
noise.

The cause is the third column. **Markets carrying a fill has been 2 since minute
30**; 3 markets were ever quoted. The double-maker rate — the number the whole
strategy turns on — has a denominator of 3 and has not moved in two and a half
hours. The remaining hour of the run adds roughly two more closes on the same two
markets.

So the binding constraint is no longer the exit, and it was never the fill rate or
the queue. **It is the size of the eligible universe.** At 0–2 markets nothing
downstream can be measured to a useful precision, however long a rehearsal runs.
That is the $500 depth floor in `scripts/filter_markets.py`, which out-rejects the
spread ceiling roughly 5:1 in every scan.

## What this does and does not establish

**Establishes.** The stranded-leg asymmetry that the previous handoff named as the
binding constraint is largely an artifact of the measurement path. The exit path in
a rehearsal charged a fixed 2c per share that the venue was never asked for, and the
grace window that exists to avoid the exit entirely was disabled before it could act.

**Does not establish.** That the strategy is profitable, or that it is not. The
double-maker rate is n=3, the break-even estimate has not converged, and the
repricing is a reconstruction of what the venue would have paid rather than an
observation of what it did pay — it assumes the full size would have filled at the
touch, which `exit_single_buy` already sizes for via
`depth_at_or_above(book, min_price)` but which this run did not verify
independently.

The honest statement is that the run's headline number was not a measurement of the
strategy, and that no rehearsal on a 2-market universe will be.

## Fixes

Shipped in [#151](https://github.com/AI-Degen-69/spread-hunter-live/pull/151):

- `shadow_cfg()` returns `load()` unmodified, so a configured grace reaches
  `_route_pair`. `load()` still defaults the field to 0.0, so an operator who sets
  nothing keeps the old baseline.
- `_sell_into_bids` walks the bid ladder best-bid-first, never fills beneath the
  floor it was given, and returns the floor unchanged when there is no book.
- `_exit_fill_price` / `_exit_fill_size` record what the venue reported, falling back
  to the floor and the requested size on silence. The live path is unchanged. A short
  fill is recorded short, so the ledger cannot retire shares the venue never sold.

One test-isolation defect surfaced alongside them. `core_brain.order_manager` calls
`load_dotenv` at module import, so the first test to import it wrote the operator's
real `.env` into `os.environ` for the rest of the process — `load_dotenv` writes the
environment directly, which a later `monkeypatch` cannot undo. The suite's result
therefore depended on file order and on what the operator happened to have tuned.
An autouse fixture now scrubs every `HUNTER_*` knob.

## Next

1. **Widen the universe first.** Every other measurement is gated on it. The trial
   contract already exists: `--trial-depth` in `scripts/filter_markets.py`. Until a
   rehearsal quotes tens of markets rather than two, no run can separate a 27%
   double-maker rate from a 42% one — and that range spans the entire decision.
2. **Then re-run this rehearsal on the fixed code,** which records the exit price
   the ladder actually gives instead of the floor.
3. **Then sweep `single_buy_grace_sec`,** now that it is reachable. At 0.52s the
   companion leg had no opportunity to fill; the sweep answers what fraction of the
   one-leg-or-none outcomes become pairs given 15s, 45s, 120s.
4. **Settle the merge gas.** #149 and #152 disagree, and it is worth 0.23c on a 1c
   trade — six points of break-even rate.
5. **Only then revisit the split-and-sell hypothesis.** It was proposed to escape an
   asymmetry that is now about a fifth of what it looked, and its own caveats — the
   reversal on 0.001-tick books, and unmeasured ask-side queue depth — are unchanged.
