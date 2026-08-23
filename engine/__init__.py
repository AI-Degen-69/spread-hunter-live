"""The live execution engine.

This package is named `engine`, not `strategy`, and the name is the point.

An earlier layout had a directory called `strategy/` with no `__init__.py`, so
Python could merge it with any same-named directory on `sys.path` into one
implicit namespace package. Live code writing `from engine.markets import ...`
could then resolve somewhere else entirely, and whether it resolved at all
depended on the operator's working directory at the moment a real order was
about to go out.

Sealing a directory with an `__init__.py` under the same name does not fix it:
a regular package wins over a namespace package and stops extending across
directories, so the other tree becomes unreachable and every import of it in
the same process fails. Renaming is the fix; sealing is not.

`config.py` and `markets.py` started as copies of the paper-run modules. They
are this repo's own files now and are tuned for live money without regard to
anything outside it.
"""
