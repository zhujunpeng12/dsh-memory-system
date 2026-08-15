"""vault-guard check — 记忆系统机械门禁（开场跑，指令在 AGENTS.md 开场节自动注入）。

检查项（全部机械、零主观）：
  ① 收尾缺口：近 3 天 raw 未提炼（raw 比 events 新，或 events 不存在）
  ② 体量超线：rules.md >250 行/32KB；projects/*.md >300 行/30KB（toolmap 除外）
  ③ rules-core.md 同步：rules.md 比 core 新 → 自动重生成（不靠记性）
  ④ 写锁状态：.vault.lock 是否被别的会话持有（>10 分钟陈旧锁可强夺）

用法：
  python check.py            # 开场检查
  python check.py --closing  # 普通收尾：只检查已经存在的 raw 是否漏提炼
  python check.py --closing --expect-write
                             # 授权写入收尾：另要求今日 raw 存在且已提炼
退出码：0 = 全过；1 = 有缺口（供未来 hooks 使用）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from vault_lock import describe_status, read_lock, reclaim_reason
from vault_tx import TERMINAL_STATES

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VAULT = Path(os.environ.get("MEMORY_VAULT") or (Path.home() / "Documents" / "Obsidian Vault"))
MEM = VAULT / "memory"
EV = MEM / "events"
RULES = MEM / "rules.md"
CORE = MEM / "rules-core.md"
LOCK = MEM / ".vault.lock"
TX_ROOT = MEM / ".vault-transactions"
PROJ = VAULT / "projects"
DSH_HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
GUARD = Path(__file__).resolve().parent
REMINDER = DSH_HOME / "storages" / "vault-gate-reminder.md"


def size_lines(p):
    try:
        text = p.read_text(encoding="utf-8")
        return len(text.splitlines()), len(text.encode("utf-8"))
    except Exception:
        return 0, 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    closing = "--closing" in args or "--expect-write" in args
    expect_write = "--expect-write" in args
    remind = "--remind" in args
    issues, oks = [], []

    # 开场时浮出上次会话结束门禁留下的提醒（evidence-ledger session/disposed 监听器写入）
    if not closing and REMINDER.exists():
        try:
            lines = REMINDER.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                issues.append(f"上次会话收尾门禁提醒（{len(lines)} 条，见 {REMINDER}）：")
                for line in lines[:8]:
                    issues.append(f"    {line}")
        except Exception:
            pass

    # ① 收尾缺口：近 3 天 raw 是否被 events 提炼
    raws = sorted(EV.glob("*.raw.md"), key=lambda p: p.stat().st_mtime)
    for raw in raws[-3:]:
        day = raw.name.split(".")[0]
        ev = EV / f"{day}.md"
        if not ev.exists():
            issues.append(f"收尾缺口：{raw.name} 存在但 {ev.name} 不存在（L0 未提炼）")
        elif raw.stat().st_mtime > ev.stat().st_mtime + 300:
            issues.append(f"收尾缺口：{raw.name} 比 {ev.name} 新超过 5 分钟（raw 有新段未提炼）")

    # ② 体量体检
    lines, size = size_lines(RULES)
    if lines > 250 or size > 32768:
        issues.append(f"rules.md 超线：{lines} 行 / {size // 1024}KB（线 250 行/32KB）")
    else:
        oks.append(f"rules.md {lines} 行 / {size // 1024}KB 在线内")
    for p in PROJ.rglob("*.md"):
        if "toolmap" in p.name.casefold():
            continue
        lines, size = size_lines(p)
        if lines > 300 or size > 30720:
            issues.append(f"项目笔记超线：{p.name} {lines} 行 / {size // 1024}KB（线 300 行/30KB）")

    # ③ rules-core 同步（机械自愈）
    if not CORE.exists() or RULES.stat().st_mtime > CORE.stat().st_mtime + 1:
        r = subprocess.run(
            [sys.executable, str(GUARD / "sync-core.py")],
            capture_output=True, text=True, encoding="utf-8",
        )
        if r.returncode == 0:
            oks.append(f"rules-core.md 已自动同步：{r.stdout.strip()}")
        else:
            issues.append(f"rules-core.md 同步失败：{r.stderr.strip()[:120]}")

    # ④ 租约锁与未完成事务状态（区分健康活跃锁与陈旧/损坏锁，绝不自动强夺活跃锁）
    lock_status = describe_status(LOCK)
    if lock_status["state"] == "locked":
        lease = lock_status.get("lease_remaining_seconds")
        lease_text = "未知" if lease is None else f"{int(lease)}s"
        reason = reclaim_reason(LOCK, read_lock(LOCK))
        if reason is None:
            issues.append(
                "Vault 写锁被健康持有（勿强夺，等待释放）："
                f"{lock_status.get('holder')} / txn={lock_status.get('transaction_id')} / "
                f"lease={lease_text} / pid_alive={lock_status.get('owner_alive')}"
            )
        else:
            issues.append(
                f"Vault 陈旧/失效锁（{reason}）：holder={lock_status.get('holder')} / lease={lease_text}；"
                "确认无活跃写入后可运行 vault-lock.py acquire --force 强夺"
            )
    elif lock_status["state"] == "corrupt":
        issues.append(f"Vault 锁文件损坏：{LOCK}（需先运行 vault-write.py recover --apply）")
    else:
        oks.append("Vault 写锁 free")

    pending = []
    if TX_ROOT.exists():
        for manifest in TX_ROOT.glob("*/manifest.json"):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("state") not in TERMINAL_STATES:
                pending.append(f"{data.get('transaction_id')}:{data.get('state')}")
    if pending:
        issues.append(f"未完成 Vault 事务 {len(pending)} 个：{', '.join(pending[:3])}；运行 vault-write.py recover --apply")
    else:
        oks.append("无未完成 Vault 事务")

    # 只有已获写入授权的事务才要求“今日必须有 raw”。普通 session/disposed
    # 只检查上方“已有 raw 是否漏提炼”，不能逼纯聊天/临时查询制造 L0。
    if expect_write:
        import datetime
        today = datetime.date.today().isoformat()
        raw = EV / f"{today}.raw.md"
        ev = EV / f"{today}.md"
        if not raw.exists():
            issues.append(f"今日无 raw：{raw.name} 不存在（本次会话产出未落 L0）")
        elif not ev.exists() or raw.stat().st_mtime > ev.stat().st_mtime + 300:
            issues.append(f"今日 raw 未提炼进 events：{ev.name} 缺失或旧于 raw 超 5 分钟")

    # 提醒文件：--remind 时写入缺口（供下一会话开场浮出）/ 无缺口时清除
    if remind:
        try:
            REMINDER.parent.mkdir(parents=True, exist_ok=True)
            if issues:
                REMINDER.write_text(
                    "\n".join(f"- {line}" for line in issues) + "\n",
                    encoding="utf-8",
                )
            else:
                REMINDER.unlink(missing_ok=True)
        except Exception:
            pass

    mode = "授权写入收尾" if expect_write else ("普通收尾" if closing else "开场")
    print(f"== vault-guard {mode}检查 ==")
    for line in oks:
        print("✅", line)
    for line in issues:
        print("⚠️", line)
    if not issues:
        print("✅ 全部通过")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
