from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
SCRIPT = ROOT / "hook-first-prompt.py"


class FirstPromptHookTests(unittest.TestCase):
    def run_hook(self, dsh_home: Path, payload: dict) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["DSH_HOME"] = str(dsh_home)
        env["VAULT_GUARD_DIR"] = str(dsh_home / "vault-guard")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=env,
            timeout=10,
        )

    def make_bootstrap(self, dsh_home: Path, *, fail: bool = False) -> None:
        guard = dsh_home / "vault-guard"
        guard.mkdir(parents=True)
        if fail:
            source = "import sys\nprint('boom', file=sys.stderr)\nraise SystemExit(7)\n"
        else:
            source = (
                "import argparse, sys\n"
                "sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
                "p=argparse.ArgumentParser(); p.add_argument('--cwd'); p.add_argument('--max-bytes'); a=p.parse_args()\n"
                "print('# [vault-bootstrap] DSH 热记忆包\\ncwd：'+a.cwd)\n"
            )
        (guard / "bootstrap.py").write_text(source, encoding="utf-8")

    def make_recall(self, dsh_home: Path, *, fail: bool = False) -> None:
        guard = dsh_home / "vault-guard"
        guard.mkdir(parents=True, exist_ok=True)
        if fail:
            source = "import sys\nprint('recall-boom', file=sys.stderr)\nraise SystemExit(9)\n"
        else:
            source = (
                "import argparse\n"
                "p=argparse.ArgumentParser(); p.add_argument('--query'); p.add_argument('--cwd'); "
                "p.add_argument('--max-bytes'); p.add_argument('--force', action='store_true'); a=p.parse_args()\n"
                "print('# [vault-cold-recall] DSH 冷层召回包\\n查询：'+a.query+'\\ncwd：'+a.cwd)\n"
            )
        (guard / "recall.py").write_text(source, encoding="utf-8")

    def test_first_prompt_injects_with_protocol_schema_and_second_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".dsh"
            self.make_bootstrap(home)
            self.make_recall(home)
            transcript = Path(tmp) / "session.jsonl.zstd"
            payload = {
                "session_id": "session-中文-1",
                "cwd": "D:\\sample-project",
                "transcript_path": str(transcript),
            }
            first = self.run_hook(home, payload)
            second = self.run_hook(home, payload)

            self.assertEqual(first.returncode, 0, first.stderr)
            output = json.loads(first.stdout)
            scoped = output["hookSpecificOutput"]
            self.assertEqual(scoped["hookEventName"], "UserPromptSubmit")
            self.assertIn("[vault-bootstrap] DSH 热记忆包", scoped["additionalContext"])
            self.assertIn("D:\\sample-project", scoped["additionalContext"])
            self.assertEqual(json.loads(second.stdout), {})
            self.assertEqual(len(list((home / "storages" / "vault-bootstrap-sessions").glob("*.json"))), 1)

    def test_failure_is_visible_and_not_marked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".dsh"
            self.make_bootstrap(home, fail=True)
            self.make_recall(home)
            result = self.run_hook(home, {"session_id": "retry-me", "cwd": "D:\\sample-project"})
            scoped = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertIn("[vault-bootstrap: absent]", scoped["additionalContext"])
            self.assertIn("exit=7", scoped["additionalContext"])

    def test_absent_context_does_not_block_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".dsh"
            self.make_bootstrap(home)
            self.make_recall(home)
            transcript = Path(tmp) / "session.jsonl.zstd"
            transcript.write_bytes(b"unreadable-zstd-does-not-count-as-success")
            result = self.run_hook(home, {
                "session_id": "retry-after-absent",
                "cwd": "D:\\sample-project",
                "transcript_path": str(transcript),
            })
            scoped = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertIn("[vault-bootstrap] DSH 热记忆包", scoped["additionalContext"])

    def test_later_history_prompt_injects_cold_packet_without_repeating_hot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".dsh"
            self.make_bootstrap(home)
            self.make_recall(home)
            base = {"session_id": "cold-later", "cwd": "D:\\sample-project"}
            first = self.run_hook(home, base)
            later = self.run_hook(home, {**base, "prompt": "继续上次的记忆系统修复"})
            self.assertIn("[vault-bootstrap]", json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"])
            context = json.loads(later.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("[vault-cold-recall]", context)
            self.assertNotIn("[vault-bootstrap]", context)

    def test_meta_only_followup_does_not_inject_another_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".dsh"
            self.make_bootstrap(home)
            self.make_recall(home)
            base = {"session_id": "meta-later", "cwd": "D:\\sample-project"}
            self.run_hook(home, base)
            later = self.run_hook(home, {
                **base,
                "prompt": "继续只读复测，请列出本轮 cold recall trace，不要调用工具",
            })
            self.assertEqual(json.loads(later.stdout), {})

    def test_first_history_prompt_contains_hot_and_cold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".dsh"
            self.make_bootstrap(home)
            self.make_recall(home)
            result = self.run_hook(home, {
                "session_id": "hot-and-cold",
                "cwd": "D:\\sample-project",
                "userPrompt": "请找出之前的复盘轨迹证据",
            })
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("[vault-bootstrap]", context)
            self.assertIn("[vault-cold-recall]", context)

    def test_recall_failure_does_not_drop_successful_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".dsh"
            self.make_bootstrap(home)
            self.make_recall(home, fail=True)
            result = self.run_hook(home, {
                "session_id": "cold-fails",
                "cwd": "D:\\sample-project",
                "prompt": "继续上次的记忆系统修复",
            })
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("[vault-bootstrap]", context)
            self.assertIn("[vault-cold-recall: absent]", context)


if __name__ == "__main__":
    unittest.main()
