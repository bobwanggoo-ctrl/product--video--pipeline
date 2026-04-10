"""FCP Title 模板扫描器。

扫描 input/fcp_titles/ 中的 .moti 模板，
提供安装到 FCP Motion Templates 目录和 FCPXML 引用的能力。

FCP Motion Templates 标准安装路径：
  ~/Movies/Motion Templates.localized/Titles.localized/<Category>/<Name>/<Name>.moti

FCPXML 引用方式：
  <effect id="rN" name="<Name>" uid="<.moti 安装路径>"/>
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# FCP Motion Templates 标准目录
FCP_TITLES_DIR = Path.home() / "Movies" / "Motion Templates.localized" / "Titles.localized"

# 模板用途映射：subtitle_style → 模板包偏好
STYLE_CATEGORY_MAP = {
    "title": "Social Media Titles",                          # 标题用 Social Media 动态模板
    "selling_point": "084 SDMAC－PROTMK 炫酷文字遮罩动画",   # 卖点字幕用黄色遮罩动画
}

# Social Media Titles 各模板配置（来源：FCP 导出参照 XML）
# position: adjust-transform position 值（百分比坐标，相对画布中心）
# scale:    adjust-transform scale 值
# max_lines: 固定为 1 — 文案提炼后只有一行，避免模版默认文字出现在画面中
# title_ok: 适合作为标题使用  subtitle_ok: 适合作为字幕单行使用
SOCIAL_MEDIA_TITLES_CONFIG: dict[str, dict] = {
    "Scene 01": {
        "max_lines": 1, "font_size": 55, "alignment": "center",
        "scale": "1.5 1.5", "position": None,
        "title_ok": True, "subtitle_ok": False,
    },
    "Scene 02": {
        "max_lines": 1, "font_size": 60, "alignment": "center",
        "scale": "1.4 1.4", "position": "0 5.55556",
        "title_ok": True, "subtitle_ok": False,
    },
    "Scene 03": {
        "max_lines": 1, "font_size": 60, "alignment": "right",
        "scale": None, "position": None,
        "title_ok": True, "subtitle_ok": False,
    },
    "Scene 04": {
        "max_lines": 1, "font_size": 55, "alignment": "center",
        "scale": "1.7 1.7", "position": None,
        "title_ok": True, "subtitle_ok": False,
    },
    "Scene 05": {
        "max_lines": 1, "font_size": 57, "alignment": "center",
        "scale": "1.8 1.8", "position": None,
        "title_ok": True, "subtitle_ok": False,
    },
    "Scene 06": {
        "max_lines": 1, "font_size": 58, "alignment": "left",
        "scale": "1.36 1.36", "position": "15 11.7593",
        "title_ok": True, "subtitle_ok": False,
    },
    "Scene 08": {
        "max_lines": 1, "font_size": 60, "alignment": "left",
        "scale": "1.27 1.27", "position": "8.33333 -11.1111",
        "title_ok": True, "subtitle_ok": False,
    },
    "Scene 09": {
        "max_lines": 1, "font_size": 65, "alignment": "center",
        "scale": None, "position": "47.2222 -39.2828",
        "title_ok": False, "subtitle_ok": True,
    },
    "Scene 10": {
        "max_lines": 1, "font_size": 60, "alignment": "center",
        "scale": "1.91 1.91", "position": None,
        "title_ok": True, "subtitle_ok": False,
    },
    "Scene 12": {
        "max_lines": 1, "font_size": 60, "alignment": "center",
        "scale": "1.37 1.37", "position": None,
        "title_ok": True, "subtitle_ok": False,
    },
}


@dataclass
class TitleTemplate:
    """单个 FCP Title 模板。"""
    name: str                    # 模板名称（如 "Scene 02"）
    category: str                # 所属包名（如 "Social Media Titles"）
    moti_path: Path              # .moti 文件的源路径
    preview_path: Path | None    # 预览图路径（large.png）
    sub_path: str = ""           # category 内的子路径（如 "In/Out Text/100"）
    installed_path: Path | None = None  # 安装后的路径


@dataclass
class TitleTemplateLibrary:
    """模板库。"""
    templates: list[TitleTemplate] = field(default_factory=list)
    categories: dict[str, list[TitleTemplate]] = field(default_factory=dict)
    installed: bool = False


def scan_templates(templates_dir: str = "") -> TitleTemplateLibrary:
    """扫描 FCP Title 模板目录。

    Args:
        templates_dir: 模板目录路径，默认 input/fcp_titles/

    Returns:
        TitleTemplateLibrary 包含所有扫描到的模板。
    """
    if not templates_dir:
        from config import settings
        templates_dir = str(settings.FCP_TITLES_DIR)

    lib = TitleTemplateLibrary()
    base = Path(templates_dir)

    if not base.exists():
        logger.warning(f"[TitleScanner] 模板目录不存在: {templates_dir}")
        return lib

    # 递归查找所有 .moti 文件
    moti_files = sorted(base.rglob("*.moti"))
    if not moti_files:
        logger.warning(f"[TitleScanner] 未找到 .moti 模板: {templates_dir}")
        return lib

    for moti in moti_files:
        name = moti.stem
        parent = moti.parent
        category = _infer_category(moti, base)

        # 计算 category 内的子路径（保留完整层级）
        # 如 base/third_party/084.../In/Out Text/100/100 C.moti
        # category = "084..."  → sub_path = "In/Out Text/100"
        sub_path = ""
        try:
            rel = moti.relative_to(base)
            parts = list(rel.parts)
            # 跳过 "third_party" 和 category 本身
            if parts and parts[0] == "third_party":
                parts = parts[1:]
            if parts and parts[0] == category:
                parts = parts[1:]
            # 去掉文件名，剩余的就是子路径
            if parts:
                parts = parts[:-1]  # remove filename
            sub_path = str(Path(*parts)) if parts else ""
        except ValueError:
            pass

        preview = None
        for img_name in ("large.png", "small.png"):
            candidate = parent / img_name
            if candidate.exists():
                preview = candidate
                break

        template = TitleTemplate(
            name=name,
            category=category,
            moti_path=moti,
            preview_path=preview,
            sub_path=sub_path,
        )
        lib.templates.append(template)
        lib.categories.setdefault(category, []).append(template)

    logger.info(
        f"[TitleScanner] 扫描到 {len(lib.templates)} 个模板, "
        f"{len(lib.categories)} 个分类: {list(lib.categories.keys())}"
    )
    return lib


def install_templates(lib: TitleTemplateLibrary) -> int:
    """将模板安装到 FCP Motion Templates 目录。

    Returns:
        成功安装的模板数量。
    """
    if not lib.templates:
        return 0

    installed_count = 0
    FCP_TITLES_DIR.mkdir(parents=True, exist_ok=True)

    for template in lib.templates:
        try:
            # 安装路径保留完整层级:
            # ~/Movies/Motion Templates.localized/Titles.localized/<Category>/<sub_path>/
            if template.sub_path:
                dest_dir = FCP_TITLES_DIR / template.category / template.sub_path
            else:
                dest_dir = FCP_TITLES_DIR / template.category / template.name
            dest_moti = dest_dir / template.moti_path.name

            if dest_moti.exists():
                template.installed_path = dest_moti
                installed_count += 1
                continue

            # 复制整个模板目录（含 .moti + .mov + preview）
            src_dir = template.moti_path.parent
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(src_dir, dest_dir)
            template.installed_path = dest_moti

            installed_count += 1
            logger.debug(f"  安装: {template.category}/{template.name}")

        except Exception as e:
            logger.warning(f"  安装失败 {template.name}: {e}")

    lib.installed = True
    logger.info(f"[TitleScanner] 安装完成: {installed_count}/{len(lib.templates)} 个模板")
    return installed_count


def get_template_for_style(
    lib: TitleTemplateLibrary,
    style: str,
    index: int = 0,
) -> TitleTemplate | None:
    """根据字幕样式选择一个模板。

    Args:
        lib: 模板库。
        style: "title" 或 "selling_point"。
        index: 用于在同一类模板中轮换（避免所有字幕用同一个模板）。

    Returns:
        TitleTemplate 或 None。
    """
    preferred_category = STYLE_CATEGORY_MAP.get(style, "Social Media Titles")

    candidates = lib.categories.get(preferred_category, [])

    # Social Media Titles：title 只取 title_ok 模板，selling_point 只取 subtitle_ok 模板
    if preferred_category == "Social Media Titles" and candidates:
        if style == "selling_point":
            filtered = [t for t in candidates
                        if SOCIAL_MEDIA_TITLES_CONFIG.get(t.name, {}).get("subtitle_ok")]
        else:
            filtered = [t for t in candidates
                        if SOCIAL_MEDIA_TITLES_CONFIG.get(t.name, {}).get("title_ok")]
        if filtered:
            candidates = filtered

    if not candidates:
        # 降级到任何可用模板
        candidates = lib.templates

    if not candidates:
        return None

    return candidates[index % len(candidates)]


def is_social_media_template(template: TitleTemplate) -> bool:
    """是否为 Social Media Titles 模板（影响 FCPXML 生成方式）。"""
    return template.category == "Social Media Titles"


def get_social_media_config(template: TitleTemplate) -> dict:
    """获取 Social Media 模板配置，不存在时返回空 dict。"""
    return SOCIAL_MEDIA_TITLES_CONFIG.get(template.name, {})


def wrap_text_for_template(text: str, template: TitleTemplate) -> list[str]:
    """将文本按模板行数限制分行。

    Social Media Titles 模板有固定行数上限（max_lines），超出部分会被截断。
    本函数按汉字/英文词边界分割，保证行数 ≤ max_lines。

    Returns:
        list of str，每个元素对应一行 <text>。
    """
    config = get_social_media_config(template)
    max_lines = config.get("max_lines", 2)

    if not text:
        return [""]

    # 按换行符或"；""。"分段（用户可能已手动换行）
    import re
    parts = [p.strip() for p in re.split(r"[\n；。]", text) if p.strip()]

    if len(parts) <= max_lines:
        return parts or [text]

    # 超出行数：把后面的行合并到最后一行
    result = parts[:max_lines - 1]
    result.append(" ".join(parts[max_lines - 1:]))
    return result


def get_fcpxml_uid(template: TitleTemplate) -> str:
    """获取模板的 FCPXML effect uid。

    FCP 通过 .moti 文件的绝对路径来识别自定义模板。
    """
    if template.installed_path:
        return str(template.installed_path)
    return str(template.moti_path)


def _infer_category(moti_path: Path, base_dir: Path) -> str:
    """从 .moti 路径推断分类名。"""
    try:
        rel = moti_path.relative_to(base_dir)
        parts = rel.parts
        # 跳过 "third_party" 前缀
        if parts and parts[0] == "third_party" and len(parts) > 1:
            return parts[1]
        if parts:
            return parts[0]
    except ValueError:
        pass
    return "Custom"
