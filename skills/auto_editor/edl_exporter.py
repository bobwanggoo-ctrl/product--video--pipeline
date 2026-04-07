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
    """导出剪映专业版兼容的 .draft 文件夹。

    .draft 文件夹结构：
      ProductVideo.draft/
        draft_content.json   — 时间轴、素材、轨道
        draft_meta_info.json — 项目元数据（分辨率、时长、版本）

    Args:
        output_path: 输出路径（传入的路径会被转为 .draft 目录）。

    Returns:
        .draft 文件夹路径。
    """
    import uuid

    def _uid() -> str:
        return uuid.uuid4().hex[:24].upper()

    # 确定 .draft 目录路径
    out = Path(output_path)
    if out.suffix == ".json":
        draft_dir = out.parent / "ProductVideo.draft"
    elif out.suffix == ".draft":
        draft_dir = out
    else:
        draft_dir = out.parent / "ProductVideo.draft"
    draft_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 构建 materials ──

    # 视频素材
    video_materials = []
    for clip in timeline.clips:
        video_materials.append({
            "id": _uid(),
            "type": "video",
            "material_name": f"Shot {clip.shot_id}",
            "path": str(Path(clip.source_path).resolve()),
            "duration": int(clip.trim_end * _US),
            "width": int(timeline.resolution.split("x")[0]) if "x" in timeline.resolution else 1920,
            "height": int(timeline.resolution.split("x")[1]) if "x" in timeline.resolution else 1080,
        })

    # 音频素材
    audio_materials = []
    if timeline.bgm_path:
        audio_materials.append({
            "id": _uid(),
            "type": "audio",
            "material_name": "BGM",
            "path": str(Path(timeline.bgm_path).resolve()),
            "duration": int(timeline.total_duration * _US),
            "volume": timeline.bgm_volume,
        })

    # 文本素材
    text_materials = []
    for clip in timeline.clips:
        if clip.subtitle_text:
            text_materials.append({
                "id": _uid(),
                "type": "text",
                "content": clip.subtitle_text,
                "content_cn": clip.subtitle_text_cn,
                "style": clip.subtitle_style,
            })

    materials = {
        "videos": video_materials,
        "audios": audio_materials,
        "texts": text_materials,
    }

    # ── 2. 构建 tracks ──

    # 视频轨道
    video_segments = []
    current_time_us = 0

    for i, clip in enumerate(timeline.clips):
        duration_us = int(clip.display_duration * _US)
        trim_start_us = int(clip.trim_start * _US)
        trim_end_us = int(clip.trim_end * _US)

        segment = {
            "material_id": video_materials[i]["id"],
            "target_timerange": {
                "start": current_time_us,
                "duration": duration_us,
            },
            "source_timerange": {
                "start": trim_start_us,
                "duration": trim_end_us - trim_start_us,
            },
            "speed": clip.speed_factor,
            "extra_material_refs": [],
        }

        # 转场信息
        if clip.transition_out != "cut":
            segment["transition"] = {
                "type": clip.transition_out,
                "duration": int(clip.transition_duration * _US),
            }

        video_segments.append(segment)

        overlap = clip.transition_duration * _US if clip.transition_out != "cut" else 0
        current_time_us += duration_us - int(overlap)

    # 文本轨道（字幕）
    text_segments = []
    text_mat_idx = 0
    current_time_us = 0
    for clip in timeline.clips:
        duration_us = int(clip.display_duration * _US)
        if clip.subtitle_text and text_mat_idx < len(text_materials):
            text_segments.append({
                "material_id": text_materials[text_mat_idx]["id"],
                "target_timerange": {
                    "start": current_time_us,
                    "duration": duration_us,
                },
            })
            text_mat_idx += 1
        overlap = clip.transition_duration * _US if clip.transition_out != "cut" else 0
        current_time_us += duration_us - int(overlap)

    # 音频轨道（BGM）
    audio_segments = []
    if timeline.bgm_path and audio_materials:
        audio_segments.append({
            "material_id": audio_materials[0]["id"],
            "target_timerange": {
                "start": 0,
                "duration": int(timeline.total_duration * _US),
            },
            "volume": timeline.bgm_volume,
            "fade_out_duration": int(timeline.bgm_fade_out_sec * _US),
        })

    tracks = [
        {"type": "video", "segments": video_segments},
        {"type": "text", "segments": text_segments},
        {"type": "audio", "segments": audio_segments},
    ]

    # ── 3. 组装 draft_content ──

    project = {
        "id": _uid(),
        "name": "Product Video",
        "materials": materials,
        "tracks": tracks,
        "duration": int(timeline.total_duration * _US),
        "canvas_config": {
            "width": int(timeline.resolution.split("x")[0]) if "x" in timeline.resolution else 1920,
            "height": int(timeline.resolution.split("x")[1]) if "x" in timeline.resolution else 1080,
            "ratio": timeline.resolution,
        },
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    # 写 draft_content.json
    content_path = draft_dir / "draft_content.json"
    content_path.write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 写 draft_meta_info.json
    res_w = int(timeline.resolution.split("x")[0]) if "x" in timeline.resolution else 1920
    res_h = int(timeline.resolution.split("x")[1]) if "x" in timeline.resolution else 1080
    meta = {
        "draft_id": project["id"],
        "draft_name": "Product Video",
        "draft_resolution": {"width": res_w, "height": res_h},
        "draft_ratio": f"{res_w}:{res_h}",
        "duration": int(timeline.total_duration * _US),
        "version": 1,
    }
    meta_path = draft_dir / "draft_meta_info.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"剪映 .draft 导出完成: {draft_dir}")
    return str(draft_dir)


# ── FCPXML 导出 ────────────────────────────────────────────────

def export_fcpxml(
    timeline: EditingTimeline,
    output_path: str,
    srt_path: str = "",
) -> str:
    """导出 Final Cut Pro 兼容的 FCPXML 1.9 文件。

    包含：视频轨（spine）、字幕（title attached clips）、BGM（connected audio）。
    """
    # FCPXML 根元素（v1.9 兼容 FCP 10.4+）
    fcpxml = ET.Element("fcpxml", version="1.9")

    # 从 timeline 读取 fps 和分辨率
    fps = int(timeline.fps) if timeline.fps == int(timeline.fps) else timeline.fps
    res_parts = timeline.resolution.split("x")
    width = res_parts[0] if len(res_parts) == 2 else "1920"
    height = res_parts[1] if len(res_parts) == 2 else "1080"

    def _rs(seconds: float) -> str:
        """将秒数转为 FCPXML rational time（基于帧数）。"""
        frames = round(seconds * fps)
        return f"{frames}/{fps}s"

    # resources: 格式 + 素材
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(resources, "format", {
        "id": "r1",
        "name": f"FFVideoFormat{height}p{fps}",
        "frameDuration": f"1/{fps}s",
        "width": width, "height": height,
    })

    # 视频素材 assets
    for clip in timeline.clips:
        asset_elem = ET.SubElement(resources, "asset", {
            "id": f"clip_{clip.shot_id}",
            "name": f"Shot {clip.shot_id}",
            "hasVideo": "1", "hasAudio": "0",
            "format": "r1",
            "duration": _rs(clip.trim_end),
        })
        ET.SubElement(asset_elem, "media-rep", {
            "kind": "original-media",
            "src": Path(clip.source_path).resolve().as_uri(),
        })

    # BGM 素材 asset
    if timeline.bgm_path:
        bgm_asset = ET.SubElement(resources, "asset", {
            "id": "bgm",
            "name": "BGM",
            "hasVideo": "0", "hasAudio": "1",
            "format": "r1",
        })
        ET.SubElement(bgm_asset, "media-rep", {
            "kind": "original-media",
            "src": Path(timeline.bgm_path).resolve().as_uri(),
        })

    # 字幕 effect 资源（FCP 内置 Basic Title）
    ET.SubElement(resources, "effect", {
        "id": "title_effect",
        "name": "Basic Title",
        "uid": ".../Titles.localized/Build In:Out.localized/Basic Title.localized/Basic Title.moti",
    })

    # library → event → project → sequence
    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="Auto Edit")
    project_elem = ET.SubElement(event, "project", name="Product Video")
    sequence = ET.SubElement(project_elem, "sequence", {
        "format": "r1",
        "duration": _rs(timeline.total_duration),
        "tcStart": "0s",
        "tcFormat": "NDF",
    })

    spine = ET.SubElement(sequence, "spine")

    # 构建 spine：视频 clip + transition + attached 字幕 + attached BGM
    current_offset = 0.0
    title_idx = 0

    for i, clip in enumerate(timeline.clips):
        # 在两个 clip 之间插入 transition
        need_transition = False
        if i > 0:
            prev_out = timeline.clips[i - 1].transition_out
            curr_in = clip.transition_in
            if prev_out != "cut" or curr_in != "cut":
                need_transition = True

        if need_transition:
            trans_dur = clip.transition_duration
            trans_elem = ET.SubElement(spine, "transition", {
                "name": _get_fcp_transition_name(clip.transition_in or timeline.clips[i - 1].transition_out),
                "duration": _rs(trans_dur),
            })
            # transition 会吃掉前后各一半时长，offset 向前回退
            current_offset -= trans_dur

        # asset-clip（start = trim 起点，duration = 展示时长）
        clip_elem = ET.SubElement(spine, "asset-clip", {
            "ref": f"clip_{clip.shot_id}",
            "name": f"Shot {clip.shot_id}",
            "offset": _rs(current_offset),
            "duration": _rs(clip.display_duration),
            "start": _rs(clip.trim_start),
            "tcFormat": "NDF",
        })

        # 字幕作为 attached title（lane=1 表示字幕轨在视频上方）
        if clip.subtitle_text:
            title_idx += 1
            ts_id = f"ts{title_idx}"
            title_elem = ET.SubElement(clip_elem, "title", {
                "ref": "title_effect",
                "name": clip.subtitle_text,
                "lane": "1",
                "offset": _rs(clip.trim_start),
                "duration": _rs(clip.display_duration),
            })
            param_text = ET.SubElement(title_elem, "param", {
                "name": "Position",
                "key": "9999/999166631/999166633/2/354/999169573/401",
                "value": "0 -450",
            })
            text_elem = ET.SubElement(title_elem, "text")
            ts = ET.SubElement(text_elem, "text-style", ref=ts_id)
            ts.text = clip.subtitle_text
            ET.SubElement(title_elem, "text-style-def", id=ts_id).append(
                _make_element("text-style", {
                    "font": "Helvetica Neue",
                    "fontSize": "42",
                    "fontColor": "1 1 1 1",
                    "bold": "1",
                    "shadowColor": "0 0 0 0.75",
                    "shadowOffset": "3 315",
                    "alignment": "center",
                })
            )

        # BGM attached 到第一个 clip
        if i == 0 and timeline.bgm_path:
            ET.SubElement(clip_elem, "asset-clip", {
                "ref": "bgm",
                "name": "BGM",
                "lane": "-1",
                "offset": _rs(clip.trim_start),
                "duration": _rs(timeline.total_duration),
                "start": "0s",
            })

        # 推进 offset
        overlap = 0.0
        if clip.transition_out != "cut" and i < len(timeline.clips) - 1:
            overlap = clip.transition_duration
        current_offset += clip.display_duration - overlap

    # 写入文件
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(fcpxml)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)

    logger.info(f"FCPXML 导出完成: {output_path}")
    return output_path


def _get_fcp_transition_name(transition_type: str) -> str:
    """Map our transition types to FCP built-in transition names."""
    mapping = {
        "dissolve": "Cross Dissolve",
        "fade": "Cross Dissolve",
        "cut": "Cut",
    }
    return mapping.get(transition_type, "Cross Dissolve")


def _make_element(tag: str, attribs: dict) -> ET.Element:
    """Helper to create an Element with attributes."""
    return ET.Element(tag, attribs)
