# Product Video Pipeline - Windows Setup
# Usage: double-click setup.bat

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Info($msg)    { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Ok($msg)      { Write-Host "[ OK ] $msg" -ForegroundColor Green }
function Warn($msg)    { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Err($msg)     { Write-Host "[ERR ] $msg" -ForegroundColor Red }
function Section($msg) { Write-Host "`n=== $msg ===" -ForegroundColor White }

function Get-EnvValue($key) {
    if (-not (Test-Path ".env")) { return "" }
    foreach ($line in (Get-Content ".env" -Encoding UTF8)) {
        if ($line -match "^$key=(.*)") { return $Matches[1].Trim() }
    }
    return ""
}

function Set-EnvValue($key, $val) {
    $content = Get-Content ".env" -Encoding UTF8 -Raw
    if ($content -match "(?m)^$key=") {
        $content = $content -replace "(?m)^$key=.*", "$key=$val"
    } else {
        $content = $content.TrimEnd() + "`r`n$key=$val`r`n"
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $ScriptDir ".env"),
        $content,
        [System.Text.UTF8Encoding]::new($false)
    )
}

try {

Write-Host ""
Write-Host "  +--------------------------------------+" -ForegroundColor Cyan
Write-Host "  |  Product Video Pipeline  Setup       |" -ForegroundColor Cyan
Write-Host "  +--------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# === 1/4 Python ===
Section "1/4  Python"

$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $null = & $cmd --version 2>$null
        & $cmd -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $PythonCmd = $cmd; break }
    } catch { }
}

if (-not $PythonCmd) {
    Warn "Python 3.10+ not found, installing via winget..."
    try {
        winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements
        Warn "Python installed. Please close and re-run setup.bat"
    } catch {
        Err "Auto install failed. Please install Python manually:"
        Write-Host "     https://www.python.org/downloads/" -ForegroundColor Yellow
        Write-Host "     (check 'Add Python to PATH' during install)" -ForegroundColor Yellow
    }
    exit 1
}

$pyver = & $PythonCmd --version 2>&1
Ok "Using $PythonCmd  ($pyver)"

# === 2/4 FFmpeg ===
Section "2/4  FFmpeg"

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    $ffver = ffmpeg -version 2>&1 | Select-Object -First 1
    Ok "FFmpeg found: $ffver"
} else {
    Warn "FFmpeg not found, installing via winget..."
    $installed = $false
    try {
        winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
        $installed = $true
    } catch { }

    if ($installed) {
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path","User")
        if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
            Ok "FFmpeg installed"
        } else {
            Warn "FFmpeg installed but requires terminal restart. Please close and re-run setup.bat"
            exit 0
        }
    } else {
        Err "Auto install failed. Please install FFmpeg manually:"
        Write-Host "     1. Download: https://www.gyan.dev/ffmpeg/builds/" -ForegroundColor Yellow
        Write-Host "     2. Extract and add the bin folder to PATH" -ForegroundColor Yellow
        Write-Host "     3. Re-run setup.bat" -ForegroundColor Yellow
        exit 1
    }
}

# === 3/4 Python deps ===
Section "3/4  Python dependencies"

$VenvDir    = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvDir)) {
    Info "Creating virtual environment .venv ..."
    & $PythonCmd -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment" }
}

Info "Installing dependencies (first run: ~2 min)..."
& $VenvPip install --upgrade pip -q
& $VenvPip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed. Check your network connection." }
Ok "Dependencies installed"

# === 4/4 API Keys ===
Section "4/4  API Keys"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Info ".env file created"
}

Write-Host ""
Write-Host "  Enter API Keys below (press Enter to skip already-configured keys)"
Write-Host ""

if ([string]::IsNullOrEmpty((Get-EnvValue "AI_NAV_TOKEN"))) {
    $val = Read-Host "  AI Nav Token (required)"
    if ($val) { Set-EnvValue "AI_NAV_TOKEN" $val; Ok "AI_NAV_TOKEN saved" }
    else { Warn "AI_NAV_TOKEN skipped - image gen and LLM will not work" }
} else { Ok "AI_NAV_TOKEN already set, skipping" }

if ([string]::IsNullOrEmpty((Get-EnvValue "KLING_ACCESS_KEY"))) {
    $val = Read-Host "  Kling Access Key (required)"
    if ($val) { Set-EnvValue "KLING_ACCESS_KEY" $val; Ok "KLING_ACCESS_KEY saved" }
    else { Warn "KLING_ACCESS_KEY skipped - video generation will not work" }
} else { Ok "KLING_ACCESS_KEY already set, skipping" }

if ([string]::IsNullOrEmpty((Get-EnvValue "KLING_SECRET_KEY"))) {
    $val = Read-Host "  Kling Secret Key (required)"
    if ($val) { Set-EnvValue "KLING_SECRET_KEY" $val; Ok "KLING_SECRET_KEY saved" }
    else { Warn "KLING_SECRET_KEY skipped - video generation will not work" }
} else { Ok "KLING_SECRET_KEY already set, skipping" }

if ([string]::IsNullOrEmpty((Get-EnvValue "GOOGLE_VISION_API_KEY"))) {
    $val = Read-Host "  Google Vision API Key (optional, Enter to skip)"
    if ($val) { Set-EnvValue "GOOGLE_VISION_API_KEY" $val; Ok "GOOGLE_VISION_API_KEY saved" }
    else { Info "Skipped - copyright detection disabled" }
} else { Ok "GOOGLE_VISION_API_KEY already set, skipping" }

# === Create run.bat ===
$runBat = "@echo off`r`ncd /d `"%~dp0`"`r`n.venv\Scripts\python.exe main.py %*`r`npause`r`n"
[System.IO.File]::WriteAllText(
    (Join-Path $ScriptDir "run.bat"),
    $runBat,
    [System.Text.UTF8Encoding]::new($false)
)

# === Done ===
Write-Host ""
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  How to run:"
Write-Host "    Double-click  run.bat            (interactive mode)"
Write-Host "    CMD:          run.bat --auto      (fully automatic)"
Write-Host ""
Write-Host "  Input files:"
Write-Host "    Put your sellpoint .txt/.docx and product images in  input\<product-name>\"
Write-Host ""

} catch {
    Write-Host ""
    Err "Setup failed: $($_.Exception.Message)"
    Write-Host ""
}
