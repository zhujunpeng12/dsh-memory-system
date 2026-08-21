# Changelog

本项目使用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式,版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- DSH 插件形态(`dsh-memory-system`):6 个 Agent 工具(memory_bootstrap / memory_recall / memory_gate / memory_govern / memory_trajectory_review / memory_write)
- `templates/` 脱敏记忆库骨架:10 分钟搭建自己的记忆系统
- 英文 README(README.en.md)
- CONTRIBUTING.md / SECURITY.md / CHANGELOG.md
- GitHub Actions CI:Python 测试 + JS 语法 + gitleaks 密钥扫描
- DSH manifest/源码/发布包独立安装一致性回归，阻断 scoped 包名、服务注入、自依赖、打包遗漏和宿主包静态导入漂移
- 首屏 30 秒安装、兼容矩阵、选型边界和安装故障排查

### Fixed
- GitHub/pnpm 安装后 loader 仍按裸名 `dsh-memory-system` 查包，导致 `ERR_MODULE_NOT_FOUND`
- 插件调用 `ctx.agents.roots()` 却未声明 `agents` 注入，导致启动时 `ctx.agents` 为 `undefined`
- `package.json` 意外依赖自身已发布的 0.1.0，导致 GitHub master 安装混入陈旧包
- 独立 profile 加载时无法从第三方插件目录解析 `@deepseek-ai/dsh-tools` / `@deepseek-ai/dsh-llm`；改用宿主注入服务与原生 ToolDefinition，保持发布包零 npm 运行依赖
- npm 包与 README 改用压缩横幅，保留仓库高分辨率源图，同时减少约 3 MB 发布包体积

## [0.1.0] - 2026-08-15

### Added
- 初始发布:本地优先的 DSH 持久记忆基础设施
- 六层机制:热记忆注入 / 工作路径 / 冷层召回 / 授权写入 / 慢治理 / 轨迹复盘
- 核心脚本(vault-guard/):bootstrap、recall、check、govern、vault-write、vault_tx、vault_lock、rule-cite、sync-core、trajectory-review、hook-first-prompt、hook-session-start
- 写入安全:租约锁(30s/5s 心跳/陈旧恢复)+ 多文件事务(before-image/SHA-256 前置/manifest/receipt)
- 中文召回:exact + 中文 bigram BM25 + 元数据重排,字节预算 14KB/4.2KB
- 44 项回归测试(unittest,零外部依赖)
- MIT 许可证

[Unreleased]: https://github.com/zhujunpeng12/dsh-memory-system/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/zhujunpeng12/dsh-memory-system/releases/tag/v0.1.0
