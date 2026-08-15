"""SessionStart bridge: inject one bounded vault-bootstrap packet."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


try:
    # stdin 同样强制 UTF-8：钩子桥（Node）按 CC hooks 约定写 UTF-8 JSON，
    # 而 Windows 默认 stdin 是 gbk+surrogateescape，中文 cwd 会解出孤立
    # 代理字符并随 argv 传给 bootstrap，最终在 utf8_size() 处崩溃（exit=1）。
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def extract_cwd(payload: object) -> str:
    keys = {"cwd", "current_dir", "working_directory", "project_dir"}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and value.strip():
                return value
        for value in payload.values():
            found = extract_cwd(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = extract_cwd(value)
            if found:
                return found
    return ""


def main() -> None:
    try:
        raw_input = sys.stdin.read().lstrip("\ufeff")
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}
    cwd = extract_cwd(payload) or str(Path.cwd())
    bootstrap = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh")) / "vault-guard" / "bootstrap.py"
    try:
        result = subprocess.run(
            [sys.executable, str(bootstrap), "--cwd", cwd, "--max-bytes", "14000"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=40,
        )
        packet = (result.stdout or "").strip()
        if not packet:
            packet = f"[vault-bootstrap] 启动包无输出（exit={result.returncode}）：{result.stderr.strip()}"
    except Exception as exc:
        packet = f"[vault-bootstrap] 启动包执行失败：{exc}"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": packet,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
