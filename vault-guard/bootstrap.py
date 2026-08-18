"""Build one bounded hot-memory packet for a DSH session."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


HOME = Path.home()
HERE = Path(__file__).resolve().parent
VAULT = Path(os.environ.get("MEMORY_VAULT") or (HOME / "Documents" / "Obsidian Vault"))
DSH_HOME = Path(os.environ.get("DSH_HOME") or (HOME / ".dsh"))
MEMORY = VAULT / "memory"
PROJECTS = VAULT / "projects"
RULE_RE = re.compile(r"^(\d+[a-z]?\.)\s+⏱×(\d+)(\s+⭐)?")
INSTRUCTION_BUDGET = 65_536
INSTRUCTION_SOURCE_WARN = 49_152
PROJECT_CHAIN_SOFT_LIMIT = 32_768
SINGLE_INSTRUCTION_SOFT_LIMIT = 8_192
INSTRUCTION_CANDIDATES = ("AGENTS.md", "CLAUDE.md", "AGENTS.local.md", "CLAUDE.local.md")


def utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def clip_utf8(text: str, limit: int, suffix: str = "\n…[已按字节预算截断，完整正文见上方来源]\n") -> str:
    if utf8_size(text) <= limit:
        return text
    suffix_bytes = suffix.encode("utf-8")
    raw = text.encode("utf-8")[: max(0, limit - len(suffix_bytes))]
    while raw:
        try:
            return raw.decode("utf-8") + suffix
        except UnicodeDecodeError:
            raw = raw[:-1]
    return suffix[:limit]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        # 非 UTF-8/损坏文件不能崩掉整个热包——记为读取失败继续
        return f"[读取失败：{path}：{exc}]"


def run_gate() -> str:
    check = HERE / "check.py"
    try:
        result = subprocess.run(
            [sys.executable, str(check)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        if not output:
            output = f"门禁无文本输出（exit={result.returncode}）"
        return f"exit={result.returncode}\n{output}"
    except Exception as exc:  # The packet must remain usable even when the gate fails.
        return f"门禁执行失败：{exc}"


def find_loader_project_root(cwd: Path) -> Path:
    """Mirror the loader default: nearest .git ancestor, otherwise cwd."""
    resolved_cwd = cwd.resolve()
    current = resolved_cwd
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return resolved_cwd
        current = current.parent


def loader_instruction_sources(cwd: Path, dsh_home: Path | None = None) -> list[tuple[Path, int, str]]:
    """Collect source files with the loader's default order and per-directory dedup."""
    resolved_home = dsh_home or DSH_HOME
    sources: list[tuple[Path, int, str]] = []
    global_file = resolved_home / "AGENTS.md"
    if global_file.is_file():
        sources.append((global_file, global_file.stat().st_size, "global"))

    resolved_cwd = cwd.resolve()
    project_root = find_loader_project_root(resolved_cwd)
    directories: list[Path] = []
    current = resolved_cwd
    while True:
        directories.append(current)
        if current == project_root:
            break
        current = current.parent

    for directory in reversed(directories):
        seen_trimmed: set[str] = set()
        for candidate in INSTRUCTION_CANDIDATES:
            path = directory / candidate
            if not path.is_file():
                continue
            try:
                trimmed = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if trimmed in seen_trimmed:
                continue
            seen_trimmed.add(trimmed)
            sources.append((path, path.stat().st_size, "project"))
    return sources


def instruction_budget_audit(cwd: Path, dsh_home: Path | None = None) -> str:
    sources = loader_instruction_sources(cwd, dsh_home)
    total = sum(size for _, size, _ in sources)
    project_total = sum(size for _, size, scope in sources if scope == "project")
    large = [(path, size) for path, size, scope in sources if scope == "project" and size > SINGLE_INSTRUCTION_SOFT_LIMIT]
    lines = [
        f"指令源链：{len(sources)} 个文件，{total}/{INSTRUCTION_BUDGET} B（渲染包装与热包未计）",
        f"项目源链：{project_total}/{PROJECT_CHAIN_SOFT_LIMIT} B；单文件软上限 {SINGLE_INSTRUCTION_SOFT_LIMIT} B",
    ]
    if large:
        names = "、".join(f"{path.name}={size}B" for path, size in large[:3])
        lines.append(f"⚠️ 项目指令单文件超 8KB：{names}")
    if project_total > PROJECT_CHAIN_SOFT_LIMIT:
        lines.append("⚠️ 项目指令聚合超 32KB：建议把细节迁为指针")
    if total > INSTRUCTION_SOURCE_WARN:
        lines.append("⚠️ 指令源链超过 48KB：检查 DSH 可见的 omitted/truncated 预算提示")
    if not large and project_total <= PROJECT_CHAIN_SOFT_LIMIT and total <= INSTRUCTION_SOURCE_WARN:
        lines.append("✅ 指令源链低于软预警线")
    return "\n".join(lines)


def parse_rules(path: Path) -> list[dict[str, object]]:
    text = read_text(path)
    lines = text.splitlines()
    rules: list[dict[str, object]] = []
    section = "未分组"
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("## "):
            section = line[3:].strip()
            index += 1
            continue
        match = RULE_RE.match(line)
        if not match:
            index += 1
            continue
        block = [line]
        cursor = index + 1
        while cursor < len(lines) and not RULE_RE.match(lines[cursor]) and not lines[cursor].startswith("## "):
            block.append(lines[cursor])
            cursor += 1
        rules.append(
            {
                "order": index,
                "section": section,
                "count": int(match.group(2)),
                "star": bool(match.group(3)),
                "text": "\n".join(block).strip(),
            }
        )
        index = cursor
    return rules


def select_rules(path: Path, budget: int) -> str:
    rules = parse_rules(path)
    ranked = sorted(rules, key=lambda item: (not item["star"], -int(item["count"]), int(item["order"])))
    selected: list[dict[str, object]] = []
    used = 0
    for item in ranked:
        cost = utf8_size(str(item["text"])) + utf8_size(str(item["section"])) + 12
        if used + cost <= budget:
            selected.append(item)
            used += cost
    selected.sort(key=lambda item: int(item["order"]))
    chunks: list[str] = []
    active_section = None
    for item in selected:
        if item["section"] != active_section:
            active_section = item["section"]
            chunks.append(f"### {active_section}")
        chunks.append(str(item["text"]))
    omitted = len(rules) - len(selected)
    chunks.append(f"\n[已选 {len(selected)}/{len(rules)} 条；省略 {omitted} 条。完整来源：{path}]")
    return "\n\n".join(chunks)


def find_project(cwd: Path) -> tuple[Path | None, Path | None]:
    if not PROJECTS.exists():
        return None, None
    ancestor_names = [part.name.casefold() for part in (cwd, *cwd.parents)]
    matches = [entry for entry in PROJECTS.iterdir() if entry.is_dir() and entry.name.casefold() in ancestor_names]
    if not matches:
        return None, None
    matches.sort(key=lambda entry: ancestor_names.index(entry.name.casefold()))
    project_dir = matches[0]
    note = project_dir / f"{project_dir.name}.md"
    return project_dir, note if note.exists() else None


def project_excerpt(cwd: Path, budget: int) -> str:
    project_dir, note = find_project(cwd)
    if not project_dir:
        return f"未从 cwd 祖先匹配到 Vault 项目。项目根：{PROJECTS}"
    if not note:
        return f"匹配项目：{project_dir.name}\n主笔记缺失：{project_dir / (project_dir.name + '.md')}"
    text = read_text(note)
    if utf8_size(text) <= budget:
        excerpt = text
    else:
        head_budget = min(700, budget // 3)
        tail_budget = max(0, budget - head_budget - 120)
        excerpt = clip_utf8(text, head_budget, "\n…[中段省略]…\n") + clip_utf8(text[-tail_budget:], tail_budget)
    toolmap = project_dir / f"{project_dir.name}-toolmap.md"
    return f"匹配项目：{project_dir.name}\n主笔记：{note}\n工具便签：{toolmap}\n\n{excerpt}"


def recent_event_index(
    days: int = 3,
    lookback_days: int = 14,
    max_headings_per_day: int = 6,
    max_heading_bytes: int = 180,
    per_day_heading_budget: int = 480,
    today: date | None = None,
) -> str:
    events = MEMORY / "events"
    chunks: list[str] = []
    anchor = today or date.today()
    found = 0
    for offset in range(lookback_days):
        day = anchor - timedelta(days=offset)
        path = events / f"{day.isoformat()}.md"
        if not path.is_file() or path.stat().st_size == 0:
            continue
        headings = [
            clip_utf8(line.strip(), max_heading_bytes, "…")
            for line in read_text(path).splitlines()
            if line.startswith("## ")
        ]
        shown_reversed: list[str] = []
        used = 0
        for heading in reversed(headings):
            if len(shown_reversed) >= max_headings_per_day:
                break
            cost = utf8_size(heading) + 1
            if used + cost > per_day_heading_budget:
                continue
            shown_reversed.append(heading)
            used += cost
        shown = list(reversed(shown_reversed))
        omitted = max(0, len(headings) - len(shown))
        body = "\n".join(shown) if shown else "[无二级事件主标题，按需读取正文]"
        if omitted:
            body = f"[省略较早 {omitted} 个主标题]\n{body}"
        chunks.append(f"### {day.isoformat()}\n来源：{path}\n{body}")
        found += 1
        if found >= days:
            break
    if found < days:
        chunks.append(f"[最近 {lookback_days} 个日历日仅找到 {found} 个有效事件日；更早内容请查月度索引]")
    return "\n\n".join(chunks)


def build_packet(cwd: Path, max_bytes: int) -> str:
    profile = MEMORY / "user_profile.md"
    core = MEMORY / "rules-core.md"
    sections = [
        "# [vault-bootstrap] DSH 热记忆包\n"
        f"cwd：{cwd}\n预算：{max_bytes} UTF-8 bytes\n"
        "使用规则：这是一次性启动摘要；除非任务需要证据，不要重复全读相同来源。",
        "## 机械门禁与指令预算\n" + clip_utf8(run_gate() + "\n" + instruction_budget_audit(cwd), 1700),
        f"## 用户画像\n来源：{profile}\n\n" + clip_utf8(read_text(profile), 2300),
        "## 活跃核心规则\n" + select_rules(core, 4300),
        "## 当前项目摘要\n" + clip_utf8(project_excerpt(cwd, 2500), 2700),
        "## 最近 3 个有效事件日主标题\n" + clip_utf8(recent_event_index(), 2100),
        "## 冷层入口\n"
        f"完整规则：{MEMORY / 'rules.md'}\n"
        f"月度索引：{MEMORY / 'index'}\n"
        f"项目目录：{PROJECTS}\n"
        f"记忆制度：{DSH_HOME / 'docs' / 'MEMORY_SYSTEM.md'}\n"
        f"操作手册：{DSH_HOME / 'docs' / 'AGENT_OPERATIONS.md'}",
    ]
    packet = "\n\n".join(sections).strip() + "\n"
    return clip_utf8(packet, max_bytes, "\n…[热包达到总预算；完整来源见冷层入口]\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a bounded DSH hot-memory packet.")
    parser.add_argument("--cwd", default=str(Path.cwd()))
    parser.add_argument("--max-bytes", type=int, default=14000)
    args = parser.parse_args()
    if args.max_bytes < 2000:
        parser.error("--max-bytes must be at least 2000")
    # 防御：剥离 argv 中可能存在的孤立代理字符（stdin 被错误编码解码后传入），
    # 否则 build_packet 的严格 UTF-8 编码会抛 UnicodeEncodeError。
    args_cwd = args.cwd.encode("utf-8", errors="replace").decode("utf-8")
    try:
        cwd = Path(args_cwd).resolve()
    except OSError:
        cwd = Path(args_cwd)
    print(build_packet(cwd, args.max_bytes), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
