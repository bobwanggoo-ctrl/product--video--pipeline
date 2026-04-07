"""Global configuration loaded from environment variables."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


# --- Paths ---
INPUT_DIR = ROOT_DIR / "input"
OUTPUT_DIR = ROOT_DIR / "output"
REFERENCE_IMAGES_DIR = INPUT_DIR / "reference_images"
MUSIC_DIR = INPUT_DIR / "music"
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

# --- AI导航 (yswg) ---
AI_NAV_BASE_URL = os.getenv("AI_NAV_BASE_URL", "http://yswg.love:15091/api/admin")
AI_NAV_TOKEN = os.getenv("AI_NAV_TOKEN", "")
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

# --- General ---
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def create_run_dirs(task_id: str) -> dict[str, Path]:
    """Create isolated output directories for a pipeline run.

    Returns dict with keys: root, storyboard, sellpoint, frames, videos, final, checkpoint
    """
    root = OUTPUT_DIR / task_id
    dirs = {
        "root": root,
        "storyboard": root / "storyboard.json",
        "sellpoint": root / "sellpoint.txt",
        "frames": root / "frames",
        "videos": root / "videos",
        "final": root / "final",
        "checkpoint": root / "checkpoint.json",
    }
    # Create directories (not file paths)
    for key in ("root", "frames", "videos", "final"):
        dirs[key].mkdir(parents=True, exist_ok=True)
    return dirs
