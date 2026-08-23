"""Backward-compatible alias for scripts.filter_loop."""
from scripts.filter_loop import *  # noqa: F401, F403
from scripts.filter_loop import main

if __name__ == "__main__":
    main()
