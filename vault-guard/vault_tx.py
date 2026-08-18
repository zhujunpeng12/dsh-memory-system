"""Recoverable transactions and append-only raw capture for the DSH Vault."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from vault_lock import VaultLock, atomic_json


TERMINAL_STATES = {"committed", "committed_recovered", "rolled_back"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except FileNotFoundError:
        return None


def _safe_relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-vault-tx-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def recover_incomplete(memory: Path, *, exclude: str | None = None) -> list[dict[str, str]]:
    """Recover non-terminal transactions. The caller must own the Vault lock."""
    root = memory / ".vault-transactions"
    recovered: list[dict[str, str]] = []
    if not root.exists():
        return recovered
    for tx_dir in sorted(root.iterdir()):
        if not tx_dir.is_dir() or tx_dir.name == "recoveries" or tx_dir.name == exclude:
            continue
        manifest_path = tx_dir / "manifest.json"
        data = _read_manifest(manifest_path)
        if not data or data.get("state") in TERMINAL_STATES:
            continue
        state = str(data.get("state"))
        operations = data.get("operations") if isinstance(data.get("operations"), list) else []
        raw_entries = [op for op in operations if isinstance(op, dict) and op.get("kind") == "raw_append"]
        if raw_entries:
            committed = True
            for op in raw_entries:
                target = Path(str(op.get("target")))
                marker = str(op.get("entry_id") or "")
                before_size = int(op.get("before_size") or 0)
                append_hash = str(op.get("append_sha256") or "")
                after_size = op.get("after_size")
                try:
                    data = target.read_bytes()
                except OSError:
                    data = b""
                if after_size is not None and len(data) == int(after_size):
                    # 正常提交路径：大小精确匹配
                    ok = True
                elif append_hash and len(data) >= before_size and len(data) > before_size:
                    # 崩溃恢复路径：文件尾部（追加起点之后）必须是追加块的完整字节，
                    # 用 append_sha256 验证——截断的半写入会在这里被识别为未完成
                    ok = sha256_bytes(data[before_size:]) == append_hash
                else:
                    ok = bool(marker and f'"id": "{marker}"' in data.decode("utf-8", errors="replace"))
                committed = committed and ok
            data["state"] = "committed_recovered" if committed else "rolled_back"
            data["recovery_reason"] = (
                "raw-entry-verified" if committed else "raw-entry-absent-or-truncated"
            )
        elif state in {"committing", "recovery_required"}:
            for op in reversed(operations):
                if not isinstance(op, dict) or op.get("kind") != "replace":
                    continue
                target = Path(str(op["target"]))
                if op.get("before_exists"):
                    _atomic_write_bytes(target, (tx_dir / str(op["backup"])).read_bytes())
                else:
                    target.unlink(missing_ok=True)
            data["state"] = "rolled_back"
            data["recovery_reason"] = "restored-before-images"
        else:
            data["state"] = "rolled_back"
            data["recovery_reason"] = "no-target-mutation"
        data["recovered_at"] = time.time()
        atomic_json(manifest_path, data)
        atomic_json(tx_dir / "receipt.json", {
            "schema": "dsh-vault-transaction-receipt-v1",
            "transaction_id": data.get("transaction_id", tx_dir.name),
            "state": data["state"],
            "recovered_from": state,
            "at": time.time(),
        })
        recovered.append({"transaction_id": tx_dir.name, "state": data["state"]})
    return recovered


class VaultTransaction:
    def __init__(
        self,
        *,
        vault: Path | None = None,
        purpose: str,
        targets: list[Path] | None = None,
        holder: str | None = None,
        wait_seconds: float = 15.0,
        lease_seconds: float = 30.0,
        heartbeat_seconds: float = 5.0,
        transaction_id: str | None = None,
    ) -> None:
        self.vault = (vault or Path(os.environ.get("MEMORY_VAULT") or (Path.home() / "Documents" / "Obsidian Vault"))).resolve()
        self.memory = self.vault / "memory"
        self.transaction_id = transaction_id or f"{int(time.time())}-{uuid.uuid4().hex}"
        self.purpose = purpose
        self.targets = [Path(p).resolve() for p in (targets or [])]
        self.holder = holder or os.environ.get("DSH_SESSION_ID") or f"pid-{os.getpid()}"
        self.tx_root = self.memory / ".vault-transactions"
        self.tx_dir = self.tx_root / self.transaction_id
        self.manifest_path = self.tx_dir / "manifest.json"
        self.data: dict[str, Any] = {
            "schema": "dsh-vault-transaction-v1",
            "transaction_id": self.transaction_id,
            "holder": self.holder,
            "purpose": purpose,
            "state": "planned",
            "created_at": time.time(),
            "targets": [str(p) for p in self.targets],
            "operations": [],
        }
        self.lock = VaultLock(
            self.memory / ".vault.lock",
            holder=self.holder,
            transaction_id=self.transaction_id,
            purpose=purpose,
            targets=[str(p) for p in self.targets],
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            wait_seconds=wait_seconds,
        )
        self.entered = False

    def _save(self) -> None:
        atomic_json(self.manifest_path, self.data)

    def _set_state(self, state: str, **extra: Any) -> None:
        self.data.update(extra)
        self.data["state"] = state
        self.data["updated_at"] = time.time()
        self._save()

    def _validate_target(self, path: Path, *, allow_raw: bool = False) -> Path:
        resolved = path.resolve()
        _safe_relative(resolved, self.vault)
        if not allow_raw and resolved.name.endswith(".raw.md"):
            raise ValueError("raw files are append-only; use append_raw_entry")
        return resolved

    def __enter__(self) -> "VaultTransaction":
        # Ordering matters: acquire the lock and recover stale transactions
        # BEFORE creating this transaction's manifest directory. Otherwise the
        # first lock holder would see still-waiting peers' fresh "planned"
        # manifests and misjudge them as abandoned leftovers.
        self.lock.acquire()
        created = False
        try:
            recovered = recover_incomplete(self.memory, exclude=self.transaction_id)
            self.tx_dir.mkdir(parents=True, exist_ok=False)
            created = True
            self._save()
            self._set_state("prepared", recovered_before_begin=recovered)
            self.entered = True
            return self
        except Exception as exc:
            if created:
                try:
                    self._set_state("rolled_back", error=f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
            self.lock.release()
            raise

    def commit_noop(self) -> None:
        if not self.entered:
            raise RuntimeError("transaction must be entered before commit_noop")
        self._set_state("committed", committed_at=time.time())
        atomic_json(self.tx_dir / "receipt.json", {
            "schema": "dsh-vault-transaction-receipt-v1",
            "transaction_id": self.transaction_id,
            "purpose": self.purpose,
            "state": "committed",
            "operations": [],
            "at": time.time(),
        })

    def replace_texts(
        self,
        changes: Mapping[Path, str],
        *,
        expected_hashes: Mapping[Path, str | None] | None = None,
    ) -> dict[str, Any]:
        if not self.entered:
            raise RuntimeError("transaction must be entered before replace_texts")
        expected_hashes = expected_hashes or {}
        operations: list[dict[str, Any]] = []
        staged: list[tuple[Path, Path]] = []
        try:
            for index, (path, text) in enumerate(changes.items()):
                target = self._validate_target(Path(path))
                before_exists = target.exists()
                before = target.read_bytes() if before_exists else b""
                expected = expected_hashes.get(Path(path), expected_hashes.get(target))
                actual = sha256_bytes(before) if before_exists else None
                if expected is not None and expected != actual:
                    raise RuntimeError(f"precondition hash mismatch: {target}")
                backup_rel = f"before/{index:04d}.bin"
                backup = self.tx_dir / backup_rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(before)
                encoded = text.encode("utf-8")
                encoded.decode("utf-8", errors="strict")
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(f"{target.name}.tmp-vault-tx-{self.transaction_id}")
                with temp.open("wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                staged.append((temp, target))
                operations.append({
                    "kind": "replace",
                    "target": str(target),
                    "before_exists": before_exists,
                    "before_sha256": actual,
                    "after_sha256": sha256_bytes(encoded),
                    "backup": backup_rel,
                })
            self.data["operations"] = operations
            self._set_state("validated")
            self.lock.assert_owned()
            self._set_state("committing")
            for temp, target in staged:
                # 每个文件替换前都校验所有权：租约被夺的窗口从「整个多文件循环」
                # 缩小到「单次 os.replace 的瞬间」，避免跨写者交错覆盖
                self.lock.assert_owned()
                os.replace(temp, target)
            self.lock.assert_owned()
            self._set_state("committed", committed_at=time.time())
            receipt = {
                "schema": "dsh-vault-transaction-receipt-v1",
                "transaction_id": self.transaction_id,
                "purpose": self.purpose,
                "state": "committed",
                "operations": operations,
                "at": time.time(),
            }
            atomic_json(self.tx_dir / "receipt.json", receipt)
            return receipt
        except Exception as exc:
            for temp, _ in staged:
                temp.unlink(missing_ok=True)
            if self.data.get("state") == "committed":
                # 文件已全部提交，只有 receipt 落盘失败：必须保持 committed 状态，
                # 绝不能翻成 rolled_back（否则 manifest 与文件内容矛盾）
                self._set_state("committed", receipt_error=f"{type(exc).__name__}: {exc}")
                try:
                    atomic_json(self.tx_dir / "receipt.json", {
                        "schema": "dsh-vault-transaction-receipt-v1",
                        "transaction_id": self.transaction_id,
                        "purpose": self.purpose,
                        "state": "committed",
                        "operations": operations,
                        "receipt_error": f"{type(exc).__name__}: {exc}",
                        "at": time.time(),
                    })
                except Exception:
                    pass
                raise
            rollback_error = None
            if self.data.get("state") == "committing":
                try:
                    for op in reversed(operations):
                        target = Path(op["target"])
                        if op["before_exists"]:
                            _atomic_write_bytes(target, (self.tx_dir / op["backup"]).read_bytes())
                        else:
                            target.unlink(missing_ok=True)
                except Exception as rb_exc:
                    rollback_error = f"{type(rb_exc).__name__}: {rb_exc}"
            state = "recovery_required" if rollback_error else "rolled_back"
            self._set_state(
                state,
                error=f"{type(exc).__name__}: {exc}",
                **({"rollback_error": rollback_error} if rollback_error else {}),
            )
            atomic_json(self.tx_dir / "receipt.json", {
                "schema": "dsh-vault-transaction-receipt-v1",
                "transaction_id": self.transaction_id,
                "purpose": self.purpose,
                "state": state,
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            })
            raise

    def append_raw_entry(
        self,
        path: Path,
        *,
        title: str,
        body: str,
        source: str,
        entry_type: str = "fact",
        supersedes: str | None = None,
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.entered:
            raise RuntimeError("transaction must be entered before append_raw_entry")
        target = self._validate_target(path, allow_raw=True)
        target.resolve().relative_to((self.memory / "events").resolve())
        if not target.name.endswith(".raw.md"):
            raise ValueError("raw append target must end with .raw.md")
        if entry_type not in {"fact", "correction", "tombstone"}:
            raise ValueError("entry_type must be fact, correction, or tombstone")
        if entry_type in {"correction", "tombstone"} and not supersedes:
            raise ValueError("correction/tombstone requires supersedes entry_id")
        clean_title = " ".join(title.strip().splitlines())
        if not clean_title or len(clean_title.encode("utf-8")) > 180:
            raise ValueError("raw title must be 1..180 UTF-8 bytes")
        clean_body = body.strip()
        if not clean_body:
            raise ValueError("raw body cannot be empty")
        entry_id = entry_id or uuid.uuid4().hex
        metadata = {
            "id": entry_id,
            "ts": time.time(),
            "type": entry_type,
            "source": source,
            "session_id": os.environ.get("DSH_SESSION_ID"),
            "supersedes": supersedes,
            "body_sha256": sha256_bytes(clean_body.encode("utf-8")),
        }
        header = b""
        if not target.exists() or target.stat().st_size == 0:
            header = f"# {target.name.split('.')[0]} raw\n\n".encode("utf-8")
        block = (
            f"## {clean_title}\n"
            f"<!-- dsh-entry: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)} -->\n"
            f"{clean_body}\n\n"
        ).encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        operation = {
            "kind": "raw_append",
            "target": str(target),
            "entry_id": entry_id,
            "before_size": target.stat().st_size if target.exists() else 0,
            "append_sha256": sha256_bytes(header + block),
        }
        self.data["operations"] = [operation]
        self._set_state("validated")
        self.lock.assert_owned()
        self._set_state("committing")
        try:
            fd = os.open(target, os.O_CREAT | os.O_APPEND | os.O_WRONLY)
            try:
                view = memoryview(header + block)
                while view:
                    count = os.write(fd, view)
                    view = view[count:]
                os.fsync(fd)
            finally:
                os.close(fd)
            operation["after_size"] = target.stat().st_size
            operation["after_sha256"] = sha256_file(target)
            self.data["operations"] = [operation]
            self._set_state("committed", committed_at=time.time())
            receipt = {
                "schema": "dsh-vault-transaction-receipt-v1",
                "transaction_id": self.transaction_id,
                "purpose": self.purpose,
                "state": "committed",
                "operations": [operation],
                "at": time.time(),
            }
            atomic_json(self.tx_dir / "receipt.json", receipt)
            return receipt
        except Exception as exc:
            try:
                present = f'"id": "{entry_id}"' in target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                present = False
            state = "committed_recovered" if present else "rolled_back"
            self._set_state(state, error=f"{type(exc).__name__}: {exc}")
            atomic_json(self.tx_dir / "receipt.json", {
                "schema": "dsh-vault-transaction-receipt-v1",
                "transaction_id": self.transaction_id,
                "purpose": self.purpose,
                "state": state,
                "operations": [operation],
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            })
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.data.get("state") not in TERMINAL_STATES | {"recovery_required"}:
                self._set_state("rolled_back", error="transaction exited before commit")
        finally:
            self.lock.release()
            self.entered = False
