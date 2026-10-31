from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from demo_memory import NEW_RULE, OLD_RULE, render_report, run_demo


class FirstMemoryDemoTests(unittest.TestCase):
    def test_demo_uses_only_temporary_synthetic_vault_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / "configured-vault"
            configured.mkdir()
            sentinel = configured / "must-not-read-or-write.txt"
            sentinel.write_bytes(b"private sentinel: unchanged")
            dsh = root / "configured-dsh"
            dsh.mkdir()
            (dsh / "sentinel.txt").write_bytes(b"unchanged")
            tmpdir = root / "disposable"
            tmpdir.mkdir()
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "demo_memory.py"), "--json"],
                env={**os.environ, "MEMORY_VAULT": str(configured), "DSH_HOME": str(dsh),
                     "DSH_SESSION_ID": "must-not-be-reused", "TMPDIR": str(tmpdir),
                     "TMP": str(tmpdir), "TEMP": str(tmpdir)},
                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
            )
            report = json.loads(result.stdout)
            self.assertTrue(report["synthetic"])
            self.assertFalse(report["configured_user_vault_used"])
            self.assertFalse(report["live_dsh_host_tested"])
            self.assertEqual(report["first_fact"], OLD_RULE)
            self.assertEqual(report["default_recall"], {"hits": 0, "raw_mode": "excluded"})
            self.assertEqual(report["effective_recall"]["text"], NEW_RULE)
            self.assertFalse(report["effective_recall"]["old_fact_visible"])
            self.assertEqual([item["status"] for item in report["history_recall"]], ["superseded", "active"])
            self.assertTrue(report["original_raw_preserved"])
            self.assertTrue(report["temporary_vault_removed"])
            self.assertEqual(list(tmpdir.iterdir()), [])
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            output = render_report(report, "en")
            self.assertIn("no live DSH host", output)
            self.assertIn("old fact excluded", output)
            self.assertNotIn(str(root), result.stdout)
            self.assertNotIn("must-not-be-reused", result.stdout)

    def test_failed_cli_does_not_report_success_and_still_cleans_up(self):
        scope = tempfile.TemporaryDirectory(prefix="dsh-demo-failure-test-")
        root = Path(scope.name)
        failed = subprocess.CompletedProcess([], 0, '{"state": "rolled_back"}', '')
        with mock.patch("demo_memory.tempfile.TemporaryDirectory", return_value=scope), \
             mock.patch("demo_memory.subprocess.run", return_value=failed) as runner:
            with self.assertRaisesRegex(RuntimeError, "did not commit"):
                run_demo()
        self.assertFalse(root.exists())
        self.assertEqual(runner.call_args.kwargs["env"]["MEMORY_VAULT"], str(root / "vault"))
        self.assertEqual(runner.call_args.kwargs["timeout"], 30)
        self.assertEqual(runner.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_documented_transcript_matches_real_output(self):
        script = Path(__file__).parent / "demo_memory.py"
        for language, filename in [("zh", "first-memory-demo-output.txt"), ("en", "first-memory-demo-output.en.txt")]:
            with self.subTest(language=language):
                result = subprocess.run([sys.executable, str(script), "--lang", language], capture_output=True,
                                        text=True, encoding="utf-8", timeout=30, check=True)
                transcript = script.parent.parent / "docs" / filename
                self.assertEqual(result.stdout, transcript.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
