// evidence-ledger — 工具调用累计账本（轨迹复盘证据层插件）
//
// 监听宿主 post-commit 会话事件流（session/event），把每个工具的调用次数、
// 错误分类、工作区维度累计进 ~/.dsh/storages/tool-telemetry.json。
// 账本跨会话、跨工作区、不随 events 归零——它是「按插件权重反向优化插件」
// 的证据层。消费方：backfill.py（历史回放/修复）、report.py（权重查询）、
// 创造模式会话（读账本决定优化哪个插件的哪个工具）。
//
// 只依赖 node 内置模块；零 inject（不要求任何 host 服务存在）。
// 改动 index.js 后需重启 dsh web 生效（模块代码不热重载，见 rules §67）。

import { readFileSync, writeFileSync, renameSync, existsSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'

export const name = 'evidence-ledger'

const HERE = dirname(fileURLToPath(import.meta.url))

// 静态工具→插件映射（v1）。核心工具标注 family，第三方插件精确标注。
// 新工具未录入时记为 unknown，账本会保留工具名——补一行映射即可追溯历史。
function loadToolmap() {
  try {
    const raw = readFileSync(join(HERE, 'toolmap.json'), 'utf8')
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed
  } catch {}
  return {}
}

function storePath() {
  return join(process.env.DSH_HOME ?? join(homedir(), '.dsh'), 'storages', 'tool-telemetry.json')
}

function emptyStore() {
  return {
    schemaVersion: 1,
    updated: Date.now(),
    plugins: {},
    tools: {},
    meta: { liveSessions: {} },
  }
}

function loadStore() {
  try {
    const parsed = JSON.parse(readFileSync(storePath(), 'utf8'))
    if (parsed?.schemaVersion === 1 && typeof parsed.tools === 'object') return parsed
  } catch {}
  return emptyStore()
}

// 原子写：先写临时文件再改名，避免坏账本。
function atomicWrite(store) {
  const file = storePath()
  const tmp = `${file}.tmp-${process.pid}`
  try {
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(tmp, JSON.stringify(store, null, 1), 'utf8')
    renameSync(tmp, file)
  } catch (error) {
    // 尽力而为：账本写失败不打扰会话本身。
  }
}

// 错误分类：flagged=工具侧 isError；exit=非零退出码；sandbox=沙箱拒绝；
// exception=常见异常痕迹。一个结果可同时命中多类，total 只计一次。
function classify(text, isError) {
  const errors = { total: 0, flagged: 0, exit: 0, sandbox: 0, exception: 0 }
  let matched = false
  if (isError) {
    errors.flagged += 1
    matched = true
  }
  if (typeof text === 'string') {
    if (/\[exit code: [1-9]/.test(text)) {
      errors.exit += 1
      matched = true
    }
    if (text.includes('[sandbox:')) {
      errors.sandbox += 1
      matched = true
    }
    if (/Traceback \(most recent call last\)|TypeError:|ReferenceError:|SyntaxError:|EINVAL|EPERM/.test(text)) {
      errors.exception += 1
      matched = true
    }
  }
  errors.total = matched ? 1 : 0
  return errors
}

// 从 tool/result 事件里取出文本与 isError（结构与会话日志里的记录一致）。
function extractResult(event) {
  const content = event?.data?.message?.content
  if (!Array.isArray(content)) return { text: '', isError: false }
  let text = ''
  let isError = false
  for (const block of content) {
    if (!block || typeof block !== 'object') continue
    if (block.isError === true) isError = true
    for (const inner of block.content ?? []) {
      if (inner && inner.type === 'text' && typeof inner.text === 'string') text += inner.text
    }
  }
  return { text, isError }
}

function ensureTool(store, toolName, time) {
  let tool = store.tools[toolName]
  if (!tool) {
    tool = {
      calls: 0,
      errors: { total: 0, flagged: 0, exit: 0, sandbox: 0, exception: 0 },
      workspaces: {},
      firstSeen: time,
      lastSeen: time,
    }
    store.tools[toolName] = tool
  }
  return tool
}

export function apply(ctx) {
  let store = loadStore()
  // 启动时把静态映射合并进账本（映射快照随账本一起被消费）。
  store.plugins = { ...store.plugins, ...loadToolmap() }
  const pending = new Map() // `${sessionId}\u0000${callId}` -> tool name
  // 活 Session 对象不暴露 cwd（验证 .cwd/.meta.cwd 均 null）——
  // 每个会话日志的首个 `session` 事件顶层带 cwd，这里按会话缓存（与 backfill 口径一致）。
  const sessionCwd = new Map() // sessionId -> cwd
  let timer = null

  const flush = () => {
    if (!store.dirty) return
    store.dirty = false
    store.updated = Date.now()
    atomicWrite(store)
  }
  const scheduleFlush = () => {
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(flush, 2000)
  }

  ctx.on('session/event', (session, event) => {
    const type = event?.type
    const data = event?.data
    if (type === 'session') {
      // 会话首个事件：记录 cwd 供工作区维度使用
      if (session?.id && typeof event?.cwd === 'string') sessionCwd.set(session.id, event.cwd)
      if (sessionCwd.size > 5000) sessionCwd.clear() // 安全阀
      return
    }
    if (type === 'tool/call') {
      const key = `${session?.id ?? '?'}\u0000${data?.callId ?? ''}`
      if (typeof data?.name === 'string' && data.name !== '') pending.set(key, data.name)
      if (pending.size > 500) pending.clear() // 安全阀：无结果的孤儿调用不无限囤积
      return
    }
    if (type !== 'tool/result') return
    const cid = data?.message?.source?.callId
    const key = `${session?.id ?? '?'}\u0000${cid ?? ''}`
    const toolName = pending.get(key)
    pending.delete(key)
    if (!toolName) return

    const { text, isError } = extractResult(event)
    const tool = ensureTool(store, toolName, event.time)
    tool.calls += 1
    if (event.time) tool.lastSeen = event.time

    const err = classify(text, isError)
    tool.errors.total += err.total
    tool.errors.flagged += err.flagged
    tool.errors.exit += err.exit
    tool.errors.sandbox += err.sandbox
    tool.errors.exception += err.exception

    const ws = sessionCwd.get(session?.id) ?? 'unknown'
    let wsEntry = tool.workspaces[ws]
    if (!wsEntry) wsEntry = tool.workspaces[ws] = { calls: 0, errors: 0 }
    wsEntry.calls += 1
    wsEntry.errors += err.total

    store.dirty = true
    scheduleFlush()
  })

  ctx.on('dispose', () => {
    if (timer !== null) clearTimeout(timer)
    flush()
  })

  // 收尾门禁（不靠记性）：会话结束时机械跑普通 --closing，只检查已有 raw 缺口，
  // 不要求纯聊天/未授权会话制造 raw；有缺口时写提醒供下一会话开场浮出。
  // check.py 的路径通过 MEMORY_GUARD_CHECK 指定；未设置时回退到 ${DSH_HOME}/vault-guard/check.py。
  ctx.on('session/disposed', () => {
    try {
      const checkPy = process.env.MEMORY_GUARD_CHECK
        ?? join(process.env.DSH_HOME ?? join(homedir(), '.dsh'), 'vault-guard', 'check.py')
      const child = spawn(
        'python',
        [checkPy, '--closing', '--remind'],
        { detached: true, stdio: 'ignore', windowsHide: true },
      )
      child.unref()
    } catch {
      // 门禁失败静默：检查是提醒机制，不打断会话生命周期
    }
  })
}
