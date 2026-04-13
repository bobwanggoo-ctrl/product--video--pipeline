"""剪辑选材：从合规通过的帧中选出最适合生成视频的子集。

纯规则逻辑，不用 LLM。基于景别分布、场景覆盖、合规评分排序，
输出分批生成计划（第一批 + 备选）。
"""

import logging
from dataclasses import dataclass, field

from models.storyboard import Storyboard
from models.compliance import ComplianceResult, ComplianceLevel

logger = logging.getLogger(__name__)

# Skill 5 需要 8-12 个视频片段
MIN_CLIPS_NEEDED = 8
MAX_CLIPS_NEEDED = 12

# 景别配额（保证节奏多样性）
SHOT_TYPE_QUOTA = {
    "Wide": (2, 5),    # 至少 2 个，最多 5 个
    "Medium": (3, 6),  # 中景最多
    "Close": (1, 3),
    "Macro": (0, 3),
}

# 第一批生成数量（留 buffer 给失败补拍）
FIRST_BATCH_SIZE = 11


@dataclass
class SelectionPlan:
    """选材结果。"""
    # 第一批：优先生成
    first_batch: list[int] = field(default_factory=list)   # shot_id 列表
    # 备选：第一批不够时补拍
    standby: list[int] = field(default_factory=list)
    # 淘汰：合规失败或景别超额
    rejected: list[dict] = field(default_factory=list)     # [{shot_id, reason}]
    # 景别分布统计
    type_distribution: dict[str, int] = field(default_factory=dict)


def select_frames(
    storyboard: Storyboard,
    compliance_results: list[ComplianceResult] | None = None,
) -> SelectionPlan:
    """从 15 个 shot 中选出生成视频的优先序列。

    Args:
        storyboard: Skill 1 输出的分镜（含景别信息）。
        compliance_results: 合规检查结果（可选，没有则全部视为通过）。

    Returns:
        SelectionPlan: 第一批 + 备选 + 淘汰。
    """
    # 构建 shot 信息表
    shots = []
    for sg in storyboard.scene_groups:
        for shot in sg.shots:
            shots.append({
                "shot_id": shot.shot_id,
                "type": shot.type,
                "purpose": shot.purpose,
                "scene_group_id": sg.scene_group_id,
            })

    # 合并合规评分
    compliance_map: dict[int, ComplianceResult] = {}
    if compliance_results:
        for cr in compliance_results:
            compliance_map[cr.shot_id] = cr

    # ── Step 1: 过滤合规失败 ──
    passed = []
    plan = SelectionPlan()

    for s in shots:
        sid = s["shot_id"]
        cr = compliance_map.get(sid)

        if cr and cr.level == ComplianceLevel.FAIL:
            plan.rejected.append({"shot_id": sid, "reason": f"合规失败: {cr.summary}"})
            continue

        # 合规评分（没有合规数据时默认 1.0）
        score = cr.score if cr else 1.0
        passed.append({**s, "compliance_score": score})

    if not passed:
        logger.error("所有帧都未通过合规检查")
        return plan

    # ── Step 2: 按景别配额 + 场景覆盖 + 评分排序 ──
    scored = _score_and_rank(passed, storyboard)

    # ── Step 3: 分批 ──
    first_batch_size = min(FIRST_BATCH_SIZE, len(scored))
    plan.first_batch = [s["shot_id"] for s in scored[:first_batch_size]]
    plan.standby = [s["shot_id"] for s in scored[first_batch_size:]]

    # 统计景别分布
    for s in scored[:first_batch_size]:
        t = s["type"]
        plan.type_distribution[t] = plan.type_distribution.get(t, 0) + 1

    # 日志
    logger.info(f"选材完成: {len(passed)}/{len(shots)} 通过合规")
    logger.info(f"  第一批: {len(plan.first_batch)} 个 {plan.first_batch}")
    logger.info(f"  备选: {len(plan.standby)} 个 {plan.standby}")
    logger.info(f"  淘汰: {len(plan.rejected)} 个")
    logger.info(f"  景别分布: {plan.type_distribution}")

    # 检查景别配额
    _warn_quota(plan.type_distribution)

    return plan


def check_and_backfill(
    plan: SelectionPlan,
    successful_shot_ids: list[int],
) -> list[int]:
    """视频生成后检查成功数，返回需要补拍的 shot_id。

    Args:
        plan: 原始选材计划。
        successful_shot_ids: 视频生成成功的 shot_id 列表。

    Returns:
        需要补拍的 shot_id 列表（从 standby 中取）。空列表表示够了。
    """
    success_count = len(successful_shot_ids)

    if success_count >= MIN_CLIPS_NEEDED:
        logger.info(f"视频生成成功 {success_count} 个，满足最低要求 {MIN_CLIPS_NEEDED}")
        return []

    needed = MIN_CLIPS_NEEDED - success_count
    backfill = plan.standby[:needed]

    if len(backfill) < needed:
        # 备选也不够，从失败的第一批中挑选重试
        failed_first = [sid for sid in plan.first_batch if sid not in successful_shot_ids]
        retry = failed_first[:needed - len(backfill)]
        backfill.extend(retry)
        logger.warning(
            f"备选不足，需要重试 {len(retry)} 个失败片段: {retry}"
        )

    logger.info(
        f"视频生成成功 {success_count} 个，不足 {MIN_CLIPS_NEEDED}，"
        f"补拍 {len(backfill)} 个: {backfill}"
    )
    return backfill


def _score_and_rank(passed: list[dict], storyboard: Storyboard) -> list[dict]:
    """综合评分排序：场景覆盖 > 景别多样性 > 合规评分。"""
    # 统计每个 scene_group 有多少可用 shot
    group_counts: dict[int, int] = {}
    for s in passed:
        gid = s["scene_group_id"]
        group_counts[gid] = group_counts.get(gid, 0) + 1

    # 每个 shot 的景别当前被选中次数（用于控制配额）
    type_selected: dict[str, int] = {}

    for s in passed:
        gid = s["scene_group_id"]
        shot_type = s["type"]

        # 场景稀缺度：该 scene_group 可用 shot 越少 → 这个 shot 越珍贵
        scarcity = 1.0 / max(group_counts.get(gid, 1), 1)

        # 景别需求度：未达最低配额的类型优先
        type_min, type_max = SHOT_TYPE_QUOTA.get(shot_type, (0, 5))
        current = type_selected.get(shot_type, 0)
        if current < type_min:
            type_need = 2.0  # 强需求
        elif current < type_max:
            type_need = 1.0  # 正常
        else:
            type_need = 0.3  # 超额，降权

        # 综合分 = 场景稀缺 × 景别需求 × 合规评分
        s["selection_score"] = scarcity * type_need * s["compliance_score"]

    # 按分数降序
    passed.sort(key=lambda x: x["selection_score"], reverse=True)

    # 更新 type_selected（模拟逐个选入的过程）
    final = []
    type_selected = {}
    for s in passed:
        shot_type = s["type"]
        _, type_max = SHOT_TYPE_QUOTA.get(shot_type, (0, 5))
        current = type_selected.get(shot_type, 0)

        # 超额的放到最后（但不完全淘汰）
        if current >= type_max:
            s["selection_score"] *= 0.1
        else:
            type_selected[shot_type] = current + 1

        final.append(s)

    # 重新排序（超额的排后面）
    final.sort(key=lambda x: x["selection_score"], reverse=True)
    return final


def _warn_quota(dist: dict[str, int]) -> None:
    """检查景别配额，不满足时发 warning。"""
    for shot_type, (min_count, max_count) in SHOT_TYPE_QUOTA.items():
        actual = dist.get(shot_type, 0)
        if actual < min_count:
            logger.warning(f"景别 {shot_type} 只有 {actual} 个，低于最低要求 {min_count}")
        elif actual > max_count:
            logger.warning(f"景别 {shot_type} 有 {actual} 个，超过上限 {max_count}")
