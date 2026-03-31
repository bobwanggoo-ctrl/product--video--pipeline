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
    # VideoDB analysis result
    scene_description: str = ""
