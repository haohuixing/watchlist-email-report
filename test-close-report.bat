@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo.
    echo Python environment not found.
    echo Run run.bat once to create .venv and install dependencies, then close it.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo .env not found.
    echo Copy .env.example to .env and fill in SMTP_USERNAME, SMTP_PASSWORD, and MAIL_TO.
    echo.
    pause
    exit /b 1
)

if /I "%~1"=="batches" (
    echo Sending the Market close report using the batch size configured in .env...
    echo With 415 rows and REPORT_BATCH_SIZE=50, this sends 9 emails.
) else (
    set "REPORT_BATCH_SIZE=500"
    echo Sending one combined Market close test email with all watchlist rows...
    echo REPORT_BATCH_SIZE is overridden to 500 for this run only.
)

echo.
echo This sends real email now. The scheduled 4:00 PM slot will NOT be marked as sent.
echo Make sure MAIL_TO points to the address you want to test.
echo.
"%PYTHON%" -m watchlist_mailer send --slot close
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (
    echo Test send completed successfully.
) else (
    echo Test send failed with exit code %CODE%. See logs\mailer.log.
)
echo.
pause
exit /b %CODE%
