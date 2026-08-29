# AGENTS.md — Spread Hunter Live
@RTK.md
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
4. **Dynamic Caps (`core_brain/config.py`):** `order_risk_pct = 25%` ($25.0 at $100 baseline), `naked_risk_pct = 6%` ($6.0 at $100 baseline), `bankroll_ceiling_pct = 90%` ($90.0 at $100 baseline), `max_pair_cost = 0.99`. Dynamic caps scale with live account value.

Full rules, including which commands are pre-approved: [docs/agents/safety.md](docs/agents/safety.md).

## Commands

```bash
python -m pytest -q                          # full test suite
python -m core_brain.order_manager status    # status and balance (read-only)
python -m core_brain.shadow_run --minutes 5  # full-loop rehearsal; no signer, spends nothing
python -m dashboard.server                   # dashboard on http://127.0.0.1:8799
```

```powershell
.\scripts\spread-hunter-menu.ps1 start       # start the whole stack
.\scripts\spread-hunter-menu.ps1 status
```

Operator-facing commands are PowerShell; sequence with `;`, never `&&`.

## Done means

`python -m pytest -q` green (run by the agent, never delegated to the operator), every changed
behaviour covered by a test that fails without the change, and a **How to verify** block
written for the operator that lists only non-pytest, operator-actionable steps. Report the
agent-run output, not the impression. Rules and examples:
[docs/agents/verifying.md](docs/agents/verifying.md).

## Model conduct — verification

- **The agent runs the test suite itself.** `python -m pytest -q` is an internal gate the
  agent executes and reports on. It is never handed to the operator as a prompt, suggestion,
  or "How to verify" step.
- **Never tell, ask, or suggest the operator to run `pytest`** (or any `/pytest` prompt) to
  validate work. Report the agent-run results instead.
- **Owner Standing Directive 2026-08-29 — tests are not verification for the Owner.**
  The Owner has stated explicitly: asking to "check tests" or citing "tests passed" as proof
  means nothing was built — tests have passed before while the feature was still buggy.
  Tests are background-only, run silently by the agent. For the Owner, verification is
  **hands-on surface behavior only**: launch the menu/stack, run a shadow rehearsal, open
  `http://127.0.0.1:8799`, check the report file in `reports/`, observe the feature working.
  Never present `pytest` counts, `pytest -q` output, or "4 passed" as "meat to check."
  When something has meat to verify, point to the file/screen/command the Owner can touch
  and what value should differ — not to test results.
- **The "How to verify" block is hands-on only.** It steers the operator to *use* the change
  — launch the script/stack, open the dashboard, click through, observe behavior — not to
  audit tooling. It must NOT include:
  - running the test suite (`pytest`);
  - `gh issue view` / `gh pr view` or `git status`, or any "confirm the issue is closed /
    PR is merged / checks are green" step — the operator reads GitHub and CI themselves, so
    don't narrate the agent's own git hygiene back as a verify step;
  - anything that only re-states state the operator can already see.
  Point the operator to touch, experience, and exercise the feature.

## Pushing and merging
You are managing repo actions and work with the remote GitHub repo. Keep organized work and branch names. Use best convention globally that supports clarity, ease of use, and harmony with the CodeRabbit AI reviewer.
Full rules: [docs/agents/git-workflow.md](docs/agents/git-workflow.md).

GitHub operations are **fully autonomous and delegated to the agent**: commit, push, branch, PR creation, review rounds, and merging are decided and executed directly by the agent. No operator sign-off is needed. Keep the operator informed with concise status updates (e.g. `Working on [branch]...`, `Committed & Pushed...`, `PR Opened #...`, `Merged PR #...`).

1. **One push per review round.** Batch every accepted fix into one commit and push once. Each push starts its own incremental review.
2. **One summary comment per round** — what you changed, what you declined, why. The `@coderabbitai` handle must not appear in it. Post `@coderabbitai resolve` as a separate comment. Never ask for a review; they fire on their own.
3. **Stop pushing after the 3rd round of fix.** Upon getting a 4th review, conclude whether it can be merged or if there is a serious blocker (Critical/High) that must be addressed. Minors/nits can be skipped once reviewed.
4. **Read the full diff and check the stack before merging.** A stacked merge can carry another PR onto `main` with it. Merge autonomously when CI checks are green and blockers are resolved.
5. **Review Priority & CodeRabbit Limit Fallback.** Prefer CodeRabbit for reviews. If CodeRabbit hits its review/rate limit (or asks to wait 1 hour), do not stall work: perform an objective agent review directly, triage findings, and proceed.

A `PreToolUse` hook (`scripts/hooks/git_workflow_guard.py`) puts these rules at the command. It **reminds** on rule 1, printing the round discipline before a push, and **guards** against runaway loops (blocking a 4th push without triage).

## Reference

| Read this | When |
| --- | --- |
| [docs/agents/workflow-cheatsheet.md](docs/agents/workflow-cheatsheet.md) | **Core Agent Responsibilities & Default Workflows** — Read for how I proactively manage PRs, UI/UX, and TDD loops |
| [docs/agents/safety.md](docs/agents/safety.md) | Before running any command that could reach the venue |
| [docs/agents/verifying.md](docs/agents/verifying.md) | Before reporting any change done |
| [docs/agents/architecture.md](docs/agents/architecture.md) | Finding the module that owns a behaviour; runtime state files |
| [docs/agents/first-run.md](docs/agents/first-run.md) | Resetting a stale runtime to first-run state; every run command and its arguments; what each menu option covers |
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
