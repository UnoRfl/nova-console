' The "Nova" desktop shortcut: open the panel (or bring it to the front).
' Starts Nova first if the bridge isn't running. If she was switched off with
' the "Nova On-Off" shortcut, says so instead of quietly turning her back on.
' Run from Explorer, so nothing it starts dies with a terminal or Claude session.
Dim shell, fso, root, wmi, procs, p, bridge
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

If fso.FileExists(root & "\OFF") Then
    shell.Popup "Nova is OFF." & vbCrLf & vbCrLf & "Turn her on with the ""Nova On-Off"" shortcut first.", 6, "Nova", 48 + 4096
    WScript.Quit
End If

bridge = False
On Error Resume Next
Set wmi = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
Set procs = wmi.ExecQuery("Select CommandLine from Win32_Process where Name like 'python%'")
If Err.Number = 0 Then
    For Each p In procs
        If InStr(LCase(p.CommandLine & ""), "nova_bridge.py") > 0 Then bridge = True
    Next
End If
Err.Clear
On Error GoTo 0

' 0 = hidden window; True = wait so the bridge is up before the panel opens.
If Not bridge Then
    shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\Nova.ps1"" start -Direct", 0, True
End If
shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\Nova.ps1"" open", 0, False
