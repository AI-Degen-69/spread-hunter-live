# Development Workflow

> This file extends [git-workflow.md](common-git-workflow.md) with the full feature
> development process that happens before git operations.

## Feature Implementation Workflow

0. **Research & Reuse** _(mandatory before any new implementation)_
   - **GitHub code search first:** Run `gh search repos` and `gh search code` to find
     existing implementations, templates, and patterns before writing anything new.
   - **Library docs second:** Use Context7 or primary vendor docs to confirm API behavior
     and version-specific details before implementing.
   - **Check package registries:** Search PyPI before writing utility code. Prefer
     battle-tested libraries over hand-rolled solutions.
   - Prefer adopting or porting a proven approach over writing net-new code.

1. **Plan First**
   - Create implementation plan for complex features
   - Identify dependencies and risks
   - Break down into phases

2. **TDD Approach**
   - Write tests first (RED)
   - Implement to pass tests (GREEN)
   - Refactor (IMPROVE)
   - Verify 80%+ coverage

3. **Code Review**
   - Review immediately after writing code
   - Address CRITICAL and HIGH issues
   - Fix MEDIUM issues when possible

4. **Commit & Push**
   - Detailed commit messages
   - Follow conventional commits format
   - See [git-workflow.md](common-git-workflow.md) for commit message format and PR process

5. **Pre-Review Checks**
   - Verify all automated checks (CI/CD) are passing
   - Resolve any merge conflicts
   - Ensure branch is up to date with target branch
   - Only request review after these checks pass
