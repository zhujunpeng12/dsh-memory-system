# agent-teams — 多 agent 团队编排（增强版）

给 DeepSeek Harness 的多 agent 团队协作插件：队长（captain）通过自然语言驱动多名常驻成员（durable subagents）完成目标——需求确认、任务拆分、依赖编排、消息协作、活动面板可视化。

本目录是 [NanmiCoder/dsh-agent-teams](https://github.com/NanmiCoder/dsh-agent-teams)（MIT）的**增强 fork**，作为 dsh-memory-system 的配套插件随仓库分发。它是「轨迹复盘 → 多 agent 派发解决」闭环的执行件：复盘发现优化点后，由本插件把修复任务派发给团队成员，解决完成后回写 events（见主 README 第⑥层）。

## 上游与本版

- 上游：NanmiCoder/dsh-agent-teams（MIT，感谢原作者 [NanmiCoder](https://github.com/NanmiCoder)）
- 本版：上游 v0.1.5 + 增强提交（见下方「增强点」），含未发布的最新修复

## 增强点（相对上游）

- **P0 需求确认单门禁**：`agent_teams_submit_brief` 提交需求单 → 活动面板拍板 → 派发工具（create/claim/dispatch）在 brief 确认前全部阻塞；确认/驳回即时反馈并 live 通知队长（无需用户催一句）
- **P1 编排纠错**：任务依赖可改（队长可更新 dependencies，含环检测）；队长可代成员认领任务
- **队长协议**（固化在系统提示词）：渐进式验收（交付一个验收一个）/ 依赖三问（产出/资源/认知）/ 文件所有权切分实现真并行 / 追加需求必须建任务挂状态（消息级工作会漏出状态机）
- **技能路由是队长职责**：派活前先判任务难度，非平凡或匹配技能领域时查本工作区配置的技能索引并点名技能
- **UI 状态机**：红绿波纹（忙碌/空闲）/ 橙色待命（queued）/ 相位同步（容器级单点）/ 任务依赖折叠与历史归档 / 徽章关闭团队 / 悬停防抖与键盘焦点优先

## 安装与构建

```bash
# 构建（需要 pnpm；产物在 lib/，.gitignore 已忽略）
cd plugins/agent-teams
pnpm install
pnpm build          # tsc + tsdown
pnpm typecheck      # 只检查不输出

# 装配（profile 内）
dsh plugin --profile web add ./plugins/agent-teams
```

装配行 `cordis.patch.yml` 的 `stateDir` 默认 `.agent-teams`（团队状态落在 `<工作区>/.agent-teams/<teamId>/`），`memberProvider` 默认 `spawn`。

## 注意

- 提示词中的技能索引路径按你的工作区配置调整（本仓库不携带任何私人技能资产）
- 与上游的差异以本目录源码为准；上游新版本可自行合并
- 构建产物 `lib/` 不纳入版本控制（上游 .gitignore 约定）
