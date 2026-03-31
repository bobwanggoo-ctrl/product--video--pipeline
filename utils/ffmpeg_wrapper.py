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
) -> str:
    """Concatenate multiple video clips with xfade transitions."""
    if len(input_paths) < 2:
        if input_paths:
            run_ffmpeg(["-i", str(input_paths[0]), "-c", "copy", str(output_path)])
        return output_path

    if clip_durations is None:
        clip_durations = [get_video_info(p)["duration"] for p in input_paths]

    inputs = []
    for p in input_paths:
        inputs.extend(["-i", str(p)])

    # Build xfade filter chain
    filter_parts = []
    offset = clip_durations[0] - transition_duration
    prev_label = "[0:v]"

    for i in range(1, len(input_paths)):
        out_label = f"[v{i-1}{i}]" if i < len(input_paths) - 1 else "[vout]"
        cur_label = f"[{i}:v]"
        filter_parts.append(
            f"{prev_label}{cur_label}xfade=transition={transition}"
            f":duration={transition_duration:.3f}:offset={offset:.3f}{out_label}"
        )
        prev_label = out_label
        if i < len(input_paths) - 1:
            offset += clip_durations[i] - transition_duration

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
