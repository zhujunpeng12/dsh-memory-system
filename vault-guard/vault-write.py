"""Authorized recoverable writer for the DSH Markdown Vault (dry-run first)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from vault_tx import TERMINAL_STATES, VaultTransaction, sha256_file

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


VAULT = Path.home() / "Documents" / "Obsidian Vault"
MEMORY = VAULT / "memory"

# PowerShell 反引号转义防护：内联字符串里的 `` `a `` 会被解释成 BEL(0x07)+'a'，
# 直接污染 Vault 正文（2026-08-18 实际事故：`ax.get_position()` 被写成 BEL+'x.get_position()'）。
_CONTROL_NAMES = {0x00: "NUL", 0x07: "BEL", 0x1B: "ESC"}

CONTROL_CHAR_HINT = (
    "疑似 PowerShell 转义污染：反引号在 PowerShell 内联字符串里会被解释成转义序列"
    "（如 `` `a `` → BEL 0x07）。写入 Vault 的正文禁止包含控制字符；"
    "正确做法：先把内容写入 UTF-8 临时文件，再用 --body-file / --source-file 传路径，"
    "或直接用 read/edit 工具。"
)


def control_char_hits(text: str) -> list[tuple[str, int]]:
    """C0 控制字符 (0x00-0x1F) 命中列表，排除无害的 \\n \\r \\t。"""
    hits: list[tuple[str, int]] = []
    for index, ch in enumerate(text):
        code = ord(ch)
        if code < 0x20 and code not in (0x09, 0x0A, 0x0D):
            hits.append((_CONTROL_NAMES.get(code, f"U+{code:04X}"), index))
    return hits


def guard_control_chars(text: str, *, apply: bool) -> None:
    """--apply 时发现控制字符即拒绝（在进入事务前阻断）；dry-run 只告警不阻断。"""
    hits = control_char_hits(text)
    if not hits:
        return
    detail = "、".join(f"{name}@{pos}" for name, pos in hits[:8])
    if len(hits) > 8:
        detail += f" …共 {len(hits)} 处"
    message = f"检测到控制字符 ({detail})：{CONTROL_CHAR_HINT}"
    if apply:
        raise RuntimeError(message)
    print(f"⚠️ dry-run 控制字符告警：{message}")


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
            guard_control_chars(args.title, apply=args.apply)
            guard_control_chars(body, apply=args.apply)
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
            guard_control_chars(text, apply=args.apply)
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

