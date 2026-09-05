<#
    fetch-vendor.ps1 -- pull the two browser libraries NOVA draws with.

    They are deliberately NOT committed: they are somebody else's minified
    code, they are close to a megabyte together, and vendoring them would put
    this repository in the business of redistributing them. They are fetched
    once, here, and from then on NOVA never touches the network again.

      three.js  (MIT)  -- renders the 3D projections
      JSCAD     (MIT)  -- the solid modelling kernel ::CAD blocks are built by

    Run from the folder you installed NOVA into:

        .\fetch-vendor.ps1
#>
[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'

$targets = @(
    @{ Name = 'three.min.js'
       Url  = 'https://cdnjs.cloudflare.com/ajax/libs/three.js/0.150.1/three.min.js'
       About = 'three.js r150 (MIT)' }
    @{ Name = 'jscad-modeling.min.js'
       Url  = 'https://cdn.jsdelivr.net/npm/@jscad/modeling@2.13.0/dist/jscad-modeling.min.js'
       About = 'JSCAD modeling 2.13.0 (MIT)' }
)

foreach ($t in $targets) {
    $path = Join-Path $PSScriptRoot $t.Name
    if ((Test-Path $path) -and -not $Force) {
        Write-Host ("have    {0}" -f $t.Name) -ForegroundColor DarkGray
        continue
    }
    Write-Host ("fetch   {0}  <- {1}" -f $t.Name, $t.About)
    try {
        Invoke-WebRequest -Uri $t.Url -OutFile $path -UseBasicParsing
        $kb = [int]((Get-Item $path).Length / 1KB)
        Write-Host ("        {0} KB" -f $kb) -ForegroundColor Green
    } catch {
        Write-Host ("        failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
        Write-Host ("        download it by hand and save it as {0}" -f $path) -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host 'Done. NOVA runs entirely offline from here.' -ForegroundColor Magenta
