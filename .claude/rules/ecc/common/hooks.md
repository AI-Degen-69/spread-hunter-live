# Hooks System

## Hook Types

- **PreToolUse**: Before tool execution (validation, parameter modification)
- **PostToolUse**: After tool execution (auto-format, checks)
- **Stop**: When session ends (final verification)

## Project-Specific Hooks

This repo uses a `PreToolUse` hook (`scripts/hooks/git_workflow_guard.py`). It is a
reminder-and-runaway-guard, not an approval gate. Every path degrades to *allow*, because
a broken guard must not wedge the repo.

- **Reminds** on `git push`: prints how many rounds of review findings the open pull
  request has had, plus the round-discipline rules.
- **Blocks** a push once the pull request has reached `MAX_REVIEW_ROUNDS` (3) rounds of
  `CHANGES_REQUESTED`. A fourth round means stop and conclude, not grind.
- **Reminds** on `gh pr merge`: prints the merge rules. It does **not** block any merge,
  for any path.
- **Reminds** on `gh pr create`: the required title and `@coderabbitai summary` body line.

There is no operator sign-off for merges, and no path — `core_brain/`, `scoring/`,
`dashboard/server.py` or any other — is gated. Git and GitHub operations are fully
autonomous and delegated to the agent; see [AGENTS.md](../../../../AGENTS.md) and
[docs/agents/git-workflow.md](../../../../docs/agents/git-workflow.md), which are the
source of truth wherever these ECC rules disagree.
