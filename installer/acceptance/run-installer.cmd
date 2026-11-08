@echo off
setlocal
set "HORIZON_ACCEPTANCE_REPORT=%~dp0report"
if not exist "%HORIZON_ACCEPTANCE_REPORT%" mkdir "%HORIZON_ACCEPTANCE_REPORT%"
if not exist "%~dp0%~1" (
  echo Installer missing: %~1
  pause
  exit /b 2
)
set "RUN_ID=%~2-%RANDOM%-%RANDOM%"
echo Starting %~2. Keep the default installation directory.
start "" /wait "%~dp0%~1" /LOG="%HORIZON_ACCEPTANCE_REPORT%\%RUN_ID%.log"
set "INSTALL_RESULT=%ERRORLEVEL%"
>>"%HORIZON_ACCEPTANCE_REPORT%\installer-results.txt" echo %DATE% %TIME% %RUN_ID% exit=%INSTALL_RESULT%
call "%~dp03-collect.cmd" --quiet
echo Installer exit code: %INSTALL_RESULT%
echo Reports: "%HORIZON_ACCEPTANCE_REPORT%"
pause
exit /b %INSTALL_RESULT%
