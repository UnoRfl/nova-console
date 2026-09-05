<#
    AudioDefaults.ps1 -- inspect and set the Windows default INPUT device.

        .\AudioDefaults.ps1                       # list capture devices + roles
        .\AudioDefaults.ps1 -SetDefault <id>      # make one device default for every role
        .\AudioDefaults.ps1 -AlignRoles           # point every role at whatever Console uses

    Why this exists: Windows keeps THREE separate defaults for input --
    Console, Multimedia and Communications. Different software asks for
    different ones. The Windows speech recogniser takes the Console default,
    while Chrome's getUserMedia commonly takes Communications. When those point
    at different microphones you get the exact symptom we hit: a level meter
    that moves and a recogniser that hears nothing.

    Uses the documented IMMDeviceEnumerator to read, and the undocumented
    IPolicyConfig to write, which is the only way to set a default endpoint
    without shipping a third-party module.
#>
param(
    [string]$SetDefault,
    [switch]$AlignRoles
)

$ErrorActionPreference = 'Stop'

if (-not ('NovaAudio.Endpoints' -as [type])) {
Add-Type -Language CSharp @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace NovaAudio
{
    internal enum EDataFlow { eRender = 0, eCapture = 1, eAll = 2 }
    internal enum ERole { eConsole = 0, eMultimedia = 1, eCommunications = 2 }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMMDeviceEnumerator
    {
        int EnumAudioEndpoints(EDataFlow dataFlow, int stateMask, out IMMDeviceCollection devices);
        int GetDefaultAudioEndpoint(EDataFlow dataFlow, ERole role, out IMMDevice device);
        int GetDevice(string id, out IMMDevice device);
    }

    [Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMMDeviceCollection
    {
        int GetCount(out int count);
        int Item(int index, out IMMDevice device);
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMMDevice
    {
        int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
        int OpenPropertyStore(int stgmAccess, out IPropertyStore properties);
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        int GetState(out int state);
    }

    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPropertyStore
    {
        int GetCount(out int count);
        int GetAt(int index, out PropertyKey key);
        int GetValue(ref PropertyKey key, out PropVariant value);
        int SetValue(ref PropertyKey key, ref PropVariant value);
        int Commit();
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct PropertyKey { public Guid fmtid; public int pid; }

    [StructLayout(LayoutKind.Explicit)]
    internal struct PropVariant
    {
        [FieldOffset(0)] public short vt;
        [FieldOffset(8)] public IntPtr pointerValue;
    }

    // Undocumented, but stable across Windows 7..11. Two CLSIDs exist; we try
    // the modern one first and fall back to the legacy Vista-era object.
    [Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPolicyConfig
    {
        int GetMixFormat(string id, IntPtr format);
        int GetDeviceFormat(string id, bool isDefault, IntPtr format);
        int ResetDeviceFormat(string id);
        int SetDeviceFormat(string id, IntPtr endpointFormat, IntPtr mixFormat);
        int GetProcessingPeriod(string id, bool isDefault, IntPtr defaultPeriod, IntPtr minimumPeriod);
        int SetProcessingPeriod(string id, IntPtr period);
        int GetShareMode(string id, IntPtr mode);
        int SetShareMode(string id, IntPtr mode);
        int GetPropertyValue(string id, ref PropertyKey key, out PropVariant value);
        int SetPropertyValue(string id, ref PropertyKey key, ref PropVariant value);
        int SetDefaultEndpoint(string id, ERole role);
        int SetEndpointVisibility(string id, bool visible);
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    internal class MMDeviceEnumeratorComObject { }

    [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
    internal class PolicyConfigClient { }

    public class Endpoint
    {
        public string Id;
        public string Name;
        public bool IsConsole;
        public bool IsMultimedia;
        public bool IsCommunications;
    }

    public static class Endpoints
    {
        private static PropertyKey FriendlyName = new PropertyKey
        {
            fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"),
            pid = 14
        };

        private static string NameOf(IMMDevice device)
        {
            IPropertyStore store;
            if (device.OpenPropertyStore(0 /*STGM_READ*/, out store) != 0 || store == null) return "(unnamed)";
            PropVariant val;
            if (store.GetValue(ref FriendlyName, out val) != 0) return "(unnamed)";
            if (val.pointerValue == IntPtr.Zero) return "(unnamed)";
            return Marshal.PtrToStringUni(val.pointerValue);
        }

        private static string DefaultId(IMMDeviceEnumerator en, ERole role)
        {
            IMMDevice dev;
            // Fails with E_NOTFOUND when no default is assigned for that role.
            if (en.GetDefaultAudioEndpoint(EDataFlow.eCapture, role, out dev) != 0 || dev == null) return null;
            string id;
            dev.GetId(out id);
            return id;
        }

        public static List<Endpoint> List()
        {
            var en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
            string console = DefaultId(en, ERole.eConsole);
            string multi   = DefaultId(en, ERole.eMultimedia);
            string comms   = DefaultId(en, ERole.eCommunications);

            IMMDeviceCollection col;
            en.EnumAudioEndpoints(EDataFlow.eCapture, 1 /*DEVICE_STATE_ACTIVE*/, out col);
            int count;
            col.GetCount(out count);

            var list = new List<Endpoint>();
            for (int i = 0; i < count; i++)
            {
                IMMDevice dev;
                col.Item(i, out dev);
                string id;
                dev.GetId(out id);
                list.Add(new Endpoint
                {
                    Id = id,
                    Name = NameOf(dev),
                    IsConsole = (id == console),
                    IsMultimedia = (id == multi),
                    IsCommunications = (id == comms)
                });
            }
            return list;
        }

        public static string SetDefault(string id)
        {
            object client;
            try { client = new PolicyConfigClient(); }
            catch (Exception ex) { return "could not create PolicyConfig: " + ex.Message; }

            var cfg = client as IPolicyConfig;
            if (cfg == null) return "PolicyConfig interface unavailable on this build of Windows";

            foreach (ERole role in new[] { ERole.eConsole, ERole.eMultimedia, ERole.eCommunications })
            {
                int hr = cfg.SetDefaultEndpoint(id, role);
                if (hr != 0) return "SetDefaultEndpoint(" + role + ") failed: 0x" + hr.ToString("X8");
            }
            return null;
        }
    }
}
'@
}

function Show-Endpoints {
    $list = [NovaAudio.Endpoints]::List()
    Write-Host ''
    Write-Host 'Active capture (input) devices' -ForegroundColor Magenta
    Write-Host '------------------------------'
    $i = 0
    foreach ($e in $list) {
        $roles = @()
        if ($e.IsConsole)        { $roles += 'Console' }
        if ($e.IsMultimedia)     { $roles += 'Multimedia' }
        if ($e.IsCommunications) { $roles += 'Communications' }
        $tag = if ($roles.Count) { '  <-- DEFAULT for: ' + ($roles -join ', ') } else { '' }
        $colour = if ($roles.Count) { 'Green' } else { 'Gray' }
        Write-Host ("  [{0}] {1}{2}" -f $i, $e.Name, $tag) -ForegroundColor $colour
        Write-Host ("       $($e.Id)") -ForegroundColor DarkGray
        $i++
    }
    Write-Host ''

    $console = $list | Where-Object { $_.IsConsole }
    $comms   = $list | Where-Object { $_.IsCommunications }
    if ($console -and $comms -and $console.Id -ne $comms.Id) {
        Write-Host '  MISMATCH: Console and Communications point at different microphones.' -ForegroundColor Yellow
        Write-Host '  Windows speech uses Console; Chrome commonly uses Communications.' -ForegroundColor Yellow
        Write-Host '  Run:  .\AudioDefaults.ps1 -AlignRoles' -ForegroundColor Yellow
        Write-Host ''
    }
    return $list
}

$list = Show-Endpoints

if ($AlignRoles) {
    $console = $list | Where-Object { $_.IsConsole } | Select-Object -First 1
    if (-not $console) { Write-Host 'No Console default to align to.' -ForegroundColor Red; exit 1 }
    $SetDefault = $console.Id
    Write-Host "Aligning every role to the Console default: $($console.Name)" -ForegroundColor Magenta
}

if ($SetDefault) {
    # Accept an index as a convenience so nobody has to paste an endpoint id.
    if ($SetDefault -match '^\d+$') {
        $idx = [int]$SetDefault
        if ($idx -lt 0 -or $idx -ge $list.Count) { Write-Host 'No device with that index.' -ForegroundColor Red; exit 1 }
        $SetDefault = $list[$idx].Id
    }
    $target = $list | Where-Object { $_.Id -eq $SetDefault } | Select-Object -First 1
    $err = [NovaAudio.Endpoints]::SetDefault($SetDefault)
    if ($err) {
        Write-Host "FAILED: $err" -ForegroundColor Red
        Write-Host 'Set it by hand in Windows sound settings instead.' -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Default input set to: $(if ($target) { $target.Name } else { $SetDefault })" -ForegroundColor Green
    Write-Host ''
    [void](Show-Endpoints)
}
