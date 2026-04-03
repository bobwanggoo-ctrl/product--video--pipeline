"""E2E 全流程测试：Skill 1 → 2 → (跳过3) → 运镜 → Skill 4 → Skill 5

输入: input/Test_1/
  - bullet point.docx  (卖点文案)
  - image1.jpg, image2.jpg  (产品参考图)

输出: output/test_e2e_{timestamp}/
  - storyboard.json      (Skill 1: 分镜脚本)
  - frames/              (Skill 2: AI 生成画面帧)
  - videos/              (Skill 4: Kling 生成视频)
  - final/               (Skill 5: 成片 + 剪映 JSON + FCPXML)

用法: python -m tests.test_e2e_pipeline
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
from models.storyboard import Storyboard
from skills.sellpoint_to_storyboard.converter import convert as skill1_convert
from skills.storyboard_to_frame.generator import generate_frames as skill2_generate
from skills.frame_to_video.motion_planner import plan_storyboard_motions
from utils.kling_client import KlingClient
from skills.auto_editor import run as skill5_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
TEST_INPUT_DIR = ROOT / "input" / "Test_1"
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT_DIR = ROOT / "output" / f"test_e2e_{TIMESTAMP}"


def read_sellpoint_docx(docx_path: str) -> str:
    """从 docx 中提取卖点文本。"""
    from docx import Document
    doc = Document(docx_path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def main():
    start_time = time.time()

    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frames_dir = OUTPUT_DIR / "frames"
    videos_dir = OUTPUT_DIR / "videos"
    final_dir = OUTPUT_DIR / "final"

    # ═══════════════════════════════════════════════════
    # Step 0: 读取输入
    # ═══════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 0: 读取输入")
    logger.info("=" * 60)

    docx_path = TEST_INPUT_DIR / "bullet point.docx"
    sellpoint_text = read_sellpoint_docx(str(docx_path))
    logger.info(f"  卖点文案: {len(sellpoint_text)} 字符")
    logger.info(f"  前100字: {sellpoint_text[:100]}...")

    # 参考图目录就是 Test_1（里面有 image1.jpg, image2.jpg）
    ref_image_dir = str(TEST_INPUT_DIR)

    # 保存卖点文本
    (OUTPUT_DIR / "sellpoint.txt").write_text(sellpoint_text, encoding="utf-8")

    # ═══════════════════════════════════════════════════
    # Step 1: Skill 1 — 卖点 → 分镜
    # ═══════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 1: Skill 1 — 卖点 → 分镜脚本")
    logger.info("=" * 60)

    storyboard_path = str(OUTPUT_DIR / "storyboard.json")
    storyboard = skill1_convert(
        sellpoint_text,
        output_path=storyboard_path,
    )
    logger.info(f"  分镜完成: {storyboard.total_shots} 个镜头")

    # ═══════════════════════════════════════════════════
    # Step 2: Skill 2 — 分镜 → 画面帧
    # ═══════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 2: Skill 2 — 分镜 → 画面帧 (AI导航)")
    logger.info("=" * 60)

    frame_result = skill2_generate(
        storyboard=storyboard,
        reference_image_dir=ref_image_dir,
        output_dir=str(frames_dir),
        aspect_ratio="16:9",
    )
    frame_paths = frame_result["frame_paths"]
    failed_frames = frame_result["failed_shots"]
    logger.info(f"  生图完成: {len(frame_paths)} 成功, {len(failed_frames)} 失败")

    if not frame_paths:
        logger.error("  没有生成任何画面帧，终止")
        return

    # ═════════════���═════════════════════════════════════
    # Step 3: 跳过合规检查
    # ═══════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 3: 合规检查 — 跳过（规则未定）")
    logger.info("=" * 60)

    # ═══════════════════════════════════════════════════
    # Step 4: 运镜规划
    # ═══════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 4: 运镜规划")
    logger.info("=" * 60)

    storyboard_dict = json.loads(Path(storyboard_path).read_text(encoding="utf-8"))
    motion_results = plan_storyboard_motions(storyboard_dict)
    motion_map = {m["shot_id"]: m["motion_prompt"] for m in motion_results}
    logger.info(f"  运镜规划完成: {len(motion_results)} 个镜头")
    for m in motion_results:
        logger.info(f"    shot_{m['shot_id']:02d} [{m['shot_type']}]: {m['motion_prompt'][:60]}...")

    # ═══════════════════════════════════════════════════
    # Step 5: Skill 4 — 画面帧 → 视频 (Kling)
    # ═══════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 5: Skill 4 — 画面帧 → 视频 (Kling AI)")
    logger.info("=" * 60)

    kling_client = KlingClient()
    videos_dir.mkdir(parents=True, exist_ok=True)

    # 批量提交
    kling_tasks: dict[str, int] = {}  # {task_id: shot_id}
    kling_failed: list[int] = []

    for shot_id, frame_path in sorted(frame_paths.items()):
        motion_prompt = motion_map.get(shot_id, "")
        try:
            result = kling_client.image_to_video(frame_path, prompt=motion_prompt)
            kling_tasks[result["task_id"]] = shot_id
            logger.info(f"  shot_{shot_id:02d} → Kling task {result['task_id']}")
        except Exception as e:
            logger.warning(f"  shot_{shot_id:02d} Kling 提交失败: {e}")
            kling_failed.append(shot_id)

    logger.info(f"  Kling 提交: {len(kling_tasks)} 成功, {len(kling_failed)} 失败")

    # 批量轮询 + 下载
    video_paths: dict[int, str] = {}
    for task_id, shot_id in kling_tasks.items():
        logger.info(f"  等待 shot_{shot_id:02d} ...")
        try:
            task_result = kling_client.wait_for_task(task_id, timeout=600.0)
            if task_result.get("video_url"):
                vp = str(videos_dir / f"shot_{shot_id:02d}.mp4")
                kling_client.download_video(task_result["video_url"], vp)
                video_paths[shot_id] = vp
                logger.info(f"  shot_{shot_id:02d} ✓")
            else:
                logger.warning(f"  shot_{shot_id:02d} 无视频 URL")
        except Exception as e:
            logger.warning(f"  shot_{shot_id:02d} Kling 失败: {e}")

    logger.info(f"  视频生成完成: {len(video_paths)} 个")

    if not video_paths:
        logger.error("  没有生成任何视频，终止")
        return

    # ═══════════════════════════════════════════════════
    # Step 6: Skill 5 — 自动剪辑
    # ═══════════════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 6: Skill 5 — 自动剪辑")
    logger.info("=" * 60)

    # 按 shot_id 排序的视频路径列表
    sorted_video_paths = [video_paths[sid] for sid in sorted(video_paths.keys())]

    final_dir.mkdir(parents=True, exist_ok=True)
    bgm_dir = str(settings.MUSIC_DIR) if settings.MUSIC_DIR.exists() else ""

    skill5_result = skill5_run(
        video_paths=sorted_video_paths,
        storyboard=storyboard,
        output_dir=str(final_dir),
        bgm_dir=bgm_dir,
        sellpoint_text=sellpoint_text,
        motion_results=motion_results,
    )

    # ═══════════════════════════════════════════════════
    # 汇总
    # ═══════════════════════════════════════════════════
    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("E2E 全流程完成!")
    logger.info("=" * 60)
    logger.info(f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    logger.info(f"  输出目录: {OUTPUT_DIR}")
    logger.info(f"  分镜: {storyboard_path}")
    logger.info(f"  画面帧: {len(frame_paths)} 张 → {frames_dir}")
    logger.info(f"  视频: {len(video_paths)} 个 → {videos_dir}")
    logger.info(f"  成片: {skill5_result.get('mp4', 'N/A')}")
    logger.info(f"  剪映: {skill5_result.get('jianying_json', 'N/A')}")
    logger.info(f"  FCPXML: {skill5_result.get('fcpxml', 'N/A')}")


if __name__ == "__main__":
    main()
