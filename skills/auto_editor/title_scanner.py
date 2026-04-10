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
    "title": "084 SDMAC－PROTMK 炫酷文字遮罩动画",       # 标题用遮罩动画
    "selling_point": "084 SDMAC－PROTMK 炫酷文字遮罩动画", # 卖点字幕也用遮罩动画
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
    if not candidates:
        # 降级到任何可用模板
        candidates = lib.templates

    if not candidates:
        return None

    return candidates[index % len(candidates)]


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
