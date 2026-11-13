' Opened through Explorer (explorer.exe NovaLaunch.vbs) by the "Nova"
' scheduled task (login + every 5 min) and by Nova.ps1 start from a Claude
' shell. Explorer is in no job object, so nothing started here dies when a
' Claude session, terminal or Task Scheduler job closes.
'
' Quiet check first (Sir, 2026-11-13: the 5-minute PowerShell launches stole
' focus while he was gaming). Everything is looked up with WMI from this
' windowless script; a PowerShell console is created ONLY for a piece that is
' actually missing, so a healthy Nova starts no console at all.
Dim shell, fso, root, wmi, procs, p, cl
Dim bridge, ears, watch, orb, mic, bot, botSup
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

bridge = False : ears = False : watch = False : orb = False
mic = False : bot = False : botSup = False
On Error Resume Next
Set wmi = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
Set procs = wmi.ExecQuery("Select CommandLine from Win32_Process where Name like 'python%' or Name = 'powershell.exe'")
If Err.Number <> 0 Then
    ' WMI unavailable: fall back to the old always-run behaviour.
    Err.Clear
    bridge = False : watch = False : mic = False : bot = False
Else
    For Each p In procs
        cl = LCase(p.CommandLine & "")
        If InStr(cl, "nova_bridge.py") > 0 Then bridge = True
        If InStr(cl, "nova_ears.py") > 0 Or InStr(cl, "nova-wake.ps1") > 0 Then ears = True
        If InStr(cl, "nova-watch.ps1") > 0 Then watch = True
        If InStr(cl, "nova_orb.py") > 0 Then orb = True
        If InStr(cl, "audiodefaults.ps1") > 0 And InStr(cl, "-keep") > 0 Then mic = True
        If InStr(cl, "nova-bot") > 0 And InStr(cl, "watch.ps1") > 0 Then botSup = True
        If Right(Replace(Trim(cl), """", ""), 6) = "bot.py" Then bot = True
    Next
End If
On Error GoTo 0

If Not fso.FileExists(root & "\nova_orb.py") Then orb = True

' 0 = hidden window. Nova.ps1 start only starts missing pieces.
If Not (bridge And ears And watch And orb) Then
    shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\Nova.ps1"" start -Direct", 0, True
End If
If Not mic Then
    shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\AudioDefaults.ps1"" -Keep K18", 0, False
End If
If Not (bot Or botSup) Then
    shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\EnsureBot.ps1""", 0, False
End If
