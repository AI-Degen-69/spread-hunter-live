---
description: Perform a repository-wide cleanliness audit to remove junk, enforce naming conventions, and verify folder structure harmony.
---

# Repo Sweep

Maintain repository harmony by performing a structured audit of files, folders, and naming conventions. Follow these steps strictly.

## Step 1: Junk & Unusable File Sweep

1. Find and evaluate all temporary, empty, or log files outside of their designated domains (e.g., `data/`). 
2. **Audit AI-Generated Folders:** Inspect folders like `.hermes`, `.superpowers`, `.claude`, `.codex`, `.ecc`, `.freebuff`, and `.agent` for stale plans, output logs, or old memory files. Delete these folders or their transient contents if they are no longer actively used, ensuring only the canonical `.agents/` and `docs/agents/` are kept.
3. Delete orphaned files, empty scratch files, `.DS_Store`, or auto-generated temp files that are not part of the active development.
4. Remove unreferenced prototype scripts that have been migrated into the main architecture.

## Step 2: Naming Convention Enforcement

1. Verify that all Python files use `snake_case.py` per `docs/agents/python-conventions.md`.
2. Verify that all agent documentation files (e.g., in `.agents/`, `docs/agents/`) use either `kebab-case.md` or `snake_case.md` consistently as defined in the glossary and `AGENTS.md`.
3. If a file violates these conventions, systematically rename it and update all corresponding imports or references.

## Step 3: Folder Structure Audit

1. Ensure files belong in their logical domain folders:
   - Dashboard UI elements must be inside `dashboard/`
   - Python operational scripts must be in `scripts/`
   - Core venue execution logic must be in `core_brain/`
   - Custom project-specific Agent skills and workflows belong in `.agents/` or `docs/agents/`
2. If files are scattered in the root directory (e.g., test scripts or random markdown files), move them to their appropriate directories and fix any broken paths.

## Step 4: Consistency & Harmony

1. Identify any duplicate utility functions or duplicate documentation files.
2. Consolidate them into a single canonical source of truth.
3. Run the formatter quality gate (`pytest` or equivalent `black`/`ruff` if configured) to ensure the changes did not break the build.

## Rules

- **Do not delete core components without testing** — Ensure deleted files were truly unusable.
- **One domain at a time** — Clean up one folder before moving to the next to keep the context clear.
- **Report Summary** — At the end of the sweep, print a summary of all files moved, deleted, or renamed.

## Core Principles & Lessons Learned

- **Context over Assumptions:** Do not blindly delete files just because they look out of place (e.g. `data/*.json`). Verify their timestamps and check if there is an active canonical version elsewhere (e.g. in `runtime/`). Read the context (like `AGENTS.md`) before taking destructive actions.
- **Exhaustive Sweeps:** AI-generated state doesn't just live in one folder. Always sweep for `.claude`, `.codex`, `.ecc`, `.freebuff`, `.hermes`, `.superpowers`, and `.agent` directories when cleaning up stale plans and logs.
- **Respect the House Rules:** Never discard explicit project instructions (like `AGENTS.md`) in favor of generic default behaviors. Internalize the specific domain rules before sweeping.
