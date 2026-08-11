@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_windows.ps1"
set "PJM_QR_EXIT=%ERRORLEVEL%"

if not "%PJM_QR_EXIT%"=="0" (
  echo.
  echo PJM QR Operator stopped with an error. Review the message above.
  pause
)

exit /b %PJM_QR_EXIT%
