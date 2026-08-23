# CLAUDE.md — Spread Hunter Live

@AGENTS.md

This repo places real orders with real money. `python -m core_brain.order_manager` is LIVE
by default. Before running anything that could reach the venue, read
[docs/agents/safety.md](docs/agents/safety.md).

`AGENTS.md` and `docs/agents/*` are the single source of truth for this repo's rules, and
they override `.claude/rules/ecc/**` wherever the two disagree. Everything that used to be
duplicated here now lives there.
