@echo off
REM 产品视频流水线 - Windows 入口
REM 双击即可：首次自动装环境 → 平时直接启动 GUI

REM 切换终端到 UTF-8（否则中文显示乱码）
chcp 65001 >nul 2>&1
setlocal

REM 切到脚本所在目录
cd /d "%~dp0"

REM 首次运行跑 bootstrap（检测 Python/ffmpeg/venv，弹 GUI 填 Key）
if not exist ".setup_done" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "tools\bootstrap_win.ps1"
    if errorlevel 1 (
        echo.
        echo 安装未完成，按任意键退出…
        pause >nul
        exit /b 1
    )
)

REM 启动 GUI
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m gui.app
    if errorlevel 1 (
        echo.
        echo GUI 异常退出。按任意键关闭此窗口…
        pause >nul
    )
) else (
    echo 错误：找不到 .venv\Scripts\python.exe
    echo 请删除 .setup_done 后重新双击本文件。
    pause >nul
    exit /b 1
)

endlocal
