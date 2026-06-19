#!/usr/bin/env python3
"""reply-serve 本地冒烟：Chroma + /api/intent（不调用 LLM 生成完整回复）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE = REPO_ROOT / "code"
FIXTURE = REPO_ROOT / "test_mock" / "fixtures" / "reply" / "intent_smoke.json"
PORT = 18766


def main() -> int:
    os.environ["HUBSTUDIO_CONFIG_FILE"] = str(REPO_ROOT / "test_mock" / "config.test.yaml")
    seed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "test_mock" / "scripts" / "seed_local_test_data.py")],
        cwd=CODE,
        check=False,
    )
    if seed.returncode != 0:
        print("seed failed", seed.returncode)
        return 1

    proc = subprocess.Popen(
        [sys.executable, "main.py", "reply-serve", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=CODE,
    )
    try:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{PORT}/api/intent"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        for _ in range(30):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                print("intent smoke OK:", json.dumps(data, ensure_ascii=False)[:500])
                return 0 if data.get("success") else 1
            except (urllib.error.URLError, ConnectionResetError):
                continue
        print("intent smoke FAIL: server not ready")
        return 1
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
