#!/bin/bash
# 产品视频流水线 — Mac 入口
# 双击即可：环境 OK 直接启动 GUI；缺依赖才跑 bootstrap
set -e

# 1. 清除 Gatekeeper 的"隔离属性"
xattr -dr com.apple.quarantine "$(dirname "$0")" 2>/dev/null || true

# 2. 切到脚本所在目录
cd "$(dirname "$0")"

# 3. 判断环境是否可用：venv 里关键包能 import
#    （比 .setup_done 标记文件可靠 —— 用户删过 .venv 也能正确识别）
need_bootstrap=0
if [[ ! -x ".venv/bin/python" ]]; then
    need_bootstrap=1
elif ! .venv/bin/python -c "import PySide6, dotenv, requests" 2>/dev/null; then
    need_bootstrap=1
fi

if [[ $need_bootstrap -eq 1 ]]; then
    echo "首次运行或依赖缺失，开始安装环境…"
    bash ./tools/bootstrap_mac.sh || {
        echo ""
        echo "❌ 安装未完成。上方错误信息请截图反馈。"
        echo "按任意键退出…"
        read -n 1
        exit 1
    }
    # bootstrap 成功才打标记，方便未来扩展其它检查
    touch ".setup_done"
fi

# 4. 启动 GUI
exec .venv/bin/python -m gui.app
