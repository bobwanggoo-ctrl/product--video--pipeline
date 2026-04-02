"""全流程 E2E 测试：Skill 1 → (跳过 2/3/4) → Skill 5。

用真实 LLM 跑 Skill 1（sellpoint → storyboard）和 Skill 5（剪辑决策 + 组装），
中间 Skill 2/3/4 用 Auto_editor_test 的现有视频替代。
"""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skills.sellpoint_to_storyboard.converter import convert as skill1_convert
from skills.auto_editor import run as skill5_run
from utils.ffmpeg_wrapper import get_video_info, run_ffprobe_json

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def main():
    # ── 输入 ──────────────────────────────────────────────
    sellpoint_path = ROOT / "input" / "sellpoint"
    video_dir = ROOT / "input" / "Auto_editor_test" / "1"
    bgm_dir = str(ROOT / "input" / "music")
    out_dir = ROOT / "output" / "e2e_full_test"

    sellpoint_text = sellpoint_path.read_text(encoding="utf-8").strip()
    if not sellpoint_text:
        logger.error("sellpoint 文件为空")
        return

    videos = sorted(video_dir.glob("*.mp4"))
    logger.info(f"输入: {len(videos)} 个视频, sellpoint {len(sellpoint_text)} 字符")

    # ── Skill 1: sellpoint → storyboard（真实 LLM）──────
    logger.info("\n" + "=" * 60)
    logger.info("SKILL 1: Sellpoint → Storyboard")
    logger.info("=" * 60)

    storyboard = skill1_convert(
        sellpoint_text,
        preferred_llm="reverse_prompt",
        output_path=out_dir / "storyboard.json",
    )

    logger.info(f"  产品类型: {storyboard.product_type}")
    logger.info(f"  模特: {storyboard.model_profile}")
    logger.info(f"  场景组: {len(storyboard.scene_groups)} 组")
    logger.info(f"  总镜头: {storyboard.total_shots} 个")

    for sg in storyboard.scene_groups:
        logger.info(f"    {sg.name}: {len(sg.shots)} shots")
        for shot in sg.shots:
            logger.info(f"      Shot {shot.shot_id}: [{shot.type}] {shot.purpose}")

    # ── 跳过 Skill 2/3/4：用现有视频映射到 storyboard shots ──
    logger.info("\n" + "=" * 60)
    logger.info("跳过 Skill 2/3/4: 用现有视频替代")
    logger.info("=" * 60)

    # Storyboard 有 15 个 shots，视频只有 13 个
    # 按顺序映射，多出的 shot 循环复用（仅测试用）
    total_shots = storyboard.total_shots
    video_paths = []
    for i in range(total_shots):
        v = videos[i % len(videos)]
        video_paths.append(str(v))

    logger.info(f"  映射 {total_shots} 个 shots → {len(videos)} 个视频（循环复用）")

    # ── Skill 5: 自动剪辑（真实 LLM）──────────────────
    logger.info("\n" + "=" * 60)
    logger.info("SKILL 5: 自动剪辑")
    logger.info("=" * 60)

    result = skill5_run(
        video_paths=video_paths,
        storyboard=storyboard,
        output_dir=str(out_dir),
        bgm_dir=bgm_dir,
        sellpoint_text=sellpoint_text,
        preferred_llm="reverse_prompt",
    )

    # ── 验证 ──────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("验证输出")
    logger.info("=" * 60)

    mp4 = result["mp4"]
    final_info = get_video_info(mp4)
    probe = run_ffprobe_json(mp4)
    vs = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)

    pix_fmt = vs.get("pix_fmt", "unknown")
    logger.info(f"  MP4: {mp4}")
    logger.info(f"  分辨率: {final_info['width']}x{final_info['height']}")
    logger.info(f"  时长: {final_info['duration']:.2f}s")
    logger.info(f"  帧率: {final_info['fps']:.1f}")
    logger.info(f"  像素格式: {pix_fmt}")
    logger.info(f"  音频: {audio.get('codec_name', 'N/A')} {audio.get('sample_rate', '?')}Hz" if audio else "  音频: 无")

    # 检查所有输出文件
    logger.info(f"\n输出文件:")
    out_path = Path(result["mp4"]).parent
    for f in sorted(out_path.glob("*")):
        if f.is_file():
            logger.info(f"  {f.name} ({f.stat().st_size / 1024:.0f} KB)")

    # 基本断言
    assert final_info["duration"] > 10, f"视频太短: {final_info['duration']:.1f}s"
    assert final_info["duration"] < 40, f"视频太长: {final_info['duration']:.1f}s"
    assert pix_fmt == "yuv420p", f"像素格式不对: {pix_fmt}"
    assert Path(result["srt_en"]).exists(), "英文字幕缺失"
    assert Path(result["srt_cn"]).exists(), "中文字幕缺失"
    assert Path(result["jianying_json"]).exists(), "剪映 JSON 缺失"
    assert Path(result["fcpxml"]).exists(), "FCPXML 缺失"

    logger.info("\n全流程 E2E 测试通过!")


if __name__ == "__main__":
    main()
