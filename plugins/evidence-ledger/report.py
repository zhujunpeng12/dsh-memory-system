"""evidence-ledger report — 账本权重/错误率报表（轨迹复盘/反向优化消费入口）。

用法：python report.py
输出：按插件聚合的调用权重与错误率 + 按工具明细 + 未映射工具提示。

用法：python report.py --snapshot <路径>
输出：v2 快照（tools 全量结构与账本一致），供轨迹复盘做精确差值。
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DSH_HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
STORE = DSH_HOME / "storages" / "tool-telemetry.json"


def write_snapshot(path: Path) -> None:
    store = json.loads(STORE.read_text(encoding="utf-8"))
    tools = store.get("tools", {})
    tot_c = sum(t["calls"] for t in tools.values())
    tot_e = sum(t["errors"]["total"] for t in tools.values())
    ws_counts = {}
    for t in tools.values():
        for ws, w in t.get("workspaces", {}).items():
            ws_counts[ws] = ws_counts.get(ws, 0) + w["calls"]
    unknown_c = ws_counts.pop("unknown", 0)
    main_ws = max(ws_counts.items(), key=lambda kv: kv[1])[0] if ws_counts else None
    snap = {
        "schema": "tool-telemetry-snapshot-v2",
        "taken_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_ts": store.get("updated"),
        "global": {"total_calls": tot_c, "total_errors": tot_e},
        "workspace": {
            "name": main_ws,
            "total_calls": ws_counts.get(main_ws, 0) if main_ws else 0,
        },
        "unknown": {
            "calls": unknown_c,
            "pct": round(unknown_c / tot_c * 100, 1) if tot_c else 0.0,
        },
        "tools": {
            name: {"calls": t["calls"], "errors": dict(t["errors"])}
            for name, t in tools.items()
        },
    }
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"snapshot written: {path}")
    print(
        f"global calls={tot_c} errors={tot_e} ({tot_e/tot_c*100:.1f}%); "
        f"main ws={main_ws}; unknown={unknown_c} ({unknown_c/tot_c*100:.1f}%)"
    )


def main():
    parser = argparse.ArgumentParser(description="ledger report / snapshot")
    parser.add_argument("--snapshot", type=Path, help="write a v2 snapshot to this path")
    args = parser.parse_args()
    if not STORE.exists():
        print("no ledger yet")
        return
    if args.snapshot:
        write_snapshot(args.snapshot)
        return
    store = json.loads(STORE.read_text(encoding="utf-8"))
    plugins = store.get("plugins", {})
    by_plugin = {}
    for tool_name, tool in sorted(store["tools"].items(), key=lambda kv: -kv[1]["calls"]):
        plugin = plugins.get(tool_name, "unknown")
        p = by_plugin.setdefault(plugin, {"calls": 0, "errors": 0})
        p["calls"] += tool["calls"]
        p["errors"] += tool["errors"]["total"]
    print("== by plugin ==")
    for plugin, p in sorted(by_plugin.items(), key=lambda kv: -kv[1]["calls"]):
        rate = p["errors"] / p["calls"] * 100 if p["calls"] else 0.0
        print(f"{plugin:<28} calls={p['calls']:<6} errors={p['errors']:<4} rate={rate:.1f}%")
    print()
    print("== by tool ==")
    for tool_name, tool in sorted(store["tools"].items(), key=lambda kv: -kv[1]["calls"]):
        e = tool["errors"]
        ws = ",".join(
            sorted(tool["workspaces"], key=lambda w: -tool["workspaces"][w]["calls"])[:3]
        )
        print(
            f"{tool_name:<22} plugin={plugins.get(tool_name, 'unknown'):<26} "
            f"calls={tool['calls']:<6} errors={e['total']:<4} "
            f"(exit={e['exit']} sandbox={e['sandbox']} flag={e['flagged']} exc={e['exception']}) ws=[{ws}]"
        )
    unknown = sorted(n for n in store["tools"] if n not in plugins)
    if unknown:
        print()
        print("unmapped tools (add to toolmap.json):", ", ".join(unknown))


if __name__ == "__main__":
    main()
