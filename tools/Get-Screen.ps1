<#
    Get-Screen.ps1 -- capture the screen so Nova can actually look at it.

        .\Get-Screen.ps1                  # all monitors, one image
        .\Get-Screen.ps1 -Primary         # just the main monitor
        .\Get-Screen.ps1 -Monitor 2       # a specific monitor
        .\Get-Screen.ps1 -Window "NOVA"   # just that window
        .\Get-Screen.ps1 -MaxWidth 2400   # allow more detail

    Prints the path of the PNG it wrote. Nova then reads that file with its
    Read tool, which renders images, so it genuinely sees what is on screen
    rather than being told about it.

    Images are scaled down and old captures pruned, because a 4K PNG is a lot
    of tokens and these pile up fast.
#>
param(
    [switch]$Primary,
    [int]$Monitor = 0,
    [string]$Window,
    [int]$MaxWidth = 1600,
    [int]$Keep = 8
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$outDir = Join-Path $env:USERPROFILE '.nova\captures'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

# --- work out what rectangle to grab -----------------------------------------
$screens = [System.Windows.Forms.Screen]::AllScreens
$label = 'all monitors'
$rect = $null

if ($Window) {
    if (-not ('NovaCapture' -as [type])) {
        Add-Type -Name 'NovaCapture' -Namespace '' -MemberDefinition @'
            [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
            [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
            [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
            [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int n);
            [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
'@
    }

    $proc = Get-Process | Where-Object {
        $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle -like "*$Window*"
    } | Select-Object -First 1

    if (-not $proc) {
        Write-Error "No visible window matching '$Window'. Run Get-Windows.ps1 to see what is open."
        exit 1
    }

    $h = $proc.MainWindowHandle
    if ([NovaCapture]::IsIconic($h)) { [void][NovaCapture]::ShowWindow($h, 9) }
    [void][NovaCapture]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds 350        # let it paint before we grab it

    $r = New-Object NovaCapture+RECT
    if (-not [NovaCapture]::GetWindowRect($h, [ref]$r)) {
        Write-Error 'Could not measure that window.'
        exit 1
    }
    $rect = New-Object System.Drawing.Rectangle $r.Left, $r.Top,
                        ($r.Right - $r.Left), ($r.Bottom - $r.Top)
    $label = "window '$($proc.MainWindowTitle)'"
}
elseif ($Monitor -gt 0) {
    if ($Monitor -gt $screens.Count) {
        Write-Error "Only $($screens.Count) monitor(s) attached."
        exit 1
    }
    $rect = $screens[$Monitor - 1].Bounds
    $label = "monitor $Monitor"
}
elseif ($Primary) {
    $rect = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $label = 'primary monitor'
}
else {
    # Union of every monitor, so a second screen is not silently dropped.
    $left   = ($screens | ForEach-Object { $_.Bounds.Left })   | Measure-Object -Minimum | ForEach-Object Minimum
    $top    = ($screens | ForEach-Object { $_.Bounds.Top })    | Measure-Object -Minimum | ForEach-Object Minimum
    $right  = ($screens | ForEach-Object { $_.Bounds.Right })  | Measure-Object -Maximum | ForEach-Object Maximum
    $bottom = ($screens | ForEach-Object { $_.Bounds.Bottom }) | Measure-Object -Maximum | ForEach-Object Maximum
    $rect = New-Object System.Drawing.Rectangle $left, $top, ($right - $left), ($bottom - $top)
    if ($screens.Count -eq 1) { $label = 'the screen' }
}

if ($rect.Width -le 0 -or $rect.Height -le 0) {
    Write-Error 'Nothing to capture (zero-sized region).'
    exit 1
}

# --- grab it ------------------------------------------------------------------
$shot = New-Object System.Drawing.Bitmap $rect.Width, $rect.Height
$g = [System.Drawing.Graphics]::FromImage($shot)
$g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $shot.Size,
                  [System.Drawing.CopyPixelOperation]::SourceCopy)
$g.Dispose()

# --- scale down so it is affordable to look at --------------------------------
$final = $shot
$scaled = $false
if ($MaxWidth -gt 0 -and $shot.Width -gt $MaxWidth) {
    $ratio = $MaxWidth / $shot.Width
    $w = [int]$MaxWidth
    $h = [int][Math]::Round($shot.Height * $ratio)
    $resized = New-Object System.Drawing.Bitmap $w, $h
    $rg = [System.Drawing.Graphics]::FromImage($resized)
    $rg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $rg.DrawImage($shot, 0, 0, $w, $h)
    $rg.Dispose()
    $shot.Dispose()
    $final = $resized
    $scaled = $true
}

$path = Join-Path $outDir ("screen-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.png')
$final.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$dims = "$($final.Width)x$($final.Height)"
$final.Dispose()

# --- prune old captures -------------------------------------------------------
Get-ChildItem $outDir -Filter 'screen-*.png' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Keep |
    Remove-Item -Force -ErrorAction SilentlyContinue

$kb = [math]::Round((Get-Item $path).Length / 1KB)
Write-Output "Captured $label at $dims ($kb KB)."
if ($scaled) { Write-Output "Scaled down from $($rect.Width)x$($rect.Height) to keep it readable." }
Write-Output "Read this file to see it: $path"
