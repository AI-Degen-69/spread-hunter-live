# Shadow Fill Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `python -m core_brain.shadow_run` rehearse the whole live lifecycle — decide, rest, fill, single buy, exit or merge, PnL — against the live book, writing only `data/shadow.db` and holding nothing it could sign with.

**Architecture:** Two new modules. `core_brain/shadow_fills.py` is a pure model: resting orders and tape volume in, credited fills out — no network, no SQLite, no clock. `core_brain/shadow_exec.py` owns the shadow store: it writes order rows for decided intents, applies credited fills, and exposes a `ShadowExecutionClient` giving `single_buy_saver` the four client methods it actually calls. `core_brain/shadow_run.py` wires both into the existing `VenueSeam`; no live module changes.

**Tech Stack:** Python 3.12, pytest, SQLite via `core_brain.order_registry.OrderRegistry`, venue reads via `core_brain.markets` (`full_book`, `recent_trades`).

**Spec:** [docs/superpowers/specs/2026-08-24-shadow-fill-simulation.md](../specs/2026-08-24-shadow-fill-simulation.md)

## Global Constraints

- Nothing in this plan may make a shadow run able to construct a signing client. The venue object stays `shadow_guard.ReadOnlyVenue` over an unauthenticated client.
- `assert_not_production_registry(db_path)` runs before anything is constructed. `data/orders.db` is refused as a store.
- Caps are the live caps: `MAX_ORDER_USD = 25.0`, `MAX_TOTAL_USD = 100.0` (`core_brain/venue.py`), `max_pair_cost` default `0.995` (`core_brain/config.py`).
- Every simulated row is labelled: `orders.order_id` starts `shadow-`, `fills.trade_id` starts `shadow-`, closes use `method='shadow_merge'`.
- `core_brain/live_fill_engine.py` is not modified, and no live module may import `core_brain.shadow_fills` or `core_brain.shadow_exec`.
- Python conventions: `from __future__ import annotations`, absolute imports, `snake_case` functions, `PascalCase` classes, narrow `except` clauses, domain-specific exceptions ([docs/agents/python-conventions.md](../../agents/python-conventions.md)).
- Vocabulary: **single buy**, **pair cost**, **merge**, **graduated**, **Trader**, **Order Manager**, **Market Filter** ([docs/agents/glossary.md](../../agents/glossary.md)).
- Tests are hermetic: `tests/conftest.py` blocks non-loopback sockets and refuses any connection to the production registry. No task may need the network.

## File Structure

| File | Responsibility |
| --- | --- |
| `core_brain/shadow_fills.py` (create) | Pure fill model: `ShadowRestingOrder`, `ShadowFill`, `queue_ahead_at`, `credit_fills` |
| `core_brain/shadow_exec.py` (create) | Shadow-store writers: `ensure_shadow_tables`, `record_submit`, `settle_market`, `record_shadow_merges`, `ShadowExecutionClient`, `shadow_positions` |
| `core_brain/shadow_run.py` (modify) | Wiring only: submit writes rows, settle wraps `inventory_fn`, the pairs pass runs on `sweep_fn` |
| `tests/test_shadow_fills.py` (create) | Model unit tests over fixture books and tape |
| `tests/test_shadow_exec.py` (create) | Store writer tests against a `tmp_path` registry |
| `tests/test_shadow_run.py` (modify) | Wiring tests: settle before decide, pairs pass gets the shadow client |
| `docs/agents/safety.md` (modify) | What a shadow run now simulates, and what it still does not |

---

### Task 1: The fill model

**Files:**
- Create: `core_brain/shadow_fills.py`
- Test: `tests/test_shadow_fills.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ShadowRestingOrder(local_id: str, token_id: str, price: float, size: float, filled: float = 0.0, queue_ahead: float = 0.0)` with a `remaining` property; `ShadowFill(local_id: str, token_id: str, price: float, size: float)`; `queue_ahead_at(book: dict, price: float) -> float`; `credit_fills(orders: list[ShadowRestingOrder], traded: dict[str, dict[float, float]]) -> tuple[list[ShadowFill], dict[str, float]]` where the second element maps `local_id -> remaining queue_ahead`.

- [ ] **Step 1: Write the failing tests**

```python
"""The shadow fill model: what a resting order would have got, and nothing more.

Conservative by construction. Only volume the tape confirms at the order's own
price can credit a fill; the book-only rule ("level emptied, credit the
remainder") reported a 50% fill rate against a tape-confirmed 3% in the paper
run -- see `core_brain/markets.py:recent_trades`.
"""
from __future__ import annotations

from core_brain.shadow_fills import (
    ShadowFill, ShadowRestingOrder, credit_fills, queue_ahead_at,
)


def _order(**kw):
    base = dict(local_id="ord-1", token_id="tok-up", price=0.47,
                size=100.0, filled=0.0, queue_ahead=0.0)
    base.update(kw)
    return ShadowRestingOrder(**base)


def test_queue_ahead_is_the_size_resting_at_our_own_price():
    book = {"bids": {0.48: 500.0, 0.47: 250.0, 0.46: 10.0}, "asks": {}}
    assert queue_ahead_at(book, 0.47) == 250.0


def test_queue_ahead_is_zero_when_no_one_rests_at_our_price():
    book = {"bids": {0.48: 500.0}, "asks": {}}
    assert queue_ahead_at(book, 0.47) == 0.0


def test_traded_volume_fills_the_queue_before_it_fills_us():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.47: 100.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 40.0)]
    assert queues["ord-1"] == 0.0


def test_volume_smaller_than_the_queue_credits_nothing():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.47: 25.0}})

    assert fills == []
    assert queues["ord-1"] == 35.0


def test_a_fill_never_exceeds_what_is_left_of_the_order():
    orders = [_order(size=100.0, filled=90.0)]
    fills, _ = credit_fills(orders, {"tok-up": {0.47: 500.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 10.0)]


def test_volume_at_another_price_or_token_credits_nothing():
    orders = [_order()]
    fills, _ = credit_fills(orders, {"tok-up": {0.46: 999.0},
                                     "tok-dn": {0.47: 999.0}})

    assert fills == []


def test_two_orders_at_one_price_share_the_volume_in_post_order():
    """Earlier order is earlier in the queue. Splitting evenly would credit the
    younger order volume the older one stood in front of."""
    orders = [_order(local_id="old", size=50.0),
              _order(local_id="new", size=50.0)]
    fills, _ = credit_fills(orders, {"tok-up": {0.47: 70.0}})

    assert fills == [ShadowFill("old", "tok-up", 0.47, 50.0),
                     ShadowFill("new", "tok-up", 0.47, 20.0)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shadow_fills.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core_brain.shadow_fills'`

- [ ] **Step 3: Write the implementation**

```python
"""The shadow fill model: tape-confirmed volume, queue position, nothing else.

Pure by design -- values in, values out, no clock, no socket, no SQLite. That is
what makes it testable against recorded books and tape, which is the only way to
tell an over-crediting model from an honest one.

This module belongs to the rehearsal and to nothing else.
`core_brain/live_fill_engine.py` carries the opposite rule and keeps it: live, a
fill exists only when the venue says so. Inferring one there would be the worst
failure available to this system.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShadowRestingOrder:
    """One simulated resting BUY, as the shadow store knows it."""
    local_id: str
    token_id: str
    price: float
    size: float
    filled: float = 0.0
    queue_ahead: float = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)


@dataclass(frozen=True)
class ShadowFill:
    """Volume credited to one resting order, at that order's own price."""
    local_id: str
    token_id: str
    price: float
    size: float


def queue_ahead_at(book: dict, price: float) -> float:
    """Size already resting at exactly `price` on the bids side.

    Better-priced bids sit ahead of the book, not ahead of this order at its own
    level: they trade against different volume, so counting them would delay
    every fill by depth that was never in this order's way.
    """
    bids = (book or {}).get("bids") or {}
    return float(bids.get(round(float(price), 4), 0.0))


def credit_fills(
    orders: list[ShadowRestingOrder],
    traded: dict[str, dict[float, float]],
) -> tuple[list[ShadowFill], dict[str, float]]:
    """Credit tape volume to resting orders, oldest first.

    `orders` arrives in post order, which is queue order at a price level.
    `traded` is `markets.recent_trades` output: token -> price -> volume since
    the last look. Volume consumes each order's remaining `queue_ahead` before
    any of it reaches the order itself.

    Returns the credited fills and every order's updated `queue_ahead`, so the
    caller can persist a queue that shrank without producing a fill -- forgetting
    that is how the same volume gets counted twice.
    """
    remaining_volume: dict[tuple[str, float], float] = {}
    for token_id, by_price in (traded or {}).items():
        for price, volume in (by_price or {}).items():
            key = (str(token_id), round(float(price), 4))
            remaining_volume[key] = remaining_volume.get(key, 0.0) + float(volume)

    fills: list[ShadowFill] = []
    queues: dict[str, float] = {}
    for o in orders:
        key = (str(o.token_id), round(float(o.price), 4))
        volume = remaining_volume.get(key, 0.0)
        queue = float(o.queue_ahead)

        consumed = min(volume, queue)
        queue -= consumed
        volume -= consumed

        credited = min(volume, o.remaining)
        if credited > 0:
            fills.append(ShadowFill(o.local_id, o.token_id, o.price, credited))
            volume -= credited

        remaining_volume[key] = volume
        queues[o.local_id] = queue

    return fills, queues
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_shadow_fills.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add core_brain/shadow_fills.py tests/test_shadow_fills.py
git commit -m "feat(core_brain): add the shadow fill model"
```

---

### Task 2: Decided intents rest as order rows

**Files:**
- Create: `core_brain/shadow_exec.py`
- Test: `tests/test_shadow_exec.py`

**Interfaces:**
- Consumes: `queue_ahead_at` (Task 1).
- Produces: `ShadowOrderRefused(RuntimeError)`; `ensure_shadow_tables(db_path) -> None`; `write_queue_ahead(db_path, local_id, queue_ahead) -> None`; `read_queue_ahead(db_path, local_id) -> float`; `record_submit(client, registry, market, intents, cfg, *, db_path, book_fn, clob_host="https://clob.polymarket.com", now_fn=time.time) -> int`. The first five parameters match the seam's `submit_fn(client, registry, market, intents, cfg)`; the rest are bound by the wiring in Task 4.

- [ ] **Step 1: Write the failing tests**

```python
"""Shadow submit: the decided couple rests in the shadow store, and only there."""
from __future__ import annotations

import pytest

from core_brain.order_registry import OrderRegistry, init_db
from core_brain.quotes import QuoteIntent


class FakeMarket:
    condition_id = "0xabc"
    up_token = "tok-up"
    down_token = "tok-dn"
    market_slug = "fake-market"
    tick_size = 0.01
    neg_risk = False


def _cfg():
    from core_brain.config import load
    return load()


def _intents():
    return [
        QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=20),
    ]


def _books(_clob_host, token_id):
    return {"token_id": token_id, "bids": {0.47: 300.0, 0.51: 80.0},
            "asks": {}, "best_bid": 0.47, "best_ask": None}


@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    return OrderRegistry(db_path=db), db


def test_each_leg_becomes_an_open_order_row_labelled_shadow(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    placed = record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                           db_path=db, book_fn=_books)

    assert placed == 2
    rows = reg.get_active_orders()
    assert {r.token_id for r in rows} == {"tok-up", "tok-dn"}
    assert all(r.status == "open" for r in rows)
    assert all((r.order_id or "").startswith("shadow-") for r in rows)


def test_both_legs_share_one_pair_id(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=_books)

    pair_ids = {r.pair_id for r in reg.get_active_orders()}
    assert len(pair_ids) == 1
    assert pair_ids.pop().startswith("pair-")


def test_queue_position_is_captured_from_the_book_at_post_time(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead, record_submit,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=_books)

    by_token = {r.token_id: r.id for r in reg.get_active_orders()}
    assert read_queue_ahead(db, by_token["tok-up"]) == 300.0
    assert read_queue_ahead(db, by_token["tok-dn"]) == 80.0


def test_a_leg_over_max_order_usd_is_refused_before_any_row_is_written(registry):
    from core_brain.shadow_exec import (
        ShadowOrderRefused, ensure_shadow_tables, record_submit,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    huge = [QuoteIntent(side="UP", token_id="tok-up", price=0.90, size=1000)]

    with pytest.raises(ShadowOrderRefused, match="MAX_ORDER_USD"):
        record_submit(object(), reg, FakeMarket(), huge, _cfg(),
                      db_path=db, book_fn=_books)

    assert reg.get_active_orders() == []


def test_no_intents_writes_nothing(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    assert record_submit(object(), reg, FakeMarket(), [], _cfg(),
                         db_path=db, book_fn=_books) == 0
    assert reg.get_active_orders() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shadow_exec.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core_brain.shadow_exec'`

- [ ] **Step 3: Write the implementation**

```python
"""The shadow store's writers: rest, fill, and close, without a venue.

Everything here writes `data/shadow.db` through the same `OrderRegistry` the
live path uses, so the rows downstream stages read have the shape those stages
expect. What differs is where the facts come from -- a model instead of a venue
-- and every row says so: order ids start `shadow-`, trade ids start `shadow-`,
closes carry a `shadow_` method.

`core_brain/shadow_guard.py` keeps this honest from the other side: the venue
object a shadow run holds cannot sign, and `data/orders.db` is refused as a
store before any of these functions can be reached.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Callable

from core_brain.order_registry import (
    FillRecord, OrderRecord, OrderRegistry, get_connection,
)
from core_brain.shadow_fills import ShadowRestingOrder, queue_ahead_at

SHADOW_ORDER_PREFIX = "shadow-"
SHADOW_TRADE_PREFIX = "shadow-"


class ShadowOrderRefused(RuntimeError):
    """A simulated order broke a live cap and was not written."""


def ensure_shadow_tables(db_path: Path | str) -> None:
    """Create the queue-position table beside the registry's own schema.

    Queue position is a property of the model, not of the venue, so it does not
    belong in `orders`. Keeping it in its own table also means a shadow store
    opened by live code reads as a registry with some odd ids, never as a
    registry with columns that do not exist.
    """
    with get_connection(Path(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_queue (
                local_id TEXT PRIMARY KEY,
                queue_ahead REAL NOT NULL
            )
            """
        )


def write_queue_ahead(db_path: Path | str, local_id: str, queue_ahead: float) -> None:
    with get_connection(Path(db_path)) as conn:
        conn.execute(
            "INSERT INTO shadow_queue (local_id, queue_ahead) VALUES (?, ?) "
            "ON CONFLICT(local_id) DO UPDATE SET queue_ahead = excluded.queue_ahead",
            (local_id, float(queue_ahead)),
        )


def read_queue_ahead(db_path: Path | str, local_id: str) -> float:
    with get_connection(Path(db_path)) as conn:
        row = conn.execute(
            "SELECT queue_ahead FROM shadow_queue WHERE local_id = ?", (local_id,)
        ).fetchone()
    return float(row["queue_ahead"]) if row else 0.0


def record_submit(
    _client,
    registry: OrderRegistry,
    market,
    intents: list,
    cfg,
    *,
    db_path: Path | str,
    book_fn: Callable[[str, str], dict],
    clob_host: str = "https://clob.polymarket.com",
    now_fn: Callable[[], float] = time.time,
) -> int:
    """Rest each decided intent as an order row, under the live caps.

    Mirrors `core_brain/trader_loop.py:_submit_intents` in everything that is
    not the venue call: the same per-leg `MAX_ORDER_USD` check, the same shared
    `pair_id`, the same `max_pair_cost_at_post` stamp. A rehearsal under looser
    caps rehearses something we do not ship.
    """
    from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD

    if not intents:
        return 0

    for i in intents:
        if i.price * i.size > MAX_ORDER_USD:
            raise ShadowOrderRefused(
                f"leg {i.side} ${i.price * i.size:.2f} exceeds "
                f"MAX_ORDER_USD ${MAX_ORDER_USD:.2f}")

    open_usd = sum(o.price * o.original_size for o in registry.get_active_orders())
    total_cost = sum(i.price * i.size for i in intents)
    if open_usd + total_cost > MAX_TOTAL_USD:
        raise ShadowOrderRefused(
            f"open ${open_usd:.2f} + ${total_cost:.2f} exceeds "
            f"MAX_TOTAL_USD ${MAX_TOTAL_USD:.2f}")

    now_ms = int(now_fn() * 1000)
    pair_id = f"pair-{uuid.uuid4().hex[:12]}"
    max_pair_cost = float(getattr(cfg, "max_pair_cost", 0.995))

    placed = 0
    for i in intents:
        local_id = str(uuid.uuid4())
        registry.create_order(OrderRecord(
            id=local_id,
            order_id=f"{SHADOW_ORDER_PREFIX}{uuid.uuid4().hex[:12]}",
            condition_id=market.condition_id, token_id=str(i.token_id),
            side="BUY", price=i.price, original_size=i.size, status="open",
            posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id,
            max_pair_cost_at_post=max_pair_cost,
        ))
        try:
            book = book_fn(clob_host, str(i.token_id))
        except (OSError, ValueError) as e:
            # Degrade, do not stop. An unread book means the queue is unknown,
            # and a queue of zero is the optimistic direction, so assume a level
            # as deep as this order instead: that never over-credits.
            book = {"bids": {round(float(i.price), 4): float(i.size)}}
        write_queue_ahead(db_path, local_id, queue_ahead_at(book, i.price))
        placed += 1

    return placed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_shadow_exec.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add core_brain/shadow_exec.py tests/test_shadow_exec.py
git commit -m "feat(core_brain): rest shadow intents as order rows"
```

---

### Task 3: Credited fills reach the registry

**Files:**
- Modify: `core_brain/shadow_exec.py`
- Test: `tests/test_shadow_exec.py`

**Interfaces:**
- Consumes: `credit_fills`, `ShadowRestingOrder` (Task 1); `read_queue_ahead`, `write_queue_ahead` (Task 2).
- Produces: `settle_market(registry, market, *, db_path, traded_fn, seen, now_fn=time.time) -> list[ShadowFill]`. Reads the market's resting rows, credits fills, writes `fills` rows with `trade_id` of the form `shadow-<uuid4 hex 16>`, and moves each order row to `partial` or `filled`.

- [ ] **Step 1: Write the failing tests**

```python
def test_settle_writes_a_fill_row_and_marks_the_order_filled(registry):
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    fills = settle_market(
        reg, FakeMarket(), db_path=db, seen=set(),
        traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
    )

    assert [f.token_id for f in fills] == ["tok-up"]
    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == 20.0
    assert inv.down_shares == 0.0
    assert [r.token_id for r in reg.get_active_orders()] == ["tok-dn"]


def test_partial_credit_leaves_the_order_partial(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 5.0}})

    up_row = next(r for r in reg.get_active_orders() if r.token_id == "tok-up")
    assert up_row.status == "partial"


def test_the_same_tape_volume_is_never_credited_twice(registry):
    """`recent_trades` de-duplicates by trade identity through `seen`; the
    settle step carries one `seen` set per market for the whole session."""
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    calls = {"n": 0}

    def traded_fn(cid, seen):
        calls["n"] += 1
        return {"tok-up": {0.47: 8.0}} if calls["n"] == 1 else {}

    seen = set()
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=traded_fn)
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=traded_fn)

    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == 8.0


def test_a_shrinking_queue_without_a_fill_is_remembered(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db,
                  book_fn=lambda h, t: {"bids": {0.47: 50.0, 0.51: 0.0}})

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 30.0}})

    up_id = next(r.id for r in reg.get_active_orders() if r.token_id == "tok-up")
    assert read_queue_ahead(db, up_id) == 20.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shadow_exec.py -q`
Expected: FAIL — `ImportError: cannot import name 'settle_market'`

- [ ] **Step 3: Write the implementation**

Append to `core_brain/shadow_exec.py`:

```python
def _filled_size(db_path: Path | str, local_id: str) -> float:
    with get_connection(Path(db_path)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS s FROM fills WHERE order_uuid = ?",
            (local_id,),
        ).fetchone()
    return float(row["s"]) if row else 0.0


def settle_market(
    registry: OrderRegistry,
    market,
    *,
    db_path: Path | str,
    traded_fn: Callable[[str, set], dict],
    seen: set,
    now_fn: Callable[[], float] = time.time,
) -> list:
    """Credit this market's resting rows from the tape, then persist the result.

    One `seen` set per market for the whole session: `markets.recent_trades`
    de-duplicates by trade identity against it, and a fresh set every cycle
    would re-credit the same trades until the order looked full.
    """
    from core_brain.shadow_fills import credit_fills

    resting = [
        o for o in registry.get_active_orders()
        if o.condition_id == market.condition_id and o.status in ("open", "partial")
    ]
    if not resting:
        return []

    traded = traded_fn(market.condition_id, seen)

    orders = [
        ShadowRestingOrder(
            local_id=o.id, token_id=o.token_id, price=o.price,
            size=o.original_size, filled=_filled_size(db_path, o.id),
            queue_ahead=read_queue_ahead(db_path, o.id),
        )
        for o in resting
    ]
    fills, queues = credit_fills(orders, traded)

    for local_id, queue_ahead in queues.items():
        write_queue_ahead(db_path, local_id, queue_ahead)

    now_ms = int(now_fn() * 1000)
    by_id = {o.local_id: o for o in orders}
    for f in fills:
        registry.record_fill(FillRecord(
            trade_id=f"{SHADOW_TRADE_PREFIX}{uuid.uuid4().hex[:16]}",
            order_uuid=f.local_id, size=f.size, price=f.price,
            venue_ts=now_ms, recorded_ts=now_ms,
        ))
        order = by_id[f.local_id]
        total_filled = order.filled + f.size
        status = "filled" if total_filled >= order.size - 1e-9 else "partial"
        registry.update_order_status(f.local_id, status=status,
                                     last_polled_ts=now_ms)

    return fills
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_shadow_exec.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add core_brain/shadow_exec.py tests/test_shadow_exec.py
git commit -m "feat(core_brain): credit shadow fills into the shadow registry"
```

---

### Task 4: Wire rest and settle into the shadow seam

**Files:**
- Modify: `core_brain/shadow_run.py` (`build_shadow_seam`, around lines 126-230)
- Test: `tests/test_shadow_run.py`

**Interfaces:**
- Consumes: `ensure_shadow_tables`, `record_submit` (Task 2), `settle_market` (Task 3).
- Produces: `build_shadow_seam(..., traded_fn: Optional[Callable[[str, set], dict]] = None)`, and `_default_traded_fn() -> Callable[[str, set], dict]`. No other new public names.

**Why `inventory_fn`:** `trader_loop.run` calls it once per market visit, immediately before `decide`. Wrapping it runs the settle step inside the live rotation without editing `core_brain/trader_loop.py` — the same reasoning that drives the time box entirely from the injected `sleep_fn`.

- [ ] **Step 1: Write the failing tests**

```python
class TestSettleWiring:
    """Rest, then fill, then decide -- in that order, inside one visit."""

    def test_the_seam_settles_before_it_reports_inventory(self, tmp_path):
        from core_brain.shadow_run import build_shadow_seam

        seen_markets = []
        seam = build_shadow_seam(
            db_path=tmp_path / "shadow.db",
            client_fn=lambda: object(),
            traded_fn=lambda cid, seen: seen_markets.append(cid) or {},
        )
        seam.inventory_fn(FakeMarket("0xabc"))

        assert seen_markets == ["0xabc"]

    def test_submitted_intents_become_rows_in_the_shadow_store(self, tmp_path):
        from core_brain.order_registry import OrderRegistry
        from core_brain.shadow_run import build_shadow_seam

        db = tmp_path / "shadow.db"
        seam = build_shadow_seam(
            db_path=db, client_fn=lambda: object(),
            fetch_books=lambda h, t: {"bids": {0.47: 10.0}},
        )
        placed = seam.submit_fn(
            seam.client, seam.registry, FakeMarket("0xabc"),
            [QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20)],
            seam.base_cfg,
        )

        assert placed == 1
        assert len(OrderRegistry(db_path=db).get_active_orders()) == 1

    def test_the_production_registry_is_still_refused(self):
        """The new writers must not have moved the guard off the front door."""
        from core_brain.order_registry import DEFAULT_DB_PATH
        from core_brain.shadow_guard import ShadowSafetyViolation
        from core_brain.shadow_run import build_shadow_seam

        with pytest.raises(ShadowSafetyViolation):
            build_shadow_seam(db_path=DEFAULT_DB_PATH)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shadow_run.py -q -k SettleWiring`
Expected: FAIL — `TypeError: build_shadow_seam() got an unexpected keyword argument 'traded_fn'`

- [ ] **Step 3: Write the implementation**

Add `import sqlite3` to the module imports. Add the parameter and replace the two ports in `build_shadow_seam`:

```python
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    ensure_shadow_tables(db_path)

    resolved_traded_fn = traded_fn or _default_traded_fn()
    seen_by_market: dict[str, set] = {}
    base_inventory_fn = _make_inventory_fn(registry, Path(db_path))

    def settling_inventory_fn(market):
        """Settle this market, then report what it now holds.

        Order matters: a decision taken against a pre-settle inventory is a
        decision taken against a position the market has already left.
        """
        seen = seen_by_market.setdefault(market.condition_id, set())
        try:
            settle_market(registry, market, db_path=db_path,
                          traded_fn=resolved_traded_fn, seen=seen)
        except (sqlite3.Error, OSError, ValueError) as e:
            # Degrade, do not stop: an unsettled visit decides against a stale
            # inventory, which the next visit corrects. Raising here would take
            # the market out of the rotation entirely.
            log.warning("settle failed for %s: %s", market.condition_id[:12], e)
        return base_inventory_fn(market)

    def shadow_submit(client, reg, market, intents, cfg_in) -> int:
        for qi in intents:
            intents_sink.append(ShadowIntent.from_quote(qi, market.condition_id))
        return record_submit(client, reg, market, intents, cfg_in,
                             db_path=db_path, book_fn=shadow_fetch_books)
```

Pass `submit_fn=shadow_submit` and `inventory_fn=settling_inventory_fn` to the returned `VenueSeam`, and add beside `_default_fetch_books`:

```python
def _default_traded_fn() -> Callable[[str, set], dict]:
    """The live tape reader. Public endpoint, no credentials, no signer."""
    def traded(condition_id: str, seen: set) -> dict:
        from core_brain.markets import recent_trades
        return recent_trades(condition_id, seen)

    return traded
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS. `test_decided_intents_are_recorded_and_never_submitted` still passes: `intents_sink` is still filled, now beside the rows.

- [ ] **Step 5: Commit**

```bash
git add core_brain/shadow_run.py tests/test_shadow_run.py
git commit -m "feat(core_brain): settle each market before it decides in shadow mode"
```

---

### Task 5: The single buy pass runs against the shadow store

**Files:**
- Modify: `core_brain/shadow_exec.py`
- Modify: `core_brain/shadow_run.py` (`run_shadow`)
- Test: `tests/test_shadow_exec.py`

**Interfaces:**
- Consumes: `settle_market` (Task 3), `_filled_size` (Task 3).
- Produces: `ShadowExecutionClient(registry, db_path, *, book_fn, clob_host=...)` with exactly the four methods `single_buy_saver` calls — `get_order_book(token_id)`, `get_order(order_id)`, `cancel_order(payload)`, `create_and_post_market_order(**kwargs)` — and `shadow_positions(registry, db_path) -> dict[str, float]`, shaped like `single_buy_saver.fetch_positions` output (token id -> shares).

**Why a shim and not the read-only proxy:** `shadow_guard.ReadOnlyVenue` raises `ShadowSafetyViolation`, a `BaseException`, on any method outside the vetted reads. Handing it to `auto_manage_pairs` would abort the rehearsal the first time a single buy needed completing — the exact case the operator wants to watch.

- [ ] **Step 1: Write the failing test**

```python
def test_a_single_buy_is_completed_against_the_shadow_store(registry):
    """One leg filled, the other not: the pairs pass completes it, and the
    completing buy lands as a shadow fill rather than a venue order."""
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market, shadow_positions,
    )
    from core_brain.single_buy_saver import auto_manage_pairs

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    client = ShadowExecutionClient(
        reg, db,
        book_fn=lambda h, t: {"token_id": t, "bids": {0.50: 500.0},
                              "asks": {0.51: 500.0}, "best_bid": 0.50,
                              "best_ask": 0.51},
    )
    results = auto_manage_pairs(
        client, reg, _cfg(), venue_positions=shadow_positions(reg, db),
    )

    assert any(r.get("action") in ("completed", "exited") for r in results), results
    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.down_shares > 0 or inv.up_shares == 0


def test_the_shim_refuses_a_method_it_does_not_implement(registry):
    """A silent no-op for an unimplemented SDK call would make a rehearsal look
    successful where the live path would have done something."""
    from core_brain.shadow_exec import ShadowExecutionClient

    reg, db = registry
    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {})

    with pytest.raises(AttributeError):
        client.post_orders([])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_shadow_exec.py -q -k "single_buy or shim"`
Expected: FAIL — `ImportError: cannot import name 'ShadowExecutionClient'`

- [ ] **Step 3: Write the implementation**

Append to `core_brain/shadow_exec.py`:

```python
class ShadowExecutionClient:
    """The four client methods `single_buy_saver` calls, backed by the store.

    Deliberately narrow: an SDK method this class does not implement raises
    `AttributeError` at the call site, which is a loud failure rather than a
    silent no-op that makes a rehearsal look successful.

    A market order here is a fill, immediately, at the book's best ask. That is
    the optimistic end of what a taker gets, and it is stated rather than
    hidden: a completion that clears in a rehearsal is not evidence that the
    same completion clears live.
    """

    def __init__(self, registry: OrderRegistry, db_path: Path | str, *,
                 book_fn: Callable[[str, str], dict],
                 clob_host: str = "https://clob.polymarket.com") -> None:
        self._registry = registry
        self._db_path = Path(db_path)
        self._book_fn = book_fn
        self._clob_host = clob_host

    def get_order_book(self, token_id: str) -> dict:
        return self._book_fn(self._clob_host, str(token_id))

    def get_order(self, order_id: str) -> dict:
        for o in self._registry.get_active_orders():
            if o.order_id == order_id or o.id == order_id:
                return {"id": o.order_id or o.id, "status": o.status,
                        "size_matched": _filled_size(self._db_path, o.id),
                        "original_size": o.original_size, "price": o.price}
        return {}

    def cancel_order(self, payload) -> dict:
        target = (getattr(payload, "orderID", None)
                  or getattr(payload, "order_id", None))
        now_ms = int(time.time() * 1000)
        for o in self._registry.get_active_orders():
            if o.order_id == target or o.id == target:
                self._registry.update_order_status(
                    o.id, status="cancelled", last_polled_ts=now_ms)
                return {"success": True, "orderID": target}
        return {"success": False, "orderID": target}

    def create_and_post_market_order(self, *args, **kwargs) -> dict:
        """Cross the book in the rehearsal: one order row, one fill, at once."""
        token_id = str(kwargs.get("token_id") or kwargs.get("asset_id") or "")
        size = float(kwargs.get("size") or kwargs.get("amount") or 0.0)
        condition_id = str(kwargs.get("condition_id") or "")
        book = self._book_fn(self._clob_host, token_id)
        price = book.get("best_ask")
        if price is None or size <= 0:
            raise ShadowOrderRefused(
                f"cannot cross {token_id[:12]} for {size}: no ask in the book")

        now_ms = int(time.time() * 1000)
        local_id = str(uuid.uuid4())
        self._registry.create_order(OrderRecord(
            id=local_id, order_id=f"{SHADOW_ORDER_PREFIX}{uuid.uuid4().hex[:12]}",
            condition_id=condition_id, token_id=token_id, side="BUY",
            price=float(price), original_size=size, status="filled",
            posted_ts=now_ms, last_polled_ts=now_ms,
            pair_id=kwargs.get("pair_id"),
        ))
        self._registry.record_fill(FillRecord(
            trade_id=f"{SHADOW_TRADE_PREFIX}{uuid.uuid4().hex[:16]}",
            order_uuid=local_id, size=size, price=float(price),
            venue_ts=now_ms, recorded_ts=now_ms,
        ))
        return {"success": True, "orderID": local_id, "status": "matched",
                "price": float(price), "size": size}


def shadow_positions(registry: OrderRegistry, db_path: Path | str) -> dict[str, float]:
    """Positions as the shadow store knows them, shaped like `fetch_positions`.

    Passed to `auto_manage_pairs` explicitly so it never reaches for the Data
    API. That read fails closed by design, and failing closed on every cycle
    would keep the pass from ever running in a rehearsal.
    """
    out: dict[str, float] = {}
    with get_connection(Path(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT o.token_id AS token_id, COALESCE(SUM(f.size), 0) AS shares
            FROM orders o LEFT JOIN fills f ON f.order_uuid = o.id
            GROUP BY o.token_id
            """
        ).fetchall()
    for r in rows:
        shares = float(r["shares"])
        if shares > 0:
            out[str(r["token_id"])] = shares
    return out
```

In `core_brain/shadow_run.py:run_shadow`, run the pass once per rotation on `sweep_fn` — the seam port `trader_loop.run` already calls once per cycle, currently a no-op:

```python
    def shadow_sweep() -> None:
        """The Order Manager's U35 pass, rehearsed. Closing actions only."""
        from core_brain.shadow_exec import ShadowExecutionClient, shadow_positions
        from core_brain.single_buy_saver import auto_manage_pairs

        exec_client = ShadowExecutionClient(
            seam.registry, db_path, book_fn=_default_fetch_books())
        try:
            for pr in auto_manage_pairs(
                exec_client, seam.registry, cfg,
                venue_positions=shadow_positions(seam.registry, db_path),
            ):
                action = pr.get("action", "?")
                if action not in ("hold", "balanced"):
                    log.info("pairs %s %s", pr.get("pair_id") or "?", action)
        except (sqlite3.Error, OSError, ValueError) as e:
            log.warning("shadow pairs pass failed: %s", e)

    seam.sweep_fn = shadow_sweep
```

Change `ShadowResult.skipped_stages` at the end of `run_shadow` to `("reconcile",)`, and update the existing `test_reconcile_and_sweep_are_skipped_not_silently_passed` to assert reconcile only, with a comment naming why sweep left the list: reporting a stage as skipped when it ran is the same lie in the other direction.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_shadow_exec.py tests/test_shadow_run.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core_brain/shadow_exec.py core_brain/shadow_run.py tests/test_shadow_exec.py tests/test_shadow_run.py
git commit -m "feat(core_brain): run the single buy pass inside a shadow run"
```

---

### Task 6: A balanced pair records a merge close

**Files:**
- Modify: `core_brain/shadow_exec.py`
- Modify: `core_brain/shadow_run.py` (`shadow_sweep`)
- Test: `tests/test_shadow_exec.py`

**Interfaces:**
- Consumes: `settle_market` (Task 3).
- Produces: `record_shadow_merges(registry, db_path, *, now_fn=time.time) -> list[str]`, returning the `pair_id`s closed.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_balanced_pair_is_merged_into_one_dollar_per_share(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}})

    merged = record_shadow_merges(reg, db)

    assert len(merged) == 1
    close = reg.get_all_closes()[0]
    assert close["method"] == "shadow_merge"
    assert close["shares"] == 20.0
    assert round(close["proceeds"], 2) == 20.00
    assert round(close["cost_basis"], 2) == round(20 * (0.47 + 0.51), 2)
    assert round(close["realized_pnl"], 2) == round(20 * (1.0 - 0.98), 2)


def test_an_unbalanced_pair_is_left_alone(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    assert record_shadow_merges(reg, db) == []
    assert reg.get_all_closes() == []


def test_a_merged_pair_is_not_merged_again(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}})

    assert len(record_shadow_merges(reg, db)) == 1
    assert record_shadow_merges(reg, db) == []
    assert len(reg.get_all_closes()) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shadow_exec.py -q -k merge`
Expected: FAIL — `ImportError: cannot import name 'record_shadow_merges'`

- [ ] **Step 3: Write the implementation**

```python
def record_shadow_merges(
    registry: OrderRegistry,
    db_path: Path | str,
    *,
    now_fn: Callable[[], float] = time.time,
) -> list[str]:
    """Close every balanced pair at exactly $1.00 a share.

    The merge itself is a signed on-chain transaction and does not happen here:
    a shadow run has no key. What is recorded is its arithmetic, which is the
    part a rehearsal can honestly reproduce -- a merged pair pays $1.00, so the
    result is `shares * (1.00 - pair cost)`, and `method='shadow_merge'` says
    where the row came from.
    """
    from core_brain.order_registry import CloseRecord

    with get_connection(Path(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT o.pair_id AS pair_id, o.condition_id AS condition_id,
                   o.token_id AS token_id,
                   COALESCE(SUM(f.size), 0) AS shares,
                   COALESCE(SUM(f.size * f.price), 0) AS cost
            FROM orders o JOIN fills f ON f.order_uuid = o.id
            WHERE o.pair_id IS NOT NULL
              AND o.pair_id NOT IN (
                  SELECT COALESCE(tx_hash, '') FROM closes
                   WHERE method = 'shadow_merge')
            GROUP BY o.pair_id, o.token_id
            """
        ).fetchall()

    by_pair: dict[str, list] = {}
    for r in rows:
        by_pair.setdefault(str(r["pair_id"]), []).append(r)

    merged: list[str] = []
    for pair_id, legs in by_pair.items():
        if len(legs) != 2:
            continue
        shares = min(float(leg["shares"]) for leg in legs)
        if shares <= 0:
            continue
        cost_basis = sum(
            float(leg["cost"]) * (shares / float(leg["shares"])) for leg in legs
        )
        proceeds = shares * 1.0
        registry.log_close(CloseRecord(
            ts=now_fn(), condition_id=str(legs[0]["condition_id"]),
            method="shadow_merge", shares=shares, cost_basis=cost_basis,
            proceeds=proceeds, fee=0.0, gas=0.0,
            realized_pnl=proceeds - cost_basis,
            # The pair id rides in tx_hash: it is the natural idempotency key
            # here, and a shadow close has no transaction to name.
            tx_hash=pair_id,
        ))
        merged.append(pair_id)

    return merged
```

Call it at the end of `shadow_sweep` in `core_brain/shadow_run.py`:

```python
        for pair_id in record_shadow_merges(seam.registry, db_path):
            log.info("merged %s at $1.00 a share", pair_id)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_shadow_exec.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core_brain/shadow_exec.py core_brain/shadow_run.py tests/test_shadow_exec.py
git commit -m "feat(core_brain): record a shadow merge for every balanced pair"
```

---

### Task 7: The boundary guard, the log, and the docs

**Files:**
- Modify: `core_brain/shadow_run.py` (`_make_logging_emit`)
- Modify: `docs/agents/safety.md`
- Test: `tests/test_shadow_run.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_live_module_imports_the_shadow_model():
    """The live fill engine's invariant is that a fill comes only from the
    venue. The shadow model infers one. The two must never meet, and the cheap
    way to keep that true is to check that nobody imports across the line."""
    from pathlib import Path

    core = Path(__file__).resolve().parent.parent / "core_brain"
    live_modules = [
        p for p in core.glob("*.py")
        if p.name not in {"shadow_run.py", "shadow_exec.py",
                          "shadow_fills.py", "shadow_guard.py"}
    ]
    offenders = [
        p.name for p in live_modules
        if "shadow_fills" in p.read_text(encoding="utf-8")
        or "shadow_exec" in p.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_a_decision_is_logged_with_the_position_behind_it(tmp_path, caplog):
    import logging

    from core_brain.shadow_run import _make_logging_emit

    emit = _make_logging_emit(tmp_path / "shadow.db")
    with caplog.at_level(logging.INFO, logger="shadow_run"):
        emit(9, "quoting", "decide", market_slug="dota-2026",
             reason="pair cost 0.98",
             extra={"intent_count": 2, "up_shares": 20.0, "down_shares": 0.0})

    assert "up=20" in caplog.text
    assert "down=0" in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shadow_run.py -q -k "no_live_module or position_behind_it"`
Expected: the position test FAILs (no position in the line); the boundary test passes and stays as a regression guard.

- [ ] **Step 3: Write the implementation**

In `_make_logging_emit`, extend the line when the extra carries inventory:

```python
        shares = ""
        if "up_shares" in extra or "down_shares" in extra:
            shares = (f" up={float(extra.get('up_shares', 0)):g}"
                      f" down={float(extra.get('down_shares', 0)):g}")
        log.info(
            "cycle=%s %s intents=%d%s%s",
            cycle,
            kw.get("market_slug") or extra.get("condition_id") or "?",
            count,
            shares,
            f" -- {reason}" if reason else "",
        )
```

In `docs/agents/safety.md`, replace the "What a shadow view shows" paragraph with what is now true: the run rests simulated orders, credits fills from the tape, runs the single buy pass and records merges, all inside `data/shadow.db`; the merge is arithmetic, not an on-chain transaction; fills are modelled, so a fill rate out of a rehearsal is a model output and never a measurement.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Verify against the live venue, then commit**

```bash
python -m core_brain.shadow_run --minutes 10 --interval 5 --max-markets 3 --db data/shadow.db
```

Expected: per-visit lines whose `up=`/`down=` counts change, and at least one `pairs ... completed` or `merged pair-... at $1.00 a share`. Then:

```bash
git add core_brain/shadow_run.py docs/agents/safety.md tests/test_shadow_run.py
git commit -m "docs(core_brain): say what a shadow run now simulates"
```

---

## How to verify (operator)

```powershell
python -m pytest -q
python -m dashboard.server --db data\shadow.db --port 8799
python -m core_brain.shadow_run --minutes 15 --interval 5 --max-markets 3
```

1. The terminal shows per-visit lines whose `up=`/`down=` counts change after a fill.
2. The dashboard, under its SHADOW badge, shows open orders, then fills, then a pair.
3. PERFORMANCE & ANALYTICS moves: total fills above zero, realized PnL from merges.
4. `data/orders.db` is untouched — compare its modification time before and after.
5. Every simulated row carries its label:

```powershell
python -c "import sqlite3;c=sqlite3.connect('file:data/shadow.db?mode=ro',uri=True);print(c.execute(\"select count(*) from orders where order_id not like 'shadow-%'\").fetchone(), c.execute(\"select count(*) from fills where trade_id not like 'shadow-%'\").fetchone())"
```

Expected: `(0,) (0,)`.
