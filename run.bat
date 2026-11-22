@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Python environment not found. Creating it and installing dependencies...
  python -m venv .venv
  if errorlevel 1 goto :failed
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 goto :failed
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :failed
)

echo Starting watchlist email supervisor. Keep this window open.
echo Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m watchlist_mailer serve
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" echo The supervisor stopped with code %CODE%. See logs\mailer.log.
pause
exit /b %CODE%

:failed
echo.
echo Setup failed. Check the messages above, then run this file again.
pause
exit /b 1
