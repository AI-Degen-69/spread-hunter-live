---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
# Python Coding Style

> This file extends [common/coding-style.md](common-coding-style.md) with Python-specific
> content. See also [docs/agents/python-conventions.md](../../docs/agents/python-conventions.md),
> which is the authoritative reference and wins on any conflict.

## Standards

- Follow **PEP 8** conventions
- Use **type annotations** on all function signatures
- Open modules with `from __future__ import annotations`

## Imports

Absolute, rooted at the repo. `pytest.ini` sets `pythonpath = .`.

```python
from core_brain.order_registry import OrderRegistry, DEFAULT_DB_PATH
from core_brain.quotes import Inventory, QuoteIntent
```

No relative imports. No wildcard imports in new code.

## Immutability

Prefer immutable data structures for value objects:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class User:
    name: str
    email: str

from typing import NamedTuple

class Point(NamedTuple):
    x: float
    y: float
```

## Formatting

- **black** for code formatting
- **isort** for import sorting
- **ruff** for linting
