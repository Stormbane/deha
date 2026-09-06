#Requires -RunAsAdministrator

# Compact the Docker Desktop WSL2 VHDX to reclaim unused space on C:.
# Run this from an elevated PowerShell window.
#
# Procedure:
#   1. Stop running Docker containers gracefully.
#   2. Shut down the WSL2 VM (releases the lock on the VHDX file).
#   3. Use diskpart to compact the VHDX (Hyper-V module not required).
#   4. Report before/after size.
#   5. Restart Docker Desktop so containers with restart=unless-stopped come back.

$ErrorActionPreference = 'Stop'
$vhdx = "$env:LOCALAPPDATA\Docker\wsl\disk\docker_data.vhdx"

if (-not (Test-Path $vhdx)) {
    Write-Error "VHDX not found at $vhdx"
    exit 1
}

$before = (Get-Item $vhdx).Length
Write-Host ("Before: {0:N2} GB" -f ($before / 1GB)) -ForegroundColor Cyan

Write-Host "Stopping running Docker containers..." -ForegroundColor Yellow
$running = & docker ps -q 2>$null
if ($running) { & docker stop $running | Out-Null }

Write-Host "Shutting down WSL2 (releases VHDX lock)..." -ForegroundColor Yellow
wsl --shutdown
Start-Sleep -Seconds 4

Write-Host "Running diskpart compact..." -ForegroundColor Yellow
$script = @"
select vdisk file="$vhdx"
attach vdisk readonly
compact vdisk
detach vdisk
exit
"@
$tmp = [System.IO.Path]::GetTempFileName()
Set-Content -Path $tmp -Value $script -Encoding ASCII
try {
    & diskpart /s $tmp
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

$after = (Get-Item $vhdx).Length
$reclaimed = ($before - $after) / 1GB
Write-Host ""
Write-Host ("Before:    {0:N2} GB" -f ($before / 1GB)) -ForegroundColor Cyan
Write-Host ("After:     {0:N2} GB" -f ($after / 1GB)) -ForegroundColor Cyan
Write-Host ("Reclaimed: {0:N2} GB" -f $reclaimed) -ForegroundColor Green

Write-Host ""
Write-Host "Restarting Docker Desktop..." -ForegroundColor Yellow
$dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dockerExe) {
    Start-Process $dockerExe
    Write-Host "Docker Desktop launching. Containers with restart=unless-stopped"
    Write-Host "(homeassistant, wyoming-whisper) will resume automatically."
    Write-Host "microwakeword-trainer has restart=no - start it manually:"
    Write-Host "  docker start microwakeword-trainer"
} else {
    Write-Warning "Docker Desktop not at default path. Launch it manually."
}
