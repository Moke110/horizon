@echo off
setlocal
set "HORIZON_ACCEPTANCE_REPORT=%~dp0report"
if not exist "%HORIZON_ACCEPTANCE_REPORT%" mkdir "%HORIZON_ACCEPTANCE_REPORT%"
set "HORIZON_ACCEPTANCE_CODE=%LOCALAPPDATA%\Programs\Horizon"
if not defined HORIZON_DATA_HOME set "HORIZON_DATA_HOME=%LOCALAPPDATA%\Horizon\data"
set "COLLECT_RESULT=unavailable"
if exist "%HORIZON_ACCEPTANCE_CODE%\hostrun\diagnostics.py" if exist "%HORIZON_ACCEPTANCE_CODE%\runtime\python.exe" (
  "%HORIZON_ACCEPTANCE_CODE%\runtime\python.exe" -X utf8 -m hostrun.diagnostics --code "%HORIZON_ACCEPTANCE_CODE%" --output "%HORIZON_ACCEPTANCE_REPORT%\application"
  if not errorlevel 1 set "COLLECT_RESULT=exported"
)
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; $r=$env:HORIZON_ACCEPTANCE_REPORT; $v=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'; $arch=$env:PROCESSOR_ARCHITECTURE; if(!$arch){$arch=if([Environment]::Is64BitOperatingSystem){'64-bit'}else{'32-bit'}}; $s=[ordered]@{schema_version=1;collected_at=[DateTime]::UtcNow.ToString('o');os='Windows';version=([Environment]::OSVersion.Version.ToString());architecture=$arch;display_version=$v.DisplayVersion;build=$v.CurrentBuild;update=$v.UBR;application_export=$env:COLLECT_RESULT}; $s|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $r 'system.json') -Encoding UTF8; $lines=@('Horizon acceptance report',('Collected: '+$s.collected_at),('OS: '+$s.os+' '+$s.version+' '+$s.architecture),('Application export: '+$env:COLLECT_RESULT),'Installer outcomes: installer-results.txt','Installation details: install-*.log and upgrade-*.log','Sanitized application reports: application/','No credentials, conversation bodies, database or session transcripts are intentionally collected.'); $lines|Set-Content -LiteralPath (Join-Path $r 'SUMMARY.txt') -Encoding UTF8"
if errorlevel 1 echo System summary collection failed. Existing installer logs remain in report.
echo Reports: "%HORIZON_ACCEPTANCE_REPORT%"
if not "%~1"=="--quiet" pause
