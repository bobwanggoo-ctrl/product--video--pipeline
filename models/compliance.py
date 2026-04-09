"""Data models for compliance checking."""

from enum import Enum
from pydantic import BaseModel, Field


class ComplianceLevel(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class ComplianceIssue(BaseModel):
    """A single compliance issue found in a frame."""
    category: str  # Gemini Vision: "geometry"|"texture"|"proportion"|"color"|"anatomy"|"artifact"
                   # Google Vision API: "copyright_logo"|"copyright_stock"|"copyright_ip"|"copyright_web"
    description: str
    severity: ComplianceLevel = ComplianceLevel.WARN


class LayoutHint(BaseModel):
    """字幕排版建议，传递给 Skill 5。"""
    shot_id: int
    primary_position: str = "bottom_center"
    fallback_position: str = "bottom_left"
    reason: str = ""
    avoid_zone: str = ""


class ComplianceResult(BaseModel):
    """Result of compliance check for a single frame."""
    shot_id: int
    frame_path: str
    reference_path: str = ""
    level: ComplianceLevel = ComplianceLevel.PASS
    score: float = 1.0  # 0.0 - 1.0
    issues: list[ComplianceIssue] = Field(default_factory=list)
    error_keywords: list[str] = Field(default_factory=list)
    summary: str = ""
