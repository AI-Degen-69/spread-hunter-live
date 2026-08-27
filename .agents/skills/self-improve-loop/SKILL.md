---
name: self-improve-loop
description: Runs an autonomous 7-step codebase improvement loop across any repository. Dynamically inspects the project architecture, extracts improvement candidates, implements with TDD, and handles CodeRabbit reviews via PR looping.
---

# Autonomous Codebase Improvement Loop

A continuous 7-step improvement cycle executing across the current repository.

```
1. Explore Codebase → 2. Rank Candidates → 3. Branch & Plan → 4. TDD & Verify → 5. Push PR → 6. CodeRabbit Review & Fix → 7. Next Candidate
```

---

## The 7-Step Cycle

### Step 1: Execute Codebase Improvement Exploration
- Run the exploration step from `C:\Users\Tiger\.agents\skills\improve-codebase-architecture` (if available), or dynamically scan the project structure.
- Inspect git commit history (`git log --oneline -n 30`) and codebase hot spots across all top-level directories.
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
  - Review plan against safety rails. Read the project's `AGENTS.md` or `README.md` to identify critical boundaries and files to avoid.

### Step 4: Implement with TDD & Verify Before Completion
- **Write Test First (RED)**: Add unit/regression test reproducing the issue or validating the new capability.
- **Implement Minimal Fix (GREEN)**: Write clean, focused implementation.
- **Verification Gate**:
  - Automatically detect the project's native test runner (e.g., `npm test`, `pytest -q`, `cargo test`) and run the test suite. Must be 100% green.
  - Run standard linting tools (e.g., `ruff check .`, `eslint`, `cargo clippy`).
  - Ensure zero test assertions were deleted or weakened.

### Step 5: Push Branch & Open GitHub PR
- Create conventional commit: `<type>(<scope>): <summary>`.
- Tag checkpoint: `loop-iter-<N>-<timestamp>`.
- Push branch with tags: `git push -u origin <branchName> --tags`.
- Open PR via `gh pr create`:
  - Title: `@coderabbitai`
  - Body: Include loop iteration, area, branch, a summary of why, real test output, and verification steps.

### Step 6: Code Review & Targeted Fixes
- **Wait for Review:** CodeRabbit takes a few minutes. Wait 3 minutes, then poll `gh pr view --json comments,reviews` every 60 seconds until the bot posts its review.
- Inspect automated CodeRabbit review.
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
- **Cleanup & Reset:** Run `git stash` and `git clean -fd` to clear untracked files, then `git checkout main` and `git pull`.
- **Iteration Cap:** Check the iteration count. If `MAX_ITERATIONS` (default: 3) is reached, terminate the loop to protect API budget. Otherwise, immediately repeat from **Step 1**.
