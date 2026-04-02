"""Data models for video clips and generation."""

from pydantic import BaseModel, Field


class VideoClip(BaseModel):
    """A single generated video clip."""
    shot_id: int
    scene_group_id: int = 0
    source_frame_path: str = ""
    video_path: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    motion_hint: str = ""
    kling_task_id: str = ""
    status: str = "pending"  # "pending" | "generating" | "done" | "failed"


class ClipAnalysis(BaseModel):
    """Analysis result for a single video clip."""
    file_path: str
    duration: float
    width: int
    height: int
    fps: float
    static_head_sec: float = 0.0
    static_tail_sec: float = 0.0
    usable_start: float = 0.0
    usable_end: float = 0.0
    is_rejected: bool = False
    # Storyboard 关联（来自 Skill 1 + Skill 4）
    shot_id: int = 0
    shot_type: str = ""          # Wide / Medium / Close / Macro
    purpose: str = ""
    scene_group_id: int = 0
    motion_prompt: str = ""      # 来自 Skill 4，含运镜方向信息
    scene_description: str = ""  # Vision 识别的画面描述
    prompt_cn: str = ""          # Storyboard 中文提示词（生成时的意图）
    quality_score: float = -1.0  # Vision 质量评分 0-10，-1 表示未检测
    quality_issues: str = ""     # Vision 发现的问题描述
