"""
中文切片英译：调用 DeepSeek（OpenAI 兼容 Chat Completions）填充 ``entitle`` / ``encontent``。

- **批量**：默认每批多条切片一次请求（``HUBSTUDIO_CHUNK_TRANSLATE_BATCH_SIZE``），减少连接次数；
  整批失败时自动 **回退为逐条** 翻译。
- **重试**：网络断开、``HTTP 429/502/503/504`` 等会按 ``HUBSTUDIO_CHUNK_TRANSLATE_RETRIES`` 指数退避重试。

配置优先读项目根 ``config.yaml`` 的 ``ai`` 与 ``ai.chunk_translate``；若无 ``ai`` 节再读环境变量
``HUBSTUDIO_CHUNK_TRANSLATE_*``。未配置有效 API Key 时不请求。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping
from urllib import error, request

from hubstudio_python.models import KnowledgeChunk

logger = logging.getLogger(__name__)

def api_key_from_ai_section(ai: Mapping[str, Any]) -> str:
    """``api_key_env`` 优先从 ``os.environ`` 取值；否则 ``api_key`` 明文。"""
    env_name = ai.get("api_key_env")
    if env_name is not None and str(env_name).strip():
        return os.environ.get(str(env_name).strip(), "").strip()
    raw = ai.get("api_key")
    if raw is None:
        return ""
    return str(raw).strip()

_SYSTEM_SINGLE = (
    "You translate Chinese customer-support knowledge into clear English. "
    "Preserve every placeholder exactly as in the source, e.g. {Broker_Name}, {Community_Link}—"
    "do not translate or alter text inside curly braces. "
    "Reply with ONLY a JSON object, no markdown fences: "
    '{"entitle": "<English title>", "encontent": "<English body>"}'
)

_SYSTEM_BATCH = (
    "You translate Chinese customer-support knowledge into clear English. "
    "The user message is a JSON array; each element has \"id\", \"title\", \"content\" (Chinese). "
    "Preserve placeholders like {Broker_Name} exactly—never translate inside {}. "
    "Reply with ONLY a JSON array of the same length and the same order as input. "
    "Each element: {\"id\": \"<exact same id>\", \"entitle\": \"...\", \"encontent\": \"...\"}. "
    "No markdown code fences."
)


def _env_first(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


def _require_str(name: str, value: str) -> str:
    if not value:
        raise ValueError(f"Missing or empty configuration: {name}")
    return value


def _require_int(name: str, raw: str) -> int:
    try:
        return int(raw.strip(), 10)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _require_float(name: str, raw: str) -> float:
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _is_retryable_http(code: int) -> bool:
    return code in {429, 502, 503, 504}


def _is_retryable_network(exc: BaseException) -> bool:
    if isinstance(exc, error.URLError):
        return True
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) == 10054:
            return True
        if getattr(exc, "errno", None) in {104, 110, 111, 32, 10054}:
            return True
    return False


@dataclass(frozen=True)
class ChunkTranslateConfig:
    base_url: str
    api_key: str
    model: str
    api_path: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    batch_size: int
    max_retries: int
    batch_sleep_seconds: float

    @classmethod
    def from_ai_yaml_section(cls, ai: Mapping[str, Any]) -> ChunkTranslateConfig | None:
        """从 ``config.yaml`` 的 ``ai`` 映射构造（``base_url`` / ``api_key`` / ``model`` 等）。"""
        api_key = api_key_from_ai_section(ai)
        if not api_key:
            return None
        base_url = str(ai.get("base_url", "")).strip().rstrip("/")
        if not base_url:
            raise ValueError("config.yaml ai.base_url is required when ai.api_key is set")
        model_top = str(ai.get("model", "")).strip()
        if not model_top:
            raise ValueError("config.yaml ai.model is required when ai.api_key is set")
        api_path = str(ai.get("api_path", "/v1/chat/completions")).strip()
        if not api_path.startswith("/"):
            api_path = f"/{api_path}"

        ct = ai.get("chunk_translate")
        if not isinstance(ct, dict):
            ct = {}

        def _int(v: object, default: int) -> int:
            if v is None:
                return default
            return int(v)

        model = str(ct.get("model") or model_top).strip()
        t_raw = ct.get("temperature")
        temperature = float(t_raw) if t_raw is not None else float(ai.get("temperature", 0.2))
        m_raw = ct.get("max_tokens")
        if m_raw is not None:
            max_tokens = int(m_raw)
        else:
            max_tokens = max(int(ai.get("max_tokens", 1024)), 2048)
        to_raw = ct.get("request_timeout_ms")
        timeout_ms = int(to_raw) if to_raw is not None else int(ai.get("request_timeout_ms", 120000))
        if timeout_ms <= 0:
            raise ValueError("ai.chunk_translate.request_timeout_ms / ai.request_timeout_ms must be positive")

        batch_size = max(1, _int(ct.get("batch_size"), 8))
        max_retries = max(1, _int(ct.get("retries"), 5))
        batch_sleep_seconds = max(0.0, _int(ct.get("batch_sleep_ms"), 0) / 1000.0)

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            api_path=api_path,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_ms / 1000.0,
            batch_size=batch_size,
            max_retries=max_retries,
            batch_sleep_seconds=batch_sleep_seconds,
        )

    @classmethod
    def try_from_env(cls) -> ChunkTranslateConfig | None:
        api_key = _env_first("HUBSTUDIO_CHUNK_TRANSLATE_API_KEY")
        if not api_key:
            return None
        base_url = _require_str(
            "base_url",
            _env_first("HUBSTUDIO_CHUNK_TRANSLATE_BASE_URL"),
        ).rstrip("/")
        model = _require_str(
            "model",
            _env_first("HUBSTUDIO_CHUNK_TRANSLATE_MODEL"),
        )
        api_path = _env_first(
            "HUBSTUDIO_CHUNK_TRANSLATE_API_PATH",
            "/v1/chat/completions",
        )
        if not api_path.startswith("/"):
            api_path = f"/{api_path}"
        temp_raw = _env_first(
            "HUBSTUDIO_CHUNK_TRANSLATE_TEMPERATURE",
            "0.2",
        )
        temperature = _require_float("temperature", temp_raw)
        max_t_raw = os.environ.get("HUBSTUDIO_CHUNK_TRANSLATE_MAX_TOKENS", "").strip()
        if max_t_raw:
            max_tokens = max(_require_int("HUBSTUDIO_CHUNK_TRANSLATE_MAX_TOKENS", max_t_raw), 2048)
        else:
            max_tokens = 4096
        timeout_ms_raw = _env_first(
            "HUBSTUDIO_CHUNK_TRANSLATE_REQUEST_TIMEOUT_MS",
            "120000",
        )
        timeout_ms = _require_int("timeout_ms", timeout_ms_raw)
        timeout_seconds = timeout_ms / 1000.0
        if timeout_seconds <= 0:
            raise ValueError("Chunk translate timeout must be positive")
        batch_raw = os.environ.get("HUBSTUDIO_CHUNK_TRANSLATE_BATCH_SIZE", "8").strip() or "8"
        batch_size = max(1, _require_int("HUBSTUDIO_CHUNK_TRANSLATE_BATCH_SIZE", batch_raw))
        retry_raw = os.environ.get("HUBSTUDIO_CHUNK_TRANSLATE_RETRIES", "5").strip() or "5"
        max_retries = max(1, _require_int("HUBSTUDIO_CHUNK_TRANSLATE_RETRIES", retry_raw))
        sleep_ms_raw = os.environ.get("HUBSTUDIO_CHUNK_TRANSLATE_BATCH_SLEEP_MS", "0").strip() or "0"
        batch_sleep_seconds = max(0.0, _require_int("HUBSTUDIO_CHUNK_TRANSLATE_BATCH_SLEEP_MS", sleep_ms_raw) / 1000.0)
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            api_path=api_path,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            batch_size=batch_size,
            max_retries=max_retries,
            batch_sleep_seconds=batch_sleep_seconds,
        )


def _strip_markdown_json_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    t = re.sub(r"^```(?:json)?\s*", "", t, count=1, flags=re.IGNORECASE)
    t = re.sub(r"\s*```\s*$", "", t, count=1)
    return t.strip()


def _post_chat(cfg: ChunkTranslateConfig, system: str, user_text: str) -> str:
    payload = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=f"{cfg.base_url}{cfg.api_path}",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
            "Connection": "close",
        },
    )
    with request.urlopen(req, timeout=cfg.timeout_seconds) as response:
        raw = response.read().decode("utf-8")

    parsed = json.loads(raw)
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Chunk translate response missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("Chunk translate response missing message")
    text = message.get("content")
    if not isinstance(text, str):
        raise ValueError("Chunk translate response missing content")
    return text.strip()


def _post_chat_with_retry(cfg: ChunkTranslateConfig, system: str, user_text: str) -> str:
    last: BaseException | None = None
    for attempt in range(cfg.max_retries):
        try:
            return _post_chat(cfg, system, user_text)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last = exc
            if _is_retryable_http(exc.code) and attempt + 1 < cfg.max_retries:
                delay = min(60.0, 1.5**attempt)
                logger.warning(
                    "Chunk translate HTTP %s, retry %s/%s in %.1fs: %s",
                    exc.code,
                    attempt + 1,
                    cfg.max_retries,
                    delay,
                    detail[:300],
                )
                time.sleep(delay)
                continue
            raise ValueError(f"Chunk translate HTTP {exc.code}: {detail}") from exc
        except (error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt + 1 < cfg.max_retries and _is_retryable_network(exc):
                delay = min(60.0, 1.5**attempt)
                logger.warning(
                    "Chunk translate network error %s, retry %s/%s in %.1fs",
                    exc,
                    attempt + 1,
                    cfg.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue
            raise
    assert last is not None
    raise last


def _translate_one(cfg: ChunkTranslateConfig, title: str, content: str) -> tuple[str, str]:
    user = (
        "Translate the following fields from Chinese to English.\n\n"
        f"title:\n{title}\n\ncontent:\n{content}\n"
    )
    raw = _post_chat_with_retry(cfg, _SYSTEM_SINGLE, user)
    raw = _strip_markdown_json_fence(raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    et = data.get("entitle")
    ec = data.get("encontent")
    if not isinstance(et, str) or not isinstance(ec, str):
        raise ValueError("JSON must have string entitle and encontent")
    return et.strip(), ec.strip()


def _translate_batch(cfg: ChunkTranslateConfig, batch: list[KnowledgeChunk]) -> dict[str, tuple[str, str]]:
    if len(batch) == 1:
        c = batch[0]
        et, ec = _translate_one(cfg, c.title, c.content)
        return {c.id: (et, ec)}

    items = [{"id": c.id, "title": c.title, "content": c.content} for c in batch]
    user = json.dumps(items, ensure_ascii=False)
    raw = _post_chat_with_retry(cfg, _SYSTEM_BATCH, user)
    raw = _strip_markdown_json_fence(raw)
    arr = json.loads(raw)
    if not isinstance(arr, list):
        raise ValueError("batch translate: expected JSON array")
    if len(arr) != len(batch):
        raise ValueError(f"batch translate: expected {len(batch)} items, got {len(arr)}")
    expected = {c.id for c in batch}
    out: dict[str, tuple[str, str]] = {}
    for item in arr:
        if not isinstance(item, dict):
            raise ValueError("batch translate: array element not object")
        cid = item.get("id")
        et = item.get("entitle")
        ec = item.get("encontent")
        if not isinstance(cid, str) or not isinstance(et, str) or not isinstance(ec, str):
            raise ValueError("batch translate: bad field types")
        out[cid] = (et.strip(), ec.strip())
    if set(out.keys()) != expected:
        raise ValueError(f"batch translate: id mismatch missing={expected - set(out)} extra={set(out) - expected}")
    return out


def enrich_chunks_zh_with_deepseek(
    chunks: list[KnowledgeChunk],
    cfg: ChunkTranslateConfig | None = None,
) -> list[KnowledgeChunk]:
    """对 ``language`` 为 ``zh`` 的切片批量英译并写回 ``entitle`` / ``encontent``。

    ``cfg`` 来自 ``config.yaml`` 的 ``ai``；为 ``None`` 时用 ``ChunkTranslateConfig.try_from_env()``。
    """
    resolved = cfg if cfg is not None else ChunkTranslateConfig.try_from_env()
    if resolved is None:
        logger.info(
            "Chunk EN translate skipped: add ai.api_key to config.yaml or set "
            "HUBSTUDIO_CHUNK_TRANSLATE_API_KEY"
        )
        return chunks

    zh_list = [c for c in chunks if c.language == "zh"]
    translations: dict[str, tuple[str, str]] = {}
    bs = resolved.batch_size

    for start in range(0, len(zh_list), bs):
        batch = zh_list[start : start + bs]
        try:
            translations.update(_translate_batch(resolved, batch))
            logger.info(
                "Translated zh batch (%s chunks): first id=%s",
                len(batch),
                batch[0].id,
            )
        except Exception as exc:
            logger.warning(
                "Batch translate failed (%s chunks), fallback to one-by-one: %s",
                len(batch),
                exc,
            )
            for c in batch:
                try:
                    et, ec = _translate_one(resolved, c.title, c.content)
                    translations[c.id] = (et, ec)
                    logger.info("Translated chunk %s → EN (fallback)", c.id)
                except Exception as exc2:
                    logger.warning("Chunk %s EN translate failed: %s", c.id, exc2)
        if resolved.batch_sleep_seconds > 0 and start + bs < len(zh_list):
            time.sleep(resolved.batch_sleep_seconds)

    out: list[KnowledgeChunk] = []
    for c in chunks:
        if c.language != "zh":
            out.append(c)
            continue
        pair = translations.get(c.id)
        if pair:
            out.append(replace(c, entitle=pair[0], encontent=pair[1]))
        else:
            out.append(c)
    return out
