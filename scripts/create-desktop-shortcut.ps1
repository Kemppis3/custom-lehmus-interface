# Creates a "Lehmus Chat" shortcut on the Windows desktop.
# Run: right-click > "Run with PowerShell"   (or: powershell -ExecutionPolicy Bypass -File scripts\create-desktop-shortcut.ps1)
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop "Lehmus Chat.lnk"))
$lnk.TargetPath = Join-Path $root "start.bat"
$lnk.WorkingDirectory = $root
$lnk.WindowStyle = 7   # start minimized
$ico = Join-Path $root "app\static\icon.ico"
if (Test-Path $ico) { $lnk.IconLocation = $ico } else { $lnk.IconLocation = "$env:SystemRoot\System32\imageres.dll,76" }
$lnk.Description = "Chat with the Lehmus AI models"
$lnk.Save()
Write-Host "Shortcut created on the desktop: Lehmus Chat"
