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

import re
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


# ── 场景动态分析 ──────────────────────────────────────────────

# 大幅动作动词：人物有明显的身体运动
_HIGH_DYNAMICS_VERBS = {
    "弯腰", "跪在", "跪", "站在", "奔跑", "跳", "扑",
    "张开", "伸展", "旋转", "挥动", "蹲", "躺",
    "后退", "冲", "跃", "翻",
}

# 小幅动作动词：人物有动作但幅度不大
_MEDIUM_DYNAMICS_VERBS = {
    "微笑", "端详", "捧着", "轻扶", "拈起", "抚摸",
    "侧坐", "盘腿", "靠着", "倚靠", "凝视", "望向",
    "举起", "捧起", "拿起", "放置", "轻放", "握着",
    "低头", "侧头", "含笑", "欣赏", "品尝", "啜饮",
    "双手", "一手", "单手",
}


def _analyze_scene_dynamics(prompt_cn: str) -> str:
    """分析 prompt_cn 的场景动态等级。

    Returns:
        "high"  - 有人物且有大幅动作（弯腰、跪、张开等）→ 用固定机位
        "medium" - 有人物但动作幅度小（微笑、端详、捧着等）→ 多样运镜
        "low"   - 纯静物，无人物 → 统一缓慢环绕
    """
    # 判断是否有人物
    has_person = any(kw in prompt_cn for kw in ["白人女性", "白人男性", "白人儿童", "穿着"])

    if not has_person:
        return "low"

    # 只在人物段落中扫描动作（避免"烛光跳动"等背景描述被误判）
    clothing_pos = prompt_cn.rfind("穿着")
    if clothing_pos == -1:
        return "medium"
    comp_pos = prompt_cn.find("；构图", clothing_pos)
    person_block = prompt_cn[clothing_pos:comp_pos] if comp_pos != -1 else prompt_cn[clothing_pos:]

    for verb in _HIGH_DYNAMICS_VERBS:
        if verb in person_block:
            return "high"

    for verb in _MEDIUM_DYNAMICS_VERBS:
        if verb in person_block:
            return "medium"

    # 有人物但没识别到具体动作，保守当 medium
    return "medium"


# ── 动态等级 × 景别 → 运镜候选池 ─────────────────────────────
# HIGH: 人物大动作 → FIXED 优先，镜头不动让人物撑画面
# MEDIUM: 人物小动作 → 多样运镜，增加视觉丰富度
# LOW: 纯静物 → 统一 ORBIT，缓慢环绕避免像图片

DYNAMICS_MOTION_MAP: dict[str, dict[str, list[MotionType]]] = {
    "high": {
        "Wide":   [MotionType.FIXED, MotionType.PULL_OUT],
        "Medium": [MotionType.FIXED, MotionType.HORIZONTAL],
        "Close":  [MotionType.FIXED, MotionType.VERTICAL],
        "Macro":  [MotionType.FIXED],
    },
    "medium": {
        "Wide":   [MotionType.HORIZONTAL, MotionType.ORBIT, MotionType.PUSH_IN],
        "Medium": [MotionType.PUSH_IN, MotionType.HORIZONTAL, MotionType.ORBIT_PUSH],
        "Close":  [MotionType.ORBIT, MotionType.PUSH_IN, MotionType.VERTICAL],
        "Macro":  [MotionType.ORBIT, MotionType.PUSH_IN],
    },
    "low": {
        "Wide":   [MotionType.ORBIT],
        "Medium": [MotionType.ORBIT],
        "Close":  [MotionType.ORBIT],
        "Macro":  [MotionType.ORBIT],
    },
}


def get_default_motion(
    shot_type: str,
    dynamics: str = "medium",
    shot_index: int = 0,
    prev_motion: "MotionType | None" = None,
) -> MotionType:
    """根据场景动态等级和景别返回运镜，同类镜头轮换 + 连续去重。

    Args:
        shot_type: Wide / Medium / Close / Macro
        dynamics: 场景动态等级 ("high" / "medium" / "low")
        shot_index: 同类型镜头的序号（用于轮换）
        prev_motion: 上一个镜头的运镜类型，用于连续去重
    """
    dmap = DYNAMICS_MOTION_MAP.get(dynamics, DYNAMICS_MOTION_MAP["medium"])
    options = dmap.get(shot_type, [MotionType.PUSH_IN])
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

    # motion_type → Kling prompt 片段（仅镜头运动层，拼接在最前面）
    # 规则：FIXED 类型严禁出现"推""拉""移""环绕"等动态词
    # 注意：FIXED 只锁定镜头不动，主体运动和背景运动由三层结构的后两层负责
    MOTION_PROMPTS: dict[MotionType, dict[str, str]] = {
        MotionType.FIXED: {
            "Wide": "固定机位俯拍",
            "Medium": "固定机位平视",
            "Close": "固定机位近距离拍摄",
            "Macro": "固定机位微距拍摄",
        },
        MotionType.HORIZONTAL: {
            "Wide": "镜头缓慢水平向右平移约画幅20%，展现完整空间全貌",
            "Medium": "镜头缓慢水平横移约画幅15%",
            "Close": "镜头小幅水平横移约画幅10%，展示侧面轮廓",
            "Macro": "镜头微微水平横移约画幅5%，展示表面细节",
        },
        MotionType.VERTICAL: {
            "Wide": "镜头缓慢从上向下垂直移动约画幅20%，展现空间层次",
            "Medium": "镜头缓慢向上移动约画幅15%",
            "Close": "镜头缓慢向下移动约画幅10%，从面部过渡到手部细节",
            "Macro": "镜头微微向下移动约画幅5%，展示纹理细节",
        },
        MotionType.PUSH_IN: {
            "Wide": "镜头缓慢向前推进约画幅15%，逐渐聚焦场景中心",
            "Medium": "镜头缓慢推近约画幅10%",
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
            "Medium": "镜头小幅环绕约8度同时推近约画幅8%",
            "Close": "镜头微微环绕约5度并推近约画幅5%，展示立体细节",
            "Macro": "镜头极微环绕约3度推��约画幅3%，展示材质光影变化",
        },
        MotionType.ORBIT_PULL: {
            "Wide": "镜头环绕约10度并缓慢拉远约画幅10%，展现宏大场景氛围",
            "Medium": "镜头小幅环绕约8度同时拉远约画幅8%",
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
    dynamics: str = "medium",
    shot_index: int = 0,
    motion_override: MotionType | None = None,
    model_name: str = "kling_2.5_turbo",
    prev_motion: MotionType | None = None,
) -> dict:
    """为单个镜头规划运镜（场景感知）。

    Args:
        shot_type: Wide / Medium / Close / Macro
        dynamics: 场景动态等级 ("high" / "medium" / "low")
        shot_index: 同类型镜头的序号（用于轮换运镜方式）
        motion_override: 手动指定运镜类型（覆盖自动映射）
        model_name: 视频生成模型名称
        prev_motion: 上一个镜头的运镜类型（用于连续去重）
    """
    motion = motion_override or get_default_motion(shot_type, dynamics, shot_index, prev_motion)
    adapter = get_adapter(model_name)
    camera_prompt = adapter.to_prompt(motion, shot_type)

    return {
        "_motion_type": motion,
        "camera_motion": camera_prompt,
        "model": model_name,
    }


def _extract_subject_motion(prompt_cn: str, shot_type: str) -> str:
    """从 prompt_cn 中提取主体运动描述（基于规则，复杂场景需 LLM 补充）。

    提取策略：
    1. 定位人物段落：找 "穿着" 到 "；构图" 之间的文本
    2. 在人物段落中找第一个动作动词锚点
    3. 从锚点截取到段落结束，即为动作描述
    4. 在动作前补上人物标识（如"白人女性"），符合可灵规则"元素名+运动方式"
    """
    # 提取人物标识（"白人女性""白人男性""白人儿童"等）
    person_label = ""
    for label in ["白人女性", "白人男性", "白人儿童"]:
        if label in prompt_cn:
            person_label = label
            break

    # 定位人物段落：最后一个 "穿着" 到 "；构图" 之间
    clothing_pos = prompt_cn.rfind("穿着")
    if clothing_pos == -1:
        return ""

    comp_pos = prompt_cn.find("；构图", clothing_pos)
    if comp_pos == -1:
        comp_pos = prompt_cn.find("；", clothing_pos)
    if comp_pos == -1:
        person_block = prompt_cn[clothing_pos:]
    else:
        person_block = prompt_cn[clothing_pos:comp_pos]

    # 跳过着装描述：找到着装后第一个逗号后的内容
    # "穿着纯色酒红色高领毛衣，正弯腰微笑着调整..."
    # "穿着纯色深蓝色衬衫的同伴举起红酒杯..."（无逗号分隔）
    ACTION_ANCHORS = [
        "正弯腰", "正跪在", "正站在", "正坐在", "正在",
        "双手", "一手", "单手",
        "后退", "侧坐", "盘腿坐", "侧身立",
        "举起", "捧起", "拿起", "拈起", "轻放", "放置",
        "弯腰", "跪在", "盘腿",
    ]

    # 在人物段落中找第一个出现的动作锚点
    best_pos = len(person_block)
    best_anchor = ""
    for anchor in ACTION_ANCHORS:
        pos = person_block.find(anchor)
        if pos != -1 and pos < best_pos:
            best_pos = pos
            best_anchor = anchor

    if best_pos >= len(person_block):
        return ""

    action_raw = person_block[best_pos:].rstrip("，,、 ")

    # 截断过长的结果（>50字），在逗号处断开
    if len(action_raw) > 50:
        parts = re.split(r'[，,]', action_raw)
        truncated = []
        length = 0
        for part in parts:
            if length + len(part) > 50:
                break
            truncated.append(part)
            length += len(part)
        action_raw = "，".join(truncated) if truncated else action_raw[:50]

    # 补上人物标识：如果动作不是以人物名开头，则前��
    if person_label and not action_raw.startswith(person_label):
        action_raw = person_label + action_raw

    return action_raw


def _extract_background_motion(prompt_cn: str) -> str:
    """从 prompt_cn 中提取背景可动元素的微动态。

    策略：扫描常见可动元素关键词，生成"物品名+运动方式"格式的微动态描述。
    规则：同类元素只取一个，避免语义重复（如"烛光"和"蜡烛"只取其一）。
    """
    motions = []
    used_groups: set[str] = set()  # 防止同组重复

    # 可动元素 → (语义组, 微动态描述)
    MOVABLE_ELEMENTS: list[tuple[str, str, str]] = [
        # (关键词, 语义组, 动态描述)
        ("壁炉", "火", "壁炉火焰轻轻跳动"),
        ("烛光", "火", "烛光微微跳动"),
        ("蜡烛", "火", "蜡烛火焰轻轻摇曳"),
        ("火焰", "火", "火焰光影轻轻跳动"),
        ("串灯", "灯", "串灯光晕微微闪烁"),
        ("灯串", "灯", "灯串光点微微闪烁"),
        ("窗帘", "窗帘", "窗帘随风微微摆动"),
        ("热可可", "热气", "热可可杯口热气缓缓升腾"),
        ("热气", "热气", "杯中热气缓缓升腾"),
        ("蒸汽", "热气", "蒸汽缓缓升腾"),
        ("丝带", "带", "丝带轻轻飘动"),
        ("缎带", "带", "缎带末端轻轻飘动"),
        ("松枝", "植物", "松枝轻微摇动"),
        ("飘雪", "雪", "窗外雪花缓缓飘落"),
        ("雪花", "雪", "雪花缓缓飘落"),
    ]

    for keyword, group, motion_desc in MOVABLE_ELEMENTS:
        if keyword in prompt_cn and group not in used_groups:
            motions.append(motion_desc)
            used_groups.add(group)
            if len(motions) >= 2:  # 最多2个背景微动，避免关键词堆叠
                break

    return "，".join(motions)


def _compose_motion_prompt(camera: str, subject: str, background: str) -> str:
    """将三层运动合并为一句完整的视频运动提示词。

    格式："{camera_motion}，{subject_motion}，{background_motion}"
    - 无主体运动时跳过
    - 无背景运动时跳过
    - 每段都以具体元素名称开头（可灵规则："物品名+运动方式"）
    """
    parts = [camera]
    if subject:
        parts.append(subject)
    if background:
        parts.append(background)
    return "，".join(parts)


def plan_storyboard_motions(
    storyboard_data: dict,
    model_name: str = "kling_2.5_turbo",
) -> list[dict]:
    """为整个分镜脚本的 15 个镜头批量规划运镜并合并为完整提示词。

    运镜选择逻辑（场景感知）：
    1. 分析每个镜头的 prompt_cn，判断动态等级 (high/medium/low)
    2. 根据 (动态等级 × 景别) 从候选池中选择运镜
       - HIGH（人物大动作）→ FIXED 优先，镜头不动让人物撑画面
       - MEDIUM（人物小动作）→ 多样运镜
       - LOW（纯静物）→ 统一 ORBIT 缓慢环绕
    3. 同类镜头轮换 + 相邻去重

    Args:
        storyboard_data: Skill 1 输出的分镜 JSON
        model_name: 视频生成模型名称

    Returns:
        列表，每项包含 shot_id, shot_type, motion_prompt, model
    """
    type_counters: dict[str, int] = {}
    results = []
    prev_motion: MotionType | None = None

    for sg in storyboard_data.get("scene_groups", []):
        for shot in sg.get("shots", []):
            shot_type = shot.get("type", "Medium")
            prompt_cn = shot.get("prompt_cn", "")
            idx = type_counters.get(shot_type, 0)
            type_counters[shot_type] = idx + 1

            motion_result = plan_motion(
                shot_type=shot_type,
                dynamics=_analyze_scene_dynamics(prompt_cn),
                shot_index=idx,
                model_name=model_name,
                prev_motion=prev_motion,
            )

            # 从 prompt_cn 提取主体运动和背景运动
            subject = _extract_subject_motion(prompt_cn, shot_type)
            background = _extract_background_motion(prompt_cn)

            # 合并三层为一句完整提示词
            motion_prompt = _compose_motion_prompt(
                motion_result["camera_motion"], subject, background
            )

            results.append({
                "shot_id": shot.get("shot_id"),
                "shot_type": shot_type,
                "motion_prompt": motion_prompt,
                "model": motion_result["model"],
            })

            # 记录当前运镜，供下一个镜头去重
            prev_motion = motion_result["_motion_type"]

    return results
