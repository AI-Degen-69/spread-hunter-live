"""Regression: shadow_run's CLI must accept --run-id.

The menu launches the rehearsal loop with `--run-id shadow-NN` (matching the
statistics observer's run id). If argparse rejects the flag the loop exits
before placing any quote, so the shadow store records nothing and the
dashboard's market inspection / orderbook panel stays empty.
"""

from core_brain import shadow_run


def test_shadow_run_accepts_run_id_flag():
    args = shadow_run._parse_args([
        "--minutes", "1", "--db", "data/x.db",
        "--run-id", "shadow-01", "--max-markets", "2",
    ])
    assert args.run_id == "shadow-01"