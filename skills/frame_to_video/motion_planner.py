"""Skill 4: 运镜规划器 - 根据镜头类型自动映射运镜方式并生成视频模型提示词。

架构设计：
- MotionType: 运镜类型枚举（基本 + 组合）
- SHOT_MOTION_MAP: shot_type → 默认运镜类型映射
- ModelAdapter: 将运镜类型转为特定视频模型的 prompt 文本（策略模式）
- 新增模型只需添加 adapter，不影响上游 Skill 1
"""

from enum import Enum


# ── 运镜类型枚举 ──────────────────────────────────────────────

class MotionType(str, Enum):
    """支持的运镜类型，基于 Kling AI 运镜体系（摇镜→环绕，旋转不用）。"""

    # 基本运镜
    HORIZONTAL = "水平运镜"      # 镜头水平左右移动
    VERTICAL = "垂直运镜"        # 镜头垂直上下移动
    PUSH_IN = "推进"             # 镜头推近（Zoom in）
    PULL_OUT = "拉远"            # 镜头拉远（Zoom out）
    ORBIT = "环绕"               # 镜头环绕主体

    # 组合运镜
    ORBIT_PUSH = "环绕推进"      # 环绕 + 推近
    ORBIT_PULL = "环绕拉远"      # 环绕 + 拉远


# ── shot_type → 默认运镜映射 ──────────────────────────────────

SHOT_MOTION_MAP: dict[str, list[MotionType]] = {
    "Wide": [MotionType.HORIZONTAL, MotionType.ORBIT, MotionType.PULL_OUT],
    "Medium": [MotionType.PUSH_IN, MotionType.HORIZONTAL, MotionType.ORBIT_PUSH],
    "Close": [MotionType.PUSH_IN, MotionType.VERTICAL],
    "Macro": [MotionType.PUSH_IN],
}


def get_default_motion(shot_type: str, shot_index: int = 0) -> MotionType:
    """根据 shot_type 返回默认运镜，同类镜头轮换避免重复。

    Args:
        shot_type: Wide / Medium / Close / Macro
        shot_index: 同类型镜头的序号，用于轮换
    """
    options = SHOT_MOTION_MAP.get(shot_type, [MotionType.PUSH_IN])
    return options[shot_index % len(options)]


# ── 模型适配器基类 ────────────────────────────────────────────

class BaseMotionAdapter:
    """视频生成模型的运镜适配器基类。子类实现 to_prompt。"""

    model_name: str = "base"

    def to_prompt(self, motion: MotionType, shot_type: str) -> str:
        raise NotImplementedError


# ── Kling 2.5 Turbo 适配器 ────────────────────────────────────

class Kling25TurboAdapter(BaseMotionAdapter):
    """Kling 2.5 Turbo: 通过文字 prompt 描述运镜（无独立运镜控制 API）。

    提示词策略：
    - 图生视频的 prompt 核心是主体运动，不是场景描述（场景已由图片提供）
    - 简单句子，符合物理规律
    - 运镜描述拼接在主体运动之后
    """

    model_name: str = "kling_2.5_turbo"

    # motion_type → Kling prompt 片段
    MOTION_PROMPTS: dict[MotionType, dict[str, str]] = {
        MotionType.HORIZONTAL: {
            "Wide": "镜头缓慢水平向右平移，展现完整空间全貌",
            "Medium": "镜头缓慢水平横移，跟随人物动作",
            "Close": "镜头小幅水平移动，展示产品侧面",
            "Macro": "镜头微微水平滑动，展示产品表面细节",
        },
        MotionType.VERTICAL: {
            "Wide": "镜头缓慢从上向下垂直移动，展现空间层次",
            "Medium": "镜头缓慢向上推移，展示人物与产品互动",
            "Close": "镜头缓慢向下移动，从面部过渡到手部细节",
            "Macro": "镜头微微向下滑动，展示产品纹理细节",
        },
        MotionType.PUSH_IN: {
            "Wide": "镜头缓慢向前推进，逐渐聚焦场景中心",
            "Medium": "镜头缓慢推近，聚焦人物与产品的互动",
            "Close": "镜头缓慢推近，聚焦产品核心细节",
            "Macro": "镜头极缓推近至微距，展示材质肌理",
        },
        MotionType.PULL_OUT: {
            "Wide": "镜头缓慢向后拉远，展现完整环境氛围",
            "Medium": "镜头缓慢拉远，从产品扩展到周围环境",
            "Close": "镜头缓慢拉远，展示产品与场景的关系",
            "Macro": "镜头缓慢拉远，从微距过渡到产品全貌",
        },
        MotionType.ORBIT: {
            "Wide": "镜头缓慢环绕场景，展现空间全貌",
            "Medium": "镜头小幅环绕，展示人物与产品的多角度",
            "Close": "镜头微微环绕，展示产品立体感",
            "Macro": "镜头极微环绕，展示产品表面光影变化",
        },
        MotionType.ORBIT_PUSH: {
            "Wide": "镜头环绕并缓慢推进，动态展现空间",
            "Medium": "镜头小幅环绕同时推近，聚焦人物与产品",
            "Close": "镜头微微环绕并推近，展示产品立体细节",
            "Macro": "镜头极微环绕推近，展示材质光影变化",
        },
        MotionType.ORBIT_PULL: {
            "Wide": "镜头环绕并缓慢拉远，展现宏大场景氛围",
            "Medium": "镜头小幅环绕同时拉远，展示场景全貌",
            "Close": "镜头微微环绕并拉远，展示产品与环境",
            "Macro": "镜头极微环绕拉远，从细节过渡到整体",
        },
    }

    def to_prompt(self, motion: MotionType, shot_type: str) -> str:
        """生成 Kling 2.5 Turbo 的运镜提示词片段。"""
        prompts_by_shot = self.MOTION_PROMPTS.get(motion, {})
        return prompts_by_shot.get(shot_type, f"{motion.value}")


# ── 适配器注册表 ──────────────────────────────────────────────

ADAPTERS: dict[str, BaseMotionAdapter] = {
    "kling_2.5_turbo": Kling25TurboAdapter(),
}


def get_adapter(model_name: str = "kling_2.5_turbo") -> BaseMotionAdapter:
    """获取指定模型的运镜适配器。"""
    adapter = ADAPTERS.get(model_name)
    if not adapter:
        available = ", ".join(ADAPTERS.keys())
        raise ValueError(f"Unknown model '{model_name}', available: {available}")
    return adapter


# ── 主入口 ────────────────────────────────────────────────────

def plan_motion(
    shot_type: str,
    shot_index: int = 0,
    motion_override: MotionType | None = None,
    model_name: str = "kling_2.5_turbo",
) -> dict:
    """为单个镜头规划运镜，返回运镜类型和模型提示词。

    Args:
        shot_type: Wide / Medium / Close / Macro
        shot_index: 同类型镜头的序号（用于轮换运镜方式）
        motion_override: 手动指定运镜类型（覆盖自动映射）
        model_name: 视频生成模型名称

    Returns:
        {
            "motion_type": "水平运镜",
            "motion_prompt": "镜头缓慢水平向右平移，展现完整空间全貌",
            "model": "kling_2.5_turbo"
        }
    """
    motion = motion_override or get_default_motion(shot_type, shot_index)
    adapter = get_adapter(model_name)
    prompt = adapter.to_prompt(motion, shot_type)

    return {
        "motion_type": motion.value,
        "motion_prompt": prompt,
        "model": model_name,
    }


def plan_storyboard_motions(
    storyboard_data: dict,
    model_name: str = "kling_2.5_turbo",
) -> list[dict]:
    """为整个分镜脚本的 15 个镜头批量规划运镜。

    Args:
        storyboard_data: Skill 1 输出的分镜 JSON
        model_name: 视频生成模型名称

    Returns:
        列表，每项包含 shot_id, shot_type, motion_type, motion_prompt
    """
    type_counters: dict[str, int] = {}
    results = []

    for sg in storyboard_data.get("scene_groups", []):
        for shot in sg.get("shots", []):
            shot_type = shot.get("type", "Medium")
            idx = type_counters.get(shot_type, 0)
            type_counters[shot_type] = idx + 1

            motion_result = plan_motion(
                shot_type=shot_type,
                shot_index=idx,
                model_name=model_name,
            )
            motion_result["shot_id"] = shot.get("shot_id")
            motion_result["shot_type"] = shot_type
            results.append(motion_result)

    return results
