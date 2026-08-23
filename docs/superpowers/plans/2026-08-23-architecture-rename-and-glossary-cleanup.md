# [Architecture & Glossary Cleanup: Removing "Live" and "Leg" Terms] Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up the codebase architecture, file naming, and terminology by replacing all legacy "live" prefixes with purpose-driven verbs/nouns (e.g. `order_manager.py`, `dashboard/server.py`, `runtime/orders.db`, `trader_loop.py`), and eliminating all "naked/leg" terminology in favor of "single buy" concepts (`exit_single_buy`, `single_buy_saver`).

**Architecture:** Systematic, phased refactor keeping backward-compatible forwarder shims during migration so existing background processes and CLI shortcuts never break. All tests and imports updated in lockstep, verified hermetically with pytest.

**Tech Stack:** Python 3.12, FastAPI / Uvicorn, SQLite3 (WAL mode), pytest, PowerShell 7.

## Global Constraints

- Never break active running daemons: provide transitional forwarding shims for moved modules.
- Preserve a 100% pass rate across the whole suite (`python -m pytest -q`).
- Zero references to "naked" or "leg" in new APIs or active trading engine code.
- Zero references to "live" in module file names (`live_exec.py`, `live_dash.py`, `live.db`).

---

## Proposed Name Mappings

| Current File / Symbol | Proposed Name | Purpose / Description |
|---|---|---|
| `engine/live_exec.py` | `engine/order_manager.py` | Order Manager daemon (`poll`) and execution CLI (`exit`, `complete`, `merge`, `redeem`, `status`, `balance`, `cancel`). |
| `engine/main_spread_hunter_loop.py` | `engine/trader_loop.py` | Trader loop daemon: quotes decision $\rightarrow$ maker order placement every 5s across filtered markets. |
| `engine/single_side_buy_saver.py` | `engine/single_buy_saver.py` | Rescue engine for single-sided fills: complete pair under $0.995 or stop-loss market sell. |
| `exit_naked_leg()` / `naked_exit` | `exit_single_buy()` / `single_buy_exit` | Stop-loss market sell of unhedged single-sided fill inventory. |
| `dash/` (folder) | `dashboard/` | Operations dashboard package. |
| `dash/live_dash.py` | `dashboard/server.py` | FastAPI dashboard backend & service supervisor. |
| `run/` (folder) | `runtime/` (or `data/`) | Runtime state directory for databases, logs, PIDs, and feeds. |
| `run/live.db` | `runtime/orders.db` | Primary SQLite database tracking orders, fills, pairs, closes, snapshots. |

---

## Tasks Breakdown

### Task 1: Single Buy Terminology Migration (`exit_naked_leg` $\rightarrow$ `exit_single_buy`)

**Files:**
- Create: `engine/single_buy_saver.py` (aliasing/shim from `single_side_buy_saver.py`)
- Modify: `engine/single_side_buy_saver.py`, `engine/live_exec.py`, `engine/order_registry.py`, `scripts/global_stop_loss.py`
- Test: `tests/test_single_side_buy_saver.py`, `tests/test_rc_fixes.py`

**Interfaces:**
- `exit_single_buy(client, registry, pair_id, max_pair_cost, ...)` replaces `exit_naked_leg` (with `exit_naked_leg` aliased for backward compatibility).
- Close method string: `"single_buy_exit"` (with `"naked_exit"` accepted as alias).

- [ ] **Step 1: Update `single_side_buy_saver.py` to define `exit_single_buy` and alias `exit_naked_leg`**
- [ ] **Step 2: Update callers in `engine/live_exec.py` and `scripts/global_stop_loss.py`**
- [ ] **Step 3: Update unit tests in `tests/test_single_side_buy_saver.py` and `tests/test_rc_fixes.py`**
- [ ] **Step 4: Run `pytest tests/test_single_side_buy_saver.py tests/test_rc_fixes.py -q`**
- [ ] **Step 5: Commit `refactor(engine): rename exit_naked_leg to exit_single_buy`**

---

### Task 2: Order Manager Renaming (`engine/live_exec.py` $\rightarrow$ `engine/order_manager.py`)

**Files:**
- Create: `engine/order_manager.py`
- Modify: `engine/live_exec.py` (shim forwarding to `engine.order_manager`), `scripts/spread-hunter-menu.ps1`, `dash/live_dash.py`
- Test: `tests/test_live_exec.py`, `tests/test_live_exec_decide.py`, `tests/test_live_exec_merge.py`

**Interfaces:**
- `python -m core_brain.order_manager poll --interval 0.5`
- `python -m core_brain.order_manager status`
- `python -m core_brain.order_manager exit <pair_id>`
- `python -m core_brain.order_manager merge <condition_id>`

- [ ] **Step 1: Move implementation to `engine/order_manager.py` and create forwarding shim in `engine/live_exec.py`**
- [ ] **Step 2: Update dashboard and menu references to invoke `python -m core_brain.order_manager`**
- [ ] **Step 3: Run full `pytest tests/test_live_exec*.py -q`**
- [ ] **Step 4: Commit `feat(engine): rename live_exec.py to order_manager.py`**

---

### Task 3: Trader Loop Renaming (`main_spread_hunter_loop.py` $\rightarrow$ `trader_loop.py`)

**Files:**
- Create: `engine/trader_loop.py`
- Modify: `engine/main_spread_hunter_loop.py` (shim forwarding to `engine.trader_loop`), `dash/live_dash.py`, `scripts/spread-hunter-menu.ps1`
- Test: `tests/test_main_spread_hunter_loop.py`, `tests/test_main_spread_hunter_loop_state.py`

**Interfaces:**
- `python -m core_brain.trader_loop loop`
- `python -m core_brain.trader_loop decide`

- [ ] **Step 1: Create `engine/trader_loop.py` and shim `engine/main_spread_hunter_loop.py`**
- [ ] **Step 2: Update dashboard supervisor commands and menu stack map**
- [ ] **Step 3: Run `pytest tests/test_main_spread_hunter_loop*.py -q`**
- [ ] **Step 4: Commit `feat(engine): rename main_spread_hunter_loop.py to trader_loop.py`**

---

### Task 4: Dashboard Renaming (`dash/live_dash.py` $\rightarrow$ `dashboard/server.py`)

**Files:**
- Create: `dashboard/` folder, `dashboard/server.py`, `dashboard/static/`
- Modify: `dash/live_dash.py` (shim forwarding to `dashboard.server`), `scripts/spread-hunter-menu.ps1`, `tests/test_live_dash.py`
- Test: `tests/test_live_dash.py`

**Interfaces:**
- `python -m dashboard.server --port 8799`

- [ ] **Step 1: Copy/move `dash/` to `dashboard/` with `server.py` as entry point**
- [ ] **Step 2: Create backward compatibility shim in `dash/live_dash.py`**
- [ ] **Step 3: Update `scripts/spread-hunter-menu.ps1` and test suite**
- [ ] **Step 4: Run `pytest tests/test_live_dash.py -q`**
- [ ] **Step 5: Commit `feat(dashboard): rename dash/live_dash.py to dashboard/server.py`**

---

### Task 5: Runtime State and Database Renaming (`run/live.db` $\rightarrow$ `runtime/orders.db`)

**Files:**
- Modify: `engine/order_registry.py` (`DEFAULT_DB_PATH`), `dashboard/server.py`, `scripts/spread-hunter-menu.ps1`, `tests/conftest.py`
- Test: Full test suite (`pytest -q`)

**Interfaces:**
- `DEFAULT_DB_PATH = ROOT / "runtime" / "orders.db"` (with automatic fallback to `run/live.db` if existing)

- [ ] **Step 1: Update `DEFAULT_DB_PATH` in `engine/order_registry.py` with seamless fallback for existing databases**
- [ ] **Step 2: Update test fixtures in `tests/conftest.py` and test files**
- [ ] **Step 3: Run the full test suite (`python -m pytest -q`), all green**
- [ ] **Step 4: Commit `feat(data): transition default db path to runtime/orders.db`**

---

## Verification Plan

### Automated Tests
- Run the complete test suite: `python -m pytest -q` (every test passes).
- Test CLI verbs: `python -m core_brain.order_manager status`.
- Test dashboard server: `python -m dashboard.server --port 8799`.
- Test interactive menu: `pwsh -File .\scripts\spread-hunter-menu.ps1 status`.

### Manual Verification
- Verify PowerShell `shm status` displays clean paths and running services.
- Verify web dashboard loads at `http://127.0.0.1:8799` with all service cards operational.
