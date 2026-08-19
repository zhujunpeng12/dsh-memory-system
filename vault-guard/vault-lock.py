"""Compatibility CLI for the lease-based Vault single-writer lock."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from vault_lock import LockBusyError, VaultLock, describe_status, release_by_holder

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


from vault_path import vault_root

MEM = vault_root() / "memory"
LOCK = MEM / ".vault.lock"


def default_holder() -> str:
    return os.environ.get("DSH_SESSION_ID") or f"shell-{os.getppid()}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DSH Vault lease lock")
    parser.add_argument("command", nargs="?", choices=("acquire", "release", "status"), default="status")
    parser.add_argument("--holder", default=default_holder())
    parser.add_argument("--transaction-id")
    parser.add_argument("--purpose", default="manual-maintenance")
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--wait", type=float, default=0.0)
    parser.add_argument("--lease", type=float, default=600.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "status":
        status = describe_status(LOCK)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        elif status["state"] == "free":
            print("free")
        elif status["state"] == "corrupt":
            print(f"corrupt lock ({int(status.get('age_seconds', 0))}s old)")
        else:
            lease = status.get("lease_remaining_seconds")
            lease_text = "unknown" if lease is None else f"{int(lease)}s"
            print(
                "locked by",
                status.get("holder"),
                f"txn={status.get('transaction_id')}",
                f"lease={lease_text}",
                f"pid_alive={status.get('owner_alive')}",
            )
        return 0

    if args.command == "release":
        if release_by_holder(LOCK, args.holder):
            print("lock released")
        else:
            print("no lock held by this holder")
        return 0

    transaction_id = args.transaction_id or args.holder
    lock = VaultLock(
        LOCK,
        holder=args.holder,
        transaction_id=transaction_id,
        purpose=args.purpose,
        targets=args.target,
        lease_seconds=args.lease,
        wait_seconds=args.wait,
        owner_pid=os.getppid(),
        enable_heartbeat=False,
        force=args.force,
    )
    try:
        lock.acquire()
    except LockBusyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"lock acquired: {args.holder} txn={transaction_id} lease={int(args.lease)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

