<#
    nova-watch.ps1 -- keeps NOVA up for as long as the PC is on.

    The wake listener died overnight without ever reaching its own shutdown
    path, so something killed it outright rather than it stopping cleanly.
    Nothing was going to bring it back before the next login. This does.

    Task Scheduler is not available here: this machine's WMI repository is
    broken, so Register-ScheduledTask fails even for a plain user-level task
    (the same fault that makes Get-CimInstance Win32_Process throw). So the
    watchdog is an ordinary hidden process, started by `Nova.ps1 start` --
    which is already exactly what the Startup launcher runs at login.

    It supervises rather than duplicates: every check just calls
    `Nova.ps1 start`, which is idempotent and starts only what is missing.
    That keeps one code path for starting NOVA, and means this file cannot
    drift away from how the pieces are really launched.
#>
param(
    [int]$Port = 8765,
    [int]$EverySeconds = 30
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'NovaPanel.ps1')

$Root       = $PSScriptRoot
$ManagerPs1 = Join-Path $Root 'Nova.ps1'
$BridgePid  = Join-Path $Root 'bridge.pid'
$WakePid    = Join-Path $Root 'wake.pid'
$WatchPid   = Join-Path $Root 'watch.pid'

function Get-Tracked {
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) { return $null }
    $id = 0
    if (-not [int]::TryParse((Get-Content $PidFile -Raw).Trim(), [ref]$id)) { return $null }
    return Get-Process -Id $id -ErrorAction SilentlyContinue
}

function Test-BridgePort {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $Port)
        $c.Close()
        return $true
    } catch { return $false }
}

# One watchdog, ever. Two would fight each other over the same pid files.
$existing = Get-Tracked $WatchPid
if ($existing -and $existing.Id -ne $PID) {
    Write-NovaLog "watchdog already running (pid $($existing.Id)) -- this one is standing down" 'watch'
    return
}
Set-Content -Path $WatchPid -Value $PID -Encoding utf8

Write-NovaLog "watchdog up (pid $PID) -- checking every $EverySeconds seconds" 'watch'
try { Set-Content -Path (Join-Path $Root 'watch.beat') -Value (Get-Date -Format 'o') -Encoding ascii } catch { }

# A service that crashes on startup should not be respawned forever at full
# speed. After a few restarts close together, back off and say so, so the log
# shows a genuine fault instead of ten thousand identical revival lines.
$recent   = New-Object System.Collections.ArrayList
$backoff  = 0

try {
    while ($true) {
        try {
            $missing = @()
            if (-not (Test-BridgePort))      { $missing += 'bridge' }
            if (-not (Get-Tracked $WakePid)) { $missing += 'wake listener' }

            if ($missing.Count -gt 0 -and $backoff -le 0) {
                Write-NovaLog ("down: " + ($missing -join ' and ') + " -- restarting") 'watch'

                & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
                    -File $ManagerPs1 start *>$null

                Start-Sleep -Seconds 4
                $still = @()
                if (-not (Test-BridgePort))      { $still += 'bridge' }
                if (-not (Get-Tracked $WakePid)) { $still += 'wake listener' }
                if ($still.Count -gt 0) {
                    Write-NovaLog ("restart did not take: " + ($still -join ' and ') + " still down") 'watch'
                } else {
                    Write-NovaLog ("back up: " + ($missing -join ' and ')) 'watch'
                }

                # Keep only the restarts from the last ten minutes.
                [void]$recent.Add((Get-Date))
                $cutoff = (Get-Date).AddMinutes(-10)
                while ($recent.Count -gt 0 -and $recent[0] -lt $cutoff) { $recent.RemoveAt(0) }

                if ($recent.Count -ge 5) {
                    $backoff = [int](300 / $EverySeconds)     # sit out five minutes
                    Write-NovaLog "five restarts in ten minutes -- backing off for five minutes; something is genuinely broken" 'watch'
                    $recent.Clear()
                }
            } elseif ($backoff -gt 0) {
                $backoff--
            }
        } catch {
            # A fault in one check must never take the watchdog down with it,
            # or the thing meant to survive everything becomes the weak link.
            try { Write-NovaLog "watchdog check failed: $($_.Exception.Message)" 'watch' } catch { }
        }

        # A heartbeat the bridge can see. It catches a watchdog that has hung
        # as well as one that has died, which a pid check alone cannot.
        try { Set-Content -Path (Join-Path $Root 'watch.beat') -Value (Get-Date -Format 'o') -Encoding ascii } catch { }

        Start-Sleep -Seconds $EverySeconds
    }
} finally {
    Write-NovaLog "watchdog stopped (pid $PID)" 'watch'
    Remove-Item $WatchPid -Force -ErrorAction SilentlyContinue
}
