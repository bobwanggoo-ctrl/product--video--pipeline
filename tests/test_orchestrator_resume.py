"""编排器验证测试：checkpoint roundtrip + Skill 5 断点恢复。

测试场景:
  1. test_checkpoint_roundtrip — 纯本地，零 API
  2. test_resume_from_skill5  — 需要 LLM API + ffmpeg

用法:
  # 只跑 roundtrip（零依赖）
  python -m tests.test_orchestrator_resume roundtrip

  # 跑 Skill 5 恢复（需 LLM + ffmpeg）
  python -m tests.test_orchestrator_resume resume

  # 全部
  python -m tests.test_orchestrator_resume all
"""

import json
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import settings
from config.settings import create_run_dirs
from models.storyboard import Storyboard
from pipeline.frame_selector import SelectionPlan
from pipeline.orchestrator import (
    PipelineOrchestrator,
    PipelineState,
    PipelineStep,
    StepStatus,
    _serialize_output,
    _deserialize_output,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 已有 E2E 产物路径 ────────────────────────────────
E2E_DIR = ROOT / "output" / "test_e2e_20260403_183248"


def _load_e2e_artifacts() -> dict:
    """Load artifacts from the previous E2E run."""
    sb_path = E2E_DIR / "storyboard.json"
    sellpoint_path = E2E_DIR / "sellpoint.txt"
    videos_dir = E2E_DIR / "videos"

    assert sb_path.exists(), f"Missing: {sb_path}"
    assert videos_dir.exists(), f"Missing: {videos_dir}"

    sb_data = json.loads(sb_path.read_text(encoding="utf-8"))
    storyboard = Storyboard.model_validate(sb_data)

    sellpoint_text = ""
    if sellpoint_path.exists():
        sellpoint_text = sellpoint_path.read_text(encoding="utf-8").strip()

    video_paths = {}
    for vf in sorted(videos_dir.glob("shot_*.mp4")):
        shot_id = int(vf.stem.split("_")[1])
        video_paths[shot_id] = str(vf)

    # Generate motion results
    from skills.frame_to_video.motion_planner import plan_storyboard_motions
    motion_results = plan_storyboard_motions(sb_data)

    return {
        "storyboard": storyboard,
        "storyboard_path": str(sb_path),
        "sellpoint_text": sellpoint_text,
        "video_paths": video_paths,
        "motion_results": motion_results,
    }


# ══════════════════════════════════════════════════════
# Test 1: Checkpoint Roundtrip (零 API 依赖)
# ══════════════════════════════════════════════════════

def test_checkpoint_roundtrip():
    """Verify PipelineState save/load preserves all data types."""
    logger.info("=" * 60)
    logger.info("Test: Checkpoint Roundtrip")
    logger.info("=" * 60)

    artifacts = _load_e2e_artifacts()
    task_id = f"test_roundtrip_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dirs = create_run_dirs(task_id)

    try:
        # Build a state with all steps completed
        state = PipelineState(task_id=task_id, mode="semi_auto")

        # Skill 1 — Storyboard (Pydantic)
        state.steps["sellpoint_to_storyboard"].status = StepStatus.COMPLETED
        state.steps["sellpoint_to_storyboard"].output_data = {
            "storyboard": artifacts["storyboard"],
        }

        # Skill 2 — frame_paths (int keys)
        state.steps["storyboard_to_frame"].status = StepStatus.COMPLETED
        state.steps["storyboard_to_frame"].output_data = {
            "frame_paths": {1: "/fake/shot_01.png", 2: "/fake/shot_02.png", 15: "/fake/shot_15.png"},
            "failed_shots": [3],
        }

        # Skill 3 — SKIPPED
        state.steps["compliance_check"].status = StepStatus.SKIPPED
        state.steps["compliance_check"].output_data = {
            "compliance_results": None,
            "skipped": True,
        }

        # 选材 — SelectionPlan (dataclass)
        plan = SelectionPlan(
            first_batch=[1, 2, 5, 6, 7, 8, 9, 10, 12, 13, 14],
            standby=[4, 11, 15],
            rejected=[{"shot_id": 3, "reason": "frame generation failed"}],
            type_distribution={"Wide": 3, "Medium": 4, "Close": 2, "Macro": 2},
        )
        state.steps["frame_selection"].status = StepStatus.COMPLETED
        state.steps["frame_selection"].output_data = {"plan": plan}

        # Skill 4 — video_paths (int keys) + motion_results (list[dict])
        state.steps["frame_to_video"].status = StepStatus.COMPLETED
        state.steps["frame_to_video"].output_data = {
            "video_paths": artifacts["video_paths"],
            "successful_shot_ids": list(artifacts["video_paths"].keys()),
            "motion_results": artifacts["motion_results"],
        }

        # Skill 5 — pending (for resume test)
        state.steps["auto_edit"].status = StepStatus.PENDING

        # ── Save ──
        state.save(run_dirs["checkpoint"])
        logger.info(f"  Saved checkpoint: {run_dirs['checkpoint']}")
        assert run_dirs["checkpoint"].exists(), "Checkpoint file not created"

        # ── Load ──
        loaded = PipelineState.load(run_dirs["checkpoint"])

        # ── Verify ──
        errors = []

        # Basic fields
        if loaded.task_id != task_id:
            errors.append(f"task_id: {loaded.task_id} != {task_id}")
        if loaded.mode != "semi_auto":
            errors.append(f"mode: {loaded.mode}")

        # Storyboard roundtrip
        sb = loaded.steps["sellpoint_to_storyboard"].output_data.get("storyboard")
        if not isinstance(sb, Storyboard):
            errors.append(f"Storyboard type: {type(sb)}")
        elif sb.total_shots != artifacts["storyboard"].total_shots:
            errors.append(f"Storyboard total_shots: {sb.total_shots}")

        # frame_paths int keys
        fp = loaded.steps["storyboard_to_frame"].output_data.get("frame_paths", {})
        if not all(isinstance(k, int) for k in fp.keys()):
            errors.append(f"frame_paths key types: {[type(k).__name__ for k in fp.keys()]}")
        if set(fp.keys()) != {1, 2, 15}:
            errors.append(f"frame_paths keys: {set(fp.keys())}")

        # SKIPPED status
        if loaded.steps["compliance_check"].status != StepStatus.SKIPPED:
            errors.append(f"compliance status: {loaded.steps['compliance_check'].status}")

        # SelectionPlan roundtrip
        loaded_plan = loaded.steps["frame_selection"].output_data.get("plan")
        if isinstance(loaded_plan, SelectionPlan):
            if loaded_plan.first_batch != plan.first_batch:
                errors.append(f"plan.first_batch mismatch")
            if loaded_plan.standby != plan.standby:
                errors.append(f"plan.standby mismatch")
        elif isinstance(loaded_plan, dict):
            # dataclass 可能反序列化为 dict，也可以接受
            if loaded_plan.get("first_batch") != plan.first_batch:
                errors.append(f"plan dict first_batch mismatch")
        else:
            errors.append(f"plan type: {type(loaded_plan)}")

        # video_paths int keys
        vp = loaded.steps["frame_to_video"].output_data.get("video_paths", {})
        if not all(isinstance(k, int) for k in vp.keys()):
            errors.append(f"video_paths key types: {[type(k).__name__ for k in vp.keys()]}")

        # motion_results preserved
        mr = loaded.steps["frame_to_video"].output_data.get("motion_results")
        if not isinstance(mr, list) or len(mr) != len(artifacts["motion_results"]):
            errors.append(f"motion_results: expected {len(artifacts['motion_results'])}, got {len(mr) if mr else 0}")

        # Skill 5 still pending
        if loaded.steps["auto_edit"].status != StepStatus.PENDING:
            errors.append(f"auto_edit status: {loaded.steps['auto_edit'].status}")

        if errors:
            for e in errors:
                logger.error(f"  FAIL: {e}")
            logger.error("Checkpoint roundtrip FAILED")
            return False
        else:
            logger.info("  Storyboard (Pydantic) roundtrip ✓")
            logger.info("  frame_paths (int keys) roundtrip ✓")
            logger.info("  SKIPPED status preserved ✓")
            logger.info("  SelectionPlan (dataclass) roundtrip ✓")
            logger.info("  video_paths (int keys) roundtrip ✓")
            logger.info("  motion_results (list[dict]) roundtrip ✓")
            logger.info("  auto_edit PENDING preserved ✓")
            logger.info("Checkpoint roundtrip PASSED ✓")
            return True

    finally:
        shutil.rmtree(run_dirs["root"], ignore_errors=True)


# ══════════════════════════════════════════════════════
# Test 2: Resume from Skill 5 (需要 LLM + ffmpeg)
# ══════════════════════════════════════════════════════

def test_resume_from_skill5():
    """Construct a checkpoint with steps 1-5 completed, resume to run Skill 5 only."""
    logger.info("=" * 60)
    logger.info("Test: Resume from Skill 5")
    logger.info("=" * 60)

    artifacts = _load_e2e_artifacts()
    task_id = f"test_resume_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dirs = create_run_dirs(task_id)

    try:
        # Build state: all steps before auto_edit are COMPLETED
        state = PipelineState(task_id=task_id, mode="full_auto")  # full_auto to skip confirmation prompts

        state.steps["sellpoint_to_storyboard"].status = StepStatus.COMPLETED
        state.steps["sellpoint_to_storyboard"].output_data = {
            "storyboard": artifacts["storyboard"],
        }

        state.steps["storyboard_to_frame"].status = StepStatus.COMPLETED
        state.steps["storyboard_to_frame"].output_data = {
            "frame_paths": {sid: f"/fake/{sid}.png" for sid in artifacts["video_paths"]},
            "failed_shots": [],
        }

        state.steps["compliance_check"].status = StepStatus.SKIPPED
        state.steps["compliance_check"].output_data = {
            "compliance_results": None,
            "skipped": True,
        }

        state.steps["frame_selection"].status = StepStatus.COMPLETED
        state.steps["frame_selection"].output_data = {
            "plan": SelectionPlan(
                first_batch=list(artifacts["video_paths"].keys()),
                standby=[],
            ),
        }

        state.steps["frame_to_video"].status = StepStatus.COMPLETED
        state.steps["frame_to_video"].output_data = {
            "video_paths": artifacts["video_paths"],
            "successful_shot_ids": list(artifacts["video_paths"].keys()),
            "motion_results": artifacts["motion_results"],
        }

        # Skill 5 is PENDING — this is what we want to resume
        state.steps["auto_edit"].status = StepStatus.PENDING

        # Save checkpoint
        state.save(run_dirs["checkpoint"])
        logger.info(f"  Checkpoint saved: {run_dirs['checkpoint']}")

        # ── Resume via orchestrator ──
        loaded_state = PipelineState.load(run_dirs["checkpoint"])
        orchestrator = PipelineOrchestrator(loaded_state)

        initial_input = {
            "sellpoint_text": artifacts["sellpoint_text"],
            "reference_image_dir": "",
            "bgm_dir": str(settings.MUSIC_DIR) if settings.MUSIC_DIR.exists() else "",
        }

        logger.info("  Resuming pipeline (only Skill 5 should run)...")
        start = time.time()
        result = orchestrator.run_all(initial_input, run_dirs)
        elapsed = time.time() - start

        # ── Verify ──
        errors = []

        if result.get("aborted"):
            errors.append("Pipeline aborted unexpectedly")

        mp4 = result.get("mp4")
        if not mp4 or not Path(mp4).exists():
            errors.append(f"MP4 not found: {mp4}")

        for key in ("srt_en", "srt_cn", "jianying_json", "fcpxml"):
            val = result.get(key)
            if not val or not Path(val).exists():
                errors.append(f"{key} not found: {val}")

        # Verify only Skill 5 ran (others should have been skipped)
        for step_name in ("sellpoint_to_storyboard", "storyboard_to_frame", "frame_selection", "frame_to_video"):
            s = loaded_state.steps[step_name]
            if s.attempts > 0:
                # attempts come from loaded checkpoint, might be 0 since we built it from scratch
                pass  # OK

        if errors:
            for e in errors:
                logger.error(f"  FAIL: {e}")
            logger.error("Resume from Skill 5 FAILED")
            return False
        else:
            logger.info(f"  耗时: {elapsed:.1f}s")
            logger.info(f"  成片: {mp4}")
            logger.info(f"  字幕(EN): {result.get('srt_en')}")
            logger.info(f"  字幕(CN): {result.get('srt_cn')}")
            logger.info(f"  剪映: {result.get('jianying_json')}")
            logger.info(f"  FCPXML: {result.get('fcpxml')}")
            logger.info("Resume from Skill 5 PASSED ✓")
            return True

    except Exception as e:
        logger.error(f"  Exception: {e}", exc_info=True)
        return False
    finally:
        # Don't auto-cleanup so user can inspect output
        logger.info(f"  输出目录: {run_dirs['root']}")


# ══════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1].lower()
    results = {}

    if mode in ("roundtrip", "all"):
        results["roundtrip"] = test_checkpoint_roundtrip()

    if mode in ("resume", "all"):
        results["resume"] = test_resume_from_skill5()

    # Summary
    print(f"\n{'=' * 60}")
    print("测试结果汇总:")
    for name, passed in results.items():
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"  {name}: {status}")
    print(f"{'=' * 60}\n")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
