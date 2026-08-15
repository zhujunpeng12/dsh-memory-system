# Security Policy

## 安全边界(本项目的核心承诺)

dsh-memory-system 处理的是用户的**个人记忆数据**(画像、规则、事件、项目笔记)。安全原则:

1. **数据不出本地** — 记忆内容只存在于 `MEMORY_VAULT` 指向的用户目录;仓库与 npm 包不含任何个人数据
2. **写入需授权** — 所有写操作默认 dry-run,`apply=true` 且经确认后才落盘
3. **写入可恢复** — 租约锁 + before-image + 事务回执,任何失败可 recover

## 报告漏洞

请**不要**公开提交漏洞细节(尤其是可能影响其他用户记忆数据的)。通过以下方式私下报告:

- 在 GitHub 仓库创建 **Private security advisory**(Settings → Security → Advisories → New draft advisory)
- 或邮件维护者(见仓库主页联系方式)

## 响应时间

- 确认漏洞:48 小时内
- 严重性评估 + 修复计划:7 天内
- 修复发布:按严重性,高危 ≤ 14 天

## 漏洞赏金

本项目为个人维护的开源项目,暂无赏金计划。感谢你的报告,会在 CHANGELOG 与致谢中署名(如你愿意)。

## 已知安全考量

- `memory_write` 工具通过 `tools/pre-execute` 钩子强制用户确认,但仍建议审查目标路径与内容
- 环境变量 `MEMORY_VAULT` / `DSH_HOME` 指向的目录应限制为本人可写
- 不要将 `.vault.lock`、`.vault-transactions/`、`*.raw.md` 提交到任何版本库(已在 .gitignore 排除)
