---
description: End-to-end workflow to pick up an open issue, plan it, orchestrate the implementation, and ship it.
---

# Work Issue

This workflow defines the end-to-end lifecycle for picking up an open issue, designing the solution, orchestrating the implementation, and shipping the PR.

## Step 1: Discovery & Assignment

1. **Find work:** Run `gh issue list --state open --label ready-for-agent` to find an actionable issue.
2. **Context:** Run `gh issue view <n> --comments` to read the issue body and discussion.
3. **Claim it:** Run `gh issue edit <n> --add-assignee @me` to prevent duplicated work.

## Step 2: Brainstorming & Planning

Never start coding immediately. Ensure the requirements and architecture are locked in.
- **Vague ask?** Use `/grill-me` (or `interview-me`) to extract exact requirements.
- **New concept?** Use `/office-hours` or `idea-refine` to brainstorm the approach.
- **Ready for architecture?** Run `/autoplan` or `/plan-eng-review` to generate a concrete `implementation_plan.md`.

## Step 3: Execution Orchestration

Delegate the actual coding to the specific orchestration workflow that fits the issue type. These workflows will enforce Test-Driven Development (TDD) and verification.
- **New feature?** Route to `/orch-add-feature`.
- **Changing existing behavior?** Route to `/orch-change-feature`.
- **Fixing a bug?** Route to `/orch-fix-defect`.

## Step 4: Self-Audit

Once the orchestration workflow completes and local tests pass, run a final sanity check.
- Run the `/review` skill to self-audit the local diff for structural issues, LLM boundary violations, or performance regressions before committing.

## Step 5: Shipping

Push the code to GitHub and open a Pull Request.
- Run the `/ship` workflow to branch, commit (using conventional commits), push, and create the PR. 
- Ensure the PR title is `@coderabbitai` and body contains `@coderabbitai summary` as per `git-workflow.md`.

## Step 6: Babysitting

Once the PR is open, CodeRabbit will begin its review.
- Run the `/pr-babysitter` workflow to handle CodeRabbit incremental reviews.
- Batch fixes, decline out-of-scope suggestions, and merge the PR once the review is green.
