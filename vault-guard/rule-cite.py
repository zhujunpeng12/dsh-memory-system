"""Safely increment one exact rules.md citation; dry-run by default."""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from rules_core import render_core
from vault_tx import VaultTransaction, sha256_file

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


HOME = Path.home()
VAULT = Path(os.environ.get("MEMORY_VAULT") or (HOME / "Documents" / "Obsidian Vault"))
MEMORY = VAULT / "memory"
RULES = MEMORY / "rules.md"
CORE = MEMORY / "rules-core.md"
RULE_ID_RE = re.compile(r"^[0-9]+[a-z]?$")


def increment_rule_text(text: str, rule_id: str) -> tuple[str, int, int]:
    if not RULE_ID_RE.fullmatch(rule_id):
        raise ValueError("规则编号只允许数字及一个小写字母后缀，例如 12 或 12b")
    pattern = re.compile(
        rf"^(?P<prefix>{re.escape(rule_id)}\.\s+⏱×)(?P<count>\d+)(?P<rest>.*)$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"规则 {rule_id} 命中 {len(matches)} 条；必须恰好命中一条才允许更新")
    match = matches[0]
    old = int(match.group("count"))
    new = old + 1
    replacement = f"{match.group('prefix')}{new}{match.group('rest')}"
    return text[: match.start()] + replacement + text[match.end() :], old, new


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Preview or apply one exact rules.md citation increment.")
    parser.add_argument("rule_id", help="精确规则编号，例如 4 或 12b")
    parser.add_argument("--apply", action="store_true", help="实际写入；省略时只预览")
    args = parser.parse_args(argv)
    try:
        current = RULES.read_text(encoding="utf-8")
        _, old, new = increment_rule_text(current, args.rule_id)
    except (OSError, ValueError) as exc:
        print(f"rule-cite blocked: {exc}", file=sys.stderr)
        return 1
    if not args.apply:
        print(f"dry-run: rule {args.rule_id} ⏱×{old} → ⏱×{new}; add --apply to write")
        return 0
    try:
        with VaultTransaction(vault=VAULT, purpose=f"rule-cite:{args.rule_id}", targets=[RULES, CORE]) as tx:
            current = RULES.read_text(encoding="utf-8")
            updated, old, new = increment_rule_text(current, args.rule_id)
            core_text, core_count = render_core(updated)
            tx.replace_texts(
                {RULES: updated, CORE: core_text},
                expected_hashes={RULES: sha256_file(RULES), CORE: sha256_file(CORE)},
            )
        print(f"updated transactionally: rule {args.rule_id} ⏱×{old} → ⏱×{new}; {core_count} 条核心规则")
        return 0
    except Exception as exc:
        print(f"rule-cite blocked after plan: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

