# dsh-memory-system — DSH 持久记忆基础设施

> 一套给 DeepSeek Harness (DSH) Agent 用的本地优先记忆系统：启动热记忆注入、可解释冷召回、租约锁保护的事务写入、只读治理与轨迹复盘。纯 Python + Markdown 文件，无数据库、无向量服务、无外部服务依赖。

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
| 零依赖 | Python 标准库 + Markdown 文件，Windows/macOS/Linux 可跑（仅 backfill.py 历史回放需可选的 zstandard） |

## 系统架构（六层闭环）

![DSH 记忆系统流程图](docs/memory-system-flowchart.png)

<details>
<summary>展开查看可编辑的 Mermaid 源码图</summary>

```mermaid
flowchart TD
    Start([新会话 / 新任务]) --> Hook[SessionStart Hook<br/>解析 JSON 与 cwd · UTF-8 兼容]

    Hook -->|实线:脚本机械执行| B1[① 启动热记忆<br/>bootstrap.py · 有界上下文 ≤14KB]
    B1 --> B1a[机械门禁<br/>缺口 · 锁 · 同步]
    B1 --> B1b[指令预算<br/>8KB/32KB/48KB 软预警]
    B1 --> B1c[用户画像<br/>完整注入 · 不绑定项目]
    B1 --> B1d[活跃规则<br/>核心标记 + 引用次数 · 按预算筛选]
    B1 --> B1e[项目摘要<br/>cwd 祖先匹配 Vault]
    B1 --> B1f[最近事件日<br/>14 天回溯 · 只取主标题 · 单条 ≤180B]
    B1f -->|合并一次注入| B1out["[vault-bootstrap] 热包"]

    B1out --> B2[② 工作路径<br/>AGENTS 内核 · Skill 路由 · 最小修改]
    B2 --> B2out[验证后交付<br/>语法/配置/真实运行/界面证据]

    B2 -.需要细节时按需读取.-> B3[③ 冷层按需读取<br/>完整 rules · 历史 events/raw · 项目笔记 · 月度索引 · session 日志]

    B3 --> Q1{有持久价值且<br/>用户同意归档?}
    Q1 -->|否| Q1no[普通收尾 · 不自动写 Vault<br/>session/disposed → check --closing]
    Q1 -->|是| B4[④ 授权写入事务<br/>拿锁 vault-lock · 写 raw 只记事实<br/>提炼归位 events/项目/rules · 释放锁]
    B4 --> B4out[授权门禁<br/>check --closing --expect-write]

    B4out --> B5[⑤ 慢维护<br/>机械体检 raw 缺口/体量/core 同步<br/>人工治理 毕业/仲裁/过期/删除确认]

    B2 -.会话轨迹.-> B6[⑥ 轨迹复盘反馈闭环<br/>trajectory-review.py 只读候选]
    B6 --> B6a[证据层<br/>用户纠正=硬信号 · session 日志 · 工具账本只作线索]
    B6a --> B6b{人工核验<br/>场景→错误→根因→先决动作}
    B6b -->|重复 ≥3 次| B6c[规则回灌<br/>毕业进 rules-core]
    B6b -->|普通探索失败| B6d[不沉淀]
    B6b -->|授权沉淀| B6e[raw → events<br/>保留结论与证据指针]
    B6c --> B1
    B6e --> B1
```

图例:实线 = 脚本机械执行;虚线 = 按需读取 / 人工核验;菱形 = 用户授权判断。反馈闭环:轨迹复盘产出的规则与事件,回灌到下一次会话的热记忆。

</details>

## 六层职责详解（每一层做什么）

### ① 启动热记忆 — 每个会话一次的有界上下文

**做什么**：新会话开始时，把「此刻最该知道的记忆」压缩成一个 ≤14KB 热包一次注入，让 Agent 不读全库也能带着上下文开工。

**包含**：机械门禁状态（缺口/锁/同步）、指令预算审计、用户画像、活跃核心规则（按 ⭐ 与引用计数筛选）、当前项目摘要（按 cwd 祖先匹配）、最近事件主标题（14 天回溯、单条 ≤180B）。

**怎么用**：`memory_bootstrap` 工具，或 `python vault-guard/bootstrap.py --cwd <项目目录> --max-bytes 14000`。接入 hooks 后每次新会话自动执行。

### ② 工作路径 — 按规则执行任务（方法论层，无脚本）

**做什么**：这是「Agent 怎么干活」的约定，不是脚本——热包注入后，Agent 按优先级与权限边界执行：系统/用户指令 > 项目 AGENTS.md > 全局规则 > 冷文档；技能路由（点名 → 速查表 → 图谱兜底）；最小修改、根因调查；交付前验证（语法/配置/真实运行/界面证据）。

**为什么没有脚本**：这一层是行为规范，由 `AGENTS.md` 承载。开源版提供 `templates/` 里的示例规则作为起点，使用者按自己的团队文化改写。

### ③ 冷层按需读取 — 需要细节才打开

**做什么**：热包只有摘要，当任务需要证据或细节时，冷召回按需打开完整来源（完整规则、历史 events/raw、项目主笔记、月度索引、session 日志）。

**触发**：双门槛——同时满足「历史/上次/纠正等召回信号」+「可检索的具体主题」才触发；纯确认语、复测元指令不打开。

**怎么用**：`memory_recall` 工具，或 `python vault-guard/recall.py --query "<问题>" --cwd <目录> --force`。exact + 中文 bigram BM25 + 元数据重排，输出 ≤4.2KB 冷包并附来源与 trace。

### ④ 授权写入 — 有持久价值且用户同意才写

**做什么**：只有「实质产出」（代码/文档变更、持久决策、用户偏好、数据口径、验收标准、下次仍需遵守的约定）且用户同意归档时，才走事务写入：拿租约锁 → 写 raw（只记事实不评价）→ 提炼归位（events/项目/rules/toolmap）→ 释放锁。

**安全机制**：30s 租约 + 5s 心跳单写者锁、before-image + SHA-256 前置 + manifest + receipt 多文件事务、raw 机械只追加（纠错必须 supersedes）、默认 dry-run、`tools/pre-execute` 强制确认。

**怎么用**：`memory_write` 工具（`op=raw/replace/recover`，`apply=true` 才落盘）。

### ⑤ 慢维护 — 机械体检 + 人工治理

**做什么**：定期（或怀疑记忆库不健康时）做两件事——机械体检：raw 缺口、体量超线、rules-core 同步、写锁状态；人工治理：规则毕业、冲突仲裁、过期归位、删除确认。

**边界**：`govern.py` 只收集证据与建议（重复/冲突/过期/体量/生命周期候选），**永不自动删改**；晋升/归档/删除永远需要人确认。

**怎么用**：`memory_govern` 工具，或 `python vault-guard/govern.py --json --max-items 100`。配合 `check.py` 的收尾门禁（`--closing` / `--closing --expect-write`）使用。

### ⑥ 轨迹复盘 — 证据驱动的质量反馈闭环

**做什么**：收尾时回溯会话轨迹，找三类点：错误（用户纠正 = 硬信号；AI 自评有自我辩护倾向）、error（仅频繁时写）、可借鉴。产出按「场景 → 错误 → 根因 → 先决动作」四字段模板的复盘候选。

**证据来源**：session 日志中的用户纠正信号 + `evidence-ledger` 插件的工具调用账本（谁的工具最常出错）。账本只作线索，不自动判错。

**闭环**：人工核验后，普通探索失败不沉淀；重复 ≥3 次的模式毕业进 rules-core；获准的沉淀走 raw → events，保留结论与证据指针——规则与事件回灌到下一次会话的热记忆，形成进化闭环。

**怎么用**：`memory_trajectory_review` 工具，或 `python vault-guard/trajectory-review.py --cwd <目录>`。配套安装 `plugins/evidence-ledger/` 才有工具账本数据。

## 作为 DSH 插件安装（推荐）

插件形态把记忆能力直接注册为 Agent 工具，并随 DSH 装配自动生效：

```bash
# 从 dsh-plugin topic 仓库安装（审核后发布时可用）
dsh plugin add dsh-vault-memory

# 或本地开发安装
npm pack            # 生成 tarball
dsh plugin add ./dsh-vault-memory-0.1.0.tgz
```

安装后重启 Harness，Agent 获得 6 个记忆工具：

| 工具 | 作用 | 写操作 |
|---|---|---|
| `memory_bootstrap` | 生成 ≤14KB 热记忆包 | 只读 |
| `memory_recall` | 冷召回（exact + 中文 BM25） | 只读 |
| `memory_gate` | 机械门禁检查 | 只读 |
| `memory_govern` | 治理候选扫描 | 只读 |
| `memory_trajectory_review` | 轨迹复盘候选（用户纠正 = 硬信号） | 只读 |
| `memory_write` | 授权事务写入（默认 dry-run） | 需确认 |

`memory_write` 默认只预览，`apply=true` 且经用户确认后才落盘；`tools/pre-execute` 钩子会强制弹确认。所有工具通过 `MEMORY_VAULT` / `DSH_HOME` 环境变量定位你的记忆库。

**轨迹复盘证据层（可选配套）**：`memory_trajectory_review` 依赖工具调用账本（`${DSH_HOME}/storages/tool-telemetry.json`）。安装配套插件 `plugins/evidence-ledger/`（工具账本，零 inject）后，每个会话的工具调用与错误会自动累计，复盘才有数据可扫。不装则复盘只扫 session 日志中的用户纠正信号。

> 手动 hooks 注入（可选）：参考 `hooks.example.json` 把 `hook-first-prompt.py` 挂到 `UserPromptSubmit`，每个新会话首个提示自动注入热包。

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

脚本现在都用 `__file__` 包内相对定位（不依赖安装位置），hooks 命令只需指向 `hook-first-prompt.py` 的**实际所在路径**。参考 `hooks.example.json`，把路径替换为插件包内脚本位置，写入 DSH 的 hooks 配置（如 `~/.dsh/hooks-dsh.json`）：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "python \"<插件包路径>/vault-guard/hook-first-prompt.py\"" }
        ]
      }
    ]
  }
}
```

> `DSH_HOME` 环境变量仍用于定位 DSH 运行时存储（storages/sessions 等），脚本自身互相调用则用包内相对路径——插件装到哪里都能跑，无需把 vault-guard 复制到 `$DSH_HOME`。

每个新会话首个提示会自动注入热记忆包；命中召回信号（历史引用 + 具体主题）时追加冷包。

## 目录结构

```
├── index.js                    # DSH host 插件：6 个记忆工具 + 写操作护栏
├── package.json                # npm 包声明（dsh-vault-memory）
├── dsh.plugin.json             # DSH 插件 manifest
├── cordis.patch.yml            # DSH bundle 装配补丁
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
├── plugins/
│   └── evidence-ledger/        # 配套插件：工具调用账本（轨迹复盘证据层）
├── templates/                  # 脱敏记忆库骨架（10 分钟搭出自己的记忆系统）
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
