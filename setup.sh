#!/usr/bin/env bash
# Product Video Pipeline — 一键部署脚本
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR ]${NC} $*"; exit 1; }
section() { echo -e "\n${BOLD}=== $* ===${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Product Video Pipeline  Setup      ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ─── 1. Python ───────────────────────────────────────────────
section "1/4  检查 Python"

PYTHON=""
for cmd in python3.13 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(sys.version_info[:2])")
        if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
            PYTHON="$cmd"
            ok "使用 $cmd  ($("$cmd" --version 2>&1))"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    error "未找到 Python 3.10+。请先安装：https://www.python.org/downloads/"
fi

# ─── 2. FFmpeg ───────────────────────────────────────────────
section "2/4  检查 FFmpeg"

if command -v ffmpeg &>/dev/null; then
    ok "FFmpeg 已安装：$(ffmpeg -version 2>&1 | head -1)"
else
    warn "未检测到 FFmpeg，尝试自动安装..."
    if [[ "$(uname)" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install ffmpeg
            ok "FFmpeg 安装完成"
        else
            error "请先安装 Homebrew（https://brew.sh），或手动安装 FFmpeg"
        fi
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update -q && sudo apt-get install -y ffmpeg
        ok "FFmpeg 安装完成"
    elif command -v yum &>/dev/null; then
        sudo yum install -y ffmpeg
        ok "FFmpeg 安装完成"
    else
        error "无法自动安装 FFmpeg，请手动安装后重试"
    fi
fi

# ─── 3. Python 依赖 ──────────────────────────────────────────
section "3/4  安装 Python 依赖"

# 优先使用 venv
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "创建虚拟环境 .venv ..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

info "安装依赖（首次约 1-2 分钟）..."
"$VENV_PIP" install --upgrade pip -q
"$VENV_PIP" install -r requirements.txt -q
ok "依赖安装完成"

# ─── 4. 配置 .env ────────────────────────────────────────────
section "4/4  配置 API Key"

if [[ ! -f ".env" ]]; then
    cp .env.example .env
    info "已生成 .env 文件"
fi

# 读取当前值
get_env() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- || echo ""; }
set_env() {
    local key="$1" val="$2"
    if grep -qE "^$key=" .env 2>/dev/null; then
        # macOS sed 和 GNU sed 兼容写法
        sed -i.bak "s|^$key=.*|$key=$val|" .env && rm -f .env.bak
    else
        echo "$key=$val" >> .env
    fi
}

echo ""
echo -e "  请依次填入 API Key（直接回车跳过已填项）\n"

# AI_NAV_TOKEN
current=$(get_env "AI_NAV_TOKEN")
if [[ -z "$current" ]]; then
    read -rp "  AI导航 Token (必填): " val
    [[ -n "$val" ]] && set_env "AI_NAV_TOKEN" "$val" || warn "AI_NAV_TOKEN 未填写，生图和 LLM 功能将不可用"
else
    ok "AI_NAV_TOKEN 已配置，跳过"
fi

# KLING_ACCESS_KEY
current=$(get_env "KLING_ACCESS_KEY")
if [[ -z "$current" ]]; then
    read -rp "  Kling Access Key (必填): " val
    [[ -n "$val" ]] && set_env "KLING_ACCESS_KEY" "$val" || warn "KLING_ACCESS_KEY 未填写，视频生成将不可用"
else
    ok "KLING_ACCESS_KEY 已配置，跳过"
fi

# KLING_SECRET_KEY
current=$(get_env "KLING_SECRET_KEY")
if [[ -z "$current" ]]; then
    read -rp "  Kling Secret Key (必填): " val
    [[ -n "$val" ]] && set_env "KLING_SECRET_KEY" "$val" || warn "KLING_SECRET_KEY 未填写，视频生成将不可用"
else
    ok "KLING_SECRET_KEY 已配置，跳过"
fi

# GOOGLE_VISION_API_KEY
current=$(get_env "GOOGLE_VISION_API_KEY")
if [[ -z "$current" ]]; then
    read -rp "  Google Vision API Key (可选，回车跳过): " val
    [[ -n "$val" ]] && set_env "GOOGLE_VISION_API_KEY" "$val" || info "跳过，侵权检测功能将禁用"
else
    ok "GOOGLE_VISION_API_KEY 已配置，跳过"
fi

# ─── 创建启动脚本 ─────────────────────────────────────────────
cat > run.sh << 'EOF'
#!/usr/bin/env bash
cd "$(dirname "${BASH_SOURCE[0]}")"
.venv/bin/python main.py "$@"
EOF
chmod +x run.sh

# ─── 完成 ─────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ✓ 部署完成！"
echo -e "${NC}"
echo -e "  运行方式："
echo -e "    ${BOLD}./run.sh${NC}         # 半自动模式（推荐初次使用）"
echo -e "    ${BOLD}./run.sh --auto${NC}  # 全自动模式（无人值守）"
echo ""
echo -e "  输入目录：将卖点文案(.txt/.docx)和产品图放入 ${BOLD}input/你的产品名/${NC}"
echo ""
