# How to verify — completable-cost gate and re-quote dead band

## 1. The tests

    python -m pytest -q

Expect green except the two pre-existing `tests/test_market_feed.py` failures
(`test_load_graduated_markets_real_file`, `test_get_market_by_cid`) — both fail on
`main` before this branch and are unrelated. The new behaviour is covered by:

    python -m pytest tests/test_completable_pair_gate.py tests/test_trader_loop.py tests/test_shadow_run.py -q

## 2. Confirm each test fails without the change

    git stash
    python -m pytest tests/test_completable_pair_gate.py -q   # expect collection error / failures
    git stash pop

(On this branch the commits are already split per task; checking out `main` and running
the new test files is the cleaner RED check — every class fails with `AttributeError` /
`TypeError` before its implementing commit.)

## 3. Read the rules off the config

    python -c "from core_brain.config import load; c=load(); print(c.max_completable_pair_cost, c.enforce_completable_pair_cost, c.requote_dead_band)"

Expect: `1.0 True 0.03`

## 4. Rehearse (OPERATOR ONLY — spends nothing, but it is yours to start)

    python -m core_brain.shadow_run --minutes 30

Then compare against run-2809a7161de1:

| what to look at | before | what the change should do |
| --- | --- | --- |
| median order lifetime | 11.7 s | roughly double, ~20-25 s |
| consecutive re-quotes that changed price | 205 of 205 | ~half as many re-quotes total |
| distinct `max_pair_cost_at_post` values | 1 (0.995) | unchanged; the new gate declines rather than stamps |
| orders posted | 209 | fewer — wide markets are now declined |
| skip reasons mentioning "completable pair" | 0 | non-zero |

Fewer orders is the expected result, not a regression. The decision rule from the
spec: if the gate declines nearly everything, the problem is price levels and no
execution state machine will fix it.
