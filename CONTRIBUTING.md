# Contributing to dsh-vault-memory

感谢你考虑为 dsh-vault-memory 贡献。这个项目把「记忆系统的工程可靠性」当作第一公民,请先读 README 理解六层机制与核心边界,再动手。

## 核心边界(不可破坏)

1. **写入安全优先** — 任何写路径必须走 `VaultTransaction`(租约锁 + before-image + SHA-256 前置 + receipt)。raw 只追加;纠错必须 `supersedes`,绝不原地改历史 raw。
2. **治理默认只读** — `govern.py` 只收集证据,永不自动晋升/归档/删除。
3. **零依赖** — Python 标准库 + Markdown。不引入数据库、向量服务、LLM API。
4. **字节预算** — 热包 14KB / 冷包 4.2KB 是硬预算,新增注入内容必须先过预算。
5. **无个人数据** — 仓库只含机制。任何提交不得包含真实路径、用户名、密钥、机构名、邮箱。

## 开发流程

```bash
# 1. 安装依赖(无第三方运行时依赖;测试用标准库 unittest)
# 2. 改代码
# 3. 跑测试
cd vault-guard
python -m unittest discover -p "test_*.py" -v

# 4. 语法检查(JS + Python)
cd ..
npm run check

# 5. 打包检查(确认 files 白名单无泄漏)
npm pack --dry-run
```

## 提交规范

- 提交信息用英文,前缀说明意图:`fix:` / `feat:` / `docs:` / `test:` / `chore:`
- 每个提交自包含:改了什么、为什么、如何验证
- 新增 Python 文件必须带 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 守卫(Windows 编码铁律)
- 新增测试必须用临时目录(`tempfile.TemporaryDirectory`),不得指向真实 Vault

## 脱敏检查清单(提交前)

- [ ] 无真实用户名/机构名/邮箱/密钥
- [ ] 无绝对路径(测试用 `/home/user/` 或 `C:\Users\someone\` 占位)
- [ ] 无 `__pycache__` / `.pyc` / 本地产物
- [ ] `npm pack --dry-run` 清单不含意外文件

## 拉请求流程

1. fork + 分支(命名:`fix/xxx`、`feat/xxx`)
2. 小步提交,每个提交可独立回滚
3. PR 描述:动机、改动、验证证据(测试输出)
4. 维护者会在 7 天内 review
