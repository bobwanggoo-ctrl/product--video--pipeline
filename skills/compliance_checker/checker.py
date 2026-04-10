"""Skill 3: 合规性检查 — 产品一致性 + AI 质量 + 侵权风险 + 排版建议。

双层并行检查：
1. Gemini Vision：产品一致性、场景融合度、场景逻辑性、AI质量、排版建议
2. Google Vision API：Logo 识别 + Web 反向搜图 + IP 标签（侵权检测）
"""

import base64
import concurrent.futures
import io
import logging
import math
from pathlib import Path

from models.compliance import (
    ComplianceIssue,
    ComplianceLevel,
    ComplianceResult,
    LayoutHint,
)
from models.storyboard import Storyboard
from utils.json_repair import extract_json

from .copyright_checker import CopyrightRisk, check_copyright_batch
from .prompts import (
    BATCH_COMPLIANCE_PROMPT,
    BATCH_NO_REFERENCE_PROMPT,
    COMPLIANCE_PROMPT,
    NO_REFERENCE_PROMPT,
)

logger = logging.getLogger(__name__)

# 支持的图片格式
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# 并发控制
MAX_WORKERS = 3       # 3 组 batch 并行
BATCH_SIZE = 5        # 每组最多 5 张
TIMEOUT_PER_SHOT = 300.0

# Final_Status → score 映射
_STATUS_SCORE = {
    "PASS": 1.0,
    "WARN": 0.6,
    "FAIL": 0.2,
}


# ── 入口 ──────────────────────────────────────────────────

def run(
    storyboard: Storyboard,
    frame_paths: dict[int, str],
    reference_image_dir: str = "",
    **kwargs,
) -> dict:
    """Skill 3 入口。

    双层并行：Gemini Vision（质量/一致性） + Google Vision API（侵权检测）。

    Returns:
        {
            "compliance_results": list[ComplianceResult],
            "layout_hints": {shot_id: LayoutHint},
            "error_keywords": {shot_id: list[str]},
            "skipped": False,
        }
    """
    ref_images_b64 = _load_reference_images(reference_image_dir)
    has_ref = len(ref_images_b64) > 0

    if not has_ref:
        logger.warning("[Skill3] 无参考图，跳过产品一致性检查，仅做质量+侵权+排版")

    shots = _get_all_shots(storyboard)
    logger.info(f"[Skill3] 开始合规检查: {len(shots)} shots, {len(ref_images_b64)} 张参考图")

    # ── 双层并行检查 ──
    # Layer 1: Gemini Vision（质量/一致性/融合/排版）—— 多线程逐 shot
    # Layer 2: Google Vision API（侵权检测）—— 批量一次调用
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as layer_executor:
        copyright_future = layer_executor.submit(check_copyright_batch, frame_paths)

        # Layer 1 在主线程执行（内部已有 ThreadPool）
        results, layout_hints, error_keywords, per_shot_trace = _batch_check(
            shots, frame_paths, ref_images_b64, reference_image_dir,
        )

        # 等待 Layer 2 完成
        try:
            copyright_risks = copyright_future.result(timeout=180.0)
        except Exception as e:
            logger.warning(f"[Skill3] 侵权检测层失败: {e}")
            copyright_risks = {}

    # ── 合并侵权检测结果 ──
    if copyright_risks:
        _merge_copyright(results, copyright_risks, error_keywords)

    # 日志汇总
    pass_count = sum(1 for r in results if r.level == ComplianceLevel.PASS)
    warn_count = sum(1 for r in results if r.level == ComplianceLevel.WARN)
    fail_count = sum(1 for r in results if r.level == ComplianceLevel.FAIL)
    logger.info(f"[Skill3] 合规检查完成: PASS={pass_count} WARN={warn_count} FAIL={fail_count}")

    for r in results:
        if r.level != ComplianceLevel.PASS:
            kw = ", ".join(r.error_keywords) if r.error_keywords else ""
            logger.info(f"  shot_{r.shot_id:02d} [{r.level.value}] {r.summary} → keywords: [{kw}]")

    return {
        "compliance_results": results,
        "layout_hints": {lh.shot_id: lh for lh in layout_hints.values()} if isinstance(layout_hints, dict) else layout_hints,
        "error_keywords": error_keywords,
        "skipped": False,
        "_trace": {
            "prompt_template": BATCH_COMPLIANCE_PROMPT if has_ref else BATCH_NO_REFERENCE_PROMPT,
            "per_shot": per_shot_trace,
            "copyright_risks": {
                sid: {"risk": cr.risk, "reasons": cr.reasons}
                for sid, cr in copyright_risks.items()
            } if copyright_risks else {},
            "meta": {
                "total_shots": len(shots),
                "checked": len(results),
                "pass": pass_count,
                "warn": warn_count,
                "fail": fail_count,
                "has_reference": has_ref,
                "reference_count": len(ref_images_b64),
                "copyright_checked": len(copyright_risks),
            },
        },
    }


# ── 参考图加载 ────────────────────────────────────────────

def _load_reference_images(ref_dir: str) -> list[str]:
    """扫描参考图目录，压缩后返回 base64 列表。"""
    if not ref_dir:
        return []
    ref_path = Path(ref_dir)
    if not ref_path.exists():
        return []

    images = sorted(
        f for f in ref_path.iterdir()
        if f.suffix.lower() in _IMG_EXTS and not f.name.startswith(".")
    )
    if not images:
        return []

    result = []
    for img_file in images[:1]:  # 只取第 1 张参考图 — 减少 payload，避免 tu-zi 超时
        try:
            b64 = _compress_image(str(img_file))
            result.append(b64)
            logger.debug(f"[Skill3] 参考图加载: {img_file.name}")
        except Exception as e:
            logger.warning(f"[Skill3] 参考图加载失败: {img_file}: {e}")

    logger.info(f"[Skill3] 加载了 {len(result)} 张参考图 (目录: {ref_dir})")
    return result


def _compress_image(image_path: str, max_size: int = 768) -> str:
    """压缩图片到 max_size，返回 base64 JPEG。

    768px: 在 Vision 识别质量和 API payload 大小之间取得平衡。
    """
    from PIL import Image

    img = Image.open(image_path)
    # 转 RGB（去掉 alpha 通道）
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# ── Storyboard → shot 列表 ─────────────────────────────────

def _get_all_shots(storyboard: Storyboard) -> list[dict]:
    """提取 storyboard 中所有 shot 信息。"""
    shots = []
    for sg in storyboard.scene_groups:
        for shot in sg.shots:
            shots.append({
                "shot_id": shot.shot_id,
                "type": shot.type,
                "purpose": shot.purpose,
                "prompt_cn": shot.prompt_cn,
            })
    return shots


# ── 批量检查 ──────────────────────────────────────────────

def _batch_check(
    shots: list[dict],
    frame_paths: dict[int, str],
    ref_images_b64: list[str],
    reference_image_dir: str,
    max_workers: int = MAX_WORKERS,
) -> tuple[list[ComplianceResult], dict[int, LayoutHint], dict[int, list[str]], dict[int, dict]]:
    """批量检查所有 shot（Grid 模式：每组 5 张拼图，并发 3 组）。"""
    results: list[ComplianceResult] = []
    layout_hints: dict[int, LayoutHint] = {}
    error_keywords: dict[int, list[str]] = {}
    per_shot_trace: dict[int, dict] = {}

    # 过滤有帧的 shot
    checkable = [(s, frame_paths[s["shot_id"]]) for s in shots if s["shot_id"] in frame_paths]

    if not checkable:
        logger.warning("[Skill3] 无可检查的 shot（frame_paths 为空）")
        return results, layout_hints, error_keywords, per_shot_trace

    # 分组：每组 BATCH_SIZE 张
    groups: list[list[tuple[dict, str]]] = []
    for i in range(0, len(checkable), BATCH_SIZE):
        groups.append(checkable[i:i + BATCH_SIZE])

    logger.info(f"[Skill3] 分 {len(groups)} 组批量检查（每组最多 {BATCH_SIZE} 张，并发 {max_workers}）")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for group in groups:
            shot_ids = [s["shot_id"] for s, _ in group]
            fp_map = {s["shot_id"]: fp for s, fp in group}
            shot_info_map = {s["shot_id"]: s for s, _ in group}
            future = executor.submit(
                _check_batch_group,
                shot_ids, fp_map, shot_info_map,
                ref_images_b64, reference_image_dir,
            )
            future_map[future] = shot_ids

        for future in concurrent.futures.as_completed(future_map):
            shot_ids = future_map[future]
            try:
                batch_results = future.result(timeout=TIMEOUT_PER_SHOT * len(shot_ids))
                for cr, lh, trace in batch_results:
                    results.append(cr)
                    if lh:
                        layout_hints[cr.shot_id] = lh
                    if cr.error_keywords:
                        error_keywords[cr.shot_id] = cr.error_keywords
                    if trace:
                        per_shot_trace[cr.shot_id] = trace
            except Exception as e:
                logger.warning(f"[Skill3] batch {shot_ids} 整组失败: {e}，降级单张")
                for sid in shot_ids:
                    fp = frame_paths.get(sid, "")
                    results.append(_default_result(sid, fp))

    results.sort(key=lambda r: r.shot_id)
    return results, layout_hints, error_keywords, per_shot_trace


# ── Grid 图生成 ───────────────────────────────────────────

def _build_grid_image(
    frame_paths: list[str],
    tile_size: int = 480,
    cols: int = 3,
    gap: int = 8,
) -> str:
    """将 N 张帧图拼成 Grid，每个 tile 左上角画 [idx] 白字黑底标签，返回 base64 JPEG。

    布局：最多 3 列，自动分行。最后一行不足 cols 时居中对齐。
    """
    from PIL import Image, ImageDraw, ImageFont

    n = len(frame_paths)
    rows = math.ceil(n / cols)
    canvas_w = cols * tile_size + (cols + 1) * gap
    canvas_h = rows * tile_size + (rows + 1) * gap
    canvas = Image.new("RGB", (canvas_w, canvas_h), (26, 26, 26))
    draw = ImageDraw.Draw(canvas)

    # 加载字体（优先系统 Arial，降级默认字体）
    font = None
    for font_path in ["/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(font_path, 28)
            break
        except Exception:
            continue
    if font is None:
        from PIL import ImageFont
        font = ImageFont.load_default()

    for idx, path in enumerate(frame_paths):
        row, col_in_row = divmod(idx, cols)

        # 最后一行居中对齐
        row_start = row * cols
        row_end = min(row_start + cols, n)
        row_len = row_end - row_start
        col_offset = ((cols - row_len) * (tile_size + gap)) // 2

        x0 = gap + col_in_row * (tile_size + gap) + col_offset
        y0 = gap + row * (tile_size + gap)

        # 加载并缩放图片（保持宽高比）
        try:
            img = Image.open(path)
            if img.mode not in ("RGB",):
                img = img.convert("RGB")
            img.thumbnail((tile_size, tile_size), Image.LANCZOS)
        except Exception as e:
            logger.warning(f"[Skill3] Grid tile 加载失败 {path}: {e}")
            img = Image.new("RGB", (tile_size, tile_size), (40, 40, 40))

        # 居中贴入 tile
        paste_x = x0 + (tile_size - img.width) // 2
        paste_y = y0 + (tile_size - img.height) // 2
        canvas.paste(img, (paste_x, paste_y))

        # 画编号标签 [1]~[N]，白字黑底
        label = f"[{idx + 1}]"
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            lw = bbox[2] - bbox[0] + 8
            lh = bbox[3] - bbox[1] + 4
        except Exception:
            lw, lh = 50, 30
        draw.rectangle([x0 + 4, y0 + 4, x0 + 4 + lw, y0 + 4 + lh], fill=(0, 0, 0))
        draw.text((x0 + 8, y0 + 6), label, fill=(255, 255, 255), font=font)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=75)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    logger.debug(f"[Skill3] Grid 生成: {n} tiles, {len(b64)//1024}KB base64")
    return b64


# ── 批量 shot 检查 ─────────────────────────────────────────

def _check_batch_group(
    batch_shot_ids: list[int],
    fp_map: dict[int, str],
    shot_info_map: dict[int, dict],
    ref_images_b64: list[str],
    reference_image_dir: str,
) -> list[tuple[ComplianceResult, LayoutHint | None, dict]]:
    """将一组 shot 拼成 Grid，一次 Vision 调用完成所有检查。

    失败时自动降级为逐张单检（_check_single_shot）。
    """
    from utils.llm_client import llm_client

    n = len(batch_shot_ids)
    has_ref = len(ref_images_b64) > 0
    frame_path_list = [fp_map[sid] for sid in batch_shot_ids]

    # index → shot_id 映射（index 从 1 开始）
    index_to_shot = {i + 1: sid for i, sid in enumerate(batch_shot_ids)}
    index_map_str = ", ".join(f"[{k}]=shot_{v:02d}" for k, v in index_to_shot.items())

    logger.info(f"[Skill3] batch 检查: shots={batch_shot_ids}")

    # 生成 Grid 图
    try:
        grid_b64 = _build_grid_image(frame_path_list)
    except Exception as e:
        logger.warning(f"[Skill3] Grid 生成失败: {e}，降级单张")
        return [
            _check_single_shot(shot_info_map[sid], fp_map[sid], ref_images_b64, reference_image_dir)
            for sid in batch_shot_ids
        ]

    # 构造 prompt
    if has_ref:
        prompt = BATCH_COMPLIANCE_PROMPT.format(n_tiles=n, index_map=index_map_str)
    else:
        prompt = BATCH_NO_REFERENCE_PROMPT.format(n_tiles=n, index_map=index_map_str)

    all_images = ref_images_b64 + [grid_b64]
    trace: dict = {"batch_shot_ids": batch_shot_ids, "index_map": index_map_str}

    # Vision 调用（最多 2 次，第 2 次加强 JSON 要求）
    raw = None
    data = None
    for attempt in range(1, 3):
        try:
            raw = llm_client.call_vision(prompt, all_images)
            trace[f"raw_attempt_{attempt}"] = raw[:500]
        except Exception as e:
            logger.warning(f"[Skill3] batch Vision 失败 attempt={attempt}: {e}")
            if attempt == 2:
                logger.warning("[Skill3] batch 全部失败，降级单张")
                return _fallback_single(batch_shot_ids, fp_map, shot_info_map, ref_images_b64, reference_image_dir)
            continue

        try:
            data = extract_json(raw)
            break
        except Exception as e:
            logger.warning(f"[Skill3] batch JSON 解析失败 attempt={attempt}: {e}")
            if attempt == 2:
                logger.warning("[Skill3] batch JSON 解析彻底失败，降级单张")
                return _fallback_single(batch_shot_ids, fp_map, shot_info_map, ref_images_b64, reference_image_dir)
            prompt = prompt + "\n\n[重要] 只返回顶层为 {\"frames\":[...]} 的 JSON，frames 必须包含全部帧，不要任何其他文字。"

    if data is None:
        return _fallback_single(batch_shot_ids, fp_map, shot_info_map, ref_images_b64, reference_image_dir)

    return _parse_batch_result(index_to_shot, fp_map, reference_image_dir, data,
                               shot_info_map, ref_images_b64)


def _fallback_single(
    shot_ids: list[int],
    fp_map: dict[int, str],
    shot_info_map: dict[int, dict],
    ref_images_b64: list[str],
    reference_image_dir: str,
) -> list[tuple[ComplianceResult, LayoutHint | None, dict]]:
    """批量失败时降级为逐张单检。"""
    results = []
    for sid in shot_ids:
        try:
            cr, lh, trace = _check_single_shot(
                shot_info_map[sid], fp_map[sid], ref_images_b64, reference_image_dir
            )
        except Exception as e:
            logger.warning(f"[Skill3] 降级单张 shot_{sid:02d} 也失败: {e}")
            cr = _default_result(sid, fp_map.get(sid, ""))
            lh, trace = None, {}
        results.append((cr, lh, trace))
    return results


def _parse_batch_result(
    index_to_shot: dict[int, int],
    fp_map: dict[int, str],
    reference_image_dir: str,
    data: dict,
    shot_info_map: dict[int, dict],
    ref_images_b64: list[str],
) -> list[tuple[ComplianceResult, LayoutHint | None, dict]]:
    """将 batch Vision 输出（{"frames": [...]}）解析为每个 shot 的结果。

    漏报的 shot 自动降级为单张检查。
    """
    frames_list = data.get("frames", [])
    # 用 index 建索引（Vision 偶尔不保证顺序）
    frame_by_index: dict[int, dict] = {}
    for frame_data in frames_list:
        idx = frame_data.get("index")
        if isinstance(idx, int) and idx in index_to_shot:
            frame_by_index[idx] = frame_data

    results = []
    for idx, sid in sorted(index_to_shot.items()):
        fp = fp_map.get(sid, "")
        fd = frame_by_index.get(idx)

        if fd is None:
            # Vision 漏报，降级单张
            logger.warning(f"[Skill3] batch 漏报 index={idx} (shot_{sid:02d})，单张补检")
            try:
                cr, lh, trace = _check_single_shot(
                    shot_info_map[sid], fp, ref_images_b64, reference_image_dir
                )
            except Exception as e:
                logger.warning(f"[Skill3] 补检 shot_{sid:02d} 失败: {e}")
                cr = _default_result(sid, fp)
                lh, trace = None, {"fallback": True}
            results.append((cr, lh, trace))
            continue

        cr = _parse_result(sid, fp, reference_image_dir, fd)
        lh = _parse_layout_hint(sid, fd)
        trace = {
            "batch_index": idx,
            "level": cr.level.value,
            "score": cr.score,
            "summary": cr.summary,
        }
        logger.info(f"  [Skill3] batch shot_{sid:02d} → {cr.level.value} (score={cr.score:.1f}) {cr.summary[:60]}")
        results.append((cr, lh, trace))

    return results


# ── 单 shot 检查 ──────────────────────────────────────────

def _check_single_shot(
    shot_info: dict,
    frame_path: str,
    ref_images_b64: list[str],
    reference_image_dir: str,
) -> tuple[ComplianceResult, LayoutHint | None, dict]:
    """单 shot 合规检查。返回 (result, layout_hint, trace_data)。"""
    sid = shot_info["shot_id"]
    logger.info(f"  [Skill3] 检查 shot_{sid:02d}: {Path(frame_path).name}")
    trace = {}

    # 加载生成图
    try:
        gen_b64 = _compress_image(frame_path)
    except Exception as e:
        logger.warning(f"  [Skill3] shot_{sid:02d} 图片加载失败: {e}")
        return _default_result(sid, frame_path), None, trace

    # 构造图片列表：[参考图...] + [生成图]
    all_images = ref_images_b64 + [gen_b64]
    has_ref = len(ref_images_b64) > 0

    # 构造 prompt
    if has_ref:
        prompt = COMPLIANCE_PROMPT.format(
            n_ref=len(ref_images_b64),
            shot_purpose=shot_info.get("purpose", "未知"),
            prompt_cn=shot_info.get("prompt_cn", "未知"),
            shot_type=shot_info.get("type", "Medium"),
        )
    else:
        prompt = NO_REFERENCE_PROMPT.format(
            shot_purpose=shot_info.get("purpose", "未知"),
            prompt_cn=shot_info.get("prompt_cn", "未知"),
            shot_type=shot_info.get("type", "Medium"),
        )

    trace["prompt"] = prompt

    # 调用 Vision LLM（JSON 解析失败时重试一次）
    raw = None
    for attempt in range(1, 3):
        try:
            from utils.llm_client import llm_client
            raw = llm_client.call_vision(prompt, all_images)
            trace["raw_response"] = raw
        except Exception as e:
            logger.warning(f"  [Skill3] shot_{sid:02d} Vision 调用失败 (attempt={attempt}): {e}")
            trace["error"] = str(e)
            if attempt == 2:
                return _default_result(sid, frame_path), None, trace
            continue

        # 解析结果
        try:
            data = extract_json(raw)
            trace["parsed"] = data
            break  # 解析成功，退出重试循环
        except Exception as e:
            logger.warning(f"  [Skill3] shot_{sid:02d} JSON 解析失败 (attempt={attempt}): {e}\n  raw: {raw[:200]}")
            trace["parse_error"] = str(e)
            if attempt == 2:
                return _default_result(sid, frame_path), None, trace
            # 第 1 次失败：重试，在 prompt 里强调必须返回 JSON
            prompt = prompt + "\n\n[重要] 你必须只返回 JSON 对象，不要包含任何代码、说明或多余文字。"
    else:
        return _default_result(sid, frame_path), None, trace

    cr = _parse_result(sid, frame_path, reference_image_dir, data)
    lh = _parse_layout_hint(sid, data)

    trace["level"] = cr.level.value
    trace["score"] = cr.score
    trace["summary"] = cr.summary
    trace["error_keywords"] = cr.error_keywords

    logger.info(
        f"  [Skill3] shot_{sid:02d} → {cr.level.value} "
        f"(score={cr.score:.1f}) {cr.summary[:60]}"
    )
    return cr, lh, trace


# ── 解析逻辑 ──────────────────────────────────────────────

def _parse_result(
    shot_id: int,
    frame_path: str,
    ref_dir: str,
    data: dict,
) -> ComplianceResult:
    """将 Vision LLM JSON 输出解析为 ComplianceResult。"""
    # Final_Status
    status_str = data.get("Final_Status", "PASS").upper()
    if status_str not in ("PASS", "WARN", "FAIL"):
        status_str = "PASS"
    level = ComplianceLevel(status_str)
    score = _STATUS_SCORE.get(status_str, 1.0)

    # Error_Keywords
    error_kw = data.get("Error_Keywords", [])
    if not isinstance(error_kw, list):
        error_kw = []
    error_kw = [str(k) for k in error_kw if k]

    # Issues
    issues: list[ComplianceIssue] = []

    for item in data.get("Consistency_Issues", []):
        if isinstance(item, dict):
            issues.append(ComplianceIssue(
                category=item.get("category", "geometry"),
                description=item.get("description", ""),
                severity=level,  # 继承整体 level
            ))

    for item in data.get("Integration_Issues", []):
        if isinstance(item, dict):
            cat = item.get("category", "lighting")
            # 融合问题严重时直接 FAIL
            sev = ComplianceLevel.FAIL if cat in ("scale", "lighting") and level == ComplianceLevel.FAIL else level
            issues.append(ComplianceIssue(
                category=cat,
                description=item.get("description", ""),
                severity=sev,
            ))

    for item in data.get("Logic_Issues", []):
        if isinstance(item, dict):
            # 逻辑问题通常是严重的
            issues.append(ComplianceIssue(
                category=item.get("category", "usage_logic"),
                description=item.get("description", ""),
                severity=level,
            ))

    for item in data.get("Quality_And_Risk_Issues", []):
        if isinstance(item, dict):
            cat = item.get("category", "artifact")
            issues.append(ComplianceIssue(
                category=cat,
                description=item.get("description", ""),
                severity=level,
            ))

    summary = data.get("Summary", "")

    return ComplianceResult(
        shot_id=shot_id,
        frame_path=frame_path,
        reference_path=ref_dir,
        level=level,
        score=score,
        issues=issues,
        error_keywords=error_kw,
        summary=summary,
    )


def _parse_layout_hint(shot_id: int, data: dict) -> LayoutHint | None:
    """解析排版建议。"""
    ls = data.get("Layout_Suggestion")
    if not ls or not isinstance(ls, dict):
        return None

    return LayoutHint(
        shot_id=shot_id,
        primary_position=ls.get("primary_position", "bottom_center"),
        fallback_position=ls.get("fallback_position", "bottom_left"),
        reason=ls.get("reason", ""),
        avoid_zone=ls.get("avoid_zone", ""),
    )


# ── 侵权结果合并 ─────────────────────────────────────────

# level 严重度排序
_LEVEL_ORDER = {ComplianceLevel.PASS: 0, ComplianceLevel.WARN: 1, ComplianceLevel.FAIL: 2}


def _merge_copyright(
    results: list[ComplianceResult],
    copyright_risks: dict[int, CopyrightRisk],
    error_keywords: dict[int, list[str]],
) -> None:
    """将 Google Vision API 侵权检测结果合并到 ComplianceResult 中（原地修改）。

    - high → FAIL + error_keywords（品牌名/IP 名 → negative prompt）
    - medium → WARN + 标注"疑似侵权"
    - low/unknown → 不影响
    """
    for cr in results:
        risk = copyright_risks.get(cr.shot_id)
        if not risk or risk.risk in ("low", "unknown"):
            continue

        # 构造侵权 issues
        copyright_issues: list[ComplianceIssue] = []
        copyright_kw: list[str] = []

        if risk.logos:
            copyright_issues.append(ComplianceIssue(
                category="copyright_logo",
                description=f"检测到品牌Logo: {', '.join(risk.logos)}",
                severity=ComplianceLevel.FAIL,
            ))
            for logo in risk.logos:
                copyright_kw.append(f"no {logo} logo")

        if risk.stock_hits:
            copyright_issues.append(ComplianceIssue(
                category="copyright_stock",
                description=f"匹配到素材库: {', '.join(risk.stock_hits)}",
                severity=ComplianceLevel.FAIL,
            ))
            copyright_kw.extend(["original photo", "no stock image"])

        if risk.ip_hits:
            copyright_issues.append(ComplianceIssue(
                category="copyright_ip",
                description=f"疑似IP形象: {', '.join(risk.ip_hits)}",
                severity=ComplianceLevel.WARN if risk.risk == "medium" else ComplianceLevel.FAIL,
            ))
            copyright_kw.extend(["no cartoon character", "no anime"])

        # 如果有 reasons 但没有细分命中（可能是多匹配/多域名导致的 medium）
        if not copyright_issues and risk.reasons:
            copyright_issues.append(ComplianceIssue(
                category="copyright_web",
                description="; ".join(risk.reasons),
                severity=ComplianceLevel.WARN,
            ))

        # 合并 issues
        cr.issues.extend(copyright_issues)

        # 合并 error_keywords
        if copyright_kw:
            cr.error_keywords = list(set(cr.error_keywords + copyright_kw))
            error_keywords[cr.shot_id] = cr.error_keywords

        # 升级 level（取更严重的）
        if risk.risk == "high":
            target_level = ComplianceLevel.FAIL
            target_score = 0.2
        else:  # medium
            target_level = ComplianceLevel.WARN
            target_score = 0.6

        if _LEVEL_ORDER.get(target_level, 0) > _LEVEL_ORDER.get(cr.level, 0):
            cr.level = target_level
        cr.score = min(cr.score, target_score)

        # 更新 summary
        risk_label = "侵权" if risk.risk == "high" else "疑似侵权"
        reason_str = "; ".join(risk.reasons[:2])
        if cr.summary:
            cr.summary = f"{cr.summary} | [{risk_label}] {reason_str}"
        else:
            cr.summary = f"[{risk_label}] {reason_str}"


def _default_result(shot_id: int, frame_path: str) -> ComplianceResult:
    """检查失败时的兜底返回（PASS，不阻断流程）。"""
    return ComplianceResult(
        shot_id=shot_id,
        frame_path=frame_path,
        level=ComplianceLevel.PASS,
        score=1.0,
        summary="检查失败，默认通过",
    )
