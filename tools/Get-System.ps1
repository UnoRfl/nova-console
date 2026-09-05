<#
    Get-System.ps1 -- a snapshot of how this machine is doing.

        .\Get-System.ps1              # the summary
        .\Get-System.ps1 -Processes 15
        .\Get-System.ps1 -Json        # machine-readable

    Every value comes from .NET or the registry, never from WMI: this machine's
    WMI repository is corrupt and every Win32_* class errors out, which also
    breaks systeminfo.exe.
#>
param(
    [int]$Processes = 8,
    [switch]$Json
)

$ErrorActionPreference = 'SilentlyContinue'

# --- memory, via the performance counters rather than WMI ---------------------
$totalMb = $null
try {
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'
    # Not a memory size, but the counters below are the reliable route.
    $avail = (Get-Counter '\Memory\Available MBytes' -EA Stop).CounterSamples[0].CookedValue
    $committed = (Get-Counter '\Memory\Committed Bytes' -EA Stop).CounterSamples[0].CookedValue / 1MB
    $commitLimit = (Get-Counter '\Memory\Commit Limit' -EA Stop).CounterSamples[0].CookedValue / 1MB
} catch { $avail = $null }

$cpuPct = $null
try { $cpuPct = [math]::Round((Get-Counter '\Processor(_Total)\% Processor Time' -EA Stop).CounterSamples[0].CookedValue, 1) } catch { }

# --- disks --------------------------------------------------------------------
$disks = [System.IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and $_.DriveType -eq 'Fixed' } | ForEach-Object {
    [pscustomobject]@{
        Drive    = $_.Name.TrimEnd('\')
        TotalGB  = [math]::Round($_.TotalSize / 1GB, 1)
        FreeGB   = [math]::Round($_.AvailableFreeSpace / 1GB, 1)
        FreePct  = [math]::Round(100 * $_.AvailableFreeSpace / $_.TotalSize)
        Format   = $_.DriveFormat
    }
}

# --- uptime -------------------------------------------------------------------
$uptime = $null
try {
    $ticks = [Environment]::TickCount64
    $uptime = [TimeSpan]::FromMilliseconds($ticks)
} catch { }

# --- battery ------------------------------------------------------------------
$battery = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    $p = [System.Windows.Forms.SystemInformation]::PowerStatus
    if ($p.BatteryChargeStatus -ne 'NoSystemBattery') {
        $battery = [pscustomobject]@{
            Percent  = [math]::Round($p.BatteryLifePercent * 100)
            Charging = ($p.PowerLineStatus -eq 'Online')
            Status   = "$($p.BatteryChargeStatus)"
        }
    }
} catch { }

# --- GPU, from the registry -----------------------------------------------------
$gpus = @()
try {
    Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}' -EA Stop |
        Where-Object { $_.PSChildName -match '^\d{4}$' } | ForEach-Object {
            $desc = (Get-ItemProperty $_.PSPath -Name 'DriverDesc' -EA SilentlyContinue).DriverDesc
            if ($desc) { $gpus += $desc }
        }
} catch { }

$snapshot = [pscustomobject]@{
    Host       = $env:COMPUTERNAME
    User       = $env:USERNAME
    OS         = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -EA SilentlyContinue).ProductName
    Build      = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -EA SilentlyContinue).CurrentBuild
    Cores      = [Environment]::ProcessorCount
    CpuPercent = $cpuPct
    AvailableMB = if ($avail) { [math]::Round($avail) } else { $null }
    CommittedMB = if ($committed) { [math]::Round($committed) } else { $null }
    CommitLimitMB = if ($commitLimit) { [math]::Round($commitLimit) } else { $null }
    UptimeHours = if ($uptime) { [math]::Round($uptime.TotalHours, 1) } else { $null }
    Gpus       = $gpus
    Disks      = $disks
    Battery    = $battery
    TopProcesses = Get-Process | Sort-Object WorkingSet64 -Descending |
        Select-Object -First $Processes |
        ForEach-Object { [pscustomobject]@{ Name = $_.ProcessName; MB = [math]::Round($_.WorkingSet64 / 1MB) } }
}

if ($Json) {
    $snapshot | ConvertTo-Json -Depth 4
    exit 0
}

Write-Output "$($snapshot.Host) - $($snapshot.OS) build $($snapshot.Build)"
Write-Output "$($snapshot.Cores) logical cores$(if ($snapshot.CpuPercent -ne $null) { ", $($snapshot.CpuPercent)% busy" })"
if ($snapshot.AvailableMB) {
    Write-Output "Memory: $($snapshot.AvailableMB) MB available, $($snapshot.CommittedMB) MB committed of $($snapshot.CommitLimitMB) MB"
}
if ($snapshot.UptimeHours) { Write-Output "Up for $($snapshot.UptimeHours) hours" }
foreach ($g in $snapshot.Gpus) { Write-Output "GPU: $g" }
foreach ($d in $snapshot.Disks) {
    Write-Output "Disk $($d.Drive) - $($d.FreeGB) GB free of $($d.TotalGB) GB ($($d.FreePct)% free, $($d.Format))"
}
if ($snapshot.Battery) {
    Write-Output "Battery: $($snapshot.Battery.Percent)%$(if ($snapshot.Battery.Charging) { ', charging' })"
}
Write-Output ''
Write-Output "Heaviest processes:"
foreach ($p in $snapshot.TopProcesses) { Write-Output ("  {0,6} MB  {1}" -f $p.MB, $p.Name) }
