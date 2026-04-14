"""Global configuration loaded from environment variables."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Project root — handles both source and PyInstaller bundle
if getattr(sys, "frozen", False):
    # Running as PyInstaller bundle: write data next to the executable
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

load_dotenv(ROOT_DIR / ".env")


# --- Paths ---
INPUT_DIR = ROOT_DIR / "input"
OUTPUT_DIR = ROOT_DIR / "output"
REFERENCE_IMAGES_DIR = INPUT_DIR / "reference_images"
MUSIC_DIR = INPUT_DIR / "music"
FONTS_DIR = INPUT_DIR / "fonts"
ASSETS_DIR = ROOT_DIR / "assets"
FCP_TITLES_DIR = ASSETS_DIR
STORYBOARDS_DIR = OUTPUT_DIR / "storyboards"
FRAMES_DIR = OUTPUT_DIR / "frames"
VIDEOS_DIR = OUTPUT_DIR / "videos"
FINAL_DIR = OUTPUT_DIR / "final"
LOGS_DIR = OUTPUT_DIR / "logs"


# --- LLM: Reverse Prompt (tu-zi 中转，备选) ---
REVERSE_PROMPT_API_KEY = os.getenv("REVERSE_PROMPT_API_KEY", "")
REVERSE_PROMPT_BASE_URL = os.getenv("REVERSE_PROMPT_BASE_URL", "https://api.tu-zi.com/v1")
REVERSE_PROMPT_PATH = os.getenv("REVERSE_PROMPT_PATH", "/chat/completions")
REVERSE_PROMPT_MODEL = os.getenv("REVERSE_PROMPT_MODEL", "gemini-3-flash-preview")
# Vision 专用模型（主路 → 备路，主路超时自动降级）
REVERSE_PROMPT_VISION_MODEL = os.getenv("REVERSE_PROMPT_VISION_MODEL", "gemini-3-flash-preview")
REVERSE_PROMPT_VISION_MODEL_FALLBACK = os.getenv("REVERSE_PROMPT_VISION_MODEL_FALLBACK", "gemini-2.5-flash-lite")

# --- AI导航 (yswg) ---
AI_NAV_BASE_URL = os.getenv("AI_NAV_BASE_URL", "http://yswg.love:15091/api/admin")

def _load_nav_token() -> str:
    """优先从 skill auth.json 读取 token（用户每次登录自动更新），
    找不到时降级到 .env 的 AI_NAV_TOKEN。
    这样用户只需在开发机上跑一次 /navigation-ai login，
    所有后续 pipeline 运行自动使用最新 token，无需手动同步。
    """
    import json
    auth_path = Path.home() / ".baoyu-skills" / "navigation-ai" / "auth.json"
    if auth_path.exists():
        try:
            token = json.loads(auth_path.read_text(encoding="utf-8")).get("token", "")
            if token:
                return token
        except Exception:
            pass
    return os.getenv("AI_NAV_TOKEN", "")

AI_NAV_TOKEN = _load_nav_token()
# 生图/生视频
AI_NAV_IMAGE_APP_ID = os.getenv("AI_NAV_IMAGE_APP_ID", "2038805674553368579")
AI_NAV_IMAGE_GROUP_ID = os.getenv("AI_NAV_IMAGE_GROUP_ID", "1")
# Gemini-3-flash LLM
AI_NAV_LLM_APP_ID = os.getenv("AI_NAV_LLM_APP_ID", "2038805674553368579")
AI_NAV_LLM_GROUP_ID = os.getenv("AI_NAV_LLM_GROUP_ID", "13")

# --- Video Generation: Kling ---
KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY", "")
KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY", "")
KLING_BASE_URL = os.getenv("KLING_BASE_URL", "https://api.klingai.com")
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v2-5")
KLING_MODE = os.getenv("KLING_MODE", "std")
KLING_DURATION = os.getenv("KLING_DURATION", "5")
KLING_ASPECT_RATIO = os.getenv("KLING_ASPECT_RATIO", "16:9")

# --- Google Cloud Vision API (侵权检测) ---
GOOGLE_VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY", "")

# --- General ---
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")
LOG_LEVEL   = os.getenv("LOG_LEVEL", "INFO")

# --- Concurrency ---
# 每个 app 实例同时持有的 Kling 槽位上限（启动默认值，运行时可通过 UI 调整）
# 推荐值：4 人同时用 → 5；6-8 人 → 3；10 人以上 → 2
KLING_MAX_CONCURRENT  = int(os.getenv("KLING_MAX_CONCURRENT",  "5"))
# 每个 app 实例同时运行的完整 pipeline 数量上限
APP_MAX_RUNNING_TASKS = int(os.getenv("APP_MAX_RUNNING_TASKS", "3"))
# 管理员模式：设为 true/1 才显示并发设置按钮（普通用户不可见）
ADMIN_MODE = os.getenv("ADMIN_MODE", "false").lower() in ("true", "1", "yes")


def create_run_dirs(task_id: str) -> dict[str, Path]:
    """Create isolated output directories for a pipeline run.

    Structure:
        output/{task_id}/               ← opened by "视频文件" button
        ├── {task_id}.mp4
        ├── {task_id}.fcpxml
        └── {task_id}-附件/             ← everything else, flat (no sub-folders)
            ├── frames/
            ├── videos/
            ├── checkpoint.json
            ├── storyboard.json
            ├── sellpoint.txt
            └── SRT / JSON exports ...
    """
    root  = OUTPUT_DIR / task_id
    other = root / f"{task_id}-附件"
    dirs = {
        "root":       root,
        "final":      root,           # mp4/fcpxml 输出到这里，"视频文件"也打开这里
        "other":      other,          # 附件目录，所有辅助文件平铺于此
        "storyboard": other / "storyboard.json",
        "sellpoint":  other / "sellpoint.txt",
        "frames":     other / "frames",
        "videos":     other / "videos",
        "trace":      other / "trace",
        "checkpoint": other / "checkpoint.json",
    }
    # Create directories (not file paths)
    for key in ("root", "frames", "videos", "other", "trace"):
        dirs[key].mkdir(parents=True, exist_ok=True)
    return dirs
