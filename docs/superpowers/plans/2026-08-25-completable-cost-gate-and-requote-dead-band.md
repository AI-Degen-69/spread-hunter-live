# Completable-Cost Gate and Re-Quote Dead Band Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refuse a resting bid whose pair cannot be completed under $1.00 by taking the other leg, and stop re-quoting an order that has only drifted a few cents.

**Architecture:** One new pure predicate in `core_brain/risk.py` is the single source of truth for the completable-cost rule. It is called from two places: `quotes._decide_quotes_from_mid`, on the FINAL resting price, before an order is posted; and `trader_loop.plan_orders`, each cycle, against the orders the dead band is holding. It deliberately does NOT live in `risk.hard_block`, which is handed the provisional price and would therefore gate a number the order never rests at. The dead band is a widened keep-tolerance inside `plan_orders`. Both the live Trader and `core_brain.shadow_run` reach `plan_orders` through the same `trader_loop.run`, so one change covers both paths.

**Tech Stack:** Python 3, stdlib only, `pytest`. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-08-25-completable-cost-gate-and-requote-dead-band.md](../specs/2026-08-25-completable-cost-gate-and-requote-dead-band.md)

## Global Constraints

- Branch from `main` (currently `89ef4fe`). `add/shadow-fill-simulation` is already merged; do **not** reuse it. Branch name: `add/completable-cost-gate`.
- `data/orders.db` is the production registry — never opened, read, or written by this work.
- `data/shadow.db` is read-only evidence — never written by this work.
- No `core_brain.order_manager` subcommand is run. No shadow run is started. Both are the operator's to run.
- `python -m pytest -q` must be green at the end of every task.
- Every changed behaviour needs a test that **fails without the change** — verify the RED step actually fails before implementing.
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`). Test files are `tests/test_*.py`. `snake_case` functions, explicit named imports, no wildcards.
- Caps `MAX_ORDER_USD = 25.0` and `MAX_TOTAL_USD = 100.0` in `core_brain/venue.py` are untouched.
- Comments in this repo explain *why*, with measured numbers. Match that density — see `core_brain/risk.py:304-356` for the house style.

---

### Task 1: The config knobs

**Files:**
- Modify: `core_brain/config.py:740-744` (insert after `max_pair_cost`)
- Modify: `core_brain/config.py:864-905` (`load()`, add env overrides)
- Test: `tests/test_completable_pair_gate.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `MakerConfig.max_completable_pair_cost: float = 1.00`,
  `MakerConfig.enforce_completable_pair_cost: bool = True`,
  `MakerConfig.requote_dead_band: float = 0.03`. Env overrides
  `HUNTER_COMPLETABLE_CAP` (float, price units) and `HUNTER_REQUOTE_DEAD_BAND`
  (float, price units).

- [ ] **Step 1: Write the failing test**

Create `tests/test_completable_pair_gate.py`:

```python
"""The completable-cost gate: bid + hedge ask, the price a real pair assembles at.

`max_pair_cost` asks what a pair costs if BOTH legs fill as resting bids. On a
binary market UP + DOWN = 1.00, so the legs are anti-correlated (-0.9989 on
shadow run run-2809a7161de1) and that outcome is rare by construction. These
tests cover the other question: what the pair costs when one leg fills as maker
and the other has to be TAKEN at its ask.
"""
import os
from unittest import mock

from core_brain import risk
from core_brain.config import MakerConfig, load


class TestConfigKnobs:
    def test_defaults_gate_at_one_dollar_with_a_three_cent_dead_band(self):
        cfg = MakerConfig()
        assert cfg.max_completable_pair_cost == 1.00
        assert cfg.enforce_completable_pair_cost is True
        assert cfg.requote_dead_band == 0.03

    def test_completable_cap_is_overridable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HUNTER_COMPLETABLE_CAP": "0.98"}):
            assert load().max_completable_pair_cost == 0.98

    def test_dead_band_is_overridable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_DEAD_BAND": "0.0"}):
            assert load().requote_dead_band == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_completable_pair_gate.py -q`
Expected: FAIL — `AttributeError: 'MakerConfig' object has no attribute 'max_completable_pair_cost'`

- [ ] **Step 3: Add the fields**

In `core_brain/config.py`, immediately after the `max_pair_cost: float = 0.995` line
(currently line 743, inside the `--- inventory ---` block):

```python
    # THE COMPLETABLE-COST GATE. `max_pair_cost` above is a BOTH-MAKER check:
    # what the pair costs if both legs fill as resting bids. On a binary market
    # UP + DOWN = 1.00, so the legs are anti-correlated -- measured -0.9989
    # across 98 mid steps on shadow run run-2809a7161de1, 97 of them opposite
    # sign -- and simultaneous maker fills are rare by construction. The pair
    # that actually assembles is one maker fill plus a TAKER completion at the
    # other leg's ask, and nothing checked that price: all 209 orders of that
    # run carried max_pair_cost_at_post=0.995 while never testing bid + hedge
    # ask. This is that test. The pair pays exactly $1.00, so `>=` the cap is a
    # booked loss or a zero-profit fill slot -- a ceiling reached, not
    # approached, like every other cap here.
    max_completable_pair_cost: float = 1.00
    # Switchable so the rule can be attributed a result on its own, the same
    # escape hatch `enforce_price_band` and `enable_pairs_rule` have.
    enforce_completable_pair_cost: bool = True
```

Then, in the `--- pacing ---` block, immediately after
`requote_interval_sec: float = 2.0` (currently line 747):

```python
    # THE RE-QUOTE DEAD BAND, in price units. Move a resting order only once
    # the desired price has moved at least this far. On run-2809a7161de1, 205
    # of 205 consecutive re-quotes changed price (median 3.0c) and every one
    # sent the order to the back of the queue at a new level; median order
    # lifetime was 11.7s against a median queue_ahead of 1058.7 shares.
    # Measured suppression on that run's own price series: 1c 0/205, 2c 74/205,
    # 3c 98/205, 4c 118/205, 5c 139/205. 3c roughly doubles median lifetime to
    # ~23s. 2c leaves too much churn; 4c and up hold a price the book has left
    # behind, in a market whose mid moved 53c in 30 minutes.
    requote_dead_band: float = 0.03
```

- [ ] **Step 4: Add the env overrides**

In `core_brain/config.py`, inside `load()`, after the `HUNTER_PAIRS_RULE` block
(currently ends line 901):

```python
    ccap = os.environ.get("HUNTER_COMPLETABLE_CAP") or ""
    if ccap.strip():
        kw["max_completable_pair_cost"] = float(ccap)
    rdb = os.environ.get("HUNTER_REQUOTE_DEAD_BAND") or ""
    if rdb.strip():
        kw["requote_dead_band"] = float(rdb)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_completable_pair_gate.py -q`
Expected: PASS, 3 passed

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green. A frozen dataclass gains three defaulted fields; nothing reads them yet.

- [ ] **Step 7: Commit**

```bash
git add core_brain/config.py tests/test_completable_pair_gate.py
git commit -m "feat(config): add the completable-pair cap and the re-quote dead band"
```

---

### Task 2: The `completable_pair_block` predicate

**Files:**
- Modify: `core_brain/risk.py` (add function after `book_health`, which ends at line ~300)
- Test: `tests/test_completable_pair_gate.py` (append a class)

**Interfaces:**
- Consumes: `MakerConfig.max_completable_pair_cost`, `MakerConfig.enforce_completable_pair_cost` from Task 1.
- Produces: `risk.completable_pair_block(cfg, price: float, hedge_ask: float | None) -> Optional[str]` — the reason a bid at `price` must not rest, or `None` if it may. Called by Task 3 (`hard_block`) and Task 5 (`plan_orders`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_completable_pair_gate.py`:

```python
class TestCompletablePairBlock:
    def test_blocks_when_bid_plus_hedge_ask_reaches_the_cap(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        why = risk.completable_pair_block(cfg, 0.55, 0.45)
        assert why is not None
        assert "completable pair" in why
        assert "1.0000" in why

    def test_blocks_when_the_pair_would_cost_more_than_a_dollar(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is not None

    def test_allows_a_pair_that_completes_under_the_cap(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.52, 0.45) is None

    def test_has_no_opinion_when_the_hedge_book_has_no_ask(self):
        # book_health already refuses an unreadable book, with its own wording.
        # A second, differently-worded refusal for the same condition would
        # make the operator's reason string ambiguous.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.99, None) is None
        assert risk.completable_pair_block(cfg, 0.99, 0.0) is None

    def test_is_switchable_off(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00,
                          enforce_completable_pair_cost=False)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is None

    def test_a_zero_cap_disables_the_rule(self):
        # The same escape hatch max_naked_usd and max_fleet_naked_usd have.
        cfg = MakerConfig(max_completable_pair_cost=0.0)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is None

    def test_a_tighter_cap_refuses_a_pair_a_dollar_cap_would_allow(self):
        cfg = MakerConfig(max_completable_pair_cost=0.98)
        assert risk.completable_pair_block(cfg, 0.54, 0.45) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_completable_pair_gate.py::TestCompletablePairBlock -q`
Expected: FAIL — `AttributeError: module 'core_brain.risk' has no attribute 'completable_pair_block'`

- [ ] **Step 3: Write the implementation**

In `core_brain/risk.py`, after `book_health` and before `hard_block`:

```python
def completable_pair_block(cfg, price: float,
                           hedge_ask: Optional[float]) -> Optional[str]:
    """Why a bid at `price` must not rest given what the hedge leg costs to TAKE.

    `max_pair_cost` asks what a pair costs if both legs fill as resting bids.
    This asks the question the tape actually answers: one leg fills as maker,
    and the other has to be bought at its ask. On a binary market the two are
    not close. UP + DOWN = 1.00 is near a mechanical identity, so the legs are
    anti-correlated -- -0.9989 across 98 mid steps on shadow run
    run-2809a7161de1, 97 of them opposite sign -- and a double-maker fill is
    rare by construction rather than by bad luck.

    The cost of not asking it is on the record. Every one of that run's 209
    orders carried `max_pair_cost_at_post = 0.995`, one distinct value, from a
    both-maker check that never tested `price + hedge ask`. The bot was resting
    pairs that were profitable only under the rarest outcome available to it.

    `hedge_ask` of None or 0 is NO OPINION, not a refusal. `book_health` already
    rejects a book it cannot read, in its own words; a second refusal for the
    same condition would leave the operator two different reasons for one fact.

    `>=` not `>`: the pair pays exactly $1.00, so a completable cost AT the cap
    is a booked loss or a fill slot worth nothing. A ceiling reached, not
    approached -- the same reading every other cap in this module takes.
    """
    if not getattr(cfg, "enforce_completable_pair_cost", True):
        return None
    cap = float(getattr(cfg, "max_completable_pair_cost", 0.0))
    if cap <= 0:
        return None
    if hedge_ask is None or float(hedge_ask) <= 0:
        return None
    cost = round(float(price) + float(hedge_ask), 4)
    if cost >= cap:
        return (f"completable pair {price:.3f}+{float(hedge_ask):.3f}="
                f"${cost:.4f} >= ${cap:.3f} cap -- the second leg cannot be "
                f"bought at a profit")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_completable_pair_gate.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all green. The function has no callers yet.

- [ ] **Step 6: Commit**

```bash
git add core_brain/risk.py tests/test_completable_pair_gate.py
git commit -m "feat(risk): add the completable-pair predicate, bid plus hedge ask"
```

---

### Task 3: Wire the gate into the quoting decision

**Files:**
- Modify: `core_brain/quotes.py:302-306` (inside `_decide_quotes_from_mid`, after the final price is computed)
- Test: `tests/test_completable_pair_gate.py` (append two classes)

**Interfaces:**
- Consumes: `risk.completable_pair_block` from Task 2.
- Produces: no new symbol. `_decide_quotes_from_mid` gains a per-side gate; `decide_quotes` returns `([], why)` with `"completable pair"` in `why` when it binds.

**Where the arm goes, and why NOT in `hard_block`.** `hard_block` is called at
`core_brain/quotes.py:271` with `provisional`, not with the price the order would
actually rest at. `provisional` is `mid - reward_offset`; the final `price` then adds
`skew_offset` and the band's `extra_offset`, clamps to the reward window, rounds to
the tick, and caps at `best_bid + tick`. `skew_offset` pulls the LIGHT side **toward**
mid, so the final price can be **above** the provisional — and a gate that cleared the
provisional would have cleared a cheaper pair than the one we post. Gating on the final
price is the only version of this rule that is not off by the skew.

The condition is otherwise as specified: only when `inv.avg(other) <= 0`, because
holding the hedge leg means completion is not needed and `hard_block`'s existing
`max_pair_cost` arm governs.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_completable_pair_gate.py`:

```python
from core_brain.quotes import Inventory, decide_quotes


def _healthy_book(bid, ask, token="tok"):
    """A book that clears every book_health arm, so a test reaches the gate."""
    return {"token_id": token, "best_bid": bid, "best_ask": ask,
            "bids": {bid: 5000.0}, "asks": {ask: 5000.0}}


def _gate_cfg(**kw):
    base = dict(objective="rewards", size_mode="shares", quote_shares=120,
                min_quote_shares=50, reward_offset=0.02,
                price_band_low=0.10, price_band_high=0.90,
                max_completable_pair_cost=1.00)
    base.update(kw)
    return MakerConfig(**base)


class TestGateInDecideQuotes:
    def test_declines_a_market_whose_pair_cannot_be_completed_under_a_dollar(self):
        # UP 0.55/0.57 and DOWN 0.43/0.46 sum to 1.03 at the asks. Resting 2c
        # under each mid gives 0.54 and 0.425; completing costs 0.54+0.46=1.00
        # and 0.425+0.57=0.995. Both at or over the cap.
        cfg = _gate_cfg()
        up = _healthy_book(0.55, 0.57, "tok-up")
        down = _healthy_book(0.43, 0.46, "tok-dn")
        intents, why = decide_quotes(cfg, up, down, Inventory(), 1e9, None)
        assert intents == []
        assert "completable pair" in why

    def test_still_quotes_both_legs_of_a_tight_market(self):
        # The regression guard: this rule must not empty the book. UP 0.50/0.52
        # and DOWN 0.46/0.48 rest at 0.49 and 0.45; completing costs 0.97 both
        # ways, comfortably under the cap.
        cfg = _gate_cfg()
        up = _healthy_book(0.50, 0.52, "tok-up")
        down = _healthy_book(0.46, 0.48, "tok-dn")
        intents, why = decide_quotes(cfg, up, down, Inventory(), 1e9, None)
        assert len(intents) == 2
        assert not why

    def test_switching_the_gate_off_restores_the_wide_market_quote(self):
        cfg = _gate_cfg(enforce_completable_pair_cost=False)
        up = _healthy_book(0.55, 0.57, "tok-up")
        down = _healthy_book(0.43, 0.46, "tok-dn")
        intents, _ = decide_quotes(cfg, up, down, Inventory(), 1e9, None)
        assert len(intents) == 2

    def test_stands_down_once_we_hold_the_hedge_leg(self):
        # 100 DOWN shares held at 0.43: completion is not needed, so the gate
        # has no business refusing the UP leg that would finish the pair at
        # 0.54 + 0.43 = 0.97. The UP quote must survive.
        cfg = _gate_cfg()
        up = _healthy_book(0.55, 0.57, "tok-up")
        down = _healthy_book(0.43, 0.46, "tok-dn")
        inv = Inventory(down_shares=100.0, down_cost=43.0)
        intents, why = decide_quotes(cfg, up, down, inv, 1e9, None)
        assert [i.side for i in intents] == ["UP"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_completable_pair_gate.py::TestGateInDecideQuotes -q`
Expected: FAIL — `test_declines_a_market_whose_pair_cannot_be_completed_under_a_dollar`
asserts `intents == []` and gets two intents.

If instead it fails because `intents == []` already with a *different* `why`, an
earlier arm bound first. Print `why`, adjust the fixture books until only the
completable arm is left, and update the numbers in the comment to match.

- [ ] **Step 3: Write the implementation**

In `core_brain/quotes.py`, inside `_decide_quotes_from_mid`, find:

```python
        if price <= 0.0 or price >= 1.0:
            blocked.append(f"{side}: price {price:.3f} off-scale")
            continue
```

and insert immediately after it:

```python
        # THE COMPLETABLE-COST GATE, on the FINAL price rather than the
        # `provisional` one `hard_block` reads twenty lines above. They are not
        # the same number: `provisional` is mid - reward_offset, and `price`
        # then adds the inventory skew and the band's extra offset, clamps to
        # the reward window and rounds to the tick. `skew_offset` pulls the
        # LIGHT side TOWARD mid, so the price we actually rest at can sit above
        # the provisional -- and a gate read off the provisional would have
        # cleared a cheaper pair than the one we post.
        #
        # Only while we hold NONE of the hedge token. A fill here then opens a
        # leg that has to be finished by CROSSING, and the price that finishes
        # it is the hedge ASK. Holding the other leg already makes completion
        # unnecessary, and `risk.hard_block`'s max_pair_cost arm is the one that
        # governs there; the two conditions never overlap.
        hedge_book = down_book if side == "UP" else up_book
        if inv.avg("DOWN" if side == "UP" else "UP") <= 0:
            completable = risk.completable_pair_block(
                cfg, price, hedge_book.get("best_ask"))
            if completable:
                blocked.append(f"{side}: {completable}")
                continue
```

- [ ] **Step 4: Note the gate in the `_decide_quotes_from_mid` docstring**

The docstring already explains why the price band and pair-cost caps moved into
`risk.hard_block`. Add one paragraph after that discussion:

```
    The COMPLETABLE-COST gate runs here rather than in `hard_block` for a
    mechanical reason: `hard_block` is handed `provisional`, and the price this
    function rests at is `provisional` plus skew, plus the band offset, clamped
    and tick-rounded. Skew pulls the light side toward mid, so the two differ in
    the direction that matters. The gate asks what the pair costs to FINISH by
    taking the other leg -- the question the old `max_pair_cost` never asked,
    which is how all 209 orders of shadow run run-2809a7161de1 rested with one
    distinct `max_pair_cost_at_post` of 0.995 and zero fills.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_completable_pair_gate.py -q`
Expected: PASS — the 10 tests from Tasks 1 and 2, plus these 4.

- [ ] **Step 6: Run the full suite and read every failure**

Run: `python -m pytest -q`
Expected: green. If a quoting test fails, check whether its fixture books genuinely
have `resting price + hedge ask >= 1.00` — the gate refusing them is correct and the
fixture wants widening, not the gate weakening. The four two-sided fixtures in
`tests/test_live_quotes.py` (lines 30, 125, 175, 201) were checked against this change
and all complete at 0.975–0.99. **Do not relax the cap to make a test pass.**

- [ ] **Step 7: Commit**

```bash
git add core_brain/quotes.py tests/test_completable_pair_gate.py
git commit -m "feat(quotes): refuse a bid whose pair cannot be completed under the cap"
```

---

### Task 4: The re-quote dead band, and the re-gate that makes it safe

**Files:**
- Modify: `core_brain/trader_loop.py:29` (import), `core_brain/trader_loop.py:53-86` (`plan_orders`)
- Test: `tests/test_trader_loop.py` (append to `TestPlanOrders`)

**Interfaces:**
- Consumes: `risk.completable_pair_block` from Task 2, `MakerConfig.requote_dead_band` from Task 1.
- Produces: `plan_orders(open_orders, intents, price_eps=1e-9, *, dead_band=0.0, cfg=None, hedge_asks=None)`. The three new arguments are keyword-only and all default to the current behaviour, so every existing call site and test is unchanged. `hedge_asks` maps `token_id -> best_ask of the OTHER token`. Task 5 supplies them.

**Why the re-gate ships with the dead band:** an order the dead band keeps rests at its own price, up to `dead_band` away from the price the gate approved. Without re-checking it, a 3c-stale bid in a moving market carries a completable cost 3c worse than anything the gate ever allowed — the dead band would be a hole punched straight through Task 3. When the re-gate cancels a kept order, that token's current intent is submitted in its place, so the market is re-quoted at a compliant price rather than left dark.

- [ ] **Step 1: Write the failing test**

Append to the `TestPlanOrders` class in `tests/test_trader_loop.py`:

```python
    def test_dead_band_keeps_an_order_that_only_drifted_a_couple_of_cents(self):
        # 205 of 205 consecutive re-quotes on run-2809a7161de1 changed price
        # (median 3.0c) and every one reset queue position to zero.
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == []
        assert to_submit == []

    def test_dead_band_still_re_quotes_once_the_price_moves_past_it(self):
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.55)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == open_orders
        assert [i.price for i in to_submit] == [0.55]

    def test_dead_band_is_symmetric(self):
        # Queue position is lost in both directions.
        open_orders = [_open(price=0.58)]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == []
        assert to_submit == []

    def test_dead_band_defaults_off_so_existing_callers_are_unchanged(self):
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == open_orders

    def test_the_larger_of_dead_band_and_price_eps_wins(self):
        # Two independent reasons to keep an order; neither may silently
        # disable the other.
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, _ = plan_orders(open_orders, intents,
                                   price_eps=0.001, dead_band=0.03)
        assert to_cancel == []

    def test_a_kept_order_is_cancelled_when_its_own_price_fails_the_gate(self):
        # The order rests at 0.60. The desired price is 0.58, within the band,
        # so the band would keep it -- but completing at the DOWN ask of 0.42
        # costs 1.02, which is exactly the hole the band would otherwise open.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks={"tok-up": 0.42})
        assert to_cancel == open_orders
        assert [i.price for i in to_submit] == [0.58]

    def test_a_kept_order_that_still_completes_under_the_cap_is_left_alone(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks={"tok-up": 0.38})
        assert to_cancel == []
        assert to_submit == []

    def test_a_missing_hedge_ask_leaves_the_kept_order_alone(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, _ = plan_orders(open_orders, intents, dead_band=0.03,
                                   cfg=cfg, hedge_asks={})
        assert to_cancel == []
```

Add `from core_brain.config import MakerConfig` to the imports at the top of
`tests/test_trader_loop.py` if it is not already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trader_loop.py::TestPlanOrders -q`
Expected: FAIL — `TypeError: plan_orders() got an unexpected keyword argument 'dead_band'`

- [ ] **Step 3: Write the implementation**

In `core_brain/trader_loop.py`, add to the imports at line 29:

```python
from core_brain import risk
```

Then replace the whole of `plan_orders` (lines 53-86) with:

```python
def plan_orders(
    open_orders: list[dict],
    intents: list[QuoteIntent],
    price_eps: float = 1e-9,
    *,
    dead_band: float = 0.0,
    cfg=None,
    hedge_asks: Optional[dict] = None,
) -> tuple[list[dict], list[QuoteIntent]]:
    """Split open orders + desired intents into (cancel, submit).

    An order resting within the keep tolerance of the desired price is kept.
    Orders on tokens we no longer quote are cancelled. An intent with no kept
    order near its price is submitted.

    TWO INDEPENDENT REASONS TO KEEP AN ORDER, and the tolerance is the larger
    of them so neither can silently disable the other:

      * `price_eps`, sub-tick venue rounding jitter. A rounding difference
        below a tick is not a price change and must not churn cancel+resubmit.
      * `dead_band`, the re-quote hysteresis. Every cancel+resubmit sends the
        order to the back of the queue at a new level, and on shadow run
        run-2809a7161de1 that happened 205 times out of 205 consecutive
        re-quotes -- median move 3.0c, median order lifetime 11.7s against a
        median queue_ahead of 1058.7 shares. Not fidgeting: the mid genuinely
        walked 0.815 -> 0.285 in 30 minutes and every re-quote answered a real
        book move. Answering it still cost the whole queue position, so the
        band trades a slightly stale price for time in the queue.

    THE RE-GATE. A kept order rests at its OWN price, up to `dead_band` away
    from the price `risk.hard_block` approved. Left unchecked, the band is a
    hole through that gate: a 3c-stale bid in a moving market can carry a
    completable cost 3c worse than anything the gate ever allowed. So a kept
    order is re-tested against `risk.completable_pair_block` at its own price
    and cancelled when it no longer passes. `cfg` and `hedge_asks` (token id ->
    the OTHER token's best ask) are what that test needs; without both, the
    re-gate stands down and the band behaves as a plain tolerance.

    A re-gated cancel drops the order out of the kept set, so this cycle's
    intent for that token IS submitted in its place. Cancelling without
    replacing would leave the market dark for a cycle on a price that is
    still quotable.
    """
    tolerance = max(float(price_eps), float(dead_band))

    wanted: dict[str, list[QuoteIntent]] = {}
    for i in intents:
        wanted.setdefault(i.token_id, []).append(i)

    kept: dict[str, list[dict]] = {}
    to_cancel: list[dict] = []
    for o in open_orders:
        tok = o["token_id"]
        targets = wanted.get(tok)
        if not targets or not any(
            abs(i.price - o["price"]) <= tolerance for i in targets
        ):
            to_cancel.append(o)
            continue
        if cfg is not None and hedge_asks is not None and risk.completable_pair_block(
                cfg, float(o["price"]), hedge_asks.get(tok)):
            to_cancel.append(o)
            continue
        kept.setdefault(tok, []).append(o)

    to_submit: list[QuoteIntent] = []
    for i in intents:
        sits = kept.get(i.token_id, [])
        if not any(abs(o["price"] - i.price) <= tolerance for o in sits):
            to_submit.append(i)

    return to_cancel, to_submit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trader_loop.py::TestPlanOrders -q`
Expected: PASS — the 5 original tests plus the 8 new ones.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: green. `plan_orders` still has one caller, still called with two arguments.

- [ ] **Step 6: Commit**

```bash
git add core_brain/trader_loop.py tests/test_trader_loop.py
git commit -m "feat(trader_loop): hold a quote through small moves, re-gate what is held"
```

---

### Task 5: Wire the dead band into the live and shadow loop

**Files:**
- Modify: `core_brain/trader_loop.py:446-447` (inside `_visit_one`)
- Test: `tests/test_trader_loop.py` (append a class)

**Interfaces:**
- Consumes: `plan_orders(..., dead_band=, cfg=, hedge_asks=)` from Task 4; `MarketEval.up_book` / `MarketEval.down_book` from `core_brain.quotes.evaluate_market_quote`.
- Produces: nothing new. `_visit_one` supplies the three arguments. `core_brain.shadow_run` drives `trader_loop.run` unchanged, so the shadow path picks this up with no edit of its own.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trader_loop.py`:

```python
class TestVisitOnePassesTheDeadBand:
    def test_a_two_cent_drift_does_not_churn_the_resting_order(self):
        """The whole point, end to end: same market, price moved 2c, no cancel."""
        seen = {}
        real_plan = plan_orders

        def spy(open_orders, intents, price_eps=1e-9, **kw):
            seen.update(kw)
            return real_plan(open_orders, intents, price_eps, **kw)

        market = FakeMarket()
        up = {"token_id": "tok-up", "best_bid": 0.50, "best_ask": 0.52,
              "bids": {0.50: 5000.0}, "asks": {0.52: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.46, "best_ask": 0.48,
                "bids": {0.46: 5000.0}, "asks": {0.48: 5000.0}}
        books = {"tok-up": up, "tok-dn": down}
        seam = VenueSeam(
            base_cfg=MakerConfig(requote_dead_band=0.03,
                                 max_completable_pair_cost=1.00),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: books[tok],
            decide=lambda *a, **k: ([_intent(price=0.49)], ""),
            open_orders_fn=lambda m: [_open(price=0.51)],
        )
        result = _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=False)

        assert seen["dead_band"] == 0.03
        assert seen["cfg"] is not None
        # The hedge ask for the UP token is the DOWN book's ask, and vice
        # versa. Getting this mapping backwards is the one way this wiring
        # can be wrong while every other assertion still passes.
        assert seen["hedge_asks"] == {"tok-up": 0.48, "tok-dn": 0.52}
        assert result.status in ("DRY_RUN", "DECLINED")
```

Import whatever the file does not already have: `_visit_one`, `VenueSeam`,
`plan_orders`, `MakerConfig`. Check `VenueSeam`'s real field names before
writing the constructor call — read `core_brain/trader_loop.py:140-175` and use
the names it defines, and drop any slot this test does not need if the dataclass
already defaults it.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trader_loop.py::TestVisitOnePassesTheDeadBand -q`
Expected: FAIL — `KeyError: 'dead_band'`, because `_visit_one` still calls `plan_orders` with two arguments.

- [ ] **Step 3: Write the implementation**

In `core_brain/trader_loop.py`, replace line 447:

```python
        to_cancel, to_submit = plan_orders(open_orders, intents)
```

with:

```python
        # The hedge ask for a token is the OTHER token's ask -- that is the
        # price a fill on this leg would have to pay to finish the pair.
        # `evaluate_market_quote` already fetched both books; re-reading them
        # here would be a second venue round-trip for a number we hold.
        hedge_asks = {
            ev.up_book.get("token_id"): ev.down_book.get("best_ask"),
            ev.down_book.get("token_id"): ev.up_book.get("best_ask"),
        }
        to_cancel, to_submit = plan_orders(
            open_orders, intents,
            dead_band=float(getattr(cfg, "requote_dead_band", 0.0)),
            cfg=cfg, hedge_asks=hedge_asks,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trader_loop.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: green. Read any failure in `tests/test_trader_loop.py`,
`tests/test_shadow_run.py` or `tests/test_live_funnel.py` carefully: a test that
asserted a cancel on a sub-3c move is now asserting the old behaviour, and the
assertion is what changes, not the band.

- [ ] **Step 6: Commit**

```bash
git add core_brain/trader_loop.py tests/test_trader_loop.py
git commit -m "feat(trader_loop): apply the configured dead band on the live and shadow path"
```

---

### Task 6: Documentation and the operator's verification block

**Files:**
- Modify: `docs/agents/strategy.md`
- Create: `docs/superpowers/plans/2026-08-25-completable-cost-gate-and-requote-dead-band-verify.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the "How to verify" block `AGENTS.md` requires before any change is reported done.

- [ ] **Step 1: Read the strategy doc and find the pricing section**

Run: `grep -n "max_pair_cost\|pair cost\|requote" docs/agents/strategy.md`

- [ ] **Step 2: Add both rules to the strategy doc**

Add a section documenting, in the doc's existing voice:
- what `max_completable_pair_cost` is, that it is `bid + hedge ask`, that it fires
  only when no hedge is held, and that it does not replace `max_pair_cost`;
- what `requote_dead_band` is, the measured suppression table (1c 0%, 2c 36%,
  3c 48%, 4c 58%, 5c 68%), and that kept orders are re-gated;
- the two env overrides, `HUNTER_COMPLETABLE_CAP` and `HUNTER_REQUOTE_DEAD_BAND`.

- [ ] **Step 3: Write the verification block**

Create the verify file with the exact commands and expected output:

```markdown
# How to verify — completable-cost gate and re-quote dead band

## 1. The tests

    python -m pytest -q

Expect green. The new behaviour is covered by:

    python -m pytest tests/test_completable_pair_gate.py tests/test_trader_loop.py -q

## 2. Confirm each test fails without the change

    git stash
    python -m pytest tests/test_completable_pair_gate.py -q   # expect collection error / failures
    git stash pop

## 3. Read the rules off the config

    python -c "from core_brain.config import load; c=load(); print(c.max_completable_pair_cost, c.enforce_completable_pair_cost, c.requote_dead_band)"

Expect: 1.0 True 0.03

## 4. Rehearse (OPERATOR ONLY — spends nothing, but it is yours to start)

    python -m core_brain.shadow_run --minutes 30

Then compare against run-2809a7161de1:

| what to look at | before | what the change should do |
| --- | --- | --- |
| median order lifetime | 11.7 s | roughly double, ~20-25 s |
| consecutive re-quotes that changed price | 205 of 205 | ~half as many re-quotes total |
| distinct `max_pair_cost_at_post` values | 1 (0.995) | unchanged; the new gate declines rather than stamps |
| orders posted | 209 | fewer -- wide markets are now declined |
| skip reasons mentioning "completable pair" | 0 | non-zero |

Fewer orders is the expected result, not a regression. The decision rule from the
spec: if the gate declines nearly everything, the problem is price levels and no
execution state machine will fix it.
```

- [ ] **Step 4: Commit**

```bash
git add docs/agents/strategy.md docs/superpowers/plans/2026-08-25-completable-cost-gate-and-requote-dead-band-verify.md
git commit -m "docs(strategy): document the completable-cost gate and the re-quote dead band"
```

---

## Self-Review

**Spec coverage**

| Spec requirement | Task |
| --- | --- |
| R1 completable-cost gate, `>=` cap at 1.00 | 1 (knob), 2 (predicate), 3 (gate on the final price) |
| R1 only when no hedge held | 3, `other_avg <= 0` |
| R1 no opinion on a missing ask | 2, `test_has_no_opinion_when_the_hedge_book_has_no_ask` |
| R1 switchable | 1, 2, 3 (`enforce_completable_pair_cost`) |
| R2 dead band, default 0.03, symmetric | 1 (knob), 4 (`dead_band`), 5 (wiring) |
| R2 independent of `price_eps` | 4, `test_the_larger_of_dead_band_and_price_eps_wins` |
| R3 re-gate kept orders | 4, `test_a_kept_order_is_cancelled_when_its_own_price_fails_the_gate` |
| R3 re-quote rather than go dark | 4, same test asserts `to_submit == [0.58]` |
| Non-goal: no taker state machine | nothing in this plan touches `single_buy_saver.py` |
| Non-goal: no TTL | not in this plan |
| Decision rule after re-run | Task 6 verify block |

**Placeholders:** none. Every code step carries the code; every test step carries the
test; every run step carries the command and the expected result. The two steps that
say "read the file first" (Task 5 step 1 on `VenueSeam`'s fields, Task 6 step 1 on the
strategy doc) name the exact line range or the exact grep.

**Type consistency:** `completable_pair_block(cfg, price, hedge_ask) -> Optional[str]`
is defined in Task 2 and called with that signature in Task 3 and Task 4.
`plan_orders`'s keyword-only `dead_band` / `cfg` / `hedge_asks` are defined in Task 4
and supplied in Task 5 under the same names. `hedge_asks` is `token_id -> ask of the
other token` in both the definition and the wiring, and Task 5 asserts the mapping
direction explicitly.

**One risk the plan cannot resolve from here:** whether any existing test asserts a
cancel on a sub-3c move. Task 5 step 5 is the point where that surfaces, and the
instruction there is explicit that the assertion changes rather than the band.
