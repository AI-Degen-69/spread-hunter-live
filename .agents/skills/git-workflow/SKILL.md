---
description: Global git & GitHub convention — tags, branches, commits, CodeRabbit PR flow, merge. Applies to every project.
---

# Git & GitHub (global convention)

## Delegation

The agent owns GitHub operations end to end -- commits, pushes, branches, PRs, review
rounds, and merges -- with full autonomy. No operator sign-off is required. Keep the
operator informed with concise inline status (e.g. `Working on [branch]...`,
`Committed & Pushed...`, `PR Opened #...`, `Merged PR #...`).

## One vocabulary: tag -> branch prefix -> commit type

| Tag | Use it for | Branch prefix | Commit type |
| --- | --- | --- | --- |
| `[ADD]` | new capability on top of what exists | `add/` | `feat` |
| `[CREATE]` | a new file, module or service | `create/` | `feat` |
| `[FIX]` | wrong behaviour corrected | `fix/` | `fix` |
| `[IMPROVE]` | same behaviour, better | `improve/` | `refactor` |
| `[REFACTOR]` | moved or renamed, behaviour unchanged | `refactor/` | `refactor` |
| `[OPTIMIZE]` | faster or cheaper | `optimize/` | `perf` |
| `[TEST]` | tests only | `test/` | `test` |
| `[DOCUMENT]` | docs only | `document/` | `docs` |
| `[FORMAT]` | whitespace, layout, lint | `format/` | `style` |
| `[UPDATE]` | dependency or data refresh | `update/` | `chore` |
| `[CONFIGURE]` | settings, workflows, tooling | `configure/` | `chore` |
| `[REVERT]` | undo a previous change | `revert/` | `revert` |

This table is the full list of commit types, including `style` and `revert` (which the ECC
common `git-workflow.md` omits).

## Commits

Conventional commits, imperative, one logical change per commit. The scope is the package/
module name -- `fix(core): size pair completion against the asks ladder`.

Never commit `.env`, keys, DB files, or logs. Commit as work completes rather than batching
a day's edits.

## Branches

Never commit straight to `main`. One branch per change, named `<prefix><short-slug>` from the table -- `fix/pair-completion-sizing`.

## Pull requests

Push the branch, then open the PR with `gh pr create`. If the repo runs CodeRabbit (auto-title
+ summary configured in its repo UI), open with **placeholders**, not text you wrote:

```bash
gh pr create --title "@coderabbitai" --body-file pr-body.md   # write pr-body.md first, outside the repo
```

- **Title: the literal string `@coderabbitai`** -- the auto-title placeholder. CodeRabbit
  replaces it with `[TAG] short plain-English title`, per its UI config. Do not hand-write the title.
- **Body: must contain the line `@coderabbitai summary`** -- the high-level summary placeholder.
  CodeRabbit replaces that line with a five-bullet plain-English summary. Rest of body is yours.

Body template:

```markdown
@coderabbitai summary

## Why

One or two sentences. What was wrong, or what was missing.

## Test output

<the real test output>

## How to verify

<operator-actionable verification steps>
```

After opening, check: if the title still reads `@coderabbitai` or the body still reads
`@coderabbitai summary` after a few minutes, CodeRabbit did not run -- fix the title by hand.

These two placeholders are the **only** permitted uses of the `@coderabbitai` handle at PR
creation. They do not trigger a review (the review fires on open regardless).

## Review rounds (CodeRabbit)

- **Never post `@coderabbitai review` or `@coderabbitai full review`** in a comment, at any
  point. Opening the PR triggers the first review over the full diff; every push triggers an
  incremental review over the new commits only. `full review` re-scans everything and is how a
  two-round review becomes six.
- Per round: read every comment and judge it -> accept the correct ones -> **batch every
  accepted fix into ONE commit and push ONCE** (each push is its own incremental review) ->
  post **one** summary comment (what you changed, what you declined, why; the `@coderabbitai`
  handle must NOT appear) -> in a **separate** comment post `@coderabbitai resolve` to close
  the accepted threads (`resolve` does not start a review).
- Fix by severity, not by comment count: Critical always; Major on core/critical paths always;
  Major elsewhere fix-if-quick-or-decline; Minor batch the quick wins or decline the batch;
  anything on a vendored path (`.claude/rules/**`, `.ecc/**`, `.agents/**`) decline in summary.
- **Stop condition:** the PR is review-complete when the latest automatic review carries no
  Critical and no Major on core paths. Open Minors do not block merge.
- **Three rounds is a runaway guard, not a target.** If a PR reaches a fourth automatic review,
  something is wrong -- stop and say so rather than grinding.
- **CodeRabbit limit fallback:** if CodeRabbit reports its review limit reached (or asks to
  wait ~1 hour), never wait -- the agent runs an objective diff review directly (logic, limits,
  tests, regressions), triages findings, posts a review summary, and proceeds.

## Merging

Read the full diff (`gh pr diff <n>`) and check the stack before merging -- a stacked merge can
carry another PR onto `main` with it. Merge autonomously once CI is green and review blockers
are resolved. Report `Merged PR #...`.

## Enforcement

A `PreToolUse` hook (`git_workflow_guard.py`) reminds on "one push per round" and guards a 4th
push without triage. A repo's own git-workflow conventions (`docs/agents/git-workflow.md` or its
AGENTS.md) win where they disagree.
