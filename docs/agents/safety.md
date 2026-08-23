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

## 4. Limits

`MAX_ORDER_USD = 25.0`, `MAX_TOTAL_USD = 100.0`, both in `core_brain/config.py` and
enforced in `core_brain/venue.py`.

Real-money checks are allowed and are often the only real proof of a change. Keep them at
the venue minimum, inside these caps, and always pair them with the undo command.

## 5. `data/orders.db` is the registry, and the only one

Every module resolves it through `core_brain.order_registry.DEFAULT_DB_PATH`. Read it;
never rewrite or delete it.

## 6. Changes that always land with a test

Sizing, fill attribution, and the merge path. An under-counted position invites fresh
exposure on top of it; a pair assembled over $1.00 is a booked loss on an instrument that
pays exactly $1.00.
