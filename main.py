"""
仓库根目录入口：可选 ``python-dotenv`` 预载 ``.env``；RAG 相关配置以根目录 ``config.yaml`` 的 ``rag`` / ``ai`` 为主（见 ``cli.main``）。

用法示例：``build`` / ``embed`` / ``chroma`` / ``shop-tier-intent``（``rag_file/kb/``）与 ``reply`` / ``reply-serve``（``rag_file/reply/``）；增量为 ``build --incremental`` → ``embed --incremental`` → ``chroma --incremental``（见 README）。``build`` 结束后会刷新 ``rag_file/kb/output/shop_tier_intent_hierarchy.json``。
"""

from pathlib import Path
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from hubstudio_python.cli import main


if __name__ == "__main__":
    main()
