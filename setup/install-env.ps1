<#
    install-env.ps1 -- build Nova's own Python environment at ~/.nova/venv.

    Run from a real terminal (not from inside the Claude desktop app's shell,
    whose installs land in an app sandbox). Safe to re-run.

    Everything goes through `python -m ...`: Smart App Control on this PC
    blocks the unsigned .exe shims pip would otherwise create.
#>
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE '.nova'
$venv = Join-Path $root 'venv'
$py   = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Host 'creating venv...' -ForegroundColor Magenta
    & py -3 -m venv $venv
}

Write-Host 'installing packages...' -ForegroundColor Magenta
& $py -m pip install --upgrade pip --quiet
# Three pins, for one reason: Smart App Control refuses to load compiled
# files that are too new to have a reputation. On 2026-11-12 the newest
# ctranslate2 (4.9.0), sherpa-onnx (2.0.1) and pydantic-core (2.50.4) were all
# blocked, while these slightly older builds load. If a pin is ever raised,
# test the import before trusting it.
& $py -m pip uninstall -y sherpa-onnx sherpa-onnx-core --quiet 2>$null
& $py -m pip install --only-binary=:all: `
    numpy sounddevice faster-whisper av onnxruntime `
    'ctranslate2==4.8.5' 'sherpa-onnx==1.13.8' 'pydantic<2.14' `
    kokoro-onnx mss pillow mcp edge-tts `
    nvidia-cublas-cu12 nvidia-cudnn-cu12

Write-Host ''
& $py -c "import sys, importlib.util as u; mods=['numpy','sounddevice','faster_whisper','av','onnxruntime','sherpa_onnx','kokoro_onnx','mss','PIL','mcp','edge_tts','ctranslate2']; import importlib
bad=[]
for m in mods:
    try: importlib.import_module(m)
    except Exception as e: bad.append(m+' ('+type(e).__name__+')')
print('python', sys.version.split()[0]); print('MISSING: '+', '.join(bad) if bad else 'ALL PACKAGES PRESENT')"
Write-Host 'NOVA-ENV-DONE'
