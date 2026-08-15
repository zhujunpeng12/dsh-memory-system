# vault-guard — DSH 记忆启动与机械门禁

日常启动只走一个入口：`bootstrap.py` 在固定字节预算内合并门禁、指令预算、画像、活跃规则、当前项目摘要和最近 3 个有效事件日主标题。详细记忆制度不再塞进全局 AGENTS。

| 脚本 | 职责 |
|---|---|
| `bootstrap.py` | 生成默认 ≤14KB 的 `[vault-bootstrap]` 热记忆包 |
| `hook-first-prompt.py` | 热包按 session 注入一次；召回信号与具体主题同时成立才追加冷包 |
| `recall.py` | exact + 中文 BM25 + 项目/日期/来源重排，输出默认 ≤4.2KB 冷包 |
| `hook-session-start.py` | SessionStart 兼容桥；因异步时序当前不启用 |
| `check.py` | 检查 raw→events 缺口、体量、锁状态并按需同步核心规则 |
| `sync-core.py` | `rules.md` → `rules-core.md`（⭐ 或 `⏱×N≥2`） |
| `vault_lock.py` / `vault-lock.py` | 30 秒租约、5 秒心跳的单写者锁库与兼容 CLI |
| `vault_tx.py` / `vault-write.py` | 可恢复事务、哈希前置条件、raw 只追加与写入回执；默认 dry-run |
| `trajectory-review.py` | 只读扫描 session 强纠正信号与工具账本，输出复盘候选 |
| `rule-cite.py` | 按精确规则编号预览/增加 `⏱×N`，写锁保护并同步核心层 |
| `govern.py` | 只读扫描锁/事务、提炼、重复、冲突、过期、体量与规则生命周期候选 |

## 日常命令

```powershell
python "$HOME\.dsh\vault-guard\bootstrap.py" --cwd "<当前目录>" --max-bytes 14000
python "$HOME\.dsh\vault-guard\recall.py" --query "继续上次的决定" --cwd "<当前目录>" --force
python "$HOME\.dsh\vault-guard\check.py"
python "$HOME\.dsh\vault-guard\vault-write.py" recover
python "$HOME\.dsh\vault-guard\govern.py"
python "$HOME\.dsh\vault-guard\govern.py" --json --max-items 100
python "$HOME\.dsh\vault-guard\check.py" --closing
python "$HOME\.dsh\vault-guard\check.py" --closing --expect-write
python "$HOME\.dsh\vault-guard\sync-core.py"
python "$HOME\.dsh\vault-guard\trajectory-review.py" --cwd "<当前目录>"
python "$HOME\.dsh\vault-guard\trajectory-review.py" --all-workspaces
python "$HOME\.dsh\vault-guard\rule-cite.py" 12b
python "$HOME\.dsh\vault-guard\rule-cite.py" 12b --apply
```

`vault-write.py raw/replace/recover` 和 `rule-cite.py` 都是默认预览，只有明确授权后才加 `--apply`。raw 必须走 `vault-write.py raw`；correction/tombstone 必须带 `--supersedes <entry_id>`，不得直接改历史 raw。共享非 raw 文件替换时传 `--expected-sha256`，防止覆盖别的会话新内容。

普通 `--closing` 是后台 session 销毁时的无写入假设检查：只报告已经存在但未提炼的 raw。只有本次会话有实质产出、用户同意归档并完成写入事务时，才运行 `--closing --expect-write`，额外要求当日 raw 存在。

`trajectory-review.py` 默认只看当前工作区、近 14 天和最多 20 个 session，输出用户强纠正与重复工具错误候选；它不会写 Vault。`rule-cite.py` 默认也只预览，确认本次确实引用或执行了该规则后才加 `--apply`。

`bootstrap.py` 即使遇到门禁非零也会输出可用热包，并明确标出真实 exit code。规则正文按 ⭐、引用计数和预算选择；events 在 14 天内回溯最近 3 个有效日期，只注入 `##` 主标题且单条不超过 180 UTF-8 字节，每个日期另有 480 字节标题预算；项目通过 cwd 的祖先目录名匹配 Vault 项目目录。任何省略都保留完整路径。

指令预算审计遵循 DSH 默认发现顺序：全局 `~/.dsh/AGENTS.md` + 项目根到 cwd 的 `AGENTS.md`、`CLAUDE.md` 及 local overlay，并对同目录同内容文件去重。8KB 单文件、32KB 项目聚合、48KB 总源链是软提醒；65,536 字节是渲染硬上限，热包不从该数值直接扣除。

## 配置与边界

- 开场链路：`dsh-hooks-claude-code` → 同步 UserPromptSubmit → `hook-first-prompt.py` → `bootstrap.py` → JSON `hookSpecificOutput.additionalContext`。
- hooks 配置：`~/.dsh/hooks-dsh.json`；profile 装配：`~/.dsh/profiles/web/cordis.patch.yml`。
- 热包去重优先读取 DSH 自己的 `transcript_path`；当前 Python 无 zstandard 时使用 `~/.dsh/storages/vault-bootstrap-sessions/` 的 14 天有界 marker 兜底。`[vault-bootstrap: absent]` 不算成功，下一轮仍会重试。冷包按每轮提示独立判断，不写长期状态。
- 结束链路的 `session/disposed` 仅运行普通 closing，检查已有 raw 缺口；是否归档仍需用户确认，不能把普通“好的/ok”当写盘授权。
- 门禁只报告事实，不替 Agent 判断要写什么或删什么。

## 单一真相源

- 记忆治理、毕业、仲裁、体量、过期与并发写入：`~/.dsh/docs/MEMORY_SYSTEM.md`
- 预设、Skill、搜索、编码、review 与长任务：`~/.dsh/docs/AGENT_OPERATIONS.md`
- 每次会话必须遵守的最小规则：`~/.dsh/AGENTS.md`
- 优化前完整快照：`~/.dsh/archive/memory-optimization-20260814-231700/`
