"""Skill 5 Module A: 视频质量检测（Gemini Vision）。

从每个视频抽 5 帧，送 LLM Vision 评估画面质量，
返回质量评分和问题描述，帮助剪辑决策筛选废片。
"""

import base64
import logging
import subprocess
import tempfile
from pathlib import Path

from config import settings
from utils.json_repair import extract_json

logger = logging.getLogger(__name__)

# 每个视频抽取的帧数
FRAMES_PER_VIDEO = 5

VISION_PROMPT = """\
你是一个 AI 生成视频的质量审核专家。

下面是同一个 AI 生成短视频（约 5 秒）中均匀抽取的 {n_frames} 帧画面。
该视频的生成意图是：{intent}

请评估这段视频的画面质量，关注以下方面：
1. **产品完整性**：产品是否变形、缺失、不自然？
2. **物理逻辑**：手部/肢体是否畸形？物体位置是否合理？
3. **画面一致性**：帧之间是否有闪烁、突变、不连贯？
4. **与意图匹配**：画面是否与生成意图描述一致？
5. **整体美观**：构图、光线、色彩是否协调？

直接输出 JSON，不要有其它说明文字：
```json
{{
  "quality_score": 7.5,
  "scene_description": "一句话描述画面内容",
  "issues": ["问题1", "问题2"],
  "recommendation": "use" 或 "reject"
}}
```

- quality_score: 0-10 分，10 分最好
  - 8-10: 画面正常，可直接使用
  - 5-7: 有小瑕疵但可接受
  - 0-4: 严重问题，建议废弃
- issues: 发现的具体问题列表，无问题则为空数组
- recommendation: "use"（可用）或 "reject"（建议废弃）
"""


def check_video_quality(
    video_path: str,
    intent: str = "",
    *,
    preferred_llm: str | None = None,
) -> dict:
    """检测单个视频的画面质量。

    Args:
        video_path: 视频文件路径。
        intent: 生成意图描述（来自 storyboard prompt_cn）。
        preferred_llm: LLM 选择（用于 vision 路由）。

    Returns:
        {
            "quality_score": float,  # 0-10
            "scene_description": str,
            "issues": list[str],
            "recommendation": str,  # "use" | "reject"
        }
        检测失败时返回默认值（score=-1，不影响流程）。
    """
    frames_b64 = _extract_frames(video_path, FRAMES_PER_VIDEO)
    if not frames_b64:
        logger.warning(f"抽帧失败: {video_path}")
        return _default_result()

    prompt = VISION_PROMPT.format(n_frames=len(frames_b64), intent=intent or "未知")

    try:
        raw = _call_vision(prompt, frames_b64, preferred_llm=preferred_llm)
        data = extract_json(raw)
        return {
            "quality_score": float(data.get("quality_score", -1)),
            "scene_description": data.get("scene_description", ""),
            "issues": data.get("issues", []),
            "recommendation": data.get("recommendation", "use"),
        }
    except Exception as e:
        logger.warning(f"Vision 质量检测失败: {video_path}: {e}")
        return _default_result()


def batch_check(
    video_paths: list[str],
    intents: list[str] | None = None,
    *,
    preferred_llm: str | None = None,
) -> list[dict]:
    """批量检测多个视频的画面质量。

    Args:
        video_paths: 视频路径列表。
        intents: 每个视频的意图描述（与 video_paths 对应）。
        preferred_llm: LLM 选择。

    Returns:
        list[dict]，与 video_paths 一一对应。
    """
    intents = intents or [""] * len(video_paths)
    results = []

    for i, (path, intent) in enumerate(zip(video_paths, intents)):
        logger.info(f"  Vision 检测 [{i+1}/{len(video_paths)}]: {Path(path).name}")
        result = check_video_quality(path, intent, preferred_llm=preferred_llm)
        score = result["quality_score"]
        rec = result["recommendation"]
        issues = ", ".join(result["issues"]) if result["issues"] else "无"
        logger.info(f"    → score={score:.1f} rec={rec} issues=[{issues}]")
        results.append(result)

    return results


def _extract_frames(video_path: str, n_frames: int) -> list[str]:
    """从视频均匀抽取 n 帧，返回 base64 编码的 JPEG 列表。"""
    from utils.ffmpeg_wrapper import get_video_info

    try:
        info = get_video_info(video_path)
        duration = info["duration"]
    except Exception:
        return []

    if duration <= 0:
        return []

    # 均匀分布取帧时间点（排除首尾 0.1s）
    margin = min(0.1, duration * 0.05)
    usable = duration - 2 * margin
    if usable <= 0:
        timestamps = [duration / 2]
    else:
        timestamps = [margin + usable * i / (n_frames - 1) for i in range(n_frames)]

    frames_b64 = []
    with tempfile.TemporaryDirectory(prefix="vision_") as tmpdir:
        for j, ts in enumerate(timestamps):
            out_path = f"{tmpdir}/frame_{j:02d}.jpg"
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{ts:.3f}",
                        "-i", video_path,
                        "-frames:v", "1",
                        "-q:v", "2",  # JPEG 质量
                        out_path,
                    ],
                    capture_output=True, timeout=10,
                )
                if Path(out_path).exists():
                    img_bytes = Path(out_path).read_bytes()
                    frames_b64.append(base64.b64encode(img_bytes).decode("ascii"))
            except Exception:
                continue

    return frames_b64


def _call_vision(
    prompt: str,
    images_b64: list[str],
    *,
    preferred_llm: str | None = None,
) -> str:
    """调用 Vision LLM：统一走 llm_client.call_vision（AI导航优先）。"""
    from utils.llm_client import llm_client
    return llm_client.call_vision(prompt, images_b64, preferred_llm=preferred_llm)


def _default_result() -> dict:
    """检测失败时的默认返回值。"""
    return {
        "quality_score": -1.0,
        "scene_description": "",
        "issues": [],
        "recommendation": "use",  # 检测失败不阻断，默认可用
    }
