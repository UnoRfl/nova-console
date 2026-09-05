<#
    nova-wake.ps1 -- always-on offline wake word.

    Listens through the Windows speech recogniser (entirely on this machine --
    no audio ever leaves the PC) and, when you say "Nova", opens the panel AND
    tells it to start recording, so you can run the two together:

        "Nova, how much disk space is left?"

        .\nova-wake.ps1                    # listen forever
        .\nova-wake.ps1 -Verbose           # log every recognition, decoys included
        .\nova-wake.ps1 -Threshold 0.8     # fussier, fewer false triggers
        .\nova-wake.ps1 -TestOnce          # confirm the mic works, then exit

    Why the decoy list: a closed grammar has to match SOMETHING, so a
    recogniser given only "nova" hears "nova" in every cough and passing
    conversation. Loading a few dozen ordinary words alongside it gives the
    recogniser somewhere harmless to put normal speech, which is what makes a
    wake word usable rather than maddening. "Nova" needs particular care --
    "over" and "no" are everywhere in English -- so its near-homophones are
    decoys too, and decoys are checked BEFORE the wake test.
#>
param(
    [double]$Threshold = 0.74,
    [switch]$TestOnce,
    [switch]$DictateOnce,
    [string[]]$Check,
    [switch]$Verbose
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'NovaPanel.ps1')

$WAKE = @(
    'nova',
    'hey nova',
    'hello nova',
    'okay nova',
    'yo nova',
    'nova wake up',
    'nova are you there'
)

# Ordinary speech needs somewhere to land. The first block is the dangerous
# one: these are what "nova" actually gets confused with.
$DECOYS = @(
    'over','oven','open','novel','november','nova scotia','no','nope','know',
    'no way','oh no','whoa','over there','all over','game over','moving over',
    'yes','okay','thanks','hello','hey','stop','wait','what','why','how',
    'the','and','but','right','sure','maybe','later','now','here','there',
    'this','that','one','two','three','good','bad','close','start',
    'nothing','something','anything','really','actually','honestly','alright',
    'come on','hold on','never mind','let me see','i think','i mean','you know'
)

function New-WakeEngine {
    Add-Type -AssemblyName System.Speech

    $installed = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
    if (-not $installed -or $installed.Count -eq 0) {
        throw 'No Windows speech recogniser is installed. Add an English speech language pack in Settings > Time & Language > Speech.'
    }
    $pick = $installed | Where-Object { $_.Culture.Name -like 'en-*' } | Select-Object -First 1
    if (-not $pick) { $pick = $installed[0] }

    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine $pick.Id
    $engine.SetInputToDefaultAudioDevice()

    # Two grammars, swapped as needed: a closed wake grammar for spotting the
    # word, and open dictation for capturing whatever you actually ask.
    $choices = New-Object System.Speech.Recognition.Choices
    foreach ($phrase in ($WAKE + $DECOYS)) { $choices.Add($phrase) }
    $builder = New-Object System.Speech.Recognition.GrammarBuilder
    $builder.Append($choices)

    $wakeGrammar = New-Object System.Speech.Recognition.Grammar $builder
    $wakeGrammar.Name = 'wake'
    $dictGrammar = New-Object System.Speech.Recognition.DictationGrammar
    $dictGrammar.Name = 'dictation'

    $engine.LoadGrammar($wakeGrammar)
    $engine.BabbleTimeout = [TimeSpan]::FromSeconds(0)

    return @{
        Engine      = $engine
        Recognizer  = $pick
        WakeGrammar = $wakeGrammar
        DictGrammar = $dictGrammar
    }
}

function Set-EngineMode {
    <#
        Swap between spotting the wake word and transcribing a request.

        Dictation gets a longer end-of-speech silence so a natural pause
        mid-sentence does not cut the request in half; wake spotting gets a
        short one so it reacts promptly.
    #>
    param($Built, [ValidateSet('wake','dictate')] [string]$Mode)

    $engine = $Built.Engine
    $engine.UnloadAllGrammars()
    if ($Mode -eq 'wake') {
        $engine.LoadGrammar($Built.WakeGrammar)
        $engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds(0)
        $engine.EndSilenceTimeout     = [TimeSpan]::FromMilliseconds(400)
    } else {
        $engine.LoadGrammar($Built.DictGrammar)
        $engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds(6)
        $engine.EndSilenceTimeout     = [TimeSpan]::FromMilliseconds(1300)
    }
}

function Get-WhisperUtterance {
    <#
        Record through Windows, transcribe with Whisper.

        The browser's capture path caused three separate faults on this machine
        (the Console/Communications role split, a missing permission grant, and
        a stale pinned device that recorded silence). MCI reads the Windows
        DEFAULT input -- the very device this wake word already hears Sir on --
        so the two can never disagree. Whisper then does the accurate part.
    #>
    param([int]$Seconds = 45)

    $recorder = Join-Path $PSScriptRoot 'tools\Record-Utterance.ps1'
    if (-not (Test-Path $recorder)) {
        Write-NovaLog 'Record-Utterance.ps1 is missing' 'wake'
        return $null
    }

    # The recogniser inside the recorder needs the device to itself for
    # endpointing, so release ours for the duration.
    $engine.RecognizeAsyncCancel()

    $wav = $null
    try {
        $lines = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$recorder" `
                    -Seconds $Seconds -SilenceSeconds 1.6 2>&1
        foreach ($line in $lines) {
            $s = ($line | Out-String).Trim()
            if ($s -eq 'SILENT') { Write-NovaLog 'heard nothing at all' 'wake'; return $null }
            if ($s -match '\.wav$') { $wav = $s }
        }
    } catch {
        Write-NovaLog "recording failed: $($_.Exception.Message)" 'wake'
        return $null
    }

    if (-not $wav -or -not (Test-Path $wav)) {
        Write-NovaLog 'no audio was captured' 'wake'
        return $null
    }

    return Get-NovaTranscript -WavPath $wav
}

function Get-Utterance {
    <#
        Capture one spoken request and return the text.

        Whisper first: it is far more accurate than the Windows desktop
        recogniser, which mangles ordinary sentences badly enough to be
        unusable. The desktop recogniser stays as a fallback for when Whisper
        or the bridge is unavailable, so a request is never simply lost.

        Either way the audio comes from the WINDOWS DEFAULT input -- the same
        device this wake word already hears Sir on. The browser is not involved
        at any point, which is the whole reason for doing it here: Chrome's
        capture path caused three separate faults on this machine and its
        device preference cannot be steered from outside.
    #>
    param($Built, [int]$Seconds = 15)

    # Tell the panel we are capturing before we start, so it turns green while
    # Sir is actually speaking rather than only lighting up afterwards. The
    # transcript follows as a second signal once we have it.
    [void](Send-NovaWake -Phrase 'listening')

    $whisper = Get-WhisperUtterance -Seconds $Seconds
    if ($whisper) { return $whisper }

    Write-NovaLog 'falling back to the Windows recogniser' 'wake'
    Set-EngineMode -Built $Built -Mode 'dictate'
    try {
        $r = $Built.Engine.Recognize([TimeSpan]::FromSeconds($Seconds))
    } catch {
        Write-NovaLog "dictation error: $($_.Exception.Message)" 'wake'
        $r = $null
    } finally {
        Set-EngineMode -Built $Built -Mode 'wake'
    }

    if ($null -eq $r) { return $null }
    $text = ($r.Text | Out-String).Trim()
    if (-not $text) { return $null }
    Write-NovaLog ("desktop recogniser heard: '{0}' ({1:P0})" -f $text, $r.Confidence) 'wake'
    return $text
}

function Test-IsWake {
    param([string]$Text)
    $t = ($Text -replace '[^a-zA-Z ]', '').Trim().ToLower() -replace '\s+', ' '
    if (-not $t) { return $false }

    # Decoys win ties. Without this, "Nova Scotia" and "moving over" would
    # trip the substring test below and summon the panel.
    foreach ($d in $DECOYS) { if ($t -eq $d.ToLower()) { return $false } }

    foreach ($w in $WAKE) { if ($t -eq $w.ToLower()) { return $true } }
    return ($t -match '\bnova\b')
}

# -Check runs the wake/decoy decision over phrases you type, with no
# microphone and no engine -- useful for working out why something did or did
# not trigger:  .\nova-wake.ps1 -Check "nova scotia","hey nova"
if ($Check) {
    Write-Host ''
    foreach ($phrase in $Check) {
        $hit = Test-IsWake $phrase
        $colour = if ($hit) { 'Green' } else { 'DarkGray' }
        Write-Host ("  {0,-24} -> {1}" -f "`"$phrase`"", $(if ($hit) { 'WAKE' } else { 'ignored' })) -ForegroundColor $colour
    }
    Write-Host ''
    exit 0
}

# ------------------------------------------------------------------ startup
$built = $null
try {
    $built = New-WakeEngine
} catch {
    Write-NovaLog "wake listener failed to start: $($_.Exception.Message)" 'wake'
    exit 1
}
$engine = $built.Engine

Write-NovaLog "wake listener up -- recogniser '$($built.Recognizer.Id)', threshold $Threshold, $($WAKE.Count) wake phrases, $($DECOYS.Count) decoys" 'wake'

if ($TestOnce) {
    Write-Host ''
    Write-Host 'Say "Nova" now -- listening for 12 seconds...' -ForegroundColor Magenta
    $r = $engine.Recognize([TimeSpan]::FromSeconds(12))
    if ($null -eq $r) {
        Write-Host 'Heard nothing at all. Check that the microphone you speak into is the default input device.' -ForegroundColor Yellow
    } else {
        $hit = Test-IsWake $r.Text
        $verdict = if ($hit -and $r.Confidence -ge $Threshold) { 'WAKE' }
                   elseif ($hit) { "wake word, but below the $Threshold threshold" }
                   else { 'treated as a decoy, ignored' }
        $colour = if ($hit -and $r.Confidence -ge $Threshold) { 'Green' } else { 'Yellow' }
        Write-Host ("heard '{0}' at {1:P0} confidence -> {2}" -f $r.Text, $r.Confidence, $verdict) -ForegroundColor $colour
    }
    $engine.Dispose()
    exit 0
}

if ($DictateOnce) {
    Write-Host ''
    Write-Host 'Say a full sentence now -- transcribing for up to 15 seconds...' -ForegroundColor Magenta
    $text = Get-Utterance -Built $built -Seconds 60
    Write-Host ''
    if ($text) {
        Write-Host "  transcribed: `"$text`"" -ForegroundColor Green
        Write-Host ''
        Write-Host '  Dictation works. This is the path NOVA now uses for your requests.'
    } else {
        Write-Host '  Nothing was transcribed.' -ForegroundColor Yellow
        Write-Host '  The wake word and dictation share one input: the WINDOWS DEFAULT.'
        Write-Host '  Set your working microphone as the default input in Windows sound'
        Write-Host '  settings, then try again.'
    }
    Write-Host ''
    $engine.Dispose()
    exit 0
}

# ------------------------------------------------------------------ the loop
$lastFire = [datetime]::MinValue
$cooldown = [TimeSpan]::FromSeconds(2)

# Which engine transcribes your request. "browser" hands the job to Chrome,
# whose accuracy is far better than the desktop recogniser; this listener then
# only spots the wake word. "windows" keeps transcription here, for when the
# browser cannot reach the microphone.
$stt = 'browser'
try {
    $cfgPath = Join-Path $PSScriptRoot 'config.json'
    if (Test-Path $cfgPath) {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        if ($cfg.stt) { $stt = [string]$cfg.stt }
    }
} catch { }
Write-NovaLog "transcription engine: $stt" 'wake'

Set-EngineMode -Built $built -Mode 'wake'

try {
    while ($true) {

        # Did the panel just finish speaking? Then capture a follow-up without
        # making the user say the wake word again. In browser mode the panel
        # reopens its own microphone instead, so it never asks us.
        if ($stt -eq 'windows' -and (Test-NovaListen)) {
            Write-NovaLog 'follow-up window open' 'wake'
            $followUp = Get-Utterance -Built $built -Seconds 30
            if ($followUp) {
                [void](Send-NovaWake -Phrase 'follow-up' -Utterance $followUp)
                [void](Show-NovaPanel -Quiet)
            } else {
                Write-NovaLog 'follow-up window closed, nothing said' 'wake'
            }
            continue
        }

        $result = $null
        try {
            $result = $engine.Recognize([TimeSpan]::FromSeconds(6))
        } catch {
            Write-NovaLog "recognise error: $($_.Exception.Message)" 'wake'
            Start-Sleep -Seconds 2
            continue
        }

        if ($null -eq $result) { continue }

        $isWake = Test-IsWake $result.Text
        $confident = $result.Confidence -ge $Threshold

        if ($Verbose) {
            $tag = if ($isWake -and $confident) { ' -> WAKE' }
                   elseif ($isWake) { ' (below threshold)' }
                   else { ' (decoy)' }
            Write-NovaLog ("heard '{0}' {1:P0}{2}" -f $result.Text, $result.Confidence, $tag) 'wake'
        }

        if (-not ($isWake -and $confident)) { continue }
        if ((Get-Date) - $lastFire -lt $cooldown) { continue }
        $lastFire = Get-Date

        Write-NovaLog ("WAKE -- '{0}' at {1:P0}" -f $result.Text, $result.Confidence) 'wake'

        # Show the panel first so there is something on screen while you talk.
        # Logged either way: a wake that fires while the panel is closed and
        # fails to reopen looks exactly like a broken wake word from outside,
        # and that cost real debugging time once already.
        try {
            $wasOpen = [bool](Find-NovaWindow)
            $shown = Show-NovaPanel -Quiet
            if (-not $shown) {
                Write-NovaLog 'panel could NOT be opened -- nothing will record' 'wake'
            } elseif (-not $wasOpen) {
                Write-NovaLog 'panel was closed, reopened it' 'wake'
            }
        } catch {
            Write-NovaLog "summon failed: $($_.Exception.Message)" 'wake'
        }

        if ($stt -ne 'windows') {
            # Browser mode: just summon. Chrome does the transcribing, and it
            # is markedly better at it than the desktop recogniser.
            [void](Send-NovaWake -Phrase $result.Text)
            continue
        }

        # If the wake phrase already carried the request ("nova what is the
        # time"), use that and skip a second capture.
        $trailing = ($result.Text -replace '(?i)^\s*(hey|hello|okay|ok|yo)?\s*nova\b[,\s]*', '').Trim()

        $utterance = $null
        if ($trailing.Length -ge 4) {
            $utterance = $trailing
            Write-NovaLog "request came with the wake phrase: '$utterance'" 'wake'
        } else {
            $utterance = Get-Utterance -Built $built -Seconds 60
        }

        if ($utterance) {
            [void](Send-NovaWake -Phrase $result.Text -Utterance $utterance)
        } else {
            # Nothing said after the wake word: still summon, and let the panel
            # arm its own microphone as a fallback.
            Write-NovaLog 'nothing said after the wake word' 'wake'
            [void](Send-NovaWake -Phrase $result.Text)
        }
    }
} finally {
    if ($engine) { $engine.Dispose() }
    Write-NovaLog 'wake listener stopped' 'wake'
}
