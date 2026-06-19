"""
项目路径、``config.yaml`` 与 ``.env``。

- ``load_paths()``：``rag_data/kb/``、``code/assets/``、``config.yaml``、``.env`` 路径。
- ``load_project_yaml()``：读取根目录 ``config.yaml``（不存在则空 dict）。
- ``apply_rag_settings_from_yaml()``：把 ``rag.embed`` / ``rag.chroma`` 写入 ``HUBSTUDIO_*`` 环境变量（仅当 YAML 里该项非空时写入，会覆盖已有同名变量）。
- ``load_env_file()``：解析 ``.env`` 写入环境（默认不覆盖已存在的变量）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


def _project_root() -> Path:
    """``code/src/hubstudio_python/config.py`` 向上三级为仓库根（``rag_data/`` 所在）。"""
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class PathsConfig:
    """构建流水线常用的目录与文件路径。"""

    project_root: Path
    rag_root: Path
    kb_root: Path
    input_dir: Path
    output_dir: Path
    assets_dir: Path
    runtime_kb_dir: Path
    schema_dir: Path
    manifest_file: Path
    incremental_update_file: Path
    config_file: Path
    env_file: Path


def bootstrap_rag_env() -> PathsConfig:
    """
    与 ``main.py`` 子命令一致：先 ``config.yaml`` 的 ``rag`` → 环境变量，再 ``.env`` 补缺。

    查询脚本、测试脚本在读取 ``ChromaConfig.from_env()`` 前应调用本函数，
    否则会落到内存 Chroma（``host/port/persist_dir`` 全空）且查不到已写入的数据。
    """
    paths = load_paths()
    project_yaml = load_project_yaml(paths.config_file)
    apply_rag_settings_from_yaml(project_yaml)
    from hubstudio_python.reply.sql.config import apply_db_settings_from_yaml

    apply_db_settings_from_yaml(project_yaml)
    from hubstudio_python.reply.sql.mysql_config import apply_mysql_settings_from_yaml

    apply_mysql_settings_from_yaml(project_yaml)
    load_env_file(paths.env_file)
    return paths


def _resolve_config_file(project_root: Path) -> Path:
    override = os.environ.get("HUBSTUDIO_CONFIG_FILE", "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else project_root / p
    code_cfg = project_root / "code" / "config.yaml"
    if code_cfg.is_file():
        return code_cfg
    legacy = project_root / "config.yaml"
    return legacy if legacy.is_file() else code_cfg


def load_paths() -> PathsConfig:
    """返回默认 RAG 目录布局（相对项目根）。"""
    from hubstudio_python.models.rag_layout import (
        assets_root,
        kb_input_dir,
        kb_incremental_file,
        kb_output_dir,
        kb_root,
        rag_root,
        runtime_kb_dir,
    )
    from hubstudio_python.models.schema_layout import schema_dir

    project_root = _project_root()
    rag = rag_root(base=project_root)
    kb = kb_root(base=project_root)
    input_dir = kb_input_dir(base=project_root)
    output_dir = kb_output_dir(base=project_root)
    assets = assets_root(base=project_root)
    manifest_file = output_dir / "manifest.json"
    incremental_update_file = kb_incremental_file(base=project_root)
    config_file = _resolve_config_file(project_root)
    env_file = project_root / ".env"
    return PathsConfig(
        project_root=project_root,
        rag_root=rag,
        kb_root=kb,
        input_dir=input_dir,
        output_dir=output_dir,
        assets_dir=assets,
        runtime_kb_dir=runtime_kb_dir(base=project_root),
        schema_dir=schema_dir(base=project_root),
        manifest_file=manifest_file,
        incremental_update_file=incremental_update_file,
        config_file=config_file,
        env_file=env_file,
    )


def load_project_yaml(config_path: Path) -> dict[str, Any]:
    """读取 ``config.yaml``；传入目录时解析为默认 config 路径。"""
    path = config_path
    if path.is_dir():
        path = _resolve_config_file(path)
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _yaml_str(value: object | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def apply_rag_settings_from_yaml(data: Mapping[str, Any] | None) -> None:
    """
    将 ``rag.embed`` / ``rag.chroma`` 映射到 ``HUBSTUDIO_EMBED_*`` / ``HUBSTUDIO_CHROMA_*``。

    仅当 YAML 中对应键为非空字符串时才 ``os.environ[...] = ...``（会覆盖已有环境变量）。
    """
    if not data:
        return
    rag = data.get("rag")
    if not isinstance(rag, dict):
        return

    embed = rag.get("embed")
    if isinstance(embed, dict):
        pairs = [
            ("HUBSTUDIO_EMBED_BASE_URL", embed.get("base_url")),
            ("HUBSTUDIO_EMBED_API_PATH", embed.get("api_path")),
            ("HUBSTUDIO_EMBED_API_KEY", embed.get("api_key")),
            ("HUBSTUDIO_EMBED_MODEL", embed.get("model")),
        ]
        for env_name, raw in pairs:
            v = _yaml_str(raw)
            if v is not None:
                os.environ[env_name] = v
        norm = embed.get("normalized")
        if isinstance(norm, bool):
            os.environ["HUBSTUDIO_EMBED_NORMALIZED"] = "true" if norm else "false"
        elif norm is not None:
            v = _yaml_str(norm)
            if v is not None:
                os.environ["HUBSTUDIO_EMBED_NORMALIZED"] = v
        to = embed.get("timeout_seconds")
        if to is not None:
            v = _yaml_str(to)
            if v is not None:
                os.environ["HUBSTUDIO_EMBED_TIMEOUT_SECONDS"] = v
        bs = embed.get("batch_size")
        if isinstance(bs, int):
            os.environ["HUBSTUDIO_EMBED_BATCH_SIZE"] = str(max(1, bs))
        elif bs is not None:
            v = _yaml_str(bs)
            if v is not None:
                os.environ["HUBSTUDIO_EMBED_BATCH_SIZE"] = v

    chroma = rag.get("chroma")
    if isinstance(chroma, dict):
        for env_name, key in (
            ("HUBSTUDIO_CHROMA_COLLECTION", "collection"),
            ("HUBSTUDIO_CHROMA_PERSIST_DIR", "persist_dir"),
            ("HUBSTUDIO_CHROMA_HOST", "host"),
            ("HUBSTUDIO_CHROMA_PORT", "port"),
        ):
            v = _yaml_str(chroma.get(key))
            if v is not None:
                if key == "persist_dir" and not Path(v).is_absolute():
                    v = str((load_paths().project_root / v).resolve())
                os.environ[env_name] = v
        cto = chroma.get("timeout_seconds")
        if cto is not None:
            v = _yaml_str(cto)
            if v is not None:
                os.environ["HUBSTUDIO_CHROMA_TIMEOUT_SECONDS"] = v

        ar = chroma.get("allow_reset")
        if isinstance(ar, bool):
            os.environ["HUBSTUDIO_CHROMA_ALLOW_RESET"] = "true" if ar else "false"
        elif ar is not None and _yaml_str(ar) is not None:
            os.environ["HUBSTUDIO_CHROMA_ALLOW_RESET"] = _yaml_str(ar)  # type: ignore[arg-type]

        ssl_raw = chroma.get("ssl")
        if isinstance(ssl_raw, bool):
            os.environ["HUBSTUDIO_CHROMA_SSL"] = "true" if ssl_raw else "false"
        elif ssl_raw is not None and _yaml_str(ssl_raw) is not None:
            os.environ["HUBSTUDIO_CHROMA_SSL"] = _yaml_str(ssl_raw)  # type: ignore[arg-type]

        for env_key, yaml_key in (
            ("HUBSTUDIO_CHROMA_TENANT", "tenant"),
            ("HUBSTUDIO_CHROMA_DATABASE", "database"),
        ):
            v = _yaml_str(chroma.get(yaml_key))
            if v is not None:
                os.environ[env_key] = v

        headers = chroma.get("headers")
        if isinstance(headers, dict) and headers:
            os.environ["HUBSTUDIO_CHROMA_HEADERS_JSON"] = json.dumps(
                {str(k): str(v) for k, v in headers.items()},
                ensure_ascii=False,
            )


def load_env_file(env_file: Path, override: bool = False) -> int:
    """
    从 ``.env`` 加载变量到进程环境。

    :param override: 为 True 时用文件中的值覆盖已存在的环境变量。
    :return: 实际写入的条目数量。
    """
    if not env_file.exists():
        return 0

    loaded = 0
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        name = key.strip()
        if not name:
            continue
        parsed = value.strip()
        # 支持可选的整行引号包裹
        if len(parsed) >= 2 and parsed[0] == parsed[-1] and parsed[0] in {"'", '"'}:
            parsed = parsed[1:-1]
        if not override and name in os.environ:
            continue
        os.environ[name] = parsed
        loaded += 1
    return loaded
