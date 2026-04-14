"""可灵 AI (Kling) API 客户端。

支持：图生视频 (image2video)，JWT 认证 (HS256)，异步任务轮询。
文档：https://klingai.com/document-api/apiReference/model/imageToVideo
"""

import base64
import logging
import time
from pathlib import Path
from typing import Callable, Optional

import jwt
import requests

from config import settings
from utils.dynamic_semaphore import DynamicSemaphore

logger = logging.getLogger(__name__)

# 任务状态（可灵 API）
TASK_STATUS = {
    "submitted": "已提交",
    "processing": "处理中",
    "succeed": "成功",
    "failed": "失败",
}

# 全局 Kling 并发信号量 — 所有 KlingClient 实例、所有 pipeline 共用
# 初始值来自 .env，运行时可通过 kling_semaphore.set_limit(n) 动态调整
kling_semaphore = DynamicSemaphore(settings.KLING_MAX_CONCURRENT)


def _generate_jwt_token(access_key: str, secret_key: str, expire_seconds: int = 1800) -> str:
    """生成可灵 API 的 JWT Token (HS256)。

    Args:
        access_key: AK
        secret_key: SK
        expire_seconds: 过期时间（秒），默认 30 分钟

    Returns:
        JWT token 字符串
    """
    now = int(time.time())
    payload = {
        "iss": access_key,
        "exp": now + expire_seconds,
        "nbf": now - 5,  # 允许 5 秒时钟偏差
    }
    headers = {
        "alg": "HS256",
        "typ": "JWT",
    }
    return jwt.encode(payload, secret_key, algorithm="HS256", headers=headers)


class KlingClient:
    """可灵 AI 视频生成客户端。"""

    def __init__(
        self,
        access_key: str = "",
        secret_key: str = "",
        base_url: str = "",
        model: str = "",
        mode: str = "",
        duration: str = "",
        aspect_ratio: str = "",
    ):
        self.access_key = access_key or settings.KLING_ACCESS_KEY
        self.secret_key = secret_key or settings.KLING_SECRET_KEY
        self.base_url = (base_url or settings.KLING_BASE_URL).rstrip("/")
        self.model = model or settings.KLING_MODEL
        self.mode = mode or settings.KLING_MODE
        self.duration = duration or settings.KLING_DURATION
        self.aspect_ratio = aspect_ratio or settings.KLING_ASPECT_RATIO

        if not self.access_key or not self.secret_key:
            raise ValueError("KLING_ACCESS_KEY / KLING_SECRET_KEY 未配置，请在 .env 中设置")

        self._token: str = ""
        self._token_expire: float = 0

    @property
    def _auth_token(self) -> str:
        """获取有效的 JWT token，过期自动刷新。"""
        now = time.time()
        if not self._token or now >= self._token_expire - 60:  # 提前 60s 刷新
            self._token = _generate_jwt_token(self.access_key, self.secret_key)
            self._token_expire = now + 1800
            logger.debug("[Kling] JWT token 已刷新")
        return self._token

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._auth_token}",
            "Content-Type": "application/json",
        }

    # ── 图生视频 ──────────────────────────────────────

    def image_to_video(
        self,
        image: str,
        prompt: str = "",
        *,
        image_tail: str = "",
        negative_prompt: str = "",
        model: str = "",
        mode: str = "",
        duration: str = "",
        aspect_ratio: str = "",
        cfg_scale: float = 0.5,
        callback_url: str = "",
    ) -> dict:
        """创建图生视频任务。

        Args:
            image: 图片 URL 或本地文件路径（自动转 base64）。
            prompt: 运镜/动作提示词。
            image_tail: 尾帧图片 URL 或 base64（可选）。
            negative_prompt: 反向提示词。
            model: 模型名称，默认 kling-v2-5。
            mode: std / pro。
            duration: 5 / 10（秒）。
            aspect_ratio: 16:9 / 9:16 / 1:1。
            cfg_scale: 生成自由度 0~1，越大越遵循提示词。
            callback_url: 任务完成回调 URL（可选）。

        Returns:
            {"task_id": str, "task_status": str}
        """
        url = f"{self.base_url}/v1/videos/image2video"

        # 处理图片：URL 直接用，本地路径转 base64
        image_value = self._resolve_image(image)
        payload = {
            "model_name": model or self.model,
            "mode": mode or self.mode,
            "duration": duration or self.duration,
            "aspect_ratio": aspect_ratio or self.aspect_ratio,
            "image": image_value,
            "prompt": prompt,
            "cfg_scale": cfg_scale,
        }

        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if image_tail:
            payload["image_tail"] = self._resolve_image(image_tail)
        if callback_url:
            payload["callback_url"] = callback_url

        logger.info(
            f"[Kling] 创建图生视频任务: model={payload['model_name']} "
            f"mode={payload['mode']} duration={payload['duration']}s "
            f"prompt={prompt[:60]}..."
        )

        resp = requests.post(url, headers=self._headers, json=payload, timeout=(10, 30))
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"创建任务失败: code={data.get('code')} message={data.get('message')}")

        task_data = data.get("data", {})
        task_id = task_data.get("task_id", "")
        task_status = task_data.get("task_status", "")

        logger.info(f"[Kling] 任务已创建: {task_id} ({task_status})")
        return {"task_id": task_id, "task_status": task_status}

    # ── 查询任务 ──────────────────────────────────────

    def get_task(self, task_id: str) -> dict:
        """查询图生视频任务状态。

        Returns:
            {
                "task_id": str,
                "task_status": str,  # submitted/processing/succeed/failed
                "task_status_msg": str,
                "video_url": str | None,  # 成功时的视频下载 URL
                "video_duration": float | None,
                "created_at": int,
                "updated_at": int,
            }
        """
        url = f"{self.base_url}/v1/videos/image2video/{task_id}"

        resp = requests.get(url, headers=self._headers, timeout=(10, 30))
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"查询任务失败: code={data.get('code')} message={data.get('message')}")

        task_data = data.get("data", {})
        task_status = task_data.get("task_status", "")

        # 提取视频结果
        video_url = None
        video_duration = None
        task_result = task_data.get("task_result", {})
        videos = task_result.get("videos", [])
        if videos:
            video_url = videos[0].get("url")
            video_duration = videos[0].get("duration")

        return {
            "task_id": task_data.get("task_id", task_id),
            "task_status": task_status,
            "task_status_msg": TASK_STATUS.get(task_status, task_status),
            "video_url": video_url,
            "video_duration": video_duration,
            "created_at": task_data.get("created_at", 0),
            "updated_at": task_data.get("updated_at", 0),
        }

    # ── 轮询等待 ──────────────────────────────────────

    def wait_for_task(
        self,
        task_id: str,
        *,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """轮询等待任务完成。

        Args:
            on_status: 状态变化时的回调 callable(status)，
                       status 为 "submitted" / "processing" / "succeed" / "failed"

        Returns:
            get_task() 的结果。

        Raises:
            TimeoutError: 超时。
            RuntimeError: 任务失败。
        """
        deadline = time.time() + timeout
        last_status: Optional[str] = None

        while time.time() < deadline:
            result = self.get_task(task_id)
            status = result["task_status"]

            if status != last_status:
                last_status = status
                if on_status:
                    on_status(status)

            if status == "succeed":
                logger.info(f"[Kling] 任务完成: {task_id} → {result['video_url']}")
                return result

            if status == "failed":
                raise RuntimeError(f"可灵任务失败: {task_id}")

            logger.debug(f"[Kling] 任务 {result['task_status_msg']}，等待...")
            time.sleep(poll_interval)

        raise TimeoutError(f"可灵任务 {task_id} 超时 ({timeout}s)")

    def submit_and_wait(
        self,
        image: str,
        prompt: str = "",
        *,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
        on_status: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> dict:
        """持有全局信号量槽位，提交并等待 Kling 任务完成。

        在获得槽位之前调用 on_status("waiting") 通知调用方当前正在排队。
        进入槽位后按 Kling 实际状态回调 on_status。

        Returns:
            wait_for_task() 的结果（含 video_url 等）。
        """
        if on_status:
            on_status("waiting")              # 等待全局信号量 → UI 变黄

        with kling_semaphore:
            create_result = self.image_to_video(image, prompt, **kwargs)
            task_id = create_result["task_id"]
            return self.wait_for_task(
                task_id,
                poll_interval=poll_interval,
                timeout=timeout,
                on_status=on_status,
            )

    # ── 下载视频 ──────────────────────────────────────

    def download_video(self, video_url: str, output_path: str) -> str:
        """下载生成的视频到本地。

        Args:
            video_url: 视频 URL。
            output_path: 本地保存路径。

        Returns:
            本地文件路径。
        """
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[Kling] 下载视频: {video_url[:80]}...")

        resp = requests.get(video_url, timeout=(10, 120), stream=True)
        resp.raise_for_status()

        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        size_mb = path.stat().st_size / (1024 * 1024)
        logger.info(f"[Kling] 下载完成: {path} ({size_mb:.1f} MB)")
        return str(path)

    # ── 一站式：图生视频 + 等待 + 下载 ────────────────

    def generate_video(
        self,
        image: str,
        prompt: str = "",
        output_path: str = "",
        *,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
        **kwargs,
    ) -> dict:
        """一站式：创建任务 → 轮询 → 下载视频。

        Args:
            image: 图片 URL 或本地路径。
            prompt: 运镜提示词。
            output_path: 视频保存路径（空则不下载）。
            **kwargs: 传给 image_to_video() 的其他参数。

        Returns:
            {
                "task_id": str,
                "video_url": str,
                "video_path": str | None,  # 如果指定了 output_path
                "video_duration": float | None,
            }
        """
        # 1. 创建任务
        create_result = self.image_to_video(image, prompt, **kwargs)
        task_id = create_result["task_id"]

        # 2. 轮询等待
        task_result = self.wait_for_task(
            task_id, poll_interval=poll_interval, timeout=timeout
        )

        # 3. 下载（如果指定路径）
        video_path = None
        if output_path and task_result["video_url"]:
            video_path = self.download_video(task_result["video_url"], output_path)

        return {
            "task_id": task_id,
            "video_url": task_result["video_url"],
            "video_path": video_path,
            "video_duration": task_result["video_duration"],
        }

    # ── 内部方法 ──────────────────────────────────────

    @staticmethod
    def _resolve_image(image: str) -> str:
        """图片路径/URL 解析：URL 直接返回，本地文件转 base64。"""
        if image.startswith(("http://", "https://")):
            return image

        path = Path(image)
        if path.exists():
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            logger.debug(f"[Kling] 本地图片转 base64: {path.name}")
            return b64

        # 既不是 URL 也不是本地文件，当作已有的 base64
        return image


def get_client(**kwargs) -> KlingClient:
    """获取 KlingClient 实例。"""
    return KlingClient(**kwargs)
