<#
.SYNOPSIS
    Autonomous self-improving loop for spread-hunter-live (Core Bot + Architecture).
    Integrates loop-operator, loop-design-check, improve-codebase-architecture,
    verification-loop, and agent-self-evaluation.
#>

param(
    [int]$MaxIterations = 5,
    [string]$ProjectDir = (Get-Location).Path
)

$ErrorActionPreference = "Continue"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  AUTONOMOUS SELF-IMPROVING LOOP (spread-hunter-live)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Project Directory: $ProjectDir"
Write-Host "Max Iterations:    $MaxIterations"
Write-Host ""

$consecutiveStalls = 0

for ($i = 1; $i -le $MaxIterations; $i++) {
    Write-Host "`n>>> [Iteration $i of $MaxIterations] Starting..." -ForegroundColor Yellow

    # Ensure clean base state
    git checkout main 2>$null
    git pull --rebase 2>$null

    # -------------------------------------------------------------
    # Phase 1: DISCOVER (Core Bot Friction + Architecture)
    # -------------------------------------------------------------
    Write-Host "  [Phase 1] Discovering improvement candidate..." -ForegroundColor Cyan
    $discoverPrompt = @"
Read SHARED_TASK_NOTES.md.
Walk the entire codebase (core_brain, scoring, dashboard, database layer, scanner, tests).
Use the 'improve-codebase-architecture' principles (identify shallow modules, seam extractions, latency bottlenecks, test gaps, and state machine resilience).
Pick ONE high-leverage improvement anywhere in the project.
Write a bounded candidate description to .improvement_candidate.md.
Rules:
- Scope: <= 3 files, <= 120 lines changed.
- Do NOT delete or weaken tests.
- Maintain existing safety caps.
"@
    $discoverPrompt | claude -p --model opus --permission-mode bypassPermissions

    if (-not (Test-Path ".improvement_candidate.md")) {
        Write-Host "  [!] No candidate generated. Skipping iteration." -ForegroundColor Red
        $consecutiveStalls++
        if ($consecutiveStalls -ge 2) {
            Write-Host "  [STALL ESCALATION] 2 consecutive iterations without candidate. Pausing loop." -ForegroundColor Red
            break
        }
        continue
    }

    # -------------------------------------------------------------
    # Phase 2 & 3: PLAN & IMPLEMENT (TDD on isolated branch)
    # -------------------------------------------------------------
    $slug = "iter-$i"
    $branchName = "improve/loop-$slug"
    Write-Host "  [Phase 2 & 3] Creating branch $branchName and implementing via TDD..." -ForegroundColor Cyan
    git checkout -b $branchName

    $implementPrompt = @"
Read .improvement_candidate.md.
Follow TDD:
1. Write a failing test first demonstrating the issue or requirement.
2. Implement the minimal fix/improvement in the target files.
3. Keep changes tight (<= 3 files, <= 120 lines).
Do NOT delete or weaken any existing tests.
"@
    $implementPrompt | claude -p --model opus --permission-mode bypassPermissions

    # -------------------------------------------------------------
    # Phase 4: VERIFY (Deterministic pytest + ruff + shadow_run)
    # -------------------------------------------------------------
    Write-Host "  [Phase 4] Running verification suite..." -ForegroundColor Cyan
    $testResult = python -m pytest -q
    $testExit = $LASTEXITCODE
    
    $shadowResult = python -m core_brain.shadow_run --minutes 1
    $shadowExit = $LASTEXITCODE

    if ($testExit -ne 0 -or $shadowExit -ne 0) {
        Write-Host "  [!] Tests or Shadow Run failed. Attempting 1 automatic fix pass..." -ForegroundColor Yellow
        $fixPrompt = @"
pytest or shadow_run failed.
Test output:
$testResult

Shadow output:
$shadowResult

Diagnose and fix the issue. Run pytest -q to verify. If unfixable, output 'GIVE_UP'.
"@
        $fixOutput = $fixPrompt | claude -p --model opus --permission-mode bypassPermissions
        
        # Re-verify
        $testResult = python -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [!] Verification failed after fix attempt. Reverting branch." -ForegroundColor Red
            git checkout main
            git branch -D $branchName
            $consecutiveStalls++
            continue
        }
    }

    # -------------------------------------------------------------
    # Phase 5: INDEPENDENT JUDGE (agent-self-evaluation in fresh context)
    # -------------------------------------------------------------
    Write-Host "  [Phase 5] Running independent judge evaluation..." -ForegroundColor Cyan
    $judgePrompt = @"
You are an independent code quality & safety judge.
Evaluate the current git diff on branch '$branchName'.
Check 5 axes: Accuracy, Completeness, Clarity, Actionability, Conciseness.
Check Boundaries:
- Did it delete or weaken tests? (FAIL if yes)
- Did it exceed 3 files or 150 lines? (FAIL if yes)
- Does it violate safety rules in docs/agents/safety.md? (FAIL if yes)

Output overall score (1.0 to 5.0). If score < 3.5 or any boundary violated, output 'JUDGE_FAIL: <reason>'. Otherwise output 'JUDGE_PASS'.
"@
    $judgeOutput = $judgePrompt | claude -p --model opus --permission-mode bypassPermissions

    if ($judgeOutput -match "JUDGE_FAIL") {
        Write-Host "  [!] Judge rejected the change ($judgeOutput). Reverting." -ForegroundColor Yellow
        git checkout main
        git branch -D $branchName
        continue
    }

    # -------------------------------------------------------------
    # Phase 6: GITHUB PROPER WORKFLOW (Commits, Tags, PR)
    # -------------------------------------------------------------
    Write-Host "  [Phase 6] Creating structured commit, tag, and PR per repo standards..." -ForegroundColor Green
    
    # Generate conventional commit message
    $commitMsgPrompt = @"
Generate a single conventional commit message for the staged changes.
Format: <type>(<scope>): <imperative summary>
Examples:
refactor(core_brain): deepen market cache seam
perf(scanner): reduce orderbook parsing latency
Output ONLY the commit message line.
"@
    $commitMsg = ($commitMsgPrompt | claude -p --model opus --permission-mode bypassPermissions).Trim()
    if ([string]::IsNullOrWhiteSpace($commitMsg) -or $commitMsg -match "`n") {
        $commitMsg = "refactor(loop): autonomous improvement iteration $i"
    }

    git add -A
    git commit -m "$commitMsg"

    # Create loop checkpoint tag
    $tagName = "loop-iter-$i-$(Get-Date -Format 'yyyyMMdd-HHmm')"
    git tag -a $tagName -m "Autonomous loop iteration $i checkpoint"

    # Generate PR Body according to docs/agents/git-workflow.md
    $prBodyPath = [System.IO.Path]::GetTempFileName() + ".md"
    $prBodyContent = @"
@coderabbitai summary

## Loop Checkpoint
- **Loop Iteration**: #$i
- **Git Tag**: `$tagName`
- **Branch**: `$branchName`

## Why
Automated codebase & core bot optimization from autonomous loop iteration #$i.

## Test output
````
$testResult
````

## How to verify
1. Run ``python -m pytest -q`` (must pass 100%).
2. Run ``python -m core_brain.shadow_run --minutes 1`` (rehearsal pass).
"@
    Set-Content -Path $prBodyPath -Value $prBodyContent -Encoding UTF8

    git push -u origin $branchName --tags
    gh pr create --title "@coderabbitai" --body-file $prBodyPath

    Remove-Item $prBodyPath -ErrorAction SilentlyContinue

    # -------------------------------------------------------------
    # Phase 7: BRIDGE & STATE UPDATE
    # -------------------------------------------------------------
    Write-Host "  [Phase 7] Updating SHARED_TASK_NOTES.md..." -ForegroundColor Cyan
    $bridgePrompt = @"
Update SHARED_TASK_NOTES.md:
- Record iteration $i completed on branch $branchName (Tag: $tagName).
- Summarize what was improved.
- Update the backlog with any newly discovered follow-ups.
"@
    $bridgePrompt | claude -p --model opus --permission-mode bypassPermissions

    $consecutiveStalls = 0
    Remove-Item ".improvement_candidate.md" -ErrorAction SilentlyContinue
    Write-Host "  [✓] Iteration $i complete." -ForegroundColor Green
}

Write-Host "`n=== Autonomous Loop Finished ===" -ForegroundColor Cyan
