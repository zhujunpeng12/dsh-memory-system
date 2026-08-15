# evidence-ledger — 工具调用累计账本（轨迹复盘证据层）

给 DeepSeek Harness 的轨迹复盘提供证据层：累计每个工具的调用次数与错误分类，跨会话、跨工作区、**永不归零**。`trajectory-review.py`（主插件 vault-guard 的一部分）消费本账本产出复盘候选。

## 位置

- 账本：`${DSH_HOME}/storages/tool-telemetry.json`（宿主级持久存储）
- 原始记录的家：`${DSH_HOME}/sessions/**/session.jsonl.zstd`（账本只存聚合，不复制原始数据）

## 账本 schema（schemaVersion 1）

```json
{
  "schemaVersion": 1,
  "updated": 1786711864246,
  "plugins": { "pwsh": "core/shell", "render_ui": "@vendor/dsh-genui" },
  "tools": {
    "pwsh": {
      "calls": 123,
      "errors": { "total": 4, "flagged": 0, "exit": 4, "sandbox": 0, "exception": 0 },
      "workspaces": { "D:\\my-project": { "calls": 10, "errors": 1 } },
      "firstSeen": 1786711864246,
      "lastSeen": 1786711886637
    }
  },
  "meta": { "replayMark": { "<sessionId>": 1891 } }
}
```

错误分类：`flagged`=工具侧 isError；`exit`=非零退出码（文本 `[exit code: N]`）；`sandbox`=沙箱拒绝；`exception`=常见异常痕迹（Traceback/TypeError/EINVAL/EPERM）。一个结果可同时命中多类，`total` 只计一次。

## 三件套

| 文件 | 职责 |
|------|------|
| `index.js` | 实时监听器：宿主装配行插件，监听 `session/event` post-commit 流，防抖 2s 原子写账本 |
| `backfill.py` | 历史回放器：`--init` 播种（账本不存在时）/ `--rebuild` 从全部日志重建（修复用）/ `--report` 顺带打印报表 |
| `report.py` | 查询器：按插件聚合权重与错误率 + 按工具明细 + 未映射工具提示 |

Python 查询/回放脚本显式使用 UTF-8 输出，Windows 控制台编码不会把中文工作区路径显示成乱码。

## toolmap.json 维护规则

- 工具→插件映射是静态表（运行时 ToolSchema 不携带包名）。第三方插件精确标注包名；核心工具按 family 标注（core/fs、core/shell 等）。
- 账本出现 `unknown` 工具时：往 toolmap.json 补一行即可追溯。
- 插件启动和 backfill 都会把该表合并进账本 `plugins` 字段。

## 装配与生效

1. 挂载：把本目录链接/复制到 DSH profile 的 node_modules（如 `%USERPROFILE%\.dsh\profiles\web\node_modules\evidence-ledger`）
2. 装配行：profile 的 `cordis.patch.yml` 追加 insert 行（id: evidence-ledger，零 inject）
3. 生效：重启 `dsh web`（模块代码不热重载）

## 注意事项（坑位）

- **--rebuild 与运行中的监听器**：监听器启动时把账本载入内存，外部 `--rebuild` 覆盖文件后，监听器后续写入基于旧快照会把重建结果覆盖回来 → 重建请安排在停服后（或重建完立刻重启 dsh web）。
- **映射口径**：`workspaces` 的 key = 会话首个 `session` 事件的顶层 `cwd`（绝对路径）。实时监听器按会话缓存该值；backfill 从日志同字段读取——两端口径一致。
- **孤儿调用**：tool/call 无对应 result（被取消的调用）不计数；pending 表 500 条安全阀。

## 消费路径

Agent 会话读账本 → 按插件权重排序（谁的工具被调最多）+ 错误率（谁最容易出错）→ 对热点插件做反向优化（描述改写/参数修正/死工具清理），优化后再用账本验证「该插件错误率是否下降」——与 trajectory-review.py 一起形成进化闭环。
