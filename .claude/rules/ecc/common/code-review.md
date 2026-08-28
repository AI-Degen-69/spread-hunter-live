# Code Review Standards

## Purpose

Code review ensures quality, security, and maintainability before code is merged.

## When to Review

**MANDATORY review triggers:**

- After writing or modifying code
- Before any commit to shared branches
- When security-sensitive code is changed (venue interaction, order sizing, risk limits)
- When architectural changes are made
- Before merging pull requests

**Pre-Review Requirements:**

Before requesting review, ensure:

- All automated checks (CI/CD) are passing
- Merge conflicts are resolved
- Branch is up to date with target branch

## Review Checklist

Before marking code complete:

- [ ] Code is readable and well-named (snake_case functions, PascalCase classes)
- [ ] Functions are focused (<50 lines)
- [ ] Files are cohesive (<800 lines)
- [ ] No deep nesting (>4 levels)
- [ ] Errors are handled explicitly with narrow except clauses
- [ ] No hardcoded secrets or credentials
- [ ] No stray print() statements (use logging)
- [ ] Tests exist for new functionality
- [ ] Test coverage meets 80% minimum

## Security Review Triggers

**STOP and carefully review when touching:**

- Order sizing or fill attribution code
- Venue interaction (CLOB client calls)
- Wallet or private key handling
- Risk limits (`MAX_ORDER_USD`, `MAX_TOTAL_USD`)
- Database queries (parameterized only)
- External API calls

## Review Severity Levels

| Level | Meaning | Action |
|-------|---------|--------|
| CRITICAL | Security vulnerability or data loss risk | **BLOCK** - Must fix before merge |
| HIGH | Bug or significant quality issue | **WARN** - Should fix before merge |
| MEDIUM | Maintainability concern | **INFO** - Consider fixing |
| LOW | Style or minor suggestion | **NOTE** - Optional |

## Review Workflow

```
1. Run git diff to understand changes
2. Check security checklist first
3. Review code quality checklist
4. Run relevant tests
5. Verify coverage >= 80%
```

## Common Issues to Catch

### Security

- Hardcoded credentials (API keys, private keys, tokens)
- SQL injection (string concatenation in queries)
- Path traversal (unsanitized file paths)
- Error messages leaking sensitive data

### Code Quality

- Large functions (>50 lines) - split into smaller
- Large files (>800 lines) - extract modules
- Deep nesting (>4 levels) - use early returns
- Missing error handling - handle explicitly
- Missing tests - add test coverage

### Performance

- N+1 queries - use JOINs or batching
- Missing pagination - add LIMIT to queries
- Unbounded queries - add constraints

## Approval Criteria

- **Approve**: No CRITICAL or HIGH issues
- **Warning**: Only HIGH issues (merge with caution)
- **Block**: CRITICAL issues found

## Integration with Other Rules

This rule works with:

- [testing.md](common-testing.md) - Test coverage requirements
- [security.md](common-security.md) - Security checklist
- [git-workflow.md](common-git-workflow.md) - Commit standards
- [agents.md](common-agents.md) - Agent delegation
