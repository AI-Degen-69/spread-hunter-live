# SPREAD HUNTER - CONTROL CENTER
# Standalone menu for the spread hunter execution engine
# (C:\Users\Tiger\Agents\Projects\spread-hunter-live).
#
# Usage:
#   .\scripts\spread-hunter-menu.ps1          # interactive menu
#   .\scripts\spread-hunter-menu.ps1 start    # dashboard + bot stack (detached)
#   .\scripts\spread-hunter-menu.ps1 stop     # bot stack, then dashboard
#   .\scripts\spread-hunter-menu.ps1 status   # dashboard + every assisting process
#   .\scripts\spread-hunter-menu.ps1 open     # start LIVE dashboard in background (no bot) & open in browser
#   .\scripts\spread-hunter-menu.ps1 shadow   # start SHADOW dashboard (--db data/shadow.db --port 8799) & open
#   .\scripts\spread-hunter-menu.ps1 stop-shadow # stop shadow dashboard (close :8799)
#   .\scripts\spread-hunter-menu.ps1 reset        # stop all, wipe runtime state, verify clean (no start)
#   .\scripts\spread-hunter-menu.ps1 reset-shadow # ...then start SHADOW dashboard (add -Minutes N for a rehearsal run)
#   .\scripts\spread-hunter-menu.ps1 reset-live -Yes # ...then start the LIVE stack (rests REAL maker bids)
#
# The bot stack (Market Filter / Query Polymarket / Decide & Execute) is started
# and stopped through the dashboard's own /api/system/start|stop endpoints --
# the same code path as the dashboard's START/STOP buttons (interprocess lock,
# starting-capital snapshot, shared run_id). The dashboard process itself is
# owned by this script via runtime/live-dash.pids.json.
#
# NOTE: "start" launches Decide & Execute, which rests REAL maker bids. That is
# an opening command and requires explicit supervision (AGENTS.md).

#Requires -Version 7.0
# PowerShell 7 (pwsh) is required. This file is UTF-8 without a BOM and
# contains box-drawing characters that Windows PowerShell 5.1 misdecodes.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = "",
    [switch]$Yes,
    [int]$Minutes = 0
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LivePort    = 8799
$DashUrl     = "http://127.0.0.1:$LivePort"
$RunDir      = Join-Path $ProjectPath "runtime"
$LegacyRunDir = Join-Path $ProjectPath "run"

# Runtime state moved run/ -> runtime/ and some files were renamed with it. A
# stack started before that move still records itself under the old names, and
# a menu that cannot see it prints STOPPED while real bids are resting. Read
# through this: current path when it exists, pre-rename path while only that
# one does, current path otherwise. Mirrors core_brain/runtime_paths.py.
function Resolve-RuntimeFile {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$LegacyName
    )
    $current = Join-Path $RunDir $Name
    if (Test-Path $current) { return $current }
    if (-not $LegacyName) { $LegacyName = $Name }
    $legacy = Join-Path $LegacyRunDir $LegacyName
    if (Test-Path $legacy) { return $legacy }
    return $current
}

# The dashboard PID file is menu-owned and rewritten on every start, so it is
# not resolved: a stale pre-rename copy only ever meant "not owned by menu".
$DashPidFile = Join-Path $RunDir "live-dash.pids.json"
$ShadowPidFile = Join-Path $RunDir "shadow-dash.pids.json"
$ShadowDbPath  = Join-Path $ProjectPath "data/shadow.db"
$ShadowPort    = 8799
$ShadowDashUrl = "http://127.0.0.1:$ShadowPort"
$ShadowOutLog  = Join-Path $RunDir "shadow_dash.out.log"
$ShadowErrLog  = Join-Path $RunDir "shadow_dash.err.log"
$ProcsFile   = Resolve-RuntimeFile -Name "processes.json" -LegacyName "live_procs.json"
$OutLog      = Join-Path $RunDir "live_dash.out.log"
$ErrLog      = Join-Path $RunDir "live_dash.err.log"
$HbFile      = Resolve-RuntimeFile -Name "global_stop_loss_heartbeat.json" -LegacyName "guardrail_watch_heartbeat.json"

# `started_at` in the process file is a NUMERIC Unix timestamp -- start_bot()
# writes `time.time()`. [datetime]::Parse() throws on that, and the stop path
# below treats a throw as "skip the kill" and then deletes the process file, so
# a live stack would be orphaned with no registry left to find it by.
function ConvertTo-RecordedStart {
    <# The recorded process start as a DateTime, or $null when unusable. #>
    param($StartedAt)
    if ($null -eq $StartedAt -or "$StartedAt" -eq "") { return $null }
    $numeric = 0.0
    if ([double]::TryParse("$StartedAt", [ref]$numeric)) {
        return [DateTimeOffset]::FromUnixTimeMilliseconds([int64]($numeric * 1000)).LocalDateTime
    }
    try { return [datetime]::Parse("$StartedAt") } catch { return $null }
}

# Process keys were renamed with the file. Read both names.
$LegacyServiceKeys = @{ filter = "screener"; query = "engine"; decide = "fleet" }

function Get-ServiceEntry {
    <# The recorded process entry for a service, accepting its pre-rename key. #>
    param(
        [Parameter(Mandatory)]$Saved,
        [Parameter(Mandatory)][string]$Key
    )
    if (-not $Saved) { return $null }
    $entry = $Saved.$Key
    if ($entry) { return $entry }
    $legacyKey = $LegacyServiceKeys[$Key]
    if ($legacyKey) { return $Saved.$legacyKey }
    return $null
}

# Module map: each stack process -> its source file (relative to the repo root).
$StackPaths = @{
    filter     = "scripts/filter_loop.py"
    query      = "core_brain/order_manager.py"
    decide     = "core_brain/trader_loop.py"
    guardrail  = "scripts/global_stop_loss.py"
    dash       = "dashboard/server.py"
    shadowDash = "dashboard/server.py"
}

$StackCmds = @{
    filter     = "python -m scripts.filter_loop"
    query      = "python -m core_brain.order_manager poll --interval 0.5"
    decide     = "python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5"
    guardrail  = "python -m scripts.global_stop_loss"
    dash       = "python -m dashboard.server --port 8799"
    shadowDash = "python -m dashboard.server --db data/shadow.db --port 8799"
}

# ── Theme system (shared profile templates, self-contained fallback) ──
# Dot-source the profile theme system (PowerShell 7 Theme folder) when installed
# - the same Write-Profile* templates the sim checkout's scripts use. When it is
# absent, define local ASCII-safe equivalents so this script stays standalone.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$ThemeColorPath = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-ColorSystem.ps1"
$ThemeTplPath   = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-Templates.ps1"
if ((Test-Path $ThemeColorPath) -and (Test-Path $ThemeTplPath)) {
    try { . $ThemeColorPath; . $ThemeTplPath } catch {}
}
if (-not (Get-Command Write-ProfileSuccess -ErrorAction SilentlyContinue)) {
    function Get-ProfileColor {
        param([string]$Name)
        switch ($Name) {
            "Success"  { [ConsoleColor]::Green }
            "Error"    { [ConsoleColor]::Red }
            "Warning"  { [ConsoleColor]::Yellow }
            "Info"     { [ConsoleColor]::Cyan }
            "Neutral"  { [ConsoleColor]::DarkGray }
            "Strong"   { [ConsoleColor]::White }
            "Highlight"{ [ConsoleColor]::Yellow }
            "Border"   { [ConsoleColor]::DarkCyan }
            "Path"     { [ConsoleColor]::Green }
            "Link"     { [ConsoleColor]::Blue }
            "Value"    { [ConsoleColor]::DarkCyan }
            "Progress" { [ConsoleColor]::DarkGray }
            "Text"     { [ConsoleColor]::Gray }
            default    { [ConsoleColor]::Gray }
        }
    }
    function Write-ProfileBanner {
        param([string]$Title, [string]$Subtitle = "", [string]$Style = "Info")
        Write-Host ("=" * 80) -ForegroundColor (Get-ProfileColor -Name Border)
        Write-Host $Title -ForegroundColor (Get-ProfileColor -Name Info)
        if ($Subtitle) { Write-Host $Subtitle -ForegroundColor (Get-ProfileColor -Name Neutral) }
        Write-Host ("=" * 80) -ForegroundColor (Get-ProfileColor -Name Border)
        Write-Host ""
    }
    function Write-ProfileSuccess { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [OK] " + $t) -ForegroundColor (Get-ProfileColor -Name Success) }
    function Write-ProfileError   { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [FAIL] " + $t) -ForegroundColor (Get-ProfileColor -Name Error) }
    function Write-ProfileWarning { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [WARN] " + $t) -ForegroundColor (Get-ProfileColor -Name Warning) }
    function Write-ProfileInfo    { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [INFO] " + $t) -ForegroundColor (Get-ProfileColor -Name Info) }
    function Write-ProfileRuleWithText { param([string]$Text, [string]$Style = "Neutral") Write-Host ("--- $Text " + ("-" * [Math]::Max(5, (75 - $Text.Length)))) -ForegroundColor (Get-ProfileColor -Name Border) }
}

# ── Console helpers (thin wrappers over the theme templates) ──
function Lsh-Banner {
    param([string]$Title, [string]$Subtitle)
    try { Clear-Host } catch {}
    $cBorder = Get-ProfileColor -Name Border
    $cTitle  = [ConsoleColor]::Yellow
    $cSub    = Get-ProfileColor -Name Neutral
    $w = 78
    Write-Host ("╔" + ("═" * $w) + "╗") -ForegroundColor $cBorder
    # Title row — bright yellow, centered-ish left
    Write-Host "║ " -ForegroundColor $cBorder -NoNewline
    Write-Host $Title.PadRight($w - 2) -ForegroundColor $cTitle -NoNewline
    Write-Host " ║" -ForegroundColor $cBorder
    if ($Subtitle) {
        Write-Host "║ " -ForegroundColor $cBorder -NoNewline
        Write-Host $Subtitle.PadRight($w - 2) -ForegroundColor $cSub -NoNewline
        Write-Host " ║" -ForegroundColor $cBorder
    }
    Write-Host ("╚" + ("═" * $w) + "╝") -ForegroundColor $cBorder
    Write-Host ""
}
function Lsh-Step   { param([string]$Msg) Write-ProfileInfo -Message $Msg }
function Lsh-Ok     { param([string]$Msg) Write-ProfileSuccess -Message $Msg }
function Lsh-Warn   { param([string]$Msg) Write-ProfileWarning -Message $Msg }
function Lsh-Fail   { param([string]$Msg) Write-ProfileError -Message $Msg }

# ── Process / port primitives ──
function Test-PidAlive {
    param([int]$ProcessId)
    if (-not $ProcessId) { return $false }
    try { Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null; return $true } catch { return $false }
}

function Test-LivePort {
    <# True when :8799 is in LISTENING state. #>
    return [bool](netstat -ano | Select-String ":$LivePort\s+.*LISTENING")
}

function Get-PortPid {
    <# PID of the process LISTENING on :8799, or $null. #>
    $line = netstat -ano | Select-String ":$LivePort\s+.*LISTENING" | Select-Object -First 1
    if (-not $line) { return $null }
    return [int](($line.ToString() -split "\s+")[-1])
}

function Format-Uptime {
    param($Start)
    if (-not $Start) { return "" }
    $el = (Get-Date) - $Start
    if ($el.TotalHours -ge 1) { return ("{0}h {1}m" -f [int]$el.TotalHours, $el.Minutes) }
    if ($el.TotalMinutes -ge 1) { return ("{0}m {1}s" -f [int]$el.TotalMinutes, $el.Seconds) }
    return ("{0}s" -f [int]$el.TotalSeconds)
}

function Format-AgeSec {
    <# Convert raw seconds into a compact human-readable string:
       ≥ 1 day  → "2d 3h"    ≥ 1 hour → "4h 21m"
       ≥ 1 min  → "6m 12s"   < 1 min  → "42s"  #>
    param([int]$Seconds)
    if ($Seconds -ge 86400) {
        $d = [math]::Floor($Seconds / 86400)
        $h = [math]::Floor(($Seconds % 86400) / 3600)
        return "{0}d {1}h" -f $d, $h
    }
    if ($Seconds -ge 3600) {
        $h = [math]::Floor($Seconds / 3600)
        $m = [math]::Floor(($Seconds % 3600) / 60)
        return "{0}h {1}m" -f $h, $m
    }
    if ($Seconds -ge 60) {
        $m = [math]::Floor($Seconds / 60)
        $s = $Seconds % 60
        return "{0}m {1}s" -f $m, $s
    }
    return "{0}s" -f $Seconds
}

# ── Dashboard process ownership (runtime/live-dash.pids.json) ──
function Get-DashInstance {
    <# The recorded dashboard process that is STILL the one we started (start-ticks checked). #>
    if (-not (Test-Path $DashPidFile)) { return $null }
    try { $data = Get-Content $DashPidFile -Raw | ConvertFrom-Json } catch { return $null }
    $d = $data.dash
    if (-not $d -or -not $d.pid) { return $null }
    try { $p = Get-Process -Id $d.pid -ErrorAction Stop } catch { return $null }
    if ($null -ne $d.started_ticks) {
        $pStart = $null
        try { $pStart = $p.StartTime } catch {}
        if ($null -eq $pStart -or $pStart.ToUniversalTime().Ticks -ne [int64]$d.started_ticks) {
            return $null  # PID recycled; no longer our process
        }
    }
    return [pscustomobject]@{ pid = $p.Id; proc = $p; port = $LivePort }
}

function Save-DashInstance {
    param([Parameter(Mandatory)]$DashProcess)
    $record = $null
    if ($DashProcess -and -not $DashProcess.HasExited) {
        $record = [pscustomobject]@{
            pid           = $DashProcess.Id
            started_ticks = $DashProcess.StartTime.ToUniversalTime().Ticks
            started       = $DashProcess.StartTime.ToString("o")
            port          = $LivePort
        }
    }
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    [pscustomobject]@{
        strategy = "spread-hunter-live"
        saved    = (Get-Date).ToString("o")
        dash     = $record
    } | ConvertTo-Json -Depth 4 | Set-Content -Path $DashPidFile -Encoding UTF8
}

function Test-DashboardServer {
    <# True when whatever answers on :LivePort is a spread-hunter dashboard
    (answers /api/system/status with a services.dash entry). Guards adoption
    so an unrelated process squatting on the port is never adopted or killed.
    #>
    try {
        $r = Invoke-RestMethod -Uri "$DashUrl/api/system/status" -UseBasicParsing -TimeoutSec 4
        return ($null -ne $r.services -and $null -ne $r.services.dash)
    } catch {
        return $false
    }
}

function Adopt-DashboardInstance {
    <# Record the running dashboard on :LivePort as owned by this menu.

    The record stores the process's OWN creation time (not "now"), so the
    PID-recycling check in Get-DashInstance still protects stop/status: a
    recycled PID would carry a different StartTime and be ignored.
    #>
    $portPid = Get-PortPid
    if (-not $portPid) { return $false }
    try {
        $proc = Get-Process -Id $portPid -ErrorAction Stop
        Save-DashInstance -DashProcess $proc
    } catch {
        return $false
    }
    return ($null -ne (Get-DashInstance))
}

function Start-Dashboard {
    <# Launch the dashboard detached; true when :8799 is serving afterwards.

    If something is already on :8799 but not recorded as ours (an orphan left
    by a restart, or a dashboard started outside this menu), offer to ADOPT it
    instead of failing or double-starting. Adoption only happens after
    Test-DashboardServer confirms it really is a spread-hunter dashboard.
    #>
    $inst = Get-DashInstance
    if ($null -ne $inst) {
        Lsh-Ok "Dashboard already running (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
        return $true
    }
    if (Test-LivePort) {
        $portPid = Get-PortPid
        if (Test-DashboardServer) {
            $adopt = $false
            if ($Action -ne "") {
                # CLI (lsh start): adopt silently once verified.
                $adopt = $true
            } else {
                $resp = Read-Host "  A dashboard is already serving on :$LivePort (PID $portPid). Adopt it so stop/status own it? [y/N]"
                $adopt = ($resp -match '^[yY]')
            }
            if ($adopt -and (Adopt-DashboardInstance)) {
                $inst = Get-DashInstance
                Lsh-Ok "Adopted the running dashboard (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime)) - stop and status now own it."
            } elseif ($adopt) {
                Lsh-Fail "Could not adopt PID $portPid (may have exited mid-check)."
            } else {
                Lsh-Warn "Not adopting PID $portPid - stop will leave it running; stack control still works."
            }
            return $true
        }
        Lsh-Fail "Port $LivePort is occupied by PID $portPid, which does NOT answer as a spread-hunter dashboard - refusing to act on it. Free the port manually first."
        return $false
    }
    Lsh-Step "Launching dashboard (python -m dashboard.server --port $LivePort)..."
    $dash = Start-Process -FilePath "python" `
        -ArgumentList "-m", "dashboard.server", "--port", "$LivePort" `
        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError  $ErrLog
    Save-DashInstance -DashProcess $dash

    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Test-LivePort) { break }
        $dash.Refresh()
        if ($dash.HasExited) { break }
    }
    if (-not (Test-LivePort)) {
        Lsh-Fail "Dashboard failed to bind port $LivePort. See $ErrLog"
        Remove-Item $DashPidFile -ErrorAction SilentlyContinue
        return $false
    }
    Lsh-Ok "Dashboard serving on $DashUrl (PID $($dash.Id))."
    return $true
}

function Stop-Dashboard {
    <# Stop the dashboard we own; leaves foreign processes on :8799 alone. #>
    $inst = Get-DashInstance
    if ($null -ne $inst) {
        Lsh-Step "Stopping dashboard PID $($inst.pid)..."
        Stop-Process -Id $inst.pid -Force -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline -and (Test-LivePort)) { Start-Sleep -Milliseconds 300 }
    }
    Remove-Item $DashPidFile -ErrorAction SilentlyContinue
    if (Test-LivePort) {
        Lsh-Warn "Port $LivePort still LISTENING (PID $(Get-PortPid)) - not owned by this menu, left running."
        return $false
    }
    return $true
}

# ── Shadow dashboard (data/shadow.db, same port 8799) ──
function Get-ShadowDashInstance {
    <# The recorded SHADOW dashboard that is still our process (start-ticks checked). #>
    if (-not (Test-Path $ShadowPidFile)) { return $null }
    try { $data = Get-Content $ShadowPidFile -Raw | ConvertFrom-Json } catch { return $null }
    $d = $data.dash
    if (-not $d -or -not $d.pid) { return $null }
    try { $p = Get-Process -Id $d.pid -ErrorAction Stop } catch { return $null }
    if ($null -ne $d.started_ticks) {
        $pStart = $null
        try { $pStart = $p.StartTime } catch {}
        if ($null -eq $pStart -or $pStart.ToUniversalTime().Ticks -ne [int64]$d.started_ticks) {
            return $null
        }
    }
    return [pscustomobject]@{ pid = $p.Id; proc = $p; port = $ShadowPort; db = $d.db }
}

function Save-ShadowDashInstance {
    param([Parameter(Mandatory)]$DashProcess)
    $record = $null
    if ($DashProcess -and -not $DashProcess.HasExited) {
        $record = [pscustomobject]@{
            pid           = $DashProcess.Id
            started_ticks = $DashProcess.StartTime.ToUniversalTime().Ticks
            started       = $DashProcess.StartTime.ToString("o")
            port          = $ShadowPort
            db            = "data/shadow.db"
        }
    }
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    [pscustomobject]@{
        strategy = "spread-hunter-live"
        mode     = "shadow"
        saved    = (Get-Date).ToString("o")
        dash     = $record
    } | ConvertTo-Json -Depth 4 | Set-Content -Path $ShadowPidFile -Encoding UTF8
}

function Test-ShadowDashboardServer {
    <# True when whatever is on :ShadowPort answers as dashboard (any db mode). #>
    try {
        $r = Invoke-RestMethod -Uri "$ShadowDashUrl/api/system/status" -UseBasicParsing -TimeoutSec 4
        return ($null -ne $r.services -and $null -ne $r.services.dash)
    } catch { return $false }
}

function Adopt-ShadowDashboardInstance {
    $portPid = Get-PortPid
    if (-not $portPid) { return $false }
    try {
        $proc = Get-Process -Id $portPid -ErrorAction Stop
        Save-ShadowDashInstance -DashProcess $proc
    } catch { return $false }
    return ($null -ne (Get-ShadowDashInstance))
}

function Start-ShadowDashboard {
    <# Launch shadow dashboard detached: python -m dashboard.server --db data/shadow.db --port 8799 #>
    $inst = Get-ShadowDashInstance
    if ($null -ne $inst) {
        Lsh-Ok "Shadow dashboard already running (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
        return $true
    }
    # Live and shadow share :8799 by request — only one can bind at a time.
    if (Test-LivePort) {
        $portPid = Get-PortPid
        if (Test-ShadowDashboardServer) {
            # Something dashboard-like is already there — adopt it as shadow if live pidfile says otherwise.
            $liveInst = Get-DashInstance
            if ($null -eq $liveInst) {
                if ($Action -ne "") {
                    if (Adopt-ShadowDashboardInstance) {
                        $inst = Get-ShadowDashInstance
                        Lsh-Ok "Adopted running shadow dashboard on :$ShadowPort (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
                        return $true
                    }
                } else {
                    $resp = Read-Host "  A dashboard is already serving on :$ShadowPort (PID $portPid). Adopt it as shadow? [y/N]"
                    if ($resp -match '^[yY]' -and (Adopt-ShadowDashboardInstance)) {
                        $inst = Get-ShadowDashInstance
                        Lsh-Ok "Adopted running shadow dashboard on :$ShadowPort (PID $($inst.pid))."
                        return $true
                    } else {
                        Lsh-Warn "Not adopting PID $portPid."
                        return $true
                    }
                }
            }
        }
        Lsh-Fail "Port $ShadowPort is occupied by PID $portPid — live dashboard is on :$ShadowPort. Stop live first (menu 2 or 'stop') or free the port, then start shadow."
        return $false
    }
    if (Test-ShadowDashboardServer) {
        # Edge: port test missed but server answers — adopt
        if (Adopt-ShadowDashboardInstance) {
            $inst = Get-ShadowDashInstance
            Lsh-Ok "Adopted running shadow dashboard (PID $($inst.pid))."
            return $true
        }
    }
    Lsh-Step "Launching shadow dashboard (python -m dashboard.server --db data/shadow.db --port $ShadowPort)..."
    $dash = Start-Process -FilePath "python" `
        -ArgumentList "-m", "dashboard.server", "--db", "data/shadow.db", "--port", "$ShadowPort" `
        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $ShadowOutLog `
        -RedirectStandardError  $ShadowErrLog
    Save-ShadowDashInstance -DashProcess $dash
    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Test-LivePort) { break }
        $dash.Refresh()
        if ($dash.HasExited) { break }
    }
    if (-not (Test-LivePort)) {
        Lsh-Fail "Shadow dashboard failed to bind port $ShadowPort. See $ShadowErrLog"
        Remove-Item $ShadowPidFile -ErrorAction SilentlyContinue
        return $false
    }
    Lsh-Ok "Shadow dashboard serving on $ShadowDashUrl (PID $($dash.Id), db=data/shadow.db)."
    return $true
}

function Stop-ShadowDashboard {
    <# Stop shadow dashboard we own; leaves foreign processes on :8799 alone. #>
    $inst = Get-ShadowDashInstance
    if ($null -ne $inst) {
        Lsh-Step "Stopping shadow dashboard PID $($inst.pid)..."
        Stop-Process -Id $inst.pid -Force -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline -and (Test-LivePort)) { Start-Sleep -Milliseconds 300 }
    }
    Remove-Item $ShadowPidFile -ErrorAction SilentlyContinue
    if (Test-LivePort) {
        # If live still occupies the port, that's expected — shadow is down but port stays LISTENING.
        $liveInst = Get-DashInstance
        if ($null -ne $liveInst) {
            Lsh-Warn "Port $ShadowPort still LISTENING (PID $(Get-PortPid)) — live dashboard still owns it."
            return $true
        }
        Lsh-Warn "Port $ShadowPort still LISTENING (PID $(Get-PortPid)) — not owned by shadow menu, left running."
        return $false
    }
    return $true
}

# ── Bot stack control through the dashboard API ──
function Get-ControlToken {
    <# The dashboard bakes a per-process CSRF token into the HTML it serves. #>
    try {
        $html = (Invoke-WebRequest -Uri $DashUrl -UseBasicParsing -TimeoutSec 5).Content
        if ($html -match 'CONTROL_TOKEN\s*=\s*"([^"]+)"') { return $Matches[1] }
    } catch {}
    return $null
}

function Invoke-Control {
    param([string]$Endpoint)
    $token = Get-ControlToken
    if (-not $token) {
        Lsh-Fail "Could not read the control token from $DashUrl. Dashboard may be restarting."
        return $null
    }
    try {
        return Invoke-RestMethod -Uri "$DashUrl$Endpoint" -Method Post `
            -Headers @{ "x-control-token" = $token } -TimeoutSec 30
    } catch {
        Lsh-Fail "Control request $Endpoint failed: $($_.Exception.Message)"
        return $null
    }
}

# ── Aligned status rows ──
# Unified 4-column template across EVERY section:
#   col 1: Name / Label      (27 wide, Strong/white)
#   col 2: Status Badge      (11 wide, color-coded word: ON/OFF for processes, FOUND/MISSING/STALE for files)
#   col 3: File / Path       (35 wide, Path/green)
#   col 4: Dynamic info      (PID if running, Run command if stopped, or file metadata)
function Write-ProcessRow {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][bool]$Running,
        [object]$PidVal = $null,
        [Parameter(Mandatory)][string]$Path,
        [string]$RunCmd = "",
        [string]$ExtraInfo = ""
    )

    $statusWord = if ($Running) { "ON" } else { "OFF" }
    $statusColor = if ($Running) { Get-ProfileColor -Name Success } else { Get-ProfileColor -Name Error }

    $dynamic = if ($Running) {
        if ($ExtraInfo) { "PID $PidVal · $ExtraInfo" }
        elseif ($PidVal) { "PID $PidVal" }
        else { "running" }
    } else {
        if ($RunCmd) { "Run: $RunCmd" } else { "stopped" }
    }
    $dynamicColor = if ($Running) { Get-ProfileColor -Name Value } else { Get-ProfileColor -Name Command }

    Write-Host ("  {0,-27}" -f $Label) -ForegroundColor (Get-ProfileColor -Name Strong) -NoNewline
    Write-Host ("{0,-11}" -f $statusWord) -ForegroundColor $statusColor -NoNewline
    Write-Host ("{0,-35}" -f $Path) -ForegroundColor (Get-ProfileColor -Name Path) -NoNewline
    Write-Host $dynamic -ForegroundColor $dynamicColor
}

function Write-FileRow {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Status,
        [AllowEmptyString()][string]$Path = "",
        [string]$Dynamic = "",
        [string]$StatusStyle = "",
        [string]$DynamicStyle = ""
    )

    if (-not $StatusStyle) {
        $StatusStyle = switch -Regex ($Status.ToUpper()) {
            '^(FOUND|OK|FRESH|ON)$'            { 'Success' }
            '^(AGING|STALE|WARN|LISTENING)$'   { 'Warning' }
            default                            { 'Error' }
        }
    }
    if (-not $DynamicStyle) {
        $DynamicStyle = if ($Dynamic -like "Run: *" -or $Dynamic -like "*Run: *") { 'Command' }
                        elseif ($Status -in "MISSING", "ERROR") { 'Error' }
                        elseif ($Status -in "STALE", "AGING") { 'Warning' }
                        else { 'Neutral' }
    }

    $cLabel   = Get-ProfileColor -Name Strong
    $cStatus  = Get-ProfileColor -Name $StatusStyle
    $cPath    = Get-ProfileColor -Name Path
    $cDynamic = Get-ProfileColor -Name $DynamicStyle

    Write-Host ("  {0,-27}" -f $Label) -ForegroundColor $cLabel -NoNewline
    Write-Host ("{0,-11}" -f $Status) -ForegroundColor $cStatus -NoNewline
    if ($Path -or $Dynamic) {
        Write-Host ("{0,-35}" -f $Path) -ForegroundColor $cPath -NoNewline
        Write-Host $Dynamic -ForegroundColor $cDynamic
    } else {
        Write-Host ""
    }
}

function Write-SectionHeader {
    param(
        [Parameter(Mandatory)][string]$Number,
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$Status,
        [string]$StatusStyle = "Success",
        [int]$Width = 90
    )
    $cBorder = Get-ProfileColor -Name Border
    $cStatus = Get-ProfileColor -Name $StatusStyle

    $left = "─── $Number · $Title | "
    $rightPrefix = " "
    $usedLen = $left.Length + $Status.Length + $rightPrefix.Length
    $ruleLen = [Math]::Max(5, ($Width - $usedLen))
    $right = $rightPrefix + ("─" * $ruleLen)

    Write-Host ""
    Write-Host $left -ForegroundColor $cBorder -NoNewline
    Write-Host $Status -ForegroundColor $cStatus -NoNewline
    Write-Host $right -ForegroundColor $cBorder
}

function Write-StackRows {
    <# Bot state count (X/3) in header + unified 4-column rows. #>
    param(
        [object[]]$Rows
    )
    $runningCount = @($Rows | Where-Object { $_.Running }).Count
    $totalCount = $Rows.Count
    if ($totalCount -eq 0) { $totalCount = 3 }

    if ($runningCount -eq $totalCount) {
        Write-SectionHeader -Number "2" -Title "BOT STACK" -Status ("ON ({0}/{1})" -f $runningCount, $totalCount) -StatusStyle "Success"
    } elseif ($runningCount -gt 0) {
        Write-SectionHeader -Number "2" -Title "BOT STACK" -Status ("PARTIAL ({0}/{1})" -f $runningCount, $totalCount) -StatusStyle "Warning"
    } else {
        Write-SectionHeader -Number "2" -Title "BOT STACK" -Status ("OFF (0/{0})" -f $totalCount) -StatusStyle "Error"
    }
    foreach ($r in $Rows) {
        Write-ProcessRow -Label $r.Name -Running $r.Running -PidVal $r.Pid -Path $r.Path -RunCmd $r.RunCmd
    }
    if ($Rows.Count -gt 0 -and $runningCount -eq 0 -and (Test-Path $ProcsFile)) {
        Write-FileRow -Label "Process file" -Status "STALE" -Path "runtime/processes.json" -Dynamic "no active PID"
    }
}

function Show-ServiceTable {
    <# Render the /api/system/status payload: bot state count + 3 stack services. #>
    param($Status)
    $rows = @()
    foreach ($svc in @("filter", "query", "decide")) {
        $s = $Status.services.$svc
        if ($s) {
            $label = @{
                filter     = "Market Filter"
                query      = "Query Polymarket"
                decide     = "Decide & Execute"
            }[$svc]
            if (-not $label) { $label = $s.name }
            $rows += [pscustomobject]@{
                Name = $label
                Running = [bool]$s.running
                Pid = $s.pid
                Path = $StackPaths[$svc]
                RunCmd = $StackCmds[$svc]
            }
        }
    }
    Write-StackRows -Rows $rows
}

function Start-BotStack {
    <# Launch market filter + order manager poll + trader loop via the dashboard's start endpoint. #>
    $rerank = Join-Path $ProjectPath "scripts\filter_loop.py"
    if (-not (Test-Path $rerank)) {
        Lsh-Fail "Filter module scripts/filter_loop.py is MISSING in this repo - the dashboard would spawn a phantom process. Add it before starting."
        return
    }
    if (-not (Test-LivePort)) {
        Lsh-Fail "Dashboard is not serving on :$LivePort - nothing to drive. Start the dashboard first."
        return
    }
    $status = $null
    try { $status = Invoke-RestMethod -Uri "$DashUrl/api/system/status" -UseBasicParsing -TimeoutSec 5 } catch {}
    if ($status -and $status.bot_state -eq "RUNNING") {
        Lsh-Warn "Bot stack is already running."
        Show-ServiceTable $status
        return
    }
    Lsh-Step "Requesting bot stack start (Market Filter + Query Polymarket + Decide & Execute)..."
    Lsh-Warn "Decide & Execute rests REAL maker bids. Verify the dashboard shows a clean state before proceeding."
    $res = Invoke-Control -Endpoint "/api/system/start"
    if (-not $res) { return }
    if ($res.ok) { Lsh-Ok "Bot stack started: $($res.message)." }
    else         { Lsh-Fail "Start refused: $($res.message)" }
    if ($res.status) { Show-ServiceTable $res.status }
}

function Stop-BotStack {
    <# Stop the stack via the API; if the dashboard is down, kill recorded PIDs directly. #>
    if (Test-LivePort) {
        Lsh-Step "Requesting bot stack stop..."
        $res = Invoke-Control -Endpoint "/api/system/stop"
        if (-not $res) { return }
        if ($res.ok) { Lsh-Ok "Bot stack stopped: $($res.message)." }
        else         { Lsh-Fail "Stop refused: $($res.message)" }
        return
    }
    if (Test-Path $ProcsFile) {
        $saved = $null
        try { $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json } catch { $saved = $null }
        if ($null -eq $saved) {
            # A registry we cannot parse is not an empty one. Falling through
            # would kill nothing, delete the file, and print "stale record
            # cleared" -- destroying the only record of the live PIDs. Matches
            # stop_bot() in dashboard/server.py, which refuses the same way.
            Lsh-Fail "Cannot read $ProcsFile; refusing to report the stack stopped. No process was killed and the file was left in place. Fix or remove it, then stop again."
            return
        }
        $killed = $false
        $skipped = $false
        foreach ($name in @("filter", "query", "decide")) {
            $info = if ($saved) { Get-ServiceEntry -Saved $saved -Key $name } else { $null }
            if ($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid)) {
                # Validate started_at before killing
                $shouldKill = $false
                if (-not $info.started_at) {
                    # No start time: skip this kill
                    $skipped = $true
                    Lsh-Warn "$name (PID $($info.pid)) has no started_at; skipping kill"
                } else {
                    $recordedStart = ConvertTo-RecordedStart -StartedAt $info.started_at
                    if ($null -eq $recordedStart) {
                        $skipped = $true
                        Lsh-Warn "$name (PID $($info.pid)) has an unreadable started_at; skipping kill"
                    } else {
                        try {
                            $proc = Get-Process -Id $info.pid -ErrorAction Stop
                            $actualStart = $proc.StartTime
                            $tolerance = [timespan]::FromSeconds(60)
                            if ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -le $tolerance.TotalSeconds) {
                                $shouldKill = $true
                            } else {
                                $skipped = $true
                                Lsh-Warn "$name (PID $($info.pid)) start time mismatch; skipping kill (recorded: $recordedStart, actual: $actualStart)"
                            }
                        } catch {
                            # If we can't get start time, skip kill
                            $skipped = $true
                            Lsh-Warn "$name (PID $($info.pid)) start time unavailable; skipping kill"
                        }
                    }
                }
                if ($shouldKill) {
                    Lsh-Step "Killing $name (PID $($info.pid))..."
                    taskkill /F /T /PID $info.pid 2>$null | Out-Null
                    $killed = $true
                }
            }
        }
        if ($skipped) {
            # Keep the record. Deleting it here is how a live stack becomes
            # unreachable: the PIDs would be gone and a later start would see
            # STOPPED and launch a second Decide & Execute beside this one.
            Lsh-Fail "Left one or more recorded processes running; keeping $ProcsFile. Stop them by PID, then re-run."
        } else {
            Remove-Item $ProcsFile -ErrorAction SilentlyContinue
            if ($killed) { Lsh-Ok "Bot stack processes killed from recorded PIDs." }
            else         { Lsh-Warn "processes.json exists but no process is alive (stale record cleared)." }
        }
    } else {
        Lsh-Warn "No bot stack is running (no processes.json)."
    }
}

# ── Status ──
function Get-HeartbeatAgeSec {
    param([string]$Ts)
    if (-not $Ts) { return $null }
    try {
        $dt = [datetime]::Parse($Ts, [Globalization.CultureInfo]::InvariantCulture,
                                 [Globalization.DateTimeStyles]::AdjustToUniversal)
        return [int]((Get-Date).ToUniversalTime() - $dt).TotalSeconds
    } catch { return $null }
}

function Show-Status {
    Lsh-Banner -Title "SPREAD HUNTER - STATUS" -Subtitle "Execution engine: $ProjectPath"

    # ── 1 · DASHBOARD ──
    $inst = Get-DashInstance
    $portUp = Test-LivePort
    if ($inst) {
        Write-SectionHeader -Number "1" -Title "DASHBOARD" -Status "ON" -StatusStyle "Success"
        Write-ProcessRow -Label "Dashboard" -Running $true -PidVal $inst.pid -Path $StackPaths["dash"] -ExtraInfo $DashUrl
        Write-FileRow -Label "PID file" -Status "FOUND" -Path "runtime/live-dash.pids.json" -Dynamic ("PID {0} recorded" -f $inst.pid)
    } elseif ($portUp) {
        Write-SectionHeader -Number "1" -Title "DASHBOARD" -Status "LISTENING" -StatusStyle "Warning"
        Write-FileRow -Label "Dashboard" -Status "LISTENING" -Path $StackPaths["dash"] -Dynamic ("PID {0} on port {1} (external)" -f (Get-PortPid), $LivePort)
        Write-FileRow -Label "PID file" -Status "MISSING" -Path "runtime/live-dash.pids.json" -Dynamic "not owned by menu"
    } else {
        Write-SectionHeader -Number "1" -Title "DASHBOARD" -Status "OFF" -StatusStyle "Error"
        Write-ProcessRow -Label "Dashboard" -Running $false -Path $StackPaths["dash"] -RunCmd ("python -m dashboard.server --port {0}" -f $LivePort)
        Write-FileRow -Label "PID file" -Status "MISSING" -Path "runtime/live-dash.pids.json" -Dynamic "no PID file"
    }

    # ── 1b · SHADOW DASHBOARD ──
    $shadowInst = Get-ShadowDashInstance
    if ($shadowInst) {
        Write-SectionHeader -Number "1b" -Title "SHADOW DASHBOARD" -Status "ON" -StatusStyle "Success"
        Write-ProcessRow -Label "Shadow Dashboard" -Running $true -PidVal $shadowInst.pid -Path $StackPaths["shadowDash"] -ExtraInfo "$ShadowDashUrl (db=shadow.db)"
        Write-FileRow -Label "PID file" -Status "FOUND" -Path "runtime/shadow-dash.pids.json" -Dynamic ("PID {0} recorded" -f $shadowInst.pid)
    } elseif ($shadowInst -eq $null -and (Test-Path $ShadowPidFile)) {
        # has pidfile but not alive — stale
        Write-SectionHeader -Number "1b" -Title "SHADOW DASHBOARD" -Status "STALE" -StatusStyle "Warning"
        Write-ProcessRow -Label "Shadow Dashboard" -Running $false -Path $StackPaths["shadowDash"] -RunCmd $StackCmds["shadowDash"]
        Write-FileRow -Label "PID file" -Status "STALE" -Path "runtime/shadow-dash.pids.json" -Dynamic "no active PID"
    } else {
        Write-SectionHeader -Number "1b" -Title "SHADOW DASHBOARD" -Status "OFF" -StatusStyle "Error"
        Write-ProcessRow -Label "Shadow Dashboard" -Running $false -Path $StackPaths["shadowDash"] -RunCmd $StackCmds["shadowDash"]
        Write-FileRow -Label "PID file" -Status "MISSING" -Path "runtime/shadow-dash.pids.json" -Dynamic "no PID file"
    }

    # ── 2 · BOT STACK (dashboard API when up, processes.json otherwise) ──
    $status = $null
    if ($portUp) {
        try { $status = Invoke-RestMethod -Uri "$DashUrl/api/system/status" -UseBasicParsing -TimeoutSec 5 } catch {}
    }
    if ($status) {
        Show-ServiceTable $status
    } else {
        $saved = $null
        if (Test-Path $ProcsFile) { try { $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json } catch {} }
        $rows = @()
        if ($saved) {
            foreach ($name in @("filter", "query", "decide")) {
                $info = Get-ServiceEntry -Saved $saved -Key $name
                $running = [bool]($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid))
                $label = @{
                    filter     = "Market Filter"
                    query      = "Query Polymarket"
                    decide     = "Decide & Execute"
                }[$name]
                $rows += [pscustomobject]@{
                    Name = $label
                    Running = $running
                    Pid = $info.pid
                    Path = $StackPaths[$name]
                    RunCmd = $StackCmds[$name]
                }
            }
            Write-StackRows -Rows $rows
        } else {
            Write-SectionHeader -Number "2" -Title "BOT STACK" -Status "OFF (0/3)" -StatusStyle "Error"
            Write-ProcessRow -Label "Market Filter" -Running $false -Path $StackPaths["filter"] -RunCmd $StackCmds["filter"]
            Write-ProcessRow -Label "Query Polymarket" -Running $false -Path $StackPaths["query"] -RunCmd $StackCmds["query"]
            Write-ProcessRow -Label "Decide & Execute" -Running $false -Path $StackPaths["decide"] -RunCmd $StackCmds["decide"]
        }
    }

    # ── 3 · GLOBAL STOP LOSS (API when up, heartbeat file otherwise) ──
    $gh = $null
    if ($portUp) {
        try { $gh = Invoke-RestMethod -Uri "$DashUrl/api/guardrail-health" -UseBasicParsing -TimeoutSec 5 } catch {}
    }
    if ($gh) {
        if ($gh.running) {
            Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status "ON" -StatusStyle "Success"
            Write-ProcessRow -Label "Global Stop Loss" -Running $true -PidVal $gh.pid -Path $StackPaths["guardrail"]
            Write-FileRow -Label "Heartbeat file" -Status "FOUND" -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic ("{0} old" -f (Format-AgeSec ([int]$gh.age_s)))
            Write-FileRow -Label "Alerts log" -Status "FOUND" -Path "runtime/global_stop_loss_alerts.log" -Dynamic ("{0} alert(s)" -f $gh.alerts_total)
        } else {
            Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status "OFF" -StatusStyle "Error"
            Write-ProcessRow -Label "Global Stop Loss" -Running $false -Path $StackPaths["guardrail"] -RunCmd "python -m scripts.global_stop_loss"
            Write-FileRow -Label "Heartbeat file" -Status "STALE" -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic ("{0} old" -f (Format-AgeSec ([int]$gh.age_s)))
            Write-FileRow -Label "Alerts log" -Status "FOUND" -Path "runtime/global_stop_loss_alerts.log" -Dynamic ("{0} alert(s)" -f $gh.alerts_total)
        }
    } elseif (Test-Path $HbFile) {
        try {
            $hb = Get-Content $HbFile -Raw | ConvertFrom-Json
            $age = Get-HeartbeatAgeSec $hb.ts
            if ($null -ne $age -and $age -le 30) {
                Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status "ON" -StatusStyle "Success"
                Write-ProcessRow -Label "Global Stop Loss" -Running $true -PidVal $hb.pid -Path $StackPaths["guardrail"]
                Write-FileRow -Label "Heartbeat file" -Status "FOUND" -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic ("{0} old" -f (Format-AgeSec $age))
                Write-FileRow -Label "Alerts log" -Status "FOUND" -Path "runtime/global_stop_loss_alerts.log" -Dynamic "0 alerts"
            } else {
                Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status "OFF" -StatusStyle "Error"
                Write-ProcessRow -Label "Global Stop Loss" -Running $false -Path $StackPaths["guardrail"] -RunCmd "python -m scripts.global_stop_loss"
                Write-FileRow -Label "Heartbeat file" -Status "STALE" -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic ("{0} old" -f (Format-AgeSec $age))
                Write-FileRow -Label "Alerts log" -Status "FOUND" -Path "runtime/global_stop_loss_alerts.log" -Dynamic "0 alerts"
            }
        } catch {
            Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status "OFF" -StatusStyle "Error"
            Write-ProcessRow -Label "Global Stop Loss" -Running $false -Path $StackPaths["guardrail"] -RunCmd "python -m scripts.global_stop_loss"
            Write-FileRow -Label "Heartbeat file" -Status "ERROR" -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic "unreadable"
            Write-FileRow -Label "Alerts log" -Status "FOUND" -Path "runtime/global_stop_loss_alerts.log" -Dynamic "0 alerts"
        }
    } else {
        Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status "OFF" -StatusStyle "Error"
        Write-ProcessRow -Label "Global Stop Loss" -Running $false -Path $StackPaths["guardrail"] -RunCmd "python -m scripts.global_stop_loss"
        Write-FileRow -Label "Heartbeat file" -Status "MISSING" -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic "no file"
        Write-FileRow -Label "Alerts log" -Status "MISSING" -Path "runtime/global_stop_loss_alerts.log" -Dynamic "no file"
    }

    # ── 4 · MARKET FILTER & UNIVERSE FEED (what the stack depends on) ──
    Show-ScreenerAndFeed

    # ── 5 · CHECKOUT IDENTITY (prove every launched module resolves inside THIS repo) ──
    Show-CheckoutIdentity
}

function Show-ScreenerAndFeed {
    $rerank = Join-Path $ProjectPath "scripts\filter_loop.py"
    $ranker = Join-Path $ProjectPath "scripts\filter_markets.py"
    $strategyCfg = Join-Path $ProjectPath "scoring\config.py"
    $modulesOk = ((Test-Path $rerank) -and (Test-Path $ranker) -and (Test-Path $strategyCfg))

    $feed = Resolve-RuntimeFile -Name "markets.json"
    $feedExists = Test-Path $feed
    $ageSec = if ($feedExists) { [int]((Get-Date) - (Get-Item $feed).LastWriteTime).TotalSeconds } else { $null }
    $count = "?"
    if ($feedExists) {
        try { $count = @(Get-Content $feed -Raw | ConvertFrom-Json).Count } catch {}
    }

    $feedStatus = "FOUND"
    if (-not $feedExists)      { $feedStatus = "MISSING" }
    elseif ($ageSec -gt 21600) { $feedStatus = "STALE" }
    elseif ($ageSec -gt 3600)  { $feedStatus = "AGING" }

    $hdrStatus = if (-not $modulesOk) { "MISSING" } else { $feedStatus }
    Write-SectionHeader -Number "4" -Title "MARKET FILTER & UNIVERSE FEED" -Status $hdrStatus

    $stRerank = if (Test-Path $rerank) { "FOUND" } else { "MISSING" }
    Write-FileRow -Label "Filter loop" -Status $stRerank -Path "scripts/filter_loop.py" -Dynamic "runs every 10 min"

    $stRanker = if (Test-Path $ranker) { "FOUND" } else { "MISSING" }
    Write-FileRow -Label "Filter engine" -Status $stRanker -Path "scripts/filter_markets.py" -Dynamic "fetch & filter"

    $feedDetail = if (-not $feedExists) { "Run: python -m scripts.filter_loop" }
                  elseif ($feedStatus -eq "STALE") { "{0} market(s) · {1} old · Run: python -m scripts.filter_loop" -f $count, (Format-AgeSec $ageSec) }
                  else { "{0} market(s) · {1} old" -f $count, (Format-AgeSec $ageSec) }
    # Show the path actually read, so a pre-rename run/markets.json is visible.
    $feedRel = ($feed.Substring($ProjectPath.Length).Trim("\", "/")) -replace "\\", "/"
    Write-FileRow -Label "Universe feed" -Status $feedStatus -Path $feedRel -Dynamic $feedDetail
}

function Show-CheckoutIdentity {
    $code = "import sys; sys.path.insert(0, r'$ProjectPath'); import os, dashboard.server as d, core_brain.order_manager as m, core_brain.trader_loop as f, scripts.filter_loop as r, scripts.filter_markets as k; print('cwd=' + os.getcwd()); print(d.__file__); print(m.__file__); print(f.__file__); print(r.__file__); print(k.__file__)"
    $raw = @()
    try { $raw = @(& python -c $code 2>&1) } catch {}
    if ($raw.Count -lt 5) {
        $raw = @()
        try { $raw = @(& py -3 -c $code 2>&1) } catch {}
    }
    $paths = @($raw | Where-Object { $_ -is [string] -and $_ -match '^[A-Za-z]:[\\/]' })

    $allOk = ($paths.Count -ge 5)
    if ($allOk) {
        for ($i = 0; $i -lt 5; $i++) {
            if ($paths[$i].Trim() -notlike "$ProjectPath*") { $allOk = $false; break }
        }
    }

    if ($allOk) {
        Write-SectionHeader -Number "5" -Title "CHECKOUT IDENTITY" -Status "FOUND" -StatusStyle "Success"
    } else {
        Write-SectionHeader -Number "5" -Title "CHECKOUT IDENTITY" -Status "CHECK" -StatusStyle "Error"
    }

    if ($paths.Count -lt 5) {
        Write-FileRow -Label "Module resolution" -Status "MISSING" -Path "python" -Dynamic "import failed"
        $tail = (($raw | Select-Object -Last 2 | ForEach-Object { "$_" }) -join " ")
        if ($tail) { Write-FileRow -Label "Diagnostic" -Status "WARN" -Path "error" -Dynamic $tail }
        return
    }
    $labels = @("dashboard.server", "core_brain.order_manager", "core_brain.trader_loop", "scripts.filter_loop", "scripts.filter_markets")
    for ($i = 0; $i -lt 5; $i++) {
        $p = $paths[$i].Trim()
        if ($p -like "$ProjectPath*") {
            Write-FileRow -Label $labels[$i] -Status "FOUND" -Path "" -Dynamic ""
        } else {
            Write-FileRow -Label $labels[$i] -Status "MISSING" -Path "" -Dynamic ($p + " <-- outside repo!")
        }
    }
}

# ── Reset & fresh start ──
function Test-StackAlive {
    <# True when ANY spread-hunter process is alive: dashboards, stack PIDs,
    or the guardrail heartbeat. Used by reset to decide whether a stop is
    needed and to prove the environment is clean before starting. #>
    if (Test-LivePort) { return $true }
    if ($null -ne (Get-DashInstance)) { return $true }
    if ($null -ne (Get-ShadowDashInstance)) { return $true }
    if (Test-Path $ProcsFile) {
        try {
            $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json
            foreach ($name in @("filter", "query", "decide")) {
                $info = Get-ServiceEntry -Saved $saved -Key $name
                if ($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid)) { return $true }
            }
        } catch {}
    }
    if (Test-Path $HbFile) {
        try {
            $hb = @(Get-Content $HbFile -Raw | ConvertFrom-Json)[0]
            if ($hb -and $hb.pid -and (Test-PidAlive -ProcessId $hb.pid)) { return $true }
        } catch {}
    }
    return $false
}

function Stop-Guardrail {
    <# Stop the global stop loss watcher via its heartbeat record (pid +
    started_at), matching the start-time tolerance used for stack PIDs. A
    stale or unreadable record is never trusted to kill a PID. #>
    if (-not (Test-Path $HbFile)) { return }
    $hb = $null
    try { $hb = @(Get-Content $HbFile -Raw | ConvertFrom-Json)[0] } catch {}
    if (-not $hb -or -not $hb.pid) {
        Remove-Item $HbFile -ErrorAction SilentlyContinue
        return
    }
    if (-not (Test-PidAlive -ProcessId $hb.pid)) {
        Remove-Item $HbFile -ErrorAction SilentlyContinue
        return
    }
    $recordedStart = ConvertTo-RecordedStart -StartedAt $hb.started_at
    if ($null -eq $recordedStart) {
        Lsh-Warn "Guardrail (PID $($hb.pid)) has an unreadable started_at; skipping kill, keeping $HbFile"
        return
    }
    try {
        $proc = Get-Process -Id $hb.pid -ErrorAction Stop
        $tolerance = [timespan]::FromSeconds(60)
        if ([math]::Abs(($proc.StartTime - $recordedStart).TotalSeconds) -gt $tolerance.TotalSeconds) {
            Lsh-Warn "Guardrail (PID $($hb.pid)) start time mismatch; skipping kill, keeping $HbFile"
            return
        }
        Lsh-Step "Stopping guardrail PID $($hb.pid)..."
        taskkill /F /T /PID $hb.pid 2>$null | Out-Null
        Remove-Item $HbFile -ErrorAction SilentlyContinue
        Lsh-Ok "Guardrail stopped."
    } catch {
        Lsh-Warn "Guardrail PID $($hb.pid) unavailable; keeping $HbFile"
    }
}

function Clear-RuntimeState {
    <# Wipe every regenerable runtime artifact for a first-run feel.

    NEVER touches data/orders.db (the production registry) or anything
    git-tracked: only gitignored state that processes recreate (runtime/*,
    run/, shadow stores, run-id markers). Call it only after Test-StackAlive
    reports nothing running. #>
    $removed = 0
    $files = @(Get-ChildItem $RunDir -File -ErrorAction SilentlyContinue)
    if ($files.Count -gt 0) { $files | Remove-Item -Force -ErrorAction SilentlyContinue; $removed += $files.Count }
    if (Test-Path $LegacyRunDir) {
        Remove-Item $LegacyRunDir -Recurse -Force -ErrorAction SilentlyContinue
        $removed++
    }
    # Shadow rehearsal artifacts (data/orders.db is never touched)
    foreach ($p in @("data/shadow.db", "data/shadow.db-wal", "data/shadow.db-shm",
                     "data/shadow_stat_verify.db", "data/shadow_stat_verify.db-wal", "data/shadow_stat_verify.db-shm",
                     "data/_preview_seed.db", "data/_preview_seed.db-wal", "data/_preview_seed.db-shm",
                     "data/_smoke_fleet.db", "data/_smoke_fleet.db-wal", "data/_smoke_fleet.db-shm",
                     "runtime/.current_run_id", "data/.current_run_id")) {
        if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue; $removed++ }
    }
    Get-ChildItem "data" -Directory -Filter "shadow_stat_*" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    Lsh-Ok "Runtime state wiped ($removed artifact(s) removed). data/orders.db untouched."
}

function Reset-Environment {
    <# Full fresh-start reset: verify -> stop -> wipe -> verify clean ->
    start the requested mode. Mode "none" stops after the clean proof;
    "shadow" also brings up the shadow dashboard (and a rehearsal run when
    -Minutes > 0); "live" starts the live stack and REQUIRES $Force because
    Decide & Execute rests real maker bids. #>
    param(
        [Parameter(Mandatory)][string]$Mode,
        [switch]$Force
    )
    Lsh-Banner -Title "RESET & FRESH START" -Subtitle "verify -> stop -> wipe -> start ($Mode)"

    # 1 · VERIFY: what is alive right now
    $found = @()
    if (Test-LivePort) { $found += "port :$LivePort (PID $(Get-PortPid))" }
    $dash = Get-DashInstance;  if ($dash) { $found += "live dashboard PID $($dash.pid)" }
    $sdash = Get-ShadowDashInstance; if ($sdash) { $found += "shadow dashboard PID $($sdash.pid)" }
    if (Test-Path $ProcsFile) {
        try {
            $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json
            foreach ($name in @("filter", "query", "decide")) {
                $info = Get-ServiceEntry -Saved $saved -Key $name
                if ($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid)) { $found += "$name PID $($info.pid)" }
            }
        } catch { Lsh-Warn "Could not read $ProcsFile; stop will keep it in place if unreadable." }
    }
    if (Test-Path $HbFile) {
        try {
            $hb = @(Get-Content $HbFile -Raw | ConvertFrom-Json)[0]
            if ($hb -and $hb.pid -and (Test-PidAlive -ProcessId $hb.pid)) { $found += "guardrail PID $($hb.pid)" }
        } catch {}
    }
    if ($found.Count -gt 0) { Lsh-Step "Alive: $(($found -join ', ')) - stopping, then wiping." }
    else                   { Lsh-Ok "Nothing spread-hunter is running (port free, no stack PIDs, no guardrail)." }

    # 2 · STOP (API stop when a dashboard answers; recorded PIDs otherwise)
    if (Test-StackAlive) {
        Stop-BotStack
        Stop-Guardrail
        $null = Stop-Dashboard
        $null = Stop-ShadowDashboard
    } else {
        Stop-Guardrail  # clears a stale heartbeat record even when nothing is alive
    }

    # 3 · VERIFY STOPPED before wiping (never wipe beside a live stack)
    if (Test-StackAlive) {
        Lsh-Fail "Refusing to wipe: a spread-hunter process is still alive. Re-run reset, or free :$LivePort manually."
        return $false
    }

    # 4 · WIPE
    Clear-RuntimeState

    # 5 · VERIFY CLEAN
    if (Test-LivePort) {
        Lsh-Fail "Port $LivePort is still LISTENING (PID $(Get-PortPid)) and not owned by this menu. Free it, then start."
        return $false
    }
    Lsh-Ok "Environment verified clean - no blockers, no contradictions."

    # 6 · START
    switch ($Mode) {
        "shadow" {
            if (Start-ShadowDashboard) {
                Start-Process $ShadowDashUrl
                Lsh-Ok "Opened $ShadowDashUrl in default browser (shadow db=data/shadow.db)."
                if ($Minutes -gt 0) {
                    Lsh-Step "Launching shadow rehearsal (python -m core_brain.shadow_run --minutes $Minutes --db data/shadow.db)..."
                    $shadowRun = Start-Process -FilePath "python" `
                        -ArgumentList "-m", "core_brain.shadow_run", "--minutes", "$Minutes", "--db", "data/shadow.db" `
                        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                        -RedirectStandardOutput (Join-Path $RunDir "shadow_run.out.log") `
                        -RedirectStandardError (Join-Path $RunDir "shadow_run.err.log")
                    Lsh-Ok "Shadow rehearsal running (PID $($shadowRun.Id), $Minutes minute(s)) - dashboard updates live from data/shadow.db."
                }
            } else {
                return $false
            }
        }
        "live" {
            if (-not $Force) {
                Lsh-Fail "Live start requires explicit confirmation: use -Yes (CLI) or the typed START confirm (menu)."
                return $false
            }
            if (Start-Dashboard) { Start-BotStack }
        }
        default {
            Lsh-Ok "Reset complete. Next: 'shadow' for a risk-free rehearsal, 'open' for a bare live dashboard, or 'start -Yes' for the live stack."
        }
    }
    return $true
}

# ── Menu ──
function Show-MenuGrid {
    $cInfo    = Get-ProfileColor -Name Info
    $cStrong  = Get-ProfileColor -Name Strong
    $cNeutral = Get-ProfileColor -Name Neutral

    Write-Host "  SPREAD HUNTER - CONTROL CENTER" -ForegroundColor $cInfo
    Write-Host ("" + ("─" * 30)) -ForegroundColor (Get-ProfileColor -Name Border)
    Write-Host ""

    # Grouped levers: badge (cyan number) + icon + label + description. One
    # wording style: Start/Open = "Start the <target>", Stop = "Stop the
    # <target>", Reset+ = "Stop all, wipe state, then start the <target>".
    # Live actions that rest real maker bids carry "; type START" as the cue.
    $groups = @(
        @{ Header = "LIVE - real maker bids"; Items = @(
            @{ K = "1"; Icon = "▶"; IconColor = "Success"; V = "Start Live Stack";         D = "Start the live dashboard + bot stack; type START to confirm" }
            @{ K = "2"; Icon = "↺"; IconColor = "Error";   V = "Reset + Live Stack";       D = "Stop all, wipe state, then start the live stack; type START" }
            @{ K = "3"; Icon = "■"; IconColor = "Error";   V = "Stop Live Stack";          D = "Stop the live bot stack, then the dashboard" }
            @{ K = "4"; Icon = "◉"; IconColor = "Warning"; V = "Open Dashboard";           D = "Live dashboard only, no bot; open in browser" }
        ) }
        @{ Header = "SHADOW - rehearsal, spends nothing"; Items = @(
            @{ K = "5"; Icon = "◎"; IconColor = "Info";    V = "Open Shadow Dashboard";    D = "Start the shadow dashboard only; drive a run with shadow_run" }
            @{ K = "6"; Icon = "↺"; IconColor = "Info";    V = "Reset + Shadow Dashboard"; D = "Stop all, wipe state, then start the shadow dashboard only" }
            @{ K = "7"; Icon = "□"; IconColor = "Neutral"; V = "Stop Shadow Dashboard";    D = "Stop the shadow viewer on :8799 only" }
        ) }
        @{ Header = "RESET / STATUS"; Items = @(
            @{ K = "8"; Icon = "↺"; IconColor = "Warning"; V = "Reset State Only";         D = "Stop all, wipe runtime state, verify clean; starts nothing" }
            @{ K = "9"; Icon = "≡"; IconColor = "Info";    V = "Status";                   D = "All processes + feed + repo identity" }
        ) }
    )

    foreach ($g in $groups) {
        Write-Host ("  " + $g.Header) -ForegroundColor $cInfo
        foreach ($it in $g.Items) {
            Write-Host "   " -NoNewline
            Write-Host (" {0} " -f $it.K) -BackgroundColor DarkCyan -ForegroundColor White -NoNewline
            Write-Host ("  {0} " -f $it.Icon) -ForegroundColor (Get-ProfileColor -Name $it.IconColor) -NoNewline
            Write-Host ("{0,-26}" -f $it.V) -ForegroundColor $cStrong -NoNewline
            Write-Host $it.D -ForegroundColor $cNeutral
        }
        Write-Host ""
    }
    Write-Host "  q  × Exit · Return to PowerShell" -ForegroundColor $cNeutral
}

function Invoke-LiveAction {
    param([string]$Key)
    switch ($Key) {
        "1" {
            # Require typed confirmation for menu-driven start
            Write-Host ""
            $confirm = Read-Host "Type START to confirm starting the bot stack (or any other key to cancel)"
            if ($confirm -ne "START") {
                Lsh-Warn "Start cancelled."
                return
            }
            if (Start-Dashboard) { Start-BotStack }
        }
        "2" {
            Write-Host ""
            $confirm = Read-Host "  Type START to confirm: stop everything, wipe state, then start the LIVE stack (real maker bids)"
            if ($confirm -ne "START") { Lsh-Warn "Reset cancelled."; return }
            $null = Reset-Environment -Mode "live" -Force
        }
        "3" {
            Stop-BotStack
            $null = Stop-Dashboard
            Lsh-Ok "Live stack is down."
        }
        "4" {
            if (Start-Dashboard) {
                try {
                    Lsh-Step "Sweeping Polymarket account balance to sync live starting capital..."
                    & python -m core_brain.order_manager account-sweep --quiet
                    if ($LASTEXITCODE -ne 0) {
                        Lsh-Warn "Initial account sweep exited with code $LASTEXITCODE; using local registry marks."
                    }
                } catch {
                    Lsh-Warn "Initial account sweep skipped: $_"
                }
                Start-Process $DashUrl
                Lsh-Ok "Opened $DashUrl in default browser."
            }
        }
        "5" {
            if (Start-ShadowDashboard) {
                Start-Process $ShadowDashUrl
                Lsh-Ok "Opened $ShadowDashUrl in default browser (shadow db=data/shadow.db)."
            }
        }
        "6" {
            $confirm = Read-Host "  Stop everything, wipe runtime state, then start the SHADOW dashboard? [y/N]"
            if ($confirm -match '^[yY]') { $null = Reset-Environment -Mode "shadow" }
            else { Lsh-Warn "Reset cancelled." }
        }
        "7" {
            if (Stop-ShadowDashboard) {
                Lsh-Ok "Shadow dashboard stopped — port $ShadowPort is free."
            } else {
                Lsh-Warn "Shadow dashboard stop finished with port still LISTENING — check PID."
            }
        }
        "8" {
            $confirm = Read-Host "  Wipe all runtime state (logs, events, feed, shadow stores)? data/orders.db is kept. [y/N]"
            if ($confirm -match '^[yY]') { $null = Reset-Environment -Mode "none" }
            else { Lsh-Warn "Reset cancelled." }
        }
        "9" { Show-Status }
        "q" { Write-Host "Exiting Spread Hunter Live menu." -ForegroundColor (Get-ProfileColor -Name Neutral); exit 0 }
        default {
            Lsh-Warn "Invalid selection: $Key (choose 1-9, or q)."
            Start-Sleep -Seconds 1
        }
    }
}

# ── Dispatch ──
if ($Action -ne "") {
    $actionMap = @{
        "start"        = "1"
        "reset-live"   = "2"
        "stop"         = "3"
        "open"         = "4"
        "dashboard"    = "4"
        "dash"         = "4"
        "shadow"       = "5"
        "shadow-open"  = "5"
        "shadow-dash"  = "5"
        "open-shadow"  = "5"
        "reset-shadow" = "6"
        "shadow-stop"  = "7"
        "stop-shadow"  = "7"
        "reset"        = "8"
        "status"       = "9"
    }
    $key = $Action.Trim().ToLower()
    if ($actionMap.ContainsKey($key)) { $key = $actionMap[$key] }

    # Require -Yes flag for non-interactive start (live stack paths: start, reset-live)
    if (($key -eq "1" -or $key -eq "2") -and -not $Yes) {
        Write-Host "ERROR: Non-interactive start requires explicit -Yes flag" -ForegroundColor Red
        Write-Host "Usage: .\scripts\spread-hunter-menu.ps1 start -Yes   or   .\scripts\spread-hunter-menu.ps1 reset-live -Yes"
        exit 1
    }

    Invoke-LiveAction $key
    exit 0
}

Lsh-Banner -Title "SPREAD HUNTER LIVE - CONTROL CENTER"
Show-MenuGrid
Write-Host "  Select " -ForegroundColor Gray -NoNewline
Write-Host "[1-9, q]" -ForegroundColor Cyan -NoNewline
Write-Host " › " -ForegroundColor Yellow -NoNewline
$choice = Read-Host
if ($null -eq $choice) { exit 0 }
$choice = $choice.Trim().ToLower()
if ($choice -eq "") { exit 0 }
Invoke-LiveAction $choice
exit 0
