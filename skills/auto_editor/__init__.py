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
from .font_scanner import scan_font_library
from .llm_editor import make_editing_decision
from .subtitle_gen import generate_dual_srt
from .ffmpeg_assembler import assemble
from .edl_exporter import export_jianying_draft, export_fcpxml, export_premiere_xml, install_to_jianying

logger = logging.getLogger(__name__)


def run(
    video_paths: list[str],
    storyboard: Storyboard,
    output_dir: str,
    *,
    task_name: str = "",
    bgm_dir: str = "",
    font_dir: str = "",
    title_templates_dir: str = "",  # 空时自动用 settings.FCP_TITLES_DIR
    sellpoint_text: str = "",
    motion_results: list[dict] | None = None,
    layout_hints: dict | None = None,
    preferred_llm: str | None = None,
    preferred_route: str | None = None,
) -> dict:
    """Skill 5 主入口：从视频素材到成品。

    Args:
        video_paths: 视频文件路径列表（与 storyboard shots 一一对应）。
        storyboard: Skill 1 输出的分镜数据。
        output_dir: 输出目录。
        bgm_dir: BGM 库目录（按节奏类型分子文件夹）。
        font_dir: 字体库目录（如 input/fonts/）。
        title_templates_dir: FCP Title 模板目录（可选，默认自动使用 assets/fcp_titles/）。
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
    clip_analyses = analyze_clips(
        video_paths, storyboard, motion_results,
        enable_vision=False,
        preferred_llm=preferred_llm,
    )
    logger.info(f"  分析完成: {len(clip_analyses)} 个片段")

    logger.info("Skill 5 Step 2/4: BGM + 字体扫描")
    bgm_list = scan_bgm_library(bgm_dir) if bgm_dir else []
    font_list = scan_font_library(font_dir) if font_dir else []
    logger.info(f"  BGM 候选: {len(bgm_list)} 首, 字体候选: {len(font_list)} 个")

    logger.info("Skill 5 Step 3/4: LLM 剪辑决策")
    timeline = make_editing_decision(
        clip_analyses,
        storyboard,
        bgm_list,
        font_list=font_list,
        sellpoint_text=sellpoint_text,
        layout_hints=layout_hints,
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

    # SRT 字幕等辅助文件 → 附件/ 子目录
    other_dir = out_dir / "附件"
    other_dir.mkdir(parents=True, exist_ok=True)

    srt_base = task_name if task_name else "subtitles"
    srt_paths = generate_dual_srt(timeline, str(other_dir), base_name=srt_base)
    logger.info(f"  SRT 字幕: {srt_paths}")

    # FFmpeg 组装（内部会用实际时长重算字幕再烧录）
    mp4_name = f"{task_name}.mp4" if task_name else "final_output.mp4"
    mp4_path = str(out_dir / mp4_name)
    assemble(
        timeline, mp4_path,
        srt_path=srt_paths["en"],
        temp_dir=str(other_dir / "temp"),   # temp 也放进附件，不污染顶层
    )

    # NLE 项目导出：所有工程文件都在顶层（out_dir = 任务根目录）
    fcpxml_name  = f"{task_name}.fcpxml" if task_name else "project.fcpxml"
    pr_xml_name  = f"{task_name}-Premiere.xml"   if task_name else "premiere.xml"
    fcpxml_path  = str(out_dir / fcpxml_name)
    pr_xml_path  = str(out_dir / pr_xml_name)
    jianying_path = export_jianying_draft(timeline, str(out_dir), task_name=task_name)
    # title_templates_dir 未传时自动使用 assets/ 目录
    if not title_templates_dir:
        from config import settings
        title_templates_dir = str(settings.FCP_TITLES_DIR)
    export_fcpxml(timeline, fcpxml_path, srt_paths["en"], title_templates_dir=title_templates_dir)
    export_premiere_xml(timeline, pr_xml_path, task_name=task_name)

    logger.info("=" * 60)
    logger.info("Skill 5 全部完成")
    logger.info("=" * 60)

    result = {
        "mp4": mp4_path,
        "srt_en": srt_paths["en"],
        "srt_cn": srt_paths["cn"],
        "jianying_json": jianying_path,
        "fcpxml": fcpxml_path,
        "pr_xml": pr_xml_path,
        "timeline": timeline,
    }

    # 透传 LLM 剪辑决策的 trace
    if hasattr(timeline, "_trace"):
        result["_trace"] = timeline._trace
        result["_trace"]["timeline"] = timeline.model_dump()

    return result
