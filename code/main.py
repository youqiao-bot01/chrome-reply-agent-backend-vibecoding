"""
仓库根目录入口：可选 ``python-dotenv`` 预载 ``.env``；RAG 相关配置以根目录 ``config.yaml`` 的 ``rag`` / ``ai`` 为主（见 ``cli.main``）。

用法示例：``build`` / ``embed`` / ``chroma`` / ``shop-tier-intent``（``code/assets/rag_data/kb/``）与 ``reply`` / ``reply-serve``（``code/assets/``）；增量为 ``build --incremental`` → ``embed --incremental`` → ``chroma --incremental``。build 结束后同步 ``gen.chunks.json`` / ``shop_tier_intent_hierarchy.json`` 至 ``code/assets/kb/``。
"""

from pathlib import Path
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass
_code_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_code_dir / "src"))

from hubstudio_python.cli import main


if __name__ == "__main__":
    main()
