' Opened through Explorer (explorer.exe NovaLaunch.vbs) by the "Nova"
' scheduled task (login + every 5 min) and by Nova.ps1 start from a Claude
' shell. Explorer is in no job object, so nothing started here dies when a
' Claude session, terminal or Task Scheduler job closes. Both commands are
' idempotent: Nova.ps1 start only starts missing pieces, and the mic keeper
' holds a mutex so a second copy exits at once.
Dim shell, fso, root
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\Nova.ps1"" start -Direct", 0, True
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\AudioDefaults.ps1"" -Keep K18", 0, False
shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\EnsureBot.ps1""", 0, False
