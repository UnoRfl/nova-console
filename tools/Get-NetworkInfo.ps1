<#
    Get-NetworkInfo.ps1 -- where this machine sits on the network.

        .\Get-NetworkInfo.ps1              # adapters, wifi, DNS, gateway
        .\Get-NetworkInfo.ps1 -Public      # also ask what the outside world sees
        .\Get-NetworkInfo.ps1 -Devices     # also list other devices on the LAN

    -Devices reads the cached map in ~/.nova/network.md if it is recent, and
    rescans with Nova-Network.ps1 otherwise, so asking twice in a minute does
    not sweep the subnet twice.
#>
param(
    [switch]$Public,
    [switch]$Devices,
    [int]$MaxMapAgeMinutes = 30
)

$ErrorActionPreference = 'SilentlyContinue'
$Root = Join-Path $env:USERPROFILE '.nova'

Write-Output "Host: $env:COMPUTERNAME"
Write-Output ''

# --- adapters -----------------------------------------------------------------
Write-Output 'Adapters carrying traffic:'
$up = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' }
foreach ($a in $up) {
    $addrs = Get-NetIPAddress -InterfaceIndex $a.InterfaceIndex -AddressFamily IPv4 |
             Where-Object { $_.IPAddress -ne '127.0.0.1' }
    foreach ($ip in $addrs) {
        Write-Output ("  {0,-16} {1,-18} {2}" -f $a.Name, "$($ip.IPAddress)/$($ip.PrefixLength)", $a.LinkSpeed)
    }
}

# --- gateway and DNS ----------------------------------------------------------
$gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1).NextHop
if ($gw) { Write-Output ''; Write-Output "Router: $gw" }

$dns = Get-DnsClientServerAddress -AddressFamily IPv4 |
       Where-Object { $_.ServerAddresses } |
       Select-Object -ExpandProperty ServerAddresses -Unique
if ($dns) { Write-Output "DNS servers: $($dns -join ', ')" }

# --- wifi ---------------------------------------------------------------------
$wifi = netsh wlan show interfaces 2>$null
if ($wifi -and ($wifi -join "`n") -match 'SSID') {
    Write-Output ''
    Write-Output 'Wi-Fi:'
    foreach ($line in $wifi) {
        if ($line -match '^\s*(SSID|Signal|Radio type|Channel|Receive rate|Transmit rate|Authentication)\s*:\s*(.+)$') {
            Write-Output ("  {0,-16} {1}" -f $Matches[1], $Matches[2].Trim())
        }
    }
} else {
    Write-Output ''
    Write-Output 'Wi-Fi: not connected (wired, or no wireless adapter).'
}

# --- tailscale ----------------------------------------------------------------
$ts = @("$env:ProgramFiles\Tailscale\tailscale.exe", "${env:ProgramFiles(x86)}\Tailscale\tailscale.exe") |
      Where-Object { Test-Path $_ } | Select-Object -First 1
if ($ts) {
    $status = & $ts status 2>$null
    if ($status) {
        Write-Output ''
        Write-Output 'Tailscale:'
        foreach ($line in ($status | Select-Object -First 8)) {
            if ($line.Trim()) { Write-Output "  $($line.Trim())" }
        }
    }
}

# --- what the internet sees ----------------------------------------------------
if ($Public) {
    Write-Output ''
    try {
        $ip = (Invoke-RestMethod -Uri 'https://api.ipify.org?format=json' -TimeoutSec 8).ip
        Write-Output "Public IP: $ip"
    } catch {
        Write-Output 'Public IP: could not be determined (no internet, or the lookup was blocked).'
    }
}

# --- other devices -------------------------------------------------------------
if ($Devices) {
    $map = Join-Path $Root 'network.md'
    $stale = $true
    if (Test-Path $map) {
        $age = (Get-Date) - (Get-Item $map).LastWriteTime
        $stale = $age.TotalMinutes -gt $MaxMapAgeMinutes
    }
    if ($stale) {
        Write-Output ''
        Write-Output 'Rescanning the local network...'
        & (Join-Path $Root 'Nova-Network.ps1') | Out-Null
    }
    if (Test-Path $map) {
        Write-Output ''
        Get-Content $map
    } else {
        Write-Output ''
        Write-Output 'No device map available.'
    }
}
