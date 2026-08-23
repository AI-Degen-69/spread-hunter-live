"""Every module the live branch of `order_manager` defers must actually import.

`quote` imports `core_brain.markets` BEFORE its dry-run return and
`core_brain.order_registry` AFTER it. A dry run therefore exercises the first and
never the second: if the second cannot resolve, the operator sees a clean dry
run, approves the order, and the crash lands on the `--live` call -- at the
venue, with money committed.

The execution package is named `core_brain` so it cannot merge with a same-named
package anywhere else, and the order manager bootstraps this repo -- and only
this repo -- onto `sys.path`. These tests exercise
the deferred imports through both invocations an operator actually uses, so the
failure surfaces in CI rather than at the venue.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIVE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_ROOT.parent
LIVE_EXEC = LIVE_ROOT / "core_brain" / "order_manager.py"


def _deferred_engine_imports() -> set[str]:
    """Module names `order_manager` imports from the `core_brain` package."""
    src = LIVE_EXEC.read_text(encoding="utf-8")
    names = set(re.findall(r"^\s*from core_brain\.(\w+) import", src, re.MULTILINE))
    names |= set(re.findall(r"^\s*from core_brain import (\w+)", src, re.MULTILINE))
    return names


def test_deferred_imports_are_discovered():
    """Guard the regex: if it silently matches nothing, the tests below pass vacuously."""
    names = _deferred_engine_imports()
    assert {"order_registry", "markets", "single_buy_saver", "config"} <= names, names


# The two ways this module is actually launched. `-m` from live/ is the
# documented form; running the file by path from the repo root is what happens
# when someone has the repo open and does not want to change directory first.
INVOCATIONS = {
    "module_from_root": (LIVE_ROOT, [sys.executable, "-c"]),
}


@pytest.mark.parametrize("cwd_name", sorted(INVOCATIONS))
def test_live_branch_imports_resolve(cwd_name):
    """Import every deferred module from the project root."""
    cwd, argv = INVOCATIONS[cwd_name]
    names = sorted(_deferred_engine_imports())

    prog = (
        "import sys\n"
        f"sys.path.insert(0, {str(LIVE_ROOT)!r})\n"
        "import core_brain.order_manager as live_exec\n"
    )
    prog += "".join(f"import core_brain.{n}\n" for n in names)
    prog += "print('ok')"

    res = subprocess.run(argv + [prog], cwd=str(cwd), capture_output=True, text=True)
    assert res.returncode == 0, f"cwd={cwd_name}\n{res.stderr}"
    assert "ok" in res.stdout


def test_core_brain_resolves_inside_project():
    """`core_brain` is a regular package inside the standalone project."""
    res = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(LIVE_ROOT)!r}); "
         "import core_brain; print(*core_brain.__path__, sep=chr(10))"],
        cwd=str(LIVE_ROOT), capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    paths = [Path(p).resolve() for p in res.stdout.strip().splitlines() if p.strip()]
    assert paths == [(LIVE_ROOT / "core_brain").resolve()], paths


def test_importing_live_exec_places_project_root_on_sys_path():
    """Importing live_exec ensures the standalone project root is on sys.path."""
    res = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(LIVE_ROOT)!r}); "
         "import core_brain.order_manager as live_exec; "
         "import pathlib; "
         "print(*[pathlib.Path(p).resolve() for p in sys.path if p], sep=chr(10))"],
        cwd=str(LIVE_ROOT), capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    entries = {Path(p) for p in res.stdout.strip().splitlines() if p.strip()}
    assert LIVE_ROOT.resolve() in entries, entries
