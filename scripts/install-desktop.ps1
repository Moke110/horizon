# Install a local, console-free shortcut; no system security policy changes.
param([string]$ShortcutDirectory)
$ErrorActionPreference = 'Stop'
$code = Split-Path -Parent $PSScriptRoot
$python = @('runtime\pythonw.exe', '.venv\Scripts\pythonw.exe', '.frontier\.venv\Scripts\pythonw.exe') | ForEach-Object { Join-Path $code $_ } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) { throw 'Frontier pythonw runtime is missing.' }
$archive = Join-Path (Split-Path -Parent $code) 'frontier-archive'
$shell = New-Object -ComObject WScript.Shell
$destinations = @((Join-Path $code 'Frontier.lnk'))
if ($ShortcutDirectory) {
    $destinations += Join-Path (Resolve-Path -LiteralPath $ShortcutDirectory) '启动 Frontier.lnk'
}
foreach ($destination in $destinations) {
    $link = $shell.CreateShortcut($destination)
    $link.TargetPath = $python
    $link.Arguments = '-X utf8 -m hostrun.desktop --code "' + $code + '" --archive "' + $archive + '"'
    $link.WorkingDirectory = $code
    $link.Description = 'Frontier Research Agent'
    $link.Save()
    Unblock-File -LiteralPath $destination
    Write-Output $destination
}
Unblock-File -LiteralPath $python
