"""Pipeline Orchestrator: controls flow, state, retries, and human checkpoints.

Mode B (semi-auto): pauses after each step for user confirmation.
Mode A (full-auto): runs all steps without pausing (future).
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
    FRAME_SELECTION = "frame_selection"   # 选材（编排器内部逻辑）
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

    流程:
        Skill 1 → Skill 2 → 合规检查 → 选材 → Skill 4 (分批+补拍) → Skill 5
    """

    STEP_ORDER = [
        PipelineStep.SELLPOINT_TO_STORYBOARD,
        PipelineStep.STORYBOARD_TO_FRAME,
        PipelineStep.COMPLIANCE_CHECK,
        PipelineStep.FRAME_SELECTION,
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
            step_state.status = (
                StepStatus.AWAITING_CONFIRM
                if self.state.mode == "semi_auto"
                else StepStatus.COMPLETED
            )
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

        if step == PipelineStep.SELLPOINT_TO_STORYBOARD:
            return self._run_sellpoint_to_storyboard(input_data)

        if step == PipelineStep.STORYBOARD_TO_FRAME:
            return self._run_storyboard_to_frame(input_data)

        if step == PipelineStep.FRAME_SELECTION:
            return self._run_frame_selection(input_data)

        if step == PipelineStep.FRAME_TO_VIDEO:
            return self._run_frame_to_video(input_data)

        if step == PipelineStep.AUTO_EDIT:
            return self._run_auto_edit(input_data)

        # Skill 3 待接入
        raise NotImplementedError(f"Step {step.value} not yet implemented.")

    # ── Skill 1 ──────────────────────────────────────

    def _run_sellpoint_to_storyboard(self, input_data: dict) -> dict:
        """Skill 1: 卖点 → 分镜。"""
        from skills.sellpoint_to_storyboard.converter import convert

        storyboard = convert(
            input_data["sellpoint_text"],
            preferred_llm=input_data.get("preferred_llm"),
            preferred_route=input_data.get("preferred_route"),
            output_path=input_data.get("output_path"),
        )
        return {"storyboard": storyboard}

    # ── Skill 2 ──────────────────────────────────────

    def _run_storyboard_to_frame(self, input_data: dict) -> dict:
        """Skill 2: 分镜 → 画面帧。

        input_data:
            storyboard: Storyboard
            reference_image_dir: str  (产品参考图目录)
            output_dir: str           (帧图保存目录)
            aspect_ratio: str         (可选, 默认 16:9)
        """
        from skills.storyboard_to_frame.generator import generate_frames

        result = generate_frames(
            storyboard=input_data["storyboard"],
            reference_image_dir=input_data.get("reference_image_dir", str(settings.REFERENCE_IMAGES_DIR)),
            output_dir=input_data.get("output_dir", str(settings.FRAMES_DIR)),
            aspect_ratio=input_data.get("aspect_ratio", "16:9"),
        )
        return result

    # ── 选材 ─────────────────────────────────────────

    def _run_frame_selection(self, input_data: dict) -> dict:
        """选材：从合规帧中选出生成视频的优先序列。

        input_data:
            storyboard: Storyboard
            compliance_results: list[ComplianceResult] (可选)

        Returns:
            {"plan": SelectionPlan}
        """
        from pipeline.frame_selector import select_frames

        plan = select_frames(
            storyboard=input_data["storyboard"],
            compliance_results=input_data.get("compliance_results"),
        )
        return {"plan": plan}

    # ── Skill 4（分批 + 补拍）────────────────────────

    def _run_frame_to_video(self, input_data: dict) -> dict:
        """Skill 4: 帧 → 视频，分批生成 + 补拍。

        input_data:
            plan: SelectionPlan
            frame_paths: dict[int, str]  # {shot_id: frame_path}
            storyboard: Storyboard
            ... (Kling API 参数)

        Returns:
            {"video_paths": list[str], "successful_shot_ids": list[int]}
        """
        from pipeline.frame_selector import check_and_backfill, MIN_CLIPS_NEEDED

        plan = input_data["plan"]
        frame_paths = input_data.get("frame_paths", {})

        # 第一批生成
        logger.info(f"[Pipeline] 第一批生成: {len(plan.first_batch)} 个")
        batch1_results = self._generate_videos(plan.first_batch, frame_paths, input_data)

        successful = [r["shot_id"] for r in batch1_results if r["success"]]
        video_paths = {r["shot_id"]: r["video_path"] for r in batch1_results if r["success"]}

        # 补拍检查
        backfill_ids = check_and_backfill(plan, successful)
        if backfill_ids:
            logger.info(f"[Pipeline] 补拍: {len(backfill_ids)} 个")
            batch2_results = self._generate_videos(backfill_ids, frame_paths, input_data)

            for r in batch2_results:
                if r["success"]:
                    successful.append(r["shot_id"])
                    video_paths[r["shot_id"]] = r["video_path"]

        if len(successful) < MIN_CLIPS_NEEDED:
            logger.error(
                f"视频生成成功 {len(successful)} 个，不足最低要求 {MIN_CLIPS_NEEDED}"
            )

        return {
            "video_paths": video_paths,
            "successful_shot_ids": successful,
        }

    def _generate_videos(
        self, shot_ids: list[int], frame_paths: dict, input_data: dict,
    ) -> list[dict]:
        """调用 Skill 4 生成视频（待接 Kling API）。

        Returns: [{"shot_id": int, "success": bool, "video_path": str}]
        """
        # TODO: 接入 Kling API
        # from skills.frame_to_video.generator import generate_video
        results = []
        for sid in shot_ids:
            frame_path = frame_paths.get(sid, "")
            if not frame_path:
                results.append({"shot_id": sid, "success": False, "video_path": ""})
                continue
            # 占位：实际调用 Kling API
            logger.warning(f"Shot {sid}: Kling API 未接入，跳过")
            results.append({"shot_id": sid, "success": False, "video_path": ""})
        return results

    # ── Skill 5 ──────────────────────────────────────

    def _run_auto_edit(self, input_data: dict) -> dict:
        """Skill 5: 自动剪辑。"""
        from skills.auto_editor import run as skill5_run

        result = skill5_run(
            video_paths=input_data["video_paths"],
            storyboard=input_data["storyboard"],
            output_dir=input_data["output_dir"],
            bgm_dir=input_data.get("bgm_dir", ""),
            sellpoint_text=input_data.get("sellpoint_text", ""),
            motion_results=input_data.get("motion_results"),
            preferred_llm=input_data.get("preferred_llm"),
            preferred_route=input_data.get("preferred_route"),
        )
        return result
