# Plan: Shadow Run — Milestone 1, money-free entrypoint

**Source PRD**: `.claude/prds/shadow-run.prd.md`
**Selected Milestone**: 1 — Money-free entrypoint
**Complexity**: Medium

## Summary

Add `python -m core_brain.shadow_run --minutes N`: a time-boxed rotation over the real
graduated market list, reading real markets and real books through the existing
`VenueSeam`, writing to its own registry, and structurally incapable of signing anything.
The loop's decision logic, config and risk caps are untouched — shadow mode swaps only the
seam's two write ports (`submit_fn`, `cancel_fn`) for recorders and swaps the client for
one that has no key. Fill inference, dashboard badging and the self-audit summary are
milestones 3–6; this milestone ends when a shadow run rotates markets, records what it
would have submitted, and exits at the time box having touched neither money nor
`data/orders.db`.

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Injectable venue seam | `core_brain/trader_loop.py:130` | `VenueSeam` holds every venue-touching port as a `Callable`; `run` reads ports off the seam so tests build one with fakes and production builds one with real calls. Shadow mode is a third seam construction — not a new loop. |
| Entrypoint shape | `core_brain/trader_loop.py:606` | `main(argv)` with `argparse`, `logging.basicConfig` to stderr with `%H:%M:%S`, config via `core_brain.config.load()`, registry path via `--db`, returns an int exit code, `if __name__ == "__main__"` at the bottom. |
| Structural prohibition | `tests/conftest.py:90` | `ProductionRegistryWriteError(BaseException)` — deriving from `BaseException`, not `Exception`, so a blanket `except Exception` cannot swallow it. `sqlite3.connect` is wrapped and the check runs before the real call. This is exactly the shape the no-signer guard needs. |
| Path normalisation before a guard decides | `tests/conftest.py:123` | `_is_production_registry` percent-decodes, strips the `file:` scheme, handles a `localhost` authority, and compares resolved absolute paths — because a guard that only strips the scheme is bypassable. Any shadow-store guard must normalise the same way. |
| Credential handling | `core_brain/venue.py:65` | The key is read from the environment and passed on in a single expression — never bound to a module global, never returned, never logged. Shadow mode must not weaken this; it simply never reaches the read. |
| Caps sourced from config | `core_brain/venue.py:25` | `MAX_ORDER_USD` / `MAX_TOTAL_USD` are re-exported from `MakerConfig` so there is one config object. Shadow must import the same constants, never redefine them. |
| Degrade, do not stop | `core_brain/trader_loop.py:156` | `run` isolates reconcile, sweep and each market visit so one failure degrades the cycle instead of killing the loop. Shadow inherits this by reusing `run`. |
| Tests | `tests/test_trader_loop.py`, `tests/test_production_registry_guard.py` | `tests/test_*.py`, pytest, plain functions, docstring names the regression being prevented, `pytest.raises(BaseException, match=...)` for guard tests, `tmp_path` for db paths. |
| Hermetic test environment | `tests/conftest.py` | Autouse fixtures scrub credentials from `os.environ` and block non-loopback sockets. Shadow tests need no network and must not opt into `@pytest.mark.allow_network`. |

**No existing pattern for**: a non-live entrypoint for the rotation loop. `--no-live` on
`trader_loop` is a flag on a LIVE-by-default command, which is precisely what the PRD
rejects. Shadow mode is a new module, not a flag.

## Files to Change

| File | Action | Why |
|---|---|---|
| `core_brain/shadow_run.py` | CREATE | The entrypoint. Argparse, time box, seam construction, shadow client, shadow store wiring. |
| `core_brain/shadow_guard.py` | CREATE | `ShadowSafetyViolation(BaseException)` plus the no-signer client and the shadow-store path guard. Separate module so the guard is importable by tests and by later milestones without importing the whole entrypoint. |
| `tests/test_shadow_guard.py` | CREATE | Proves the guard fires on every signing and write path, and that `BaseException` inheritance survives a blanket `except Exception`. |
| `tests/test_shadow_run.py` | CREATE | Proves the entrypoint constructs a read-only seam, honours the time box, writes only to the shadow store, and uses live config and caps unchanged. |
| `docs/agents/safety.md` | UPDATE | Shadow run is a new command class: pre-approved for an agent to run, unlike every other loop command. The safety doc is the source of truth for what an agent may run. |
| `AGENTS.md` | UPDATE | Add `python -m core_brain.shadow_run` to the Commands block so it is discoverable. |

**Not touched in this milestone**: `core_brain/trader_loop.py`, `core_brain/venue.py`,
`core_brain/quotes.py`, `core_brain/order_manager.py`, `dashboard/server.py`. If the plan
starts requiring an edit to any of these, stop — that is a signal the seam is being
bypassed rather than used.

## Tasks

### Task 1: `ShadowSafetyViolation` and the no-signer client

- **Action**: In `core_brain/shadow_guard.py`, define
  `ShadowSafetyViolation(BaseException)`. Define `shadow_client(funder=None)` returning a
  read-only CLOB client built **without** ever reading `POLY_PRIVATE_KEY` / `POLY_KEY`.
  Wrap it in a proxy that allows the read methods the loop needs (market fetch, book
  fetch, `get_open_orders`, `get_trades`) and raises `ShadowSafetyViolation` on every
  write method — `post_order`, `post_orders`, `create_and_post_market_order`,
  `create_order`, `cancel`, `cancel_all`, `cancel_orders`, and anything whose name starts
  with `post_`, `create_`, or `cancel`. Deny by default: an unknown attribute raises
  rather than passes through.
- **Mirror**: `tests/conftest.py:90` for the `BaseException` class and the
  guard-before-the-real-call structure; `core_brain/venue.py:65` for how a client is
  built, minus the key read.
- **DECIDED — no credentials at all.** The shadow client reads no key and no L2 API creds.
  `api_creds_from_env()` (`core_brain/venue.py:110`) is not called. If a read endpoint
  turns out to require authentication, that is a finding to report to the operator, not a
  reason to load credentials: the safety guarantee is that a shadow run holds nothing it
  could authenticate with. Note in the module docstring which endpoints work unauthenticated.
- **Validate**: `python -m pytest -q tests/test_shadow_guard.py`

### Task 2: Shadow store, and a guard that keeps shadow off the production registry

- **Action**: Default shadow registry path `data/shadow.db`, overridable with `--db`. Add
  `assert_not_production_registry(path)` to `shadow_guard.py`, raising
  `ShadowSafetyViolation` when the resolved path is `data/orders.db`. Normalise before
  comparing: percent-decode, strip a `file:` scheme, handle a `localhost` authority,
  resolve to an absolute path. Call it in `shadow_run.main` before constructing the
  `OrderRegistry`. Initialise the shadow db with the existing `init_db` so the schema
  matches — no bespoke schema.
- **Mirror**: `tests/conftest.py:123` for the normalisation; `core_brain/order_registry.py:496`
  (`init_db`) and `:666` (`OrderRegistry(db_path=...)`) for construction.
- **Note**: the production registry stays readable if a later milestone needs live
  inventory context — the guard blocks it as a *shadow write target*, not as a read.
  Milestone 1 does not read it.
- **Validate**: `python -m pytest -q tests/test_shadow_run.py -k registry`

### Task 3: The entrypoint

- **Action**: `core_brain/shadow_run.py` with `main(argv)`:
  `--minutes` (float, default 5.0), `--interval` (default 5.0, matching the live loop),
  `--db` (default `data/shadow.db`), `--max-markets`, `--funder`. Load config with
  `core_brain.config.load()` — **unchanged**, same gates, same `MAX_ORDER_USD` /
  `MAX_TOTAL_USD`. Resolve markets with the same graduated-list path the live loop uses.
  Build a `VenueSeam` with: real `fetch_market`, real `fetch_books`, real `decide`,
  the shadow client, the shadow registry, `submit_fn`/`cancel_fn` replaced by recorders
  (this milestone: record the intent and return a count; milestone 2 gives them their full
  shape), `reconcile_fn`/`sweep_fn` set to no-ops (they reconcile against venue positions
  that do not exist in shadow). Log a banner at startup naming the mode, the store, the
  time box and the fact that no signer is loaded.
- **Mirror**: `core_brain/trader_loop.py:606` end to end — argparse style, logging setup,
  bankroll read, market spec resolution, seam construction, exit code.
- **Open question to resolve here**: whether the live bankroll read
  (`account.fetch_live_balance`) should run in shadow. It is a read, and using the real
  balance keeps sizing true to live — but it needs the funder address. Default: attempt
  it, fall back to config bankroll on any failure, exactly as the live loop already does.
- **Validate**: `python -m pytest -q tests/test_shadow_run.py`

### Task 4: The time box

- **Action**: `run` (`core_brain/trader_loop.py:156`) takes `interval`, `once` and an
  injectable `sleep_fn`, but has no deadline. Do **not** add one to `run` — that edits the
  live loop. Drive the time box from `shadow_run` with a `sleep_fn` that raises once the
  wall clock passes the deadline.
- **DECIDED — the signal is `_Deadline(KeyboardInterrupt)`.** `trader_loop.py:238` wraps
  the sleep call in `except KeyboardInterrupt: break` and nothing else — no
  `except Exception` — and that break is the loop's designed clean exit, returning
  `last_cycle` normally. Subclassing `KeyboardInterrupt` means the deadline walks through
  that existing door with a meaningful name, `run` returns results rather than raising,
  and `trader_loop.py` needs no edit. A plain `Exception` would escape `run` uncaught and
  lose the rotation's results; a bare `BaseException` subclass would not be caught by that
  handler at all.
- **Mirror**: `core_brain/trader_loop.py:238` — the existing `KeyboardInterrupt` clean-exit
  contract, reused rather than duplicated.
- **Validate**: a test with a fake clock asserting the run stops within one interval of
  the deadline and returns 0.

### Task 5: Docs

- **Action**: Add shadow run to the AGENTS.md Commands block, and to
  `docs/agents/safety.md` as the one loop command an agent may run without operator
  sign-off — with the reason stated: no signer is loaded, so it cannot reach the venue's
  write path. State plainly that shadow numbers are rehearsal, not results.
- **Mirror**: existing entries in both files.
- **Validate**: read back; no command is added to the pre-approved list whose module can
  construct a signing client.

## Validation

```bash
python -m pytest -q
```

```bash
python -m pytest -q tests/test_shadow_guard.py tests/test_shadow_run.py
```

Operator check, after the suite is green — this reaches the network read-only:

```powershell
python -m core_brain.shadow_run --minutes 1 --max-markets 3
```

Expected: a banner naming shadow mode and `data/shadow.db`, one or more rotations logged
with decide outcomes, exit code 0 at roughly 60 seconds, `data/orders.db` mtime unchanged.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| A read path in the SDK lazily constructs a signer, so "no key loaded" is not the guarantee it appears to be | Medium | Deny-by-default proxy: unknown attributes raise rather than pass through. Test asserts the specific write methods raise, and that the environment key vars are never read during a shadow run. |
| ~~The deadline signal is swallowed by `run`'s error isolation~~ — **checked, does not apply**. `trader_loop.py:238` catches only `KeyboardInterrupt` around the sleep call | Resolved | `_Deadline(KeyboardInterrupt)` uses that existing clean-exit path. Still test with a fake clock that the run stops within one interval of the deadline and returns 0. |
| Shadow store diverges from the live schema and later milestones cannot reuse registry queries | Medium | Use `init_db` and `OrderRegistry` unchanged with a different path. No bespoke schema. |
| Reconcile and sweep set to no-op hides a stage the PRD wants exercised | Medium | Loop-health reporting (milestone 6) must name reconcile and sweep as deliberately skipped, not as stages that silently passed. Record the decision now so it is not lost. |
| **`live_fill_engine.py:6` states a CRITICAL INVARIANT: `on_book()` must never infer a fill from book deltas, price changes or trade tape** — which is exactly what the confirmed shadow fill rule does | High | Not milestone 1's problem, but do not resolve it by relaxing that invariant. Shadow needs its own fill engine; `LiveFillEngine` must keep refusing to infer. Flagged here so milestone 3 does not discover it late. |
| An operator mistakes a shadow run for a live one, or runs both at once | Medium | Startup banner, distinct store, distinct process name. Concurrency policy is still an open PRD question — do not silently permit it; log loudly if a live process registry entry is active. |

## Acceptance

- [ ] All tasks complete
- [ ] `python -m pytest -q` green
- [ ] Every new behaviour has a test that fails without the change
- [ ] `core_brain/trader_loop.py`, `venue.py`, `quotes.py`, `order_manager.py` and
      `dashboard/server.py` are unmodified
- [ ] A shadow run leaves `data/orders.db` byte-identical
- [ ] No shadow code path reads `POLY_PRIVATE_KEY` or `POLY_KEY`
- [ ] Patterns mirrored, not reinvented — seam reused, `init_db` reused, guard shaped like
      the conftest one
- [ ] **How to verify** block written for the operator, per `docs/agents/verifying.md`

---
*Status: AWAITING CONFIRMATION — no code written.*
