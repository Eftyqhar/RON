' run_ron_silent.vbs — Silent Launcher for R.O.N. (Zero Console Window)
' Executes RON via pythonw.exe without displaying any Command Prompt or terminal.

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)

WshShell.CurrentDirectory = ScriptDir
' Parameter 0 = SW_HIDE (No console window, no taskbar button, completely silent)
WshShell.Run "pythonw.exe run_mini_hud.py", 0, False
