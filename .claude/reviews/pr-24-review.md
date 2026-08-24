# PR Review: #24 — chore: prune ECC rules for spread-hunter-live

**Reviewed**: 2026-08-24
**Author**: AI-Degen-69
**Branch**: chore/prune-ecc-rules → main
**Decision**: APPROVE

## Summary

Documentation-only change that removes ~92 lines of irrelevant TypeScript/Claude-Code
defaults from the ECC rules and aligns them with the project's actual Python/trading
conventions. All changes are markdown files under `.claude/rules/ecc/`. No source code,
no tests, no config touched.

## Findings

### CRITICAL
None

### HIGH
None

### MEDIUM

1. **Broken relative links in Python rules** — The `>` reference links in Python-specific
   files were changed from `../common/coding-style.md` to `common-coding-style.md`. These
   are markdown files consumed by agents, not rendered by GitHub. The links use the
   `.agent/rules/` flat filename convention but the git-tracked files live under
   `.claude/rules/ecc/common/` and `.claude/rules/ecc/python/` (nested directories).
   Neither the old nor new links resolve on GitHub, so this is a wash — the links are
   informational, not functional.
   - **File**: All 6 `python/*.md` files
   - **Impact**: Low — agents read these as context, not as clickable links
   - **Suggestion**: Consider using relative paths that match the actual git layout
     (e.g., `../common/coding-style.md`) for consistency, or remove the links entirely
     since agents load both common and python files automatically.

### LOW

1. **README.md not updated** — `.claude/rules/ecc/README.md` still references the
   original ECC install structure with all language directories. Could add a note that
   only `common/` and `python/` are customized for this project.
   - **File**: `.claude/rules/ecc/README.md`

2. **Naming table in common-coding-style.md** — The naming table is excellent but
   duplicates content from `docs/agents/python-conventions.md`. This is intentional
   (the rules file is what agents load first), but worth noting for future
   deduplication if the convention table ever drifts.

## Validation Results

| Check | Result |
|---|---|
| Type check | Skipped (no Python source changes) |
| Lint | Skipped (no Python source changes) |
| Tests | Pass (644 passed, 2 pre-existing stale-feed failures, 1 skipped) |
| Build | Skipped (not applicable) |

## Files Reviewed

| File | Change Type |
|---|---|
| `.claude/rules/ecc/common/agents.md` | Modified |
| `.claude/rules/ecc/common/code-review.md` | Modified |
| `.claude/rules/ecc/common/coding-style.md` | Modified |
| `.claude/rules/ecc/common/development-workflow.md` | Modified |
| `.claude/rules/ecc/common/git-workflow.md` | Modified |
| `.claude/rules/ecc/common/hooks.md` | Modified |
| `.claude/rules/ecc/common/patterns.md` | Modified |
| `.claude/rules/ecc/common/performance.md` | Modified |
| `.claude/rules/ecc/common/security.md` | Modified |
| `.claude/rules/ecc/common/testing.md` | Modified |
| `.claude/rules/ecc/python/coding-style.md` | Modified |
| `.claude/rules/ecc/python/fastapi.md` | Modified |
| `.claude/rules/ecc/python/hooks.md` | Modified |
| `.claude/rules/ecc/python/patterns.md` | Modified |
| `.claude/rules/ecc/python/security.md` | Modified |
| `.claude/rules/ecc/python/testing.md` | Modified |
