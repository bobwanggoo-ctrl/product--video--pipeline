"""Data models for compliance checking."""

from enum import Enum
from pydantic import BaseModel, Field


class ComplianceLevel(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class ComplianceIssue(BaseModel):
    """A single compliance issue found in a frame."""
    category: str  # "shape" | "pattern" | "text" | "size" | "style"
    description: str
    severity: ComplianceLevel
    suggestion: str = ""


class ComplianceResult(BaseModel):
    """Result of compliance check for a single frame."""
    shot_id: int
    frame_path: str
    reference_path: str
    level: ComplianceLevel = ComplianceLevel.PASS
    score: float = 1.0  # 0.0 - 1.0
    issues: list[ComplianceIssue] = Field(default_factory=list)
    summary: str = ""
