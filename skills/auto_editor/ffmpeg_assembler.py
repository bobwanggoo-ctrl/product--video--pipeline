"""Skill 5 Module B: FFmpeg 视频组装。

按 EditingTimeline 的决策执行：
trim → 变速 → 逐片段转场拼接 → 字幕烧录 → BGM 混入 → 输出 MP4。
"""

import logging
import tempfile
from pathlib import Path

from models.timeline import EditingTimeline
from skills.auto_editor.subtitle_gen import generate_srt_from_actual_durations
from utils.ffmpeg_wrapper import (
    concat_with_xfade, mix_bgm, run_ffmpeg, get_video_info,
)

logger = logging.getLogger(__name__)

# 变速后最小展示时长（兜底校验，与 llm_editor 一致）
MIN_DISPLAY_DURATION = 1.5
VALID_SPEED_FACTORS = [1.0, 1.25, 1.5, 1.75, 2.0]

# 字幕字体查找顺序：env SUBTITLE_FONT > 项目内置思源黑体 > 系统字体
import os
import platform

_BUNDLED_FONT = (
    Path(__file__).resolve().parent.parent.parent
    / "assets/fonts/source-han-sans/SourceHanSansCN-Regular.otf"
)

_SYSTEM_FONT_CANDIDATES = {
    "Darwin": [
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "Windows": [
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",     # 黑体
        "C:/Windows/Fonts/arial.ttf",
    ],
    "Linux": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


def _resolve_subtitle_font() -> str | None:
    """查找可用的字幕字体文件。找不到返回 None，调用方应跳过字幕烧录。"""
    env_font = os.environ.get("SUBTITLE_FONT")
    if env_font and Path(env_font).is_file():
        return env_font
    if _BUNDLED_FONT.is_file():
        return str(_BUNDLED_FONT)
    for candidate in _SYSTEM_FONT_CANDIDATES.get(platform.system(), []):
        if Path(candidate).is_file():
            return candidate
    return None


def _escape_fontfile_for_drawtext(font_path: str) -> str:
    """转义字体路径给 ffmpeg drawtext 使用。
    Windows 反斜杠先转正斜杠，再转义冒号和空格。
    """
    path = font_path.replace("\\", "/")
    return path.replace(":", "\\:").replace(" ", "\\ ")


def assemble(
    timeline: EditingTimeline,
    output_path: str,
    srt_path: str = "",
    *,
    temp_dir: str | None = None,
) -> str:
    """按 EditingTimeline 组装最终 MP4。

    Args:
        timeline: 剪辑时间线。
        output_path: 最终 MP4 输出路径。
        srt_path: SRT 字幕文件路径（如有则烧录到视频）。
        temp_dir: 临时文件目录（默认系统 temp）。

    Returns:
        输出文件路径。
    """
    if not timeline.clips:
        raise ValueError("时间线为空，没有可组装的片段")

    workdir = Path(temp_dir) if temp_dir else Path(tempfile.mkdtemp(prefix="assembler_"))
    workdir.mkdir(parents=True, exist_ok=True)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Step 1: 逐片段 trim + 变速
    logger.info(f"Step 1: 处理 {len(timeline.clips)} 个片段 (trim + 变速)")
    processed_paths: list[str] = []
    processed_durations: list[float] = []

    for i, clip in enumerate(timeline.clips):
        out_file = str(workdir / f"clip_{i:03d}_shot{clip.shot_id}.mp4")

        # 变速兜底校验
        speed = _safe_speed(clip.speed_factor, clip.trim_end - clip.trim_start, clip.shot_id)

        # 构建 ffmpeg 滤镜（始终包含 setpts 重置时间戳，xfade 需要 PTS 从 0 开始）
        vf_parts = ["setpts=PTS-STARTPTS"]
        if speed != 1.0:
            vf_parts.append(f"setpts=PTS/{speed}")

        # trim + 变速 + 去音频
        # 注意：-ss 放在 -i 之前是 input seeking（快速但 PTS 可能不从 0 开始）
        # 加 setpts=PTS-STARTPTS 强制重置
        trimmed_duration = clip.trim_end - clip.trim_start
        run_ffmpeg([
            "-ss", f"{clip.trim_start:.3f}",
            "-i", clip.source_path,
            "-t", f"{trimmed_duration:.3f}",
            "-an",
            "-vf", ",".join(vf_parts),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            out_file,
        ])

        # 获取实际处理后时长
        actual_duration = get_video_info(out_file)["duration"]
        processed_paths.append(out_file)
        processed_durations.append(actual_duration)

        logger.info(
            f"  Shot {clip.shot_id}: trim [{clip.trim_start:.1f}-{clip.trim_end:.1f}] "
            f"× {speed}x → {actual_duration:.2f}s"
        )

    # Step 2: 逐片段转场拼接
    logger.info("Step 2: 拼接片段 (per-clip 转场)")
    transitions = _build_transitions(timeline)
    concat_out = str(workdir / "concat.mp4")
    concat_with_xfade(
        processed_paths, concat_out,
        clip_durations=processed_durations,
        transitions=transitions,
    )

    # Step 2.5: 结尾淡出黑场（如果最后一个 clip 的 transition_out 是 fade）
    last_clip = timeline.clips[-1]
    if last_clip.transition_out == "fade":
        logger.info("Step 2.5: 结尾淡出黑场")
        fade_out_path = str(workdir / "fade_out.mp4")
        concat_duration = get_video_info(concat_out)["duration"]
        fade_start = max(0, concat_duration - last_clip.transition_duration)
        run_ffmpeg([
            "-i", concat_out,
            "-vf", f"fade=t=out:st={fade_start:.3f}:d={last_clip.transition_duration:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            fade_out_path,
        ])
        concat_out = fade_out_path

    # Step 3: 字幕烧录（用实际视频时长重算字幕时间）
    if srt_path and _has_drawtext_support():
        logger.info("Step 3: 用实际时长重算字幕时间 + 烧录")
        # 用 processed_durations（实际 ffmpeg 输出时长）替代理论 display_duration
        actual_srt_path = str(workdir / "subtitles_actual.srt")
        generate_srt_from_actual_durations(
            timeline, actual_durations=processed_durations,
            output_path=actual_srt_path,
        )
        subtitled_out = str(workdir / "subtitled.mp4")
        _burn_subtitles(concat_out, actual_srt_path, subtitled_out, timeline=timeline)
        current_output = subtitled_out
    else:
        if srt_path:
            logger.info("Step 3: 跳过字幕烧录（ffmpeg 缺少 drawtext 滤镜，字幕仅输出 SRT 文件）")
        else:
            logger.info("Step 3: 跳过字幕烧录（无 SRT 文件）")
        current_output = concat_out

    # Step 4: 混入 BGM
    if timeline.bgm_path and Path(timeline.bgm_path).exists():
        logger.info(f"Step 4: 混入 BGM → {timeline.bgm_path}")
        video_duration = get_video_info(current_output)["duration"]
        mix_bgm(
            current_output, timeline.bgm_path, output_path,
            video_duration=video_duration,
            bgm_volume=timeline.bgm_volume,
            fade_out_sec=timeline.bgm_fade_out_sec,
        )
    else:
        logger.info("Step 4: 跳过 BGM（无 BGM 文件）")
        # 直接复制到输出路径
        run_ffmpeg(["-i", current_output, "-c", "copy", output_path])

    final_info = get_video_info(output_path)
    logger.info(
        f"组装完成: {output_path} | "
        f"{final_info['width']}x{final_info['height']} | "
        f"{final_info['duration']:.1f}s"
    )
    return output_path


def _build_transitions(timeline: EditingTimeline) -> list[dict]:
    """从 EditingTimeline 构建 per-clip 转场参数列表。"""
    transitions = []
    for i in range(len(timeline.clips) - 1):
        curr = timeline.clips[i]
        next_clip = timeline.clips[i + 1]

        # 使用当前 clip 的 transition_out 作为两者之间的转场
        t_type = curr.transition_out
        t_dur = curr.transition_duration
        if t_type == "cut" and next_clip.transition_in != "cut":
            t_type = next_clip.transition_in
            t_dur = next_clip.transition_duration  # 回退时用 next 的时长

        transitions.append({
            "type": t_type,
            "duration": t_dur if t_type != "cut" else 0.0,
        })
    return transitions


def _safe_speed(speed_factor: float, trimmed_duration: float, shot_id: int) -> float:
    """兜底变速校验（与 llm_editor._validate_speed 逻辑一致）。"""
    speed = max(1.0, min(2.0, speed_factor))
    speed = min(VALID_SPEED_FACTORS, key=lambda x: abs(x - speed))

    if trimmed_duration / speed < MIN_DISPLAY_DURATION:
        for sf in reversed(VALID_SPEED_FACTORS):
            if trimmed_duration / sf >= MIN_DISPLAY_DURATION:
                logger.warning(f"Shot {shot_id}: 兜底降速 {speed}x → {sf}x")
                return sf
        return 1.0
    return speed


def _burn_subtitles(video_path: str, srt_path: str, output_path: str, *, timeline: EditingTimeline | None = None) -> None:
    """用 ffmpeg drawtext 滤镜逐条烧录 SRT 字幕到视频。

    根据 timeline 中每个 clip 的 subtitle_style 和 subtitle_position 定位字幕。
    使用 filter_script 文件避免 shell 转义问题。
    """
    entries = _parse_srt(srt_path)
    if not entries:
        run_ffmpeg(["-i", video_path, "-c", "copy", output_path])
        return

    font_path = _resolve_subtitle_font()
    if not font_path:
        logger.warning(
            "[Subtitle] 找不到可用字体文件（项目内置思源黑体 + 系统字体都未命中），"
            "跳过字幕烧录。输出视频不含字幕，SRT 文件仍会保留。"
        )
        run_ffmpeg(["-i", video_path, "-c", "copy", output_path])
        return
    logger.info(f"[Subtitle] 使用字体: {font_path}")
    fontfile_escaped = _escape_fontfile_for_drawtext(font_path)

    # 构建 clip 字幕元数据查找表（按字幕序号顺序）
    clip_subtitle_info = []
    if timeline:
        for clip in timeline.clips:
            if clip.subtitle_text:
                clip_subtitle_info.append({
                    "style": clip.subtitle_style,
                    "position": clip.subtitle_position,
                })

    # 构建 drawtext filter chain，写入 filter script 文件
    filters = []
    for i, entry in enumerate(entries):
        # drawtext 特殊字符转义：
        # \ → \\, ' → \u2019, : → \:, % → %%, ; → \;
        # 另外 filter_script 模式中还需转义 , → \,（逗号分隔滤镜）
        text = (entry["text"]
                .replace("\\", "\\\\")
                .replace("'", "\u2019")
                .replace(":", "\\:")
                .replace("%", "%%")
                .replace(";", "\\;")
                .replace("&", "\\&")
                .replace("!", "\\!")
                .replace("[", "\\[")
                .replace("]", "\\]")
                .replace("=", "\\=")
                )
        start = entry["start"]
        end = entry["end"]

        # 根据 subtitle_style 和 subtitle_position 决定字号和位置
        info = clip_subtitle_info[i] if i < len(clip_subtitle_info) else {}
        style = info.get("style", "selling_point")
        position = info.get("position", "bottom_center")

        fontsize = 52 if style == "title" else 40
        x_expr, y_expr = _get_drawtext_position(position, style)

        filters.append(
            f"drawtext=text='{text}'"
            f":fontfile='{fontfile_escaped}'"
            f":fontsize={fontsize}:fontcolor=white:borderw=3:bordercolor=black"
            f":x={x_expr}:y={y_expr}"
            f":enable='between(t,{start:.3f},{end:.3f})'"
        )

    filter_str = ",".join(filters)

    # 写入临时 filter script 文件（避免命令行转义问题）
    script_path = Path(video_path).parent / "filter_script.txt"
    script_path.write_text(filter_str, encoding="utf-8")

    run_ffmpeg([
        "-i", video_path,
        "-filter_script:v", str(script_path),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        output_path,
    ])


def _get_drawtext_position(position: str, style: str) -> tuple[str, str]:
    """根据 subtitle_position 和 subtitle_style 返回 drawtext 的 x/y 表达式。

    position: top_left / top_center / top_right / bottom_left / bottom_center / bottom_right
    style: title（大字，画面下1/3） / selling_point（小字，紧贴底部）
    """
    # title 样式放画面下 1/3（y ≈ h*0.65），selling_point 紧贴底部
    if style == "title":
        y_map = {
            "top_left": "th+40", "top_center": "th+40", "top_right": "th+40",
            "bottom_left": "h*2/3-th", "bottom_center": "h*2/3-th", "bottom_right": "h*2/3-th",
        }
    else:
        y_map = {
            "top_left": "th+20", "top_center": "th+20", "top_right": "th+20",
            "bottom_left": "h-th-60", "bottom_center": "h-th-60", "bottom_right": "h-th-60",
        }

    x_map = {
        "top_left": "20", "top_center": "(w-text_w)/2", "top_right": "w-text_w-20",
        "bottom_left": "20", "bottom_center": "(w-text_w)/2", "bottom_right": "w-text_w-20",
    }

    x_expr = x_map.get(position, "(w-text_w)/2")
    y_expr = y_map.get(position, "h-th-60")
    return x_expr, y_expr


def _parse_srt(srt_path: str) -> list[dict]:
    """解析 SRT 文件，返回 [{start, end, text}, ...]。"""
    import re

    content = Path(srt_path).read_text(encoding="utf-8")
    entries = []

    # SRT 格式: index\nHH:MM:SS,mmm --> HH:MM:SS,mmm\ntext\n
    blocks = re.split(r'\n\n+', content.strip())
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue

        time_match = re.match(
            r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})',
            lines[1]
        )
        if not time_match:
            continue

        g = time_match.groups()
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000
        text = " ".join(lines[2:])

        entries.append({"start": start, "end": end, "text": text})

    return entries


def _has_drawtext_support() -> bool:
    """检查 ffmpeg 是否编译了 drawtext 滤镜（依赖 libfreetype）。"""
    import subprocess
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=5,
        )
        return "drawtext" in result.stdout
    except Exception:
        return False
