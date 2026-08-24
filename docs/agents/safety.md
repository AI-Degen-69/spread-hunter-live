# Safety rails

This repository places real orders with real money. Read this before running anything.

## 1. LIVE is the default

`python -m core_brain.order_manager` reaches the venue on every subcommand. `--no-live`
gives a dry-run preview. There is no separate staging venue.

## 2. Closing commands are pre-approved

`exit`, `merge`, `redeem`, `cancel`, `cancel-market`, `cancel-all` reduce exposure. An
agent may run them without asking.

Cancelling pulls **resting** orders only. A leg that already filled is still open
exposure — that needs `complete` (supervised) then `merge`, or `exit`.

## 3. Opening commands require explicit supervision

Four things spend money:

- `quote` — rests new bids.
- `complete` — buys the missing side of a single buy. It removes risk, but it does so by
  spending, so it belongs here and not with the closing commands.
- The Trader loop (`core_brain.trader_loop --live`).
- The dashboard's **START** button, which calls `start_bot()` and launches the Trader. A
  click on the dashboard is a live order path exactly like a typed command.

Propose the command; the operator runs it. An agent runs one only when the operator says
so in that session.

## 3a. Shadow run is the one loop command an agent may run

`python -m core_brain.shadow_run --minutes N` rehearses the full loop against the live
book and is pre-approved, unlike every other loop command. The reason is structural, not
procedural: it builds its venue client with **no private key and no API credentials**,
wrapped in a deny-by-default proxy (`core_brain/shadow_guard.py`), so there is nothing
loaded with which a write could be signed. It writes only to `data/shadow.db`;
`data/orders.db` is refused outright. It stops itself on a wall-clock time box.

Two cautions:

- **Shadow numbers are rehearsal, not results.** Fills are recorded intents, not
  executions; never quote them as performance.
- The guarantee holds only for this entrypoint. If a change makes `core_brain.shadow_run`
  able to construct a signing client, it comes off the pre-approved list until reviewed.


## 4. Limits

`MAX_ORDER_USD = 25.0`, `MAX_TOTAL_USD = 100.0` are defined in `core_brain/venue.py`,
which reads them from the `max_order_usd` / `max_total_usd` fields of `MakerConfig` in
`core_brain/config.py`. Grep for the lowercase field names there; the uppercase constants
exist only in `venue.py`.

They are enforced at the call sites, not in `venue.py`: `core_brain/order_manager.py`,
`core_brain/trader_loop.py` and `core_brain/single_buy_saver.py`.

Real-money checks are allowed and are often the only real proof of a change. Keep them at
the venue minimum, inside these caps, and always pair them with the undo command.

## 5. `data/orders.db` is the registry, and the only one

Every module resolves it through `core_brain.order_registry.DEFAULT_DB_PATH`. Read it;
never rewrite or delete it.

## 6. Changes that always land with a test

Sizing, fill attribution, and the merge path. An under-counted position invites fresh
exposure on top of it; a pair assembled over $1.00 is a booked loss on an instrument that
pays exactly $1.00.
