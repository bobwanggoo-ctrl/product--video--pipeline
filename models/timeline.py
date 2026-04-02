"""Data models for timeline and EDL export."""

from pydantic import BaseModel, Field


class TimelineClip(BaseModel):
    """A single clip in the editing timeline."""
    shot_id: int
    scene_group_id: int = 0
    source_path: str
    trim_start: float = 0.0
    trim_end: float = 0.0
    display_duration: float = 2.5
    speed_factor: float = Field(default=1.0, ge=1.0, le=2.0)  # 1.0-2.0x，禁止慢放
    subtitle_text: str = ""
    subtitle_text_cn: str = ""   # 中文回译，与英文严格对应
    subtitle_style: str = "selling_point"  # "title" | "selling_point"
    transition_in: str = "cut"  # "cut" | "fade" | "dissolve"
    transition_out: str = "cut"
    transition_duration: float = 0.4


class EditingTimeline(BaseModel):
    """Complete editing timeline."""
    clips: list[TimelineClip] = Field(default_factory=list)
    bgm_path: str = ""
    bgm_volume: float = 1.0
    bgm_fade_out_sec: float = 2.0
    total_duration: float = 0.0
    resolution: str = "1920x1080"
    fps: float = 24.0


class BgmInfo(BaseModel):
    """BGM library entry."""
    name: str
    duration: float
    path: str


class LLMEditingDecision(BaseModel):
    """LLM-generated editing decisions."""
    bgm_choice: str = ""
    bgm_reason: str = ""
    subtitles: list[dict] = Field(default_factory=list)
    clip_order: list[int] = Field(default_factory=list)
    rejected_shots: list[dict] = Field(default_factory=list)
    structure: dict = Field(default_factory=dict)


class EDLEvent(BaseModel):
    """A single EDL event (CMX 3600 format)."""
    event_num: int
    reel: str
    track: str = "V"
    edit_type: str = "C"  # C=cut, D=dissolve, W=wipe
    transition_duration: str = "00:00:00:00"
    source_in: str = "00:00:00:00"
    source_out: str = "00:00:00:00"
    record_in: str = "00:00:00:00"
    record_out: str = "00:00:00:00"
