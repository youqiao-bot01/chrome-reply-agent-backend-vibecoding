"""仓库根 CLI 入口：转发至 ``code/main.py``。"""

from __future__ import annotations

import runpy
from pathlib import Path

_code_main = Path(__file__).resolve().parent / "code" / "main.py"
runpy.run_path(str(_code_main), run_name="__main__")
