"""Skill 4: 运镜规划器 - 根据镜头类型自动映射运镜方式并生成视频模型提示词。

架构设计：
- MotionType: 运镜类型枚举（基本 + 组合 + 固定）
- SHOT_MOTION_MAP: shot_type → 默认运镜类型映射
- ModelAdapter: 将运镜类型转为特定视频模型的 prompt 文本（策略模式）
- 新增模型只需添加 adapter，不影响上游 Skill 1

运镜实操原则（源自可灵 AI 使用经验）：
1. 看图说话：描述画面中能动物体的动态，需符合物理特性和常识
2. 元素命名规范：精准指向画面元素名称（"物品名+运动方式"），避免模糊的"产品XXX"
3. 镜头运动前置：镜头运动描述放在 prompt 最前面
4. 关键词控制：运动方式词汇精准绑定单个物品，避免旋转/下落/飘动/滚动等词同时堆叠
5. 稳定性保障：动态元素多→固定镜头；静止物体→缓慢环绕（防止像图片放大缩小）
6. 运镜幅度量化：明确量化幅度（如"推进幅度约画幅的10%"）
"""

from enum import Enum


# ── 运镜类型枚举 ──────────────────────────────────────────────

class MotionType(str, Enum):
    """支持的运镜类型，基于 Kling AI 运镜体系（摇镜→环绕，旋转不用）。"""

    # 基本运镜
    FIXED = "固定机位"            # 镜头完全不动，靠主体运动撑画面
    HORIZONTAL = "水平运镜"      # 镜头水平左右移动
    VERTICAL = "垂直运镜"        # 镜头垂直上下移动
    PUSH_IN = "推进"             # 镜头推近（Zoom in）
    PULL_OUT = "拉远"            # 镜头拉远（Zoom out）
    ORBIT = "环绕"               # 镜头环绕主体

    # 组合运镜
    ORBIT_PUSH = "环绕推进"      # 环绕 + 推近
    ORBIT_PULL = "环绕拉远"      # 环绕 + 拉远


# ── shot_type → 默认运镜映射 ──────────────────────────────────
# 每类景别至少 3 种可选，轮换时不会连续重复

SHOT_MOTION_MAP: dict[str, list[MotionType]] = {
    "Wide": [MotionType.HORIZONTAL, MotionType.ORBIT, MotionType.PULL_OUT, MotionType.FIXED],
    "Medium": [MotionType.PUSH_IN, MotionType.HORIZONTAL, MotionType.ORBIT_PUSH, MotionType.FIXED],
    "Close": [MotionType.ORBIT, MotionType.VERTICAL, MotionType.PUSH_IN, MotionType.FIXED],
    "Macro": [MotionType.FIXED, MotionType.ORBIT, MotionType.PUSH_IN],
}


def get_default_motion(
    shot_type: str,
    shot_index: int = 0,
    prev_motion: "MotionType | None" = None,
) -> MotionType:
    """根据 shot_type 返回默认运镜，同类镜头轮换 + 连续去重。

    Args:
        shot_type: Wide / Medium / Close / Macro
        shot_index: 同类型镜头的序号（用���轮换）
        prev_motion: 上一个镜头的运镜类型，用于连续去重
    """
    options = SHOT_MOTION_MAP.get(shot_type, [MotionType.PUSH_IN])
    motion = options[shot_index % len(options)]

    # 连续去重：如果与上一个镜头运镜相同，跳到下一个选项
    if prev_motion and motion == prev_motion and len(options) > 1:
        motion = options[(shot_index + 1) % len(options)]

    return motion


# ── 模型适配器基类 ────────────────────────────────────────────

class BaseMotionAdapter:
    """视频生成模型的运镜适配器基类。子类实现 to_prompt。"""

    model_name: str = "base"

    def to_prompt(self, motion: MotionType, shot_type: str) -> str:
        raise NotImplementedError


# ── Kling 2.5 Turbo 适配器 ────────────────────────────────────

class Kling25TurboAdapter(BaseMotionAdapter):
    """Kling 2.5 Turbo: 通过文字 prompt 描述运镜（无独立运镜控制 API）。

    提示词策略（可灵实操经验）：
    - 镜头运动描述放在 prompt 最前面（"镜头缓慢环绕，……"）
    - 运动方式词汇精准绑定单个物品，避免多种运动词同时堆叠
    - 动态元素多→固定镜头保稳定；静止物体→缓慢环绕防止像图片放大缩小
    - 运镜幅度明确量化（如"推进幅度约画幅的10%"）
    - 固定机位禁止叠加任何推/拉/移动描述，避免"固定又推进"的矛盾
    """

    model_name: str = "kling_2.5_turbo"

    # motion_type → Kling prompt 片段（镜头运动描述，拼接在最前面）
    # 规则：FIXED 类型严禁出现"推""拉""移""环绕"等动态词
    MOTION_PROMPTS: dict[MotionType, dict[str, str]] = {
        MotionType.FIXED: {
            "Wide": "固定机位俯拍，画面静止",
            "Medium": "固定机位平视，画面静止",
            "Close": "固定机位近距离拍摄，画面静止",
            "Macro": "固定机位微距拍摄，画面静止",
        },
        MotionType.HORIZONTAL: {
            "Wide": "镜头缓慢水平向右平移约画幅20%，展现完整空间全貌",
            "Medium": "镜头缓慢水平横移约画幅15%，跟随人物动作",
            "Close": "镜头小幅水平横移约画幅10%，展示侧面轮廓",
            "Macro": "镜头微微水平横移约画幅5%，展示表面细节",
        },
        MotionType.VERTICAL: {
            "Wide": "镜头缓慢从上向下垂直移动约画幅20%，展现空间层次",
            "Medium": "镜头缓慢向上推移约画幅15%，展示人物与周围互动",
            "Close": "镜头缓慢向下移动约画幅10%，从面部过渡到手部细节",
            "Macro": "镜头微微向下横移约画幅5%，展示纹理细节",
        },
        MotionType.PUSH_IN: {
            "Wide": "镜头缓慢向前推进约画幅15%，逐渐聚焦场景中心",
            "Medium": "镜头缓慢推近约画幅10%，聚焦人物互动",
            "Close": "镜头缓慢推近约画幅10%，聚焦核心细节",
            "Macro": "镜头极缓推近约画幅5%，展示材质肌理",
        },
        MotionType.PULL_OUT: {
            "Wide": "镜头缓慢向后拉远约画幅15%，展现完整环境氛围",
            "Medium": "镜头缓慢拉远约画幅10%，从焦点扩展到周围环境",
            "Close": "镜头缓慢拉远约画幅10%，展示与场景的关系",
            "Macro": "镜头缓慢拉远约画幅5%，从微距过渡到全貌",
        },
        MotionType.ORBIT: {
            "Wide": "镜头缓慢环绕场景约15度，展现空间全貌",
            "Medium": "镜头小幅环绕约10度，展示多角度",
            "Close": "镜头微微环绕约8度，展示立体感",
            "Macro": "镜头极微环绕约5度，展示表面光影变化",
        },
        MotionType.ORBIT_PUSH: {
            "Wide": "镜头环绕约10度并缓慢推进约画幅10%，动态展现空间",
            "Medium": "镜头小幅环绕约8度同时推近约画幅8%，聚焦人物互动",
            "Close": "镜头微微环绕约5度并推近约画幅5%，展示立体细节",
            "Macro": "镜头极微环绕约3度推近约画幅3%，展示材质光影变化",
        },
        MotionType.ORBIT_PULL: {
            "Wide": "镜头环绕约10度并缓慢拉远约画幅10%，展现宏大场景氛围",
            "Medium": "镜头小幅环绕约8度同时拉远约画幅8%，展示场景全貌",
            "Close": "镜头微微环绕约5度并拉远约画幅5%，展示与环境关系",
            "Macro": "镜头极微环绕约3度拉远约画幅3%，从细节过渡到整体",
        },
    }

    # 矛盾校验：FIXED 的 prompt 中不能包含动态词
    _FIXED_FORBIDDEN_WORDS = {"推", "拉", "移", "环绕", "横移", "推近", "拉远"}

    def to_prompt(self, motion: MotionType, shot_type: str) -> str:
        """生成 Kling 2.5 Turbo 的运镜提示词片段。"""
        prompts_by_shot = self.MOTION_PROMPTS.get(motion, {})
        prompt = prompts_by_shot.get(shot_type, f"{motion.value}")

        # 矛盾校验：固定机位不能包含动态运镜词
        if motion == MotionType.FIXED:
            for word in self._FIXED_FORBIDDEN_WORDS:
                if word in prompt:
                    raise ValueError(
                        f"运镜矛盾: FIXED 机位的 prompt 中出现动态词 '{word}': {prompt}"
                    )

        return prompt


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
    prev_motion: MotionType | None = None,
) -> dict:
    """为单个镜头规划运镜，返回运镜类型和模型提示词。

    Args:
        shot_type: Wide / Medium / Close / Macro
        shot_index: 同类型镜头的序号（用于轮换运镜方式）
        motion_override: 手动指定运镜类型（覆盖自动映射）
        model_name: 视频生成模型名称
        prev_motion: 上一个镜头的运镜类型（用于连续去重）

    Returns:
        {
            "motion_type": "水平运镜",
            "motion_prompt": "镜头缓慢水平向右平移约画幅20%，展现完整空间全貌",
            "model": "kling_2.5_turbo"
        }
    """
    motion = motion_override or get_default_motion(shot_type, shot_index, prev_motion)
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

    策略：
    - 同类型镜头轮换运镜方式
    - 相邻镜头连续去重（避免连续3个推进）
    - 镜头运动 + 主体运动 + 背景运动三层结构

    Args:
        storyboard_data: Skill 1 输出的分镜 JSON
        model_name: 视频生成模型名称

    Returns:
        列表，每项包含 shot_id, shot_type, motion_type, motion_prompt
    """
    type_counters: dict[str, int] = {}
    results = []
    prev_motion: MotionType | None = None

    for sg in storyboard_data.get("scene_groups", []):
        for shot in sg.get("shots", []):
            shot_type = shot.get("type", "Medium")
            idx = type_counters.get(shot_type, 0)
            type_counters[shot_type] = idx + 1

            motion_result = plan_motion(
                shot_type=shot_type,
                shot_index=idx,
                model_name=model_name,
                prev_motion=prev_motion,
            )
            motion_result["shot_id"] = shot.get("shot_id")
            motion_result["shot_type"] = shot_type
            results.append(motion_result)

            # 记录当前运镜，供下一个镜头去重
            prev_motion = MotionType(motion_result["motion_type"])

    return results
