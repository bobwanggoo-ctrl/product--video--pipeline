"""Google Vision API 侵权检测模块。

基于 Google Cloud Vision API 做三层侵权检测：
- LOGO_DETECTION: 品牌 Logo 识别
- WEB_DETECTION: 反向搜图查素材库来源
- LABEL_DETECTION: IP 形象标签识别

从 lens-batch.py 提取核心逻辑，适配为 Skill 3 的子模块。
"""

import base64
import io
import json
import logging
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ===== 常量 =====

STOCK_DOMAINS = [
    "shutterstock.com", "gettyimages.com", "istockphoto.com", "stock.adobe.com",
    "adobestock.com", "123rf.com", "dreamstime.com", "alamy.com", "depositphotos.com",
    "bigstockphoto.com", "pond5.com", "veer.com", "vcg.com", "hellorf.com",
    "tuchong.com", "huitu.com", "nipic.com", "photoshelter.com",
    "quanjing.com", "699pic.com", "58pic.com", "lovepik.com",
]

IP_LABELS = [
    "cartoon", "animated cartoon", "fictional character", "anime", "animation",
    "comic", "manga", "mascot", "toy", "action figure", "superhero",
]

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMG_BYTES = 4 * 1024 * 1024
BATCH_SIZE = 16  # Vision API 单次最多 16 张

# Full 模式检测 features
FULL_FEATURES = [
    {"type": "LOGO_DETECTION", "maxResults": 10},
    {"type": "WEB_DETECTION", "maxResults": 10},
    {"type": "LABEL_DETECTION", "maxResults": 15},
]


# ===== 数据结构 =====

@dataclass
class CopyrightRisk:
    """单张帧图的侵权风险评估。"""
    risk: str = "low"  # "high" | "medium" | "low" | "unknown"
    reasons: list[str] = field(default_factory=list)
    logos: list[str] = field(default_factory=list)
    stock_hits: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    ip_hits: list[str] = field(default_factory=list)


# ===== 图片压缩 =====

def _compress_for_vision(path: str) -> str:
    """读取图片并返回 base64，大图自动压缩到 4MB 以下。"""
    file_size = os.path.getsize(path)
    if file_size <= MAX_IMG_BYTES:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    try:
        from PIL import Image
        img = Image.open(path)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        w, h = img.size
        scale = min(2048 / max(w, h), 1.0)
        if scale < 1:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        quality = 85
        while quality >= 30:
            buf.seek(0)
            buf.truncate()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= MAX_IMG_BYTES:
                break
            quality -= 10
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()


# ===== Vision API 调用 =====

def _call_vision_batch(images_b64: list[str], api_key: str) -> dict:
    """批量调用 Google Vision API。"""
    endpoint = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
    requests_list = [
        {"image": {"content": b64}, "features": FULL_FEATURES}
        for b64 in images_b64
    ]
    payload = json.dumps({"requests": requests_list}).encode()
    req = urllib.request.Request(
        endpoint, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"HTTP {e.code}: {body[:300]}"}
    except Exception as e:
        return {"error": str(e)}


# ===== 风险评估 =====

def _assess_risk(response: dict) -> CopyrightRisk:
    """从 Vision API 单张图响应中评估侵权风险。"""
    result = CopyrightRisk()
    risk = "low"

    # Logo 检测
    logos = response.get("logoAnnotations", [])
    if logos:
        logo_names = [l["description"] for l in logos]
        result.logos = logo_names
        detail = ", ".join(f"{l['description']}({l.get('score', 0):.0%})" for l in logos)
        result.reasons.append(f"品牌Logo: {detail}")
        risk = "high"

    # Web 检测
    web = response.get("webDetection", {})
    all_urls = []
    for key in ("fullMatchingImages", "partialMatchingImages", "pagesWithMatchingImages"):
        for item in web.get(key, []):
            all_urls.append(item.get("url", ""))

    stock_hits = set()
    for url in all_urls:
        url_lower = url.lower()
        for domain in STOCK_DOMAINS:
            if domain in url_lower:
                stock_hits.add(domain)

    if stock_hits:
        result.stock_hits = sorted(stock_hits)
        result.reasons.append(f"素材库来源: {', '.join(result.stock_hits)}")
        risk = "high"

    full_matches = web.get("fullMatchingImages", [])
    domains = set()
    for url in all_urls:
        try:
            d = urllib.parse.urlparse(url).netloc.replace("www.", "")
            if d:
                domains.add(d)
        except Exception:
            pass

    if len(full_matches) >= 3 and risk != "high":
        risk = "medium"
        result.reasons.append(f"{len(full_matches)} 个完全匹配")
    if len(domains) >= 5 and risk != "high":
        risk = "medium"
        result.reasons.append(f"被 {len(domains)} 个网站使用")

    # 标签检测（IP 形象）
    labels = response.get("labelAnnotations", [])
    result.labels = [l["description"] for l in labels[:10]]
    ip_hits = []
    for label in labels:
        desc = label.get("description", "").lower()
        for ip_label in IP_LABELS:
            if ip_label in desc:
                ip_hits.append(f"{label['description']}({label.get('score', 0):.0%})")
                break
    if ip_hits:
        result.ip_hits = ip_hits
        result.reasons.append(f"疑似IP形象: {', '.join(ip_hits)}")
        if risk == "low":
            risk = "medium"

    if not result.reasons:
        result.reasons.append("未发现明显侵权风险")

    result.risk = risk
    return result


# ===== 公开接口 =====

def check_copyright_batch(frame_paths: dict[int, str]) -> dict[int, CopyrightRisk]:
    """批量检测所有帧图的侵权风险。

    Args:
        frame_paths: {shot_id: frame_file_path}

    Returns:
        {shot_id: CopyrightRisk}
    """
    from config import settings

    api_key = settings.GOOGLE_VISION_API_KEY
    if not api_key:
        logger.warning("[Copyright] GOOGLE_VISION_API_KEY 未配置，跳过侵权检测")
        return {}

    if not frame_paths:
        return {}

    # 按 shot_id 排序，保持确定性
    ordered = sorted(frame_paths.items())
    shot_ids = [sid for sid, _ in ordered]
    paths = [p for _, p in ordered]

    logger.info(f"[Copyright] 开始侵权检测: {len(paths)} 张帧图")

    # 压缩图片
    b64_list = []
    failed_indices = set()
    for i, path in enumerate(paths):
        try:
            b64_list.append(_compress_for_vision(path))
        except Exception as e:
            logger.warning(f"[Copyright] 图片压缩失败 shot_{shot_ids[i]:02d}: {e}")
            b64_list.append(None)
            failed_indices.add(i)

    # 过滤掉失败的
    valid_b64 = [b for b in b64_list if b is not None]
    valid_indices = [i for i in range(len(b64_list)) if i not in failed_indices]

    if not valid_b64:
        logger.warning("[Copyright] 所有图片压缩失败，跳过侵权检测")
        return {}

    # 分批调用 Vision API（每批最多 16 张）
    results: dict[int, CopyrightRisk] = {}
    for batch_start in range(0, len(valid_b64), BATCH_SIZE):
        batch_b64 = valid_b64[batch_start:batch_start + BATCH_SIZE]
        batch_indices = valid_indices[batch_start:batch_start + BATCH_SIZE]

        resp = _call_vision_batch(batch_b64, api_key)

        if "error" in resp:
            logger.warning(f"[Copyright] Vision API 错误: {resp['error']}")
            for idx in batch_indices:
                results[shot_ids[idx]] = CopyrightRisk(
                    risk="unknown",
                    reasons=[f"API错误: {str(resp['error'])[:100]}"],
                )
            continue

        responses = resp.get("responses", [])
        for j, idx in enumerate(batch_indices):
            sid = shot_ids[idx]
            if j < len(responses):
                r = responses[j]
                if "error" in r:
                    results[sid] = CopyrightRisk(
                        risk="unknown",
                        reasons=[f"Vision: {r['error'].get('message', '')[:80]}"],
                    )
                else:
                    results[sid] = _assess_risk(r)
            else:
                results[sid] = CopyrightRisk(risk="unknown", reasons=["无响应"])

            icon = {"high": "red", "medium": "yellow", "low": "green"}.get(
                results[sid].risk, "?"
            )
            logger.info(
                f"  [Copyright] shot_{sid:02d} [{icon}] "
                f"{'; '.join(results[sid].reasons)}"
            )

    # 填充压缩失败的
    for i in failed_indices:
        results[shot_ids[i]] = CopyrightRisk(
            risk="unknown", reasons=["图片压缩失败"]
        )

    # 统计
    high = sum(1 for r in results.values() if r.risk == "high")
    medium = sum(1 for r in results.values() if r.risk == "medium")
    low = sum(1 for r in results.values() if r.risk == "low")
    logger.info(f"[Copyright] 侵权检测完成: high={high} medium={medium} low={low}")

    return results
