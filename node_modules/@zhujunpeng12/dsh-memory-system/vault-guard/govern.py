"""Read-only governance candidates for the DSH Markdown Vault.

This scanner automates evidence collection, never the decision.  It does not
repair transactions, merge rules, archive events, edit raw logs or delete data.
Every finding contains an explicit suggested action and a source pointer so a
human/agent can review it before using the transactional writer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from vault_lock import describe_status, read_lock, reclaim_reason
from vault_tx import TERMINAL_STATES

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


DEFAULT_VAULT = Path(os.environ.get("MEMORY_VAULT") or (Path.home() / "Documents" / "Obsidian Vault"))
DATE_RE = re.compile(r"^(20\d{2})-(\d{2})-(\d{2})")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
RULE_RE = re.compile(r"^(\d+[a-z]?)\.\s+⏱×(\d+)\s+(.*)$", re.I)
CONFLICT_RE = re.compile(r"冲突|不一致|改主意|覆盖旧|取代旧|supersedes|correction", re.I)
GENERIC_HEADINGS = {"错误", "结论", "验证", "复盘", "待办", "后续", "背景", "用户纠正"}


@dataclass
class Candidate:
    category: str
    severity: str
    evidence: str
    detail: str
    suggested_action: str
    automatic: bool = False


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _event_date(path: Path) -> date | None:
    match = DATE_RE.match(path.name)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _normalize_heading(text: str) -> str:
    text = re.sub(r"[\s`*_~#｜|:：，,。.!！?？()（）\[\]【】<>《》+\-/]+", "", text.casefold())
    return text[:160]


def _add(output: list[Candidate], category: str, severity: str, evidence: str, detail: str, action: str) -> None:
    output.append(Candidate(category, severity, evidence, detail, action, False))


def scan_governance(
    vault: Path = DEFAULT_VAULT,
    *,
    recent_days: int = 60,
    archive_days: int = 120,
) -> tuple[list[Candidate], dict[str, object]]:
    memory = vault / "memory"
    events = memory / "events"
    projects = vault / "projects"
    rules = memory / "rules.md"
    lock = memory / ".vault.lock"
    tx_root = memory / ".vault-transactions"
    findings: list[Candidate] = []

    # Reliability: stale/corrupt locks and non-terminal/malformed transactions.
    status = describe_status(lock)
    if status.get("state") == "corrupt":
        _add(findings, "lock-corrupt", "high", str(lock), "锁文件无法解析。", "先运行 vault-write.py recover 预览；确认后再 --apply。")
    elif status.get("state") == "locked":
        reason = reclaim_reason(lock, read_lock(lock))
        if reason:
            _add(
                findings,
                "lock-stale",
                "high",
                str(lock),
                f"锁可回收原因={reason}，holder={status.get('holder')}，txn={status.get('transaction_id')}。",
                "确认没有活跃写入后，用恢复命令处理；不要直接删除锁文件。",
            )

    if tx_root.exists():
        for manifest in sorted(tx_root.glob("*/manifest.json")):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception as exc:
                _add(findings, "transaction-corrupt", "high", str(manifest), f"manifest 无法解析：{exc}", "用备份与 before-image 人工核查，再运行恢复预览。")
                continue
            state = str(data.get("state") or "missing")
            if state not in TERMINAL_STATES:
                _add(
                    findings,
                    "transaction-pending",
                    "high",
                    str(manifest),
                    f"事务 {data.get('transaction_id') or manifest.parent.name} state={state}。",
                    "运行 vault-write.py recover 预览；核对目标哈希后才 --apply。",
                )
        for receipt in sorted(tx_root.glob("*/receipt.json")):
            try:
                data = json.loads(receipt.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("state") == "recovery_required" or data.get("rollback_error"):
                _add(findings, "rollback-failed", "high", str(receipt), "写入失败且回滚未完整完成。", "停止新写入，按 receipt 与 before-image 恢复并复核哈希。")

    # Capture-to-summary gaps. Raw remains append-only evidence and is never edited here.
    for raw in sorted(events.glob("*.raw.md")) if events.exists() else []:
        day = raw.name.split(".")[0]
        event = events / f"{day}.md"
        if not event.exists():
            _add(findings, "raw-unrefined", "medium", str(raw), f"缺少 {event.name}。", "人工提炼事实与结论到 events；保留 raw 原文。")
        elif raw.stat().st_mtime > event.stat().st_mtime + 300:
            _add(findings, "raw-newer-than-event", "medium", str(raw), f"比 {event.name} 新超过 5 分钟。", "只提炼新增段落；不要覆盖或重写 raw。")

    today = date.today()
    recent_cutoff = today - timedelta(days=recent_days)
    archive_cutoff = today - timedelta(days=archive_days)
    heading_occurrences: defaultdict[str, list[tuple[Path, int, str]]] = defaultdict(list)
    old_events: list[Path] = []
    overlong: list[tuple[Path, int, int, str]] = []
    event_files = [path for path in sorted(events.glob("*.md")) if not path.name.endswith(".raw.md")] if events.exists() else []
    for path in event_files:
        event_day = _event_date(path)
        if event_day and event_day < archive_cutoff:
            old_events.append(path)
        for line_no, line in enumerate(_read(path).splitlines(), start=1):
            match = HEADING_RE.match(line)
            if not match:
                continue
            title = match.group(1).strip()
            title_bytes = len(title.encode("utf-8"))
            if title_bytes > 180:
                overlong.append((path, line_no, title_bytes, title))
            if event_day and event_day >= recent_cutoff:
                normalized = _normalize_heading(title)
                if len(normalized) >= 6 and normalized not in GENERIC_HEADINGS:
                    heading_occurrences[normalized].append((path, line_no, title))
            if CONFLICT_RE.search(title):
                _add(
                    findings,
                    "conflict-marker",
                    "medium",
                    f"{path}:{line_no}",
                    f"标题含冲突/修订信号：{title[:160]}",
                    "对照原始证据，人工选择新增/覆盖/合并/并存；不自动裁决。",
                )

    for occurrences in heading_occurrences.values():
        unique_paths = {str(item[0]) for item in occurrences}
        if len(occurrences) < 2 or len(unique_paths) < 2:
            continue
        evidence = "; ".join(f"{path}:{line}" for path, line, _ in occurrences[:4])
        _add(
            findings,
            "duplicate-heading",
            "low",
            evidence,
            f"近 {recent_days} 天出现 {len(occurrences)} 个同名/近同名标题：{occurrences[0][2][:120]}",
            "先比对内容与来源；仅在语义和事实均重复时提案合并。",
        )

    if old_events:
        _add(
            findings,
            "archive-review",
            "low",
            f"{old_events[0]} .. {old_events[-1]}",
            f"共有 {len(old_events)} 个事件文件早于 {archive_cutoff.isoformat()}。",
            "批量生成预览清单，逐项确认仍可检索后再归档；不自动删除。",
        )

    for path, line_no, size, title in overlong:
        _add(
            findings,
            "heading-over-budget",
            "low",
            f"{path}:{line_no}",
            f"标题 {size} UTF-8 bytes，超过 180B：{title[:120]}",
            "把复合主题拆成一主题一标题；正文信息保留在标题下。",
        )

    # Rule lifecycle candidates. Counts inform review; they never prove truth.
    for line_no, line in enumerate(_read(rules).splitlines(), start=1):
        match = RULE_RE.match(line)
        if not match:
            continue
        rule_id, count, body = match.group(1), int(match.group(2)), match.group(3)
        starred = "⭐" in body
        if count >= 3 and not starred:
            _add(findings, "rule-promotion-review", "medium", f"{rules}:{line_no}", f"规则 {rule_id} 已引用 {count} 次但未标核心。", "核对三次独立证据、适用边界与反例后，人工决定是否毕业。")
        elif count == 0 and not starred:
            _add(findings, "rule-zero-citation", "low", f"{rules}:{line_no}", f"规则 {rule_id} 当前引用计数为 0。", "核对是否仍准确、有用且不误导；只生成保留/归档建议，不自动删除。")

    # Size pressure candidates; moving content is a separate approved transaction.
    for path, line_limit, byte_limit, label in [(rules, 250, 32768, "rules")]:
        text = _read(path)
        if text:
            lines, size = len(text.splitlines()), len(text.encode("utf-8"))
            if lines > line_limit or size > byte_limit:
                _add(findings, "file-over-budget", "medium", str(path), f"{label}: {lines} 行 / {size}B，软线 {line_limit} 行 / {byte_limit}B。", "先指针化与去重预览；用户确认后事务化调整。")
    if projects.exists():
        for path in sorted(projects.rglob("*.md")):
            if "toolmap" in path.name.casefold():
                continue
            text = _read(path)
            lines, size = len(text.splitlines()), len(text.encode("utf-8"))
            if lines > 300 or size > 30720:
                _add(findings, "file-over-budget", "medium", str(path), f"project: {lines} 行 / {size}B，软线 300 行 / 30720B。", "保留摘要和索引入口，正文拆入冷层；先预览后写入。")

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda item: (order.get(item.severity, 9), item.category, item.evidence.casefold()))
    counts = Counter(item.category for item in findings)
    summary = {
        "mode": "dry-run/read-only",
        "vault": str(vault),
        "total": len(findings),
        "categories": dict(sorted(counts.items())),
        "automatic_changes": 0,
    }
    return findings, summary


def markdown_report(candidates: list[Candidate], summary: dict[str, object], max_items: int) -> str:
    shown = candidates[:max_items]
    lines = [
        "# DSH 记忆治理候选（dry-run）",
        "",
        "> 只采集证据，不自动合并、晋升、归档、修锁、覆盖 raw 或删除任何内容。",
        f"> Vault：{summary['vault']}；候选 {summary['total']} 条；本次展示 {len(shown)} 条。",
        "",
    ]
    if not shown:
        lines.append("- 当前规则下无治理候选。")
    for index, item in enumerate(shown, start=1):
        lines.extend([
            f"## {index}. [{item.severity}] {item.category}",
            f"- 证据：{item.evidence}",
            f"- 发现：{item.detail}",
            f"- 建议：{item.suggested_action}",
            "- 自动执行：否",
            "",
        ])
    if len(candidates) > len(shown):
        lines.append(f"> 另有 {len(candidates) - len(shown)} 条未展示；使用 `--json --max-items` 扩大查看。")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only DSH Vault governance candidates")
    parser.add_argument("--vault", default=str(DEFAULT_VAULT))
    parser.add_argument("--recent-days", type=int, default=60)
    parser.add_argument("--archive-days", type=int, default=120)
    parser.add_argument("--max-items", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.recent_days < 1 or args.archive_days < args.recent_days or args.max_items < 1:
        parser.error("recent-days >= 1, archive-days >= recent-days, max-items >= 1")
    candidates, summary = scan_governance(Path(args.vault), recent_days=args.recent_days, archive_days=args.archive_days)
    shown = candidates[: args.max_items]
    if args.json:
        print(json.dumps({"summary": summary, "candidates": [asdict(item) for item in shown]}, ensure_ascii=False, indent=2))
    else:
        print(markdown_report(candidates, summary, args.max_items), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
