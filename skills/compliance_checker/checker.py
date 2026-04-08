"""Skill 3: 合规性检查 — 产品一致性 + AI 质量 + 侵权风险 + 排版建议。

将 AI 生成图与用户上传的产品参考图送入 Gemini Vision，
输出 ComplianceResult（供选材淘汰/降权）和 LayoutHint（供 Skill 5 排版参考）。
"""

import base64
import concurrent.futures
import io
import logging
from pathlib import Path

from models.compliance import (
    ComplianceIssue,
    ComplianceLevel,
    ComplianceResult,
    LayoutHint,
)
from models.storyboard import Storyboard
from utils.json_repair import extract_json

from .prompts import COMPLIANCE_PROMPT, NO_REFERENCE_PROMPT

logger = logging.getLogger(__name__)

# 支持的图片格式
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# 并发控制
MAX_WORKERS = 3
TIMEOUT_PER_SHOT = 200.0

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

    results, layout_hints, error_keywords, per_shot_trace = _batch_check(
        shots, frame_paths, ref_images_b64, reference_image_dir,
    )

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
            "prompt_template": COMPLIANCE_PROMPT if has_ref else NO_REFERENCE_PROMPT,
            "per_shot": per_shot_trace,
            "meta": {
                "total_shots": len(shots),
                "checked": len(results),
                "pass": pass_count,
                "warn": warn_count,
                "fail": fail_count,
                "has_reference": has_ref,
                "reference_count": len(ref_images_b64),
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
    for img_file in images[:6]:  # 最多 6 张参考图
        try:
            b64 = _compress_image(str(img_file))
            result.append(b64)
            logger.debug(f"[Skill3] 参考图加载: {img_file.name}")
        except Exception as e:
            logger.warning(f"[Skill3] 参考图加载失败: {img_file}: {e}")

    logger.info(f"[Skill3] 加载了 {len(result)} 张参考图 (目录: {ref_dir})")
    return result


def _compress_image(image_path: str, max_size: int = 1024) -> str:
    """压缩图片到 max_size，返回 base64 JPEG。"""
    from PIL import Image

    img = Image.open(image_path)
    # 转 RGB（去掉 alpha 通道）
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
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
    """并发检查所有 shot。返回 (results, layout_hints, error_keywords, per_shot_trace)。"""
    results: list[ComplianceResult] = []
    layout_hints: dict[int, LayoutHint] = {}
    error_keywords: dict[int, list[str]] = {}
    per_shot_trace: dict[int, dict] = {}

    # 过滤有帧的 shot
    checkable = [(s, frame_paths[s["shot_id"]]) for s in shots if s["shot_id"] in frame_paths]

    if not checkable:
        logger.warning("[Skill3] 无可检查的 shot（frame_paths 为空）")
        return results, layout_hints, error_keywords, per_shot_trace

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for shot_info, frame_path in checkable:
            future = executor.submit(
                _check_single_shot,
                shot_info, frame_path, ref_images_b64, reference_image_dir,
            )
            future_map[future] = shot_info["shot_id"]

        for future in concurrent.futures.as_completed(future_map):
            sid = future_map[future]
            try:
                cr, lh, trace = future.result(timeout=TIMEOUT_PER_SHOT)
                results.append(cr)
                if lh:
                    layout_hints[sid] = lh
                if cr.error_keywords:
                    error_keywords[sid] = cr.error_keywords
                if trace:
                    per_shot_trace[sid] = trace
            except Exception as e:
                logger.warning(f"[Skill3] shot_{sid:02d} 检查失败: {e}")
                fp = frame_paths.get(sid, "")
                results.append(_default_result(sid, fp))

    # 按 shot_id 排序
    results.sort(key=lambda r: r.shot_id)
    return results, layout_hints, error_keywords, per_shot_trace


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

    # 调用 Vision LLM
    try:
        from utils.llm_client import llm_client
        raw = llm_client.call_vision(prompt, all_images)
        trace["raw_response"] = raw
    except Exception as e:
        logger.warning(f"  [Skill3] shot_{sid:02d} Vision 调用失败: {e}")
        trace["error"] = str(e)
        return _default_result(sid, frame_path), None, trace

    # 解析结果
    try:
        data = extract_json(raw)
        trace["parsed"] = data
    except Exception as e:
        logger.warning(f"  [Skill3] shot_{sid:02d} JSON 解析失败: {e}\n  raw: {raw[:200]}")
        trace["parse_error"] = str(e)
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

    for item in data.get("Quality_And_Risk_Issues", []):
        if isinstance(item, dict):
            cat = item.get("category", "artifact")
            sev = ComplianceLevel.FAIL if cat == "copyright" else level
            issues.append(ComplianceIssue(
                category=cat,
                description=item.get("description", ""),
                severity=sev,
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


def _default_result(shot_id: int, frame_path: str) -> ComplianceResult:
    """检查失败时的兜底返回（PASS，不阻断流程）。"""
    return ComplianceResult(
        shot_id=shot_id,
        frame_path=frame_path,
        level=ComplianceLevel.PASS,
        score=1.0,
        summary="检查失败，默认通过",
    )
