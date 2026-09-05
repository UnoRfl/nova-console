<#
    Get-Windows.ps1 -- see what is open, and move it around.

        .\Get-Windows.ps1                    # list every visible window
        .\Get-Windows.ps1 -Focus "opera"     # bring one to the front
        .\Get-Windows.ps1 -Minimise "discord"
        .\Get-Windows.ps1 -Maximise "code"
        .\Get-Windows.ps1 -Close "notepad"   # asks the window to close, politely

    Matching is a case-insensitive substring of the window title or the
    process name, and the first match wins. -Close sends WM_CLOSE, so an app
    with unsaved work gets to put up its own "save?" prompt rather than
    losing anything.
#>
param(
    [string]$Focus,
    [string]$Minimise,
    [string]$Maximise,
    [string]$Close
)

$ErrorActionPreference = 'Stop'

if (-not ('NovaWindows' -as [type])) {
    Add-Type -Name 'NovaWindows' -Namespace '' -MemberDefinition @'
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wp, IntPtr lp);
'@
}

$SW_MINIMIZE = 6
$SW_MAXIMIZE = 3
$SW_RESTORE  = 9
$WM_CLOSE    = 0x0010

function Get-VisibleWindows {
    Get-Process |
        Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle } |
        ForEach-Object {
            $h = $_.MainWindowHandle
            $state = 'normal'
            if ([NovaWindows]::IsIconic($h)) { $state = 'minimised' }
            elseif ([NovaWindows]::IsZoomed($h)) { $state = 'maximised' }
            [pscustomobject]@{
                Pid     = $_.Id
                Process = $_.ProcessName
                Title   = $_.MainWindowTitle
                State   = $state
                MB      = [math]::Round($_.WorkingSet64 / 1MB)
                Handle  = $h
            }
        } | Sort-Object Process, Title
}

function Find-Window {
    param([string]$Needle)
    $all = Get-VisibleWindows
    $hit = $all | Where-Object { $_.Title -like "*$Needle*" } | Select-Object -First 1
    if (-not $hit) {
        $hit = $all | Where-Object { $_.Process -like "*$Needle*" } | Select-Object -First 1
    }
    return $hit
}

function Act {
    param([string]$Needle, [string]$Verb)
    $w = Find-Window $Needle
    if (-not $w) {
        Write-Output "No open window matching '$Needle'."
        return
    }
    switch ($Verb) {
        'focus' {
            if ([NovaWindows]::IsIconic($w.Handle)) { [void][NovaWindows]::ShowWindow($w.Handle, $SW_RESTORE) }
            [void][NovaWindows]::SetForegroundWindow($w.Handle)
            Write-Output "Brought '$($w.Title)' to the front."
        }
        'minimise' {
            [void][NovaWindows]::ShowWindow($w.Handle, $SW_MINIMIZE)
            Write-Output "Minimised '$($w.Title)'."
        }
        'maximise' {
            [void][NovaWindows]::ShowWindow($w.Handle, $SW_MAXIMIZE)
            Write-Output "Maximised '$($w.Title)'."
        }
        'close' {
            # WM_CLOSE, not Stop-Process: the app can still ask to save.
            [void][NovaWindows]::SendMessage($w.Handle, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
            Write-Output "Asked '$($w.Title)' to close. It may prompt to save."
        }
    }
}

if ($Focus)    { Act $Focus    'focus';    exit 0 }
if ($Minimise) { Act $Minimise 'minimise'; exit 0 }
if ($Maximise) { Act $Maximise 'maximise'; exit 0 }
if ($Close)    { Act $Close    'close';    exit 0 }

$windows = Get-VisibleWindows
Write-Output "$($windows.Count) open window(s):"
Write-Output ''
foreach ($w in $windows) {
    $title = $w.Title
    if ($title.Length -gt 58) { $title = $title.Substring(0, 55) + '...' }
    Write-Output ("  {0,-16} {1,-58} {2,-10} {3,5} MB" -f $w.Process, $title, $w.State, $w.MB)
}
