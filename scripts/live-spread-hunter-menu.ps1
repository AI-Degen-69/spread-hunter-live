# SPREAD HUNTER LIVE - LIVE-ONLY CONTROL CENTER
# Standalone menu for the spread-hunter-live execution engine
# (C:\Users\Tiger\Agents\Projects\spread-hunter-live).
#
# Kept deliberately separate from the simulation menu (hunter-menu.ps1 in the
# "AI Trading/spread-hunter" checkout): this menu only touches the live engine.
#
# Usage:
#   .\scripts\live-spread-hunter-menu.ps1          # interactive menu
#   .\scripts\live-spread-hunter-menu.ps1 start    # dashboard + bot stack (detached)
#   .\scripts\live-spread-hunter-menu.ps1 stop     # bot stack, then dashboard
#   .\scripts\live-spread-hunter-menu.ps1 status   # dashboard + every assisting process
#   .\scripts\live-spread-hunter-menu.ps1 open     # open the dashboard in the browser
#
# The bot stack (screener / engine-poll / fleet) is started and stopped through
# the dashboard's own /api/system/start|stop endpoints -- the same code path as
# the dashboard's START/STOP buttons (interprocess lock, starting-capital
# snapshot, shared run_id). The dashboard process itself is owned by this
# script via run/live-dash.pids.json, mirroring the old checkout's convention.
#
# NOTE: "start" launches the fleet, which rests REAL maker bids. That is an
# opening command and requires explicit supervision (AGENTS.md).

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = "",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LivePort    = 8799
$DashUrl     = "http://127.0.0.1:$LivePort"
$RunDir      = Join-Path $ProjectPath "run"
$DashPidFile = Join-Path $RunDir "live-dash.pids.json"
$ProcsFile   = Join-Path $RunDir "live_procs.json"
$OutLog      = Join-Path $RunDir "live_dash.out.log"
$ErrLog      = Join-Path $RunDir "live_dash.err.log"
$HbFile      = Join-Path $RunDir "guardrail_watch_heartbeat.json"

# Module map: each stack process -> its source file (relative to the repo root).
$StackPaths = @{
    supervisor = "engine/live_exec.py"   # the engine poll loop doubles as supervisor
    screener   = "scripts/rerank_loop.py"
    engine     = "engine/live_exec.py"
    fleet      = "engine/live_fleet.py"
    guardrail  = "scripts/guardrail_watch.py"
    dash       = "dash/live_dash.py"
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
function Lsh-Banner { param([string]$Title, [string]$Subtitle) try { Clear-Host } catch {}; Write-ProfileBanner -Title $Title -Subtitle $Subtitle -Style Info }
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

# ── Dashboard process ownership (run/live-dash.pids.json) ──
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
    Lsh-Step "Launching dashboard (python -m dash.live_dash --port $LivePort)..."
    $dash = Start-Process -FilePath "python" `
        -ArgumentList "-m", "dash.live_dash", "--port", "$LivePort" `
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
# The PCS canonical family (Write-ProfileStatus / Write-ProfileKeyValue) cannot
# express a fixed status column next to a padded label, so the status view uses
# these local helpers. Colors still come from Get-ProfileColor (theme rule 5).
#
# Column contract - every section's rows start at the same positions:
#   col 3   label   (25 wide, Strong/white)
#   col 29  status  (9 wide, red/green/yellow word) OR the value of a key-value row
#   col 40  detail  (PIDs, URLs, paths; style color)
#   col 54  path    (stack table only: source file of each process)
function Write-StatusLine {
    param(
        [Parameter(Mandatory)][string]$Label,
        [AllowEmptyString()][string]$Status = "",
        [AllowEmptyString()][string]$Detail = "",
        [string]$StatusStyle = "Info",
        [string]$DetailStyle = "Info"
    )
    $cLabel  = Get-ProfileColor -Name Strong
    $cStatus = Get-ProfileColor -Name $StatusStyle
    $cDetail = Get-ProfileColor -Name $DetailStyle
    Write-Host ("  {0,-25}" -f $Label) -ForegroundColor $cLabel -NoNewline
    if ($Status) {
        Write-Host ("  {0,-9}" -f $Status) -ForegroundColor $cStatus -NoNewline
        if ($Detail) { Write-Host ("  " + $Detail) -ForegroundColor $cDetail } else { Write-Host "" }
    } else {
        if ($Detail) { Write-Host ("  " + $Detail) -ForegroundColor $cDetail } else { Write-Host "" }
    }
}

function Write-StackRow {
    <# One bot-stack row: name | status | PID | source file, all columns padded. #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][bool]$Running,
        [object]$ProcId,
        [Parameter(Mandatory)][string]$Path
    )
    $word = if ($Running) { "RUNNING" } else { "STOPPED" }
    $cStatus = Get-ProfileColor -Name $(if ($Running) { "Success" } else { "Error" })
    $pidTxt = if ($ProcId) { "PID $ProcId" } else { "" }
    Write-Host ("  {0,-25}" -f $Name) -ForegroundColor (Get-ProfileColor -Name Strong) -NoNewline
    Write-Host ("  {0,-9}" -f $word) -ForegroundColor $cStatus -NoNewline
    Write-Host ("  {0,-12}" -f $pidTxt) -ForegroundColor (Get-ProfileColor -Name Neutral) -NoNewline
    Write-Host ("  " + $Path) -ForegroundColor (Get-ProfileColor -Name Path)
}

function Write-StackRows {
    <# Bot state + starting capital + one aligned row per stack process. #>
    param(
        [object[]]$Rows,
        [string]$BotState,
        [object]$Capital
    )
    if ($BotState -eq "RUNNING") {
        Write-StatusLine -Label "Bot state" -Status "RUNNING" -StatusStyle Success
    } else {
        Write-StatusLine -Label "Bot state" -Status "STOPPED" -StatusStyle Error
    }
    if ($null -ne $Capital) {
        $capTxt = "{0:F2}" -f [double]$Capital
        Write-StatusLine -Label "Starting capital" -Detail ("$" + $capTxt + " · snapshot at bot start")
    }
    foreach ($r in $Rows) { Write-StackRow -Name $r.Name -Running $r.Running -ProcId $r.Pid -Path $r.Path }
    if ($Rows.Count -gt 0 -and @($Rows | Where-Object { $_.Running }).Count -eq 0) {
        Write-StatusLine -Label "live_procs.json" -Status "STALE" -StatusStyle Warning -Detail "record exists but no process is alive"
    }
}

function Show-ServiceTable {
    <# Render the /api/system/status payload: bot state + each stack service
    (dash excluded - it is reported separately in the DASHBOARD section). #>
    param($Status)
    $rows = @()
    $rows += [pscustomobject]@{ Name = "Supervisor"; Running = [bool]$Status.supervisor.running; Pid = $Status.supervisor.pid; Path = $StackPaths["supervisor"] }
    foreach ($svc in @("screener", "engine", "fleet")) {
        $s = $Status.services.$svc
        $rows += [pscustomobject]@{ Name = $s.name; Running = [bool]$s.running; Pid = $s.pid; Path = $StackPaths[$svc] }
    }
    Write-StackRows -Rows $rows -BotState ([string]$Status.bot_state) -Capital $Status.starting_capital
}

function Start-BotStack {
    <# Launch screener + engine-poll + fleet via the dashboard's start endpoint. #>
    $rerank = Join-Path $ProjectPath "scripts\rerank_loop.py"
    if (-not (Test-Path $rerank)) {
        Lsh-Fail "Screener module scripts/rerank_loop.py is MISSING in this repo - the dashboard would spawn a phantom process. Add it before starting."
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
    Lsh-Step "Requesting bot stack start (screener + engine poll + fleet)..."
    Lsh-Warn "The fleet rests REAL maker bids. Verify the dashboard shows a clean state before proceeding."
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
        try { $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json } catch { $saved = $null }
        $killed = $false
        foreach ($name in @("supervisor", "screener", "engine", "fleet")) {
            $info = if ($saved) { $saved.$name } else { $null }
            if ($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid)) {
                # Validate started_at before killing
                $shouldKill = $false
                if (-not $info.started_at) {
                    # No start time: skip this kill
                    Lsh-Warn "$name (PID $($info.pid)) has no started_at; skipping kill"
                } else {
                    try {
                        $recordedStart = [datetime]::Parse([string]$info.started_at)
                        $proc = Get-Process -Id $info.pid -ErrorAction Stop
                        $actualStart = $proc.StartTime
                        $tolerance = [timespan]::FromSeconds(60)
                        if ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -le $tolerance.TotalSeconds) {
                            $shouldKill = $true
                        } else {
                            Lsh-Warn "$name (PID $($info.pid)) start time mismatch; skipping kill (recorded: $recordedStart, actual: $actualStart)"
                        }
                    } catch {
                        # If we can't get start time, skip kill
                        Lsh-Warn "$name (PID $($info.pid)) start time unavailable; skipping kill"
                    }
                }
                if ($shouldKill) {
                    Lsh-Step "Killing $name (PID $($info.pid))..."
                    taskkill /F /T /PID $info.pid 2>$null | Out-Null
                    $killed = $true
                }
            }
        }
        Remove-Item $ProcsFile -ErrorAction SilentlyContinue
        if ($killed) { Lsh-Ok "Bot stack processes killed from recorded PIDs." }
        else         { Lsh-Warn "live_procs.json exists but no process is alive (stale record cleared)." }
    } else {
        Lsh-Warn "No bot stack is running (no live_procs.json)."
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
    Lsh-Banner -Title "SPREAD HUNTER LIVE - STATUS" -Subtitle "Execution engine: $ProjectPath"

    # ── 1 · DASHBOARD ──
    Write-ProfileRuleWithText -Text "1 · DASHBOARD" -Style Border
    $inst = Get-DashInstance
    $portUp = Test-LivePort
    if ($inst) {
        Write-StatusLine -Label "Dashboard" -Status "RUNNING" -StatusStyle Success `
            -Detail ("PID {0} · port {1} · up {2}" -f $inst.pid, $LivePort, (Format-Uptime $inst.proc.StartTime))
    } elseif ($portUp) {
        Write-StatusLine -Label "Dashboard" -Status "LISTENING" -StatusStyle Warning `
            -Detail ("PID {0} on port {1} · not owned by this menu" -f (Get-PortPid), $LivePort)
    } else {
        Write-StatusLine -Label "Dashboard" -Status "STOPPED" -StatusStyle Error `
            -Detail "nothing serving on port $LivePort"
    }
    Write-StatusLine -Label "Dashboard URL" -Detail $DashUrl -DetailStyle Link
    Write-StatusLine -Label "PID file" -Detail "run/live-dash.pids.json" -DetailStyle Path

    # ── 2 · BOT STACK (dashboard API when up, live_procs.json otherwise) ──
    Write-ProfileRuleWithText -Text "2 · BOT STACK" -Style Border
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
            foreach ($name in @("supervisor", "screener", "engine", "fleet")) {
                $info = $saved.$name
                $running = [bool]($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid))
                $label = @{
                    supervisor = "Supervisor"
                    screener   = "Screener (rerank)"
                    engine     = "Engine (sweep/poll)"
                    fleet      = "Fleet (decide/submit)"
                }[$name]
                $rows += [pscustomobject]@{ Name = $label; Running = $running; Pid = $info.pid; Path = $StackPaths[$name] }
            }
            $botState = if (@($rows | Where-Object { $_.Running }).Count -gt 0) { "RUNNING" } else { "STOPPED" }
            Write-StackRows -Rows $rows -BotState $botState -Capital $saved.starting_account_value
        } else {
            Write-StatusLine -Label "Bot stack" -Status "STOPPED" -StatusStyle Error -Detail "no run/live_procs.json on disk"
        }
    }

    # ── 3 · GUARDRAIL WATCHDOG (API when up, heartbeat file otherwise) ──
    Write-ProfileRuleWithText -Text "3 · GUARDRAIL WATCHDOG" -Style Border
    $gh = $null
    if ($portUp) {
        try { $gh = Invoke-RestMethod -Uri "$DashUrl/api/guardrail-health" -UseBasicParsing -TimeoutSec 5 } catch {}
    }
    if ($gh) {
        if ($gh.running) {
            Write-StatusLine -Label "Guardrail Watchdog" -Status "RUNNING" -StatusStyle Success `
                -Detail ("PID {0} · heartbeat {1}s old · alerts {2}" -f $gh.pid, [int]$gh.age_s, $gh.alerts_total)
        } else {
            Write-StatusLine -Label "Guardrail Watchdog" -Status "DOWN" -StatusStyle Error `
                -Detail ("heartbeat {0}s old · alerts {1}" -f [int]$gh.age_s, $gh.alerts_total)
        }
    } elseif (Test-Path $HbFile) {
        try {
            $hb = Get-Content $HbFile -Raw | ConvertFrom-Json
            $age = Get-HeartbeatAgeSec $hb.ts
            if ($null -ne $age -and $age -le 30) {
                Write-StatusLine -Label "Guardrail Watchdog" -Status "RUNNING" -StatusStyle Success `
                    -Detail ("PID {0} · heartbeat {1}s old" -f $hb.pid, $age)
            } else {
                Write-StatusLine -Label "Guardrail Watchdog" -Status "DOWN" -StatusStyle Error `
                    -Detail ("heartbeat {0}s old" -f $age)
            }
        } catch {
            Write-StatusLine -Label "Guardrail Watchdog" -Status "DOWN" -StatusStyle Error -Detail "unreadable heartbeat file"
        }
    } else {
        Write-StatusLine -Label "Guardrail Watchdog" -Status "DOWN" -StatusStyle Error -Detail "no heartbeat file"
    }
    Write-StatusLine -Label "Heartbeat file" -Detail "run/guardrail_watch_heartbeat.json" -DetailStyle Path
    Write-StatusLine -Label "Alerts log" -Detail "run/guardrail_alerts.log" -DetailStyle Path

    # ── 4 · SCREENER & UNIVERSE FEED (what the stack depends on) ──
    Show-ScreenerAndFeed

    # ── 5 · CHECKOUT IDENTITY (prove every launched module resolves inside THIS repo) ──
    Show-CheckoutIdentity
}

function Show-ScreenerAndFeed {
    Write-ProfileRuleWithText -Text "4 · SCREENER & UNIVERSE FEED" -Style Border
    $rerank = Join-Path $ProjectPath "scripts\rerank_loop.py"
    $ranker = Join-Path $ProjectPath "scripts\rank_markets.py"
    $strategyCfg = Join-Path $ProjectPath "strategy\config.py"
    if ((Test-Path $rerank) -and (Test-Path $ranker) -and (Test-Path $strategyCfg)) {
        Write-StatusLine -Label "Screener modules" -Status "OK" -StatusStyle Success `
            -Detail "scripts/rerank_loop.py · scripts/rank_markets.py · strategy/config.py" -DetailStyle Path
    } else {
        Write-StatusLine -Label "Screener modules" -Status "MISSING" -StatusStyle Error `
            -Detail "dashboard start-bot would spawn a phantom process"
    }
    $feed = Join-Path $ProjectPath "run\markets.json"
    if (-not (Test-Path $feed)) {
        Write-StatusLine -Label "Universe feed" -Status "MISSING" -StatusStyle Error `
            -Detail "run/markets.json · fleet idles with no universe (run the screener first)" -DetailStyle Path
        return
    }
    $ageSec = [int]((Get-Date) - (Get-Item $feed).LastWriteTime).TotalSeconds
    $count = "?"
    try { $count = @(Get-Content $feed -Raw | ConvertFrom-Json).Count } catch {}
    if ($ageSec -gt 86400) {
        Write-StatusLine -Label "Universe feed" -Status "STALE" -StatusStyle Warning `
            -Detail ("{0} market(s) · {1}s old (>24h, ranker likely down) · run/markets.json" -f $count, $ageSec) -DetailStyle Path
    } else {
        Write-StatusLine -Label "Universe feed" -Status "FRESH" -StatusStyle Success `
            -Detail ("{0} market(s) · {1}s old · run/markets.json" -f $count, $ageSec) -DetailStyle Path
    }
}

function Show-CheckoutIdentity {
    Write-ProfileRuleWithText -Text "5 · CHECKOUT IDENTITY" -Style Border
    # Single-line -c: robust against hosts whose $ErrorActionPreference='Stop'
    # turns native stderr into a terminating error, and against multiline
    # argument mangling. The repo root is inserted into sys.path EXPLICITLY:
    # the launched processes get it via -WorkingDirectory, but a python -c
    # inherits the shell's cwd, which some hosts (profile-set locations) do
    # not forward. cwd is printed so failures stay diagnosable.
    $code = "import sys; sys.path.insert(0, r'$ProjectPath'); import os, dash.live_dash as d, engine.live_fleet as f, scripts.rerank_loop as r, scripts.rank_markets as k; print('cwd=' + os.getcwd()); print(os.path.dirname(d.__file__)); print(os.path.dirname(f.__file__)); print(r.__file__); print(k.__file__)"
    $raw = @()
    try { $raw = @(& python -c $code 2>&1) } catch {}
    if ($raw.Count -lt 4) {
        # Likely EAP=Stop or a non-python shim swallowing stdout: retry quietly.
        $raw = @()
        try { $raw = @(& py -3 -c $code 2>&1) } catch {}
    }
    $paths = @($raw | Where-Object { $_ -is [string] -and $_ -match '^[A-Za-z]:[\\/]' })
    if ($paths.Count -lt 4) {
        Write-StatusLine -Label "Module resolution" -Status "FAILED" -StatusStyle Error `
            -Detail "python missing or an import failed"
        $tail = (($raw | Select-Object -Last 2 | ForEach-Object { "$_" }) -join " ")
        if ($tail) { Write-StatusLine -Label "Diagnostic" -Detail $tail -DetailStyle Warning }
        return
    }
    $labels = @("dash.live_dash", "engine.live_fleet", "scripts.rerank_loop", "scripts.rank_markets")
    for ($i = 0; $i -lt 4; $i++) {
        $p = $paths[$i].Trim()
        if ($p -like "$ProjectPath*") {
            Write-StatusLine -Label $labels[$i] -Status "OK" -StatusStyle Success -Detail $p -DetailStyle Path
        } else {
            Write-StatusLine -Label $labels[$i] -Status "OUTSIDE" -StatusStyle Error -Detail ($p + "  <-- outside this repo!") -DetailStyle Path
        }
    }
}

# ── Menu ──
function Show-MenuGrid {
    $cInfo   = Get-ProfileColor -Name Info
    $cStrong = Get-ProfileColor -Name Strong
    $cNeutral = Get-ProfileColor -Name Neutral
    Write-Host "  LIVE EXECUTION ENGINE" -ForegroundColor $cInfo
    Write-Host ""
    $items = @(
        @{ K = "1"; V = "Start Live Stack"; D = "Dashboard + screener + engine + fleet (detached)" }
        @{ K = "2"; V = "Stop Live Stack";  D = "Stop bot stack, then the dashboard" }
        @{ K = "3"; V = "Status";           D = "All 5 processes + feed + repo identity" }
        @{ K = "4"; V = "Open Dashboard";   D = "Open http://127.0.0.1:8799 in the browser" }
        @{ K = "q"; V = "Exit";             D = "Return to PowerShell" }
    )
    foreach ($it in $items) {
        Write-Host ("  [{0}] " -f $it.K) -NoNewline
        Write-Host ("{0,-27}" -f $it.V) -ForegroundColor $cStrong -NoNewline
        Write-Host $it.D -ForegroundColor $cNeutral
    }
    Write-Host ""
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
            Stop-BotStack
            $null = Stop-Dashboard
            Lsh-Ok "Live stack is down."
        }
        "3" { Show-Status }
        "4" {
            Start-Process $DashUrl
            Lsh-Ok "Opened $DashUrl in default browser."
        }
        "q" { Write-Host "Exiting Spread Hunter Live menu." -ForegroundColor (Get-ProfileColor -Name Neutral); exit 0 }
        default {
            Lsh-Warn "Invalid selection: $Key (choose 1-4, or q)."
            Start-Sleep -Seconds 1
        }
    }
}

# ── Dispatch ──
if ($Action -ne "") {
    $actionMap = @{
        "start"  = "1"
        "stop"   = "2"
        "status" = "3"
        "open"   = "4"
    }
    $key = $Action.Trim().ToLower()
    if ($actionMap.ContainsKey($key)) { $key = $actionMap[$key] }

    # Require -Yes flag for non-interactive start
    if ($key -eq "1" -and -not $Yes) {
        Write-Host "ERROR: Non-interactive start requires explicit -Yes flag" -ForegroundColor Red
        Write-Host "Usage: .\scripts\live-spread-hunter-menu.ps1 start -Yes"
        exit 1
    }

    Invoke-LiveAction $key
    exit 0
}

while ($true) {
    Lsh-Banner -Title "SPREAD HUNTER LIVE - CONTROL CENTER" `
               -Subtitle "Live execution engine only (separate from the simulation hunter-menu)"
    Show-MenuGrid
    $choice = Read-Host "  Select [1-4, q]"
    if ($null -eq $choice) { break }
    $choice = $choice.Trim()
    if ($choice -eq "") { continue }
    Invoke-LiveAction $choice
    if ($choice -ne "q") {
        Write-Host ""
        try { [void][Console]::ReadLine() } catch { [void](Read-Host "  Press [Enter] to return to the menu...") }
    }
}
