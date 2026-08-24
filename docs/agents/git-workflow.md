# Git and GitHub

Repo: `AI-Degen-69/spread-hunter-live` (`origin`), default branch `main`. CodeRabbit
reviews every pull request; the conventions below match its configuration, so its title
check passes on the first try.

## Delegation

Operator directive (2026-08-24): the agent owns GitHub operations end to end --
commits, pushes, branches, PRs, review rounds and merges -- deciding the actions
itself and reporting what happened afterwards. It answers its own questions and
escalates only genuine dilemmas. The sign-off gate below still applies: a PR
touching order sizing, fill attribution, risk limits, the merge path or live
execution waits for explicit operator sign-off before merging, however green
the checks are.

## Tags

One vocabulary, used for the PR title, the branch name and the commit type:

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

This table is the full list of commit types for this repo, including `style` and `revert`,
which `.claude/rules/ecc/common/git-workflow.md` omits.

## Commits

Conventional commits, imperative, one logical change per commit. The scope is the package
name — `fix(core_brain): size pair completion against the asks ladder`.

Never commit `.env`, keys, `data/*.db`, or logs. Commit as work completes rather than
batching a day's edits.

## Branches

Never commit straight to `main`. One branch per change, named `<prefix><short-slug>` from
the table — `fix/pair-completion-sizing`.

## Pull requests

Push the branch, then open the PR with `gh pr create`. CodeRabbit is configured in its
repository UI to generate both the title and the summary, so the PR is opened with
**placeholders**, not with text you wrote:

```bash
gh pr create --title "@coderabbitai" --body-file pr-body.md   # write pr-body.md first, outside the repo
```

- **Title: the literal string `@coderabbitai`.** This is the auto-title placeholder.
  CodeRabbit replaces it with `[TAG] short plain-English title a high-schooler
  understands`, per the auto-title instructions set in its UI. Do not write the title
  yourself — a hand-written title suppresses nothing, it just means the configured format
  is never applied.
- **Body: must contain the line `@coderabbitai summary`.** This is the high-level summary
  placeholder. CodeRabbit replaces that line with a five-bullet plain-English summary. The
  rest of the body is yours.

Body template:

````markdown
@coderabbitai summary

## Why

One or two sentences. What was wrong, or what was missing.

## Test output

```
python -m pytest -q
<paste the real output>
```

## How to verify

<the same How to verify block given to the operator — see docs/agents/verifying.md>
````

After opening, check the PR: if the title still reads `@coderabbitai` or the body still
reads `@coderabbitai summary` after a few minutes, CodeRabbit did not run. Fix the title
by hand rather than leaving a placeholder as the PR title.

**These two placeholders are the only permitted uses of the `@coderabbitai` handle at PR
creation.** They do not trigger a review — the review fires on PR open regardless. The ban
in the next section is about comments posted during review rounds.

## Review by CodeRabbit

CodeRabbit reviews this repo automatically, and is configured entirely through its
repository UI — this repo has no `.coderabbit.yaml`, and adding one would silently
override every UI setting.

**Never post `@coderabbitai review` or `@coderabbitai full review` in a comment, at any
point.** There is nothing to request:

- Opening the PR triggers the first review, over the full diff.
- Every push after that triggers an incremental review, scoped to the new commits only.

`full review` re-scans the entire diff — all files, including the ones already passed twice
— and costs far more than the automatic incremental pass it duplicates. Asking for one is
how a two-round review turns into six.

### Working a round

1. Read every comment and judge it. Implement the ones that are correct and worth it.
2. **Batch every accepted fix into one commit and push once.** Each separate push is its
   own incremental review, so four pushes cost four reviews for one round of feedback.
3. Post **one** summary comment: what you changed, what you declined, and why. Plain text,
   and the `@coderabbitai` handle must not appear anywhere in it — mentioning the handle in
   a comment risks re-triggering a review. (The title and body placeholders set at PR
   creation are the exception, and they are set once, never re-posted.) Declining is fine
   when the suggestion is wrong, out of scope, or lands on a vendored path; say so plainly.
4. In a **separate** comment, post `@coderabbitai resolve` to close the accepted threads.
   `resolve` does not start a review.

### Fix by severity, not by comment count

| Severity | Action |
| --- | --- |
| Critical | Always fix. Blocks merge. |
| Major on `core_brain/`, `scoring/`, `dashboard/server.py` | Always fix. Blocks merge. |
| Major elsewhere (docs, tests, tooling) | Fix if quick; otherwise decline with a reason |
| Minor | Batch the quick wins into the same commit, or decline the whole batch in one reply |
| Anything on a vendored path (`.claude/rules/**`, `.ecc/**`, `.agents/**`) | Decline in the summary comment. The fix is a path filter in the CodeRabbit UI, not an edit to vendored files |

### Stop condition

The PR is review-complete when the latest **automatic** review carries no Critical and no
Major touching `core_brain/`, `scoring/` or `dashboard/server.py`. Open Minors do not block
merge.

Three rounds is a runaway guard, not a target. If a PR reaches a fourth automatic review,
something is wrong with the change or the filters — stop and say so rather than grinding.

### Writing style

Write PR bodies and review replies the way CodeRabbit is configured to write: plain
English, no abbreviations, key point first, technical terms explained in one sentence.
Call out anything that risks a pair over $1.00 or a single unmatched buy.

CI (`.github/workflows/tests.yml`) must be green on both ubuntu and windows.

## Merging

Routine changes — docs, tests, tooling, dashboard cosmetics — may be merged once CI is
green and CodeRabbit is clear. Anything touching order sizing, fill attribution, risk
limits, the merge path, or live execution waits for operator sign-off, even when every
check passes.
