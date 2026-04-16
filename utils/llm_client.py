"""Unified LLM client.

路由优先级（文本 & Vision 均一致）:
  AI导航 (GROUP_ID=13) → 最多重试 3 次 → Reverse Prompt (tu-zi) 兜底
"""

import json
import re
import time
import logging
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

# AI导航重试策略
_AINAV_MAX_ATTEMPTS = 3          # 最多尝试 3 次
_AINAV_RETRY_DELAYS = [3.0, 6.0] # 第2、3次前的等待秒数


class LLMClient:
    """Unified LLM interface.

    路由（文本 & Vision 均相同）:
    1. AI导航 Gemini-3-flash (GROUP_ID=13) — 主路，最多重试 3 次
    2. Reverse Prompt (tu-zi) — 兜底，仅在 AI导航 全部重试耗尽后启用
    """

    # ── 公开接口 ──────────────────────────────────────

    def call(
        self,
        system_prompt: str,
        user_message: str,
        *,
        preferred_llm: Optional[str] = None,
        preferred_route: Optional[str] = None,  # 保留兼容
        temperature: float = 0.3,
        max_tokens: int = 8192,
        json_mode: bool = True,
    ) -> str:
        """Call LLM (text only) and return raw text response.

        Args:
            preferred_llm: 'ai_nav' 强制用AI导航 | 'reverse_prompt'/'tuzi' 强制用tu-zi | None 自动
        """
        choice = (preferred_llm or "").strip().lower()

        if choice in ("reverse", "reverse_prompt", "tuzi"):
            return self._call_reverse_prompt(
                system_prompt, user_message, temperature, max_tokens, json_mode
            )

        # AI导航优先（包含 choice=="ai_nav" 和 auto 两种情况）
        if settings.AI_NAV_TOKEN:
            try:
                return self._call_ai_nav_with_retry(
                    system_prompt, user_message, temperature, max_tokens, json_mode
                )
            except Exception as e:
                if choice in ("ai_nav", "ainav"):
                    raise  # 显式指定 ai_nav → 不降级，直接报错
                logger.warning(
                    f"[LLM] AI导航 {_AINAV_MAX_ATTEMPTS} 次均失败，切换 tu-zi: {e}"
                )
                if settings.REVERSE_PROMPT_API_KEY:
                    return self._call_reverse_prompt(
                        system_prompt, user_message, temperature, max_tokens, json_mode
                    )
                raise

        if settings.REVERSE_PROMPT_API_KEY:
            return self._call_reverse_prompt(
                system_prompt, user_message, temperature, max_tokens, json_mode
            )

        raise ValueError("No LLM API configured. Set AI_NAV_TOKEN or REVERSE_PROMPT_API_KEY in .env")

    def call_vision(
        self,
        prompt: str,
        image_base64_list: list[str],
        *,
        preferred_llm: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """Call multimodal LLM with images.

        Vision 路线：
        1. 先用 navigation-ai skill 上传图片 → 拿 CDN URL（token 自动管理）
        2. 再用 tu-zi 发 Vision 请求（URL 替代 base64，请求体更轻）
        AI导航 group=13 不支持多模态，仅在显式指定时尝试。
        """
        choice = (preferred_llm or "").strip().lower()

        if choice in ("ai_nav", "ainav"):
            return self._call_ai_nav_vision_with_retry(prompt, image_base64_list, max_tokens)

        # 默认路线：skill 上传图片 → tu-zi Vision
        if settings.REVERSE_PROMPT_API_KEY:
            # 尝试通过 skill 上传图片拿 CDN URL，失败则降级用 base64
            cdn_urls = self._upload_images_via_skill(image_base64_list)
            if cdn_urls:
                return self._call_reverse_prompt_vision_urls(prompt, cdn_urls, max_tokens)
            return self._call_reverse_prompt_vision(prompt, image_base64_list, max_tokens)

        raise ValueError("No Vision LLM configured. Set REVERSE_PROMPT_API_KEY in .env")

    def _upload_images_via_skill(self, image_base64_list: list[str]) -> list[str]:
        """用 navigation-ai skill 上传图片，返回 CDN URL 列表。失败返回空列表。"""
        import subprocess, tempfile, base64, os
        from pathlib import Path

        skill_script = Path.home() / ".claude" / "skills" / "navigation-ai" / "scripts" / "main.ts"
        if not skill_script.exists():
            return []

        urls = []
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                for i, b64 in enumerate(image_base64_list):
                    img_path = Path(tmpdir) / f"vision_{i}.jpg"
                    img_path.write_bytes(base64.b64decode(b64))
                    r = subprocess.run(
                        ["npx", "-y", "bun", str(skill_script),
                         "upload", "--image-file", str(img_path), "--json"],
                        capture_output=True, text=True, timeout=30,
                    )
                    if r.returncode == 0:
                        import json
                        url = json.loads(r.stdout.strip()).get("url", "")
                        if url:
                            urls.append(url)
        except Exception as e:
            logger.warning(f"[Vision] skill 上传图片失败，降级 base64: {e}")
            return []

        return urls if len(urls) == len(image_base64_list) else []

    def _call_reverse_prompt_vision_urls(
        self,
        prompt: str,
        image_urls: list[str],
        max_tokens: int = 4096,
    ) -> str:
        """用 CDN URL（而非 base64）调用 tu-zi Vision，请求体更轻。"""
        base_url = settings.REVERSE_PROMPT_BASE_URL.rstrip("/")
        url = f"{base_url}/chat/completions"

        content: list = [{"type": "text", "text": prompt}]
        for img_url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": img_url}})

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.REVERSE_PROMPT_API_KEY}",
        }

        models_to_try = [settings.REVERSE_PROMPT_VISION_MODEL]
        fallback = getattr(settings, "REVERSE_PROMPT_VISION_MODEL_FALLBACK", "")
        if fallback and fallback != settings.REVERSE_PROMPT_VISION_MODEL:
            models_to_try.append(fallback)

        last_exc: Exception | None = None
        for model_idx, model in enumerate(models_to_try):
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": max_tokens,
            }
            started_at = time.perf_counter()
            logger.info(f"[Vision][START][TuZi+URL] model={model} images={len(image_urls)}")

            for attempt in range(1, 3):
                try:
                    resp = requests.post(url, headers=headers, json=payload, timeout=(10, 120))
                    resp.raise_for_status()
                    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                    logger.info(f"[Vision][END][TuZi+URL] model={model} elapsed_ms={elapsed_ms}")
                    data = resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if not text:
                        raise ValueError(f"Vision 返回空结果: {data}")
                    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text).strip()
                except requests.exceptions.RequestException as e:
                    last_exc = e
                    retryable = isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))
                    if retryable and attempt < 2:
                        logger.warning(f"[Vision][RETRY][TuZi+URL] model={model} attempt={attempt}")
                        time.sleep(2.0)
                        continue
                    if retryable and model_idx < len(models_to_try) - 1:
                        logger.warning(f"[Vision][FALLBACK][TuZi+URL] {model} 超时，切换 {models_to_try[model_idx + 1]}")
                    break
                except Exception as e:
                    last_exc = e
                    break

        raise RuntimeError(f"TuZi+URL Vision 所有模型均失败: {last_exc}")

    # ── AI导航（含重试）────────────────────────────────

    def _call_ai_nav_with_retry(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """AI导航文本调用，失败时指数退避重试最多 _AINAV_MAX_ATTEMPTS 次。"""
        last_exc: Exception | None = None
        for attempt in range(1, _AINAV_MAX_ATTEMPTS + 1):
            try:
                return self._call_ai_nav(
                    system_prompt, user_message, temperature, max_tokens, json_mode
                )
            except Exception as e:
                last_exc = e
                if attempt < _AINAV_MAX_ATTEMPTS:
                    delay = _AINAV_RETRY_DELAYS[attempt - 1]
                    logger.warning(
                        f"[LLM][AiNav] attempt={attempt} 失败，{delay}s 后重试: {e}"
                    )
                    time.sleep(delay)
        raise RuntimeError(
            f"AI导航文本调用 {_AINAV_MAX_ATTEMPTS} 次全部失败: {last_exc}"
        ) from last_exc

    def _call_ai_nav_vision_with_retry(
        self,
        prompt: str,
        image_base64_list: list[str],
        max_tokens: int,
    ) -> str:
        """AI导航 Vision 调用，失败时指数退避重试最多 _AINAV_MAX_ATTEMPTS 次。"""
        last_exc: Exception | None = None
        for attempt in range(1, _AINAV_MAX_ATTEMPTS + 1):
            try:
                return self._call_ai_nav_vision(prompt, image_base64_list, max_tokens)
            except Exception as e:
                last_exc = e
                if attempt < _AINAV_MAX_ATTEMPTS:
                    delay = _AINAV_RETRY_DELAYS[attempt - 1]
                    logger.warning(
                        f"[Vision][AiNav] attempt={attempt} 失败，{delay}s 后重试: {e}"
                    )
                    time.sleep(delay)
        raise RuntimeError(
            f"AI导航 Vision 调用 {_AINAV_MAX_ATTEMPTS} 次全部失败: {last_exc}"
        ) from last_exc

    def _call_ai_nav(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """单次 AI导航文本调用（params.messages 格式 + 异步轮询）。"""
        from utils.ai_nav_client import AiNavClient

        client = AiNavClient(purpose="llm")
        started_at = time.perf_counter()
        logger.info("[LLM][START][AiNav] group=13 params.messages")

        task_id = client.create_llm_task(
            system_prompt=system_prompt,
            user_message=user_message,
        )
        result = client.wait_for_task(task_id, timeout=240.0)

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(f"[LLM][END][AiNav] elapsed_ms={elapsed_ms}")

        text = result.get("result_text", "")
        if not text:
            raise ValueError(f"AI导航返回空结果: {result}")

        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text).strip()

    def _call_ai_nav_vision(
        self,
        prompt: str,
        image_base64_list: list[str],
        max_tokens: int,
    ) -> str:
        """单次 AI导航 Vision 调用（multimodal messages 格式 + 异步轮询）。"""
        from utils.ai_nav_client import AiNavClient

        client = AiNavClient(purpose="llm")
        started_at = time.perf_counter()
        logger.info(f"[Vision][START][AiNav] images={len(image_base64_list)}")

        image_urls = [
            f"data:image/jpeg;base64,{img}" for img in image_base64_list
        ]
        task_id = client.create_llm_task(
            system_prompt="",
            user_message=prompt,
            image_urls=image_urls,
        )
        result = client.wait_for_task(task_id, timeout=240.0)

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(f"[Vision][END][AiNav] elapsed_ms={elapsed_ms}")

        text = result.get("result_text", "")
        if not text:
            raise ValueError(f"AI导航 Vision 返回空结果: {result}")

        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text).strip()

    # ── Reverse Prompt / tu-zi（兜底）──────────────────

    def _call_reverse_prompt(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Call Reverse Prompt (tu-zi) OpenAI-compatible API（文本）。"""
        base_url = settings.REVERSE_PROMPT_BASE_URL.rstrip("/")
        url = f"{base_url}{settings.REVERSE_PROMPT_PATH}"
        model = settings.REVERSE_PROMPT_MODEL

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.REVERSE_PROMPT_API_KEY}",
        }
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started_at = time.perf_counter()
        logger.info(f"[LLM][START][TuZi] model={model}")

        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=(10, 300))
                resp.raise_for_status()
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                logger.info(f"[LLM][END][TuZi] model={model} elapsed_ms={elapsed_ms} attempt={attempt}")
                break
            except requests.exceptions.RequestException as e:
                last_exc = e
                retryable = isinstance(
                    e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
                ) or (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code in (429, 503)
                )
                if retryable and attempt < 3:
                    delay = 1.5 * (2 ** (attempt - 1))
                    logger.warning(f"[LLM][RETRY][TuZi] attempt={attempt} sleep={delay:.1f}s")
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"TuZi request failed: {e}") from e
        else:
            raise RuntimeError(f"TuZi request failed after retries: {last_exc}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise ValueError(f"No choices in TuZi response: {data}")
        content = (choices[0].get("message") or {}).get("content", "").strip()
        if not content:
            raise ValueError(f"Empty TuZi response: {data}")
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)

    def _call_reverse_prompt_vision(
        self,
        prompt: str,
        image_base64_list: list[str],
        max_tokens: int = 4096,
    ) -> str:
        """Call tu-zi Vision（图文混合，兜底路线）。

        主路：REVERSE_PROMPT_VISION_MODEL
        备路：REVERSE_PROMPT_VISION_MODEL_FALLBACK（主路超时后自动降级）
        """
        base_url = settings.REVERSE_PROMPT_BASE_URL.rstrip("/")
        url = f"{base_url}/chat/completions"

        content: list = [{"type": "text", "text": prompt}]
        for img_b64 in image_base64_list:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
            })

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.REVERSE_PROMPT_API_KEY}",
        }

        models_to_try = [settings.REVERSE_PROMPT_VISION_MODEL]
        fallback = getattr(settings, "REVERSE_PROMPT_VISION_MODEL_FALLBACK", "")
        if fallback and fallback != settings.REVERSE_PROMPT_VISION_MODEL:
            models_to_try.append(fallback)

        last_exc: Exception | None = None
        for model_idx, model in enumerate(models_to_try):
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": max_tokens,
            }
            started_at = time.perf_counter()
            logger.info(f"[Vision][START][TuZi] model={model} images={len(image_base64_list)}")

            for attempt in range(1, 3):
                try:
                    resp = requests.post(url, headers=headers, json=payload, timeout=(10, 120))
                    resp.raise_for_status()
                    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                    logger.info(f"[Vision][END][TuZi] model={model} elapsed_ms={elapsed_ms}")
                    data = resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if not text:
                        raise ValueError(f"Vision 返回空结果: {data}")
                    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text).strip()
                except requests.exceptions.RequestException as e:
                    last_exc = e
                    retryable = isinstance(
                        e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
                    )
                    if retryable and attempt < 2:
                        logger.warning(f"[Vision][RETRY][TuZi] model={model} attempt={attempt}")
                        time.sleep(2.0)
                        continue
                    if retryable and model_idx < len(models_to_try) - 1:
                        logger.warning(
                            f"[Vision][FALLBACK][TuZi] {model} 超时，切换 {models_to_try[model_idx + 1]}"
                        )
                    break
                except Exception as e:
                    last_exc = e
                    break

        raise RuntimeError(f"TuZi Vision 所有模型均失败: {last_exc}")


# Singleton
llm_client = LLMClient()
