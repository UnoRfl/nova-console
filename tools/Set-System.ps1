<#
    Set-System.ps1 -- the machine's knobs, so Nova can turn them herself.

    Everything here is a thing Sir would otherwise have to reach over and do:
    volume, the media keys, locking, sleeping, blanking the displays. Each is a
    single named action rather than improvised PowerShell, for the same reason
    the other helpers exist -- these are tested, they avoid the broken WMI
    repository on this machine, and they print one clean sentence back.

    Volume goes through the Core Audio API directly. There is no built-in
    cmdlet for it, and the usual SendKeys trick only moves in coarse steps and
    fails when the panel does not have focus.

        .\Set-System.ps1 -Volume 35
        .\Set-System.ps1 -VolumeUp 10
        .\Set-System.ps1 -ToggleMute
        .\Set-System.ps1 -Media PlayPause
        .\Set-System.ps1 -Lock
        .\Set-System.ps1                     # report the current state
#>
[CmdletBinding()]
param(
    [ValidateRange(0, 100)] [int] $Volume = -1,
    [ValidateRange(1, 100)] [int] $VolumeUp = 0,
    [ValidateRange(1, 100)] [int] $VolumeDown = 0,
    [switch] $Mute,
    [switch] $Unmute,
    [switch] $ToggleMute,
    [ValidateSet('PlayPause', 'Next', 'Previous', 'Stop')] [string] $Media,
    [switch] $Lock,
    [switch] $Sleep,
    [switch] $DisplayOff,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'

if (-not ('NovaAudio' -as [type])) {
    Add-Type -Language CSharp @'
using System;
using System.Runtime.InteropServices;

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
    int NotUsed1(); int NotUsed2(); int NotUsed3(); int NotUsed4();
    int SetMasterVolumeLevelScalar(float level, Guid ctx);
    int NotUsed5();
    int GetMasterVolumeLevelScalar(out float level);
    int NotUsed6(); int NotUsed7(); int NotUsed8(); int NotUsed9();
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, Guid ctx);
    int GetMute(out bool mute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, out IAudioEndpointVolume endpointVolume);
}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int NotUsed();
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumeratorComObject { }

public static class NovaAudio {
    static IAudioEndpointVolume Endpoint() {
        IMMDeviceEnumerator en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice dev;
        Marshal.ThrowExceptionForHR(en.GetDefaultAudioEndpoint(0, 1, out dev));   // render, console
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        IAudioEndpointVolume vol;
        Marshal.ThrowExceptionForHR(dev.Activate(ref iid, 23, IntPtr.Zero, out vol));
        return vol;
    }
    public static int GetVolume() {
        float v; Marshal.ThrowExceptionForHR(Endpoint().GetMasterVolumeLevelScalar(out v));
        return (int)Math.Round(v * 100f);
    }
    public static void SetVolume(int percent) {
        if (percent < 0) percent = 0; if (percent > 100) percent = 100;
        Marshal.ThrowExceptionForHR(Endpoint().SetMasterVolumeLevelScalar(percent / 100f, Guid.Empty));
    }
    public static bool GetMute() {
        bool m; Marshal.ThrowExceptionForHR(Endpoint().GetMute(out m)); return m;
    }
    public static void SetMute(bool mute) {
        Marshal.ThrowExceptionForHR(Endpoint().SetMute(mute, Guid.Empty));
    }
}

public static class NovaKeys {
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    public static void Tap(byte vk) {
        keybd_event(vk, 0, 0, UIntPtr.Zero);            // down
        keybd_event(vk, 0, 2, UIntPtr.Zero);            // up
    }
    public static void DisplayOff() {
        // Broadcast, monitor power, low power state.
        SendMessage((IntPtr)0xFFFF, 0x0112, (IntPtr)0xF170, (IntPtr)2);
    }
}
'@
}

$did = @()

if ($Volume -ge 0)   { [NovaAudio]::SetVolume($Volume);                       $did += "volume set to $Volume percent" }
if ($VolumeUp -gt 0) { [NovaAudio]::SetVolume([NovaAudio]::GetVolume() + $VolumeUp);   $did += "volume up $VolumeUp" }
if ($VolumeDown -gt 0){ [NovaAudio]::SetVolume([NovaAudio]::GetVolume() - $VolumeDown); $did += "volume down $VolumeDown" }
if ($Mute)       { [NovaAudio]::SetMute($true);                    $did += 'muted' }
if ($Unmute)     { [NovaAudio]::SetMute($false);                   $did += 'unmuted' }
if ($ToggleMute) { $m = -not [NovaAudio]::GetMute(); [NovaAudio]::SetMute($m); $did += $(if ($m) { 'muted' } else { 'unmuted' }) }

if ($Media) {
    $vk = switch ($Media) { 'PlayPause' { 0xB3 } 'Next' { 0xB0 } 'Previous' { 0xB1 } 'Stop' { 0xB2 } }
    [NovaKeys]::Tap([byte]$vk)
    $did += "media $Media"
}

if ($DisplayOff) { [NovaKeys]::DisplayOff(); $did += 'displays off' }
if ($Lock)  { Start-Process -FilePath 'rundll32.exe' -ArgumentList 'user32.dll,LockWorkStation' -NoNewWindow; $did += 'workstation locked' }
if ($Sleep) { Start-Process -FilePath 'rundll32.exe' -ArgumentList 'powrprof.dll,SetSuspendState 0,1,0' -NoNewWindow; $did += 'sleeping' }

$state = [ordered]@{
    volume  = [NovaAudio]::GetVolume()
    muted   = [NovaAudio]::GetMute()
    applied = $did
}

if ($Json) { $state | ConvertTo-Json -Compress }
else {
    if ($did.Count) { Write-Output ($did -join ', ') }
    Write-Output ("volume {0} percent, {1}" -f $state.volume, $(if ($state.muted) { 'muted' } else { 'not muted' }))
}
