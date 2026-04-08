"""E2E 全流程联调（带全链路 Trace）

Skill 1 → 2 → 3 → 选材 → 4 → 5 完整链路。
每个环节的 prompt、LLM 回复、图片、视频全部记录到 trace/ 目录。
跑完后生成 trace_report.md 可读汇总。

输入: 用户指定的 input 目录（含 docx/txt 卖点文案 + 产品参考图）
输出: output/e2e_traced_{timestamp}/

用法: python -m tests.test_e2e_traced [input_dir]
  默认 input_dir = input/Test_1
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import settings
from config.settings import create_run_dirs
from models.storyboard import Storyboard
from pipeline.frame_selector import select_frames
from skills.sellpoint_to_storyboard.converter import convert as skill1_convert
from skills.storyboard_to_frame.generator import generate_frames as skill2_generate
from skills.compliance_checker import run as skill3_run
from skills.frame_to_video.motion_planner import plan_storyboard_motions
from skills.auto_editor import run as skill5_run
from utils.kling_client import KlingClient
from utils.trace_logger import TraceLogger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def read_sellpoint(input_dir: Path) -> str:
    """从 docx 或 txt 读取卖点文案。"""
    # docx 优先
    for docx in input_dir.glob("*.docx"):
        from docx import Document
        doc = Document(str(docx))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if text:
            logger.info(f"  读取卖点文案: {docx.name} ({len(text)} 字符)")
            return text
    # txt 备选
    for txt in input_dir.glob("*.txt"):
        text = txt.read_text(encoding="utf-8").strip()
        if text:
            logger.info(f"  读取卖点文案: {txt.name} ({len(text)} 字符)")
            return text
    # 无扩展名文件兜底（如"卖点"文件）
    for f in input_dir.iterdir():
        if f.is_file() and not f.suffix and not f.name.startswith("."):
            try:
                text = f.read_text(encoding="utf-8").strip()
                if text and len(text) > 20:
                    logger.info(f"  读取卖点文案: {f.name} ({len(text)} 字符)")
                    return text
            except Exception:
                continue
    return ""


def main():
    # ── 解析输入 ──
    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "input" / "Test_1"
    if not input_dir.exists():
        print(f"输入目录不存在: {input_dir}")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    task_id = f"e2e_traced_{timestamp}"
    run_dirs = create_run_dirs(task_id)
    trace = TraceLogger(str(run_dirs["trace"]))

    total_start = time.time()

    print(f"\n{'='*60}")
    print(f"E2E 全流程联调 (Traced)")
    print(f"输入: {input_dir}")
    print(f"输出: {run_dirs['root']}")
    print(f"{'='*60}\n")

    # ═══════════════════════════════════════════════════════
    # Step 0: 读取输入
    # ═══════════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 0: 读取输入")
    logger.info("=" * 60)

    sellpoint_text = read_sellpoint(input_dir)
    if not sellpoint_text:
        logger.error(f"未找到卖点文案: {input_dir}")
        return

    ref_image_dir = str(input_dir)
    img_exts = {".jpg", ".jpeg", ".png", ".webp"}
    ref_images = [f for f in input_dir.iterdir() if f.suffix.lower() in img_exts]
    logger.info(f"  卖点文案: {len(sellpoint_text)} 字符")
    logger.info(f"  参考图: {len(ref_images)} 张")

    # 保存输入到 trace
    trace.save_text("input", "sellpoint.txt", sellpoint_text)
    trace.save_json("input", "meta.json", {
        "input_dir": str(input_dir),
        "sellpoint_chars": len(sellpoint_text),
        "reference_images": [f.name for f in ref_images],
    })

    # 保存到 run 目录
    run_dirs["sellpoint"].write_text(sellpoint_text, encoding="utf-8")

    # ═══════════════════════════════════════════════════════
    # Step 1: Skill 1 — 卖点 → 分镜
    # ═══════════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 1: Skill 1 — 卖点 → 分镜脚本")
    logger.info("=" * 60)

    trace.start_timer("step1_storyboard")

    storyboard = skill1_convert(
        sellpoint_text,
        output_path=str(run_dirs["storyboard"]),
    )

    elapsed = trace.stop_timer("step1_storyboard")

    # 保存 trace
    if hasattr(storyboard, "_trace"):
        trace.save_step_trace("step1_storyboard", storyboard._trace)
    trace.save_json("step1_storyboard", "storyboard.json", storyboard.model_dump())
    trace.set_meta("step1_storyboard", {
        "耗时": f"{elapsed:.1f}s",
        "镜头数": storyboard.total_shots,
        "场景组数": len(storyboard.scene_groups),
        "产品类型": storyboard.product_type,
    })

    logger.info(f"  分镜完成: {storyboard.total_shots} 个镜头, {elapsed:.1f}s")

    # ═══════════════════════════════════════════════════════
    # Step 2: Skill 2 — 分镜 → 画面帧
    # ═══════════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 2: Skill 2 — 分镜 → 画面帧 (AI导航)")
    logger.info("=" * 60)

    trace.start_timer("step2_frames")

    frame_result = skill2_generate(
        storyboard=storyboard,
        reference_image_dir=ref_image_dir,
        output_dir=str(run_dirs["frames"]),
        aspect_ratio="16:9",
    )
    frame_paths = frame_result["frame_paths"]
    failed_frames = frame_result["failed_shots"]

    elapsed = trace.stop_timer("step2_frames")

    # 保存 trace
    if "_trace" in frame_result:
        trace.save_step_trace("step2_frames", frame_result["_trace"])
    trace.set_meta("step2_frames", {
        "耗时": f"{elapsed:.1f}s",
        "成功": len(frame_paths),
        "失败": len(failed_frames),
        "参考图": len(ref_images),
        "frame_paths": {k: str(v) for k, v in frame_paths.items()},
    })

    logger.info(f"  生图完成: {len(frame_paths)} 成功, {len(failed_frames)} 失败, {elapsed:.1f}s")

    if not frame_paths:
        logger.error("  没有生成任何画面帧，终止")
        trace.generate_report(task_id)
        return

    # ═══════════════════════════════════════════════════════
    # Step 3: Skill 3 — 合规检查
    # ═══════════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 3: Skill 3 — 合规检查 (Gemini Vision)")
    logger.info("=" * 60)

    trace.start_timer("step3_compliance")

    compliance_result = skill3_run(
        storyboard=storyboard,
        frame_paths=frame_paths,
        reference_image_dir=ref_image_dir,
    )
    compliance_results = compliance_result.get("compliance_results", [])
    layout_hints = compliance_result.get("layout_hints", {})
    error_keywords = compliance_result.get("error_keywords", {})

    elapsed = trace.stop_timer("step3_compliance")

    # 保存 trace
    if "_trace" in compliance_result:
        trace.save_step_trace("step3_compliance", compliance_result["_trace"])

    from models.compliance import ComplianceLevel
    pass_n = sum(1 for cr in compliance_results if cr.level == ComplianceLevel.PASS)
    warn_n = sum(1 for cr in compliance_results if cr.level == ComplianceLevel.WARN)
    fail_n = sum(1 for cr in compliance_results if cr.level == ComplianceLevel.FAIL)

    trace.set_meta("step3_compliance", {
        "耗时": f"{elapsed:.1f}s",
        "PASS": pass_n,
        "WARN": warn_n,
        "FAIL": fail_n,
        "error_keywords": {str(k): v for k, v in error_keywords.items()},
    })

    logger.info(f"  合规检查完成: PASS={pass_n} WARN={warn_n} FAIL={fail_n}, {elapsed:.1f}s")

    # ═══════════════════════════════════════════════════════
    # Step 4: 选材
    # ═══════════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 4: 选材")
    logger.info("=" * 60)

    trace.start_timer("step4_selection")

    plan = select_frames(storyboard, compliance_results)

    elapsed = trace.stop_timer("step4_selection")

    trace.save_json("step4_selection", "meta.json", {
        "elapsed_sec": elapsed,
        "first_batch": plan.first_batch,
        "standby": plan.standby,
        "rejected": plan.rejected,
        "type_distribution": plan.type_distribution,
    })
    trace.set_meta("step4_selection", {
        "耗时": f"{elapsed:.1f}s",
        "第一批": f"{len(plan.first_batch)} 个 {plan.first_batch}",
        "备选": f"{len(plan.standby)} 个 {plan.standby}",
        "淘汰": f"{len(plan.rejected)} 个",
    })

    logger.info(f"  选材完成: 第一批 {len(plan.first_batch)}, 备选 {len(plan.standby)}, 淘汰 {len(plan.rejected)}")

    # ═══════════════════════════════════════════════════════
    # Step 5: Skill 4 — 画面帧 → 视频 (Kling)
    # ═══════════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 5: Skill 4 — 画面帧 → 视频 (Kling AI)")
    logger.info("=" * 60)

    trace.start_timer("step5_videos")

    # 运镜规划
    storyboard_dict = storyboard.model_dump()
    motion_results = plan_storyboard_motions(storyboard_dict)
    motion_map = {m["shot_id"]: m["motion_prompt"] for m in motion_results}

    # 保存 motion prompts
    for m in motion_results:
        trace.save_text("step5_videos", f"prompts/shot_{m['shot_id']:02d}.txt",
                        f"shot_type: {m.get('shot_type', '')}\nmotion: {m['motion_prompt']}")

    # Kling 生成
    kling_client = KlingClient()
    videos_dir = run_dirs["videos"]
    videos_dir.mkdir(parents=True, exist_ok=True)

    # 只生成选中的 shot
    selected_shots = plan.first_batch
    kling_tasks: dict[str, int] = {}
    kling_failed: list[int] = []

    for shot_id in selected_shots:
        if shot_id not in frame_paths:
            logger.warning(f"  shot_{shot_id:02d} 无画面帧，跳过")
            continue
        motion_prompt = motion_map.get(shot_id, "")
        try:
            result = kling_client.image_to_video(frame_paths[shot_id], prompt=motion_prompt)
            kling_tasks[result["task_id"]] = shot_id
            logger.info(f"  shot_{shot_id:02d} → Kling task {result['task_id']}")
        except Exception as e:
            logger.warning(f"  shot_{shot_id:02d} Kling 提交失败: {e}")
            kling_failed.append(shot_id)

    logger.info(f"  Kling 提交: {len(kling_tasks)} 成功, {len(kling_failed)} 失败")

    # 批量轮询 + 下载
    video_paths: dict[int, str] = {}
    per_shot_video_trace: dict[int, dict] = {}

    for task_id_str, shot_id in kling_tasks.items():
        logger.info(f"  等待 shot_{shot_id:02d} ...")
        shot_trace = {
            "task_id": task_id_str,
            "motion_prompt": motion_map.get(shot_id, ""),
        }
        try:
            task_result = kling_client.wait_for_task(task_id_str, timeout=600.0)
            if task_result.get("video_url"):
                vp = str(videos_dir / f"shot_{shot_id:02d}.mp4")
                kling_client.download_video(task_result["video_url"], vp)
                video_paths[shot_id] = vp
                shot_trace["success"] = True
                shot_trace["video_path"] = vp
                logger.info(f"  shot_{shot_id:02d} ✓")
            else:
                shot_trace["success"] = False
                shot_trace["error"] = "无视频 URL"
                logger.warning(f"  shot_{shot_id:02d} 无视频 URL")
        except Exception as e:
            shot_trace["success"] = False
            shot_trace["error"] = str(e)
            logger.warning(f"  shot_{shot_id:02d} Kling 失败: {e}")

        per_shot_video_trace[shot_id] = shot_trace

    elapsed = trace.stop_timer("step5_videos")

    # 保存 trace
    trace.save_step_trace("step5_videos", {"per_shot": per_shot_video_trace})
    trace.set_meta("step5_videos", {
        "耗时": f"{elapsed:.1f}s",
        "提交": len(kling_tasks),
        "成功": len(video_paths),
        "失败": len(kling_failed) + len(kling_tasks) - len(video_paths),
    })

    logger.info(f"  视频生成完成: {len(video_paths)} 个, {elapsed:.1f}s")

    if not video_paths:
        logger.error("  没有生成任何视频，终止")
        trace.generate_report(task_id)
        return

    # ═══════════════════════════════════════════════════════
    # Step 6: Skill 5 — 自动剪辑
    # ═══════════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 6: Skill 5 — 自动剪辑")
    logger.info("=" * 60)

    trace.start_timer("step6_edit")

    sorted_video_paths = [video_paths[sid] for sid in sorted(video_paths.keys())]
    bgm_dir = str(settings.MUSIC_DIR) if settings.MUSIC_DIR.exists() else ""
    font_dir = str(settings.FONTS_DIR) if settings.FONTS_DIR.exists() else ""

    skill5_result = skill5_run(
        video_paths=sorted_video_paths,
        storyboard=storyboard,
        output_dir=str(run_dirs["final"]),
        bgm_dir=bgm_dir,
        font_dir=font_dir,
        sellpoint_text=sellpoint_text,
        motion_results=motion_results,
        layout_hints=layout_hints,
    )

    elapsed = trace.stop_timer("step6_edit")

    # 保存 trace
    if "_trace" in skill5_result:
        trace.save_step_trace("step6_edit", skill5_result["_trace"])
    trace.set_meta("step6_edit", {
        "耗时": f"{elapsed:.1f}s",
        "成片": skill5_result.get("mp4", "N/A"),
        "FCPXML": skill5_result.get("fcpxml", "N/A"),
        "剪映JSON": skill5_result.get("jianying_json", "N/A"),
    })

    logger.info(f"  剪辑完成: {elapsed:.1f}s")

    # ═══════════════════════════════════════════════════════
    # 生成 Trace Report
    # ═══════════════════════════════════════════════════════
    report_path = trace.generate_report(task_id)

    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"E2E 全流程完成!")
    print(f"{'='*60}")
    print(f"  总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
    print(f"  成片: {skill5_result.get('mp4', 'N/A')}")
    print(f"  FCPXML: {skill5_result.get('fcpxml', 'N/A')}")
    print(f"  Trace: {report_path}")
    print(f"  输出目录: {run_dirs['root']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
