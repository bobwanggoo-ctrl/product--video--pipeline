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
        """Save state to JSON for checkpoint/resume.

        output_data is serialized as file-path references only — no Pydantic objects.
        """
        data = {
            "task_id": self.task_id,
            "mode": self.mode,
            "current_step": self.current_step,
            "steps": {
                name: {
                    "status": s.status.value,
                    "attempts": s.attempts,
                    "error": s.error,
                    "output_data": _serialize_output(name, s.output_data),
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
                state.steps[name].output_data = _deserialize_output(
                    name, step_data.get("output_data"),
                )
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
            # Don't override SKIPPED status (set by _run_compliance_check etc.)
            if step_state.status != StepStatus.SKIPPED:
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

        if step == PipelineStep.COMPLIANCE_CHECK:
            return self._run_compliance_check(input_data)

        if step == PipelineStep.FRAME_SELECTION:
            return self._run_frame_selection(input_data)

        if step == PipelineStep.FRAME_TO_VIDEO:
            return self._run_frame_to_video(input_data)

        if step == PipelineStep.AUTO_EDIT:
            return self._run_auto_edit(input_data)

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
        result = {"storyboard": storyboard}
        # 透传 trace 数据
        if hasattr(storyboard, "_trace"):
            result["_trace"] = storyboard._trace
        return result

    # ── Skill 2 ──────────────────────────────────────

    def _run_storyboard_to_frame(self, input_data: dict) -> dict:
        """Skill 2: 分镜 → 画面帧。"""
        from skills.storyboard_to_frame.generator import generate_frames

        result = generate_frames(
            storyboard=input_data["storyboard"],
            reference_image_dir=input_data.get("reference_image_dir", str(settings.REFERENCE_IMAGES_DIR)),
            output_dir=input_data.get("output_dir", str(settings.FRAMES_DIR)),
            aspect_ratio=input_data.get("aspect_ratio", "16:9"),
            error_keywords=input_data.get("error_keywords"),
        )
        return result

    # ── Skill 3 ────────────────────────────────────────

    def _run_compliance_check(self, input_data: dict) -> dict:
        """Skill 3: 合规检查。"""
        from skills.compliance_checker import run as compliance_run

        return compliance_run(
            storyboard=input_data["storyboard"],
            frame_paths=input_data.get("frame_paths", {}),
            reference_image_dir=input_data.get("reference_image_dir", ""),
        )

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
        """Skill 4: 帧 → 视频，含运镜规划 + 分批生成 + 补拍。"""
        from pipeline.frame_selector import check_and_backfill, MIN_CLIPS_NEEDED
        from skills.frame_to_video.motion_planner import plan_storyboard_motions

        plan = input_data["plan"]
        frame_paths = input_data.get("frame_paths", {})
        storyboard = input_data.get("storyboard")

        # 运镜规划（纯规则，不调 API）
        storyboard_dict = storyboard.model_dump() if hasattr(storyboard, "model_dump") else storyboard
        motion_results = plan_storyboard_motions(storyboard_dict)
        motion_map = {m["shot_id"]: m["motion_prompt"] for m in motion_results}
        logger.info(f"[Pipeline] 运镜规划完成: {len(motion_results)} 个镜头")

        # 注入 motion_map 到 input_data 供 _generate_videos 使用
        input_data["motion_map"] = motion_map

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
            "motion_results": motion_results,
        }

    def _generate_videos(
        self, shot_ids: list[int], frame_paths: dict, input_data: dict,
    ) -> list[dict]:
        """调用 Kling API 批量生成视频。

        先批量提交所有任务，再逐个轮询等待 + 下载。

        Returns: [{"shot_id": int, "success": bool, "video_path": str}]
        """
        from utils.kling_client import KlingClient

        client = KlingClient()
        motion_map = input_data.get("motion_map", {})  # {shot_id: motion_prompt}
        output_dir = Path(input_data.get("video_output_dir", str(settings.VIDEOS_DIR)))
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. 批量提交
        task_map: dict[str, int] = {}  # {task_id: shot_id}
        submit_failed: list[dict] = []

        for sid in shot_ids:
            frame_path = frame_paths.get(sid, "")
            if not frame_path:
                submit_failed.append({"shot_id": sid, "success": False, "video_path": ""})
                continue
            try:
                motion_prompt = motion_map.get(sid, "")
                result = client.image_to_video(frame_path, prompt=motion_prompt)
                task_map[result["task_id"]] = sid
                logger.info(f"  shot_{sid:02d} → Kling task {result['task_id']}")
            except Exception as e:
                logger.warning(f"  shot_{sid:02d} Kling 提交失败: {e}")
                submit_failed.append({"shot_id": sid, "success": False, "video_path": ""})

        logger.info(f"[Pipeline] Kling 提交: {len(task_map)} 成功, {len(submit_failed)} 失败")

        # 2. 逐个轮询 + 下载
        results = list(submit_failed)
        for task_id, sid in task_map.items():
            try:
                task_result = client.wait_for_task(task_id, timeout=600.0)
                video_url = task_result.get("video_url")
                if not video_url:
                    logger.warning(f"  shot_{sid:02d} 无视频 URL")
                    results.append({"shot_id": sid, "success": False, "video_path": ""})
                    continue

                video_path = str(output_dir / f"shot_{sid:02d}.mp4")
                client.download_video(video_url, video_path)
                results.append({"shot_id": sid, "success": True, "video_path": video_path})
                logger.info(f"  shot_{sid:02d} ✓ → {video_path}")
            except Exception as e:
                logger.warning(f"  shot_{sid:02d} Kling 失败: {e}")
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
            font_dir=input_data.get("font_dir", ""),
            title_templates_dir=input_data.get("title_templates_dir", ""),
            sellpoint_text=input_data.get("sellpoint_text", ""),
            motion_results=input_data.get("motion_results"),
            layout_hints=input_data.get("layout_hints"),
            preferred_llm=input_data.get("preferred_llm"),
            preferred_route=input_data.get("preferred_route"),
        )
        return result

    # ── run_all: 全流程串联 ─────────────────────────────

    def run_all(self, initial_input: dict, run_dirs: dict, on_progress=None, should_stop=None) -> dict:
        """Run the full pipeline, chaining data between steps.

        Args:
            initial_input: {sellpoint_text, reference_image_dir, bgm_dir}
            run_dirs: from config.settings.create_run_dirs()
            on_progress: optional callable(step_name, status, detail)
            should_stop: optional callable() -> bool  — checked between steps
        """
        self._initial_input = initial_input
        self._run_dirs = run_dirs
        self._on_progress = on_progress

        for step in self.STEP_ORDER:
            # Check stop signal between steps
            if should_stop and should_stop():
                logger.info("[Pipeline] 收到停止信号，保存进度并退出")
                self.state.save(run_dirs["checkpoint"])
                return {"aborted": True, "output_dir": str(run_dirs["root"])}

            step_state = self.state.steps[step.value]

            # Skip completed / skipped steps (checkpoint resume)
            if step_state.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                logger.info(f"[Pipeline] 跳过已完成步骤: {step.value}")
                if on_progress:
                    on_progress(step.value, "skipped", "")
                continue

            input_data = self._build_step_input(step)

            if on_progress:
                on_progress(step.value, "started", "")

            try:
                result = self.run_step(step, input_data)
            except Exception as e:
                logger.error(f"[Pipeline] {step.value} 失败: {e}")
                if on_progress:
                    on_progress(step.value, "failed", str(e))
                self.state.save(run_dirs["checkpoint"])
                if self.state.mode == "semi_auto":
                    action = self._handle_failure(step, e)
                    if action == "retry":
                        step_state.status = StepStatus.PENDING
                        self.state.save(run_dirs["checkpoint"])
                        return self.run_all(initial_input, run_dirs, on_progress, should_stop)
                    elif action == "skip" and step == PipelineStep.COMPLIANCE_CHECK:
                        step_state.status = StepStatus.SKIPPED
                        continue
                raise

            # Semi-auto: show result and wait for confirmation
            if step_state.status == StepStatus.AWAITING_CONFIRM:
                self._show_step_result(step, result)
                action = self._wait_for_confirmation(step)
                if action == "retry":
                    step_state.status = StepStatus.PENDING
                    step_state.output_data = None
                    self.state.save(run_dirs["checkpoint"])
                    return self.run_all(initial_input, run_dirs, on_progress, should_stop)
                elif action == "quit":
                    self.state.save(run_dirs["checkpoint"])
                    print(f"\n进度已保存: {run_dirs['checkpoint']}")
                    return {"aborted": True}
                else:
                    self.confirm_step(step)

            if on_progress:
                on_progress(step.value, "completed", "")

            # Save compliance report as named JSON after compliance step
            if step == PipelineStep.COMPLIANCE_CHECK:
                self._save_compliance_report(run_dirs, initial_input)

            # Save checkpoint after each step
            self.state.save(run_dirs["checkpoint"])

        return self._collect_final_output()

    def _build_step_input(self, step: PipelineStep) -> dict:
        """Assemble input_data for a step from previous outputs + initial_input."""
        ini = self._initial_input
        dirs = self._run_dirs
        out = lambda s: self.state.steps[s].output_data or {}

        if step == PipelineStep.SELLPOINT_TO_STORYBOARD:
            return {
                "sellpoint_text": ini["sellpoint_text"],
                "output_path": str(dirs["storyboard"]),
            }

        if step == PipelineStep.STORYBOARD_TO_FRAME:
            return {
                "storyboard": out("sellpoint_to_storyboard").get("storyboard"),
                "reference_image_dir": ini.get("reference_image_dir", ""),
                "output_dir": str(dirs["frames"]),
            }

        if step == PipelineStep.COMPLIANCE_CHECK:
            return {
                "storyboard": out("sellpoint_to_storyboard").get("storyboard"),
                "frame_paths": out("storyboard_to_frame").get("frame_paths", {}),
                "reference_image_dir": ini.get("reference_image_dir", ""),
            }

        if step == PipelineStep.FRAME_SELECTION:
            return {
                "storyboard": out("sellpoint_to_storyboard").get("storyboard"),
                "compliance_results": out("compliance_check").get("compliance_results"),
            }

        if step == PipelineStep.FRAME_TO_VIDEO:
            return {
                "plan": out("frame_selection").get("plan"),
                "frame_paths": out("storyboard_to_frame").get("frame_paths", {}),
                "storyboard": out("sellpoint_to_storyboard").get("storyboard"),
                "video_output_dir": str(dirs["videos"]),
            }

        if step == PipelineStep.AUTO_EDIT:
            video_paths_dict = out("frame_to_video").get("video_paths", {})
            sorted_paths = [video_paths_dict[sid] for sid in sorted(video_paths_dict.keys())]
            return {
                "video_paths": sorted_paths,
                "storyboard": out("sellpoint_to_storyboard").get("storyboard"),
                "output_dir": str(dirs["final"]),
                "task_name": ini.get("task_name", ""),
                "bgm_dir": ini.get("bgm_dir", ""),
                "font_dir": ini.get("font_dir", ""),
                "title_templates_dir": ini.get("title_templates_dir", ""),
                "sellpoint_text": ini.get("sellpoint_text", ""),
                "motion_results": out("frame_to_video").get("motion_results"),
                "layout_hints": out("compliance_check").get("layout_hints"),
            }

        return {}

    def _save_compliance_report(self, run_dirs: dict, initial_input: dict) -> None:
        """Save compliance results as {task_name}-合规审查.json in the task root."""
        import json as _json
        task_name = initial_input.get("task_name", "")
        if not task_name:
            return
        compliance_data = self.state.steps["compliance_check"].output_data or {}
        cr_list = compliance_data.get("compliance_results") or []
        if not cr_list:
            return
        try:
            serializable = [
                cr.model_dump() if hasattr(cr, "model_dump") else cr
                for cr in cr_list
            ]
            other_dir = run_dirs.get("other", run_dirs["root"])
            out_path = other_dir / f"{task_name}-合规审查.json"
            out_path.write_text(
                _json.dumps(serializable, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"[Pipeline] 合规审查报告 → {out_path}")
        except Exception as e:
            logger.warning(f"[Pipeline] 合规报告保存失败: {e}")

    def _collect_final_output(self) -> dict:
        """Gather final results from all steps."""
        skill5_out = self.state.steps["auto_edit"].output_data or {}
        return {
            "mp4": skill5_out.get("mp4"),
            "srt_en": skill5_out.get("srt_en"),
            "srt_cn": skill5_out.get("srt_cn"),
            "jianying_json": skill5_out.get("jianying_json"),
            "fcpxml": skill5_out.get("fcpxml"),
        }

    # ── 半自动交互 ──────────────────────────────────────

    STEP_NAMES = {
        "sellpoint_to_storyboard": "卖点 → 分镜",
        "storyboard_to_frame": "分镜 → 画面帧",
        "compliance_check": "合规检查",
        "frame_selection": "选材",
        "frame_to_video": "画面帧 → 视频",
        "auto_edit": "自动剪辑",
    }

    def _show_step_result(self, step: PipelineStep, result: dict):
        """Display step result summary to user."""
        idx = self.STEP_ORDER.index(step) + 1
        total = len(self.STEP_ORDER)
        name = self.STEP_NAMES.get(step.value, step.value)

        print(f"\n{'=' * 50}")
        print(f"[Step {idx}/{total}] {name} 完成")

        if step == PipelineStep.SELLPOINT_TO_STORYBOARD:
            sb = result.get("storyboard")
            if sb:
                print(f"  镜头数: {sb.total_shots} | 场景组: {len(sb.scene_groups)}")
                print(f"  产品类型: {sb.product_type}")
                print(f"  输出: {self._run_dirs['storyboard']}")

        elif step == PipelineStep.STORYBOARD_TO_FRAME:
            fp = result.get("frame_paths", {})
            fail = result.get("failed_shots", [])
            print(f"  成功: {len(fp)} | 失败: {len(fail)}")
            print(f"  输出: {self._run_dirs['frames']}")

        elif step == PipelineStep.COMPLIANCE_CHECK:
            cr_list = result.get("compliance_results") or []
            if cr_list:
                from models.compliance import ComplianceLevel
                pass_n = sum(1 for cr in cr_list if cr.level == ComplianceLevel.PASS)
                warn_n = sum(1 for cr in cr_list if cr.level == ComplianceLevel.WARN)
                fail_n = sum(1 for cr in cr_list if cr.level == ComplianceLevel.FAIL)
                print(f"  PASS: {pass_n} | WARN: {warn_n} | FAIL: {fail_n}")
                for cr in cr_list:
                    if cr.level != ComplianceLevel.PASS:
                        kw = ", ".join(cr.error_keywords) if cr.error_keywords else ""
                        kw_str = f" → keywords: [{kw}]" if kw else ""
                        print(f"    shot_{cr.shot_id:02d} [{cr.level.value}] {cr.summary}{kw_str}")
            elif result.get("skipped"):
                print(f"  已跳过")

        elif step == PipelineStep.FRAME_SELECTION:
            plan = result.get("plan")
            if plan:
                print(f"  第一批: {len(plan.first_batch)} | 备选: {len(plan.standby)}")

        elif step == PipelineStep.FRAME_TO_VIDEO:
            vp = result.get("video_paths", {})
            sid = result.get("successful_shot_ids", [])
            print(f"  成功: {len(sid)} 个视频")
            print(f"  输出: {self._run_dirs['videos']}")

        elif step == PipelineStep.AUTO_EDIT:
            print(f"  成片: {result.get('mp4', 'N/A')}")
            print(f"  剪映: {result.get('jianying_json', 'N/A')}")
            print(f"  FCPXML: {result.get('fcpxml', 'N/A')}")

        print(f"{'=' * 50}")

    def _wait_for_confirmation(self, step: PipelineStep) -> str:
        """Wait for user input in semi-auto mode.

        Returns: 'confirm', 'retry', or 'quit'
        """
        while True:
            choice = input("\n  [c] 确认继续  [r] 重试  [q] 退出保存\n  > ").strip().lower()
            if choice in ("c", ""):
                return "confirm"
            elif choice == "r":
                return "retry"
            elif choice == "q":
                return "quit"
            else:
                print("  请输入 c / r / q")

    def _handle_failure(self, step: PipelineStep, error: Exception) -> str:
        """Handle step failure in semi-auto mode."""
        name = self.STEP_NAMES.get(step.value, step.value)
        print(f"\n[!] {name} 失败: {error}")
        while True:
            choice = input("  [r] 重试  [q] 退出保存\n  > ").strip().lower()
            if choice == "r":
                return "retry"
            elif choice == "q":
                return "quit"
            else:
                print("  请输入 r / q")


# ── Checkpoint 序列化辅助 ──────────────────────────────

def _serialize_output(step_name: str, output_data: Any) -> Any:
    """Serialize step output_data for JSON checkpoint.

    Converts Pydantic models to dicts, keeps path strings and plain dicts as-is.
    """
    if output_data is None:
        return None

    if not isinstance(output_data, dict):
        return output_data

    result = {}
    for k, v in output_data.items():
        # 跳过 trace 数据（不存入 checkpoint）
        if k.startswith("_"):
            continue
        result[k] = _serialize_value(v)
    return result


def _serialize_value(v: Any) -> Any:
    """递归序列化单个值：Pydantic → dict, list → 递归, dict → 递归。"""
    if v is None:
        return None
    if hasattr(v, "model_dump"):
        return {"__pydantic__": True, "__type__": type(v).__name__, "data": v.model_dump()}
    if hasattr(v, "__dataclass_fields__"):
        import dataclasses
        return {"__dataclass__": True, "__type__": type(v).__name__, "data": dataclasses.asdict(v)}
    if isinstance(v, list):
        return [_serialize_value(item) for item in v]
    if isinstance(v, dict):
        return {str(kk): _serialize_value(vv) for kk, vv in v.items()}
    if isinstance(v, (str, int, float, bool)):
        return v
    # Enum 等
    if hasattr(v, "value"):
        return v.value
    return str(v)


def _deserialize_output(step_name: str, output_data: Any) -> Any:
    """Deserialize step output_data from JSON checkpoint."""
    if output_data is None:
        return None

    if not isinstance(output_data, dict):
        return output_data

    result = {}
    for k, v in output_data.items():
        result[k] = _deserialize_value(k, v)
    return result


# Pydantic 类型注册表（用于反序列化）
_PYDANTIC_REGISTRY = {
    "Storyboard": lambda d: __import__("models.storyboard", fromlist=["Storyboard"]).Storyboard.model_validate(d),
    "ComplianceResult": lambda d: __import__("models.compliance", fromlist=["ComplianceResult"]).ComplianceResult.model_validate(d),
    "LayoutHint": lambda d: __import__("models.compliance", fromlist=["LayoutHint"]).LayoutHint.model_validate(d),
}

_DATACLASS_REGISTRY = {
    "SelectionPlan": lambda d: __import__("pipeline.frame_selector", fromlist=["SelectionPlan"]).SelectionPlan(**d),
}


def _deserialize_value(key: str, v: Any) -> Any:
    """递归反序列化单个值。"""
    if isinstance(v, dict) and v.get("__pydantic__"):
        type_name = v["__type__"]
        data = v["data"]
        factory = _PYDANTIC_REGISTRY.get(type_name)
        return factory(data) if factory else data
    if isinstance(v, dict) and v.get("__dataclass__"):
        type_name = v["__type__"]
        data = v["data"]
        factory = _DATACLASS_REGISTRY.get(type_name)
        return factory(data) if factory else data
    if isinstance(v, list):
        return [_deserialize_value(key, item) for item in v]
    if isinstance(v, dict) and key in ("frame_paths", "video_paths", "layout_hints"):
        # Restore int keys
        return {int(kk): _deserialize_value(key, vv) for kk, vv in v.items()}
    return v
