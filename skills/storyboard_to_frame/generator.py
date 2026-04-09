"""Skill 2: 分镜 → 画面帧。

读取 Storyboard 中每个 shot 的 prompt_cn，
配合用户上传的产品参考图，调用 AI导航生图 API 生成画面帧。

策略：先批量提交所有任务，再逐个轮询等待结果。
"""

import logging
from pathlib import Path

import requests

from models.storyboard import Storyboard
from utils.ai_nav_client import AiNavClient

logger = logging.getLogger(__name__)

# 支持的图片格式
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# 参考图上限（AI导航最多支持 15 张，产品一般 ≤6 张）
MAX_REFERENCE_IMAGES = 6


def generate_frames(
    storyboard: Storyboard,
    reference_image_dir: str,
    output_dir: str,
    *,
    aspect_ratio: str = "16:9",
    poll_interval: float = 3.0,
    timeout: float = 180.0,
    error_keywords: dict[int, list[str]] | None = None,
) -> dict:
    """从 storyboard 生成所有 shot 的画面帧。

    Args:
        storyboard: Skill 1 输出的分镜脚本���
        reference_image_dir: 产品参考图目录（input/reference_images/）。
        output_dir: 帧图保存目录（output/frames/{task_id}/）。
        aspect_ratio: 生图宽高比，默认 16:9。
        poll_interval: 轮询间隔秒数。
        timeout: 单任务超时秒数。
        error_keywords: Skill 3 输出的 {shot_id: [keyword, ...]}，
                        拼接到 prompt 末尾作为 negative 约束。

    Returns:
        {
            "frame_paths": {shot_id: str},   # 成功的帧路径
            "failed_shots": [shot_id, ...],  # 失败的 shot_id
        }
    """
    client = AiNavClient(purpose="image")

    # 1. 收集并上传参考图
    ref_keys = _upload_reference_images(client, reference_image_dir)
    logger.info(f"[Skill2] 参考图上传完成: {len(ref_keys)} 张")

    # 2. 收集所有 shot
    shots = []
    for sg in storyboard.scene_groups:
        for shot in sg.shots:
            shots.append(shot)

    if not shots:
        logger.warning("[Skill2] storyboard 中没有 shot")
        return {"frame_paths": {}, "failed_shots": []}

    logger.info(f"[Skill2] 共 {len(shots)} 个 shot，开始批量提交生图任务")

    # 3. 批量提交 create_task
    task_map: dict[str, int] = {}  # {task_id: shot_id}
    submit_failed: list[int] = []

    for shot in shots:
        # 构造 prompt，拼接 error_keywords 作为 negative 约束
        prompt = shot.prompt_cn
        if error_keywords:
            kw_list = error_keywords.get(shot.shot_id, [])
            if kw_list:
                neg = ", ".join(kw_list)
                prompt = f"{prompt}\n\n[Negative: {neg}]"
                logger.info(f"  shot_{shot.shot_id:02d} 附加 negative: {neg}")

        try:
            task_id = client.create_task(
                image_urls=ref_keys,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                image_count=1,
            )
            task_map[task_id] = shot.shot_id
            logger.info(f"  shot_{shot.shot_id:02d} → task {task_id}")
        except Exception as e:
            logger.warning(f"  shot_{shot.shot_id:02d} 提交失败: {e}")
            submit_failed.append(shot.shot_id)

    logger.info(f"[Skill2] 提交完成: {len(task_map)} 成功, {len(submit_failed)} 失败")

    # 4. 批量轮询 wait_for_task + 下载
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    frame_paths: dict[int, str] = {}
    poll_failed: list[int] = []

    for i, (task_id, shot_id) in enumerate(task_map.items()):
        logger.info(f"[Skill2] 等待 [{i+1}/{len(task_map)}] shot_{shot_id:02d} ...")
        try:
            result = client.wait_for_task(
                task_id, poll_interval=poll_interval, timeout=timeout,
            )
            urls = result.get("result_urls", [])
            if not urls:
                logger.warning(f"  shot_{shot_id:02d} 无图片 URL 返回")
                poll_failed.append(shot_id)
                continue

            save_path = out_path / f"shot_{shot_id:02d}.png"
            _download_image(urls[0], str(save_path))
            frame_paths[shot_id] = str(save_path)
            logger.info(f"  shot_{shot_id:02d} ✓ → {save_path.name}")

        except Exception as e:
            logger.warning(f"  shot_{shot_id:02d} 失败: {e}")
            poll_failed.append(shot_id)

    failed_shots = submit_failed + poll_failed
    logger.info(
        f"[Skill2] 完成: {len(frame_paths)} 成功, {len(failed_shots)} 失败"
        + (f" (失败: {failed_shots})" if failed_shots else "")
    )

    # trace: per-shot prompt + 参考图信息
    per_shot_prompts = {}
    for shot in shots:
        per_shot_prompts[shot.shot_id] = shot.prompt_cn

    return {
        "frame_paths": frame_paths,
        "failed_shots": failed_shots,
        "_trace": {
            "per_shot_prompts": per_shot_prompts,
            "meta": {
                "reference_images": len(ref_keys),
                "total_shots": len(shots),
                "success": len(frame_paths),
                "failed": len(failed_shots),
                "failed_ids": failed_shots,
                "frame_paths": {k: str(v) for k, v in frame_paths.items()},
            },
        },
    }


def _upload_reference_images(client: AiNavClient, ref_dir: str) -> list[str]:
    """扫描参考图目录，上传所有图片，返回 key 列表。"""
    ref_path = Path(ref_dir)
    if not ref_path.exists():
        logger.warning(f"[Skill2] 参考图目录不存在: {ref_dir}")
        return []

    image_files = sorted(
        f for f in ref_path.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not image_files:
        logger.warning(f"[Skill2] 参考图目录为空: {ref_dir}")
        return []

    if len(image_files) > MAX_REFERENCE_IMAGES:
        logger.info(f"[Skill2] 参考图 {len(image_files)} 张，截取前 {MAX_REFERENCE_IMAGES} 张")
        image_files = image_files[:MAX_REFERENCE_IMAGES]

    keys = []
    for img_file in image_files:
        try:
            key = client.upload_image(str(img_file))
            keys.append(key)
        except Exception as e:
            logger.warning(f"[Skill2] 上传失败: {img_file.name}: {e}")

    return keys


def _download_image(url: str, save_path: str) -> str:
    """下载图片到本地。"""
    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    resp = requests.get(url, timeout=(10, 60), stream=True)
    resp.raise_for_status()

    with open(path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    size_kb = path.stat().st_size / 1024
    logger.debug(f"[Skill2] 下载完成: {path.name} ({size_kb:.0f} KB)")
    return str(path)
