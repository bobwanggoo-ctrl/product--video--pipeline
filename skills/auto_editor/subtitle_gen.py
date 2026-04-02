"""Skill 5 Module B: 字幕生成。

根据 EditingTimeline 中每个 clip 的字幕文案和时间位置，
生成 SRT 字幕文件（供 MP4 烧录和 NLE 独立轨道引用）。
"""

import logging
from pathlib import Path

from models.timeline import EditingTimeline

logger = logging.getLogger(__name__)


def generate_srt(
    timeline: EditingTimeline,
    output_path: str,
    *,
    language: str = "en",
) -> str:
    """根据 EditingTimeline 生成 SRT 字幕文件。

    Args:
        timeline: 剪辑时间线（含每个 clip 的 subtitle_text 和时间位置）。
        output_path: SRT 文件输出路径。
        language: "en" 输出英文字幕，"cn" 输出中文字幕，"both" 双语。

    Returns:
        SRT 文件路径。
    """
    entries: list[str] = []
    index = 1
    current_time = 0.0

    for clip in timeline.clips:
        text = _get_subtitle_text(clip.subtitle_text, clip.subtitle_text_cn, language)

        if text:
            # 字幕结束时间 = 当前时间 + 展示时长 - 转场重叠
            # 避免相邻字幕在 xfade 重叠区域同时显示
            overlap = clip.transition_duration if clip.transition_out != "cut" else 0.0
            start = current_time
            end = current_time + clip.display_duration - overlap
            entries.append(
                f"{index}\n"
                f"{_format_srt_time(start)} --> {_format_srt_time(end)}\n"
                f"{text}\n"
            )
            index += 1

        # 计算下一个 clip 的起始时间（减去转场重叠）
        overlap = clip.transition_duration if clip.transition_out != "cut" else 0.0
        current_time += clip.display_duration - overlap

    # 写入文件
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n".join(entries), encoding="utf-8")

    logger.info(f"SRT 生成完成: {index - 1} 条字幕 → {output_path}")
    return output_path


def generate_dual_srt(
    timeline: EditingTimeline,
    output_dir: str,
    base_name: str = "subtitles",
) -> dict[str, str]:
    """生成英文 + 中文两份 SRT 文件。

    Returns:
        {"en": "path/to/en.srt", "cn": "path/to/cn.srt"}
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    en_path = str(out_dir / f"{base_name}_en.srt")
    cn_path = str(out_dir / f"{base_name}_cn.srt")

    generate_srt(timeline, en_path, language="en")
    generate_srt(timeline, cn_path, language="cn")

    return {"en": en_path, "cn": cn_path}


def _get_subtitle_text(text_en: str, text_cn: str, language: str) -> str:
    """根据语言选择返回字幕文本。"""
    if language == "en":
        return text_en
    elif language == "cn":
        return text_cn
    elif language == "both":
        parts = [p for p in [text_en, text_cn] if p]
        return "\n".join(parts)
    return text_en


def _format_srt_time(seconds: float) -> str:
    """将秒数转为 SRT 时间格式 HH:MM:SS,mmm。"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
