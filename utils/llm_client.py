"""Unified LLM client.

路由优先级: AI导航 (GROUP_ID=13) → Reverse Prompt (tu-zi) 备选
"""

import json
import re
import time
import logging
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified LLM interface.

    路由:
    - ai_nav: AI导航 Gemini-3-flash (GROUP_ID=13)，异步任务模式
    - reverse_prompt / tuzi: tu-zi 中转 OpenAI 兼��接口（备选）
    - auto: AI导航优先 → tu-zi 备选
    """

    def call(
        self,
        system_prompt: str,
        user_message: str,
        *,
        preferred_llm: Optional[str] = None,
        preferred_route: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        json_mode: bool = True,
    ) -> str:
        """Call LLM and return raw text response.

        Args:
            preferred_llm: 'ai_nav' | 'reverse_prompt' | 'tuzi' | None (auto)
            preferred_route: 保留兼容，暂不使用
        """
        choice = (preferred_llm or "").strip().lower()

        if choice in ("ai_nav", "ainav"):
            return self._call_ai_nav(system_prompt, user_message, temperature, max_tokens, json_mode)
        if choice in ("reverse", "reverse_prompt", "tuzi"):
            return self._call_reverse_prompt(system_prompt, user_message, temperature, max_tokens, json_mode)

        # Auto: AI导航优先 → tu-zi 备选
        if settings.AI_NAV_TOKEN:
            try:
                return self._call_ai_nav(system_prompt, user_message, temperature, max_tokens, json_mode)
            except Exception as e:
                logger.warning(f"[LLM] AI导航调用失败，降级到 tu-zi: {e}")
                if settings.REVERSE_PROMPT_API_KEY:
                    return self._call_reverse_prompt(system_prompt, user_message, temperature, max_tokens, json_mode)
                raise

        if settings.REVERSE_PROMPT_API_KEY:
            return self._call_reverse_prompt(system_prompt, user_message, temperature, max_tokens, json_mode)

        raise ValueError("No LLM API configured. Set AI_NAV_TOKEN or REVERSE_PROMPT_API_KEY in .env")

    def call_vision(
        self,
        prompt: str,
        image_base64_list: list[str],
        *,
        preferred_llm: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """Call multimodal LLM with images (for compliance checking).

        走 reverse_prompt (tu-zi) 优先 — 稳定支持 Vision 多图。
        AI导航 GROUP_ID=13 备用（模型厂商不稳定时自动降级）。
        """
        use_reverse = (
            preferred_llm != "ai_nav"
            and settings.REVERSE_PROMPT_API_KEY
        )

        if use_reverse:
            return self._call_reverse_prompt_vision(prompt, image_base64_list, max_tokens)

        # AI导航 fallback
        from utils.ai_nav_client import AiNavClient
        client = AiNavClient(purpose="llm")
        image_urls = [f"data:image/png;base64,{img_b64}" for img_b64 in image_base64_list]
        task_id = client.create_llm_task(
            system_prompt="",
            user_message=prompt,
            image_urls=image_urls,
        )
        result = client.wait_for_task(task_id, timeout=180.0)
        text = result.get("result_text", "")
        if not text:
            raise ValueError(f"Vision 返回空结果: {result}")
        return text

    def _call_reverse_prompt_vision(
        self,
        prompt: str,
        image_base64_list: list[str],
        max_tokens: int = 4096,
    ) -> str:
        """Call tu-zi Vision via OpenAI-compatible multimodal messages.

        使用 REVERSE_PROMPT_VISION_MODEL（默认 gemini-2.5-flash-lite），比主模型更快。
        """
        base_url = settings.REVERSE_PROMPT_BASE_URL.rstrip("/")
        path = "/chat/completions"
        # REVERSE_PROMPT_BASE_URL 已含 /v1，path 不再重复
        model = settings.REVERSE_PROMPT_VISION_MODEL
        url = f"{base_url}{path}"

        content: list = [{"type": "text", "text": prompt}]
        for img_b64 in image_base64_list:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            })

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.REVERSE_PROMPT_API_KEY}",
        }

        started_at = time.perf_counter()
        logger.info(f"[Vision][START][ReversePrompt] images={len(image_base64_list)}")

        last_exc = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=(10, 300))
                resp.raise_for_status()
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                logger.info(f"[Vision][END][ReversePrompt] elapsed_ms={elapsed_ms} attempt={attempt}")
                break
            except requests.exceptions.RequestException as e:
                last_exc = e
                is_retryable = (
                    isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))
                    or (hasattr(e, "response") and e.response is not None and e.response.status_code in (429, 503))
                )
                if is_retryable and attempt < 3:
                    sleep_sec = 1.5 * (2 ** (attempt - 1))
                    logger.warning(f"[Vision][RETRY][ReversePrompt] attempt={attempt} sleep={sleep_sec:.1f}s")
                    time.sleep(sleep_sec)
                    continue
                raise
        else:
            raise last_exc

        data = resp.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not text:
            raise ValueError(f"Vision 返回空结果: {data}")
        return text

    # ── AI导航 (���步任务) ────────────────────────────

    def _call_ai_nav(
        self, system_prompt: str, user_message: str,
        temperature: float, max_tokens: int, json_mode: bool,
    ) -> str:
        """Call Gemini-3-flash via AI导航 GROUP_ID=13 异步任务，messages 格式。"""
        from utils.ai_nav_client import AiNavClient

        client = AiNavClient(purpose="llm")

        started_at = time.perf_counter()
        logger.info("[LLM][START][AiNav] Gemini-3-flash via AI导航")

        task_id = client.create_llm_task(
            system_prompt=system_prompt,
            user_message=user_message,
        )
        result = client.wait_for_task(task_id, timeout=180.0)

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(f"[LLM][END][AiNav] elapsed_ms={elapsed_ms} duration_ms={result.get('duration_ms', 0)}")

        text = result.get("result_text", "")
        if not text:
            raise ValueError(f"AI导航返回空结果: {result}")

        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text).strip()

    # ── Reverse Prompt (tu-zi) ──────────────────────

    def _call_reverse_prompt(
        self, system_prompt: str, user_message: str,
        temperature: float, max_tokens: int, json_mode: bool,
    ) -> str:
        """Call Reverse Prompt (tu-zi) OpenAI-compatible API."""
        base_url = settings.REVERSE_PROMPT_BASE_URL.rstrip("/")
        path = settings.REVERSE_PROMPT_PATH
        model = settings.REVERSE_PROMPT_MODEL
        url = f"{base_url}{path}"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.REVERSE_PROMPT_API_KEY}",
        }
        payload = {
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
        logger.info(f"[LLM][START][ReversePrompt] model={model} url={url}")

        last_exc = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=(10, 300))
                resp.raise_for_status()
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                logger.info(f"[LLM][END][ReversePrompt] model={model} elapsed_ms={elapsed_ms} attempt={attempt}")
                break
            except requests.exceptions.RequestException as e:
                last_exc = e
                is_retryable = (
                    isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))
                    or (hasattr(e, "response") and e.response is not None and e.response.status_code in (429, 503))
                )
                if is_retryable and attempt < 3:
                    sleep_sec = 1.5 * (2 ** (attempt - 1))
                    logger.warning(f"[LLM][RETRY][ReversePrompt] attempt={attempt} sleep={sleep_sec:.1f}s")
                    time.sleep(sleep_sec)
                    continue
                raise RuntimeError(f"ReversePrompt request failed: {e}") from e
        else:
            raise RuntimeError(f"ReversePrompt request failed after retries: {last_exc}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise ValueError(f"No choices in response: {data}")
        content = (choices[0].get("message") or {}).get("content", "").strip()
        if not content:
            raise ValueError(f"Empty content in response: {data}")
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)


# Singleton
llm_client = LLMClient()
