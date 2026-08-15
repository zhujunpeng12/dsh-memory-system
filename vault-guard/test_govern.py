from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from govern import scan_governance
from vault_lock import SCHEMA, atomic_json


def digest_tree(root: Path) -> str:
    rows = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: str(item)):
        rows.append(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return hashlib.sha256(b"\n".join(rows)).hexdigest()


class GovernanceTests(unittest.TestCase):
    def make_vault(self, root: Path) -> Path:
        vault = root / "vault"
        memory = vault / "memory"
        events = memory / "events"
        tx = memory / ".vault-transactions" / "tx-pending"
        events.mkdir(parents=True)
        tx.mkdir(parents=True)
        (memory / "rules.md").write_text(
            "# 规则\n\n1. ⏱×3 高频但未毕业\n2. ⏱×0 从未引用\n",
            encoding="utf-8",
        )
        (events / "2026-08-13.raw.md").write_text("# raw\nappend-only\n", encoding="utf-8")
        duplicate = "## 同一个重复治理主题\n正文。\n"
        (events / "2026-08-14.md").write_text("# 事件\n" + duplicate, encoding="utf-8")
        (events / "2026-08-15.md").write_text(
            "# 事件\n" + duplicate + "## 冲突修订候选\n需要人工仲裁。\n" + "## " + ("超长标题" * 40) + "\n正文保留。\n",
            encoding="utf-8",
        )
        (events / "2020-01-01.md").write_text("# 旧事件\n", encoding="utf-8")
        (tx / "manifest.json").write_text(json.dumps({"transaction_id": "tx-pending", "state": "committing"}), encoding="utf-8")
        now = time.time()
        atomic_json(memory / ".vault.lock", {
            "schema": SCHEMA,
            "holder": "dead",
            "token": "dead-token",
            "transaction_id": "tx-pending",
            "pid": 99999999,
            "process_start": "never",
            "purpose": "test",
            "targets": [],
            "acquired_at": now - 100,
            "heartbeat_at": now - 100,
            "lease_seconds": 1,
            "lease_until": now - 99,
        })
        return vault

    def test_scanner_finds_governance_classes_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make_vault(Path(tmp))
            before = digest_tree(vault)
            candidates, summary = scan_governance(vault, recent_days=60, archive_days=120)
            after = digest_tree(vault)
            categories = {item.category for item in candidates}
            self.assertEqual(before, after)
            self.assertEqual(summary["mode"], "dry-run/read-only")
            self.assertEqual(summary["automatic_changes"], 0)
            self.assertTrue({
                "lock-stale",
                "transaction-pending",
                "raw-unrefined",
                "duplicate-heading",
                "conflict-marker",
                "archive-review",
                "heading-over-budget",
                "rule-promotion-review",
                "rule-zero-citation",
            }.issubset(categories))
            self.assertTrue(all(item.automatic is False for item in candidates))


if __name__ == "__main__":
    unittest.main()
