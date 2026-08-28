"""Statistical Validation Run: full trading pipeline against live CLOB without spending."""
from __future__ import annotations

from statistical_validation_run.run import (
    count_closes,
    count_matured_markouts,
    make_hybrid_deadline_sleep,
    main,
)

__all__ = [
    "count_closes",
    "count_matured_markouts",
    "make_hybrid_deadline_sleep",
    "main",
]
