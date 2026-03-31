"""Unified LLM client supporting Gemini (dual-auth) and DeepSeek/OpenAI."""

import json
import os
import re
import time
import logging
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified LLM interface with Gemini (service + proxy) and OpenAI/DeepSeek support."""

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
            preferred_llm: 'gemini' | 'deepseek' | 'openai' | None (auto: Gemini first)
            preferred_route: 'service' | 'proxy' | 'auto' (Gemini only)
        """
        choice = (preferred_llm or "").strip().lower()

        if choice in ("gemini", "google"):
            return self._call_gemini(system_prompt, user_message, preferred_route, temperature, max_tokens, json_mode)
        if choice in ("deepseek", "openai"):
            return self._call_openai(system_prompt, user_message, temperature, max_tokens, json_mode)

        # Auto: Gemini first, then OpenAI/DeepSeek
        if settings.GEMINI_API_KEY or settings.GEMINI_SELLPOINT_APP_KEY:
            return self._call_gemini(system_prompt, user_message, preferred_route, temperature, max_tokens, json_mode)
        if settings.OPENAI_API_KEY:
            return self._call_openai(system_prompt, user_message, temperature, max_tokens, json_mode)

        raise ValueError("No LLM API key configured. Set GEMINI_API_KEY or OPENAI_API_KEY in .env")

    def call_vision(
        self,
        prompt: str,
        image_base64_list: list[str],
        *,
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """Call multimodal LLM with images (for compliance checking).

        Uses Gemini Vision API via google-genai SDK.
        """
        from google import genai
        from google.genai import types
        import base64

        api_key = model or settings.GEMINI_VISION_API_KEY or settings.GEMINI_API_KEY
        vision_model = settings.GEMINI_VISION_MODEL or settings.GEMINI_MODEL

        if not api_key:
            raise ValueError("No Gemini Vision API key configured.")

        client = genai.Client(api_key=api_key)

        # Build content parts: text prompt + images
        parts = [types.Part.from_text(text=prompt)]
        for img_b64 in image_base64_list:
            img_bytes = base64.b64decode(img_b64)
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))

        started_at = time.perf_counter()
        logger.info(f"[LLM][START][Vision] model={vision_model} images={len(image_base64_list)}")

        response = client.models.generate_content(
            model=vision_model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.2,
            ),
        )

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(f"[LLM][END][Vision] model={vision_model} elapsed_ms={elapsed_ms}")

        return response.text

    def _call_gemini(
        self, system_prompt: str, user_message: str,
        preferred_route: Optional[str], temperature: float,
        max_tokens: int, json_mode: bool,
    ) -> str:
        """Call Gemini via service interface (App-Key polling) or proxy (Bearer token)."""
        has_service = bool(
            settings.GEMINI_SELLPOINT_APP_KEY
            and settings.GEMINI_SELLPOINT_APP_SECRET
            and settings.GEMINI_SELLPOINT_SERVICE_ID
            and settings.GEMINI_SELLPOINT_BASE_URL
        )
        has_proxy = bool(settings.GEMINI_API_KEY)
        route = (preferred_route or "auto").strip().lower()

        use_service = (route == "service" and has_service) or (route == "auto" and has_service)

        if use_service:
            return self._call_gemini_service(system_prompt, user_message)

        if not has_proxy:
            raise ValueError("No Gemini API key available for proxy route.")
        return self._call_gemini_proxy(system_prompt, user_message, temperature, max_tokens, json_mode)

    def _call_gemini_service(self, system_prompt: str, user_message: str) -> str:
        """Gemini via service interface with App-Key auth and task polling."""
        base_url = settings.GEMINI_SELLPOINT_BASE_URL.rstrip("/")
        invoke_url = f"{base_url}/api/admin/api/v1/ai/service/invoke/{settings.GEMINI_SELLPOINT_SERVICE_ID}"
        task_base_url = f"{base_url}/api/admin/api/v1/ai/service/tasks"

        headers = {
            "Content-Type": "application/json",
            "App-Key": settings.GEMINI_SELLPOINT_APP_KEY,
            "App-Secret": settings.GEMINI_SELLPOINT_APP_SECRET,
        }
        payload = {
            "params": {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            },
            "clientTraceId": f"pipeline-{int(time.time() * 1000)}",
        }

        started_at = time.perf_counter()
        logger.info(f"[LLM][START][GeminiService] url={invoke_url}")

        resp = requests.post(invoke_url, headers=headers, json=payload, timeout=(10, 120))
        resp.raise_for_status()
        task_id = (resp.json().get("data") or {}).get("taskId")
        if not task_id:
            raise ValueError(f"No taskId returned: {resp.json()}")

        # Poll for completion
        deadline = time.time() + settings.GEMINI_SELLPOINT_POLL_TIMEOUT
        while time.time() < deadline:
            status_resp = requests.get(f"{task_base_url}/{task_id}", headers=headers, timeout=(10, 30))
            status_resp.raise_for_status()
            data = (status_resp.json().get("data") or {})
            status = data.get("status")

            if status in (2, "2"):
                result = data.get("result", {})
                content = self._extract_service_content(result)
                if content:
                    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                    logger.info(f"[LLM][END][GeminiService] task_id={task_id} elapsed_ms={elapsed_ms}")
                    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content).strip()
                raise ValueError(f"Task succeeded but empty result: {data}")

            if status in (3, 4, "3", "4"):
                raise RuntimeError(f"Task failed: {data.get('failReason', 'unknown')}")

            time.sleep(settings.GEMINI_SELLPOINT_POLL_INTERVAL)

        raise TimeoutError(f"Poll timeout for task {task_id}")

    def _extract_service_content(self, result) -> Optional[str]:
        """Extract text content from service interface result."""
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            content = result.get("content") or result.get("raw") or result.get("text")
            if not content and result.get("choices"):
                try:
                    content = result["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    pass
            if not content:
                str_values = [v for v in result.values() if isinstance(v, str) and len(v) > 50]
                if str_values:
                    content = max(str_values, key=len)
            if not content and "scene_groups" in result:
                content = json.dumps(result, ensure_ascii=False)
            return content
        if isinstance(result, list):
            return json.dumps(result, ensure_ascii=False)
        return None

    def _call_gemini_proxy(
        self, system_prompt: str, user_message: str,
        temperature: float, max_tokens: int, json_mode: bool,
    ) -> str:
        """Call Gemini via proxy with Bearer token auth."""
        base_url = settings.GEMINI_BASE_URL.rstrip("/")
        model = settings.GEMINI_MODEL
        url = f"{base_url}/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.GEMINI_API_KEY}",
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
        logger.info(f"[LLM][START][Gemini] model={model} url={url}")

        last_exc = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=(10, 120))
                resp.raise_for_status()
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                logger.info(f"[LLM][END][Gemini] model={model} elapsed_ms={elapsed_ms} attempt={attempt}")
                break
            except requests.exceptions.RequestException as e:
                last_exc = e
                is_retryable = (
                    isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))
                    or (hasattr(e, "response") and e.response is not None and e.response.status_code in (429, 503))
                )
                if is_retryable and attempt < 3:
                    sleep_sec = 1.5 * (2 ** (attempt - 1))
                    logger.warning(f"[LLM][RETRY][Gemini] attempt={attempt} sleep={sleep_sec:.1f}s")
                    time.sleep(sleep_sec)
                    continue
                raise RuntimeError(f"Gemini proxy request failed: {e}") from e
        else:
            raise RuntimeError(f"Gemini proxy request failed after retries: {last_exc}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise ValueError(f"No choices in response: {data}")
        content = (choices[0].get("message") or {}).get("content", "").strip()
        if not content:
            raise ValueError(f"Empty content in response: {data}")
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)

    def _call_openai(
        self, system_prompt: str, user_message: str,
        temperature: float, max_tokens: int, json_mode: bool,
    ) -> str:
        """Call OpenAI/DeepSeek compatible API."""
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL or "https://api.openai.com/v1",
            timeout=120,
        )
        model = settings.OPENAI_MODEL

        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        started_at = time.perf_counter()
        logger.info(f"[LLM][START][OpenAI] model={model}")

        response = client.chat.completions.create(**kwargs)

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(f"[LLM][END][OpenAI] model={model} elapsed_ms={elapsed_ms}")

        if not response.choices:
            raise ValueError("OpenAI returned empty choices.")
        content = response.choices[0].message.content
        if not content:
            raise ValueError("OpenAI returned empty content.")
        return content


# Singleton
llm_client = LLMClient()
