@echo off
setlocal
 title Horizon
set "HORIZON_CODE=%~dp0."
set "HORIZON_ARCHIVE=%~dp0..\horizon-archive"
set "HORIZON_PYTHON=%HORIZON_CODE%\runtime\python.exe"
if exist "%HORIZON_PYTHON%" goto ready
set "HORIZON_PYTHON=%HORIZON_CODE%\.venv\Scripts\python.exe"
if exist "%HORIZON_PYTHON%" goto ready
set "HORIZON_PYTHON=%HORIZON_CODE%\.horizon\.venv\Scripts\python.exe"
if exist "%HORIZON_PYTHON%" goto ready
echo Horizon Python runtime is missing.
goto failed
:ready
cd /d "%HORIZON_CODE%"
if not "%~1"=="" goto command
start "Horizon" "%HORIZON_PYTHON:python.exe=pythonw.exe%" -X utf8 -m hostrun.desktop --code "%HORIZON_CODE%" --archive "%HORIZON_ARCHIVE%"
exit /b 0
:command
"%HORIZON_PYTHON%" -X utf8 -m hostrun.cli --code "%HORIZON_CODE%" --archive "%HORIZON_ARCHIVE%" %*
exit /b %errorlevel%
:failed
echo.
echo Horizon could not start. Please review the error above.
pause
exit /b 1
