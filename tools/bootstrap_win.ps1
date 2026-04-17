# 首次环境安装脚本（Windows） — 由 "开始使用.bat" 调用
# 职责：检 Python / ffmpeg -> 建 venv -> 装 Python 依赖 -> 调 first_run_wizard
# 成功后写 .setup_done 标记，下次启动会跳过这里

$ErrorActionPreference = 'Stop'

function Log  ($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "  [X] $m" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host ""
Write-Host "  +-------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |   产品视频流水线 - 首次环境安装（约5分钟） |" -ForegroundColor Cyan
Write-Host "  +-------------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Python ----
Log "检查 Python（需要 3.10 或更高版本）"
$PythonCmd = $null
foreach ($cmd in @('python', 'py', 'python3')) {
    try {
        $null = & $cmd --version 2>$null
        & $cmd -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $PythonCmd = $cmd; break }
    } catch {}
}

if (-not $PythonCmd) {
    Warn "没找到 Python 3.10+。尝试用 winget 安装..."
    try {
        winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements
        Warn "Python 安装完成。请关闭此窗口，重新双击 开始使用.bat"
        Read-Host "按回车退出"
        exit 0
    } catch {
        Warn "winget 不可用或安装失败。正在打开官网下载页..."
        Start-Process "https://www.python.org/downloads/windows/"
        Die "请手动下载 Python（勾选 'Add Python to PATH'），装完重新运行本脚本"
    }
}
$pyver = & $PythonCmd --version 2>&1
Ok "找到 $PythonCmd ($pyver)"

# ---- 2. ffmpeg ----
Log "检查 FFmpeg"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Ok "FFmpeg 已安装"
} else {
    Warn "未找到 FFmpeg，尝试用 winget 安装..."
    $installed = $false
    try {
        winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
        $installed = $true
    } catch {}

    if ($installed) {
        # 刷新环境变量
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                    [System.Environment]::GetEnvironmentVariable('Path','User')
        if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
            Ok "FFmpeg 安装完成"
        } else {
            Warn "FFmpeg 已装，但 PATH 未刷新。请关闭窗口重开一次"
            Read-Host "按回车退出"
            exit 0
        }
    } else {
        Warn "winget 不可用或安装失败。正在打开手动下载页..."
        Start-Process "https://www.gyan.dev/ffmpeg/builds/"
        Write-Host ""
        Write-Host "  手动安装步骤：" -ForegroundColor Yellow
        Write-Host "    1. 下载 'release full' 版本 ZIP"
        Write-Host "    2. 解压到某个稳定目录（如 C:\ffmpeg）"
        Write-Host "    3. 把 bin 子目录加入系统 PATH 环境变量"
        Write-Host "    4. 重新双击 开始使用.bat"
        Die "请手动安装 FFmpeg"
    }
}

# ---- 3. 虚拟环境 + 依赖 ----
$Venv       = Join-Path $Root '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$VenvPip    = Join-Path $Venv 'Scripts\pip.exe'

if (-not (Test-Path $Venv)) {
    Log "创建 Python 虚拟环境（.venv\）"
    & $PythonCmd -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Die "创建虚拟环境失败" }
    Ok "虚拟环境已创建"
}

Log "安装 Python 依赖（首次约 2-3 分钟，请耐心）"
& $VenvPip install --upgrade pip --quiet
& $VenvPip install -r (Join-Path $Root 'requirements.txt') --quiet
if ($LASTEXITCODE -ne 0) { Die "依赖安装失败，请检查网络" }
Ok "依赖安装完成"

# ---- 4. API Key 填写向导（图形界面）----
$envPath = Join-Path $Root '.env'
$needKey = $true
if (Test-Path $envPath) {
    $content = Get-Content $envPath -Raw
    if ($content -match '(?m)^AI_NAV_TOKEN=.+') { $needKey = $false }
}

if ($needKey) {
    Log "即将打开 API Key 填写窗口（图形界面）"
    & $VenvPython (Join-Path $Root 'tools\first_run_wizard.py')
    if ($LASTEXITCODE -ne 0) { Die "Key 填写取消或失败" }
} else {
    Ok ".env 已有 Key 配置，跳过填写"
}

# ---- 5. 完成 ----
New-Item -ItemType File -Force -Path (Join-Path $Root '.setup_done') | Out-Null
Write-Host ""
Ok "所有环境安装完成！"
Write-Host ""
