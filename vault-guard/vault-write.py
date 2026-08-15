"""Authorized recoverable writer for the DSH Markdown Vault (dry-run first)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from vault_tx import TERMINAL_STATES, VaultTransaction, sha256_file

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


VAULT = Path(os.environ.get("MEMORY_VAULT") or (Path.home() / "Documents" / "Obsidian Vault"))
MEMORY = VAULT / "memory"


def pending_transactions() -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    root = MEMORY / ".vault-transactions"
    for manifest in root.glob("*/manifest.json") if root.exists() else []:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("state") not in TERMINAL_STATES:
            pending.append({"transaction_id": str(data.get("transaction_id")), "state": str(data.get("state"))})
    return pending


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DSH Vault writer; mutations require --apply")
    sub = parser.add_subparsers(dest="command", required=True)
    raw = sub.add_parser("raw", help="append one immutable raw evidence entry")
    raw.add_argument("--title", required=True)
    raw.add_argument("--body-file", type=Path, required=True)
    raw.add_argument("--source", required=True)
    raw.add_argument("--date", default=date.today().isoformat())
    raw.add_argument("--kind", choices=("fact", "correction", "tombstone"), default="fact")
    raw.add_argument("--supersedes")
    raw.add_argument("--apply", action="store_true")
    replace = sub.add_parser("replace", help="replace one non-raw Vault file transactionally")
    replace.add_argument("--target", type=Path, required=True)
    replace.add_argument("--source-file", type=Path, required=True)
    replace.add_argument("--expected-sha256")
    replace.add_argument("--apply", action="store_true")
    recover = sub.add_parser("recover", help="inspect or recover unfinished transactions")
    recover.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "raw":
            body = args.body_file.read_text(encoding="utf-8")
            target = MEMORY / "events" / f"{args.date}.raw.md"
            if not args.apply:
                print(f"dry-run: append {args.kind} to {target}; title={args.title!r}; bytes={len(body.encode('utf-8'))}")
                return 0
            with VaultTransaction(vault=VAULT, purpose="raw-append", targets=[target]) as tx:
                receipt = tx.append_raw_entry(
                    target,
                    title=args.title,
                    body=body,
                    source=args.source,
                    entry_type=args.kind,
                    supersedes=args.supersedes,
                )
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
            return 0
        if args.command == "replace":
            text = args.source_file.read_text(encoding="utf-8")
            target = args.target.resolve()
            current = sha256_file(target)
            if args.expected_sha256 and current != args.expected_sha256:
                raise RuntimeError(f"precondition hash mismatch before plan: {target}")
            if not args.apply:
                print(f"dry-run: replace {target}; before={current}; after-bytes={len(text.encode('utf-8'))}")
                return 0
            expected = {target: args.expected_sha256} if args.expected_sha256 else {}
            with VaultTransaction(vault=VAULT, purpose="authorized-replace", targets=[target]) as tx:
                receipt = tx.replace_texts({target: text}, expected_hashes=expected)
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
            return 0
        pending = pending_transactions()
        if not args.apply:
            print(json.dumps({"dry_run": True, "pending": pending}, ensure_ascii=False, indent=2))
            return 0
        with VaultTransaction(vault=VAULT, purpose="transaction-recovery", targets=[]) as tx:
            recovered = tx.data.get("recovered_before_begin", [])
            tx.commit_noop()
        print(json.dumps({"recovered": recovered}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"vault-write blocked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

