"""Pure rendering logic for rules-core.md."""
from __future__ import annotations

import os
import re


RULE_START = re.compile(r"^\d+[a-z]?\.\s+⏱×\d+")


def block_matches(block: str, min_count: int) -> bool:
    first = block.splitlines()[0] if block.strip() else ""
    match = re.match(r"^(\d+[a-z]?)\.\s+⏱×(\d+)(\s+⭐)?", first)
    return bool(match and (match.group(3) or int(match.group(2)) >= min_count))


def render_core(text: str, min_count: int | None = None) -> tuple[str, int]:
    threshold = int(os.environ.get("VAULT_CORE_MIN", "2")) if min_count is None else int(min_count)
    parts = re.split(r"(?=^\d+[a-z]?\.\s+⏱×\d+)", text, flags=re.M)
    kept = [part.rstrip() for part in parts[1:] if block_matches(part, threshold)]
    header = (
        "# 经验规则（核心常驻层）\n"
        "> 本文件由 sync-core.py 自动生成——**不要手改**，改 rules.md 后自动同步。\n"
        f"> 组成 = ⭐ 人工核心标记规则 + ⏱×N≥{threshold} 的活跃规则（自进化：被用过的自动常驻）。\n"
        "> 完整版与 Why → memory/rules.md（按需 grep 按 §N 查）。\n\n"
    )
    body = "\n\n".join(kept) if kept else "（暂无核心规则被标记，待 rules.md 打 ⭐ 或引用计数累积）"
    return header + body + "\n", len(kept)

