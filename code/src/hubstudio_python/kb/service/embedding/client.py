"""
OpenAI 兼容 ``/v1/embeddings`` 客户端（urllib，无额外依赖）。

用于离线生成向量；配置全部来自 ``HUBSTUDIO_EMBED_*`` 环境变量。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from urllib import request
from urllib.error import HTTPError, URLError


def _env_optional_bool(name: str) -> bool | None:
    """``HUBSTUDIO_EMBED_NORMALIZED`` 等：未设置返回 ``None``；非法值视为未设置。"""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding 服务端点与鉴权信息。"""

    base_url: str
    api_key: str
    model: str
    api_path: str = "/v1/embeddings"
    timeout_seconds: int = 60
    #: 为 ``True`` 时在 JSON 中加入 ``"normalized": true``（部分兼容网关要求与 Postman 一致）
    normalized: bool | None = None

    @classmethod
    def from_env(cls) -> "EmbeddingConfig":
        """必填：``HUBSTUDIO_EMBED_BASE_URL`` / ``API_KEY`` / ``MODEL``。"""
        base_url = os.environ.get("HUBSTUDIO_EMBED_BASE_URL", "").strip()
        api_key = os.environ.get("HUBSTUDIO_EMBED_API_KEY", "").strip()
        model = os.environ.get("HUBSTUDIO_EMBED_MODEL", "").strip()
        api_path = os.environ.get("HUBSTUDIO_EMBED_API_PATH", "/v1/embeddings").strip() or "/v1/embeddings"
        timeout_seconds = int(os.environ.get("HUBSTUDIO_EMBED_TIMEOUT_SECONDS", "60"))
        normalized = _env_optional_bool("HUBSTUDIO_EMBED_NORMALIZED")
        if not base_url:
            raise ValueError("Missing HUBSTUDIO_EMBED_BASE_URL")
        if not api_key:
            raise ValueError("Missing HUBSTUDIO_EMBED_API_KEY")
        if not model:
            raise ValueError("Missing HUBSTUDIO_EMBED_MODEL")
        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            api_path=api_path if api_path.startswith("/") else f"/{api_path}",
            timeout_seconds=max(5, timeout_seconds),
            normalized=normalized,
        )


def _embed_batch_size_from_env() -> int:
    """单请求最多 ``input`` 条数；过大时网关/服务端可能直接断连（``Remote end closed connection``）。"""
    raw = os.environ.get("HUBSTUDIO_EMBED_BATCH_SIZE", "64").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 64
    return max(1, min(n, 2048))


class EmbeddingClient:
    """对 Embedding API 的薄封装。"""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        嵌入多段文本；返回与 ``texts`` 顺序一致的向量列表。

        条数较多时按 ``HUBSTUDIO_EMBED_BATCH_SIZE``（默认 64）**分批 POST**，避免单次请求体过大被远端断连。

        响应格式遵循 OpenAI：``data[].index`` + ``data[].embedding``。
        """
        batch = _embed_batch_size_from_env()
        if len(texts) <= batch:
            return self._embed_texts_batch(texts)
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            part = texts[i : i + batch]
            out.extend(self._embed_texts_batch(part))
        return out

    def _embed_texts_batch(self, texts: list[str]) -> list[list[float]]:
        """单批 POST（``texts`` 非空）。"""
        if not texts:
            return []
        payload: dict[str, object] = {
            "model": self.config.model,
            "input": texts,
        }
        if self.config.normalized is not None:
            payload["normalized"] = bool(self.config.normalized)
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=f"{self.config.base_url.strip()}{self.config.api_path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
                # 部分 CDN/WAF 对 Python 默认 ``Python-urllib/x.y`` 返回 403
                "User-Agent": "hubstudio-rag-embed/1.0",
            },
        )
        url = f"{self.config.base_url.strip()}{self.config.api_path}"
        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw_bytes = response.read()
                raw = raw_bytes.decode("utf-8", errors="replace")
        except HTTPError as exc:
            raw_bytes = b""
            try:
                raw_bytes = exc.read()
            except Exception:
                pass
            text = raw_bytes.decode("utf-8", errors="replace")
            hdrs = getattr(exc, "headers", None)
            print(
                f"\n=== Embedding API HTTP {exc.code} POST {url} ===",
                file=sys.stderr,
                flush=True,
            )
            if hdrs is not None:
                print("--- response headers (as returned) ---", file=sys.stderr, flush=True)
                print(str(hdrs), file=sys.stderr, flush=True)
            print(
                f"--- response body (utf-8, errors=replace, {len(raw_bytes)} bytes) ---",
                file=sys.stderr,
                flush=True,
            )
            print(text, file=sys.stderr, flush=True)
            print("=== end Embedding API response ===\n", file=sys.stderr, flush=True)
            raise RuntimeError(
                f"Embedding request failed: HTTP {exc.code} POST {url}. "
                "Full response headers/body printed to stderr above."
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"Embedding request failed: could not reach {url!r}: {exc!r}. "
                "If you see Remote end closed connection: try lowering "
                "HUBSTUDIO_EMBED_BATCH_SIZE or config.yaml rag.embed.batch_size, then retry."
            ) from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                "\n=== Embedding API response is not valid JSON (printed raw below) ===",
                file=sys.stderr,
                flush=True,
            )
            print(raw, file=sys.stderr, flush=True)
            print("=== end ===\n", file=sys.stderr, flush=True)
            raise RuntimeError(
                f"Embedding response is not JSON: POST {url}. Raw body printed to stderr above."
            ) from exc
        data = parsed.get("data")
        if not isinstance(data, list):
            print(
                "\n=== Embedding API JSON parsed but missing/invalid `data` (raw body below) ===",
                file=sys.stderr,
                flush=True,
            )
            print(raw, file=sys.stderr, flush=True)
            print("=== end ===\n", file=sys.stderr, flush=True)
            raise ValueError(
                "Embedding response missing data list; full JSON printed to stderr above."
            )
        rows = sorted(data, key=lambda item: int(item.get("index", 0)))
        embeddings: list[list[float]] = []
        for row in rows:
            embedding = row.get("embedding")
            if not isinstance(embedding, list):
                raise ValueError("Embedding row missing embedding vector")
            embeddings.append([float(value) for value in embedding])
        return embeddings
