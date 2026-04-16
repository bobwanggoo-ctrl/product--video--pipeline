"""Unified LLM client — 所有路线走 AI导航 group=13。"""

import json
import re
import time
import logging
from typing import Optional

import requests

from config import settings

from utils.json_repair import normalize_llm_text

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
        """Call LLM (text only) — 走 AI导航 group=13，tu-zi 已停用。"""
        # AI导航（唯一路线）
        if settings.AI_NAV_TOKEN:
            return self._call_ai_nav_with_retry(
                system_prompt, user_message, temperature, max_tokens, json_mode
            )
        raise ValueError("AI_NAV_TOKEN 未配置，请在 .env 中设置")

    def call_vision(
        self,
        prompt: str,
        image_base64_list: list[str],
        *,
        preferred_llm: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """Call multimodal LLM with images.

        路线：skill 上传图片 → CDN URL → skill stream（group=13 Vision）。
        stream 接口比 tasks 轮询快，且支持带图请求（imageUrls 字段）。
        """
        if not settings.AI_NAV_TOKEN:
            raise ValueError("AI_NAV_TOKEN 未配置")

        # 1. 上传图片拿 CDN URL
        cdn_urls = self._upload_images_via_skill(image_base64_list)
        if not cdn_urls:
            raise RuntimeError("Vision 图片上传失败")

        # 2. skill stream 带图请求（直接流式输出，无需轮询）
        return self._call_vision_via_skill_stream(prompt, cdn_urls)

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

    def _call_vision_via_skill_stream(self, prompt: str, image_urls: list[str]) -> str:
        """用 navigation-ai skill stream 命令发带图请求（group=13 Vision），含重试。"""
        import subprocess
        from pathlib import Path

        skill_script = Path.home() / ".claude" / "skills" / "navigation-ai" / "scripts" / "main.ts"
        cmd = [
            "npx", "-y", "bun", str(skill_script),
            "stream", "--group-id", "13",
            "--user", prompt,
        ]
        for url in image_urls:
            cmd += ["--image-url-add", url]

        for attempt in range(1, 4):
            started_at = time.perf_counter()
            logger.info(f"[Vision][START][Skill+Stream] images={len(image_urls)} attempt={attempt}")

            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)

            if r.returncode == 0:
                text = "\n".join(
                    line for line in r.stdout.splitlines()
                    if not line.startswith("[navigation-ai]")
                ).strip()
                if text:
                    logger.info(f"[Vision][END][Skill+Stream] elapsed_ms={elapsed_ms}")
                    return normalize_llm_text(text)

            logger.warning(f"[Vision][Skill+Stream] attempt={attempt} 失败: {r.stderr[:200]}")
            if attempt < 3:
                time.sleep(2.0)

        raise RuntimeError("skill stream Vision 3 次均失败")

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
        return normalize_llm_text(content)

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
                    return normalize_llm_text(text)
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
