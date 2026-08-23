"""PreToolUse guard for the git and GitHub rules in docs/agents/git-workflow.md.

Those rules govern actions that reach GitHub and cannot be undone from here: a
push starts a paid review round, a merge lands on `main`. They live in a file
that AGENTS.md links to rather than imports, so an agent that reads only as far
as its immediate question -- how do I open a pull request -- never reaches them.
This guard puts them in front of the command instead of in a file beside it.

It is a speed bump, not a security boundary. Anything running here can record an
approval or bypass the hook. What it buys is that a protected merge cannot
happen by accident, or without an explicit act bound to a specific commit.

Contract: reads the PreToolUse JSON payload on stdin. Exit 0 allows the command
and prints any reminder to stdout; exit 2 blocks it and prints the reason to
stderr. Anything unexpected exits 0, because a broken guard must not wedge the
repo -- every shell-out and every parse degrades to allow.

Recording an operator approval, which the message on a blocked merge repeats:

    python scripts/hooks/git_workflow_guard.py --approve <pr-number>

The approval is bound to the pull request's head commit, so it lapses the moment
anything else is pushed to the branch.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

# Paths whose review findings always block a merge, and which need operator
# sign-off before merging however green the checks are.
SIGN_OFF_PATHS = ("core_brain/", "scoring/", "dashboard/server.py")

# A fourth automatic review means stop, not grind.
MAX_REVIEW_ROUNDS = 3

DOC = "docs/agents/git-workflow.md"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APPROVALS = REPO_ROOT / "runtime" / "merge-approvals.json"

# Global options accepted before a subcommand. True when the option consumes
# the following token as its argument.
GLOBAL_OPTS = {
    "-C": True, "-c": True, "--git-dir": True, "--work-tree": True,
    "--namespace": True, "--exec-path": True, "--config-env": True,
    "-R": True, "--repo": True,
    "--no-pager": False, "--paginate": False, "--bare": False,
    "--literal-pathspecs": False,
}

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


# ---------------------------------------------------------------------------
# command parsing
# ---------------------------------------------------------------------------

def split_segments(command: str) -> list[str]:
    """Split a shell line on the operators that separate whole commands.

    `cd x && git push` has to route on its second segment rather than fail to
    match because the line does not begin with `git`.
    """
    return [seg for seg in re.split(r"&&|\|\||[;|\n]", command) if seg.strip()]


def tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=False)
    except ValueError:
        return segment.split()


def strip_global_opts(tokens: list[str]) -> list[str]:
    """Drop global options so `git -C /repo push` routes like `git push`."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            name = token.split("=", 1)[0]
            index += 2 if ("=" not in token and GLOBAL_OPTS.get(name, False)) else 1
            continue
        out.append(token)
        index += 1
    return out


def _program(token: str) -> str:
    name = Path(token.strip("\"'")).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def classify(command: str) -> tuple[str, str | None]:
    """Return the action a command performs, plus any pull request number.

    One of "merge", "push", "create" or "". Merge wins if a line somehow holds
    more than one, being the irreversible end of the list.
    """
    action = ""
    pr: str | None = None
    for segment in split_segments(command):
        words = strip_global_opts(tokenize(segment))
        if not words:
            continue
        program, rest = _program(words[0]), words[1:]
        if program == "git" and rest[:1] == ["push"]:
            action = action or "push"
        elif program == "gh" and rest[:2] == ["pr", "merge"]:
            action = "merge"
            pr = next((w for w in rest[2:] if w.isdigit()), None)
        elif program == "gh" and rest[:2] == ["pr", "create"]:
            action = action or "create"
    return action, pr


# ---------------------------------------------------------------------------
# github lookups -- every one degrades to allow
# ---------------------------------------------------------------------------

def _run(args: list[str]) -> str:
    """Return stdout, or an empty string if the command fails for any reason."""
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _pr_json(pr: str | None, fields: str) -> dict:
    args = ["gh", "pr", "view"] + ([pr] if pr else []) + ["--json", fields]
    raw = _run(args)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _open_pr_for_head() -> str | None:
    data = _pr_json(None, "number,state")
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


def _head_sha(pr: str) -> str:
    return str(_pr_json(pr, "headRefOid").get("headRefOid") or "")


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------

def load_approvals() -> dict:
    try:
        data = json.loads(APPROVALS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_approved(pr: str, head: str) -> bool:
    """True when this exact commit was signed off.

    An approval recorded against an earlier commit does not carry forward: a
    push after sign-off has to be signed off again.
    """
    entry = load_approvals().get(str(pr))
    if not isinstance(entry, dict) or not head:
        return False
    return entry.get("head_sha") == head


def record_approval(pr: str) -> int:
    head = _head_sha(pr)
    if not head:
        print(f"Could not read the head commit of pull request #{pr}.",
              file=sys.stderr)
        return 1
    approvals = load_approvals()
    approvals[str(pr)] = {
        "head_sha": head,
        "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        APPROVALS.parent.mkdir(parents=True, exist_ok=True)
        APPROVALS.write_text(json.dumps(approvals, indent=2) + "\n",
                             encoding="utf-8")
    except OSError as exc:
        print(f"Could not write {APPROVALS}: {exc}", file=sys.stderr)
        return 1
    print(f"Recorded approval for pull request #{pr} at commit {head[:7]}.")
    print("It lapses if anything else is pushed to the branch.")
    return 0


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

def check_push() -> tuple[int, str]:
    pr = _open_pr_for_head()
    if pr is None:
        return 0, ""
    rounds = _review_count(pr)
    if rounds >= MAX_REVIEW_ROUNDS:
        return 2, (
            f"BLOCKED: pull request #{pr} has had {rounds} automatic reviews. "
            f"A fourth means stop, not grind ({DOC}, 'Stop condition').\n\n"
            "Decline the remaining minors in one summary comment and merge, or "
            "explain to the operator why another round is warranted and ask "
            "them to confirm this push.\n\n" + ROUND_RULES
        )
    return 0, (f"Pull request #{pr} already has {rounds} automatic review(s).\n\n"
               f"{ROUND_RULES}")


def check_merge(pr: str | None) -> tuple[int, str]:
    pr = pr or _open_pr_for_head()
    if pr is None:
        return 0, MERGE_RULES
    flagged = sorted({p for p in _changed_paths(pr)
                      for s in SIGN_OFF_PATHS if p.startswith(s)})
    if not flagged:
        return 0, f"Pull request #{pr} touches no sign-off paths.\n\n{MERGE_RULES}"

    head = _head_sha(pr)
    if is_approved(pr, head):
        return 0, (f"Pull request #{pr} touches sign-off paths and is approved "
                   f"at commit {head[:7]}.\n\n{MERGE_RULES}")
    return 2, (
        f"BLOCKED: pull request #{pr} touches paths that need operator "
        f"sign-off before merging ({DOC}, 'Merging'):\n"
        + "".join(f"  {p}\n" for p in flagged)
        + "\nAsk the operator to confirm. Once they have, record it with:\n\n"
        f"    python scripts/hooks/git_workflow_guard.py --approve {pr}\n\n"
        "The approval is bound to the current head commit and lapses on the "
        "next push.\n\n" + MERGE_RULES
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--approve":
        return record_approval(argv[1])

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    # Valid JSON of an unexpected shape is still unexpected input: allow it.
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command.strip():
        return 0

    action, pr = classify(command)
    if action == "merge":
        code, message = check_merge(pr)
    elif action == "push":
        code, message = check_push()
    elif action == "create":
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
