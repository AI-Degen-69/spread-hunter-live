"""PreToolUse guard for the git and GitHub rules in docs/agents/git-workflow.md.

Those rules govern actions that reach GitHub and cannot be undone from here: a
push starts a paid review round, a merge lands on `main`. They live in a file
that AGENTS.md links to rather than imports, so an agent that reads only as far
as its immediate question -- how do I open a pull request -- never reaches them.
This guard puts them in front of the command instead of in a file beside it.

Contract: reads the PreToolUse JSON payload on stdin. Exit 0 allows the command
and prints any reminder to stdout; exit 2 blocks it and prints the reason to
stderr. Anything unexpected exits 0, because a broken guard must not wedge the
repo.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

# Paths whose review findings always block a merge, and which need operator
# sign-off before merging regardless of how green the checks are.
SIGN_OFF_PATHS = ("core_brain/", "scoring/", "dashboard/server.py")

DOC = "docs/agents/git-workflow.md"

ROUND_RULES = f"""\
Pushing to a branch with an open pull request starts another automatic review.
Before this push, from {DOC}:

  * Batch every accepted fix into ONE commit and push ONCE. Each push is its own
    incremental review, so four pushes cost four reviews for one round.
  * Post ONE summary comment per round: what changed, what you declined, why.
    The `@coderabbitai` handle must not appear anywhere in that comment.
  * Post `@coderabbitai resolve` as a SEPARATE comment.
  * Never post `@coderabbitai review` or `full review`. Reviews fire on their own.
"""

MERGE_RULES = f"""\
Before merging, from {DOC}:

  * Read the full diff -- `gh pr diff <n>` -- not just the checks.
  * Check what else is in the stack. A stacked merge can carry another pull
    request onto `main` with it.
  * Routine changes (docs, tests, tooling, dashboard cosmetics) may merge once
    CI is green and the review is clear. Order sizing, fill attribution, risk
    limits, the merge path and live execution wait for operator sign-off.
"""


def _run(args: list[str]) -> str:
    """Return stdout, or an empty string if the command fails for any reason."""
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _pr_number_for_head() -> str | None:
    """The open pull request for the current branch, if there is one."""
    raw = _run(["gh", "pr", "view", "--json", "number,state"])
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if data.get("state") != "OPEN":
        return None
    number = data.get("number")
    return str(number) if number is not None else None


def _review_count(pr: str) -> int:
    raw = _run(["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr}/reviews",
                "--jq", "length"])
    try:
        return int(raw.strip())
    except ValueError:
        return 0


def _changed_paths(pr: str) -> list[str]:
    raw = _run(["gh", "pr", "diff", pr, "--name-only"])
    return [line.strip() for line in raw.splitlines() if line.strip()]


def check_push() -> tuple[int, str]:
    pr = _pr_number_for_head()
    if pr is None:
        return 0, ""
    rounds = _review_count(pr)
    note = f"Pull request #{pr} already has {rounds} automatic review(s).\n\n{ROUND_RULES}"
    if rounds >= 3:
        # Three rounds is a runaway guard, not a target: a fourth review means
        # something is wrong with the change or the filters.
        return 2, (
            f"BLOCKED: pull request #{pr} has had {rounds} automatic reviews. "
            f"A fourth means stop, not grind ({DOC}, 'Stop condition').\n\n"
            "Decline the remaining minors in one summary comment and merge, or "
            "explain to the operator why another round is warranted and ask "
            "them to confirm this push.\n\n" + ROUND_RULES
        )
    return 0, note


def check_merge(command: str) -> tuple[int, str]:
    match = re.search(r"\bgh\s+pr\s+merge\s+(\d+)", command)
    pr = match.group(1) if match else _pr_number_for_head()
    if pr is None:
        return 0, MERGE_RULES
    touched = _changed_paths(pr)
    flagged = sorted({p for p in touched
                      for s in SIGN_OFF_PATHS if p.startswith(s)})
    if flagged:
        return 2, (
            f"BLOCKED: pull request #{pr} touches paths that need operator "
            f"sign-off before merging ({DOC}, 'Merging'):\n"
            + "".join(f"  {p}\n" for p in flagged)
            + "\nAsk the operator to confirm, then merge on their say-so.\n\n"
            + MERGE_RULES
        )
    return 0, f"Pull request #{pr} touches no sign-off paths.\n\n{MERGE_RULES}"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not isinstance(command, str) or not command:
        return 0

    if re.search(r"\bgh\s+pr\s+merge\b", command):
        code, message = check_merge(command)
    elif re.search(r"\bgit\s+push\b", command):
        code, message = check_push()
    elif re.search(r"\bgh\s+pr\s+create\b", command):
        code, message = 0, (
            "Opening a pull request: the title is the literal string "
            "`@coderabbitai` and the body must contain the line "
            f"`@coderabbitai summary`. Write the body file outside the repo. ({DOC})"
        )
    else:
        return 0

    if message:
        print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
