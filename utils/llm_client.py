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

    路由：所有路线走 AI导航 group=13。
    Vision：skill upload → CDN URL（有缓存，同图只传一次）→ skill stream。
    """

    # 进程级 CDN URL 缓存：同一张图在整个 pipeline run 中只上传一次
    _upload_cache: dict[str, str] = {}

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
        import subprocess, tempfile, base64, hashlib, json
        from pathlib import Path
        from concurrent.futures import ThreadPoolExecutor

        skill_script = Path(__file__).resolve().parent.parent / "tools" / "navigation-ai" / "main.ts"
        if not skill_script.exists():
            # 兼容旧路径
            skill_script = Path.home() / ".claude" / "skills" / "navigation-ai" / "scripts" / "main.ts"
        if not skill_script.exists():
            return []

        # 结果槽：先用 None 占位
        results: list[str | None] = [None] * len(image_base64_list)

        # 快速 cache key：MD5(前 2000 字节 base64)，足够唯一
        def _cache_key(b64: str) -> str:
            return hashlib.md5(b64[:2000].encode()).hexdigest()

        # 查缓存 — 已上传过的图直接复用 CDN URL
        to_upload: list[tuple[int, str, str]] = []  # (index, b64, cache_key)
        for i, b64 in enumerate(image_base64_list):
            key = _cache_key(b64)
            if key in LLMClient._upload_cache:
                results[i] = LLMClient._upload_cache[key]
                logger.debug(f"[Vision] upload cache hit idx={i}")
            else:
                to_upload.append((i, b64, key))

        if not to_upload:
            return results  # type: ignore[return-value]

        # 并发上传（临时文件各自独立，避免竞争）
        def _upload_one(idx: int, b64: str, key: str) -> tuple[int, str]:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp.write(base64.b64decode(b64))
                tmp_path = tmp.name
            try:
                r = subprocess.run(
                    ["npx", "-y", "bun", str(skill_script),
                     "upload", "--image-file", tmp_path, "--json"],
                    capture_output=True, text=True, timeout=30,
                )
                if r.returncode == 0:
                    url = json.loads(r.stdout.strip()).get("url", "")
                    if url:
                        LLMClient._upload_cache[key] = url
                        return idx, url
            except Exception as e:
                logger.warning(f"[Vision] upload failed idx={idx}: {e}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            return idx, ""

        max_w = min(len(to_upload), 4)
        logger.debug(f"[Vision] uploading {len(to_upload)} images (workers={max_w}, cached={len(image_base64_list)-len(to_upload)})")
        with ThreadPoolExecutor(max_workers=max_w) as pool:
            for idx, url in pool.map(lambda t: _upload_one(*t), to_upload):
                if url:
                    results[idx] = url

        if any(r is None for r in results):
            logger.warning("[Vision] 部分图片上传失败")
            return []

        return results  # type: ignore[return-value]

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
                    logger.warning(f"[LLM][AiNav] attempt={attempt} 失败，{delay}s 后重试: {e}")
                    time.sleep(delay)
        raise RuntimeError(f"AI导航文本调用 {_AINAV_MAX_ATTEMPTS} 次全部失败: {last_exc}") from last_exc

    def _call_ai_nav_vision_with_retry(
        self,
        prompt: str,
        image_base64_list: list[str],
        max_tokens: int,
        image_urls: list[str] | None = None,
    ) -> str:
        """AI导航 Vision 调用，失败时指数退避重试最多 _AINAV_MAX_ATTEMPTS 次。"""
        last_exc: Exception | None = None
        for attempt in range(1, _AINAV_MAX_ATTEMPTS + 1):
            try:
                return self._call_ai_nav_vision(
                    prompt, image_base64_list, max_tokens, image_urls=image_urls
                )
            except Exception as e:
                last_exc = e
                if attempt < _AINAV_MAX_ATTEMPTS:
                    delay = _AINAV_RETRY_DELAYS[attempt - 1]
                    logger.warning(f"[Vision][AiNav] attempt={attempt} 失败，{delay}s 后重试: {e}")
                    time.sleep(delay)
        raise RuntimeError(f"AI导航 Vision 调用 {_AINAV_MAX_ATTEMPTS} 次全部失败: {last_exc}") from last_exc

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

        return normalize_llm_text(text)

    def _call_ai_nav_vision(
        self,
        prompt: str,
        image_base64_list: list[str],
        max_tokens: int,
        image_urls: list[str] | None = None,
    ) -> str:
        """单次 AI导航 Vision 调用（CDN URL 优先，base64 兜底）。"""
        from utils.ai_nav_client import AiNavClient

        client = AiNavClient(purpose="llm")
        started_at = time.perf_counter()

        urls = image_urls or [
            f"data:image/jpeg;base64,{img}" for img in image_base64_list
        ]
        logger.info(f"[Vision][START][AiNav] images={len(urls)} url_mode={bool(image_urls)}")

        task_id = client.create_llm_task(
            system_prompt="",
            user_message=prompt,
            image_urls=urls,
        )
        result = client.wait_for_task(task_id, timeout=240.0)

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(f"[Vision][END][AiNav] elapsed_ms={elapsed_ms}")

        text = result.get("result_text", "")
        if not text:
            raise ValueError(f"AI导航 Vision 返回空结果: {result}")

        return normalize_llm_text(text)

    def _call_vision_via_skill_stream(self, prompt: str, image_urls: list[str]) -> str:
        """用 navigation-ai skill stream 命令发带图请求（group=13 Vision），含重试。"""
        import subprocess
        from pathlib import Path

        skill_script = Path(__file__).resolve().parent.parent / "tools" / "navigation-ai" / "main.ts"
        if not skill_script.exists():
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
