"""FFmpeg / FFprobe subprocess wrappers."""

import json
import subprocess
from pathlib import Path


def run_ffmpeg(args: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess:
    """Execute ffmpeg command, raise on failure."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)


def run_ffprobe_json(file_path: str) -> dict:
    """Get media info via ffprobe, return JSON dict."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def get_video_info(file_path: str) -> dict:
    """Extract video basics: duration, width, height, fps."""
    probe = run_ffprobe_json(file_path)
    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        raise ValueError(f"No video stream found: {file_path}")

    duration = float(probe.get("format", {}).get("duration", 0))
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    fps_str = video_stream.get("r_frame_rate", "30/1")
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 30.0
    else:
        fps = float(fps_str)

    return {"duration": duration, "width": width, "height": height, "fps": fps}


def trim_video(input_path: str, output_path: str, start: float, end: float) -> str:
    """Trim video segment (strip audio), return output path."""
    run_ffmpeg([
        "-i", str(input_path),
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-an",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        str(output_path),
    ])
    return output_path


def concat_with_xfade(
    input_paths: list[str],
    output_path: str,
    transition: str = "fade",
    transition_duration: float = 0.4,
    clip_durations: list[float] | None = None,
    transitions: list[dict] | None = None,
) -> str:
    """Concatenate multiple video clips with xfade transitions.

    Args:
        input_paths: 视频文件路径列表。
        output_path: 输出路径。
        transition: 全局默认转场类型（当 transitions 未指定时使用）。
        transition_duration: 全局默认转场时长。
        clip_durations: 每个片段的时长列表（可选，自动检测）。
        transitions: per-clip 转场参数列表（可选），长度 = len(input_paths) - 1。
            每项为 {"type": "fade"|"dissolve"|"cut", "duration": 0.4}。
            type 为 "cut" 时该位置不使用 xfade（硬切）。
    """
    if len(input_paths) < 2:
        if input_paths:
            run_ffmpeg(["-i", str(input_paths[0]), "-c", "copy", str(output_path)])
        return output_path

    if clip_durations is None:
        clip_durations = [get_video_info(p)["duration"] for p in input_paths]

    # 构建 per-clip 转场参数
    n_transitions = len(input_paths) - 1
    if transitions is None:
        transitions = [{"type": transition, "duration": transition_duration}] * n_transitions
    elif len(transitions) < n_transitions:
        # 不足的部分用默认值补齐
        transitions = list(transitions) + [
            {"type": transition, "duration": transition_duration}
        ] * (n_transitions - len(transitions))

    # 分离 cut 和 xfade 段：连续的 cut 用 concat 协议，xfade 段用 xfade 滤镜
    # 简化策略：所有 cut 转场用 offset = clip_duration（无重叠），xfade 正常处理
    inputs = []
    for p in input_paths:
        inputs.extend(["-i", str(p)])

    # 构建 xfade filter chain（per-clip 转场）
    # xfade offset = 到目前为止合成流的总时长 - 当前转场重叠时长
    # 合成流总时长在每次 xfade 后更新为: prev_total + clip_dur - t_dur
    filter_parts = []
    prev_label = "[0:v]"
    composed_duration = clip_durations[0]  # 当前合成流的总时长

    for i in range(1, len(input_paths)):
        t = transitions[i - 1]
        t_type = t.get("type", "cut")
        t_dur = float(t.get("duration", 0.4)) if t_type != "cut" else 0.05
        # cut 用 0.05s 极短 fade 模拟（0.001 会因浮点精度导致 xfade 截断）

        xfade_offset = composed_duration - t_dur

        out_label = f"[v{i-1}{i}]" if i < len(input_paths) - 1 else "[vout]"
        cur_label = f"[{i}:v]"

        xfade_name = t_type if t_type in (
            "fade", "dissolve", "wipeleft", "wiperight", "wipeup", "wipedown",
            "slideleft", "slideright", "slideup", "slidedown",
            "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite",
            "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
        ) else "fade"

        filter_parts.append(
            f"{prev_label}{cur_label}xfade=transition={xfade_name}"
            f":duration={t_dur:.3f}:offset={xfade_offset:.3f}{out_label}"
        )

        prev_label = out_label
        composed_duration = xfade_offset + t_dur + clip_durations[i] - t_dur

    run_ffmpeg(inputs + [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        str(output_path),
    ])
    return output_path


def mix_bgm(
    video_path: str,
    bgm_path: str,
    output_path: str,
    *,
    video_duration: float,
    bgm_volume: float = 1.0,
    fade_out_sec: float = 2.0,
) -> str:
    """Mix BGM into video (remove original audio, fade out BGM)."""
    fade_start = max(0, video_duration - fade_out_sec)
    filter_complex = (
        f"[1:a]atrim=0:{video_duration:.3f},"
        f"afade=t=out:st={fade_start:.3f}:d={fade_out_sec:.3f},"
        f"volume={bgm_volume:.2f}[bgm]"
    )
    run_ffmpeg([
        "-i", str(video_path),
        "-i", str(bgm_path),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[bgm]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ])
    return output_path


def get_audio_duration(file_path: str) -> float:
    """Get audio file duration in seconds."""
    probe = run_ffprobe_json(file_path)
    return float(probe.get("format", {}).get("duration", 0))
