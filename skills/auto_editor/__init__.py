"""Skill 5: 自动剪辑 — 入口函数。

串联 Module A（分析 + 决策）和 Module B（执行 + 导出），
输出 MP4 成品 + SRT 字幕 + 剪映 JSON + FCPXML。
"""

import logging
from pathlib import Path

from models.storyboard import Storyboard
from models.timeline import EditingTimeline

from .video_analyzer import analyze_clips
from .bgm_scanner import scan_bgm_library
from .llm_editor import make_editing_decision
from .subtitle_gen import generate_dual_srt
from .ffmpeg_assembler import assemble
from .edl_exporter import export_jianying_json, export_fcpxml

logger = logging.getLogger(__name__)


def run(
    video_paths: list[str],
    storyboard: Storyboard,
    output_dir: str,
    *,
    bgm_dir: str = "",
    sellpoint_text: str = "",
    motion_results: list[dict] | None = None,
    preferred_llm: str | None = None,
    preferred_route: str | None = None,
) -> dict:
    """Skill 5 主入口：从视频素材到成品。

    Args:
        video_paths: 视频文件路径列表（与 storyboard shots 一一对应）。
        storyboard: Skill 1 输出的分镜数据。
        output_dir: 输出目录。
        bgm_dir: BGM 库目录（按节奏类型分子文件夹）。
        sellpoint_text: 原始卖点文案（用于字幕提炼）。
        motion_results: Skill 4 输出的运镜结果列表（可选）。
        preferred_llm: LLM 选择。
        preferred_route: Gemini 路由选择。

    Returns:
        {
            "mp4": "path/to/final.mp4",
            "srt_en": "path/to/subtitles_en.srt",
            "srt_cn": "path/to/subtitles_cn.srt",
            "jianying_json": "path/to/draft_content.json",
            "fcpxml": "path/to/project.fcpxml",
            "timeline": EditingTimeline,
        }
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Module A: 分析 + 决策 ──────────────────────────
    logger.info("=" * 60)
    logger.info("Skill 5 Step 1/4: 视频片段分析")
    logger.info("=" * 60)
    clip_analyses = analyze_clips(video_paths, storyboard, motion_results)
    logger.info(f"  分析完成: {len(clip_analyses)} 个片段")

    logger.info("Skill 5 Step 2/4: BGM 扫描")
    bgm_list = scan_bgm_library(bgm_dir) if bgm_dir else []
    logger.info(f"  BGM 候选: {len(bgm_list)} 首")

    logger.info("Skill 5 Step 3/4: LLM 剪辑决策")
    timeline = make_editing_decision(
        clip_analyses,
        storyboard,
        bgm_list,
        sellpoint_text=sellpoint_text,
        preferred_llm=preferred_llm,
        preferred_route=preferred_route,
    )
    logger.info(
        f"  决策完成: {len(timeline.clips)} 个片段, "
        f"预计 {timeline.total_duration:.1f}s"
    )

    # ── Module B: 执行 + 导出 ──────────────────────────
    logger.info("=" * 60)
    logger.info("Skill 5 Step 4/4: 组装 + 导出")
    logger.info("=" * 60)

    # SRT 字幕（预生成，给 NLE 项目引用；MP4 烧录用 assembler 内部实际时长版）
    srt_paths = generate_dual_srt(timeline, str(out_dir))
    logger.info(f"  SRT 字幕: {srt_paths}")

    # FFmpeg 组装（内部会用实际时长重算字幕再烧录）
    mp4_path = str(out_dir / "final_output.mp4")
    assemble(
        timeline, mp4_path,
        srt_path=srt_paths["en"],
        temp_dir=str(out_dir / "temp"),
    )

    # NLE 项目导出
    jianying_path = str(out_dir / "draft_content.json")
    fcpxml_path = str(out_dir / "project.fcpxml")
    export_jianying_json(timeline, jianying_path, srt_paths["en"])
    export_fcpxml(timeline, fcpxml_path, srt_paths["en"])

    logger.info("=" * 60)
    logger.info("Skill 5 全部完成")
    logger.info("=" * 60)

    return {
        "mp4": mp4_path,
        "srt_en": srt_paths["en"],
        "srt_cn": srt_paths["cn"],
        "jianying_json": jianying_path,
        "fcpxml": fcpxml_path,
        "timeline": timeline,
    }
