#!/usr/bin/env bash
# 首次环境安装脚本（Mac） — 由 "开始使用.command" 调用
# 职责：检 Python / ffmpeg → 建 venv → 装 Python 依赖 → 调 first_run_wizard
# 成功后写 .setup_done 标记，下次启动会跳过这里
set -eu

# 颜色
C_INFO='\033[0;36m'; C_OK='\033[0;32m'; C_WARN='\033[1;33m'; C_ERR='\033[0;31m'; C_OFF='\033[0m'
log()  { printf "${C_INFO}==>${C_OFF} %s\n" "$*"; }
ok()   { printf "${C_OK}  ✓${C_OFF} %s\n" "$*"; }
warn() { printf "${C_WARN}  !${C_OFF} %s\n" "$*"; }
die()  { printf "${C_ERR}  ✗${C_OFF} %s\n" "$*"; exit 1; }

# 项目根目录（脚本位于 tools/ 下）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo ""
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║   产品视频流水线 — 首次环境安装（约5分钟）  ║"
echo "  ╚═══════════════════════════════════════════╝"
echo ""

# ── 1. Python ─────────────────────────────────────────────────
log "检查 Python（需要 3.10 或更高版本）"
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
            PYTHON="$cmd"
            ok "找到 $cmd ($("$cmd" --version 2>&1))"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo ""
    warn "没找到 Python 3.10+。即将打开官网下载页，请下载最新版安装："
    echo "    https://www.python.org/downloads/macos/"
    open "https://www.python.org/downloads/macos/" 2>/dev/null || true
    echo ""
    echo "  安装完成后：关闭本窗口 → 重新双击"开始使用.command""
    die "请安装 Python 后重试"
fi

# ── 2. ffmpeg ─────────────────────────────────────────────────
log "检查 FFmpeg"
if command -v ffmpeg >/dev/null 2>&1; then
    ok "FFmpeg 已安装"
else
    warn "未找到 FFmpeg，尝试自动安装…"
    if command -v brew >/dev/null 2>&1; then
        brew install ffmpeg || die "Homebrew 安装 FFmpeg 失败，请手动执行: brew install ffmpeg"
        ok "FFmpeg 安装完成"
    else
        echo ""
        warn "没装 Homebrew。正在打开安装指引…"
        echo "    请在终端执行这行命令（复制粘贴）："
        echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        echo "    然后再次双击"开始使用.command""
        open "https://brew.sh" 2>/dev/null || true
        die "请先安装 Homebrew"
    fi
fi

# ── 3. 虚拟环境 + 依赖 ─────────────────────────────────────────
VENV="$ROOT/.venv"
if [[ ! -d "$VENV" ]]; then
    log "创建 Python 虚拟环境（.venv/）"
    "$PYTHON" -m venv "$VENV" || die "创建虚拟环境失败"
    ok "虚拟环境已创建"
fi

log "安装 Python 依赖（首次约 2-3 分钟，请耐心）"
"$VENV/bin/pip" install --upgrade pip || warn "pip 升级失败，继续使用旧版"
"$VENV/bin/pip" install -r "$ROOT/requirements.txt" || die "依赖安装失败，上方 pip 输出有详细原因（常见：网络 / 代理 / 镜像源）"
ok "依赖安装完成"

# ── 4. API Key 填写向导（图形界面）─────────────────────────────
if [[ ! -f "$ROOT/.env" ]] || ! grep -q "^AI_NAV_TOKEN=." "$ROOT/.env" 2>/dev/null; then
    log "即将打开 API Key 填写窗口（图形界面）"
    "$VENV/bin/python" "$ROOT/tools/first_run_wizard.py" || die "Key 填写取消或失败"
else
    ok ".env 已有 Key 配置，跳过填写"
fi

# ── 5. FCP 模板自动安装（Mac only）─────────────────────────────
log "安装 FCP 字幕模板到 ~/Movies/Motion Templates.localized/"
"$VENV/bin/python" -c "
from skills.auto_editor.title_scanner import install_templates
try:
    installed = install_templates()
    print(f'  已安装 {installed} 个模板')
except Exception as e:
    print(f'  跳过（非 macOS 或无权限）: {e}')
" || true

# ── 完成 ──────────────────────────────────────────────────────
touch "$ROOT/.setup_done"
echo ""
ok "所有环境安装完成！"
echo ""
