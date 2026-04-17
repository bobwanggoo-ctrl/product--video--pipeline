#!/bin/bash
# 产品视频流水线 — Mac 入口
# 双击即可：首次自动装环境 → 平时直接启动 GUI
set -e

# 1. 清除 Gatekeeper 的"隔离属性"，避免首次双击被拦
#    （Ventura+ 对从网络下载的 .command 会加 com.apple.quarantine）
xattr -dr com.apple.quarantine "$(dirname "$0")" 2>/dev/null || true

# 2. 切到脚本所在目录
cd "$(dirname "$0")"

# 3. 首次运行跑 bootstrap（检测 Python/ffmpeg/venv，弹 GUI 填 Key）
if [[ ! -f ".setup_done" ]]; then
    bash ./tools/bootstrap_mac.sh || { echo ""; echo "安装未完成，按任意键退出…"; read -n 1; exit 1; }
fi

# 4. 启动 GUI
if [[ -x ".venv/bin/python" ]]; then
    exec .venv/bin/python -m gui.app
else
    echo "错误：找不到 .venv/bin/python。请删除 .setup_done 后重新双击本文件。"
    read -n 1 -s -r -p "按任意键退出…"
    exit 1
fi
