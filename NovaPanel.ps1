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

if (-not ('NovaWinZoom' -as [type])) {
    Add-Type -Name 'NovaWinZoom' -Namespace '' -MemberDefinition @'
        [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
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

<#
    The panel as its own app on the taskbar (Sir, 2026-11-20: "pin it like a real app").

    Chrome gives an --app window its own AppUserModelID but no relaunch command,
    so Windows pinned chrome.exe. We stamp the window with Nova's own ID, a
    relaunch command (NovaOpen.vbs: raise / start-if-down / refuse-if-OFF) and
    the Nova icon, and keep a Start Menu shortcut carrying the same ID -- that
    shortcut is what the taskbar pins and takes its icon from. Chrome clears
    these properties itself when the window closes.
#>
$script:NovaAppId = 'Uno.Nova.Panel'

if (-not ('NovaAppIdentity' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class NovaAppIdentity {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PROPERTYKEY { public Guid fmtid; public uint pid; }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    public struct PROPVARIANT { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        void GetCount(out uint c);
        void GetAt(uint i, out PROPERTYKEY k);
        void GetValue(ref PROPERTYKEY k, out PROPVARIANT v);
        void SetValue(ref PROPERTYKEY k, ref PROPVARIANT v);
        void Commit();
    }

    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IShellLinkW {
        void GetPath(IntPtr a, int b, IntPtr c, uint d);
        void GetIDList(out IntPtr p);
        void SetIDList(IntPtr p);
        void GetDescription(IntPtr a, int b);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string s);
        void GetWorkingDirectory(IntPtr a, int b);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string s);
        void GetArguments(IntPtr a, int b);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string s);
        void GetHotkey(out short h);
        void SetHotkey(short h);
        void GetShowCmd(out int c);
        void SetShowCmd(int c);
        void GetIconLocation(IntPtr a, int b, out int i);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string s, int i);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string s, uint r);
        void Resolve(IntPtr h, uint f);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string s);
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLink { }

    [DllImport("shell32.dll")]
    static extern int SHGetPropertyStoreForWindow(IntPtr h, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore ps);

    static readonly Guid AppUserModel = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
    // 5 = ID, 2 = RelaunchCommand, 3 = RelaunchIconResource, 4 = RelaunchDisplayNameResource

    static void Put(IPropertyStore ps, uint pid, string value) {
        var k = new PROPERTYKEY { fmtid = AppUserModel, pid = pid };
        var v = new PROPVARIANT { vt = 31, p = Marshal.StringToCoTaskMemUni(value) };   // VT_LPWSTR
        try { ps.SetValue(ref k, ref v); } finally { Marshal.FreeCoTaskMem(v.p); }
    }

    public static string Get(IntPtr hwnd, uint pid) {
        Guid iid = typeof(IPropertyStore).GUID; IPropertyStore ps;
        if (SHGetPropertyStoreForWindow(hwnd, ref iid, out ps) != 0) return null;
        try {
            var k = new PROPERTYKEY { fmtid = AppUserModel, pid = pid };
            PROPVARIANT v; ps.GetValue(ref k, out v);
            return v.vt == 31 ? Marshal.PtrToStringUni(v.p) : null;
        } finally { Marshal.ReleaseComObject(ps); }
    }

    public static bool StampWindow(IntPtr hwnd, string appId, string relaunch, string icon, string name) {
        Guid iid = typeof(IPropertyStore).GUID; IPropertyStore ps;
        if (SHGetPropertyStoreForWindow(hwnd, ref iid, out ps) != 0) return false;
        try {
            Put(ps, 2, relaunch);
            Put(ps, 3, icon);
            Put(ps, 4, name);
            Put(ps, 5, appId);   // last: the taskbar regroups the button when the ID changes
            return true;
        } finally { Marshal.ReleaseComObject(ps); }
    }

    public static void WriteShortcut(string lnk, string target, string args, string workDir, string icon, string desc, string appId) {
        var link = (IShellLinkW)new ShellLink();
        link.SetPath(target);
        link.SetArguments(args);
        link.SetWorkingDirectory(workDir);
        link.SetIconLocation(icon, 0);
        link.SetDescription(desc);
        var ps = (IPropertyStore)link;
        Put(ps, 5, appId);
        ps.Commit();
        ((IPersistFile)link).Save(lnk, true);
        Marshal.ReleaseComObject(link);
    }
}
'@
}

function Get-NovaAppLaunch {
    # What every Nova entry point runs: the same rules as the Desktop shortcut.
    $wscript = Join-Path $env:WINDIR 'System32\wscript.exe'
    $vbs = Join-Path $script:NovaRoot 'NovaOpen.vbs'
    $ico = Join-Path $script:NovaRoot 'NovaPanel.ico'
    return @{ Exe = $wscript; Args = "`"$vbs`""; Icon = $ico; Command = "`"$wscript`" `"$vbs`"" }
}

function Install-NovaAppShortcut {
    <#
        Start Menu "Nova" shortcut carrying the panel's AppUserModelID. Windows
        matches the running window to it, so the taskbar button gets its icon
        and "Pin to taskbar" pins this shortcut, not Chrome. -Force rewrites it;
        otherwise it is only created when missing. Also stamps the Desktop one.
    #>
    param([switch]$Force)
    $l = Get-NovaAppLaunch
    $start = Join-Path ([Environment]::GetFolderPath('Programs')) 'Nova.lnk'
    if ((Test-Path $start) -and -not $Force) { return }
    $targets = @($start)
    $desk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nova.lnk'
    if (Test-Path $desk) { $targets += $desk }
    foreach ($lnk in $targets) {
        try {
            [NovaAppIdentity]::WriteShortcut($lnk, $l.Exe, $l.Args, $script:NovaRoot, $l.Icon, 'Nova -- open the panel', $script:NovaAppId)
            Write-NovaLog "app shortcut written: $lnk"
        } catch {
            Write-NovaLog "app shortcut failed ($lnk): $($_.Exception.Message)"
        }
    }
}

function Set-NovaPanelIdentity {
    param([Parameter(Mandatory = $true)][IntPtr]$Handle)
    try {
        if ([NovaAppIdentity]::Get($Handle, 5) -eq $script:NovaAppId) { return $true }
        $l = Get-NovaAppLaunch
        return [NovaAppIdentity]::StampWindow($Handle, $script:NovaAppId, $l.Command, "$($l.Icon),0", 'Nova')
    } catch {
        Write-NovaLog "panel identity failed: $($_.Exception.Message)"
        return $false
    }
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
        Install-NovaAppShortcut
        [void](Set-NovaPanelIdentity -Handle $h)
        # The panel is a maximized window by default (Sir, 2026-11-20: "windowed fullscreen").
        if ([NovaWin32]::IsIconic($h) -or -not [NovaWinZoom]::IsZoomed($h)) { [void][NovaWin32]::ShowWindow($h, 3) }  # SW_MAXIMIZE
        [void][NovaWin32]::SetForegroundWindow($h)
        if ($Screen) { Move-NovaPanel -Window $open -Screen $Screen -Maximize:$Maximize }
        Set-Content -Path $pidFile -Value $open.Id -Encoding utf8
        if (-not $Quiet) { Write-NovaLog "panel raised (pid $($open.Id))" }
        return $true
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    Install-NovaAppShortcut

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
        '--start-maximized',
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
            [void](Set-NovaPanelIdentity -Handle $win.MainWindowHandle)
            if ($Screen) { Move-NovaPanel -Window $win -Screen $Screen -Maximize:$Maximize }
            elseif (-not [NovaWinZoom]::IsZoomed($win.MainWindowHandle)) { [void][NovaWin32]::ShowWindow($win.MainWindowHandle, 3) }
            Set-Content -Path $pidFile -Value $win.Id -Encoding utf8
            if (-not $Quiet) { Write-NovaLog "panel opened (pid $($win.Id)) via $(Split-Path $browser -Leaf)" }
            return $true
        }
    }

    Write-NovaLog 'panel launched but no NOVA window appeared within 10s'
    return $false
}
