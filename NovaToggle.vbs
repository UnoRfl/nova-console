' The "Nova On-Off" shortcut. Flips her between off (stays off: no auto-start
' at login, no 5-minute self-heal) and on, then says which it is now.
' Run from Explorer, so it sits in no job object and can start her directly.
Dim shell, fso, root, msg
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\Nova.ps1"" toggle -Direct", 0, True

If fso.FileExists(root & "\OFF") Then
    msg = "Nova is OFF." & vbCrLf & vbCrLf & "She will stay off, restarts included, until you run this again."
Else
    msg = "Nova is ON."
End If
' Closes itself after 4 seconds; 64 = information icon, 4096 = on top.
shell.Popup msg, 4, "Nova", 64 + 4096
