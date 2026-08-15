from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ClosingGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_script("vault_guard_check_test", "check.py")
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        memory = root / "memory"
        events = memory / "events"
        projects = root / "projects"
        events.mkdir(parents=True)
        projects.mkdir()
        rules = memory / "rules.md"
        core = memory / "rules-core.md"
        rules.write_text("1. ⏱×0 rule\n", encoding="utf-8")
        core.write_text("core\n", encoding="utf-8")
        self.mod.MEM = memory
        self.mod.EV = events
        self.mod.RULES = rules
        self.mod.CORE = core
        self.mod.LOCK = memory / ".vault.lock"
        self.mod.PROJ = projects
        self.mod.REMINDER = root / "reminder.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_gate(self, args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = self.mod.main(args)
        return code, stream.getvalue()

    def test_plain_closing_does_not_require_raw(self) -> None:
        code, output = self.run_gate(["--closing"])
        self.assertEqual(code, 0)
        self.assertNotIn("今日无 raw", output)

    def test_authorized_closing_requires_raw(self) -> None:
        code, output = self.run_gate(["--closing", "--expect-write"])
        self.assertEqual(code, 1)
        self.assertIn("今日无 raw", output)


class RuleCitationTests(unittest.TestCase):
    def test_increment_is_exact_and_does_not_match_suffix_rule(self) -> None:
        mod = load_script("rule_cite_test", "rule-cite.py")
        text = "2. ⏱×0 first\n2b. ⏱×3 second\n"
        updated, old, new = mod.increment_rule_text(text, "2")
        self.assertEqual((old, new), (0, 1))
        self.assertIn("2. ⏱×1 first", updated)
        self.assertIn("2b. ⏱×3 second", updated)

    def test_missing_rule_is_blocked(self) -> None:
        mod = load_script("rule_cite_missing_test", "rule-cite.py")
        with self.assertRaises(ValueError):
            mod.increment_rule_text("1. ⏱×0 only\n", "9")

    def test_apply_updates_isolated_rules_under_lock_contract(self) -> None:
        mod = load_script("rule_cite_apply_test", "rule-cite.py")
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Obsidian Vault"
            memory = vault / "memory"
            memory.mkdir(parents=True)
            rules = memory / "rules.md"
            core = memory / "rules-core.md"
            rules.write_text("1. ⏱×0 only\n", encoding="utf-8")
            core.write_text("old core\n", encoding="utf-8")
            mod.VAULT = vault
            mod.MEMORY = memory
            mod.RULES = rules
            mod.CORE = core
            code = mod.main(["1", "--apply"])
            self.assertEqual(code, 0)
            self.assertIn("1. ⏱×1 only", rules.read_text(encoding="utf-8"))
            self.assertIn("核心常驻层", core.read_text(encoding="utf-8"))
            self.assertFalse((memory / ".vault.lock").exists())
            receipts = list((memory / ".vault-transactions").glob("*/receipt.json"))
            self.assertEqual(len(receipts), 1)

    def test_cli_apply_acquires_syncs_and_releases_in_temporary_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guard = home / ".dsh" / "vault-guard"
            memory = home / "Documents" / "Obsidian Vault" / "memory"
            guard.mkdir(parents=True)
            memory.mkdir(parents=True)
            shutil.copy2(ROOT / "vault-lock.py", guard / "vault-lock.py")
            shutil.copy2(ROOT / "sync-core.py", guard / "sync-core.py")
            rules = memory / "rules.md"
            rules.write_text("1. ⏱×1 ⭐ only\n", encoding="utf-8")
            env = os.environ.copy()
            env.update({"HOME": str(home), "USERPROFILE": str(home), "DSH_SESSION_ID": "test-rule-cite"})
            result = subprocess.run(
                [sys.executable, str(ROOT / "rule-cite.py"), "1", "--apply"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("1. ⏱×2 ⭐ only", rules.read_text(encoding="utf-8"))
            self.assertTrue((memory / "rules-core.md").exists())
            self.assertFalse((memory / ".vault.lock").exists())


class TrajectoryReviewTests(unittest.TestCase):
    def test_strong_correction_is_detected(self) -> None:
        mod = load_script("trajectory_review_test", "trajectory-review.py")
        self.assertTrue(mod.is_correction("不对，我说的是流程图片"))
        self.assertTrue(mod.is_correction("你还没有修完"))

    def test_forward_preference_is_not_misclassified(self) -> None:
        mod = load_script("trajectory_review_preference_test", "trajectory-review.py")
        self.assertFalse(mod.is_correction("我不希望丢失重要的信息"))


if __name__ == "__main__":
    unittest.main()
