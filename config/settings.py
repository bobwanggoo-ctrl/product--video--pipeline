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


# --- LLM: Gemini (Service Interface) ---
GEMINI_SELLPOINT_APP_KEY = os.getenv("GEMINI_SELLPOINT_APP_KEY", "")
GEMINI_SELLPOINT_APP_SECRET = os.getenv("GEMINI_SELLPOINT_APP_SECRET", "")
GEMINI_SELLPOINT_SERVICE_ID = os.getenv("GEMINI_SELLPOINT_SERVICE_ID", "")
GEMINI_SELLPOINT_BASE_URL = os.getenv("GEMINI_SELLPOINT_BASE_URL", "")
GEMINI_SELLPOINT_POLL_INTERVAL = int(os.getenv("GEMINI_SELLPOINT_POLL_INTERVAL_SEC", "3"))
GEMINI_SELLPOINT_POLL_TIMEOUT = int(os.getenv("GEMINI_SELLPOINT_POLL_TIMEOUT_SEC", "120"))

# --- LLM: Gemini (Proxy) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-preview-05-20")

# --- LLM: DeepSeek / OpenAI (Fallback) ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")

# --- Image Generation ---
GEMINI_IMAGE_API_KEY = os.getenv("GEMINI_IMAGE_API_KEY", "")
GEMINI_IMAGE_BASE_URL = os.getenv("GEMINI_IMAGE_BASE_URL", "")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.0-flash-exp")

GEMINI_YI_IMAGE_API_KEY = os.getenv("GEMINI_YI_IMAGE_API_KEY", "")
GEMINI_YI_IMAGE_BASE_URL = os.getenv("GEMINI_YI_IMAGE_BASE_URL", "")
GEMINI_YI_IMAGE_MODEL = os.getenv("GEMINI_YI_IMAGE_MODEL", "gemini-2.0-flash-exp")

# --- Video Generation: Kling ---
KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY", "")
KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY", "")
KLING_BASE_URL = os.getenv("KLING_BASE_URL", "https://api.klingai.com")
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v1")

# --- Video Analysis: VideoDB ---
VIDEODB_API_KEY = os.getenv("VIDEODB_API_KEY", "")

# --- Compliance Check ---
GEMINI_VISION_API_KEY = os.getenv("GEMINI_VISION_API_KEY", "")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash-preview-05-20")

# --- General ---
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
