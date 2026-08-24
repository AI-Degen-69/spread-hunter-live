# TDD evidence report: shadow run (Milestone 1)

**Source plan**: `.claude/plans/shadow-run.plan.md`
**Branch**: `add/shadow-run-demo`
**Date**: 2026-08-24

## User journeys

1. As the operator, I want to watch the full loop work against the live book
   without spending money, so I can see the machine before trusting it with funds.
2. As an agent, I want a loop command I am allowed to run unattended, so rehearsal
   does not need supervision that only exists because the command might spend.
3. As the operator, I want `data/orders.db` structurally untouchable by a shadow
   run, so fabricated fills can never corrupt real order history.

## Task -> test -> RED -> GREEN mapping

| Plan task | Test target | RED evidence | GREEN evidence |
|---|---|---|---|
| 1. `ShadowSafetyViolation`, no-signer client | `tests/test_shadow_guard.py` (11 parametrised bypass cases, 25 tests total) | Prior session RED; all green at start of this run | `python -m pytest -q tests/test_shadow_guard.py` → 25 passed |
| 2. Shadow store + registry guard | same parametrised block | Same | Same run |
| 4. Time box (`_Deadline`) | `tests/test_shadow_run.py::TestDeadline` (4 tests) | Same | `python -m pytest -q tests/test_shadow_run.py::TestDeadline` → 4 passed |
| 3. Entrypoint | `tests/test_shadow_run.py::TestRunShadow` (6), `TestMain` (4); 14 in the file | Commit `707654a`: 9 failed / 4 passed — `ImportError: cannot import name 'run_shadow'/'build_shadow_seam'/'main'`, i.e. failure caused solely by the missing implementation | Commit `06f8215`; review-round wrap regression RED then GREEN (see below) |
| 5. Docs | Read-back review | n/a (documentation) | AGENTS.md Commands block; `docs/agents/safety.md` §3a |

Review round 1 additions (one commit): `test_an_injected_raw_client_is_wrapped_in_the_denying_proxy`
failed before the fix (`assert False` — the raw client was stored unwrapped) and passes after
`build_shadow_seam` wraps every injected client; three coverage-only tests
(`__setattr__` denial, real-builder credential read, default-client seam path) prove
existing guarantees that had no test.

One mid-GREEN correction to a test, not the code: the banner test originally ran
`--minutes 2` against the wall clock and hung the suite for its full time box;
changed to `--minutes 0` (one rotation, immediate deadline). A second test-only fix:
the default-db assertion compares `Path` objects, not strings (Windows separators).

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | A safety violation survives a blanket `except Exception` (BaseException root) | `test_shadow_guard.py::test_violation_is_not_swallowed_by_a_blanket_except_exception` | unit | PASS |
| 2 | Vetted reads pass through to the inner client | `...::test_an_allowlisted_read_reaches_the_client` | unit | PASS |
| 3 | `post_order` raises at the proxy; inner client untouched | `...::test_post_order_raises_and_never_reaches_the_client` | unit | PASS |
| 4 | Market-order submission raises at the proxy | `...::test_market_order_submission_raises_...` | unit | PASS |
| 5 | Unknown methods are denied by default (deny-list avoided) | `...::test_an_unknown_method_is_denied_rather_than_passed_through` | unit | PASS |
| 6 | Building the shadow client reads no credential env var (`POLY_PRIVATE_KEY`, `POLY_KEY`, L2 creds) | `...::test_shadow_client_never_reads_a_credential` | unit | PASS |
| 7 | The real builder (`_build_unauthenticated_client`, the path every injected test skips) builds with no `key`/`signature_type`/`funder` kwarg and reads no credential env var | `...::test_the_real_builder_reads_no_credential` | unit | PASS |
| 8 | Setting an attribute on the proxy is denied; the inner client is never mutated | `...::test_setting_an_attribute_on_the_venue_is_denied` | unit | PASS |
| 9 | `shadow_client` always wraps in the denying proxy | `...::test_shadow_client_returns_a_denying_proxy` | unit | PASS |
| 10 | 11 spellings of `data/orders.db` (relative, backslash, URI, percent-encoded, localhost authority) are refused | `...::test_production_registry_is_refused_as_a_shadow_store` | unit | PASS |
| 11 | Non-production paths (default store, tmp, same-dir different name) are accepted | `...::test_the_default_shadow_store_is_accepted` + 2 | unit | PASS |
| 12 | Loop stops within one interval of the deadline and returns results | `test_shadow_run.py::TestDeadline::test_the_loop_stops_once_the_deadline_passes` | integration | PASS |
| 13 | Deadline exits through `run`'s own KeyboardInterrupt clean exit, returning results | `TestDeadline::test_the_deadline_returns_results_rather_than_escaping_as_an_error` | integration | PASS |
| 14 | Sleep is clamped so the box cannot be overshot | `TestDeadline::test_a_sleep_before_the_deadline_is_clamped_...` | unit | PASS |
| 15 | `run_shadow` refuses the production registry before constructing anything | `TestRunShadow::test_the_production_registry_is_refused_as_the_store` | integration | PASS |
| 16 | Decided intents are recorded (with condition id) and never submitted | `TestRunShadow::test_decided_intents_are_recorded_and_never_submitted` | integration | PASS |
| 17 | Default client path yields the denying proxy | `TestRunShadow::test_the_client_is_the_denying_proxy_by_default` | unit | PASS |
| 18 | An injected raw client is wrapped in the denying proxy; its `post_order` raises at the seam | `TestRunShadow::test_an_injected_raw_client_is_wrapped_in_the_denying_proxy` | integration | PASS |
| 19 | Live caps reach the decision unchanged | `TestRunShadow::test_live_caps_are_used_unchanged` | integration | PASS |
| 20 | Reconcile/sweep reported as skipped stages, not silent passes | `TestRunShadow::test_reconcile_and_sweep_are_skipped_not_silently_passed` | unit | PASS |
| 21 | Arg defaults: minutes 5.0, interval 5.0, db `data/shadow.db` | `TestMain::test_argument_defaults_match_the_plan` | unit | PASS |
| 22 | `--db data/orders.db` refused from argv | `TestMain::test_main_refuses_the_production_registry_via__db` | integration | PASS |
| 23 | Banner names mode, store, time box, "no signer" | `TestMain::test_main_logs_a_banner_naming_mode_store_timebox_and_no_signer` | unit | PASS |
| 24 | Exit code 0 when the box expires with rotation results | `TestMain::test_main_returns_0_when_the_time_box_expires_with_results` | integration | PASS |

## Full-suite validation

```
python -m pytest -q
→ 2 failed, 644 passed, 1 skipped in 41.21s
```

The 2 failures (`tests/test_market_feed.py::test_load_graduated_markets_real_file`,
`::test_get_market_by_cid`) are pre-existing and environmental: they read the
generated feed `run/markets.json`, which on this machine is past the 24h
staleness gate. CI skips them, since a fresh checkout has no feed to read. They touch no shadow module and fail identically without
this change. Re-running the ranker refreshes the file.

## Coverage and known gaps

Coverage tooling (`pytest-cov`/`coverage`) is not installed in this environment;
no dependency was added for this milestone. Every public function of
`core_brain/shadow_guard.py` and `core_brain/shadow_run.py` is exercised above,
including the previously-untested `_build_unauthenticated_client` and
`ReadOnlyVenue.__setattr__`.
Known gaps, deliberate:

- No test runs `main()` against the real graduated list or the real venue — that
  is the operator's network-reaching check below.
- Milestone 1 records intents but performs no fill inference (see plan risk table,
  `live_fill_engine.py` invariant).

## Checkpoints

- `707654a` — test: RED, shadow entrypoint contract (9 failing)
- `06f8215` — fix: GREEN, run_shadow/build_shadow_seam/main (34/34 target files green)
- review round 1 — wrap injected clients (RED→GREEN), `__setattr__`,
  real-builder credential test, default-client seam test, report counts
