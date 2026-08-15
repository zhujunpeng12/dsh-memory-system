"""Lease-based single-writer lock for the local Markdown Vault."""
from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "dsh-vault-lock-v2"
DEFAULT_LEASE_SECONDS = 30.0
DEFAULT_HEARTBEAT_SECONDS = 5.0
LEGACY_STALE_SECONDS = 600.0


class LockBusyError(RuntimeError):
    """Another healthy writer owns the lock."""


class LockOwnershipError(RuntimeError):
    """The transaction no longer owns its lock token."""


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_lock(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _windows_process_state(pid: int) -> tuple[bool, str | None]:
    """Query Windows process state without using os.kill.

    ``os.kill(pid, 0)`` is a POSIX probe but is not safe to use as one on
    Windows.  Use typed Win32 handles so 64-bit HANDLE values are never
    truncated by ctypes' default ``c_int`` return type.
    """
    if pid <= 0:
        return False, None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        filetime_ptr = ctypes.POINTER(wintypes.FILETIME)
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            filetime_ptr,
            filetime_ptr,
            filetime_ptr,
            filetime_ptr,
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Access denied means the process exists but cannot be inspected.
            return (True, None) if ctypes.get_last_error() == 5 else (False, None)
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True, None
            if exit_code.value != 259:  # STILL_ACTIVE
                return False, None
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return True, None
            fingerprint = str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
            return True, fingerprint
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        # Unknown is treated as alive; a failed probe must never authorize
        # stealing an active writer's lock.
        return True, None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_state(pid)[0]
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def process_fingerprint(pid: int) -> str | None:
    """Return a PID-reuse-resistant process creation fingerprint when possible."""
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_state(pid)[1]
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
    except Exception:
        return None


def describe_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"state": "free"}
    meta = read_lock(path)
    if meta is None:
        age = max(0.0, time.time() - path.stat().st_mtime)
        return {"state": "corrupt", "age_seconds": age}
    now = time.time()
    pid = int(meta.get("pid") or 0)
    alive = pid_alive(pid)
    recorded = meta.get("process_start")
    current = process_fingerprint(pid) if alive else None
    lease_until = float(meta.get("lease_until") or 0)
    acquired_at = float(meta.get("acquired_at") or meta.get("at") or 0)
    return {
        "state": "locked",
        **meta,
        "age_seconds": max(0.0, now - acquired_at),
        "lease_remaining_seconds": lease_until - now if lease_until else None,
        "owner_alive": alive,
        "pid_reused": bool(alive and recorded and current and recorded != current),
    }


def reclaim_reason(path: Path, meta: dict[str, Any] | None, *, force: bool = False) -> str | None:
    now = time.time()
    if meta is None:
        try:
            age = now - path.stat().st_mtime
        except OSError:
            return "lock-disappeared"
        return "corrupt-lock" if force or age >= LEGACY_STALE_SECONDS else None
    if meta.get("schema") != SCHEMA:
        age = now - float(meta.get("at") or meta.get("acquired_at") or 0)
        return "legacy-stale" if age >= LEGACY_STALE_SECONDS else None
    pid = int(meta.get("pid") or 0)
    if pid and not pid_alive(pid):
        return "owner-process-dead"
    recorded = meta.get("process_start")
    current = process_fingerprint(pid) if pid else None
    if recorded and current and recorded != current:
        return "owner-pid-reused"
    lease_until = float(meta.get("lease_until") or 0)
    heartbeat_at = float(meta.get("heartbeat_at") or meta.get("acquired_at") or 0)
    lease_seconds = max(1.0, float(meta.get("lease_seconds") or DEFAULT_LEASE_SECONDS))
    if lease_until and now > lease_until and now - heartbeat_at > lease_seconds:
        return "lease-expired"
    return None


def _recovery_receipt(lock_path: Path, previous: dict[str, Any] | None, reason: str) -> None:
    root = lock_path.parent / ".vault-transactions" / "recoveries"
    atomic_json(root / f"lock-{int(time.time())}-{uuid.uuid4().hex}.json", {
        "schema": "dsh-vault-lock-recovery-v1",
        "at": time.time(),
        "reason": reason,
        "previous": previous,
    })


def release_by_holder(path: Path, holder: str) -> bool:
    meta = read_lock(path)
    if meta and meta.get("holder") == holder:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return True
    return False


class VaultLock:
    def __init__(
        self,
        path: Path,
        *,
        holder: str,
        transaction_id: str,
        purpose: str = "vault-write",
        targets: Iterable[str] = (),
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        wait_seconds: float = 0.0,
        owner_pid: int | None = None,
        enable_heartbeat: bool = True,
        force: bool = False,
    ) -> None:
        self.path = path
        self.holder = holder
        self.transaction_id = transaction_id
        self.purpose = purpose
        self.targets = list(targets)
        self.lease_seconds = max(5.0, float(lease_seconds))
        self.heartbeat_seconds = max(1.0, min(float(heartbeat_seconds), self.lease_seconds / 2))
        self.wait_seconds = max(0.0, float(wait_seconds))
        self.owner_pid = int(owner_pid or os.getpid())
        self.enable_heartbeat = enable_heartbeat
        self.force = force
        self.token = uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.acquired = False

    def _metadata(self) -> dict[str, Any]:
        now = time.time()
        return {
            "schema": SCHEMA,
            "holder": self.holder,
            "token": self.token,
            "transaction_id": self.transaction_id,
            "pid": self.owner_pid,
            "process_start": process_fingerprint(self.owner_pid),
            "purpose": self.purpose,
            "targets": self.targets,
            "acquired_at": now,
            "heartbeat_at": now,
            "lease_seconds": self.lease_seconds,
            "lease_until": now + self.lease_seconds,
        }

    def _create(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(self._metadata(), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            view = memoryview(raw)
            while view:
                count = os.write(fd, view)
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _try_reclaim(self) -> str | None:
        try:
            before = self.path.read_bytes()
        except FileNotFoundError:
            return "lock-disappeared"
        meta = read_lock(self.path)
        reason = reclaim_reason(self.path, meta, force=self.force)
        if not reason:
            return None
        try:
            if self.path.read_bytes() != before:
                return None
            self.path.unlink()
        except FileNotFoundError:
            return "lock-disappeared"
        _recovery_receipt(self.path, meta, reason)
        return reason

    def acquire(self) -> "VaultLock":
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                self._create()
                self.acquired = True
                if self.enable_heartbeat:
                    self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
                    self._thread.start()
                return self
            except FileExistsError:
                if self._try_reclaim():
                    continue
                if time.monotonic() >= deadline:
                    status = describe_status(self.path)
                    raise LockBusyError(
                        f"Vault lock busy: holder={status.get('holder')} "
                        f"txn={status.get('transaction_id')} purpose={status.get('purpose')}"
                    )
                time.sleep(min(0.5, max(0.05, deadline - time.monotonic())) + random.uniform(0, 0.08))

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            if not self.renew():
                return

    def renew(self) -> bool:
        meta = read_lock(self.path)
        if not meta or meta.get("token") != self.token:
            return False
        now = time.time()
        meta["heartbeat_at"] = now
        meta["lease_until"] = now + self.lease_seconds
        try:
            atomic_json(self.path, meta)
            return True
        except OSError:
            return False

    def assert_owned(self) -> None:
        meta = read_lock(self.path)
        if not meta or meta.get("token") != self.token:
            raise LockOwnershipError("Vault lock ownership was lost before commit")

    def release(self) -> bool:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.heartbeat_seconds + 1)
        meta = read_lock(self.path)
        if meta and meta.get("token") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.acquired = False
            return True
        self.acquired = False
        return False

    def __enter__(self) -> "VaultLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
