"""Transactionally regenerate rules-core.md from rules.md."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from rules_core import render_core
from vault_tx import VaultTransaction, sha256_file

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


from vault_path import vault_root

VAULT = vault_root()
MEM = VAULT / "memory"
RULES = MEM / "rules.md"
CORE = MEM / "rules-core.md"


def main() -> int:
    try:
        # 快速路径（无锁）：内容未变直接返回，避免每场会话都拿写锁
        rules_text = RULES.read_text(encoding="utf-8")
        output, count = render_core(rules_text)
        if CORE.exists() and CORE.read_text(encoding="utf-8") == output:
            print(f"{count} 条核心规则（内容未变）")
            return 0
        # 变更路径（拿锁）：锁内重读 rules 并重算哈希先决，消除
        # 「锁外读 rules → 并发改 rules → 提交过期 rules-core」的 TOCTOU 窗口
        with VaultTransaction(vault=VAULT, purpose="sync-rules-core", targets=[CORE]) as tx:
            fresh_text = RULES.read_text(encoding="utf-8")
            fresh_output, fresh_count = render_core(fresh_text)
            if CORE.exists() and CORE.read_text(encoding="utf-8") == fresh_output:
                print(f"{fresh_count} 条核心规则（内容未变）")
                return 0
            expected = sha256_file(CORE)
            tx.replace_texts({CORE: fresh_output}, expected_hashes={CORE: expected})
        print(f"{fresh_count} 条核心规则（事务同步完成）")
        return 0
    except Exception as exc:
        print(f"sync-core blocked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

