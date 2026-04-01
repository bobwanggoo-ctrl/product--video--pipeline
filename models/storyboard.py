"""Data models for storyboard generation."""

from pydantic import BaseModel, Field


class Shot(BaseModel):
    """Single storyboard shot."""
    shot_id: int
    type: str  # "Wide" | "Medium" | "Close" | "Macro"
    purpose: str
    prompt_cn: str
    motion_hint: str = ""  # e.g. "缓慢推进，从左至右平移"


class SceneGroup(BaseModel):
    """A group of shots sharing the same scene environment."""
    scene_group_id: int
    name: str
    environment_anchor: str
    shots: list[Shot] = Field(default_factory=list)


class Storyboard(BaseModel):
    """Complete storyboard output from sellpoint conversion."""
    product_type: str = ""
    product_type_reason: str = ""
    model_profile: str = ""
    director_plan: dict = Field(default_factory=dict)
    scene_groups: list[SceneGroup] = Field(default_factory=list)

    @property
    def total_shots(self) -> int:
        return sum(len(sg.shots) for sg in self.scene_groups)
