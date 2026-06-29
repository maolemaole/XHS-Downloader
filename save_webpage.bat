@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    set /p "INPUT=Paste a webpage URL, or enter a URL-list file: "
    if not defined INPUT (
        echo No URL was entered.
        pause
        exit /b 1
    )
    set "SCRIPT_ARGS="%INPUT%""
) else (
    set "SCRIPT_ARGS=%*"
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\save_webpage.py" %SCRIPT_ARGS%
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "scripts\save_webpage.py" %SCRIPT_ARGS%
) else (
    uv run python "scripts\save_webpage.py" %SCRIPT_ARGS%
)

set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo The saver exited with code %RESULT%.
pause
exit /b %RESULT%
