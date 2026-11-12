<#
    NovaPanel.ps1 -- shared helpers. Dot-source this; it defines:

        Show-NovaPanel      open the HUD, or bring it to the front if already open
        Get-NovaUrl         the bridge's tokenised panel URL
        Get-NovaToken       the shared secret the bridge expects
        Send-NovaWake       tell an open panel to start recording now
        Write-NovaLog       append to ~/.nova/<name>.log

    Not meant to be run directly -- use Nova.ps1 for that.
#>

$script:NovaRoot = Join-Path $env:USERPROFILE '.nova'

function Write-NovaLog {
    param([string]$Message, [string]$Name = 'nova')
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    try {
        if (-not (Test-Path $script:NovaRoot)) {
            New-Item -ItemType Directory -Path $script:NovaRoot -Force | Out-Null
        }
        Add-Content -Path (Join-Path $script:NovaRoot "$Name.log") -Value $line -Encoding utf8
    } catch { }
}

function Get-NovaUrl {
    $f = Join-Path $script:NovaRoot 'url.txt'
    if (Test-Path $f) { return (Get-Content $f -Raw).Trim() }
    return $null
}

function Get-NovaToken {
    $f = Join-Path $script:NovaRoot 'token'
    if (Test-Path $f) { return (Get-Content $f -Raw).Trim() }
    return $null
}

function Get-NovaPort {
    $url = Get-NovaUrl
    if ($url -and ($url -match ':(\d+)/')) { return [int]$Matches[1] }
    return 8765
}

function Send-NovaWake {
    <#
        Drop a wake flag on the bridge, optionally carrying the request that
        was just transcribed. The panel claims it on its next poll: with an
        utterance it dispatches straight to Claude, without one it falls back
        to arming its own microphone.
    #>
    param(
        [string]$Phrase = 'nova',
        [string]$Utterance = ''
    )

    $token = Get-NovaToken
    if (-not $token) { return $false }
    $port = Get-NovaPort
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/wake" `
            -Headers @{ 'X-Nova-Token' = $token } `
            -ContentType 'application/json' `
            -Body (@{ phrase = $Phrase; utterance = $Utterance } | ConvertTo-Json -Compress) `
            -TimeoutSec 5 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Get-NovaTranscript {
    <#
        Send a WAV to the bridge and get Whisper's transcript back.

        This is the half that makes recording through Windows worthwhile:
        Windows captures reliably, Whisper is the accurate part.
    #>
    param([Parameter(Mandatory = $true)][string]$WavPath)

    if (-not (Test-Path $WavPath)) { return $null }
    $token = Get-NovaToken
    if (-not $token) { return $null }
    $port = Get-NovaPort

    try {
        $bytes = [System.IO.File]::ReadAllBytes($WavPath)
        $r = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/transcribe" `
            -Headers @{ 'X-Nova-Token' = $token } `
            -ContentType 'audio/wav' -Body $bytes -TimeoutSec 120
    } catch {
        Write-NovaLog "transcription request failed: $($_.Exception.Message)" 'wake'
        return $null
    }

    if ($r.error) {
        Write-NovaLog "whisper error: $($r.error) $($r.detail)" 'wake'
        return $null
    }
    $text = ($r.text | Out-String).Trim()
    if (-not $text) { return $null }
    Write-NovaLog ("whisper ({0} ms): '{1}'" -f $r.ms, $text) 'wake'
    return $text
}

function Test-NovaListen {
    <#
        Has the panel finished speaking and asked for a follow-up? Claiming is
        destructive on the bridge, so this returns true at most once per reply.
    #>
    $token = Get-NovaToken
    if (-not $token) { return $false }
    $port = Get-NovaPort
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/listen" `
            -Headers @{ 'X-Nova-Token' = $token } -TimeoutSec 3
        return [bool]$r.pending
    } catch {
        return $false
    }
}

function Find-NovaBrowser {
    # Chromium-family only: the HUD needs webkitSpeechRecognition, and --app
    # gives a chromeless window that reads as a HUD rather than a web page.
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

if (-not ('NovaWin32' -as [type])) {
    Add-Type -Name 'NovaWin32' -Namespace '' -MemberDefinition @'
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
'@
}

function Find-NovaWindow {
    <#
        Track the panel by window title, not by the PID we launched.

        Chrome's launcher process forwards the command to the browser process
        for that profile and then exits immediately, so the PID returned by
        Start-Process is dead within seconds. Tracking it meant every wake
        opened a brand-new window instead of raising the one already there.
        The --app window carries the page's <title>, which is "NOVA".
    #>
    Get-Process -Name chrome, msedge -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle -eq 'NOVA' } |
        Select-Object -First 1
}

if (-not ('NovaWinMove' -as [type])) {
    Add-Type -Name 'NovaWinMove' -Namespace '' -MemberDefinition @'
        [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int x, int y, int w, int h, bool repaint);
'@
}

function Move-NovaPanel {
    <#
        Put the panel on a given monitor: 'secondary' (the one that is not the
        main screen, else the main one) or 'primary'. -Maximize fills it.
    #>
    param([Parameter(Mandatory = $true)]$Window, [string]$Screen = 'secondary', [switch]$Maximize)
    Add-Type -AssemblyName System.Windows.Forms
    $all = [System.Windows.Forms.Screen]::AllScreens
    $target = if ($Screen -eq 'primary') { $all | Where-Object Primary | Select-Object -First 1 }
              else { $all | Where-Object { -not $_.Primary } | Select-Object -First 1 }
    if (-not $target) { $target = [System.Windows.Forms.Screen]::PrimaryScreen }
    $a = $target.WorkingArea
    $h = $Window.MainWindowHandle
    [void][NovaWin32]::ShowWindow($h, 9)                         # SW_RESTORE first, or it will not move
    [void][NovaWinMove]::MoveWindow($h, $a.X + 40, $a.Y + 40, [Math]::Min(1280, $a.Width - 80), [Math]::Min(880, $a.Height - 80), $true)
    if ($Maximize) { [void][NovaWin32]::ShowWindow($h, 3) }      # SW_MAXIMIZE on that monitor
    [void][NovaWin32]::SetForegroundWindow($h)
}

function Show-NovaPanel {
    param(
        [int]$Width  = 1280,
        [int]$Height = 880,
        [string]$Screen = '',
        [switch]$Maximize,
        [switch]$Quiet
    )

    $pidFile = Join-Path $script:NovaRoot 'panel.pid'

    # Already open? Raise it and we're done -- no second window.
    $open = Find-NovaWindow
    if ($open) {
        $h = $open.MainWindowHandle
        if ([NovaWin32]::IsIconic($h)) { [void][NovaWin32]::ShowWindow($h, 9) }  # SW_RESTORE
        [void][NovaWin32]::SetForegroundWindow($h)
        if ($Screen) { Move-NovaPanel -Window $open -Screen $Screen -Maximize:$Maximize }
        Set-Content -Path $pidFile -Value $open.Id -Encoding utf8
        if (-not $Quiet) { Write-NovaLog "panel raised (pid $($open.Id))" }
        return $true
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue

    $url = Get-NovaUrl
    if (-not $url) {
        Write-NovaLog 'cannot open panel: ~/.nova/url.txt missing -- is the bridge running?'
        return $false
    }

    $browser = Find-NovaBrowser
    if (-not $browser) {
        Write-NovaLog 'cannot open panel: no Chrome or Edge found'
        return $false
    }

    # A separate profile keeps the HUD's microphone grant and window geometry
    # away from your day-to-day browsing profile.
    $profileDir = Join-Path $script:NovaRoot 'browser-profile'

    # --app mode has no address bar, so there is no padlock to click and no
    # ordinary way to allow the microphone. Write the grant into this profile
    # before launching -- scoped to the bridge origin only. Safe to repeat, and
    # only ever done here, in the branch where no panel is running.
    $granter = Join-Path $script:NovaRoot 'grant_mic.py'
    if (Test-Path $granter) {
        $py = Get-Command python -ErrorAction SilentlyContinue
        if ($py) {
            $origin = $url -replace '^(https?://[^/]+).*$', '$1'
            & $py.Source "$granter" --origin $origin 2>&1 | Out-Null
        }
    }

    # Every value containing a path is quoted: Start-Process joins ArgumentList
    # with spaces and quotes nothing, so "C:\Users\You\..." would otherwise
    # arrive as two separate arguments.
    $argList = @(
        "--app=$url",
        "--user-data-dir=`"$profileDir`"",
        "--window-size=$Width,$Height",
        '--new-window',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=TranslateUI'
        # No secure-origin override needed: 127.0.0.1 is a potentially
        # trustworthy origin by spec, so getUserMedia and speech recognition
        # work over plain http here.
    )

    try {
        [void](Start-Process -FilePath $browser -ArgumentList $argList -PassThru)
    } catch {
        Write-NovaLog "failed to launch panel: $($_.Exception.Message)"
        return $false
    }

    # Wait for the real browser window to appear, then record THAT pid -- the
    # launcher's own pid is gone almost immediately.
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        $win = Find-NovaWindow
        if ($win) {
            if ($Screen) { Move-NovaPanel -Window $win -Screen $Screen -Maximize:$Maximize }
            Set-Content -Path $pidFile -Value $win.Id -Encoding utf8
            if (-not $Quiet) { Write-NovaLog "panel opened (pid $($win.Id)) via $(Split-Path $browser -Leaf)" }
            return $true
        }
    }

    Write-NovaLog 'panel launched but no NOVA window appeared within 10s'
    return $false
}
