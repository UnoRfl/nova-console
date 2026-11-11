<#
    Nova.ps1 -- one place to run, inspect and install NOVA.

        .\Nova.ps1 status      what is running right now
        .\Nova.ps1 start       start the bridge and the wake listener
        .\Nova.ps1 stop        stop both
        .\Nova.ps1 restart     stop, then start
        .\Nova.ps1 model opus  switch which Claude answers (haiku|sonnet|opus)
        .\Nova.ps1 open        open the panel now, without saying anything
        .\Nova.ps1 wake        simulate the wake word (opens and arms the mic)
        .\Nova.ps1 test        12-second microphone check for the wake word
        .\Nova.ps1 logs        tail the bridge and wake logs
        .\Nova.ps1 login       sign the Claude CLI in (needed once)
        .\Nova.ps1 install     start automatically at every login
        .\Nova.ps1 uninstall   remove the login tasks

    The two moving parts:
      nova_bridge.py   localhost web server + relay to the claude CLI
      nova-wake.ps1    offline "Nova" listener that opens and arms the panel
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('status','start','stop','restart','open','wake','mic','audio','test','dictate','logs','login','install','uninstall','model','orb')]
    [string]$Command = 'status',

    # Second positional, used only by 'model' so far: haiku | sonnet | opus.
    [Parameter(Position = 1)]
    [string]$Arg = '',

    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'NovaPanel.ps1')

$Root       = $PSScriptRoot
$ManagerPs1 = Join-Path $Root 'Nova.ps1'
$BridgePy   = Join-Path $Root 'nova_bridge.py'
$WakePs1    = Join-Path $Root 'nova-wake.ps1'
$WatchPs1   = Join-Path $Root 'nova-watch.ps1'
$BridgePid  = Join-Path $Root 'bridge.pid'
$WakePid    = Join-Path $Root 'wake.pid'
$WatchPid   = Join-Path $Root 'watch.pid'
$TaskBridge = 'Nova-Bridge'
$TaskWake   = 'Nova-Wake'

function Get-Python {
    # pythonw runs without a console window, which is what we want for a daemon.
    # Nova's own environment first: that is where its packages live.
    foreach ($n in @('pythonw.exe','python.exe')) {
        $v = Join-Path $PSScriptRoot "venv\Scripts\$n"
        if (Test-Path $v) { return $v }
    }
    foreach ($n in @('pythonw','python')) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return $null
}

function Get-TrackedProcess {
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) { return $null }
    $id = 0
    if (-not [int]::TryParse((Get-Content $PidFile -Raw).Trim(), [ref]$id)) { return $null }
    return Get-Process -Id $id -ErrorAction SilentlyContinue
}

function Test-Port {
    param([int]$P)
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $P)
        $c.Close()
        return $true
    } catch { return $false }
}

function Stop-Tracked {
    param([string]$PidFile, [string]$Label)
    $proc = Get-TrackedProcess $PidFile
    if ($proc) {
        try { Stop-Process -Id $proc.Id -Force; Write-Host "stopped  $Label (pid $($proc.Id))" }
        catch { Write-Host "could not stop $Label : $($_.Exception.Message)" }
    } else {
        Write-Host "not running  $Label"
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

function Find-ClaudeExe {
    # The desktop app keeps versioned copies under its MSIX package folder, so
    # resolve the newest rather than pinning a version that will go stale.
    $onPath = Get-Command claude -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    $glob = Join-Path $env:LOCALAPPDATA 'Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\*\claude.exe'
    $found = Get-ChildItem -Path $glob -ErrorAction SilentlyContinue |
        Sort-Object { [version]($_.Directory.Name -replace '[^\d.]', '') } -Descending |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

# ------------------------------------------------------------------ commands

function Invoke-Status {
    Write-Host ''
    Write-Host 'N O V A' -ForegroundColor Magenta
    Write-Host '-------'

    $bridge = Get-TrackedProcess $BridgePid
    $portUp = Test-Port $Port
    if ($bridge)      { Write-Host "  bridge      running (pid $($bridge.Id))" }
    elseif ($portUp)  { Write-Host "  bridge      running (started outside this script)" }
    else              { Write-Host "  bridge      stopped" }
    Write-Host "  port $Port   $(if ($portUp) { 'listening' } else { 'closed' })"

    $wake = Get-TrackedProcess $WakePid
    Write-Host "  wake word   $(if ($wake) { "listening (pid $($wake.Id))" } else { 'stopped' })"

    $watch = Get-TrackedProcess $WatchPid
    Write-Host "  watchdog    $(if ($watch) { "supervising (pid $($watch.Id))" } else { 'stopped' })"

    $url = Get-NovaUrl
    Write-Host "  panel url   $(if ($url) { $url } else { '(bridge has not written one yet)' })"

    if ($portUp) {
        try {
            $tok = Get-NovaToken
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -Headers @{ 'X-Nova-Token' = $tok } -TimeoutSec 5
            Write-Host "  claude      $($h.status)  [$($h.model)]"
            Write-Host "  power       $($h.power)$(if ($h.power -eq 'full') { '   (everything available; risky actions ask first)' } else { '   (read-only)' })"
            if ($h.status -ne 'ready' -and $h.detail) {
                Write-Host ''
                Write-Host "  $($h.detail)" -ForegroundColor Yellow
            }
        } catch { Write-Host "  claude      (health check failed)" -ForegroundColor Yellow }
    }

    Write-Host ''
    $vbs = Join-Path ([Environment]::GetFolderPath('Startup')) 'Nova.vbs'
    if (Test-Path $vbs) {
        Write-Host '  autostart   ON  (starts at every login)' -ForegroundColor Green
    } else {
        Write-Host '  autostart   off -- run  .\Nova.ps1 install  to make it permanent'
    }
    Write-Host ''
}

function Invoke-Start {
    $py = Get-Python
    if (-not $py) { Write-Host 'python is not on PATH.' -ForegroundColor Red; return }

    if (Test-Port $Port) {
        Write-Host "bridge already listening on $Port"
    } else {
        # Every path argument is quoted individually: Start-Process joins
        # ArgumentList with spaces and quotes nothing, so an unquoted
        # "C:\Users\You\..." arrives as two separate arguments.
        $p = Start-Process -FilePath $py -ArgumentList @("`"$BridgePy`"", '--port', "$Port") `
                           -WorkingDirectory $Root -WindowStyle Hidden -PassThru
        Set-Content -Path $BridgePid -Value $p.Id -Encoding utf8
        Write-Host "started  bridge (pid $($p.Id)) on 127.0.0.1:$Port"

        for ($i = 0; $i -lt 30; $i++) {
            if (Test-Port $Port) { break }
            Start-Sleep -Milliseconds 200
        }
        if (-not (Test-Port $Port)) {
            Write-Host 'bridge did not come up -- check .nova\bridge.log' -ForegroundColor Yellow
        }
    }

    if (Get-TrackedProcess $WakePid) {
        Write-Host 'wake listener already running'
    } else {
        # Mark III ears: VAD + voice match + Whisper, in Nova's own Python.
        # The old Windows-recogniser listener (nova-wake.ps1) is kept on disk
        # as a fallback only if the new one is missing.
        $EarsPy = Join-Path $Root 'nova_ears.py'
        if ((Test-Path $EarsPy) -and ($py -like '*\venv\*')) {
            $p = Start-Process -FilePath $py -ArgumentList @("`"$EarsPy`"") `
                -WorkingDirectory $Root -WindowStyle Hidden -PassThru
        } else {
            $p = Start-Process -FilePath 'powershell.exe' `
                -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$WakePs1`"") `
                -WorkingDirectory $Root -WindowStyle Hidden -PassThru
        }
        Set-Content -Path $WakePid -Value $p.Id -Encoding utf8
        Write-Host "started  wake listener (pid $($p.Id)) -- say `"Hey Nova`""
    }

    # The orb: her presence on screen, so the full panel need not be open.
    $OrbPy  = Join-Path $Root 'nova_orb.py'
    $OrbPid = Join-Path $Root 'orb.pid'
    if ((Test-Path $OrbPy) -and ($py -like '*\venv\*') -and -not (Get-TrackedProcess $OrbPid)) {
        $p = Start-Process -FilePath $py -ArgumentList @("`"$OrbPy`"") -WorkingDirectory $Root -PassThru
        Set-Content -Path $OrbPid -Value $p.Id -Encoding utf8
        Write-Host "started  orb (pid $($p.Id))"
    }

    # The watchdog goes up last, once there is something to watch. It calls
    # this same function when it finds a piece missing, so starting it before
    # them would only race the things it is about to launch.
    if (Get-TrackedProcess $WatchPid) {
        Write-Host 'watchdog already running'
    } else {
        $p = Start-Process -FilePath 'powershell.exe' `
            -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$WatchPs1`"") `
            -WorkingDirectory $Root -WindowStyle Hidden -PassThru
        Set-Content -Path $WatchPid -Value $p.Id -Encoding utf8
        Write-Host "started  watchdog (pid $($p.Id)) -- keeps NOVA up while this PC is on"
    }
}

function Invoke-Stop {
    # Order matters: the watchdog exists to restart these two, so stopping
    # it last would mean it revived them while the stop was still running.
    Stop-Tracked $WatchPid  'watchdog'
    Stop-Tracked $WakePid   'wake listener'
    Stop-Tracked $BridgePid 'bridge'
    Stop-Tracked (Join-Path $Root 'orb.pid') 'orb'
    $panel = Join-Path $Root 'panel.pid'
    if (Test-Path $panel) { Remove-Item $panel -Force -ErrorAction SilentlyContinue }
}

function Invoke-Model {
    param([string]$Want)

    # Short names, because this is meant to be said out loud as much as typed.
    $map = @{
        'auto'   = 'auto'
        'haiku'  = 'haiku'
        'fast'   = 'haiku'
        'sonnet' = 'sonnet'
        'opus'   = 'opus'
        'deep'   = 'opus'
    }
    $key = $Want.Trim().ToLower()
    if (-not $key) { Write-Host 'Which model? auto, haiku, sonnet or opus.' -ForegroundColor Yellow; return }
    if (-not $map.ContainsKey($key)) {
        Write-Host "Unknown model '$Want'. Use auto, haiku, sonnet or opus." -ForegroundColor Red
        return
    }
    if (-not (Test-Port $Port)) { Write-Host 'The bridge is not running.' -ForegroundColor Red; return }

    try {
        $tok = Get-NovaToken
        $body = @{ model = $map[$key] } | ConvertTo-Json -Compress
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/model" -Method Post `
             -Headers @{ 'X-Nova-Token' = $tok } -ContentType 'application/json' `
             -Body $body -TimeoutSec 20
        Write-Host "model is now $($r.model)" -ForegroundColor Green
    } catch {
        Write-Host "Could not switch model: $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Invoke-Login {
    $exe = Find-ClaudeExe
    if (-not $exe) { Write-Host 'Could not find claude.exe on this machine.' -ForegroundColor Red; return }
    Write-Host ''
    Write-Host 'Opening the Claude CLI so you can sign in.' -ForegroundColor Magenta
    Write-Host "  $exe" -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  1. In the window that opens, type  /login  and press Enter.'
    Write-Host '  2. Finish the sign-in in your browser.'
    Write-Host '  3. Close that window, then run:  .\Nova.ps1 restart'
    Write-Host ''
    # A real console of its own: the login flow is interactive and cannot run
    # inside a redirected, non-interactive shell.
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', "`"$exe`"") -WorkingDirectory $env:USERPROFILE
}

function Invoke-Install {
    <#
        Autostart via the Startup folder rather than Task Scheduler.

        Register-ScheduledTask goes through a CIM provider, and this machine's
        WMI repository is broken, so it fails with "Access is denied" even for
        a plain user-level task. The Startup folder needs no admin, no WMI and
        no elevation, and a .vbs launcher starts PowerShell with window style 0
        so nothing flashes on screen at login.
    #>
    $startupDir = [Environment]::GetFolderPath('Startup')
    if (-not $startupDir -or -not (Test-Path $startupDir)) {
        Write-Host 'Could not locate your Startup folder.' -ForegroundColor Red
        return
    }
    $vbs = Join-Path $startupDir 'Nova.vbs'

    $launcher = @"
' Starts NOVA at login with no visible console window.
' Remove this file (or run: Nova.ps1 uninstall) to stop it.
Dim shell
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""$ManagerPs1"" start", 0, False
"@

    try {
        Set-Content -Path $vbs -Value $launcher -Encoding ascii -Force
    } catch {
        Write-Host "Could not write the startup launcher: $($_.Exception.Message)" -ForegroundColor Red
        return
    }

    # Verify rather than assume -- the previous version reported success even
    # when registration had actually failed.
    if (Test-Path $vbs) {
        Write-Host "installed  $vbs" -ForegroundColor Green
        Write-Host ''
        Write-Host 'NOVA now starts automatically every time you log in.'
        Write-Host 'Nothing else to do -- say "Nova" whenever you want it.'
    } else {
        Write-Host 'Install failed: the launcher was not created.' -ForegroundColor Red
    }
}

function Invoke-Uninstall {
    $vbs = Join-Path ([Environment]::GetFolderPath('Startup')) 'Nova.vbs'
    if (Test-Path $vbs) {
        Remove-Item $vbs -Force
        Write-Host "removed  $vbs"
    } else {
        Write-Host "absent   $vbs"
    }
    # Clean up scheduled tasks from the older install path, if any took.
    foreach ($t in @($TaskBridge, $TaskWake)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Host "removed  scheduled task $t"
        }
    }
}

function Invoke-Logs {
    foreach ($f in @('bridge.log','wake.log','nova.log')) {
        $p = Join-Path $Root $f
        if (Test-Path $p) {
            Write-Host ''
            Write-Host "--- $f (last 15) ---" -ForegroundColor Magenta
            Get-Content $p -Tail 15
        }
    }
    Write-Host ''
}

switch ($Command) {
    'status'    { Invoke-Status }
    'start'     { Invoke-Start;  Invoke-Status }
    'stop'      { Invoke-Stop }
    'restart'   { Invoke-Stop; Start-Sleep -Milliseconds 700; Invoke-Start; Invoke-Status }
    'model'     { Invoke-Model $Arg }
    'orb'       {
                  # The orb re-reads orb.json while it runs, so this takes
                  # effect within a second and needs no restart.
                  #   hide    fade out; she comes back whenever she is called
                  #   show    stay on screen
                  #   toggle  flip between those two
                  #   lock / unlock   whether it can be dragged
                  $file = Join-Path $Root 'orb.json'
                  $o = @{}
                  if (Test-Path $file) {
                      try { (Get-Content $file -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $o[$_.Name] = $_.Value } } catch { }
                  }
                  switch ($Arg) {
                      'hide'   { $o['hidden'] = $true }
                      'show'   { $o['hidden'] = $false }
                      'toggle' { $o['hidden'] = -not [bool]$o['hidden'] }
                      'lock'   { $o['locked'] = $true }
                      'unlock' { $o['locked'] = $false }
                      default  { Write-Host 'usage: .\Nova.ps1 orb hide | show | toggle | lock | unlock' }
                  }
                  # WriteAllText, not Set-Content: PowerShell 5.1 would add a BOM.
                  [IO.File]::WriteAllText($file, ($o | ConvertTo-Json -Compress))
                  Write-Host ("orb: locked={0} auto-hide={1}" -f [bool]$o['locked'], [bool]$o['hidden'])
                }
    'open'      { if (Show-NovaPanel) { Write-Host 'panel opened' } }
    'wake'      { [void](Send-NovaWake -Phrase 'nova'); [void](Show-NovaPanel); Write-Host 'wake signalled -- the panel should chime and start recording' }
    'mic'       {
                  # Chrome holds Preferences in memory and flushes on exit, so
                  # the panel has to be closed before the grant is written or
                  # it is silently undone.
                  $win = Find-NovaWindow
                  if ($win) {
                      Stop-Process -Id $win.Id -Force
                      Write-Host "closed the panel (pid $($win.Id)) so the grant sticks"
                      Start-Sleep -Seconds 2
                  }
                  Remove-Item (Join-Path $Root 'panel.pid') -Force -EA SilentlyContinue
                  $py = (Get-Command python).Source
                  & $py (Join-Path $Root 'grant_mic.py') --origin "http://127.0.0.1:$Port"
                  & $py (Join-Path $Root 'grant_mic.py') --origin "http://127.0.0.1:$Port" --show
                  Write-Host ''
                  Write-Host 'Reopen with:  .\Nova.ps1 open'
                }
    'audio'     {
                  # Windows keeps three separate input defaults and different
                  # software asks for different ones; when they disagree you get
                  # a moving level meter and no transcript.
                  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'AudioDefaults.ps1') -AlignRoles
                }
    'test'      { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$WakePs1" -TestOnce }
    'dictate'   {
                  # The live listener owns the microphone, so stand it down for
                  # the duration or the test gets no audio.
                  $wasRunning = [bool](Get-TrackedProcess $WakePid)
                  if ($wasRunning) { Stop-Tracked $WakePid 'wake listener'; Start-Sleep -Seconds 1 }
                  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$WakePs1" -DictateOnce
                  if ($wasRunning) { Invoke-Start | Out-Null; Write-Host 'wake listener restarted' }
                }
    'logs'      { Invoke-Logs }
    'login'     { Invoke-Login }
    'install'   { Invoke-Install }
    'uninstall' { Invoke-Uninstall }
}
