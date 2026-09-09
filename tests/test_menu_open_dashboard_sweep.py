"""Option 3 (host live dashboard) survives a menu launched outside the repo root.

`Open-Dashboard -Mode "live"` runs the account sweep as a direct call:

    & python -m core_brain.order_manager account-sweep --quiet

That call inherits the menu's current location. Every `Start-Process
-FilePath "python"` in the same script passes
`-WorkingDirectory $ProjectPath`; this direct call did not. Launching the
menu from any cwd other than the repo root therefore died with:

    ModuleNotFoundError: No module named 'core_brain'

exit code 1, and the operator got "using local registry marks" even though
the venue was reachable. The dashboard itself still opened (it launches
with an explicit working directory), which is why the failure read as a
tolerable warning instead of the cwd bug it was.

The same flaw existed in the statistical-run path's direct call:

    & python -m scripts.rank_markets

Journeys under test:
1. As the operator, the live account-sweep invocation pins the repo root
   (Push-Location $ProjectPath, an explicit -WorkingDirectory, or a
   PYTHONPATH rooted at $ProjectPath) instead of inheriting my shell cwd.
2. As the operator, the statistical rank_markets invocation pins the repo
   root the same way.
"""
from __future__ import annotations

import re
from pathlib import Path

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"

# A cwd pin inside the lifted function body: entering $ProjectPath for the
# direct `& python` call, or pointing Python at it explicitly.
_CWD_PIN = re.compile(
    r"Push-Location\s+\$ProjectPath"
    r"|Set-Location\s+\$ProjectPath"
    r"|-WorkingDirectory\s+\$ProjectPath"
    r"|PYTHONPATH.*\$ProjectPath",
    re.IGNORECASE,
)


def _function_source(name: str) -> str:
    """Lift just `function <name> { ... }` out of the menu script.

    The menu script starts a live control centre when dot-sourced, so tests
    inspect the one function under test on its own.
    """
    text = MENU.read_text(encoding="utf-8")
    match = re.search(
        rf"^function {re.escape(name)} \{{.*?^\}}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name} is no longer defined in the menu script"
    return match.group(0)


def _direct_python_calls(body: str) -> list[str]:
    """Direct `& python -m <module>` calls (inherit the caller cwd).

    `Start-Process -FilePath "python" ... -WorkingDirectory $ProjectPath`
    launches are safe and excluded; only bare `& python` / `& py` calls
    that depend on the shell's current directory are returned.
    """
    return [
        line.strip()
        for line in body.splitlines()
        if re.match(r"&\s+(python|py)(\.exe)?\s+-m\s+", line.strip(), re.IGNORECASE)
    ]


def test_live_sweep_does_not_inherit_the_shell_cwd():
    body = _function_source("Open-Dashboard")
    calls = _direct_python_calls(body)
    assert any(
        "core_brain.order_manager" in c and "account-sweep" in c for c in calls
    ), "Open-Dashboard no longer sweeps via core_brain.order_manager"
    # Locality: the pin must sit next to the call, not merely anywhere in
    # the function body.
    idx = body.index("core_brain.order_manager")
    window = body[max(0, idx - 1200): idx + 400]
    assert _CWD_PIN.search(window), (
        "Open-Dashboard runs `& python -m core_brain.order_manager account-sweep` "
        f"with the caller's cwd; launching the menu outside the repo root dies with "
        f"ModuleNotFoundError: {calls}"
    )


def test_statistical_ranker_does_not_inherit_the_shell_cwd():
    text = MENU.read_text(encoding="utf-8")
    assert "& python -m scripts.rank_markets" in text, (
        "statistical-run no longer ranks via scripts.rank_markets"
    )
    # The rank call lives in the statistical-run switch arm, not inside a
    # named function, so guard a window around the call site itself.
    idx = text.index("& python -m scripts.rank_markets")
    window = text[max(0, idx - 1200): idx + 400]
    assert _CWD_PIN.search(window), (
        "statistical-run executes `& python -m scripts.rank_markets` with the "
        "caller's cwd; launching the menu outside the repo root dies with "
        "ModuleNotFoundError"
    )
