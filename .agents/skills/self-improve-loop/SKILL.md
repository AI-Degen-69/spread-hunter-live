---
name: self-improve-loop
description: Runs the exact 7-step autonomous codebase improvement loop in Antigravity across any folder (core brain, dashboard, strategy, scoring, tests). Discovers candidates via improve-codebase-architecture, plans, implements with TDD, verifies, creates PR, handles Critical/High CodeRabbit reviews, and loops to the next candidate.
---

# Autonomous Codebase Improvement Loop

A continuous 7-step improvement cycle executing across the entire repository (core_brain, dashboard, strategy, scoring, database, tests).

```
1. Explore Codebase → 2. Rank Candidates → 3. Branch & Plan → 4. TDD & Verify → 5. Push PR → 6. CodeRabbit Review & Fix → 7. Next Candidate
```

---

## The 7-Step Cycle

### Step 1: Execute Codebase Improvement Exploration
- Run the exploration step from `improve-codebase-architecture`.
- Inspect git commit history (`git log --oneline -n 30`) and codebase hot spots across **all folders**:
  - `core_brain/` (execution loop, order manager, venue, single buy saver, scanner)
  - `scoring/` & strategy (rankings, filters, pair assembly logic)
  - `dashboard/` (UI/UX, frontend telemetry, HTML/CSS/JS, server)
  - `data/` & database registry
  - `tests/` & test infrastructure
- Look for shallow modules, seam leaks, latency bottlenecks, unhandled edge cases, or test gaps.

### Step 2: Extract & Rank Improvement Candidates
- Generate candidate cards with:
  - **Area / Files**: Path to affected files.
  - **Problem**: What causes friction, latency, or architectural shallowness.
  - **Solution**: Plain-English refactor / improvement plan.
  - **Benefits**: Locality, testability, leverage, or performance gains.
  - **Strength Badge**: `Strong`, `Worth exploring`, or `Speculative`.
- Append new candidates to the backlog in `SHARED_TASK_NOTES.md`.

### Step 3: Pick Candidate, Create Branch & Plan Review
- Select the highest-priority unaddressed candidate.
- Create git branch: `improve/loop-iter-<N>-<topic>` (or `optimize/`, `fix/`, `refactor/`).
- Formulate a clear, bounded plan:
  - Scope: $\le 3$ files, $\le 120$ lines changed.
  - Machine-decidable acceptance criteria.
  - Review plan against safety rails (no changes to `MAX_ORDER_USD`/`MAX_TOTAL_USD`, no `data/orders.db` edits).

### Step 4: Implement with TDD & Verify Before Completion
- **Write Test First (RED)**: Add unit/regression test reproducing the issue or validating the new capability.
- **Implement Minimal Fix (GREEN)**: Write clean, focused implementation.
- **Verification Gate**:
  1. `python -m pytest -q` (must be 100% green).
  2. `python -m core_brain.shadow_run --minutes 1` (rehearsal pass against live book).
  3. `ruff check .` (lint & style).
  4. Ensure zero test assertions were deleted or weakened.

### Step 5: Push Branch & Open GitHub PR
- Create conventional commit: `<type>(<scope>): <summary>` (e.g. `refactor(core_brain): deepen market cache seam`).
- Tag checkpoint: `loop-iter-<N>-<timestamp>`.
- Push branch with tags: `git push -u origin <branchName> --tags`.
- Open PR via `gh pr create`:
  - Title: `@coderabbitai`
  - Body:
    ```markdown
    @coderabbitai summary

    ## Loop Checkpoint
    - **Loop Iteration**: #<N>
    - **Area**: <Dashboard / Core Brain / Strategy / Scoring / etc.>
    - **Branch**: `<branchName>`

    ## Why
    <summary of improvement>

    ## Test output
    ```
    <real pytest output>
    ```

    ## How to verify
    1. Run `python -m pytest -q`
    2. Run `python -m core_brain.shadow_run --minutes 1`
    ```

### Step 6: Code Review & Targeted Fixes
- Inspect automated CodeRabbit review: `gh pr view --json comments,reviews`.
- **Severity Action Matrix**:
  - **Critical / High / Major**: Always fix immediately.
  - **Valid / High-value suggestions**: Fix and batch.
  - **Minor / Low / Nitpicks**: Leave out and skip.
- Batch all accepted fixes into **one commit** and push once.
- Post summary comment explaining what was resolved.
- Post separate `@coderabbitai resolve` comment.
- Leave PR as is.

### Step 7: Bridge & Move to Next Candidate (Loop)
- Record completed iteration in `SHARED_TASK_NOTES.md`.
- Switch back to `main`, pull latest, and immediately repeat from **Step 1** for the next candidate.
