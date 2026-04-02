"""Skill 5 Module B: 项目文件导出（剪映 JSON + FCPXML）。

将 EditingTimeline 导出为 NLE 可导入的项目文件，
字幕和 BGM 作为独立轨道引用（不烧录到视频）。
"""

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from models.timeline import EditingTimeline

logger = logging.getLogger(__name__)


# 剪映用微秒为时间单位
_US = 1_000_000


# ── 剪映 JSON 导出 ────────────────────────────────────────────

def export_jianying_json(
    timeline: EditingTimeline,
    output_path: str,
    srt_path: str = "",
) -> str:
    """导出剪映兼容的 JSON 项目文件（draft_content.json 简化格式）。

    注意：剪映的完整 draft_content.json 格式非常复杂，
    这里导出的是一个可被剪映识别的简化版本，包含：
    - 视频轨道：片段列表 + trim + 转场
    - 文本轨道：字幕
    - 音频轨道：BGM 引用

    Args:
        timeline: 剪辑时间线。
        output_path: 输出 JSON 路径。
        srt_path: SRT 字幕文件路径。

    Returns:
        输出文件路径。
    """
    # 视频轨道
    video_segments = []
    current_time_us = 0  # 剪映用微秒

    for clip in timeline.clips:
        duration_us = int(clip.display_duration * 1_000_000)
        trim_start_us = int(clip.trim_start * 1_000_000)
        trim_end_us = int(clip.trim_end * 1_000_000)

        segment = {
            "material_id": f"shot_{clip.shot_id}",
            "source_path": str(Path(clip.source_path).resolve()),
            "target_timerange": {
                "start": current_time_us,
                "duration": duration_us,
            },
            "source_timerange": {
                "start": trim_start_us,
                "duration": trim_end_us - trim_start_us,
            },
            "speed": clip.speed_factor,
            "transition": {
                "type": clip.transition_out,
                "duration": int(clip.transition_duration * 1_000_000) if clip.transition_out != "cut" else 0,
            },
        }
        video_segments.append(segment)

        overlap = clip.transition_duration * 1_000_000 if clip.transition_out != "cut" else 0
        current_time_us += duration_us - int(overlap)

    # 文本轨道（字幕）
    text_segments = []
    current_time_us = 0
    for clip in timeline.clips:
        duration_us = int(clip.display_duration * 1_000_000)
        if clip.subtitle_text:
            text_segments.append({
                "content": clip.subtitle_text,
                "content_cn": clip.subtitle_text_cn,
                "style": clip.subtitle_style,
                "target_timerange": {
                    "start": current_time_us,
                    "duration": duration_us,
                },
            })
        overlap = clip.transition_duration * 1_000_000 if clip.transition_out != "cut" else 0
        current_time_us += duration_us - int(overlap)

    # 音频轨道（BGM）
    audio_segments = []
    if timeline.bgm_path:
        audio_segments.append({
            "source_path": str(Path(timeline.bgm_path).resolve()),
            "volume": timeline.bgm_volume,
            "fade_out_sec": timeline.bgm_fade_out_sec,
            "target_timerange": {
                "start": 0,
                "duration": int(timeline.total_duration * 1_000_000),
            },
        })

    project = {
        "version": "1.0",
        "generator": "product-video-pipeline",
        "resolution": timeline.resolution,
        "total_duration_us": int(timeline.total_duration * 1_000_000),
        "tracks": {
            "video": video_segments,
            "text": text_segments,
            "audio": audio_segments,
        },
        "srt_path": str(Path(srt_path).resolve()) if srt_path else "",
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"剪映 JSON 导出完成: {output_path}")
    return output_path


# ── FCPXML 导出 ────────────────────────────────────────────────

def export_fcpxml(
    timeline: EditingTimeline,
    output_path: str,
    srt_path: str = "",
) -> str:
    """导出 Final Cut Pro 兼容的 FCPXML 文件。

    FCPXML v1.11 格式，包含：
    - spine: 视频片段序列 + 转场
    - title elements: 字幕
    - audio: BGM 引用

    Args:
        timeline: 剪辑时间线。
        output_path: 输出 FCPXML 路径。
        srt_path: SRT 字幕文件路径（记录在 note 中供参考）。

    Returns:
        输出文件路径。
    """
    # FCPXML 根元素
    fcpxml = ET.Element("fcpxml", version="1.11")

    # 从 timeline 读取 fps 和分辨率
    fps = int(timeline.fps) if timeline.fps == int(timeline.fps) else timeline.fps
    res_parts = timeline.resolution.split("x")
    width = res_parts[0] if len(res_parts) == 2 else "1920"
    height = res_parts[1] if len(res_parts) == 2 else "1080"

    def _rational_sec(seconds: float) -> str:
        """将秒数转为 FCPXML rational time（基于帧数）。"""
        frames = round(seconds * fps)
        return f"{frames}/{fps}s"

    # resources: 素材引用
    resources = ET.SubElement(fcpxml, "resources")
    format_elem = ET.SubElement(resources, "format", {
        "id": "r1",
        "name": f"FFVideoFormat{height}p{fps}",
        "frameDuration": f"1/{fps}s",
        "width": width, "height": height,
    })

    # 为每个片段和 BGM 创建 asset
    for i, clip in enumerate(timeline.clips):
        ET.SubElement(resources, "asset", {
            "id": f"clip_{clip.shot_id}",
            "name": f"Shot {clip.shot_id}",
            "src": str(Path(clip.source_path).resolve()),
            "hasVideo": "1", "hasAudio": "0",
            "format": "r1",
        })

    if timeline.bgm_path:
        ET.SubElement(resources, "asset", {
            "id": "bgm",
            "name": "BGM",
            "src": str(Path(timeline.bgm_path).resolve()),
            "hasVideo": "0", "hasAudio": "1",
        })

    # library → event → project → sequence
    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="Auto Edit")
    project = ET.SubElement(event, "project", name="Product Video")
    sequence = ET.SubElement(project, "sequence", {
        "format": "r1",
        "duration": _rational_sec(timeline.total_duration),
        "tcStart": "0s",
        "tcFormat": "NDF",
    })

    spine = ET.SubElement(sequence, "spine")

    # 视频片段 + 字幕叠加
    for i, clip in enumerate(timeline.clips):
        trimmed = clip.trim_end - clip.trim_start
        clip_elem = ET.SubElement(spine, "clip", {
            "name": f"Shot {clip.shot_id}",
            "ref": f"clip_{clip.shot_id}",
            "offset": _rational_sec(clip.trim_start),
            "duration": _rational_sec(clip.display_duration),
            "start": _rational_sec(clip.trim_start),
        })

        # 转场
        if i > 0 and clip.transition_in != "cut":
            transition = ET.SubElement(spine, "transition", {
                "name": clip.transition_in.capitalize(),
                "duration": _rational_sec(clip.transition_duration),
            })

        # 字幕作为 title 叠加
        if clip.subtitle_text:
            title = ET.SubElement(clip_elem, "title", {
                "name": clip.subtitle_text[:30],
                "duration": _rational_sec(clip.display_duration),
            })
            text_elem = ET.SubElement(title, "text")
            text_style = ET.SubElement(text_elem, "text-style", {
                "font": "Helvetica",
                "fontSize": "48",
                "fontColor": "1 1 1 1",
            })
            text_style.text = clip.subtitle_text

    # BGM 音频轨道
    if timeline.bgm_path:
        bgm_clip = ET.SubElement(spine, "clip", {
            "name": "BGM",
            "ref": "bgm",
            "lane": "-1",
            "offset": "0s",
            "duration": _rational_sec(timeline.total_duration),
        })

    # 写入文件
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(fcpxml)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)

    logger.info(f"FCPXML 导出完成: {output_path}")
    return output_path
