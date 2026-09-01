"""Whether THIS process is a rehearsal, declared in-process and never by env.

A trial that widens a safety gate has to be gated on something. The obvious
candidate -- "is a signing key reachable?" -- turns out to be the wrong
question twice over. It is too weak, because `.env` is the documented home for
the key and `venue.signed_client` only copies it into `os.environ` at the
moment it builds a signer, which on the live path can happen after `load()`
has run. And it is too strong, because `core_brain.shadow_run` cannot sign
*whatever* is in `.env`: it builds a credential-free client wrapped in a
deny-by-default proxy, so a key sitting on disk is irrelevant to what that
process can do.

The right question is whether this process can place an order, and that is a
property of the entrypoint, not of the filesystem. So the entrypoint declares
it, in-process, and only `core_brain.shadow_run` and the read-only ranker do.

Deliberately NOT readable from the environment. An exported variable is
inherited by every child process, including one that later builds a signer;
an in-process flag cannot leak that way, and cannot be set by an operator who
has not read the code that sets it.

This module imports nothing from the package so that both config modules can
depend on it without a cycle.
"""
from __future__ import annotations

_REHEARSAL = False


def declare_rehearsal() -> None:
    """Mark this process as unable to place an order.

    Call before `config.load()`. The only legitimate callers are
    `core_brain.shadow_run`, whose client cannot sign, and the ranker, which
    only reads books and writes a market list.
    """
    global _REHEARSAL
    _REHEARSAL = True


def is_rehearsal() -> bool:
    return _REHEARSAL


def reset_for_test() -> None:
    """Clear the flag. Tests only -- nothing in the running system clears it."""
    global _REHEARSAL
    _REHEARSAL = False
