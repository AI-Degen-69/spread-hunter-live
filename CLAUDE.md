# CLAUDE.md — Spread Hunter Live

@AGENTS.md

## Before you touch anything

This repo places real orders with real money. `python -m engine.order_manager` is LIVE by
default — every subcommand reaches the venue unless you pass `--no-live`.

- **Opening commands are the operator's to run.** `quote`, `complete`, the Trader loop,
  and the dashboard's START button all rest real funds — `complete` buys the missing side,
  so it spends money to remove risk. Hand over the exact command; run it yourself only
  when the operator says so in that session.
- Closing commands reduce exposure and are pre-approved: `exit`, `merge`, `redeem`,
  `cancel`, `cancel-market`, `cancel-all`. Cancelling only pulls resting orders — a leg
  that already filled needs `complete` (supervised), then `merge`, or `exit`.
- Real-money checks are allowed and often the only real proof. Keep them at the venue
  minimum, inside `MAX_ORDER_USD` / `MAX_TOTAL_USD`, and always pair them with the undo
  command.
- `data/orders.db` is the production order registry. Read it; never rewrite or delete it.

## Done means

`python -m pytest -q` green, and every changed behaviour covered by a test that fails
without the change. Report the output, not the impression.

Then write the operator a **How to verify** block — the exact terminal command, the
dashboard click path, or the smallest real-money test that proves the change, with the
value to look for and the undo command beside it. Rules and examples: the "Verifying a
change" section of AGENTS.md.

Git, branches, pull requests and the CodeRabbit review loop: the "Git and GitHub"
section of AGENTS.md. It is the standing instruction, not a suggestion.

Changes to sizing, fill attribution, or the merge path land with a test, always. An
under-counted position invites fresh exposure on top of it; a pair assembled over $1.00
is a booked loss on an instrument that pays exactly $1.00.

## Conventions

Full set in `.claude/skills/spread-hunter-live/SKILL.md`. The short version:

- Conventional commits, imperative: `fix(engine): size pair completion against the asks ladder`.
- Absolute imports rooted at the repo (`from engine.order_registry import OrderRegistry`).
  No relative imports, no wildcards.
- Narrow `except` clauses only. A bare `except Exception: pass` in an execution path
  hides money-losing state.
- Operator-facing commands are PowerShell (`.\scripts\spread-hunter-menu.ps1`);
  sequence with `;`, never `&&`.

## One name per concept

Match the code and the dashboard: **single buy** (not naked leg / one-sided / unhedged),
**pair cost**, **merge** as the exit, **graduated** for the markets in `run/markets.json`.
Full rename table: the Glossary section of AGENTS.md.
