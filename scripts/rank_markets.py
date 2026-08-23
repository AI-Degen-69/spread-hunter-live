"""Backward-compatible alias for scripts.filter_markets."""
from scripts.filter_markets import *  # noqa: F401, F403
from scripts.filter_markets import main

if __name__ == "__main__":
    main()
