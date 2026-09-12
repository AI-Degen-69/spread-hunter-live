# Constraints: Issue #197

## Quality & Tests
- Existing behavior outside the new KPI field must remain unchanged.
- Zero breakage of the existing 2,019 tests in `python -m pytest -q`.
- Do not skip, weaken, delete, or rewrite existing assertions merely to obtain a green suite.
- A new dedicated unit test file `tests/test_pnl_by_fill_path.py` must test the 25/35/41 attribution against a seeded SQLite DB using `pytest.approx`.
- The test must not rely on `data/01_shadow_11-09_00-37.db` since it is gitignored; it must build its own registry on `tmp_path`.

## Anti-Cheat
- When total PnL is 0.0 or closes are empty, percentages must return `None` (not 0.0) matching repo convention.
- Live `merge` method closes default to `maker_merged` because live execution has no taker signal in SQLite; shadow merges inspect multiple orders under `(pair_id, token_id)`.
- No live network calls, venue credentials, or execution loop modifications.

## Performance
- Taker pair detection must be a single efficient query or scan over the in-memory/sqlite orders table.
