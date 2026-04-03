"""AI导航 (yswg) API 客户端。

支持：上传图片、创建异步生图任务、轮询任务状态。
"""

import logging
import time
from pathlib import Path
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

# 任务状态
TASK_STATUS = {
    0: "PENDING",    # 待处理（排队中）
    1: "RUNNING",    # 处理中
    2: "SUCCESS",    # 成功
    3: "FAILED",     # 失败
    4: "CANCELED",   # 已取消
}


class AiNavClient:
    """AI导航平台客户端。"""

    def __init__(
        self,
        base_url: str = "",
        token: str = "",
        app_id: str = "",
        group_id: str = "",
        purpose: str = "image",
    ):
        """初始化客户端。

        Args:
            purpose: "image" (生图/生视频, GROUP_ID=1) 或 "llm" (Gemini LLM, GROUP_ID=13)。
                     手动传 app_id/group_id 时忽略此参数。
        """
        self.base_url = (base_url or settings.AI_NAV_BASE_URL).rstrip("/")
        self.token = token or settings.AI_NAV_TOKEN

        if app_id and group_id:
            self.app_id = app_id
            self.group_id = group_id
        elif purpose == "llm":
            self.app_id = settings.AI_NAV_LLM_APP_ID
            self.group_id = settings.AI_NAV_LLM_GROUP_ID
        else:
            self.app_id = settings.AI_NAV_IMAGE_APP_ID
            self.group_id = settings.AI_NAV_IMAGE_GROUP_ID

        if not self.token:
            raise ValueError("AI_NAV_TOKEN 未配置，请在 .env 中设置")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
        }

    # ── 上传图片 ──────────────────────────────────────

    def upload_image(self, image_path: str, scene: str = "") -> str:
        """上传图片（自动压缩），返回图片 URL key。

        Args:
            image_path: 本地图片路径。
            scene: 场景编码（可选）。

        Returns:
            上传后的图片 key（用于创建任务时引用）。
        """
        url = f"{self.base_url}/web/files/images"
        params = {}
        if scene:
            params["scene"] = scene

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片不存在: {image_path}")

        logger.info(f"[AiNav] 上传图片: {path.name}")

        with open(image_path, "rb") as f:
            resp = requests.post(
                url,
                headers=self._headers,
                params=params,
                files={"file": (path.name, f)},
                timeout=(10, 60),
            )

        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "200":
            raise RuntimeError(f"上传失败: {data}")

        key = data.get("data", {}).get("key") or data.get("data", {}).get("url", "")
        if not key:
            raise RuntimeError(f"上传返回无 key/url: {data}")

        logger.info(f"[AiNav] 上传成功: {key}")
        return key

    # ── 创建异步任务 ──────────────────────────────────

    def create_task(
        self,
        image_urls: list[str],
        prompt: str,
        *,
        aspect_ratio: str = "1:1",
        image_count: int = 1,
    ) -> str:
        """创建异步生图任务，返回任务 ID。

        Args:
            image_urls: 图片 URL 列表（上传后的 key 或完整 URL）。
            prompt: 生图提示词。
            aspect_ratio: 宽高比，如 "1:1"、"16:9"。
            image_count: 生成图片数量（默认 1，最大 10）。

        Returns:
            任务 ID（字符串）。
        """
        url = f"{self.base_url}/web/ai/invoke/tasks"

        payload = {
            "appId": self.app_id,
            "groupId": self.group_id,
            "params": {
                "image": image_urls,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
            },
            "imageCount": image_count,
        }

        logger.info(f"[AiNav] 创建任务: prompt={prompt[:50]}... images={len(image_urls)}")

        resp = requests.post(
            url,
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
            timeout=(10, 30),
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "200":
            raise RuntimeError(f"创建任务失败: {data}")

        raw_data = data.get("data", {})
        # data 可能是 dict、list[dict]、或 list[str]
        if isinstance(raw_data, list):
            if raw_data and isinstance(raw_data[0], dict):
                task_id = str(raw_data[0].get("id", ""))
            elif raw_data:
                task_id = str(raw_data[0])
            else:
                task_id = ""
        elif isinstance(raw_data, dict):
            task_id = str(raw_data.get("id", ""))
        else:
            task_id = str(raw_data) if raw_data else ""
        if not task_id:
            raise RuntimeError(f"创建任务返回无 ID: {data}")

        logger.info(f"[AiNav] 任务已创建: {task_id}")
        return task_id

    # ── 查询任务状态 ──────────────────────────────────

    def get_task(self, task_id: str) -> dict:
        """查询任务详情。

        Returns:
            {
                "id": str,
                "status": int,  # 0=PENDING, 1=RUNNING, 2=SUCCESS, 3=FAILED, 4=CANCELED
                "status_name": str,
                "result_urls": list[str],  # 成功时的图片 URL
                "fail_reason": str,
                "duration_ms": int,
                "queue_position": int | None,
                "estimated_wait": int | None,
            }
        """
        url = f"{self.base_url}/web/ai/invoke/tasks/{task_id}"

        resp = requests.get(url, headers=self._headers, timeout=(10, 30))
        resp.raise_for_status()
        data = resp.json()

        task_data = data.get("data", {})
        status = task_data.get("status", 0)

        # 提取结果
        result_urls = []
        result_text = ""
        response_json = task_data.get("responseJson") or {}
        for item in response_json.get("data", []):
            if isinstance(item, dict):
                if item.get("url"):
                    result_urls.append(item["url"])
                if item.get("text"):
                    result_text += item["text"]
            elif isinstance(item, str):
                result_text += item

        # 兜底：responseJson 本身可能是文本
        if not result_urls and not result_text:
            raw = response_json.get("text") or response_json.get("content") or ""
            if isinstance(raw, str):
                result_text = raw

        return {
            "id": str(task_data.get("id", "")),
            "status": status,
            "status_name": TASK_STATUS.get(status, f"UNKNOWN({status})"),
            "result_urls": result_urls,
            "result_text": result_text,
            "fail_reason": task_data.get("failReason") or "",
            "duration_ms": int(task_data.get("durationMs") or 0),
            "queue_position": task_data.get("queuePosition"),
            "estimated_wait": task_data.get("estimatedWaitSeconds"),
        }

    # ── 轮询等待任务完成 ──────────────────────────────

    def wait_for_task(
        self,
        task_id: str,
        *,
        poll_interval: float = 3.0,
        timeout: float = 120.0,
    ) -> dict:
        """轮询等待任务完成。

        Returns:
            get_task() 的结果。

        Raises:
            TimeoutError: 超时。
            RuntimeError: 任务失败或取消。
        """
        deadline = time.time() + timeout

        while time.time() < deadline:
            result = self.get_task(task_id)
            status = result["status"]

            if status == 2:  # SUCCESS
                logger.info(
                    f"[AiNav] 任务完成: {task_id} "
                    f"({result['duration_ms']}ms, {len(result['result_urls'])} 张图)"
                )
                return result

            if status in (3, 4):  # FAILED / CANCELED
                raise RuntimeError(
                    f"任务{result['status_name']}: {result['fail_reason']}"
                )

            # PENDING / RUNNING
            queue = result.get("queue_position")
            queue_info = f" 队列位置={queue}" if queue else ""
            logger.debug(f"[AiNav] 任务 {result['status_name']}{queue_info}，等待...")
            time.sleep(poll_interval)

        raise TimeoutError(f"任务 {task_id} 超时 ({timeout}s)")


# Singleton
ai_nav_client = AiNavClient.__new__(AiNavClient)
ai_nav_client.__class__ = AiNavClient


def get_client(**kwargs) -> AiNavClient:
    """获取 AiNavClient 实例（懒初始化）。"""
    return AiNavClient(**kwargs)
