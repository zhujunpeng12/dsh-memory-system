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
from vault_tx import VaultTransaction, sha256_bytes, sha256_file


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

    def test_raw_recovery_verifies_complete_bytes_and_preserves_history(self) -> None:
        prefix = b"# existing history\n"
        block = b'## entry\n<!-- dsh-entry: {"id": "entry-1"} -->\ncomplete body\n\n'
        cases = [
            ("complete", prefix + block, {}, "committed_recovered"),
            ("truncated", prefix + block[:-8], {}, "incomplete_preserved"),
            ("same-size-corrupt", prefix + block.replace(b"body", b"BAD!"),
             {"after_size": len(prefix + block)}, "incomplete_preserved"),
            ("legacy-size-only", prefix + block,
             {"after_size": len(prefix + block), "append_sha256": ""}, "incomplete_preserved"),
            ("later-append", prefix + block + b"later entry\n",
             {"append_size": len(block)}, "committed_recovered"),
            ("absent", None, {}, "rolled_back"),
        ]
        for label, content, extra, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                vault = Path(tmp) / "vault"
                memory = vault / "memory"
                target = memory / "events" / "2026-08-15.raw.md"
                target.parent.mkdir(parents=True)
                if content is not None:
                    target.write_bytes(content)
                old_tx = memory / ".vault-transactions" / "old-raw"
                atomic_json(old_tx / "manifest.json", {
                    "transaction_id": "old-raw", "state": "committing",
                    "operations": [{
                        "kind": "raw_append", "target": str(target),
                        "entry_id": "entry-1", "before_size": len(prefix),
                        "append_sha256": sha256_bytes(block), **extra,
                    }],
                })
                with VaultTransaction(vault=vault, purpose="recover-raw") as tx:
                    tx.commit_noop()
                manifest = json.loads((old_tx / "manifest.json").read_text())
                receipt = json.loads((old_tx / "receipt.json").read_text())
                self.assertEqual(manifest["state"], expected)
                self.assertEqual(receipt["state"], expected)
                self.assertEqual(target.read_bytes() if target.exists() else None, content)
                # The next writer must not reprocess a terminal receipt.
                with VaultTransaction(vault=vault, purpose="recover-again") as tx:
                    self.assertEqual(tx.data["recovered_before_begin"], [])
                    tx.commit_noop()
                self.assertFalse((memory / ".vault.lock").exists())

    def test_raw_append_receipt_matches_exact_written_bytes(self) -> None:
        for prefix in (b"", b"# existing history\r\n"):
            with self.subTest(existing=bool(prefix)), tempfile.TemporaryDirectory() as tmp:
                vault = Path(tmp) / "vault"
                target = vault / "memory" / "events" / "2026-08-15.raw.md"
                target.parent.mkdir(parents=True)
                target.write_bytes(prefix)

                with VaultTransaction(vault=vault, purpose="raw-byte-integrity") as tx:
                    receipt = tx.append_raw_entry(
                        target, title="entry", body="first line\nsecond line 中文",
                        source="unit-test", entry_id="entry-1",
                    )

                written = target.read_bytes()
                appended = written[len(prefix):]
                operation = receipt["operations"][0]
                self.assertEqual(written[:len(prefix)], prefix)
                self.assertIn("first line\nsecond line 中文\n\n".encode("utf-8"), appended)
                self.assertNotIn(b"\r\n", appended)
                self.assertEqual(operation["before_size"], len(prefix))
                self.assertEqual(operation["append_size"], len(appended))
                self.assertEqual(operation["after_size"], len(written))
                self.assertEqual(operation["append_sha256"], sha256_bytes(appended))
                self.assertEqual(operation["after_sha256"], sha256_bytes(written))

    def test_raw_partial_write_with_entry_id_is_not_committed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            target = vault / "memory" / "events" / "2026-08-15.raw.md"
            real_write = os.write
            calls = 0

            def partial_write(fd, data):
                nonlocal calls
                calls += 1
                if calls == 1:
                    # Write metadata (including the ID), but none of the body.
                    end = bytes(data).index(b" -->\n") + len(b" -->\n")
                    return real_write(fd, data[:end])
                raise OSError("injected partial-write failure")

            with VaultTransaction(vault=vault, purpose="partial-write") as tx:
                with mock.patch("vault_tx.os.write", side_effect=partial_write):
                    with self.assertRaisesRegex(OSError, "partial-write"):
                        tx.append_raw_entry(target, title="entry", body="complete body",
                                            source="unit-test", entry_id="entry-1")
            self.assertIn(b'"id": "entry-1"', target.read_bytes())
            self.assertNotIn(b"complete body", target.read_bytes())
            self.assertEqual(json.loads(tx.manifest_path.read_text())["state"], "incomplete_preserved")
            self.assertEqual(json.loads((tx.tx_dir / "receipt.json").read_text())["state"], "incomplete_preserved")

    def test_zero_progress_raw_write_fails_without_hanging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            target = vault / "memory" / "events" / "2026-08-15.raw.md"
            with VaultTransaction(vault=vault, purpose="zero-write") as tx:
                with mock.patch("vault_tx.os.write", return_value=0):
                    with self.assertRaisesRegex(OSError, "no write progress"):
                        tx.append_raw_entry(target, title="entry", body="body", source="unit-test")
            self.assertEqual(json.loads(tx.manifest_path.read_text())["state"], "rolled_back")
            self.assertEqual(target.read_bytes(), b"")

    def test_raw_verification_read_failure_remains_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            target = vault / "memory" / "events" / "2026-08-15.raw.md"
            with VaultTransaction(vault=vault, purpose="read-failure") as tx:
                with mock.patch("vault_tx.sha256_file", side_effect=OSError("audit failed")), \
                     mock.patch("vault_tx._raw_append_state", side_effect=PermissionError("read denied")):
                    with self.assertRaisesRegex(OSError, "audit failed"):
                        tx.append_raw_entry(target, title="entry", body="body", source="unit-test")
            self.assertEqual(json.loads(tx.manifest_path.read_text())["state"], "recovery_required")
            before = target.read_bytes()
            with mock.patch("vault_tx._raw_append_state", side_effect=PermissionError("read denied")):
                with self.assertRaises(PermissionError):
                    with VaultTransaction(vault=vault, purpose="retry-denied"):
                        pass
            self.assertFalse((vault / "memory" / ".vault.lock").exists())
            self.assertEqual(json.loads(tx.manifest_path.read_text())["state"], "recovery_required")
            with VaultTransaction(vault=vault, purpose="retry-readable") as next_tx:
                next_tx.commit_noop()
            self.assertEqual(json.loads(tx.manifest_path.read_text())["state"], "committed_recovered")
            self.assertEqual(target.read_bytes(), before)

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
