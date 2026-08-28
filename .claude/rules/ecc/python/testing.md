---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
# Python Testing

> This file extends [common/testing.md](common-testing.md) with Python-specific content.
> See also [docs/agents/python-conventions.md](../../docs/agents/python-conventions.md)
> for hermetic conftest details.

## Framework

Use **pytest** as the testing framework.

## Running Tests

```bash
python -m pytest -q
```

## Coverage

```bash
pytest --cov=core_brain --cov-report=term-missing
```

## Hermetic Environment

`tests/conftest.py` is hermetic:
- Scrubs credentials from `os.environ`
- Blocks every non-loopback socket
- Opt out per test with `@pytest.mark.allow_network`
- Platform-specific tests carry `@pytest.mark.skipif(sys.platform ...)`

## Test Organization

Use `pytest.mark` for test categorization:

```python
import pytest

@pytest.mark.unit
def test_calculate_spread():
    ...

@pytest.mark.integration
def test_database_connection():
    ...
```
