"""Skill 5 Module A: BGM 库扫描。

扫描指定目录的 BGM 文件，按子文件夹分类（节奏类型），
返回 BgmInfo 列表供 LLM 选择。
"""

import logging
from pathlib import Path

from models.timeline import BgmInfo
from utils.ffmpeg_wrapper import get_audio_duration

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}


def scan_bgm_library(bgm_dir: str) -> list[BgmInfo]:
    """扫描 BGM 目录，返回可用 BGM 列表。

    目录结构示例：
        assets/bgm/
        ├── upbeat/         # 欢快节奏
        │   ├── track_01.mp3
        │   └── track_02.mp3
        ├── emotional/      # 情感抒情
        │   └── track_03.mp3
        └── chill/          # 轻松舒缓
            └── track_04.mp3

    子文件夹名即为节奏类型标签，会附加到 BgmInfo.name 中。

    Args:
        bgm_dir: BGM 根目录路径。

    Returns:
        list[BgmInfo]，每项包含文件名（含类型标签）、时长、路径。
    """
    root = Path(bgm_dir)
    if not root.exists():
        logger.warning(f"BGM 目录不存在: {bgm_dir}")
        return []

    results: list[BgmInfo] = []

    # 扫描子目录（节奏类型分类）
    for item in sorted(root.iterdir()):
        if item.is_dir():
            category = item.name
            for audio_file in sorted(item.iterdir()):
                if audio_file.suffix.lower() in AUDIO_EXTENSIONS:
                    bgm = _analyze_bgm(audio_file, category)
                    if bgm:
                        results.append(bgm)
        elif item.is_file() and item.suffix.lower() in AUDIO_EXTENSIONS:
            # 根目录下的文件，无分类
            bgm = _analyze_bgm(item, "uncategorized")
            if bgm:
                results.append(bgm)

    logger.info(f"BGM 库扫描完成: {len(results)} 首可用")
    for bgm in results:
        logger.info(f"  [{bgm.name}] {bgm.duration:.1f}s — {bgm.path}")

    return results


def _analyze_bgm(file_path: Path, category: str) -> BgmInfo | None:
    """分析单个 BGM 文件，返回 BgmInfo。"""
    try:
        duration = get_audio_duration(str(file_path))
    except Exception as e:
        logger.warning(f"BGM 分析失败 {file_path}: {e}")
        return None

    if duration < 10:
        logger.warning(f"BGM 时长过短 ({duration:.1f}s)，跳过: {file_path}")
        return None

    name = f"[{category}] {file_path.stem}"
    return BgmInfo(name=name, duration=duration, path=str(file_path))
