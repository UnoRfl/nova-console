<#
    Nova-Network.ps1 -- map this machine's network so Nova knows where it lives.

        .\Nova-Network.ps1              # scan and rewrite ~/.nova/network.md
        .\Nova-Network.ps1 -Quick       # skip the sweep, use what is already known
        .\Nova-Network.ps1 -Show        # print the current map

    Deliberately gentle: it pings, reads the ARP cache Windows already keeps,
    and asks DNS for names. No port scanning, no probing of services -- this
    exists so an assistant can say "your router is at .1 and your NAS is at
    .100", not to audit anyone's security.
#>
param(
    [switch]$Quick,
    [switch]$Show
)

$ErrorActionPreference = 'SilentlyContinue'
$Root = Join-Path $env:USERPROFILE '.nova'
$MapFile = Join-Path $Root 'network.md'

if ($Show) {
    if (Test-Path $MapFile) { Get-Content $MapFile } else { 'No map yet -- run .\Nova-Network.ps1' }
    exit 0
}

function Get-LocalFacts {
    $ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' }
    $gateway = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1).NextHop
    $adapters = foreach ($ip in $ips) {
        $a = Get-NetAdapter -InterfaceIndex $ip.InterfaceIndex
        [pscustomobject]@{
            IP     = $ip.IPAddress
            Prefix = $ip.PrefixLength
            Name   = $a.Name
            Desc   = $a.InterfaceDescription
            Mac    = $a.MacAddress
            Speed  = $a.LinkSpeed
        }
    }
    [pscustomobject]@{
        Host     = $env:COMPUTERNAME
        Gateway  = $gateway
        Adapters = $adapters
    }
}

function Resolve-HostName {
    param([string]$Address)
    # DNS first, then NetBIOS, which is often the only thing that names a
    # printer or a games console on a home LAN.
    try {
        $entry = [System.Net.Dns]::GetHostEntry($Address)
        if ($entry.HostName -and $entry.HostName -ne $Address) { return $entry.HostName }
    } catch { }
    try {
        $nb = & nbtstat -A $Address 2>$null | Select-String '<00>\s+UNIQUE' | Select-Object -First 1
        if ($nb) { return ($nb.ToString().Trim() -split '\s+')[0] }
    } catch { }
    return ''
}

function Invoke-Sweep {
    param([string]$Base, [int]$Timeout = 700)
    # .NET async pings so a /24 finishes in about a second rather than minutes.
    $pings = @()
    foreach ($n in 1..254) {
        $p = New-Object System.Net.NetworkInformation.Ping
        $pings += [pscustomobject]@{ Address = "$Base.$n"; Task = $p.SendPingAsync("$Base.$n", $Timeout); Ping = $p }
    }
    $alive = @()
    foreach ($item in $pings) {
        try {
            $r = $item.Task.Result
            if ($r.Status -eq 'Success') {
                $alive += [pscustomobject]@{ IP = $item.Address; RoundTrip = $r.RoundtripTime }
            }
        } catch { }
        $item.Ping.Dispose()
    }
    return $alive
}

function Get-MacTable {
    $table = @{}
    Get-NetNeighbor -AddressFamily IPv4 |
        Where-Object { $_.LinkLayerAddress -and $_.State -in @('Reachable','Stale','Permanent') } |
        ForEach-Object { $table[$_.IPAddress] = $_.LinkLayerAddress }
    return $table
}

function Test-RandomMac {
    param([string]$Mac)
    # Bit 1 of the first octet marks a locally administered address, which in
    # practice means a phone or laptop rotating its MAC for privacy.
    if (-not $Mac) { return $false }
    $first = [Convert]::ToInt32(($Mac -split '[-:]')[0], 16)
    return (($first -band 2) -ne 0)
}

# ------------------------------------------------------------------ gather
$facts = Get-LocalFacts
$lan = $facts.Adapters | Where-Object { $_.IP -like '192.168.*' -or $_.IP -like '10.*' -or $_.IP -like '172.*' } | Select-Object -First 1

Write-Host "scanning from $($facts.Host) ..." -ForegroundColor Magenta

$alive = @()
if ($lan -and -not $Quick) {
    $base = ($lan.IP -split '\.')[0..2] -join '.'
    Write-Host "  sweeping $base.0/$($lan.Prefix) ..."
    $alive = Invoke-Sweep -Base $base
    Write-Host "  $($alive.Count) hosts responded"
}

$macs = Get-MacTable
if (-not $alive -and $macs.Count) {
    $alive = $macs.Keys | Where-Object { $_ -notlike '*.255' } | ForEach-Object { [pscustomobject]@{ IP = $_; RoundTrip = $null } }
}

$devices = foreach ($h in ($alive | Sort-Object { [version]($_.IP) })) {
    $mac = $macs[$h.IP]
    $name = Resolve-HostName $h.IP
    $role = ''
    if ($h.IP -eq $facts.Gateway) { $role = 'router / gateway' }
    elseif ($lan -and $h.IP -eq $lan.IP) { $role = 'THIS PC (runs Nova)' }
    elseif (Test-RandomMac $mac) { $role = 'randomised MAC (likely a phone or laptop)' }
    [pscustomobject]@{
        IP   = $h.IP
        Name = $name
        Mac  = $mac
        Ms   = $h.RoundTrip
        Role = $role
    }
}

# Tailscale gives real names for anything reachable off the LAN.
$tsNodes = @()
$ts = @("$env:ProgramFiles\Tailscale\tailscale.exe", "${env:ProgramFiles(x86)}\Tailscale\tailscale.exe") |
      Where-Object { Test-Path $_ } | Select-Object -First 1
if ($ts) {
    foreach ($line in (& $ts status 2>$null)) {
        if ($line -match '^\s*(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\S+)\s+(\S+)') {
            $tsNodes += [pscustomobject]@{
                IP = $Matches[1]; Name = $Matches[2]; Owner = $Matches[3]; OS = $Matches[4]
            }
        }
    }
}

# ------------------------------------------------------------------ write
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("# Network map")
[void]$sb.AppendLine()
[void]$sb.AppendLine("Last scanned $(Get-Date -Format 'yyyy-MM-dd HH:mm').")
[void]$sb.AppendLine()
[void]$sb.AppendLine("## This machine")
[void]$sb.AppendLine()
[void]$sb.AppendLine("- Hostname: **$($facts.Host)** (this is the PC Nova itself runs on)")
foreach ($a in $facts.Adapters) {
    [void]$sb.AppendLine("- $($a.IP)/$($a.Prefix) on $($a.Name) - $($a.Desc), $($a.Speed)")
}
[void]$sb.AppendLine("- Router / default gateway: $($facts.Gateway)")
[void]$sb.AppendLine()

if ($devices) {
    [void]$sb.AppendLine("## Devices seen on the local network")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("| Address | Name | MAC | Notes |")
    [void]$sb.AppendLine("|---|---|---|---|")
    foreach ($d in $devices) {
        $n = if ($d.Name) { $d.Name } else { '-' }
        $m = if ($d.Mac) { $d.Mac } else { '-' }
        $r = if ($d.Role) { $d.Role } else { '-' }
        [void]$sb.AppendLine("| $($d.IP) | $n | $m | $r |")
    }
    [void]$sb.AppendLine()
}

if ($tsNodes) {
    [void]$sb.AppendLine("## Tailscale (reachable from anywhere)")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("| Address | Name | Owner | OS |")
    [void]$sb.AppendLine("|---|---|---|---|")
    foreach ($t in $tsNodes) {
        [void]$sb.AppendLine("| $($t.IP) | $($t.Name) | $($t.Owner) | $($t.OS) |")
    }
    [void]$sb.AppendLine()
}

[void]$sb.AppendLine("Addresses on a home network are handed out by the router and can change.")
[void]$sb.AppendLine("Re-run ``.\Nova.ps1 network`` if something has moved.")

Set-Content -Path $MapFile -Value $sb.ToString() -Encoding utf8
Write-Host "wrote $MapFile" -ForegroundColor Green
Write-Host ''
Get-Content $MapFile
