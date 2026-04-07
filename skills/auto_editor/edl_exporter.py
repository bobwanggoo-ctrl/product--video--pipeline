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
    """导出剪映参考 JSON（含完整时间线信息）。

    注意：剪映专业版的 .draft 文件是加密的，无法直接生成可导入的项目文件。
    此 JSON 作为参考文件，包含完整的素材、轨道、时间码信息，
    用户可参照在剪映中手动重建项目。

    格式结构：
    - materials: {videos, audios, texts}  素材引用池
    - tracks: [{type, segments}]          时间线轨道
    时间单位：微秒。
    """
    import uuid

    def _uid() -> str:
        return uuid.uuid4().hex[:24].upper()

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
    Path(output_path).write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"剪映参考 JSON 导出完成: {output_path}")
    return output_path


# ── FCPXML 导出 ────────────────────────────────────────────────

def export_fcpxml(
    timeline: EditingTimeline,
    output_path: str,
    srt_path: str = "",
) -> str:
    """导出 Final Cut Pro 兼容的 FCPXML 1.11 文件。

    格式对照 FCP 自身导出的 .fcpxmld，包含视频轨、字幕 title、BGM。
    """
    fcpxml = ET.Element("fcpxml", version="1.11")

    fps = int(timeline.fps) if timeline.fps == int(timeline.fps) else timeline.fps
    res_parts = timeline.resolution.split("x")
    width = res_parts[0] if len(res_parts) == 2 else "1920"
    height = res_parts[1] if len(res_parts) == 2 else "1080"

    # FCP 用 100/2400s 表示 24fps（= 1/24），与 FCP 导出一致
    frame_dur = f"100/{fps * 100}s"

    def _rs(seconds: float) -> str:
        """秒 → FCPXML rational time，对齐到帧边界。"""
        base = fps * 100
        # 先对齐到帧边界（round 到最近的帧），再转为 rational
        frames = round(seconds * fps)
        ticks = frames * 100
        return f"{ticks}/{base}s"

    # ── resources ──
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(resources, "format", {
        "id": "r1",
        "name": f"FFVideoFormat{height}p{fps}",
        "frameDuration": frame_dur,
        "width": width, "height": height,
    })

    # 视频素材
    asset_id_counter = 2  # r1 是 format，从 r2 开始
    clip_ref_map = {}  # shot_id → asset ref id
    for clip in timeline.clips:
        ref_id = f"r{asset_id_counter}"
        clip_ref_map[clip.shot_id] = ref_id
        asset_id_counter += 1
        asset_elem = ET.SubElement(resources, "asset", {
            "id": ref_id,
            "name": f"Shot {clip.shot_id}",
            "start": "0s",
            "duration": _rs(clip.trim_end),
            "hasVideo": "1",
            "format": "r1",
        })
        ET.SubElement(asset_elem, "media-rep", {
            "kind": "original-media",
            "src": Path(clip.source_path).resolve().as_uri(),
        })

    # BGM 素材
    bgm_ref = None
    if timeline.bgm_path:
        bgm_ref = f"r{asset_id_counter}"
        asset_id_counter += 1
        bgm_asset = ET.SubElement(resources, "asset", {
            "id": bgm_ref,
            "name": "BGM",
            "start": "0s",
            "hasAudio": "1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "44100",
        })
        ET.SubElement(bgm_asset, "media-rep", {
            "kind": "original-media",
            "src": Path(timeline.bgm_path).resolve().as_uri(),
        })

    # 字幕 effect（FCP 内置 Essential Title）
    title_ref = f"r{asset_id_counter}"
    asset_id_counter += 1
    ET.SubElement(resources, "effect", {
        "id": title_ref,
        "name": "Essential Title",
        "uid": ".../Titles.localized/Essential Titles.localized/Essential Title.localized/Essential Title.moti",
    })

    # 转场 effect（FCP 内置 Cross Dissolve）— 备用，transition 元素用 name 而非 ref
    dissolve_ref = f"r{asset_id_counter}"
    asset_id_counter += 1
    ET.SubElement(resources, "effect", {
        "id": dissolve_ref,
        "name": "Cross Dissolve",
        "uid": ".../Transitions.localized/Dissolve.localized/Cross Dissolve.localized/Cross Dissolve.motr",
    })

    # ── library → event → project → sequence ──
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

    # ── spine: clips + transitions + titles + BGM ──
    # offset 按顺序累加，不手动减 transition 时长（FCP 自动处理重叠）
    current_offset = 0.0
    ts_counter = 0

    def _snap(seconds: float) -> float:
        """Snap to nearest frame boundary."""
        return round(seconds * fps) / fps

    for i, clip in enumerate(timeline.clips):
        # Transition between clips（放在 clip 前面）
        need_transition = False
        if i > 0:
            prev_out = timeline.clips[i - 1].transition_out
            curr_in = clip.transition_in
            if prev_out != "cut" or curr_in != "cut":
                need_transition = True

        if need_transition:
            trans_dur = _snap(clip.transition_duration)
            ET.SubElement(spine, "transition", {
                "name": "Cross Dissolve",
                "duration": _rs(trans_dur),
            })

        # asset-clip
        clip_elem = ET.SubElement(spine, "asset-clip", {
            "ref": clip_ref_map[clip.shot_id],
            "offset": _rs(current_offset),
            "name": f"Shot {clip.shot_id}",
            "duration": _rs(clip.display_duration),
            "tcFormat": "NDF",
        })

        # BGM attached to first clip
        if i == 0 and bgm_ref:
            ET.SubElement(clip_elem, "asset-clip", {
                "ref": bgm_ref,
                "lane": "-1",
                "offset": "0s",
                "name": "BGM",
                "duration": _rs(timeline.total_duration),
                "audioRole": "dialogue",
            })

        # Title attached to clip — 按 subtitle_style 区分样式
        if clip.subtitle_text:
            ts_counter += 1
            ts_id = f"ts{ts_counter}"
            is_title = clip.subtitle_style == "title"
            font_size = "80" if is_title else "48"

            title_elem = ET.SubElement(clip_elem, "title", {
                "ref": title_ref,
                "lane": "1",
                "offset": "0s",
                "name": "Essential Title",
                "start": "3600s",
                "duration": _rs(clip.display_duration),
            })
            # Position: title 居中偏下，selling_point 底部
            y_pos = "-200" if is_title else "-430"
            ET.SubElement(title_elem, "param", {
                "name": "Position",
                "key": "9999/999166631/999166633/2/354/999169573/401",
                "value": f"0 {y_pos}",
            })
            text_elem = ET.SubElement(title_elem, "text")
            ts_node = ET.SubElement(text_elem, "text-style", ref=ts_id)
            ts_node.text = clip.subtitle_text
            tsd = ET.SubElement(title_elem, "text-style-def", id=ts_id)
            style_attrs = {
                "font": "Helvetica",
                "fontSize": font_size,
                "fontColor": "1 1 1 1",
                "bold": "1",
                "alignment": "center",
            }
            if is_title:
                style_attrs["strokeColor"] = "0 0 0 1"
                style_attrs["strokeWidth"] = "2"
            else:
                style_attrs["shadowColor"] = "0 0 0 0.75"
                style_attrs["shadowOffset"] = "3 315"
            ET.SubElement(tsd, "text-style", style_attrs)

        # Advance offset（顺序累加，不减 transition）
        current_offset = _snap(current_offset + clip.display_duration)

    # Write
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(fcpxml)
    ET.indent(tree, space="    ")

    # Write with DOCTYPE
    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE fcpxml>\n\n')
        ET.indent(tree, space="    ")
        tree.write(f, encoding="unicode", xml_declaration=False)

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
