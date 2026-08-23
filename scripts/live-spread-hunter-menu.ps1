# Backward-compatible forwarder for scripts/spread-hunter-menu.ps1
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments)]
    [string[]]$Remaining
)

$TargetScript = Join-Path $PSScriptRoot "spread-hunter-menu.ps1"
if (Test-Path $TargetScript) {
    & $TargetScript @Remaining
} else {
    Write-Error "Target script not found: $TargetScript"
}
