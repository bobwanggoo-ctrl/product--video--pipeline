"""Skill 5 Module A: 字体库扫描。

扫描 input/fonts/ 目录，从中筛选出适合产品视频字幕的字体，
按分类标注后返回 FontInfo 列表供 LLM 选择。

设计逻辑：
1. 内置"推荐字体表"（经过人工审核，标注了分类和适用场景）
2. 扫描字体目录，与推荐表匹配
3. 未匹配的字体做自动发现（标注为 extra，优先级低）
4. 返回 matched + extra 列表
"""

import logging
from pathlib import Path

from models.timeline import FontInfo

logger = logging.getLogger(__name__)

# ── 推荐字体表 ──────────────────────────────────────────────
# key = 字体文件名（需与 input/fonts/ 中的文件名完全匹配）
# 按产品视频字幕的实际需求精选，分六大类：
#   sans-serif:  无衬线，现代简洁 → 标题、卖点均可
#   serif:       衬线体，优雅高端 → 时尚/奢侈品标题
#   display:     展示型，粗壮醒目 → 标题、号召性文案
#   handwriting: 手写体，个性温暖 → 创意标题
#   cjk-sans:    中文黑体/圆体    → 中文标题和卖点
#   cjk-art:     中文艺术书法体   → 中文创意标题

RECOMMENDED_FONTS: dict[str, dict] = {
    # ── sans-serif（无衬线）──
    "HelveticaNeue.ttc": {
        "name": "Helvetica Neue",
        "family": "Helvetica Neue",
        "category": "sans-serif",
        "tags": ["modern", "clean", "versatile", "professional"],
        "description": "万能无衬线体，适合所有产品类型的标题和卖点字幕",
    },
    "Avenir Next.ttc": {
        "name": "Avenir Next",
        "family": "Avenir Next",
        "category": "sans-serif",
        "tags": ["modern", "geometric", "elegant"],
        "description": "几何无衬线体，兼具现代感和优雅，适合科技/生活方式产品",
    },
    "Futura.ttc": {
        "name": "Futura",
        "family": "Futura",
        "category": "sans-serif",
        "tags": ["geometric", "bold", "fashion"],
        "description": "经典几何体，时尚品牌常用（Supreme/Louis Vuitton），适合潮流产品标题",
    },
    "GillSans.ttc": {
        "name": "Gill Sans",
        "family": "Gill Sans",
        "category": "sans-serif",
        "tags": ["humanist", "warm", "british"],
        "description": "人文无衬线体，温暖友好，适合生活/食品/护肤产品",
    },
    "Optima.ttc": {
        "name": "Optima",
        "family": "Optima",
        "category": "sans-serif",
        "tags": ["elegant", "calligraphic", "luxury"],
        "description": "优雅无衬线体，笔画有粗细变化，适合高端护肤/珠宝产品",
    },
    "Seravek.ttc": {
        "name": "Seravek",
        "family": "Seravek",
        "category": "sans-serif",
        "tags": ["modern", "clean", "readable"],
        "description": "清晰易读的无衬线体，适合卖点说明文字",
    },
    "PTSans.ttc": {
        "name": "PT Sans",
        "family": "PT Sans",
        "category": "sans-serif",
        "tags": ["neutral", "readable", "wide"],
        "description": "中性无衬线体，字距宽松易读，适合信息密集的卖点字幕",
    },

    # ── display（展示型）──
    "DIN Alternate Bold.ttf": {
        "name": "DIN Alternate Bold",
        "family": "DIN Alternate",
        "style": "Bold",
        "category": "display",
        "tags": ["bold", "industrial", "specs", "numbers"],
        "description": "工业风粗体，数字表现力极强，适合科技/运动产品的规格数据",
    },
    "DIN Condensed Bold.ttf": {
        "name": "DIN Condensed Bold",
        "family": "DIN Condensed",
        "style": "Bold",
        "category": "display",
        "tags": ["condensed", "bold", "compact"],
        "description": "窄版粗体，适合空间有限时的标题或数据展示",
    },
    "Impact.ttf": {
        "name": "Impact",
        "family": "Impact",
        "category": "display",
        "tags": ["bold", "heavy", "attention"],
        "description": "超粗标题体，视觉冲击力强，适合促销/号召行动文案",
    },
    "Copperplate.ttc": {
        "name": "Copperplate",
        "family": "Copperplate",
        "category": "display",
        "tags": ["small-caps", "elegant", "luxury"],
        "description": "铜版体全大写，端庄高级，适合奢侈品/酒类品牌标题",
    },
    "Phosphate.ttc": {
        "name": "Phosphate",
        "family": "Phosphate",
        "category": "display",
        "tags": ["inline", "retro", "bold"],
        "description": "内嵌线条装饰体，复古运动感，适合运动/户外产品标题",
    },
    "Rockwell.ttc": {
        "name": "Rockwell",
        "family": "Rockwell",
        "category": "display",
        "tags": ["slab-serif", "bold", "strong"],
        "description": "粗衬线体，稳重有力，适合工具/户外/男性产品标题",
    },

    # ── serif（衬线体）──
    "Didot.ttc": {
        "name": "Didot",
        "family": "Didot",
        "category": "serif",
        "tags": ["elegant", "fashion", "luxury", "contrast"],
        "description": "高对比衬线体，Vogue/Harper's Bazaar 风格，适合时尚/美妆产品",
    },
    "Bodoni 72.ttc": {
        "name": "Bodoni 72",
        "family": "Bodoni 72",
        "category": "serif",
        "tags": ["elegant", "classic", "luxury"],
        "description": "经典意大利衬线体，Armani 风格，适合高端时装/饰品",
    },
    "Baskerville.ttc": {
        "name": "Baskerville",
        "family": "Baskerville",
        "category": "serif",
        "tags": ["classic", "trustworthy", "readable"],
        "description": "传统衬线体，传递信任感，适合健康/金融/教育类产品",
    },
    "Georgia Bold.ttf": {
        "name": "Georgia Bold",
        "family": "Georgia",
        "style": "Bold",
        "category": "serif",
        "tags": ["screen", "readable", "warm"],
        "description": "屏幕优化衬线体，小尺寸仍清晰，适合手机端产品视频卖点字幕",
    },
    "Palatino.ttc": {
        "name": "Palatino",
        "family": "Palatino",
        "category": "serif",
        "tags": ["humanist", "book", "warm"],
        "description": "人文衬线体，温暖书卷气，适合文化/手工/有机产品",
    },

    # ── handwriting（手写/艺术英文）──
    "SignPainter.ttc": {
        "name": "SignPainter",
        "family": "SignPainter-HouseScript",
        "category": "handwriting",
        "tags": ["script", "casual", "retro"],
        "description": "招牌画师风格，复古休闲，适合餐饮/手工/咖啡产品标题",
    },
    "Bradley Hand Bold.ttf": {
        "name": "Bradley Hand Bold",
        "family": "Bradley Hand",
        "style": "Bold",
        "category": "handwriting",
        "tags": ["casual", "friendly", "warm"],
        "description": "手写粗体，亲切温暖，适合儿童/宠物/家居产品",
    },
    "Noteworthy.ttc": {
        "name": "Noteworthy",
        "family": "Noteworthy",
        "category": "handwriting",
        "tags": ["notebook", "casual", "cute"],
        "description": "笔记风手写体，轻松可爱，适合文具/创意/生活小物",
    },

    # ── cjk-sans（中文黑体/圆体）──
    "ヒラギノ角ゴシック W6.ttc": {
        "name": "Hiragino Sans W6",
        "family": "Hiragino Sans",
        "style": "W6",
        "category": "cjk-sans",
        "has_cjk": True,
        "tags": ["modern", "clean", "bold", "chinese", "japanese"],
        "description": "冬青黑体中粗，中日文通用的现代黑体，适合所有产品类型的中文标题",
    },
    "ヒラギノ角ゴシック W3.ttc": {
        "name": "Hiragino Sans W3",
        "family": "Hiragino Sans",
        "style": "W3",
        "category": "cjk-sans",
        "has_cjk": True,
        "tags": ["modern", "clean", "light", "chinese", "japanese"],
        "description": "冬青黑体细体，适合中文卖点说明字幕",
    },
    "STHeiti Medium.ttc": {
        "name": "华文黑体 Medium",
        "family": "Heiti TC",
        "style": "Medium",
        "category": "cjk-sans",
        "has_cjk": True,
        "tags": ["neutral", "system", "readable", "chinese"],
        "description": "系统默认中文黑体，稳定可靠，适合通用中文字幕",
    },
    "AppleSDGothicNeo.ttc": {
        "name": "Apple SD Gothic Neo",
        "family": "Apple SD Gothic Neo",
        "category": "cjk-sans",
        "has_cjk": True,
        "tags": ["modern", "korean", "clean", "chinese"],
        "description": "苹果圆黑体，现代圆润，适合年轻/潮流产品中文字幕",
    },
    "Hiragino Sans GB.ttc": {
        "name": "冬青黑体简",
        "family": "Hiragino Sans GB",
        "style": "W3",
        "category": "cjk-sans",
        "has_cjk": True,
        "tags": ["modern", "clean", "chinese", "simplified"],
        "description": "简体中文专用冬青黑体，清晰锐利，适合中文卖点字幕",
    },
    "Songti.ttc": {
        "name": "宋体 SC",
        "family": "Songti SC",
        "category": "cjk-sans",
        "has_cjk": True,
        "tags": ["serif", "traditional", "chinese", "formal"],
        "description": "中文宋体，正式典雅，适合文化/传统/茶酒类产品",
    },
    "站酷庆科黄油体.ttf": {
        "name": "站酷庆科黄油体",
        "family": "zcoolqingkehuangyouti",
        "category": "cjk-sans",
        "has_cjk": True,
        "tags": ["rounded", "cute", "bold", "chinese", "pop"],
        "description": "圆润可爱粗体，适合食品/母婴/潮玩产品中文标题",
    },

    # ── cjk-art（中文艺术/书法）──
    "安景臣毛笔行书.ttf": {
        "name": "安景臣毛笔行书",
        "family": "AnJingChenMaoBiXingShu",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["calligraphy", "brush", "chinese", "traditional"],
        "description": "毛笔行书，传统国风，适合茶叶/白酒/书法/国潮产品标题",
    },
    "清刻本琴韵楷体.ttf": {
        "name": "清刻本琴韵楷体",
        "family": "QingKeBenQinYunKaiTi",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["kai", "elegant", "chinese", "classical"],
        "description": "古典楷体，文雅端正，适合文房/香薰/中式家居产品",
    },
    "字体圈欣意吉祥宋.ttf": {
        "name": "欣意吉祥宋",
        "family": "Fontquan-XinYiJiXiangSong",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["song", "decorative", "chinese", "festive"],
        "description": "装饰宋体，喜庆吉祥，适合节日/礼品/年货产品",
    },
    "杨任东竹石体-Medium.ttf": {
        "name": "杨任东竹石体",
        "family": "YRDZST",
        "style": "Medium",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["stone", "natural", "chinese", "artistic"],
        "description": "竹石体，自然质朴，适合茶具/陶瓷/手工艺品产品",
    },
    "字魂270号-龙门手书.ttf": {
        "name": "字魂龙门手书",
        "family": "zihun270hao-longmenshoushu",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["brush", "powerful", "chinese", "calligraphy"],
        "description": "龙门手书，气势磅礴，适合白酒/武术/国潮产品标题",
    },
    "字魂55号-龙吟手书.ttf": {
        "name": "字魂龙吟手书",
        "family": "zihun55hao-longyinshoushu",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["brush", "flowing", "chinese", "calligraphy"],
        "description": "龙吟手书，飘逸灵动，适合茶叶/中医/传统文化产品",
    },
    "字酷堂清楷体.ttf": {
        "name": "字酷堂清楷体",
        "family": "zktqkt",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["kai", "clean", "chinese", "classical"],
        "description": "清楷体，端正隽秀，适合教育/文房/传统食品产品",
    },
    "书体坊兰亭体.ttf": {
        "name": "书体坊兰亭体",
        "family": "SCFwxz",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["calligraphy", "elegant", "chinese", "classical"],
        "description": "兰亭体，书法经典，适合高端文化/收藏品产品标题",
    },
    "上首疾风书法体.ttf": {
        "name": "上首疾风书法体",
        "family": "SSJiFengShuFaTi",
        "category": "cjk-art",
        "has_cjk": True,
        "tags": ["brush", "dynamic", "chinese", "bold"],
        "description": "疾风书法体，刚劲有力，适合运动/电竞/潮牌产品中文标题",
    },
}


def scan_font_library(font_dir: str) -> list[FontInfo]:
    """扫描字体目录，返回推荐字体列表。

    只返回推荐表中存在且目录中实际有文件的字体。
    推荐表之外的字体不返回——避免给 LLM 过多无用选项。

    Args:
        font_dir: 字体目录路径（如 input/fonts/）。

    Returns:
        list[FontInfo]，按分类排序。
    """
    root = Path(font_dir)
    if not root.exists():
        logger.warning(f"字体目录不存在: {font_dir}")
        return []

    # 扫描目录中的所有字体文件
    existing_files = {f.name for f in root.iterdir() if f.suffix.lower() in _FONT_EXTENSIONS}

    results: list[FontInfo] = []
    matched = 0

    for filename, meta in RECOMMENDED_FONTS.items():
        if filename not in existing_files:
            continue

        matched += 1
        font_path = str(root / filename)
        results.append(FontInfo(
            name=meta["name"],
            family=meta.get("family", meta["name"]),
            style=meta.get("style", "Regular"),
            path=font_path,
            category=meta.get("category", "sans-serif"),
            has_cjk=meta.get("has_cjk", False),
            tags=meta.get("tags", []),
            description=meta.get("description", ""),
        ))

    # 按分类排序：sans-serif → display → serif → handwriting → cjk-sans → cjk-art
    category_order = {
        "sans-serif": 0, "display": 1, "serif": 2,
        "handwriting": 3, "cjk-sans": 4, "cjk-art": 5,
    }
    results.sort(key=lambda f: (category_order.get(f.category, 99), f.name))

    logger.info(f"字体库扫描完成: {matched}/{len(RECOMMENDED_FONTS)} 个推荐字体匹配")
    for font in results:
        cjk_mark = "中英" if font.has_cjk else "英文"
        logger.info(f"  [{font.category}] {font.name} ({cjk_mark}) — {font.description}")

    return results


def format_font_list_for_llm(fonts: list[FontInfo]) -> str:
    """将字体列表格式化为 LLM 可读的文本。

    在 LLM 剪辑决策 prompt 中嵌入，让 LLM 从中选择字体。
    """
    if not fonts:
        return "（无可用字体库，使用默认 Helvetica）"

    lines = ["可选字体库（按分类排列）："]
    current_category = ""
    category_names = {
        "sans-serif": "无衬线体 Sans-Serif",
        "display": "展示型 Display",
        "serif": "衬线体 Serif",
        "handwriting": "手写体 Handwriting",
        "cjk-sans": "中文黑体 CJK Sans",
        "cjk-art": "中文艺术体 CJK Art",
    }

    for font in fonts:
        if font.category != current_category:
            current_category = font.category
            cat_name = category_names.get(current_category, current_category)
            lines.append(f"\n── {cat_name} ──")

        cjk_mark = " [中英]" if font.has_cjk else ""
        lines.append(f"  • {font.name}{cjk_mark} — {font.description}")

    return "\n".join(lines)


_FONT_EXTENSIONS = {".ttf", ".otf", ".ttc"}
