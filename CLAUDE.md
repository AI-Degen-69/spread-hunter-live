# CLAUDE.md — Spread Hunter Live

@AGENTS.md

This repo places real orders with real money. `python -m core_brain.order_manager` is LIVE
by default. Before running anything that could reach the venue, read
[docs/agents/safety.md](docs/agents/safety.md).

`AGENTS.md` and `docs/agents/*` are the single source of truth for this repo's rules, and
they override `.claude/rules/ecc/**` wherever the two disagree. Everything that used to be
duplicated here now lives there.

> **Memory — Owner 2026-08-29:** Never use `pytest` results as Owner verification. The
> Owner considers green tests meaningless — only hands-on feature behavior counts (launch
> menu/stack, open dashboard, observe report/file). See `AGENTS.md` § Model conduct —
> verification for the standing rule.
