"""evidence-ledger report — 账本权重/错误率报表（轨迹复盘/反向优化消费入口）。

用法：python report.py
输出：按插件聚合的调用权重与错误率 + 按工具明细 + 未映射工具提示。
"""
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DSH_HOME = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
STORE = DSH_HOME / "storages" / "tool-telemetry.json"


def main():
    if not STORE.exists():
        print("no ledger yet")
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
