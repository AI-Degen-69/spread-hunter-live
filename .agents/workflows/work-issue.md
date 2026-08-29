---
description: End-to-end workflow to pick up an open issue, plan it, orchestrate the implementation, and ship it.
---

# Work Issue

This workflow defines the end-to-end lifecycle for picking up an open issue, designing the solution, orchestrating the implementation, and shipping the PR.

## Default Behavior — Invoked Without Arguments

When this workflow is invoked with no issue number or further instructions:

1. **List all open issues first:** Run `gh issue list --state open --limit 50` (and `gh issue view <n> --comments` for detail as needed) and present every open issue plainly — number, title, labels, and a one-line description.
2. **Categorize/group them:** Group by a criteria the agent chooses and states explicitly (e.g., dependency chain, risk area / strategy / infra / UI, effort/size, or ready-for-agent vs. backlog). Show the grouping.
3. **Recommend an order of work:** Propose a concrete execution sequence with rationale (dependencies first, unblockers before dependents, quick wins vs. deep work), and call out the single next issue to pick up.

Do not skip the listing step. Do not pick an issue silently — always show the full inventory, the grouping, and the recommended order before asking which to start.

## Step Reporting & Transparency Rule (MANDATORY)

At every step of this workflow, the agent MUST explicitly declare which skill or workflow is being executed, OR if a recommended sub-step/skill is skipped, provide an explicit skip notice:
- If executed: `**Executing Skill/Workflow**: [skill/workflow name]`
- If skipped:
  `**Skipped**: [skill name] from [.agents/workflows/work-issue.md / docs/agents/workflow-cheatsheet.md]`
  `-> **Reason**: [concise reason why this step was skipped or replaced]`

Never silently skip a step or proceed to execution without explicit declaration.

## Step 1: Discovery & Assignment

1. **Find work:** Run `gh issue list --state open --label ready-for-agent` to find an actionable issue.
2. **Context:** Run `gh issue view <n> --comments` to read the issue body and discussion.
3. **Claim it:** Run `gh issue edit <n> --add-assignee "@me"` to prevent duplicated work.

## Step 2: Brainstorming & Planning

Never start coding immediately. Ensure the requirements and architecture are locked in.
- **Vague ask?** Use `/grill-me` (or `interview-me`) to extract exact requirements.
- **New concept?** Use `/office-hours` or `idea-refine` to brainstorm the approach.
- **Ready for architecture?** Run `/autoplan` or `/plan-eng-review` to generate a concrete `implementation_plan.md`.
- **UI/UX components?** Run `plan-design-review` to verify visual architecture before implementation.

## Step 3: Execution Orchestration

Delegate the actual coding to the specific orchestration workflow that fits the issue type. These workflows enforce Test-Driven Development (TDD) and verification.
- **New feature?** Route to `/orch-add-feature`.
- **Changing existing behavior?** Route to `/orch-change-feature`.
- **Fixing a bug?** Route to `/orch-fix-defect`.

## Step 4: Self-Audit

Once the orchestration workflow completes and local tests pass, run a final sanity check.
- Run the `/review` skill to self-audit the local diff for structural issues, LLM boundary violations, or performance regressions before committing.
- Tests are an internal gate only — never present `pytest` output or counts as Owner verification (per `AGENTS.md` Owner Directive 2026-08-29). Owner verification is hands-on: launch the stack, open the dashboard, observe the report.

## Step 5: Shipping

Push the code to GitHub and open a Pull Request.
- Run the `/ship` workflow to branch, commit (using conventional commits), push, and create the PR. 
- Ensure the PR title is `@coderabbitai` and body contains `@coderabbitai summary` as per `git-workflow.md`.

## Step 6: Babysitting

Once the PR is open, CodeRabbit will begin its review.
- Run the `/pr-babysitter` workflow to handle CodeRabbit incremental reviews.
- Batch fixes, decline out-of-scope suggestions, and merge the PR once the review is green via `/land-and-deploy`.
