# Testing Requirements

## Minimum Test Coverage: 80%

Test Types (ALL required):
1. **Unit Tests** - Individual functions, utilities, data transformations
2. **Integration Tests** - Database operations, API endpoints, venue interactions
3. **E2E Tests** - Critical user flows (shadow run, dashboard)

## Test-Driven Development

MANDATORY workflow:
1. Write test first (RED)
2. Run test - it should FAIL
3. Write minimal implementation (GREEN)
4. Run test - it should PASS
5. Refactor (IMPROVE)
6. Verify coverage (80%+)

## Troubleshooting Test Failures

1. Check test isolation (hermetic conftest scrubs env vars, blocks sockets)
2. Verify mocks are correct
3. Fix implementation, not tests (unless tests are wrong)

## Test Structure (AAA Pattern)

Prefer Arrange-Act-Assert structure for tests:

```python
def test_calculates_spread_correctly():
    # Arrange
    best_ask_up = 0.42
    best_ask_down = 0.55

    # Act
    spread = best_ask_up + best_ask_down

    # Assert
    assert spread < 1.0
    assert round(1.0 - spread, 2) == 0.03
```

### Test Naming

Use descriptive names that explain the behavior under test:

```python
def test_returns_empty_when_no_markets_match_query(): ...
def test_raises_when_api_key_is_missing(): ...
def test_falls_back_to_cached_depth_when_venue_is_down(): ...
```
