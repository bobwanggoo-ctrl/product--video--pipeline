"""Skill 5 Module A: 视频片段技术分析。

用 FFmpeg 获取每个片段的技术参数，关联 Storyboard 内容数据，
输出 ClipAnalysis 列表供 LLM 剪辑决策使用。
"""

import logging
from pathlib import Path

from models.video_clip import ClipAnalysis
from models.storyboard import Storyboard
from utils.ffmpeg_wrapper import get_video_info

logger = logging.getLogger(__name__)


def analyze_clips(
    video_paths: list[str],
    storyboard: Storyboard,
    motion_results: list[dict] | None = None,
) -> list[ClipAnalysis]:
    """分析所有视频片段，返回 ClipAnalysis 列表。

    Args:
        video_paths: 视频文件路径列表，顺序与 storyboard shots 一致。
        storyboard: Skill 1 输出的分镜数据。
        motion_results: Skill 4 输出的运镜结果列表（可选）。
            每项需包含 shot_id 和 motion_prompt。

    Returns:
        list[ClipAnalysis]，每个片段的技术参数 + Storyboard 内容关联。
    """
    # 展平 storyboard shots，按顺序索引
    shots_flat: list[dict] = []
    for sg in storyboard.scene_groups:
        for shot in sg.shots:
            shots_flat.append({
                "shot_id": shot.shot_id,
                "shot_type": shot.type,
                "purpose": shot.purpose,
                "scene_group_id": sg.scene_group_id,
            })

    # 构建 motion_prompt 查找表
    motion_map: dict[int, str] = {}
    if motion_results:
        for m in motion_results:
            motion_map[m["shot_id"]] = m.get("motion_prompt", "")

    results: list[ClipAnalysis] = []

    for i, video_path in enumerate(video_paths):
        path = Path(video_path)
        shot_info = shots_flat[i] if i < len(shots_flat) else {}
        shot_id = shot_info.get("shot_id", i + 1)

        # 文件不存在 → 废片
        if not path.exists():
            logger.warning(f"Shot {shot_id}: 文件不存在 {video_path}，标记为废片")
            results.append(_make_rejected(video_path, shot_id, shot_info, motion_map))
            continue

        # FFmpeg 技术分析
        try:
            info = get_video_info(str(video_path))
        except Exception as e:
            logger.error(f"Shot {shot_id}: FFmpeg 分析失败 {video_path}: {e}")
            results.append(_make_rejected(video_path, shot_id, shot_info, motion_map))
            continue

        duration = info["duration"]
        is_rejected = duration < 0.5

        results.append(ClipAnalysis(
            file_path=str(video_path),
            duration=duration,
            width=info["width"],
            height=info["height"],
            fps=info["fps"],
            usable_start=0.0,
            usable_end=duration,
            is_rejected=is_rejected,
            shot_id=shot_id,
            shot_type=shot_info.get("shot_type", ""),
            purpose=shot_info.get("purpose", ""),
            scene_group_id=shot_info.get("scene_group_id", 0),
            motion_prompt=motion_map.get(shot_id, ""),
        ))

        if is_rejected:
            logger.warning(f"Shot {shot_id}: 时长 {duration:.1f}s 过短，标记为废片")
        else:
            logger.info(
                f"Shot {shot_id}: {info['width']}x{info['height']} "
                f"{info['fps']:.0f}fps {duration:.1f}s [{shot_info.get('shot_type', '')}]"
            )

    usable = sum(1 for r in results if not r.is_rejected)
    rejected = sum(1 for r in results if r.is_rejected)
    logger.info(f"分析完成: {len(results)} 个片段, {usable} 个可用, {rejected} 个废片")
    return results


def _make_rejected(
    video_path: str,
    shot_id: int,
    shot_info: dict,
    motion_map: dict[int, str],
) -> ClipAnalysis:
    """构造一个废片 ClipAnalysis。"""
    return ClipAnalysis(
        file_path=str(video_path),
        duration=0.0, width=0, height=0, fps=0.0,
        is_rejected=True,
        shot_id=shot_id,
        shot_type=shot_info.get("shot_type", ""),
        purpose=shot_info.get("purpose", ""),
        scene_group_id=shot_info.get("scene_group_id", 0),
        motion_prompt=motion_map.get(shot_id, ""),
    )
