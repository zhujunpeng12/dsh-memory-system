"""Generate read-only trajectory-review candidates from DSH evidence.

The report is deliberately conservative: user corrections and aggregate tool
errors are candidates, never automatic verdicts. It does not write Vault files,
increment rule counts, or graduate rules.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import zstandard
except ImportError:  # The tool can still report telemetry without session parsing.
    zstandard = None

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DSH_HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
SESSIONS = DSH_HOME / "sessions"
LEDGER = DSH_HOME / "storages" / "tool-telemetry.json"

CORRECTION_RE = re.compile(
    r"^\s*(?:不对|不是(?:这个|这样|我要的)|你(?:还)?没(?:有)?|还没(?:有)?|仍(?:然)?没(?:有)?|"
    r"我说的是|我需要(?:了解|的是)|请(?:先)?修|纠正|错了|有误|实际并非)",
    re.IGNORECASE,
)


def extract_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (extract_text(item) for item in value)))
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if "content" in value:
            return extract_text(value["content"])
    return ""


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_correction(text: str) -> bool:
    return bool(CORRECTION_RE.search(normalize_text(text)))


def clip(text: str, limit: int = 240) -> str:
    text = normalize_text(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def read_events(path: Path):
    if zstandard is None:
        return
    dctx = zstandard.ZstdDecompressor()
    with path.open("rb") as source, dctx.stream_reader(source) as reader:
        text = reader.read().decode("utf-8", errors="replace")
    for line in text.splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def same_workspace(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except Exception:
        return left.casefold() == right.casefold()


def format_time(value) -> str:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            pass
    return str(value or "unknown")


def session_candidates(days: int, max_sessions: int, workspace: str | None, max_items: int):
    if zstandard is None or not SESSIONS.exists():
        return [], "zstandard unavailable or sessions directory missing"
    cutoff = time.time() - days * 86400
    logs = sorted(SESSIONS.rglob("session.jsonl.zstd"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidates = []
    seen = set()
    inspected = 0
    for path in logs:
        if inspected >= max_sessions:
            break
        if path.stat().st_mtime < cutoff:
            break
        events = list(read_events(path) or [])
        meta = next((event for event in events if event.get("type") == "session"), {})
        cwd = str(meta.get("cwd") or "unknown")
        if workspace and not same_workspace(cwd, workspace):
            continue
        inspected += 1
        session_id = str(meta.get("id") or path.parent.name)
        for event in events:
            if event.get("type") != "user/message":
                continue
            data = event.get("data") or {}
            message = normalize_text(extract_text(data.get("content")))
            if not message or not is_correction(message):
                continue
            key = (cwd.casefold(), message.casefold())
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "time": format_time(event.get("time")),
                    "session": session_id,
                    "cwd": cwd,
                    "message": clip(message),
                    "source": str(path),
                }
            )
            if len(candidates) >= max_items:
                return candidates, f"inspected {inspected} sessions"
    return candidates, f"inspected {inspected} sessions"


def tool_candidates(workspace: str | None, min_errors: int, max_items: int):
    if not LEDGER.exists():
        return []
    store = json.loads(LEDGER.read_text(encoding="utf-8"))
    plugins = store.get("plugins", {})
    output = []
    for tool_name, tool in (store.get("tools") or {}).items():
        if workspace:
            entry = next(
                (value for key, value in (tool.get("workspaces") or {}).items() if same_workspace(key, workspace)),
                None,
            )
            calls = int((entry or {}).get("calls", 0))
            errors = int((entry or {}).get("errors", 0))
        else:
            calls = int(tool.get("calls", 0))
            errors = int((tool.get("errors") or {}).get("total", 0))
        if calls <= 0 or errors < min_errors:
            continue
        output.append(
            {
                "tool": tool_name,
                "plugin": plugins.get(tool_name, "unknown"),
                "calls": calls,
                "errors": errors,
                "rate": round(errors / calls * 100, 1),
            }
        )
    output.sort(key=lambda item: (-item["errors"], -item["rate"], item["tool"]))
    return output[:max_items]


def markdown_report(corrections, tools, scope: str, note: str) -> str:
    lines = [
        "# 轨迹复盘候选（只读）",
        "",
        "> 用户纠正和工具非零只是待核查证据，不自动判错、不自动写 Vault、不自动增加规则计数。",
        f"> 范围：{scope}；{note}",
        "",
        "## 用户纠正候选",
        "",
    ]
    if not corrections:
        lines.append("- 无强纠正信号；这不代表没有需要人工复盘的内容。")
    for item in corrections:
        lines.extend(
            [
                f"- [ ] {item['time']}｜{item['cwd']}",
                f"  - 用户证据：{item['message']}",
                f"  - 日志：{item['source']}（session={item['session']}）",
                "  - 待提炼：🔴 错误: <场景> → <错误> → <根因> → <先决动作>",
            ]
        )
    lines.extend(["", "## 工具账本信号", ""])
    if not tools:
        lines.append("- 当前阈值下无重复错误信号。")
    for item in tools:
        lines.append(
            f"- [ ] {item['tool']}｜{item['plugin']}｜errors={item['errors']}/{item['calls']} "
            f"({item['rate']:.1f}%)：先查原始 session 证据，再判断是否为正常探索。"
        )
    lines.extend(
        [
            "",
            "## 人工门禁",
            "",
            "1. 只有确认根因和可执行先决动作后，才提炼到 events。",
            "2. 同一模式近 14 天至少 3 次，或一次造成严重后果，才考虑毕业到 rules。",
            "3. 只有明确引用或确实执行某条规则后，才用 rule-cite.py 增加计数。",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate conservative, read-only DSH trajectory candidates.")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--max-sessions", type=int, default=20)
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--min-tool-errors", type=int, default=3)
    parser.add_argument("--cwd", default=os.getcwd(), help="默认只看当前工作区")
    parser.add_argument("--all-workspaces", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.days < 1 or args.max_sessions < 1 or args.max_items < 1 or args.min_tool_errors < 1:
        parser.error("days/max-sessions/max-items/min-tool-errors must all be positive")

    workspace = None if args.all_workspaces else args.cwd
    corrections, note = session_candidates(args.days, args.max_sessions, workspace, args.max_items)
    tools = tool_candidates(workspace, args.min_tool_errors, args.max_items)
    scope = "all workspaces" if workspace is None else workspace
    if args.json:
        print(json.dumps({"scope": scope, "note": note, "corrections": corrections, "tools": tools}, ensure_ascii=False, indent=2))
    else:
        print(markdown_report(corrections, tools, scope, note), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
