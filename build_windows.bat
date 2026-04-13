@echo off
:: Build ProductVideoPipeline.exe for Windows
cd /d "%~dp0"

echo === Product Video Pipeline - Windows Build ===

:: 1. Install deps
echo [1/3] Installing dependencies...
pip install -r requirements.txt -q

:: 2. Build
echo [2/3] Running PyInstaller...
python -m PyInstaller product_video_pipeline.spec --noconfirm --clean
if %ERRORLEVEL% NEQ 0 (
    echo [ERR] PyInstaller failed
    pause
    exit /b 1
)

:: 3. Post-process: copy runtime assets next to .exe
set OUT=dist\ProductVideoPipeline
echo [3/3] Copying runtime assets...
if not exist "%OUT%\input\fonts" mkdir "%OUT%\input\fonts"
if not exist "%OUT%\input\music" mkdir "%OUT%\input\music"
if not exist "%OUT%\output"      mkdir "%OUT%\output"

if exist "input\fonts"  xcopy /E /I /Q "input\fonts"  "%OUT%\input\fonts"
if exist "input\music"  xcopy /E /I /Q "input\music"  "%OUT%\input\music"
if exist ".env.example" copy ".env.example" "%OUT%\.env.example"
if exist ".env"         copy ".env"         "%OUT%\.env"

echo.
echo Build complete: dist\ProductVideoPipeline\
echo.
echo To distribute: zip the dist\ProductVideoPipeline folder
pause
