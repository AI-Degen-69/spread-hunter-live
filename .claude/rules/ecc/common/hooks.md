# Hooks System

## Hook Types

- **PreToolUse**: Before tool execution (validation, parameter modification)
- **PostToolUse**: After tool execution (auto-format, checks)
- **Stop**: When session ends (final verification)

## Project-Specific Hooks

This repo uses a `PreToolUse` hook (`scripts/hooks/git_workflow_guard.py`) that:
- **Reminds** on push discipline (one push per review round)
- **Blocks** pushes after 3 automatic reviews
- **Blocks** merges of PRs touching `core_brain/`, `scoring/`, or `dashboard/server.py`
  without operator sign-off

Record sign-off with:
```bash
python scripts/hooks/git_workflow_guard.py --approve <pr>
```
