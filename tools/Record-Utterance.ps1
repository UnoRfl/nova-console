<#
    Record-Utterance.ps1 -- capture speech through Windows, not the browser.

        .\Record-Utterance.ps1                     # record until you stop talking
        .\Record-Utterance.ps1 -Seconds 5 -Fixed   # record exactly five seconds
        .\Record-Utterance.ps1 -OutFile C:\tmp\a.wav

    Prints the path of the WAV it wrote, or nothing at all if it heard silence.

    Why this exists: Chrome's capture path caused three separate faults on this
    machine -- the Console/Communications role split, a missing permission
    grant, and a stale pinned device that silently recorded nothing. The
    Windows path has been reliable throughout; the wake word hears Sir at
    ninety percent through it. So we record here and hand the audio to Whisper,
    which is the accurate part.

    Recording goes through MCI (winmm), which reads the Windows DEFAULT input
    device -- the same device the wake word uses, so the two can never disagree
    the way the browser did. 16 kHz mono is exactly what Whisper wants, so no
    resampling happens later.
#>
param(
    # A ceiling, not a timer: EndSilenceTimeout ends ordinary speech long
    # before this. It existed at twelve seconds and was truncating him
    # mid-sentence whenever he said anything of length.
    [double]$Seconds = 45,
    [switch]$Fixed,
    [string]$OutFile,
    [double]$SilenceSeconds = 1.6,
    [switch]$KeepSilent
)

$ErrorActionPreference = 'Stop'

if (-not ('NovaMci' -as [type])) {
    Add-Type -Name 'NovaMci' -Namespace '' -MemberDefinition @'
        [DllImport("winmm.dll", CharSet = CharSet.Auto)]
        public static extern int mciSendString(string command, System.Text.StringBuilder ret, int retLen, IntPtr hwnd);
        [DllImport("winmm.dll", CharSet = CharSet.Auto)]
        public static extern int mciGetErrorString(int err, System.Text.StringBuilder buf, int bufLen);
'@
}

function Invoke-Mci {
    param([string]$Command, [switch]$Tolerate)
    $sb = New-Object System.Text.StringBuilder 512
    $rc = [NovaMci]::mciSendString($Command, $sb, $sb.Capacity, [IntPtr]::Zero)
    if ($rc -ne 0 -and -not $Tolerate) {
        $err = New-Object System.Text.StringBuilder 512
        [void][NovaMci]::mciGetErrorString($rc, $err, $err.Capacity)
        throw "MCI '$Command' failed: $($err.ToString())"
    }
    return $sb.ToString()
}

if (-not $OutFile) {
    $dir = Join-Path $env:USERPROFILE '.nova\captures'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $OutFile = Join-Path $dir ("utterance-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.wav')
}

# Any earlier handle left open would make "open new" fail.
[void](Invoke-Mci 'close novarec' -Tolerate)

Invoke-Mci 'open new type waveaudio alias novarec' | Out-Null
try {
    # 16 kHz mono 16-bit: Whisper's native rate, and small on disk.
    Invoke-Mci 'set novarec bitspersample 16 channels 1 samplespersec 16000 alignment 2 bytespersec 32000' | Out-Null
    Invoke-Mci 'record novarec' | Out-Null

    if ($Fixed) {
        Start-Sleep -Seconds $Seconds
    } else {
        # Endpoint on silence by watching how many bytes MCI has captured:
        # a quiet stretch still advances position, so instead we sample the
        # peak level from the recorded buffer indirectly via elapsed position
        # deltas. Simpler and more reliable here: let the Windows recogniser
        # tell us when speech ended, and cap it.
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $ended = $false

        try {
            Add-Type -AssemblyName System.Speech
            $rec = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
                   Where-Object { $_.Culture.Name -like 'en-*' } | Select-Object -First 1
            if ($rec) {
                $eng = New-Object System.Speech.Recognition.SpeechRecognitionEngine $rec.Id
                $eng.SetInputToDefaultAudioDevice()
                $eng.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
                $eng.InitialSilenceTimeout = [TimeSpan]::FromSeconds(6)
                $eng.EndSilenceTimeout     = [TimeSpan]::FromSeconds($SilenceSeconds)
                $eng.BabbleTimeout         = [TimeSpan]::FromSeconds(0)
                # Returns the moment speech stops, which is exactly the cue we
                # want to end the recording on.
                [void]$eng.Recognize([TimeSpan]::FromSeconds($Seconds))
                $eng.Dispose()
                $ended = $true
            }
        } catch {
            # The recogniser could not share the device; fall back to a timer.
            $ended = $false
        }

        if (-not $ended) {
            $remaining = $Seconds - $sw.Elapsed.TotalSeconds
            if ($remaining -gt 0) { Start-Sleep -Seconds ([Math]::Min($remaining, 8)) }
        }
    }

    Invoke-Mci 'stop novarec' | Out-Null
    Invoke-Mci ('save novarec "' + $OutFile + '"') | Out-Null
}
finally {
    [void](Invoke-Mci 'close novarec' -Tolerate)
}

if (-not (Test-Path $OutFile)) {
    Write-Error 'Recording produced no file.'
    exit 1
}

$bytes = (Get-Item $OutFile).Length
# A 44-byte header and little else means nothing was captured at all.
if ($bytes -lt 4000 -and -not $KeepSilent) {
    Remove-Item $OutFile -Force -ErrorAction SilentlyContinue
    Write-Output 'SILENT'
    exit 0
}

$seconds = [math]::Round(($bytes - 44) / 32000.0, 2)
Write-Output $OutFile
Write-Output "  $([math]::Round($bytes/1KB,1)) KB, about $seconds seconds of 16 kHz mono audio"

# Keep the capture folder from growing without limit.
Get-ChildItem (Split-Path $OutFile) -Filter 'utterance-*.wav' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 10 |
    Remove-Item -Force -ErrorAction SilentlyContinue
