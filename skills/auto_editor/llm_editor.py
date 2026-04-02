"""Skill 5 Module A: LLM 剪辑决策引擎。

接收视频分析结果 + Storyboard + BGM 列表 + 剪辑规则，
调用 LLM 生成剪辑决策，经过时长校验后构建 EditingTimeline。
"""

import json
import logging
from pathlib import Path
from typing import Optional

from models.storyboard import Storyboard
from models.timeline import (
    BgmInfo, EditingTimeline, TimelineClip,
)
from models.video_clip import ClipAnalysis
from utils.json_repair import extract_json
from utils.llm_client import llm_client

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).resolve().parent / "rules"
EDITING_RULES_PATH = RULES_DIR / "editing_rules.md"
SUBTITLE_RULES_PATH = RULES_DIR / "subtitle_rules.md"

# 变速后最小展示时长（秒）
MIN_DISPLAY_DURATION = 1.5
# 成片总时长范围
TARGET_DURATION_MIN = 20.0
TARGET_DURATION_MAX = 30.0
# 可选变速值
VALID_SPEED_FACTORS = [1.0, 1.25, 1.5, 1.75, 2.0]


def load_rules() -> str:
    """加载剪辑规则 + 字幕规则，拼接为完整 system prompt。"""
    parts = []
    for path in [EDITING_RULES_PATH, SUBTITLE_RULES_PATH]:
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n---\n\n".join(parts)


def build_user_message(
    clips: list[ClipAnalysis],
    storyboard: Storyboard,
    bgm_list: list[BgmInfo],
    sellpoint_text: str = "",
) -> str:
    """构建发给 LLM 的 user message。"""
    # 片段信息
    clip_info = []
    for c in clips:
        status = "废片" if c.is_rejected else "可用"
        clip_info.append(
            f"  Shot {c.shot_id} | {c.shot_type:6s} | {c.duration:.1f}s | "
            f"{status} | 目的: {c.purpose} | 运镜: {c.motion_prompt[:50]}"
        )

    # BGM 列表
    bgm_info = []
    for b in bgm_list:
        bgm_info.append(f"  {b.name} | {b.duration:.0f}s | {b.path}")

    # 产品信息
    product_info = (
        f"产品类型: {storyboard.product_type}\n"
        f"模特画像: {storyboard.model_profile}\n"
        f"导演计划: {json.dumps(storyboard.director_plan, ensure_ascii=False)}"
    )

    return f"""# 剪辑任务

## 产品信息
{product_info}

## 原始卖点文案
{sellpoint_text or '(未提供，请根据 purpose 字段推断卖点)'}

## 可用片段（共 {len(clips)} 个，含废片）
{chr(10).join(clip_info)}

## 可用 BGM（共 {len(bgm_list)} 首）
{chr(10).join(bgm_info) if bgm_info else '  (无可用 BGM，bgm_choice 留空)'}

## 任务要求
请根据上述剪辑规则和字幕规则，为这些片段做出完整的剪辑决策。
直接输出 JSON，不要有其它说明文字。"""


def make_editing_decision(
    clips: list[ClipAnalysis],
    storyboard: Storyboard,
    bgm_list: list[BgmInfo],
    sellpoint_text: str = "",
    preferred_llm: Optional[str] = None,
    preferred_route: Optional[str] = None,
    max_retries: int = 2,
) -> EditingTimeline:
    """调用 LLM 生成剪辑决策，校验后构建 EditingTimeline。

    Args:
        clips: video_analyzer 输出的片段分析列表。
        storyboard: Skill 1 输出的分镜数据。
        bgm_list: bgm_scanner 输出的 BGM 列表。
        sellpoint_text: 原始卖点文案（用于字幕提炼）。
        preferred_llm: LLM 选择。
        preferred_route: Gemini 路由选择。
        max_retries: JSON 解析失败时重试次数。

    Returns:
        校验通过的 EditingTimeline。
    """
    system_prompt = load_rules()
    user_message = build_user_message(clips, storyboard, bgm_list, sellpoint_text)

    # 构建 clip 查找表（用于校验时获取原始时长）
    clip_map: dict[int, ClipAnalysis] = {c.shot_id: c for c in clips}

    last_err = None
    for attempt in range(1, max_retries + 2):
        if attempt > 1:
            logger.info(f"[Editor] 重试 {attempt}/{max_retries + 1}")

        try:
            raw = llm_client.call(
                system_prompt,
                user_message,
                preferred_llm=preferred_llm,
                preferred_route=preferred_route,
                json_mode=True,
            )
        except Exception as e:
            logger.error(f"[Editor] LLM 调用失败: {e}")
            raise

        try:
            data = extract_json(raw)
        except ValueError as e:
            last_err = e
            if attempt <= max_retries:
                logger.warning(f"[Editor] JSON 解析失败 (attempt {attempt})，重试...")
                continue
            raise ValueError(f"JSON 解析失败，已重试 {max_retries + 1} 次: {e}") from e

        # 构建 EditingTimeline
        try:
            timeline = _build_timeline(data, clip_map, bgm_list)
        except ValueError as e:
            last_err = e
            if attempt <= max_retries:
                logger.warning(f"[Editor] 校验失败 (attempt {attempt}): {e}，重试...")
                continue
            raise

        logger.info(
            f"[Editor] 决策完成: {len(timeline.clips)} 个片段, "
            f"总时长 {timeline.total_duration:.1f}s, BGM: {timeline.bgm_path or '无'}"
        )
        return timeline

    raise ValueError(f"剪辑决策失败: {last_err}")


def _build_timeline(
    data: dict,
    clip_map: dict[int, ClipAnalysis],
    bgm_list: list[BgmInfo],
) -> EditingTimeline:
    """从 LLM 输出的 JSON 构建 EditingTimeline，执行时长校验链。"""
    raw_clips = data.get("clips", [])
    if not raw_clips:
        raise ValueError("LLM 输出中没有 clips 数组")

    timeline_clips: list[TimelineClip] = []

    for rc in raw_clips:
        shot_id = rc["shot_id"]
        source_clip = clip_map.get(shot_id)

        if not source_clip:
            logger.warning(f"Shot {shot_id} 不在分析结果中，跳过")
            continue
        if source_clip.is_rejected:
            logger.warning(f"Shot {shot_id} 是废片，跳过")
            continue

        # 解析 trim 范围
        trim_start = float(rc.get("trim_start", source_clip.usable_start))
        trim_end = float(rc.get("trim_end", source_clip.usable_end))
        trimmed_duration = trim_end - trim_start

        if trimmed_duration <= 0:
            logger.warning(f"Shot {shot_id}: trim 范围无效 ({trim_start}-{trim_end})，跳过")
            continue

        # 解析并校验 speed_factor
        speed_factor = float(rc.get("speed_factor", 1.0))
        speed_factor = _validate_speed(shot_id, speed_factor, trimmed_duration)

        display_duration = trimmed_duration / speed_factor

        timeline_clips.append(TimelineClip(
            shot_id=shot_id,
            scene_group_id=source_clip.scene_group_id,
            source_path=source_clip.file_path,
            trim_start=trim_start,
            trim_end=trim_end,
            display_duration=round(display_duration, 2),
            speed_factor=speed_factor,
            subtitle_text=rc.get("subtitle_text", ""),
            subtitle_text_cn=rc.get("subtitle_text_cn", ""),
            subtitle_style=rc.get("subtitle_style", "selling_point"),
            transition_in=rc.get("transition_in", "cut"),
            transition_out=rc.get("transition_out", "cut"),
            transition_duration=float(rc.get("transition_duration", 0.4)),
        ))

    if not timeline_clips:
        raise ValueError("校验后没有可用片段")

    # 计算总时长（减去转场重叠）
    total = _calculate_total_duration(timeline_clips)

    # 总时长校验（警告但不阻断，留给人工调整）
    if total < TARGET_DURATION_MIN:
        logger.warning(f"总时长 {total:.1f}s 低于目标 {TARGET_DURATION_MIN}s")
    elif total > TARGET_DURATION_MAX:
        logger.warning(f"总时长 {total:.1f}s 超过目标 {TARGET_DURATION_MAX}s")

    # 转场比例校验：cut 应占 70%+（警告不阻断）
    if len(timeline_clips) > 1:
        total_transitions = len(timeline_clips) - 1
        cut_count = sum(1 for c in timeline_clips[:-1] if c.transition_out == "cut")
        cut_ratio = cut_count / total_transitions
        if cut_ratio < 0.7:
            logger.warning(
                f"Cut 转场比例 {cut_ratio:.0%} ({cut_count}/{total_transitions}) "
                f"低于 70% 底线，节奏可能过拖"
            )

    # 解析 BGM
    bgm_choice = data.get("bgm_choice", "")
    bgm_path = ""
    for b in bgm_list:
        if bgm_choice and (bgm_choice in b.name or bgm_choice in b.path):
            bgm_path = b.path
            break

    return EditingTimeline(
        clips=timeline_clips,
        bgm_path=bgm_path,
        bgm_volume=1.0,
        bgm_fade_out_sec=2.0,
        total_duration=round(total, 2),
    )


def _validate_speed(shot_id: int, speed_factor: float, trimmed_duration: float) -> float:
    """校验并修正 speed_factor，确保变速后时长 ≥ MIN_DISPLAY_DURATION。

    修正策略：逐级降速，直到满足约束。
    """
    # 限制在合法范围
    speed_factor = max(1.0, min(2.0, speed_factor))

    # 就近对齐到合法值
    speed_factor = min(VALID_SPEED_FACTORS, key=lambda x: abs(x - speed_factor))

    # 变速后时长校验
    display_after = trimmed_duration / speed_factor
    if display_after >= MIN_DISPLAY_DURATION:
        return speed_factor

    # 逐级降速（从快到慢，找最快的合法速度）
    for sf in reversed(VALID_SPEED_FACTORS):
        if trimmed_duration / sf >= MIN_DISPLAY_DURATION:
            logger.info(
                f"Shot {shot_id}: speed {speed_factor}x → {sf}x "
                f"(变速后 {trimmed_duration/speed_factor:.1f}s < {MIN_DISPLAY_DURATION}s)"
            )
            return sf

    # 所有速度都不满足，用 1.0x
    logger.warning(f"Shot {shot_id}: 素材太短 ({trimmed_duration:.1f}s)，强制 1.0x")
    return 1.0


def _calculate_total_duration(clips: list[TimelineClip]) -> float:
    """计算时间线总时长，考虑转场重叠。"""
    if not clips:
        return 0.0

    total = clips[0].display_duration
    for i in range(1, len(clips)):
        # 转场重叠：前一个 clip 的 transition_out 和当前 clip 的 transition_in
        # 如果都是 cut 则无重叠
        overlap = 0.0
        prev_out = clips[i - 1].transition_out
        curr_in = clips[i].transition_in
        if prev_out != "cut" or curr_in != "cut":
            overlap = clips[i].transition_duration
        total += clips[i].display_duration - overlap

    return total
