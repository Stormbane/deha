# Register the Narada supervisor as a Task Scheduler task that runs at user logon.
#
# Usage:
#   .\install_narada_supervisor_task.ps1            # install / update
#   .\install_narada_supervisor_task.ps1 -Uninstall # remove
#   .\install_narada_supervisor_task.ps1 -RunNow    # install and start immediately
#
# The task runs as the current user, only when that user is logged on,
# with the project's .venv python invoking the supervisor module. stdout
# goes to the supervisor's rotating log file (see supervisor.py).

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$RunNow,
    [string]$Voice = "am_michael:0.5,af_heart:0.5",
    [string]$Model = "sonnet"
)

$SupervisorTask = "NaradaSupervisor"
$LogTailTask    = "NaradaLogTail"
$ProjectDir     = (Resolve-Path "$PSScriptRoot\..").Path
$Python         = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$LogPath        = Join-Path $env:LOCALAPPDATA "narada\logs\supervisor.log"

if ($Uninstall) {
    foreach ($name in @($SupervisorTask, $LogTailTask)) {
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Output "Removed task $name."
        } else {
            Write-Output "Task $name not registered."
        }
    }
    return
}

if (-not (Test-Path $Python)) {
    Write-Error "Python not found at $Python. Run pip install -e .[voice] in the project's .venv first."
    exit 1
}

# Make sure the log directory exists so the tail task can attach
# even before the supervisor has written its first line.
$LogDir = Split-Path $LogPath -Parent
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
if (-not (Test-Path $LogPath)) {
    New-Item -ItemType File -Path $LogPath -Force | Out-Null
}

# Shared trigger + principal — both tasks fire at user logon, run as
# the interactive user (so their windows appear on the desktop).
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

# --- 1. Supervisor task ---
$supervisorAction = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m deha.voice.supervisor --voice $Voice --model $Model" `
    -WorkingDirectory $ProjectDir

$supervisorTaskObj = New-ScheduledTask `
    -Action $supervisorAction `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Narada voice supervisor. Brings up HA + brain_server at logon."

# --- 2. Log-tail terminal ---
# A persistent visible PowerShell window that tails the supervisor log.
# Survives across supervisor restarts, where the supervisor's own stdout
# window does not. Lives on top of (not instead of) the supervisor's
# stdout window — they show similar content but the file tail is the
# durable observation surface.
$tailScript = @"
`$Host.UI.RawUI.WindowTitle = 'Narada — supervisor log'
Write-Host 'tailing $LogPath' -ForegroundColor DarkGray
Get-Content -Path '$LogPath' -Tail 200 -Wait
"@
# -NoExit keeps the window around if the tail crashes; -Command runs it.
$tailAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -Command `"$tailScript`""

$tailTaskObj = New-ScheduledTask `
    -Action $tailAction `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Narada log tail. Persistent live view of the supervisor log."

# --- Register both, idempotent ---
# Stop any running task instance before unregistering — Unregister alone
# leaves the running process orphaned, which then collides with the
# fresh launch on port 9999.
foreach ($pair in @(
    @{ Name = $SupervisorTask; Task = $supervisorTaskObj },
    @{ Name = $LogTailTask;    Task = $tailTaskObj }
)) {
    $existing = Get-ScheduledTask -TaskName $pair.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-ScheduledTask -TaskName $pair.Name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $pair.Name -Confirm:$false
    }
    Register-ScheduledTask -TaskName $pair.Name -InputObject $pair.Task | Out-Null
    Write-Output "Registered task $($pair.Name)."
}

if ($RunNow) {
    Start-ScheduledTask -TaskName $SupervisorTask
    Start-ScheduledTask -TaskName $LogTailTask
    Write-Output "Started $SupervisorTask and $LogTailTask."
}

Write-Output ""
Write-Output "Useful commands:"
Write-Output "  Get-ScheduledTask -TaskName $SupervisorTask, $LogTailTask"
Write-Output "  Start-ScheduledTask -TaskName $SupervisorTask"
Write-Output "  Stop-ScheduledTask  -TaskName $SupervisorTask"
Write-Output "  curl http://127.0.0.1:9999/status"
