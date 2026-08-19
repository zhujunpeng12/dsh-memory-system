# 记忆库模板（Memory Vault Template）

用这个骨架搭出自己的记忆库。**基础模式下无需手动复制**——`bootstrap.py` 首次运行会自动创建最小骨架（`memory/{events,index}` + `projects/` + 空 `user_profile.md`/`rules.md`）于默认位置 `~/.dsh-memory/`。

本模板用于两种场景：
1. **Vault 模式**：把 `vault/` 复制到你的 Obsidian Vault（设置 `MEMORY_VAULT` 指向它）；
2. **基础模式个性化**：复制到 `~/.dsh-memory/` 替换自动生成的占位文件，再按注释填写。

## 结构

```
vault/
├── memory/
│   ├── user_profile.example.md   # → 重命名为 user_profile.md
│   ├── rules.example.md          # → 重命名为 rules.md（rules-core.md 由 sync-core.py 生成）
│   ├── events/                   # 事件日志：YYYY-MM-DD.md（或 .raw.md 原始轨迹）
│   └── index/                    # 月度索引：YYYY-MM.md
└── projects/                     # 项目笔记：<项目名>/<项目名>.md
```

## 快速开始（Vault 模式）

```powershell
# 1. 复制骨架到目标位置
Copy-Item -Recurse vault\* "C:\Users\you\Documents\Obsidian Vault\"

# 2. 重命名示例文件
Rename-Item "...\memory\user_profile.example.md" "user_profile.md"
Rename-Item "...\memory\rules.example.md" "rules.md"

# 3. 设置环境变量（或写进你的 shell 配置）
$env:MEMORY_VAULT = "C:\Users\you\Documents\Obsidian Vault"

# 4. 生成热记忆包
python vault-guard\bootstrap.py --cwd "C:\path\to\project"
```

## 最小要求

- `memory/user_profile.md` — 必须存在（bootstrap 会读取）
- `memory/rules.md` + `memory/rules-core.md` — 规则（core 可由 `sync-core.py` 生成）
- `memory/events/` — 事件目录（可以为空）
- `projects/` — 项目目录（可以为空，bootstrap 按 cwd 祖先匹配）

缺文件时 bootstrap/recall 会输出「读取失败/缺失」提示，不会崩溃——先跑起来再慢慢补。
