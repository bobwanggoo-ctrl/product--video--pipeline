"""Pipeline Orchestrator: controls flow, state, retries, and human checkpoints.

Mode B (semi-auto): pauses after each step for user confirmation.
Mode A (full-auto): runs all steps without pausing (future).

To be fully implemented in Step 6.
"""

import json
import logging
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Any

from config import settings

logger = logging.getLogger(__name__)


class PipelineStep(str, Enum):
    SELLPOINT_TO_STORYBOARD = "sellpoint_to_storyboard"
    STORYBOARD_TO_FRAME = "storyboard_to_frame"
    COMPLIANCE_CHECK = "compliance_check"
    FRAME_TO_VIDEO = "frame_to_video"
    AUTO_EDIT = "auto_edit"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_CONFIRM = "awaiting_confirm"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepState:
    step: PipelineStep
    status: StepStatus = StepStatus.PENDING
    input_data: Any = None
    output_data: Any = None
    error: Optional[str] = None
    attempts: int = 0


@dataclass
class PipelineState:
    """Full pipeline state, serializable for checkpoint/resume."""
    task_id: str = ""
    mode: str = "semi_auto"  # "semi_auto" | "full_auto"
    steps: dict[str, StepState] = field(default_factory=dict)
    current_step: Optional[str] = None

    def __post_init__(self):
        if not self.steps:
            for step in PipelineStep:
                self.steps[step.value] = StepState(step=step)

    def save(self, path: Path):
        """Save state to JSON for checkpoint/resume."""
        data = {
            "task_id": self.task_id,
            "mode": self.mode,
            "current_step": self.current_step,
            "steps": {
                name: {
                    "status": s.status.value,
                    "attempts": s.attempts,
                    "error": s.error,
                }
                for name, s in self.steps.items()
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "PipelineState":
        """Load state from checkpoint file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        state = cls(task_id=data["task_id"], mode=data["mode"], current_step=data.get("current_step"))
        for name, step_data in data.get("steps", {}).items():
            if name in state.steps:
                state.steps[name].status = StepStatus(step_data["status"])
                state.steps[name].attempts = step_data.get("attempts", 0)
                state.steps[name].error = step_data.get("error")
        return state


class PipelineOrchestrator:
    """Orchestrates the full pipeline in semi-auto or full-auto mode.

    Semi-auto (Mode B): Each step pauses for user confirmation.
    Full-auto (Mode A): Runs all steps sequentially without pausing.
    """

    STEP_ORDER = [
        PipelineStep.SELLPOINT_TO_STORYBOARD,
        PipelineStep.STORYBOARD_TO_FRAME,
        PipelineStep.COMPLIANCE_CHECK,
        PipelineStep.FRAME_TO_VIDEO,
        PipelineStep.AUTO_EDIT,
    ]

    def __init__(self, state: Optional[PipelineState] = None):
        self.state = state or PipelineState()

    def run_step(self, step: PipelineStep, input_data: Any = None) -> Any:
        """Execute a single pipeline step. Returns output data."""
        step_state = self.state.steps[step.value]
        step_state.status = StepStatus.IN_PROGRESS
        step_state.input_data = input_data
        step_state.attempts += 1
        self.state.current_step = step.value

        logger.info(f"[Pipeline] Starting step: {step.value} (attempt {step_state.attempts})")

        try:
            result = self._dispatch(step, input_data)
            step_state.output_data = result
            step_state.status = StepStatus.AWAITING_CONFIRM if self.state.mode == "semi_auto" else StepStatus.COMPLETED
            return result
        except Exception as e:
            step_state.status = StepStatus.FAILED
            step_state.error = str(e)
            logger.error(f"[Pipeline] Step {step.value} failed: {e}")
            raise

    def confirm_step(self, step: PipelineStep):
        """User confirms a step result (semi-auto mode)."""
        step_state = self.state.steps[step.value]
        if step_state.status == StepStatus.AWAITING_CONFIRM:
            step_state.status = StepStatus.COMPLETED
            logger.info(f"[Pipeline] Step {step.value} confirmed.")

    def _dispatch(self, step: PipelineStep, input_data: Any) -> Any:
        """Route to the appropriate skill handler."""
        # To be wired up in Step 6
        raise NotImplementedError(f"Step {step.value} not yet implemented.")
