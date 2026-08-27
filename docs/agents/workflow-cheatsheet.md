# Agent Responsibilities & Default Workflow

This document defines my core responsibilities, default job, and operating principles in this repository. I will proactively use these workflows to guide implementation, maintain order, and keep the project tip-top.

## Issue & Task Management
* `/issue-create` - Takes a raw, unstructured idea and turns it into a ready-to-work GitHub issue to capture thoughts immediately and revisit later.
* `/work-issue` - The end-to-end flow for picking up an open issue, brainstorming/planning it, routing it to execution orchestration, and shipping the final PR.

## Core Operating Principles
* **Plan Before Execute** - Mandates locking in architecture and plans before writing complex code.
* **Live By Default** - Treats all venue-reaching `spread-hunter` commands as live; explicitly exempts the no-signer/no-spend `shadow_run` rehearsal and read-only `order_manager status` commands from this restriction.
* **Test-Driven Development** - Requires 80%+ test coverage, passing tests, and explicit verification steps before marking tasks complete.

## Development Lifecycle & Orchestration
* `/grill-me` - Interviews you one question at a time to extract exact requirements when an ask is vague.
* `/orch-add-feature` - Builds brand-new features end-to-end using strict TDD.
* `/orch-change-feature` - Alters existing, working features to new behaviors by updating tests first.
* `/orch-fix-defect` - Systematically reproduces bugs in tests before attempting to fix them.
* `verification-before-completion` - Proves code works locally before claiming a task is finished.
* `doubt-driven-development` - Performs an adversarial review of critical logic to catch blind spots and assumptions.

## Git and Pull Request Management
* `/ship` - Automatically bumps versions, commits, and creates the initial Pull Request.
* `/pr-babysitter` - Actively negotiates with CodeRabbit, triaging feedback and pushing auto-fixes up to three times.
* `/santa-loop` - Requires two independent AI agents to review and agree before sensitive code is allowed to ship.
* `/land-and-deploy` - Handles merging and runs canary checks to ensure production stability post-deploy.

## UI/UX and Frontend Design Workflows
* `/gan-design` - Runs a generator/evaluator loop specifically for frontend visual work, iterating on a design until it hits a high aesthetic score.
* `/multi-frontend` - Orchestrates a multi-agent workflow dedicated solely to components, layouts, animation, and UI polish.
* `plan-design-review` - Acts as a "designer's eye" review for plans, scoring the UI/UX architecture out of 10 and forcing fixes before code is written.
* `frontend-ui-engineering` - The core skill that enforces accessibility (WCAG), responsive layouts, and production-quality HTML/CSS.
* `browse` / `browser-testing-with-devtools` - Uses headless browsers and real Chrome DevTools to navigate the UI, check visual states, and debug layouts directly in the DOM.

## Housekeeping & Cleanliness
* `/repo-sweep` - Performs a repository-wide cleanliness audit to remove junk, enforce naming conventions, and verify folder structure harmony.

## Knowledge Management
* `/learn` - Extracts reusable patterns and solutions from our live chat session and saves them as permanent skills for future use.
