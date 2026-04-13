@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Updating Product Video Pipeline...
echo.

:: Check if git is available
where git >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERR] Git not found. Please install Git first:
    echo       https://git-scm.com/download/win
    pause
    exit /b 1
)

:: Pull latest code
git pull origin master
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERR] Update failed. Check your network connection.
    pause
    exit /b 1
)

echo.
echo Update complete. You can now run run.bat
pause
