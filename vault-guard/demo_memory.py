"""Run a synthetic first-memory walkthrough using the real CLI in fresh processes.

All writes are confined to a disposable temporary Vault. This is an engine
walkthrough, not a live DeepSeek Harness session or an LLM demonstration.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
OLD_RULE = "演示项目的发布检查使用蓝色清单。"
NEW_RULE = "演示项目的发布检查改为绿色清单。"
QUERY = "演示项目发布检查清单"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_demo() -> dict:
    """Exercise only synthetic data; never read or write a configured Vault."""
    with tempfile.TemporaryDirectory(prefix="dsh-memory-demo-") as temporary:
        root = Path(temporary)
        vault = root / "vault"
        workspace = root / "workspace"
        workspace.mkdir()
        home = root / "home"
        home.mkdir()
        env = {
            **os.environ,
            "MEMORY_VAULT": str(vault),
            "DSH_HOME": str(root / "dsh"),
            "DSH_SESSION_ID": "synthetic-first-memory-demo",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        def call(script: str, *args: str) -> dict:
            process = subprocess.run(
                [sys.executable, str(HERE / script), *args],
                cwd=workspace, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=30, check=False,
            )
            if process.returncode:
                raise RuntimeError(f"{script} failed with exit code {process.returncode}: {process.stderr.strip()}")
            return json.loads(process.stdout)

        def remember(text: str, *, supersedes: str | None = None) -> dict:
            body = root / "synthetic-body.txt"
            body.write_text(text, encoding="utf-8")
            args = ["raw", "--title", "演示发布规则", "--body-file", str(body),
                    "--source", "synthetic-first-memory-demo", "--date", "2026-08-22"]
            if supersedes:
                args += ["--kind", "correction", "--supersedes", supersedes]
            # --apply is intentional and restricted to the newly created
            # temporary Vault; it never authorizes writes to a user's Vault.
            return call("vault-write.py", *args, "--apply")

        def retrieve(*flags: str) -> dict:
            return call("recall.py", "--query", QUERY, "--cwd", str(workspace),
                        "--force", "--json", *flags)

        first = remember(OLD_RULE)
        _require(first["state"] == "committed", "initial synthetic fact did not commit")
        original_id = first["operations"][0]["entry_id"]
        raw_path = vault / "memory" / "events" / "2026-08-22.raw.md"
        original_bytes = raw_path.read_bytes()

        default = retrieve()
        _require(default["hits"] == [], "default recall unexpectedly included raw evidence")
        initial = retrieve("--include-raw")
        _require(any(OLD_RULE in hit["snippet"] for hit in initial["hits"]),
                 "fresh process did not retrieve the original synthetic fact")

        correction = remember(NEW_RULE, supersedes=original_id)
        _require(correction["state"] == "committed", "synthetic correction did not commit")
        effective = retrieve("--include-raw")
        _require(any(NEW_RULE in hit["snippet"] for hit in effective["hits"]),
                 "effective recall did not find the correction")
        _require(not any(OLD_RULE in hit["snippet"] for hit in effective["hits"]),
                 "effective recall leaked the superseded fact")

        history = retrieve("--include-raw", "--include-history")
        _require(any(OLD_RULE in hit["snippet"] and "raw-lifecycle=superseded" in hit["reasons"]
                     for hit in history["hits"]), "audit did not preserve labelled old evidence")
        _require(any(NEW_RULE in hit["snippet"] and "raw-lifecycle=active" in hit["reasons"]
                     for hit in history["hits"]), "audit did not label the new evidence active")
        _require(raw_path.read_bytes().startswith(original_bytes), "original raw evidence was changed")

        report = {
            "schema": "dsh-first-memory-demo-v1",
            "synthetic": True,
            "live_dsh_host_tested": False,
            "configured_user_vault_used": False,
            "first_fact": OLD_RULE,
            "default_recall": {"hits": len(default["hits"]), "raw_mode": default["trace"]["raw_mode"]},
            "explicit_raw_recall": {"text": OLD_RULE, "fresh_process": True},
            "correction": {"text": NEW_RULE, "state": correction["state"], "supersedes_original": True},
            "effective_recall": {"text": NEW_RULE, "old_fact_visible": False, "fresh_process": True},
            "history_recall": [{"text": OLD_RULE, "status": "superseded"},
                               {"text": NEW_RULE, "status": "active"}],
            "original_raw_preserved": True,
        }
    report["temporary_vault_removed"] = not root.exists()
    _require(report["temporary_vault_removed"], "temporary Vault cleanup failed")
    return report


def render_report(report: dict, lang: str = "zh") -> str:
    if lang == "zh":
        return "\n".join([
            "合成记忆演示（真实命令行、独立 Python 进程；不是运行中的 DSH）",
            f"1. 记下原始事实：{report['first_fact']}",
            "2. 新进程默认召回：0 条（默认不读取 raw 原始记录）",
            f"3. 显式读取 raw：{report['explicit_raw_recall']['text']}",
            f"4. 追加纠错：{report['correction']['text']}",
            f"5. 当前有效记忆：{report['effective_recall']['text']}（旧说法已排除）",
            f"6. 历史记录：已替代 = {report['history_recall'][0]['text']}",
            f"   历史记录：当前有效 = {report['history_recall'][1]['text']}",
            "7. 原始记录未被改写；临时演示库已删除。",
            "通过：仅验证合成演示。未使用你的记忆库、模型 API 或 DSH 宿主。",
        ])
    return "\n".join([
        "SYNTHETIC MEMORY DEMO (real CLI, fresh Python processes; no live DSH host)",
        f"1. Remembered raw fact: {report['first_fact']}",
        "2. Fresh-process default recall: 0 hits (raw is excluded by default)",
        f"3. Explicit raw recall: {report['explicit_raw_recall']['text']}",
        f"4. Appended correction: {report['correction']['text']}",
        f"5. Effective raw recall: {report['effective_recall']['text']} (old fact excluded)",
        f"6. History: superseded = {report['history_recall'][0]['text']}",
        f"   History: active = {report['history_recall'][1]['text']}",
        "7. Original raw bytes preserved; temporary Vault removed.",
        "PASS: synthetic walkthrough only. No user Vault, model API or DSH host was used.",
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the checked synthetic result as JSON")
    parser.add_argument("--lang", choices=("zh", "en"), default="zh", help="human-readable output language (default: zh); JSON is unchanged")
    args = parser.parse_args(argv)
    try:
        report = run_demo()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_report(report, args.lang))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
