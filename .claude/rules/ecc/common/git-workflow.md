# Git Workflow

## Commit Message Format
```
<type>: <description>

<optional body>
```

Types: feat, fix, refactor, docs, test, chore, perf, ci

## Pull Request Workflow

When creating PRs:
1. Analyze full commit history (not just latest commit)
2. Use `git diff [base-branch]...HEAD` to see all changes
3. Draft comprehensive PR summary
4. Include test plan with TODOs
5. Push with `-u` flag if new branch

> For the full development process (planning, TDD, code review) before git operations,
> see [development-workflow.md](common-development-workflow.md).

## Repo-Specific Rules

See [docs/agents/git-workflow.md](../../docs/agents/git-workflow.md) for:
- One push per review round
- Summary comment per round (no `@coderabbitai` handle)
- Stop pushing after 3rd review round
- Stacked merge checks before merging
