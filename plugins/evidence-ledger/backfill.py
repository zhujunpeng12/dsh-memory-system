"""evidence-ledger backfill — 回放历史会话日志，播种/重建累计账本。

用法：
  python backfill.py --init     # 账本不存在时才播种（默认）
  python backfill.py --rebuild  # 从全部日志重建（覆盖账本，修复用）
  python backfill.py --report   # 播种/重建后顺带打印权重表

注意：--rebuild 覆盖账本时，运行中的 evidence-ledger 插件内存里仍是旧快照，
其后续写入会基于旧快照覆盖回来 → 重建最好在停服（或重启前）执行；
插件重启后会加载重建后的账本继续累计。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import zstandard
except ImportError:
    zstandard = None  # 无 zstandard 时仅无法回放会话日志；播种空账本仍可用

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DSH_HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
SESSIONS = DSH_HOME / "sessions"
STORE = DSH_HOME / "storages" / "tool-telemetry.json"
TOOLMAP = Path(__file__).parent / "toolmap.json"

EXIT_RE = re.compile(r"\[exit code: [1-9]")
EXC_RE = re.compile(
    r"Traceback \(most recent call last\)|TypeError:|ReferenceError:|SyntaxError:|EINVAL|EPERM"
)


def classify(text, is_error):
    errors = {"total": 0, "flagged": 0, "exit": 0, "sandbox": 0, "exception": 0}
    matched = False
    if is_error:
        errors["flagged"] += 1
        matched = True
    if isinstance(text, str):
        if EXIT_RE.search(text):
            errors["exit"] += 1
            matched = True
        if "[sandbox:" in text:
            errors["sandbox"] += 1
            matched = True
        if EXC_RE.search(text):
            errors["exception"] += 1
            matched = True
    errors["total"] = 1 if matched else 0
    return errors


def walk_logs():
    if not SESSIONS.exists():
        return
    for wsdir in sorted(SESSIONS.iterdir()):
        if not wsdir.is_dir():
            continue
        for sdir in sorted(wsdir.iterdir()):
            if not sdir.is_dir():
                continue
            f = sdir / "session.jsonl.zstd"
            if f.exists():
                yield wsdir.name, sdir.name, f


def read_events(f):
    dctx = zstandard.ZstdDecompressor()
    with open(f, "rb") as raw, dctx.stream_reader(raw) as reader:
        text = reader.read().decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def build(store):
    try:
        store.setdefault("plugins", {}).update(
            json.loads(TOOLMAP.read_text(encoding="utf-8"))
        )
    except Exception:
        pass

    marks = {}
    pending = {}
    for wsname, sid, f in walk_logs():
        cwd = None
        last_seq = 0
        for ev in read_events(f):
            seq = ev.get("seq")
            if isinstance(seq, int) and seq > last_seq:
                last_seq = seq
            etype = ev.get("type")
            if etype == "session":
                cwd = ev.get("cwd")
            elif etype == "tool/call":
                d = ev.get("data") or {}
                pending[(sid, d.get("callId"))] = d.get("name")
            elif etype == "tool/result":
                d = ev.get("data") or {}
                msg = d.get("message") or {}
                cid = (msg.get("source") or {}).get("callId")
                tool_name = pending.pop((sid, cid), None)
                if not tool_name:
                    continue
                text, is_err = "", False
                for block in msg.get("content") or []:
                    if isinstance(block, dict):
                        if block.get("isError"):
                            is_err = True
                        for inner in block.get("content") or []:
                            if isinstance(inner, dict) and inner.get("type") == "text":
                                text += inner.get("text", "")
                tool = store.setdefault("tools", {}).setdefault(
                    tool_name,
                    {
                        "calls": 0,
                        "errors": {"total": 0, "flagged": 0, "exit": 0, "sandbox": 0, "exception": 0},
                        "workspaces": {},
                        "firstSeen": ev.get("time"),
                        "lastSeen": ev.get("time"),
                    },
                )
                tool["calls"] += 1
                if ev.get("time"):
                    tool["lastSeen"] = ev["time"]
                err = classify(text, is_err)
                for k in ("total", "flagged", "exit", "sandbox", "exception"):
                    tool["errors"][k] += err[k]
                ws = cwd or wsname
                w = tool["workspaces"].setdefault(ws, {"calls": 0, "errors": 0})
                w["calls"] += 1
                w["errors"] += err["total"]
        marks[f"{wsname}\u0000{sid}"] = max(marks.get(f"{wsname}\u0000{sid}", 0), last_seq)
    store["meta"] = {**(store.get("meta") or {}), "replayMark": marks}
    store["updated"] = int(time.time() * 1000)


def print_report(store):
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


def main():
    if zstandard is None:
        print("backfill 需要 zstandard 回放会话日志（pip install zstandard）", file=sys.stderr)
        return 1
    args = sys.argv[1:]
    mode = "init"
    do_report = "--report" in args
    if "--rebuild" in args:
        mode = "rebuild"
    if mode == "init" and STORE.exists():
        print(f"ledger exists ({STORE}); use --rebuild to force rebuild")
        return
    store = {"schemaVersion": 1, "updated": 0, "plugins": {}, "tools": {}, "meta": {}}
    build(store)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_name(STORE.name + ".tmp-bf")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STORE)
    total_calls = sum(t["calls"] for t in store["tools"].values())
    print(f"backfilled {len(store['tools'])} tools / {total_calls} calls -> {STORE}")
    if do_report:
        print_report(store)


if __name__ == "__main__":
    main()
