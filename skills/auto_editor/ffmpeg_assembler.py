"""Skill 5 Module B: FFmpeg 视频组装。

按 EditingTimeline 的决策执行：
trim → 变速 → 逐片段转场拼接 → 字幕烧录 → BGM 混入 → 输出 MP4。
"""

import logging
import tempfile
from pathlib import Path

from models.timeline import EditingTimeline
from utils.ffmpeg_wrapper import (
    concat_with_xfade, mix_bgm, run_ffmpeg, get_video_info,
)

logger = logging.getLogger(__name__)

# 变速后最小展示时长（兜底校验，与 llm_editor 一致）
MIN_DISPLAY_DURATION = 1.5
VALID_SPEED_FACTORS = [1.0, 1.25, 1.5, 1.75, 2.0]


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

        # 构建 ffmpeg 滤镜
        filters = []
        if speed != 1.0:
            filters.append(f"setpts=PTS/{speed}")

        # trim + 变速 + 去音频
        filter_arg = ["-vf", ",".join(filters)] if filters else []
        run_ffmpeg([
            "-i", clip.source_path,
            "-ss", f"{clip.trim_start:.3f}",
            "-to", f"{clip.trim_end:.3f}",
            "-an",
            *filter_arg,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
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

    # Step 3: 字幕烧录（如有 SRT）
    if srt_path and Path(srt_path).exists():
        logger.info("Step 3: 烧录字幕")
        subtitled_out = str(workdir / "subtitled.mp4")
        _burn_subtitles(concat_out, srt_path, subtitled_out)
        current_output = subtitled_out
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
        if t_type == "cut" and next_clip.transition_in != "cut":
            t_type = next_clip.transition_in

        transitions.append({
            "type": t_type,
            "duration": curr.transition_duration if t_type != "cut" else 0.0,
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


def _burn_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    """用 ffmpeg subtitles 滤镜烧录 SRT 字幕到视频。"""
    # 使用 subtitles 滤镜，自动处理字体和样式
    # 路径中的特殊字符需要转义
    escaped_srt = str(srt_path).replace("\\", "\\\\").replace(":", "\\:")
    run_ffmpeg([
        "-i", video_path,
        "-vf", f"subtitles='{escaped_srt}':force_style='FontSize=22,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        output_path,
    ])
