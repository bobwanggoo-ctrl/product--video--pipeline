"""Skill 5 端到端测试：模拟 LLM 决策 → 字幕生成 → FFmpeg 组装 → 导出。

使用 input/Auto_editor_test/1/ 下的真实视频片段。
无需 .env，手动构建 EditingTimeline 代替 LLM 调用。
"""

import logging
import sys
import tempfile
from pathlib import Path

# 项目根目录加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models.timeline import EditingTimeline, TimelineClip
from skills.auto_editor.ffmpeg_assembler import assemble
from skills.auto_editor.subtitle_gen import generate_srt, generate_dual_srt
from skills.auto_editor.edl_exporter import export_jianying_json, export_fcpxml
from utils.ffmpeg_wrapper import get_video_info

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def main():
    video_dir = ROOT / "input" / "Auto_editor_test" / "1"
    bgm_path = ROOT / "input" / "music" / "电影原声带电子芯片音乐-跟着律动摇摆起来-滑水(Surfin_爱给网_aigei_com.mp3"

    # 收集视频文件
    videos = sorted(video_dir.glob("*.mp4"))
    if not videos:
        logger.error(f"未找到视频文件: {video_dir}")
        return

    logger.info(f"找到 {len(videos)} 个视频文件")

    # 获取每个视频的信息
    video_infos = []
    for v in videos:
        try:
            info = get_video_info(str(v))
            video_infos.append({"path": str(v), **info})
            logger.info(f"  {v.name}: {info['duration']:.2f}s {info['width']}x{info['height']}")
        except Exception as e:
            logger.warning(f"  跳过 {v.name}: {e}")

    # 模拟 LLM 决策：选 8 个片段，混合节奏
    selected = [0, 6, 1, 3, 10, 7, 4, 11]  # 选取部分，模拟剪辑排序
    selected = [i for i in selected if i < len(video_infos)]

    # 模拟字幕文案（英文 + 中文）
    subtitles = [
        ("Hand-painted personalized name signs", "手绘个性化名字牌"),
        ("Perfect gift for any occasion", "适合任何场合的完美礼物"),
        ("Each piece crafted with love", "每一件都用心制作"),
        ("Vibrant colors that pop", "鲜艳夺目的色彩"),
        ("Make someone's day special", "让每一天都特别"),
        ("Unique designs, one of a kind", "独一无二的设计"),
        ("From our hands to your heart", "从我们手中到你心中"),
        ("Order yours today!", "立即订购！"),
    ]

    # 构建 timeline
    clips = []
    for idx, sel_idx in enumerate(selected):
        vi = video_infos[sel_idx]
        dur = vi["duration"]

        # 模拟 trim（取中间 80%）
        trim_start = dur * 0.1
        trim_end = dur * 0.9
        trimmed = trim_end - trim_start

        # 模拟变速决策
        if idx in (0, 3, 7):  # hook + 卖点 + CTA 保持原速
            speed = 1.0
        elif idx in (1, 5):  # 过渡镜头加速
            speed = 1.5
        else:
            speed = 1.25

        display_dur = trimmed / speed

        # 模拟转场决策
        if idx == 0:
            t_in, t_out = "cut", "fade"
        elif idx == len(selected) - 1:
            t_in, t_out = "fade", "cut"
        elif idx % 3 == 0:
            t_in, t_out = "dissolve", "dissolve"
        elif idx % 3 == 1:
            t_in, t_out = "fade", "cut"
        else:
            t_in, t_out = "cut", "fade"

        sub_en, sub_cn = subtitles[idx] if idx < len(subtitles) else ("", "")

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
            transition_duration=0.4 if t_out != "cut" else 0.0,
        ))

    # 计算总时长
    total = 0.0
    for c in clips:
        overlap = c.transition_duration if c.transition_out != "cut" else 0.0
        total += c.display_duration - overlap
    # 加上最后一个 clip 的被减掉的 overlap（最后一个不需要减）
    # 实际上上面的循环已经正确了，最后一个 transition_out=cut → overlap=0

    timeline = EditingTimeline(
        clips=clips,
        bgm_path=str(bgm_path) if bgm_path.exists() else "",
        bgm_volume=0.6,
        bgm_fade_out_sec=2.0,
        total_duration=total,
    )

    logger.info(f"\n=== Timeline ===")
    logger.info(f"片段数: {len(clips)}")
    logger.info(f"总时长: {total:.2f}s")
    logger.info(f"BGM: {timeline.bgm_path or '无'}")
    for c in clips:
        logger.info(
            f"  Shot {c.shot_id}: trim[{c.trim_start:.1f}-{c.trim_end:.1f}] "
            f"×{c.speed_factor}x → {c.display_duration:.2f}s "
            f"| {c.transition_in}→{c.transition_out} "
            f"| \"{c.subtitle_text[:30]}\""
        )

    # 输出目录
    out_dir = ROOT / "output" / "skill5_e2e_test"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 生成 SRT 字幕
    logger.info("\n=== Step 1: 生成字幕 ===")
    srt_paths = generate_dual_srt(timeline, str(out_dir), base_name="test_subtitles")
    logger.info(f"英文字幕: {srt_paths['en']}")
    logger.info(f"中文字幕: {srt_paths['cn']}")

    # Step 2: FFmpeg 组装
    logger.info("\n=== Step 2: FFmpeg 组装 ===")
    mp4_path = str(out_dir / "final_output.mp4")
    assemble(
        timeline, mp4_path,
        srt_path=srt_paths["en"],
        temp_dir=str(out_dir / "temp"),
    )

    # Step 3: 导出剪映 JSON
    logger.info("\n=== Step 3: 导出剪映 JSON ===")
    jy_path = export_jianying_json(timeline, str(out_dir / "draft_content.json"), srt_paths["en"])

    # Step 4: 导出 FCPXML
    logger.info("\n=== Step 4: 导出 FCPXML ===")
    fcp_path = export_fcpxml(timeline, str(out_dir / "project.fcpxml"), srt_paths["en"])

    # 验证输出
    logger.info("\n=== 验证输出 ===")
    final_info = get_video_info(mp4_path)
    logger.info(f"MP4: {mp4_path}")
    logger.info(f"  分辨率: {final_info['width']}x{final_info['height']}")
    logger.info(f"  时长: {final_info['duration']:.2f}s")
    logger.info(f"  帧率: {final_info['fps']:.1f}")

    # 检查像素格式
    from utils.ffmpeg_wrapper import run_ffprobe_json
    probe = run_ffprobe_json(mp4_path)
    vs = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    pix_fmt = vs.get("pix_fmt", "unknown")
    logger.info(f"  像素格式: {pix_fmt}")
    if pix_fmt == "yuv420p":
        logger.info("  ✓ QuickTime 兼容")
    else:
        logger.warning(f"  ✗ 像素格式 {pix_fmt} 可能不兼容 QuickTime")

    # 检查音频流（BGM）
    audio_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio_stream:
        logger.info(f"  音频: {audio_stream.get('codec_name', 'unknown')} "
                    f"{audio_stream.get('sample_rate', '?')}Hz")
    else:
        logger.info("  音频: 无")

    logger.info(f"\n所有输出文件:")
    for f in sorted(out_dir.glob("*")):
        if f.is_file():
            logger.info(f"  {f.name} ({f.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
