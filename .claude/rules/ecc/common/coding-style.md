# Coding Style

## Naming

This is a Python-only codebase. Follow these conventions consistently:

| Element           | Convention            | Example                                  |
| ----------------- | --------------------- | ---------------------------------------- |
| Files / modules   | `snake_case`          | `order_registry.py`, `live_fill.py`      |
| Functions / vars  | `snake_case`          | `complete_pair`, `best_ask`              |
| Classes           | `PascalCase`          | `OrderRegistry`, `LiveFillEngine`        |
| Constants         | `SCREAMING_SNAKE_CASE` | `MIN_ORDER_SHARES`, `DEFAULT_TICK_SIZE` |
| Booleans          | prefix `is_`, `has_`, `should_`, `can_` | `is_filled`, `has_depth` |

## Immutability

Prefer immutable data where practical:

- Use `@dataclass(frozen=True)` or `NamedTuple` for value objects.
- Avoid mutating function arguments.
- Return new collections instead of modifying existing ones.

**Exception:** Mutable state stores (e.g., `OrderRegistry` backed by SQLite) are fine
when mutation is intentional, auditable, and tested.

## Core Principles

### KISS (Keep It Simple)

- Prefer the simplest solution that actually works
- Avoid premature optimization
- Optimize for clarity over cleverness

### DRY (Don't Repeat Yourself)

- Extract repeated logic into shared functions or utilities
- Avoid copy-paste implementation drift
- Introduce abstractions when repetition is real, not speculative

### YAGNI (You Aren't Gonna Need It)

- Do not build features or abstractions before they are needed
- Avoid speculative generality
- Start simple, then refactor when the pressure is real

## File Organization

MANY SMALL FILES > FEW LARGE FILES:
- High cohesion, low coupling
- 200-400 lines typical, 800 max
- Extract utilities from large modules
- Organize by feature/domain, not by type

## Error Handling

ALWAYS handle errors comprehensively:
- Raise domain-specific exceptions that name the refusal
- Use narrow `except` clauses (`except sqlite3.Error`, `except OSError`)
- Never silently swallow errors — a bare `except Exception: pass` hides money-losing state
- Log detailed error context on the server side

## Input Validation

ALWAYS validate at system boundaries:
- Validate all user input before processing
- Use schema-based validation where available
- Fail fast with clear error messages
- Never trust external data (API responses, user input, file content)

## Code Smells to Avoid

- **Deep nesting:** prefer early returns over nested conditionals
- **Magic numbers:** use named constants for thresholds, delays, and limits
- **Long functions:** split into focused pieces with clear responsibilities

## Code Quality Checklist

Before marking work complete:
- [ ] Code is readable and well-named (snake_case functions, PascalCase classes)
- [ ] Functions are small (<50 lines)
- [ ] Files are focused (<800 lines)
- [ ] No deep nesting (>4 levels)
- [ ] Proper error handling with narrow except clauses
- [ ] No hardcoded values (use constants or config)
