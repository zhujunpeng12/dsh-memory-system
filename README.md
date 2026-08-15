# dsh-memory-system — DSH 持久记忆基础设施

> 一套给 DeepSeek Harness (DSH) Agent 用的本地优先记忆系统：启动热记忆注入、可解释冷召回、租约锁保护的事务写入、只读治理与轨迹复盘。纯 Python + Markdown 文件，无数据库、无向量服务、无外部依赖。

**重要边界：本仓库只包含「机制」，不包含任何个人数据。** 记忆内容（画像、规则、事件、项目笔记）始终留在使用者自己的 Obsidian Vault 里，通过环境变量指向。

## 为什么需要它

Agent 会话之间默认是失忆的。本系统用 **六层机制** 把「记忆」变成可工程化的闭环：

1. **启动热记忆** — 每个新会话注入一次 ≤14KB 的热包（门禁 + 指令预算 + 用户画像 + 活跃规则 + 项目摘要 + 近期事件标题）
2. **工作路径** — 按全局/项目 `AGENTS.md` 规则完成任务
3. **冷层召回** — 双门槛触发（历史引用 + 具体主题），exact + 中文 BM25 + 元数据重排，输出 ≤4.2KB 冷包，向量检索默认关闭（依赖零）
4. **授权写入** — 租约锁（30s 租约 / 5s 心跳 / 陈旧锁恢复）+ 多文件事务（before-image / SHA-256 前置 / manifest / receipt），raw 机械只追加，纠错必须 supersedes
5. **慢治理** — `govern.py` 只读扫描重复、冲突、过期、体量、规则生命周期候选，默认不写
6. **轨迹复盘** — 只读扫描会话轨迹，用「用户纠正」作为硬信号产出复盘候选

## 特性亮点

| 能力 | 说明 |
|---|---|
| 写入安全 | 单写者租约锁 + 可恢复事务，多文件变更原子化，raw 只追加不覆写 |
| 中文召回 | 中文 bigram BM25 + exact/标题/路径匹配 + 元数据重排，全程可解释 trace |
| 字节预算 | 热包 14KB / 冷包 4.2KB 硬预算，UTF-8 安全截断 |
| 治理只读 | L0-L3 边界，自动收集证据、永不自动删改 |
| 零依赖 | 标准库 + Markdown 文件，Windows/macOS/Linux 可跑 |

## 快速开始

### 1. 准备目录结构

在任意位置建一个记忆库（默认约定 `~/Documents/Obsidian Vault`，可用环境变量覆盖）：

```
Obsidian Vault/
├── memory/
│   ├── user_profile.md      # 用户画像
│   ├── rules.md             # 完整规则
│   ├── rules-core.md        # 活跃核心规则（可由 sync-core.py 生成）
│   ├── events/              # 事件日志（YYYY-MM-DD.md）
│   ├── index/               # 月度索引
│   └── projects/            # 项目笔记
└── projects/                # 项目目录（cwd 祖先匹配）
```

### 2. 配置环境变量

复制 `.env.example` 并按需设置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_VAULT` | `~/Documents/Obsidian Vault` | 记忆库根目录（含 `memory/` 与 `projects/`） |
| `DSH_HOME` | `~/.dsh` | DSH 家目录（hooks 配置、storages 等） |

Windows PowerShell 示例：

```powershell
$env:MEMORY_VAULT = "C:\Users\you\Documents\Obsidian Vault"
$env:DSH_HOME = "C:\Users\you\.dsh"
```

### 3. 生成热记忆包

```powershell
python vault-guard\bootstrap.py --cwd "C:\path\to\project" --max-bytes 14000
```

### 4. 冷召回

```powershell
python vault-guard\recall.py --query "继续上次的XXX" --cwd "C:\path\to\project" --force
```

### 5. 接入 DSH hooks（可选）

参考 `hooks.example.json`，把 `${DSH_HOME}` 替换为你的实际路径，写入 DSH 的 hooks 配置（如 `~/.dsh/hooks-dsh.json`）：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "python \"${DSH_HOME}/vault-guard/hook-first-prompt.py\"" }
        ]
      }
    ]
  }
}
```

每个新会话首个提示会自动注入热记忆包；命中召回信号（历史引用 + 具体主题）时追加冷包。

## 目录结构

```
├── vault-guard/
│   ├── bootstrap.py            # 热记忆包生成（≤14KB）
│   ├── hook-first-prompt.py    # UserPromptSubmit hooks：按 session 注入一次热包 + 冷包触发
│   ├── hook-session-start.py   # SessionStart 兼容桥（当前默认不启用）
│   ├── recall.py               # 冷召回：exact + 中文 BM25 + 元数据重排
│   ├── recall_trigger.py       # 召回信号双门槛判断
│   ├── check.py                # 机械门禁（开场/收尾检查）
│   ├── sync-core.py            # rules.md → rules-core.md 事务同步
│   ├── vault_lock.py           # 30s 租约 / 5s 心跳单写者锁
│   ├── vault-lock.py           # 锁的兼容 CLI（acquire/release/status）
│   ├── vault_tx.py             # 可恢复事务：before-image/哈希前置/manifest/receipt
│   ├── vault-write.py          # 授权写入 CLI（raw 只追加，dry-run 默认）
│   ├── rule-cite.py            # 规则引用计数（dry-run 默认）
│   ├── govern.py               # 只读治理扫描（重复/冲突/过期/体量/生命周期）
│   ├── trajectory-review.py    # 轨迹复盘候选（用户纠正 = 硬信号）
│   └── test_*.py               # 回归测试（unittest，无外部依赖）
├── hooks.example.json          # DSH hooks 装配示例
├── .env.example                # 环境变量示例
└── LICENSE
```

## 运行测试

```powershell
cd vault-guard
python -m unittest discover -p "test_*.py" -v
```

测试全部使用临时目录，不触碰真实记忆库。

## 使用约定（方法论）

- **单一真相源**：一条事实只有一个家（运行参数 → AGENTS.md；决策历史 → 项目笔记；当日流水 → events；跨项目经验 → rules），其他位置只放指针
- **写入授权**：所有写操作先 dry-run 预览，明确授权后才 `--apply`；raw 只追加，纠错用 supersedes 而非覆写
- **治理默认只读**：`govern.py` 只收集证据和建议，晋升/归档/删除永远需要人确认

## 许可证

MIT — 详见 [LICENSE](LICENSE)。

## 免责声明

- 本仓库不含任何个人数据；记忆内容始终留在使用者本地
- 使用前请自行审查：部署前确认你的记忆库结构、hooks 装配与安全边界
