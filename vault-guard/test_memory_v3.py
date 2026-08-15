from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from vault_lock import SCHEMA, LockBusyError, VaultLock, atomic_json
from vault_tx import VaultTransaction, sha256_file


ROOT = Path(__file__).parent


class LeaseLockTests(unittest.TestCase):
    def test_busy_lock_fails_and_dead_owner_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp) / "memory"
            memory.mkdir()
            lock_path = memory / ".vault.lock"
            first = VaultLock(lock_path, holder="one", transaction_id="one", wait_seconds=0)
            first.acquire()
            try:
                with self.assertRaises(LockBusyError):
                    VaultLock(lock_path, holder="two", transaction_id="two", wait_seconds=0).acquire()
            finally:
                first.release()

            now = time.time()
            atomic_json(lock_path, {
                "schema": SCHEMA,
                "holder": "dead",
                "token": "dead-token",
                "transaction_id": "dead-tx",
                "pid": 99999999,
                "process_start": "never",
                "purpose": "test",
                "targets": [],
                "acquired_at": now,
                "heartbeat_at": now,
                "lease_seconds": 30,
                "lease_until": now + 30,
            })
            second = VaultLock(lock_path, holder="two", transaction_id="two", wait_seconds=0)
            second.acquire()
            second.release()
            self.assertFalse(lock_path.exists())
            self.assertEqual(len(list((memory / ".vault-transactions" / "recoveries").glob("*.json"))), 1)

    def test_manual_cli_release_uses_stable_parent_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Documents" / "Obsidian Vault" / "memory").mkdir(parents=True)
            env = os.environ.copy()
            env.update({"HOME": str(home), "USERPROFILE": str(home)})
            acquire = subprocess.run(
                [sys.executable, str(ROOT / "vault-lock.py"), "acquire"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
            )
            release = subprocess.run(
                [sys.executable, str(ROOT / "vault-lock.py"), "release"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
            )
            self.assertEqual(acquire.returncode, 0, acquire.stdout + acquire.stderr)
            self.assertEqual(release.returncode, 0, release.stdout + release.stderr)
            self.assertIn("lock released", release.stdout)
            self.assertFalse((home / "Documents" / "Obsidian Vault" / "memory" / ".vault.lock").exists())


class TransactionTests(unittest.TestCase):
    def test_multi_file_commit_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            memory = vault / "memory"
            memory.mkdir(parents=True)
            first = memory / "rules.md"
            second = memory / "rules-core.md"
            first.write_text("old-a\n", encoding="utf-8")
            second.write_text("old-b\n", encoding="utf-8")
            expected = {first: sha256_file(first), second: sha256_file(second)}
            with VaultTransaction(vault=vault, purpose="test-multi", targets=[first, second]) as tx:
                receipt = tx.replace_texts({first: "new-a\n", second: "new-b\n"}, expected_hashes=expected)
            self.assertEqual(receipt["state"], "committed")
            self.assertEqual(first.read_text(encoding="utf-8"), "new-a\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "new-b\n")
            self.assertTrue((tx.tx_dir / "receipt.json").exists())
            self.assertFalse((memory / ".vault.lock").exists())

    def test_incomplete_commit_is_rolled_back_by_next_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            memory = vault / "memory"
            target = memory / "rules.md"
            target.parent.mkdir(parents=True)
            target.write_text("broken\n", encoding="utf-8")
            old_tx = memory / ".vault-transactions" / "old-tx"
            (old_tx / "before").mkdir(parents=True)
            (old_tx / "before" / "0000.bin").write_bytes(b"known-good\n")
            atomic_json(old_tx / "manifest.json", {
                "schema": "dsh-vault-transaction-v1",
                "transaction_id": "old-tx",
                "state": "committing",
                "operations": [{
                    "kind": "replace",
                    "target": str(target),
                    "before_exists": True,
                    "backup": "before/0000.bin",
                }],
            })
            with VaultTransaction(vault=vault, purpose="recovery-test", targets=[]) as tx:
                tx.commit_noop()
            self.assertEqual(target.read_text(encoding="utf-8"), "known-good\n")
            manifest = json.loads((old_tx / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["state"], "rolled_back")

    def test_parallel_raw_appends_are_serialized_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            target = vault / "memory" / "events" / "2026-08-15.raw.md"

            def append(index: int) -> str:
                with VaultTransaction(vault=vault, purpose=f"raw-{index}", targets=[target], wait_seconds=10) as tx:
                    receipt = tx.append_raw_entry(
                        target,
                        title=f"并发条目 {index}",
                        body=f"事实 {index}",
                        source="unit-test",
                    )
                return receipt["state"]

            with ThreadPoolExecutor(max_workers=6) as pool:
                states = list(pool.map(append, range(8)))
            text = target.read_text(encoding="utf-8")
            self.assertEqual(states, ["committed"] * 8)
            self.assertEqual(text.count("<!-- dsh-entry:"), 8)
            self.assertEqual(len(set(line for line in text.splitlines() if '"id":' in line)), 8)
            self.assertFalse((vault / "memory" / ".vault.lock").exists())

    def test_raw_replace_and_unlinked_correction_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            raw = vault / "memory" / "events" / "2026-08-15.raw.md"
            raw.parent.mkdir(parents=True)
            raw.write_text("# raw\n", encoding="utf-8")
            with VaultTransaction(vault=vault, purpose="block-raw-replace", targets=[raw]) as tx:
                with self.assertRaises(ValueError):
                    tx.replace_texts({raw: "overwrite\n"})
            with VaultTransaction(vault=vault, purpose="block-unlinked-correction", targets=[raw]) as tx:
                with self.assertRaises(ValueError):
                    tx.append_raw_entry(raw, title="纠错", body="内容", source="test", entry_type="correction")

    def test_concurrent_starters_are_not_misrecovered(self) -> None:
        """Manifests are created only after lock acquisition, so a waiting peer's
        planned transaction can never be mistaken for an abandoned leftover."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            target = vault / "memory" / "events" / "2026-08-15.raw.md"
            barrier = threading.Barrier(8)

            def append(index: int) -> str:
                try:
                    # Synchronize contenders before any one of them owns the
                    # single-writer lock. A barrier inside the transaction
                    # would deadlock by design because only one writer enters.
                    barrier.wait(timeout=15)
                    with VaultTransaction(vault=vault, purpose=f"starter-{index}", targets=[target], wait_seconds=60) as tx:
                        tx.append_raw_entry(target, title=f"并发启动 {index}", body=f"内容 {index}", source="unit-test")
                    return "ok"
                except Exception as exc:  # noqa: BLE001 - report as state string
                    return f"{type(exc).__name__}: {exc}"

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(append, range(8)))
            self.assertEqual(results, ["ok"] * 8)
            manifests = list((vault / "memory" / ".vault-transactions").glob("*/manifest.json"))
            self.assertEqual(len(manifests), 8)
            states = {json.loads(m.read_text(encoding="utf-8"))["state"] for m in manifests}
            self.assertEqual(states, {"committed"})
            text = target.read_text(encoding="utf-8")
            self.assertEqual(text.count("<!-- dsh-entry:"), 8)
            self.assertFalse((vault / "memory" / ".vault.lock").exists())

    def test_rollback_failure_marks_recovery_required_and_next_writer_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            memory = vault / "memory"
            target = memory / "rules.md"
            target.parent.mkdir(parents=True)
            target.write_text("old\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                with VaultTransaction(vault=vault, purpose="fault-injection", targets=[target]) as tx:
                    with mock.patch.object(
                        tx.lock,
                        "assert_owned",
                        side_effect=[None, RuntimeError("post-replace-lock-loss")],
                    ), mock.patch("vault_tx._atomic_write_bytes", side_effect=OSError("rollback-disk-error")):
                        tx.replace_texts({target: "new\n"})
            manifests = list((memory / ".vault-transactions").glob("*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            state = json.loads(manifests[0].read_text(encoding="utf-8"))["state"]
            self.assertEqual(state, "recovery_required")
            receipt = json.loads((manifests[0].parent / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["state"], "recovery_required")
            # The next writer must restore the before-image automatically.
            with VaultTransaction(vault=vault, purpose="recovery-test", targets=[]) as tx:
                tx.commit_noop()
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            state_after = json.loads(manifests[0].read_text(encoding="utf-8"))["state"]
            self.assertEqual(state_after, "rolled_back")
            self.assertFalse((memory / ".vault.lock").exists())

    def test_raw_append_error_after_write_writes_recovery_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            target = vault / "memory" / "events" / "2026-08-15.raw.md"
            with mock.patch("vault_tx.sha256_file", side_effect=OSError("post-write-audit-failure")):
                with self.assertRaises(OSError):
                    with VaultTransaction(vault=vault, purpose="raw-audit-fault", targets=[target]) as tx:
                        tx.append_raw_entry(target, title="审计失败条目", body="内容", source="unit-test")
            text = target.read_text(encoding="utf-8")
            self.assertIn("<!-- dsh-entry:", text)
            manifests = list((vault / "memory" / ".vault-transactions").glob("*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["state"], "committed_recovered")
            receipt = json.loads((manifests[0].parent / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["state"], "committed_recovered")
            self.assertFalse((vault / "memory" / ".vault.lock").exists())


if __name__ == "__main__":
    unittest.main()
