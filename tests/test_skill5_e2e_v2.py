"""Skill 5 端到端测试 v2：修复版。

修复点：
- 不重复使用素材
- 80% cut 转场（8 个片段间 7 个转场，6 个 cut + 1 个 dissolve）
- 单片段时长控制在 2-3.5s
- 结尾最后一个片段 transition_out = "fade"（淡出黑场）
- 包含 BGM 和字幕
"""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models.timeline import EditingTimeline, TimelineClip
from skills.auto_editor.ffmpeg_assembler import assemble
from skills.auto_editor.subtitle_gen import generate_dual_srt
from skills.auto_editor.edl_exporter import export_jianying_json, export_fcpxml
from utils.ffmpeg_wrapper import get_video_info, run_ffprobe_json

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def main():
    video_dir = ROOT / "input" / "Auto_editor_test" / "1"
    bgm_path = ROOT / "input" / "music" / "电影原声带电子芯片音乐-跟着律动摇摆起来-滑水(Surfin_爱给网_aigei_com.mp3"

    videos = sorted(video_dir.glob("*.mp4"))
    if not videos:
        logger.error(f"未找到视频文件: {video_dir}")
        return

    logger.info(f"找到 {len(videos)} 个视频文件")

    video_infos = []
    for v in videos:
        try:
            info = get_video_info(str(v))
            video_infos.append({"path": str(v), "name": v.name, **info})
            logger.info(f"  {v.name}: {info['duration']:.2f}s")
        except Exception as e:
            logger.warning(f"  跳过 {v.name}: {e}")

    # ── 模拟 LLM 决策 ──────────────────────────────────────
    # 选 8 个不重复的片段（总共 13 个，去掉重复的 "(1).mp4"）
    # 排除索引 8 （"_3192_0 (1).mp4" 是索引 6 的副本）
    selected_indices = [5, 3, 2, 10, 0, 9, 12, 11]  # 8 个不重复素材

    # 模拟字幕文案
    subtitles = [
        ("Hand-painted name signs", "手绘名字牌"),
        ("Crafted with love & care", "用心制作"),
        ("Vibrant colors that last", "持久鲜艳的色彩"),
        ("Perfect personalized gift", "完美的个性化礼物"),
        ("Unique handmade designs", "独特的手工设计"),
        ("Joy in every brushstroke", "每一笔都是欢乐"),
        ("Made just for you", "为你专属定制"),
        ("Order yours today!", "立即订购！"),
    ]

    # 转场决策：7 个转场中 6 个 cut + 1 个 dissolve（≈86% cut）
    # 格式: (transition_out, transition_duration)
    # dissolve 放在 shot 3→4 之间（场景切换点）
    transitions_plan = [
        ("cut", 0.0),       # 1→2: cut
        ("cut", 0.0),       # 2→3: cut
        ("dissolve", 0.5),  # 3→4: 交叉溶解（场景转换）
        ("cut", 0.0),       # 4��5: cut
        ("cut", 0.0),       # 5→6: cut
        ("cut", 0.0),       # 6→7: cut
        ("cut", 0.0),       # 7→8: cut（最后一个 clip 的 transition_out 单独覆盖为 fade）
    ]

    # 变速决策：hook 和 CTA 保持原速，过渡加速
    speed_plan = [1.0, 1.25, 1.25, 1.0, 1.5, 1.25, 1.25, 1.0]

    clips = []
    for idx, sel_idx in enumerate(selected_indices):
        vi = video_infos[sel_idx]
        dur = vi["duration"]

        # trim（取中间 70%，留出首尾瑕疵）
        trim_start = dur * 0.15
        trim_end = dur * 0.85
        trimmed = trim_end - trim_start

        speed = speed_plan[idx]
        display_dur = trimmed / speed

        # 转场
        if idx < len(transitions_plan):
            t_out, t_dur = transitions_plan[idx]
        else:
            # 最后一个片段：fade out 到黑场
            t_out, t_dur = "fade", 0.5

        # transition_in 与前一个片段的 transition_out 一致
        if idx == 0:
            t_in = "cut"
        else:
            t_in = transitions_plan[idx - 1][0]

        sub_en, sub_cn = subtitles[idx]

        clips.append(TimelineClip(
            shot_id=sel_idx + 1,
            source_path=vi["path"],
            trim_start=trim_start,
            trim_end=trim_end,
            display_duration=display_dur,
            speed_factor=speed,
            subtitle_text=sub_en,
            subtitle_text_cn=sub_cn,
            transition_in=t_in,
            transition_out=t_out,
            transition_duration=t_dur,
        ))

    # 计算总时长
    total = 0.0
    for c in clips:
        overlap = c.transition_duration if c.transition_out != "cut" else 0.0
        total += c.display_duration - overlap

    timeline = EditingTimeline(
        clips=clips,
        bgm_path=str(bgm_path) if bgm_path.exists() else "",
        bgm_volume=0.6,
        bgm_fade_out_sec=2.0,
        total_duration=total,
    )

    logger.info(f"\n{'='*60}")
    logger.info(f"Timeline: {len(clips)} 片段, 预计 {total:.2f}s")
    logger.info(f"BGM: {timeline.bgm_path or '无'}")
    logger.info(f"{'='*60}")
    for c in clips:
        logger.info(
            f"  Shot {c.shot_id:2d}: {c.display_duration:.2f}s "
            f"×{c.speed_factor}x | {c.transition_in:>8s}→{c.transition_out:<8s} "
            f"| \"{c.subtitle_text}\""
        )

    # ── 执行组装 ──────────────────────────────────────────
    out_dir = ROOT / "output" / "skill5_e2e_test_v2"
    out_dir.mkdir(parents=True, exist_ok=True)

    # SRT 字幕
    logger.info("\n>>> 生成字幕")
    srt_paths = generate_dual_srt(timeline, str(out_dir), base_name="subtitles")

    # FFmpeg 组装
    logger.info("\n>>> FFmpeg 组装")
    mp4_path = str(out_dir / "final_output.mp4")
    assemble(
        timeline, mp4_path,
        srt_path=srt_paths["en"],
        temp_dir=str(out_dir / "temp"),
    )

    # 导出 NLE 项目
    logger.info("\n>>> 导出 NLE 项目")
    export_jianying_json(timeline, str(out_dir / "draft_content.json"), srt_paths["en"])
    export_fcpxml(timeline, str(out_dir / "project.fcpxml"), srt_paths["en"])

    # ── 验证 ──────────────────────────────────────────────
    logger.info(f"\n{'='*60}")
    logger.info("验证输出")
    logger.info(f"{'='*60}")

    final_info = get_video_info(mp4_path)
    probe = run_ffprobe_json(mp4_path)
    vs = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)

    pix_fmt = vs.get("pix_fmt", "unknown")
    logger.info(f"  文件: {mp4_path}")
    logger.info(f"  分辨率: {final_info['width']}x{final_info['height']}")
    logger.info(f"  时长: {final_info['duration']:.2f}s")
    logger.info(f"  帧率: {final_info['fps']:.1f}")
    logger.info(f"  像素格式: {pix_fmt} {'(QuickTime OK)' if pix_fmt == 'yuv420p' else '(QuickTime 不兼容!)'}")
    logger.info(f"  音频: {audio.get('codec_name', 'N/A')} {audio.get('sample_rate', '?')}Hz" if audio else "  音频: 无")

    # 统计转场类型
    cut_count = sum(1 for c in clips[:-1] if c.transition_out == "cut")
    total_transitions = len(clips) - 1
    logger.info(f"  转场: {cut_count}/{total_transitions} cut ({cut_count/total_transitions*100:.0f}%)")
    logger.info(f"  结尾: transition_out={clips[-1].transition_out}")

    logger.info(f"\n输出文件:")
    for f in sorted(out_dir.glob("*")):
        if f.is_file():
            logger.info(f"  {f.name} ({f.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
