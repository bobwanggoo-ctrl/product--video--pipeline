@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
chcp 65001 >nul 2>&1

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║  Product Video Pipeline  发布构建        ║
echo  ╚══════════════════════════════════════════╝
echo.

:: ── 版本号（改这里即可）──────────────────────────────
set VERSION=1.0
set OUT_DIR=dist\ProductVideoPipeline
set ZIP_NAME=ProductVideoPipeline_v%VERSION%.zip

:: ── 1. 检查 Python ──────────────────────────────────
echo [1/6] 检查 Python...
set PYTHON=
for %%c in (python python3 py) do (
    if not defined PYTHON (
        %%c --version >nul 2>&1 && set PYTHON=%%c
    )
)
if not defined PYTHON (
    echo [ERR] 找不到 Python，请先安装 Python 3.10+
    pause & exit /b 1
)
echo       Python: %PYTHON%

:: ── 2. 安装依赖 ──────────────────────────────────────
echo [2/6] 安装/更新依赖...
%PYTHON% -m pip install -r requirements.txt -q
if %ERRORLEVEL% NEQ 0 (echo [ERR] 依赖安装失败 & pause & exit /b 1)
echo       依赖安装完成

:: ── 3. PyInstaller 打包 ──────────────────────────────
echo [3/6] PyInstaller 打包...
%PYTHON% -m PyInstaller product_video_pipeline.spec --noconfirm --clean
if %ERRORLEVEL% NEQ 0 (echo [ERR] PyInstaller 失败 & pause & exit /b 1)
echo       PyInstaller 完成

:: ── 4. 下载并内嵌 FFmpeg ─────────────────────────────
echo [4/6] 准备 FFmpeg...
if exist "%OUT_DIR%\ffmpeg.exe" (
    echo       FFmpeg 已存在，跳过下载
) else (
    set FFMPEG_ZIP=ffmpeg_temp.zip
    set FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip

    echo       正在下载 FFmpeg（约 70MB）...
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%FFMPEG_URL%' -OutFile '%FFMPEG_ZIP%' -UseBasicParsing}" 2>nul
    if not exist "%FFMPEG_ZIP%" (
        echo [WARN] FFmpeg 下载失败，请手动将 ffmpeg.exe 和 ffprobe.exe 放入 %OUT_DIR%\
        goto :skip_ffmpeg
    )

    echo       解压 FFmpeg...
    powershell -Command "& {Add-Type -AssemblyName System.IO.Compression.FileSystem; $z=[System.IO.Compression.ZipFile]::OpenRead('%FFMPEG_ZIP%'); foreach($e in $z.Entries){if($e.Name -eq 'ffmpeg.exe' -or $e.Name -eq 'ffprobe.exe'){[System.IO.Compression.ZipFileExtensions]::ExtractToFile($e,'%OUT_DIR%\'+$e.Name,$true)}}; $z.Dispose()}" 2>nul
    del "%FFMPEG_ZIP%" >nul 2>&1

    if exist "%OUT_DIR%\ffmpeg.exe" (
        echo       FFmpeg 内嵌完成
    ) else (
        echo [WARN] FFmpeg 解压失败，请手动将 ffmpeg.exe 和 ffprobe.exe 放入 %OUT_DIR%\
    )
)
:skip_ffmpeg

:: ── 5. 生成预配置 .env ───────────────────────────────
echo [5/6] 生成预配置 .env...

:: 读取当前 .env 中的共享 API 配置（不含 Kling 密钥和 ADMIN_MODE）
set RP_KEY=
set RP_BASE=https://api.tu-zi.com/v1
set RP_MODEL=gemini-3-flash-preview
set AINAV_TOKEN=
set AINAV_URL=http://yswg.love:15091/api/admin
set AINAV_IMG_APP=2038805674553368579
set AINAV_IMG_GID=3
set AINAV_LLM_APP=2038805674553368579
set AINAV_LLM_GID=13
set GVISION_KEY=
set KLING_URL=https://api-beijing.klingai.com
set KLING_MOD=kling-v2-5-turbo
set KLING_CONCUR=5
set APP_TASKS=3

:: 从本地 .env 提取值
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="REVERSE_PROMPT_API_KEY"    set RP_KEY=%%b
        if "%%a"=="REVERSE_PROMPT_BASE_URL"   set RP_BASE=%%b
        if "%%a"=="REVERSE_PROMPT_MODEL"      set RP_MODEL=%%b
        if "%%a"=="AI_NAV_TOKEN"              set AINAV_TOKEN=%%b
        if "%%a"=="AI_NAV_BASE_URL"           set AINAV_URL=%%b
        if "%%a"=="AI_NAV_IMAGE_APP_ID"       set AINAV_IMG_APP=%%b
        if "%%a"=="AI_NAV_IMAGE_GROUP_ID"     set AINAV_IMG_GID=%%b
        if "%%a"=="AI_NAV_LLM_APP_ID"         set AINAV_LLM_APP=%%b
        if "%%a"=="AI_NAV_LLM_GROUP_ID"       set AINAV_LLM_GID=%%b
        if "%%a"=="GOOGLE_VISION_API_KEY"     set GVISION_KEY=%%b
        if "%%a"=="KLING_BASE_URL"            set KLING_URL=%%b
        if "%%a"=="KLING_MODEL"               set KLING_MOD=%%b
        if "%%a"=="KLING_MAX_CONCURRENT"      set KLING_CONCUR=%%b
        if "%%a"=="APP_MAX_RUNNING_TASKS"     set APP_TASKS=%%b
    )
)

:: 写入 .env（KLING 密钥留空，ADMIN_MODE 固定 false）
(
echo # ============================================================
echo # Product Video Pipeline - 环境配置
echo # ============================================================
echo.
echo # --- LLM: Reverse Prompt (tu-zi 中转) ---
echo REVERSE_PROMPT_API_KEY=!RP_KEY!
echo REVERSE_PROMPT_BASE_URL=!RP_BASE!
echo REVERSE_PROMPT_PATH=/chat/completions
echo REVERSE_PROMPT_MODEL=!RP_MODEL!
echo.
echo # --- AI导航 (yswg) ---
echo AI_NAV_BASE_URL=!AINAV_URL!
echo AI_NAV_TOKEN=!AINAV_TOKEN!
echo AI_NAV_IMAGE_APP_ID=!AINAV_IMG_APP!
echo AI_NAV_IMAGE_GROUP_ID=!AINAV_IMG_GID!
echo AI_NAV_LLM_APP_ID=!AINAV_LLM_APP!
echo AI_NAV_LLM_GROUP_ID=!AINAV_LLM_GID!
echo.
echo # --- Kling AI 视频生成（请填入您的密钥）---
echo KLING_ACCESS_KEY=
echo KLING_SECRET_KEY=
echo KLING_BASE_URL=!KLING_URL!
echo KLING_MODEL=!KLING_MOD!
echo KLING_MODE=std
echo KLING_DURATION=5
echo KLING_ASPECT_RATIO=16:9
echo.
echo # --- Google Cloud Vision API ---
echo GOOGLE_VISION_API_KEY=!GVISION_KEY!
echo.
echo # --- 并发控制 ---
echo KLING_MAX_CONCURRENT=!KLING_CONCUR!
echo APP_MAX_RUNNING_TASKS=!APP_TASKS!
echo.
echo # --- 管理员模式（普通用户保持 false）---
echo ADMIN_MODE=false
echo.
echo # --- 其他 ---
echo HTTPS_PROXY=
echo LOG_LEVEL=INFO
) > "%OUT_DIR%\.env"
echo       .env 已生成

:: ── 复制素材目录 ─────────────────────────────────────
if exist "input\music"  xcopy /E /I /Q "input\music"  "%OUT_DIR%\input\music"  >nul
if exist "input\fonts"  xcopy /E /I /Q "input\fonts"  "%OUT_DIR%\input\fonts"  >nul
if exist "assets"       xcopy /E /I /Q "assets"       "%OUT_DIR%\assets"       >nul
if not exist "%OUT_DIR%\output" mkdir "%OUT_DIR%\output"

:: ── 创建快捷方式和说明 ───────────────────────────────
echo [6/6] 创建快捷方式和说明...

:: 双击启动 GUI
(
echo @echo off
echo start "" "%%~dp0ProductVideoPipeline.exe"
) > "%OUT_DIR%\打开应用.bat"

:: Kling API 配置助手
(
echo @echo off
echo chcp 65001 ^>nul
echo cd /d "%%~dp0"
echo echo.
echo echo === Kling API 配置 ===
echo echo.
echo echo 请前往 https://klingai.com 获取您的 API 密钥
echo echo.
echo set /p AK=请输入 KLING_ACCESS_KEY:
echo set /p SK=请输入 KLING_SECRET_KEY:
echo.
echo :: 写入 .env
echo powershell -Command "& {$c=Get-Content '.env'; $c=$c -replace '^KLING_ACCESS_KEY=.*','KLING_ACCESS_KEY='+$env:AK; $c=$c -replace '^KLING_SECRET_KEY=.*','KLING_SECRET_KEY='+$env:SK; $c | Set-Content '.env' -Encoding UTF8}"
echo echo.
echo echo 配置已保存！重新打开应用生效。
echo pause
) > "%OUT_DIR%\配置Kling密钥.bat"

:: 使用说明
(
echo Product Video Pipeline v%VERSION%
echo ================================
echo.
echo 【首次使用】
echo 1. 双击「配置Kling密钥.bat」填入您的 Kling API 密钥
echo 2. 双击「打开应用.bat」或直接双击 ProductVideoPipeline.exe 启动
echo.
echo 【文件夹说明】
echo   input\music\  — 放入 BGM 音乐文件（mp3/wav），按风格分子文件夹
echo   input\fonts\  — 放入字体文件（ttf/otf）
echo   output\       — 所有输出文件保存在此
echo.
echo 【输出结构】
echo   output\任务名\
echo     任务名.mp4              成品视频
echo     任务名-工程文件.fcpxml  Final Cut Pro 工程
echo     任务名-Premiere.xml    Premiere Pro 工程
echo     任务名-剪映工程\        剪映草稿（拖入剪映草稿目录）
echo     任务名-附件\            字幕、参考 JSON 等附件
echo.
echo 【问题反馈】请联系管理员
) > "%OUT_DIR%\使用说明.txt"

echo       快捷方式和说明已创建

:: ── 打包成 ZIP ───────────────────────────────────────
echo.
echo 正在打包 %ZIP_NAME%...
if exist "%ZIP_NAME%" del "%ZIP_NAME%"
powershell -Command "& {Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('%OUT_DIR%','%ZIP_NAME%')}" 2>nul
if exist "%ZIP_NAME%" (
    for %%f in ("%ZIP_NAME%") do set SIZE=%%~zf
    set /a SIZE_MB=!SIZE!/1048576
    echo.
    echo  ╔══════════════════════════════════════════╗
    echo  ║  构建完成！                              ║
    echo  ║  输出: %ZIP_NAME%
    echo  ║  大小: ~!SIZE_MB! MB                         ║
    echo  ╚══════════════════════════════════════════╝
) else (
    echo [WARN] ZIP 打包失败，但文件夹已就绪: %OUT_DIR%
    echo       可手动压缩该文件夹发给同事
)
echo.
pause
