# Run 145w: the wide-book trial, 2026-09-01

[Run 145](2026-09-01-run145-reachability.md) closed with one regime unmeasured.
Its only sub-$0.99 touch pairs — $0.85 and $0.96 — came from the ten samples
where a book had widened, and three gates refuse that regime outright. This run
raises all three and measures what is behind them.

Everything below is measured from the two stores this run wrote and from a
venue-wide scan of 500 markets. Where a figure could not be measured it says so.

**Result in one line: the touch pair is exactly `1.00 - spread`, so the edge a
maker-only pair can earn IS the spread — and on this venue spread and liquidity
are mutually exclusive.**

---

## Setup

| | |
| --- | --- |
| Ran | 2026-09-01, two 45-minute processes |
| Trial | `HUNTER_WIDE_BOOK_TRIAL=0.15`, and the ranker's `--trial-spread 0.15` |
| Signer | none — both processes declare themselves rehearsals and neither can sign |

| Store | What it holds |
| --- | --- |
| `data/14_shadow_widetrial_01-09_09-40.db` | rehearsal with the trial ceiling: 51 orders, 51 quotes, 0 fills |
| `data/14_booktape_wide_books_01-09_09-40.db` | 8 genuinely wide books: 918 samples, 32 live tape rows |

No order reached a venue and no money moved.

## The law: touch pair = 1.00 - spread

`best_bid(UP) + best_bid(DOWN)` is the cheapest pair two resting bids can make.
Measured against each market's own spread, it is not approximately `1.00 -
spread`; it is exactly that, in every sample of both runs:

| Spread | Touch pair | Edge per pair |
| --- | --- | --- |
| 0.170 | $0.8300 | 17.0c |
| 0.141 | $0.8590 | 14.1c |
| 0.099 | $0.9010 | 9.9c |
| 0.070 | $0.9300 | 7.0c |
| 0.010 | $0.9900 | 1.0c |
| 0.001 | $0.9990 | 0.1c |

It follows directly from the identity run 145 established. `mid(UP) + mid(DOWN)`
= 1.0000, and each best bid sits half a spread under its own mid, so the two
bids sum to `1.00 - spread`. The identity held in 787 of 798 samples here, on
books far wider than any run 145 saw.

**This single line explains every previous result.** The strategy trades markets
with a 1c or 0.1c spread. Their touch pair is therefore $0.99 or $0.999, and
`max_pair_cost = 0.99` refuses it on a `>=` comparison. That is not a policy
that could be tuned. It is arithmetic: on a one-tick book there is one cent of
gross edge and the cap sits exactly on it.

## The trial does what it was built to do

Touch-pair cost across 798 samples on the eight wide books:

| | Wide books (this run) | Liquid tight books (run 145) |
| --- | --- | --- |
| Samples | 798 | 1,247 |
| Median touch pair | **$0.9310** | $0.9990 |
| Minimum | $0.7190 | $0.9900 |
| **Refused by `max_pair_cost`** | **11.65%** | **100.00%** |

With the ceiling raised, 88% of samples present a pair the risk gate would
accept, against none at all before. The gate was never the problem in itself —
it was refusing books that had no edge to offer.

Reachability is better too, and unlike run 145 it does not fall to zero:

| Bid resting at | Wide books | Liquid tight books |
| --- | --- | --- |
| mid | 78.33% | 34.25% |
| 1 tick under | 19.78% | 7.47% |
| 3 ticks under | 12.38% | **0.00%** |
| 5 ticks under | 5.04% | **0.00%** |

A wide book has room between mid and the touch, so a resting order can sit at a
real offset and still be somewhere the tape can reach.

## And then the flow is not there

| | Wide books | Liquid tight books |
| --- | --- | --- |
| Markets | 8 | 8 |
| Window | 45 min | 75 min |
| Live tape volume | **1,016 shares** | 66,853 shares |
| Per minute | **22.6** | 891 |
| Markets that printed at all | **4 of 8** | 8 of 8 |

**Wide books carry about 2.5% of the flow.** Half of them did not trade once in
forty-five minutes.

Against that, their edge is 7x to 69x better per share — 6.9c at the median
against 1.0c or 0.1c. The two effects are within an order of magnitude of each
other, which is the honest summary: neither regime is obviously the better
business, and the 0.1c books are arguably the worse one despite carrying all
the volume, because no pair they offer can clear the cap at all.

Depth tells the same story from the other side. Pairs available at the touch,
and the theoretical maximum if every one of them filled:

| Market | Spread | Edge | Pairs at touch | Max |
| --- | --- | --- | --- | --- |
| `zay-flowers-774pt5` | 0.1519 | 15.19c | **8** | $1.22 |
| `ucl-fcb-fey` | 0.0887 | 8.87c | **6** | $0.53 |
| `putin-meets-iranian-officials` | 0.1413 | 14.13c | 28 | $3.96 |
| `nfl-sf-la-spread-home-6pt5` | 0.0546 | 5.46c | **187** | $10.21 |
| `fed-no-change` (tight) | 0.0100 | 1.00c | 23,152 | $231.52 |
| `crystal-palace` (tight) | 0.0010 | 0.10c | 770,875 | $770.87 |

Spreads are the run's mean per market, carried to four places so the `Max`
column multiplies out. Rounding them to three made two rows fail to.

Edge and size are inversely coupled through the spread, because both are the
spread. Seventeen cents on eight shares, or 770,875 shares at a tenth of a cent.

One row is unlike the others. `nfl-sf-la` carries a 5.46c spread AND 187 pairs at
the touch — real edge on real size. It is one market of eight, and it is the
shape worth looking for.

## The regime is unreachable at the volume the ranker requires

A venue-wide scan of 500 active markets, bucketed by 24-hour volume:

| 24h volume | n | Median spread | Spread over 6c |
| --- | --- | --- | --- |
| over $1M | 3 | 0.0100 | **0** |
| $250k–1M | 12 | 0.0010 | **0** |
| $125k–250k | 22 | 0.0010 | **0** |
| $25k–125k | 63 | 0.0020 | **0** |
| $1k–25k | 400 | 0.0100 | 26 (6.5%) |

**At the ranker's own volume floor of $125,000, zero of 37 markets have a spread
over 6c.** Wide books exist only in the $1k–25k bucket, and the eight recorded
here do $3,000–$7,000 a day between them.

The ranker confirmed it from the other direction. With `--trial-spread 0.15` it
went from 0 markets to 1 — and that market, `atp-aguiard-matsuok`, has a spread
of **0.01**. It was admitted by scan-to-scan churn, not by width. The rejection
census names what actually binds:

```
scored 114, rejected 113 (NO: top-3 bid depth=9, YES spread=11,
YES: top-3 bid depth=52, blocked dynamic/submarket keyword=3,
carries a submarket group label=4, horizon=1, income=1, volume=32)
gates: ... spread <= 0.15 (TRIAL), ...
```

Eleven spread rejections against sixty-one on the `$500 top-3 bid depth` floor.

## What the rehearsal did

51 orders, 51 quotes, 0 fills, on a book that was never wide. The
`book too wide` refusal fired **zero** times, because the trial admitted a
one-tick market. The rehearsal therefore tests that the trial does not break the
quoting path, and nothing more.

Its skip log surfaces a gate this investigation had not looked at. The dominant
refusal was not spread or depth but the **decided-market band**: 79 cycles
refused with `mid 0.895 outside [0.20,0.80]`, and more at 0.905, 0.945 and
0.115. Wide books are wide partly because they are lopsided, so a ceiling raise
hands markets to a quoter whose price band then refuses them. That is a fourth
gate on the same regime and it was not moved here.

## Conclusions

1. **The touch pair is exactly `1.00 - spread`.** The edge available to a
   maker-only pair IS the spread, on every book measured in both runs.
2. **`max_pair_cost = 0.99` is not the obstacle it appeared to be.** It refuses
   100% of liquid tight books because those books offer one cent of edge, and
   11.65% of wide books. The cap is reporting the arithmetic, not causing it.
3. **The trial works.** Raising all three ceilings moves the median touch pair
   from $0.9990 to $0.9310 and makes reachability non-zero past one tick under
   mid for the first time in this project's measurements.
4. **The regime it unlocks is unfundable.** Zero markets above $25k of daily
   volume have a spread over 6c. The wide books that exist do $3–7k a day and
   half of them did not trade at all in forty-five minutes.
5. **Edge and liquidity are the same quantity with opposite signs.** 2.5% of the
   flow at 7–69x the edge per share. Neither regime is clearly better, which is
   a different and worse answer than "the gates are wrong".
6. **The shape worth hunting is mid-spread with real depth.** `nfl-sf-la` at
   5.5c and 187 pairs is the only sample carrying both. Whether that cell is
   populated enough to build on is the open question.

## What this run does NOT establish

- **Nothing about fills.** Zero, again, and on a market that was never wide.
  The rehearsal tested the plumbing, not the regime.
- **Nothing about whether the wide-book regime is profitable.** 1,016 shares of
  tape across 8 markets in 45 minutes is enough to say the flow is thin and not
  enough to price it. No fill, no markout, no PnL.
- **Nothing about the `nfl-sf-la` shape.** One market, one window. Whether
  mid-spread books with real depth are a population or a coincidence is
  unmeasured.
- **Nothing about the decided-market band.** It was the rehearsal's dominant
  refusal and this run did not vary it.
- **Nothing about the $500 depth floor**, which out-rejects the spread ceiling
  six to one and was left at its shipped value throughout.
- **Nothing about other hours.** One Sunday window. The venue-wide spread/volume
  scan is a single snapshot of 500 markets, not a distribution over time.

## Reproducing the figures

```powershell
python -m scripts.booktape_report --db data\14_booktape_wide_books_01-09_09-40.db
python -m scripts.filter_markets --trial-spread 0.15 --top 12
```

The rehearsal ran as:

```powershell
$env:HUNTER_WIDE_BOOK_TRIAL="0.15"; python -m core_brain.shadow_run --minutes 45 --interval 5 --db data\14_shadow_widetrial_01-09_09-40.db
```

`HUNTER_WIDE_BOOK_TRIAL` applies only in a process that has declared itself
unable to place an order, so the same variable in a live shell raises at config
load rather than widening a safety gate. See `core_brain/rehearsal.py`.

The spread-versus-volume table comes from `gamma-api.polymarket.com/markets`
ordered by `volume24hr`, 500 rows, bucketed on `volume24hr` and `spread`. The
wide-book universe the recorder followed is `runtime/run145w/wide_books.json`,
which `runtime/` does not publish; the `condition_id` of every market sampled is
in `book_samples`, which is the durable record.
