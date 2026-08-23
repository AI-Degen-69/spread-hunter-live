# AGENTS.md — Spread Hunter Live

Real-money execution engine and operations dashboard for the Polymarket **spread hunter**
strategy: buy one UP share and one DOWN share of the same binary market for less than
$1.00 combined, then merge the pair back into exactly $1.00 of USDC.

```
profit per pair = 1.00 - (avg UP price + avg DOWN price)
```

The two failure modes everything else exists to prevent: a pair assembled **over $1.00**
is a booked loss, and a **single buy** (one leg filled, one not) is a directional bet
nobody decided to take.

## Before you touch anything

1. **LIVE is the default.** Every `core_brain.order_manager` subcommand reaches the venue.
   `--no-live` is the dry run.
2. **Opening commands are the operator's to run** — `quote`, `complete`, the Trader loop,
   and the dashboard's **START** button all spend money. Propose them; do not run them
   unless the operator says so in that session.
3. **`data/orders.db` is the production registry.** Read it; never rewrite or delete it.
4. **Caps:** `MAX_ORDER_USD = 25.0`, `MAX_TOTAL_USD = 100.0` (`core_brain/venue.py`).

Full rules, including which commands are pre-approved: [docs/agents/safety.md](docs/agents/safety.md).

## Commands

```bash
python -m pytest -q                          # full test suite
python -m core_brain.order_manager status    # status and balance (read-only)
python -m dashboard.server                   # dashboard on http://127.0.0.1:8799
```

```powershell
.\scripts\spread-hunter-menu.ps1 start       # start the whole stack
.\scripts\spread-hunter-menu.ps1 status
```

Operator-facing commands are PowerShell; sequence with `;`, never `&&`.

## Done means

`python -m pytest -q` green, every changed behaviour covered by a test that fails without
the change, and a **How to verify** block written for the operator. Report the output, not
the impression. Rules and examples: [docs/agents/verifying.md](docs/agents/verifying.md).

## Pushing and merging

Pushes and merges reach GitHub and cannot be undone from here. These four rules are
here, rather than only in the linked file, because reading that file *partway* is how
they get missed. Full rules: [docs/agents/git-workflow.md](docs/agents/git-workflow.md).

1. **One push per review round.** Batch every accepted fix into one commit and push
   once. Each push starts its own incremental review.
2. **One summary comment per round** — what you changed, what you declined, why. The
   `@coderabbitai` handle must not appear in it. Post `@coderabbitai resolve` as a
   separate comment. Never ask for a review; they fire on their own.
3. **A fourth automatic review means stop, not grind.** Decline the rest in the summary
   and say so.
4. **Read the full diff and check the stack before merging.** A stacked merge can carry
   another PR onto `main` with it. Anything touching order sizing, fill attribution,
   risk limits, the merge path or live execution waits for operator sign-off, however
   green the checks are.

A `PreToolUse` hook (`scripts/hooks/git_workflow_guard.py`) puts these rules at the
command. It **reminds** on rule 1, printing the round discipline before a push, and
**blocks** on rules 3 and 4: a push once the PR has had three automatic reviews, and a
merge of any PR touching `core_brain/`, `scoring/` or `dashboard/server.py` until the
operator signs off. Record sign-off with
`python scripts/hooks/git_workflow_guard.py --approve <pr>`; it is bound to the head
commit and lapses on the next push.

## Reference

| Read this | When |
| --- | --- |
| [docs/agents/safety.md](docs/agents/safety.md) | Before running any command that could reach the venue |
| [docs/agents/verifying.md](docs/agents/verifying.md) | Before reporting any change done |
| [docs/agents/architecture.md](docs/agents/architecture.md) | Finding the module that owns a behaviour; runtime state files |
| [docs/agents/glossary.md](docs/agents/glossary.md) | Naming anything in code, commits, issues or dashboard copy |
| [docs/agents/strategy.md](docs/agents/strategy.md) | Changing quoting, sizing, market selection or pricing mode |
| [docs/agents/python-conventions.md](docs/agents/python-conventions.md) | Writing or reviewing Python in this repo |
| [docs/agents/git-workflow.md](docs/agents/git-workflow.md) | **In full, before your first push to any branch.** Branching, committing, opening a PR, CodeRabbit review, merging |
| [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) | Working GitHub issues via `gh` |
| [docs/agents/triage-labels.md](docs/agents/triage-labels.md) | Labelling an issue |
| [docs/agents/domain.md](docs/agents/domain.md) | `CONTEXT.md` and ADR conventions |

## Rule precedence

This file and `docs/agents/*` win over `.claude/rules/ecc/**`. The ECC common rules are
language-agnostic defaults written for TypeScript projects; where they disagree with the
Python conventions or the test bar here, this repo's rules apply.
