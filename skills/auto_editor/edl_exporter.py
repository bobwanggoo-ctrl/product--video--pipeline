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
    title_templates_dir: str = "",
) -> str:
    """导出 Final Cut Pro 兼容的 FCPXML 1.11 文件。

    格式对照 FCP 自身导出的 .fcpxmld，包含视频轨、字幕 title、BGM。

    Args:
        title_templates_dir: FCP Title 模板目录。非空时自动扫描并安装模板，
                             字幕将使用自定义模板替代 Essential Title。
    """
    fcpxml = ET.Element("fcpxml", version="1.11")

    # ── 加载自定义 Title 模板（可选）──
    title_lib = None
    if title_templates_dir:
        try:
            from .title_scanner import scan_templates, install_templates
            title_lib = scan_templates(title_templates_dir)
            if title_lib.templates:
                install_templates(title_lib)
                logger.info(f"[FCPXML] 加载了 {len(title_lib.templates)} 个 Title 模板")
        except Exception as e:
            logger.warning(f"[FCPXML] Title 模板加载失败，使用 Essential Title: {e}")
            title_lib = None

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
    asset_id_counter = [2]  # r1 是 format，从 r2 开始（用 list 使循环内可修改）
    clip_ref_map = {}  # shot_id → asset ref id
    for clip in timeline.clips:
        ref_id = f"r{asset_id_counter[0]}"
        clip_ref_map[clip.shot_id] = ref_id
        asset_id_counter[0] += 1
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
        bgm_ref = f"r{asset_id_counter[0]}"
        asset_id_counter[0] += 1
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

    # 字幕 effect — 优先使用自定义 .moti 模板，降级到 Essential Title
    # 规则：每个视频固定 1 个 title 模板 + 最多 2 个 selling_point 模板，不再每 clip 轮换
    title_ref_map: dict[str, str] = {}   # style → ref_id（title/selling_point 各一个或两个）
    title_name_map: dict[str, str] = {}  # style → template name
    title_tmpl_map: dict[str, object] = {}  # style → TitleTemplate

    if title_lib and title_lib.templates:
        from .title_scanner import get_template_for_style, get_fcpxml_uid

        def _register_tmpl(tmpl, ref_key: str):
            """注册一个模板到 resources，返回 ref_id。"""
            if not tmpl or not tmpl.installed_path:
                return None
            fcp_titles_base = Path.home() / "Movies" / "Motion Templates.localized"
            try:
                rel_path = tmpl.installed_path.relative_to(fcp_titles_base)
                uid = f"~/{rel_path}"
            except ValueError:
                uid = f"~/{tmpl.installed_path}"
            ref_id = f"r{asset_id_counter[0]}"
            asset_id_counter[0] += 1
            ET.SubElement(resources, "effect", {
                "id": ref_id,
                "name": tmpl.name,
                "uid": uid,
                "src": tmpl.installed_path.resolve().as_uri(),
            })
            logger.info(f"[FCPXML] 注册模板: {tmpl.name} → {ref_id}")
            return ref_id

        # 1 个 title 模板（固定 index=0）
        t_title = get_template_for_style(title_lib, "title", 0)
        ref = _register_tmpl(t_title, "title")
        if ref:
            title_ref_map["title"] = ref
            title_name_map["title"] = t_title.name
            title_tmpl_map["title"] = t_title

        # 最多 2 个 selling_point 模板（index=0 和 index=1）
        for idx in range(2):
            t_sp = get_template_for_style(title_lib, "selling_point", idx)
            if not t_sp:
                break
            key = f"selling_point_{idx}"
            ref = _register_tmpl(t_sp, key)
            if ref:
                title_ref_map[key] = ref
                title_name_map[key] = t_sp.name
                title_tmpl_map[key] = t_sp

    # selling_point 模板轮换计数（在 clip 循环中递增）
    sp_counter = [0]

    # 兼容旧路径：custom_title_ref 指向 title 模板
    custom_title_ref = title_ref_map.get("title")
    custom_title_name = title_name_map.get("title")

    default_title_ref = f"r{asset_id_counter[0]}"
    asset_id_counter[0] += 1
    ET.SubElement(resources, "effect", {
        "id": default_title_ref,
        "name": "Essential Title",
        "uid": ".../Titles.localized/Essential Titles.localized/Essential Title.localized/Essential Title.moti",
    })

    # 转场 effect（FCP 内置 Cross Dissolve — 使用 FxPlug uid）
    dissolve_ref = f"r{asset_id_counter[0]}"
    asset_id_counter[0] += 1
    ET.SubElement(resources, "effect", {
        "id": dissolve_ref,
        "name": "交叉叠化",
        "uid": "FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265",
    })

    # ── library → event → project → sequence ──
    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="Auto Edit")
    project_elem = ET.SubElement(event, "project", name="Product Video")
    sequence = ET.SubElement(project_elem, "sequence", {
        "format": "r1",
        "tcStart": "0s",
        "tcFormat": "NDF",
    })
    bgm_elem = None  # 延后设置 duration

    spine = ET.SubElement(sequence, "spine")

    # ── spine: clips + transitions + titles + BGM ──
    ts_counter = 0
    current_offset = 0.0  # 追踪 spine 位置（用于 transition offset）

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
            # transition_duration: 优先用当前 clip 的，回退用前一个 clip 的
            trans_dur_sec = clip.transition_duration
            if trans_dur_sec <= 0:
                trans_dur_sec = timeline.clips[i - 1].transition_duration
            if trans_dur_sec <= 0:
                trans_dur_sec = 0.4  # 兜底默认
            trans_dur = _snap(trans_dur_sec)
            # transition offset = 前一个 clip 结束前 transition 重叠的起点
            trans_offset = _snap(current_offset - trans_dur / 2)
            trans_elem = ET.SubElement(spine, "transition", {
                "name": "交叉叠化",
                "offset": _rs(trans_offset),
                "duration": _rs(trans_dur),
            })
            # filter-video 自闭合（不加 data/param，FCP 用默认设置）
            ET.SubElement(trans_elem, "filter-video", {
                "ref": dissolve_ref,
                "name": "交叉叠化",
            })

        # asset-clip
        # 转场后的 clip 需要 start 属性提供前句柄（transition 需要额外素材做淡入）
        clip_attrs = {
            "ref": clip_ref_map[clip.shot_id],
            "offset": _rs(current_offset),
            "name": f"Shot {clip.shot_id}",
            "duration": _rs(clip.display_duration),
            "tcFormat": "NDF",
        }
        if need_transition:
            # 提供 transition 半长的前句柄
            handle = _snap(trans_dur / 2)
            clip_attrs["start"] = _rs(handle)
        clip_elem = ET.SubElement(spine, "asset-clip", clip_attrs)

        # BGM attached to first clip（duration 延后补齐）
        if i == 0 and bgm_ref:
            bgm_elem = ET.SubElement(clip_elem, "asset-clip", {
                "ref": bgm_ref,
                "lane": "-1",
                "offset": "0s",
                "name": "BGM",
                "duration": "0s",  # placeholder, 循环结束后更新
                "audioRole": "dialogue",
            })

        # Title attached to clip — 按 subtitle_style + subtitle_position 区分样式和位置
        if clip.subtitle_text:
            ts_counter += 1
            ts_id = f"ts{ts_counter}"
            is_title = clip.subtitle_style == "title"
            font_size = "150" if is_title else "75"

            # 选择模板: 自定义 .moti 优先，降级 Essential Title
            use_custom = bool(title_ref_map)
            tmpl = None  # 当前 clip 选中的模板

            if use_custom:
                if clip.subtitle_style == "title":
                    # title 固定用预选的那 1 个
                    use_ref = title_ref_map.get("title", default_title_ref)
                    use_name = title_name_map.get("title", "Essential Title")
                    tmpl = title_tmpl_map.get("title")
                else:
                    # selling_point 在 0/1 两个预选模板间交替
                    sp_keys = [k for k in title_ref_map if k.startswith("selling_point")]
                    if sp_keys:
                        key = sp_keys[sp_counter[0] % len(sp_keys)]
                        sp_counter[0] += 1
                        use_ref = title_ref_map[key]
                        use_name = title_name_map[key]
                        tmpl = title_tmpl_map[key]
                    else:
                        use_ref = default_title_ref
                        use_name = "Essential Title"
            else:
                use_ref = default_title_ref
                use_name = "Essential Title"

            title_elem = ET.SubElement(clip_elem, "title", {
                "ref": use_ref,
                "lane": "1",
                "offset": "0s",
                "name": use_name,
                "start": "3600s",
                "duration": _rs(clip.display_duration),
            })

            # ── Social Media Titles 模板渲染 ──
            if use_custom and title_lib and tmpl and tmpl.installed_path:
                from .title_scanner import is_social_media_template, get_social_media_config
                if is_social_media_template(tmpl):
                    cfg = get_social_media_config(tmpl)

                    # DTD 顺序：param* → text* → text-style-def* → adjust-transform?
                    # text_slots = 模板的 Text 槽位数，必须全部填满，否则未填槽显示默认占位文字
                    text_slots = cfg.get("text_slots", 1)
                    alignment = cfg.get("alignment", "center")
                    for _ in range(text_slots):
                        text_elem = ET.SubElement(title_elem, "text")
                        ts_node = ET.SubElement(text_elem, "text-style", ref=ts_id)
                        ts_node.text = clip.subtitle_text  # 所有槽填同一条文案
                    tsd = ET.SubElement(title_elem, "text-style-def", id=ts_id)
                    ET.SubElement(tsd, "text-style", {
                        "font": "Helvetica",
                        "fontSize": str(cfg.get("font_size", 60)),
                        "fontFace": "Regular",
                        "fontColor": "1 1 1 1",
                        "alignment": alignment,
                    })

                    # adjust-transform 放在最后（DTD 要求）
                    transform_attrs = {}
                    if cfg.get("position"):
                        transform_attrs["position"] = cfg["position"]
                    if cfg.get("scale"):
                        transform_attrs["scale"] = cfg["scale"]
                    if transform_attrs:
                        ET.SubElement(title_elem, "adjust-transform", transform_attrs)
                else:
                    # ── 084 SDMAC 遮罩动画模板 通用渲染 ──
                    _render_custom_title(title_elem, clip, ts_id, ts_counter)
            else:
                # ── Essential Title / 无模板 通用渲染 ──
                _render_custom_title(title_elem, clip, ts_id, ts_counter, is_custom=False)

        # 累加 offset（不减 transition 时长，FCP 按 offset 定位）
        current_offset = _snap(current_offset + clip.display_duration)

    # 结尾淡出（最后一个 clip 的 transition_out 为 fade 时）
    last_clip = timeline.clips[-1] if timeline.clips else None
    if last_clip and last_clip.transition_out == "fade":
        fade_dur = _snap(last_clip.transition_duration if last_clip.transition_duration > 0 else 0.5)
        fade_offset = _snap(current_offset - fade_dur / 2)
        fade_elem = ET.SubElement(spine, "transition", {
            "name": "交叉叠化",
            "offset": _rs(fade_offset),
            "duration": _rs(fade_dur),
        })
        ET.SubElement(fade_elem, "filter-video", {
            "ref": dissolve_ref,
            "name": "交叉叠化",
        })

    # ── 回填 sequence 和 BGM 的 duration（用实际 spine 时长）──
    spine_duration = current_offset
    sequence.set("duration", _rs(spine_duration))
    if bgm_elem is not None:
        bgm_elem.set("duration", _rs(spine_duration))

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


def _get_fcp_position(position: str, style: str) -> tuple[str, str]:
    """根据 subtitle_position 和 subtitle_style 返回 FCP Position 坐标。

    FCP 坐标系：1920x1080 画布中心 (0,0)，Y 轴向上为正。
    X 范围 ≈ -960~960，Y 范围 ≈ -540~540。

    title 样式放画面下 1/3（y≈-89），selling_point 默认右下（x=430, y=-480）。
    """
    # Y 坐标：style 决定垂直层级
    if style == "title":
        y_top, y_bottom = "400", "-89"
    else:
        y_top, y_bottom = "430", "-480"

    y_map = {
        "top_left": y_top, "top_center": y_top, "top_right": y_top,
        "bottom_left": y_bottom, "bottom_center": y_bottom, "bottom_right": y_bottom,
    }

    # X 坐标：position 决定水平位置
    # selling_point bottom_center 默认偏右 430（LLM 指定其他位置时走各自映射）
    if style == "selling_point":
        x_map = {
            "top_left": "-600", "top_center": "0", "top_right": "600",
            "bottom_left": "-600", "bottom_center": "430", "bottom_right": "600",
        }
    else:
        x_map = {
            "top_left": "-600", "top_center": "0", "top_right": "600",
            "bottom_left": "-600", "bottom_center": "0", "bottom_right": "600",
        }

    x_pos = x_map.get(position, "430" if style == "selling_point" else "0")
    y_pos = y_map.get(position, y_bottom)
    return x_pos, y_pos


def _make_element(tag: str, attribs: dict) -> ET.Element:
    """Helper to create an Element with attributes."""
    return ET.Element(tag, attribs)


def _render_custom_title(
    title_elem: ET.Element,
    clip,
    ts_id: str,
    ts_counter: int,
    is_custom: bool = True,
) -> None:
    """084 SDMAC 遮罩动画模板 / Essential Title 通用字幕渲染。"""
    is_title = clip.subtitle_style == "title"
    font_size = "150" if is_title else "75"

    x_pos, y_pos = _get_fcp_position(clip.subtitle_position, clip.subtitle_style)
    if is_custom:
        ET.SubElement(title_elem, "param", {
            "name": "位置",
            "key": "9999/2310926/2310927/1/100/101",
            "value": f"{x_pos} {y_pos}",
        })
    else:
        ET.SubElement(title_elem, "param", {
            "name": "Position",
            "key": "9999/10085/10086/1/100/101",
            "value": f"{x_pos} {y_pos}",
        })

    text_elem = ET.SubElement(title_elem, "text")
    ts_node = ET.SubElement(text_elem, "text-style", ref=ts_id)
    ts_node.text = clip.subtitle_text
    tsd = ET.SubElement(title_elem, "text-style-def", id=ts_id)
    alignment = (
        "left" if "left" in clip.subtitle_position
        else "right" if "right" in clip.subtitle_position
        else "center"
    )
    style_attrs = {
        "font": "Impact",
        "fontSize": font_size,
        "fontFace": "Regular",
        "fontColor": "1 1 1 1",
        "bold": "1",
        "baseline": "1",
        "alignment": alignment,
    }
    if not is_title and not is_custom:
        style_attrs["shadowColor"] = "0 0 0 0.75"
        style_attrs["shadowOffset"] = "3 315"
    ET.SubElement(tsd, "text-style", style_attrs)
