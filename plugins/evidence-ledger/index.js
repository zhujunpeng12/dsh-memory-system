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

// 活 Session 的稳定标识：优先直接 .id，缺失时用不可变 SessionHeader 的 id。
// 两者同源（SessionHeader 由会话创建时填充），取其一即可保持 pending 键一致。
function sidOf(session) {
  return session?.id ?? session?.header?.id ?? '?'
}

// 活 Session 的 cwd：藏在 .header.cwd（不可变 SessionHeader）里——
// 实测 .cwd / .meta.cwd 均为 null（早期误判「活 Session 不暴露 cwd」）。
// 注意：session/event 流根本不携带会话头事件（头事件走 sessionPersistence.create(meta)，
// 与 append() 的 post-commit 流是两条路径），所以事件里的 cwd 只能作补充，不能作唯一来源。
function headerCwd(session) {
  const cwd = session?.header?.cwd
  return typeof cwd === 'string' && cwd !== '' ? cwd : undefined
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
  // 会话缓存 cwd：session/event 流不携带会话头事件，因此以活 Session 的
  // header.cwd 为主路径（见 headerCwd）；缓存只兜底事件流自身带 cwd 的场景。
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
      // 会话首个事件：记录 cwd 供工作区维度使用（事件流若携带）。
      // 事件 cwd 缺失时回退 session.header.cwd——头事件通常不经过本流。
      const sid = sidOf(session)
      const cwd = typeof event?.cwd === 'string' ? event.cwd : headerCwd(session)
      if (sid !== '?' && typeof cwd === 'string') sessionCwd.set(sid, cwd)
      if (sessionCwd.size > 5000) sessionCwd.clear() // 安全阀
      return
    }
    if (type === 'tool/call') {
      const key = `${sidOf(session)}\u0000${data?.callId ?? ''}`
      if (typeof data?.name === 'string' && data.name !== '') pending.set(key, data.name)
      if (pending.size > 500) pending.clear() // 安全阀：无结果的孤儿调用不无限囤积
      return
    }
    if (type !== 'tool/result') return
    const cid = data?.message?.source?.callId
    const key = `${sidOf(session)}\u0000${cid ?? ''}`
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

    // 工作区归属：会话缓存 → 活 Session 的 header.cwd（主路径）→ unknown。
    // header.cwd 与日志首条 session 事件的 cwd 同源，live/backfill 两端口径一致。
    const sid = sidOf(session)
    const ws = sessionCwd.get(sid) ?? headerCwd(session) ?? 'unknown'
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
  // 注意：harness 无 SessionEnd 事件，dsh-hooks 桥接的 Stop 映射到每轮 turn-stopping，
  // 所以「会话结束强制检查」只能挂在这个原生 session/disposed 事件上。
  ctx.on('session/disposed', () => {
    try {
      const checkPy = process.env.MEMORY_GUARD_CHECK
        ?? join(process.env.DSH_HOME ?? join(homedir(), '.dsh'), 'vault-guard', 'check.py')
      const child = spawn(
        'python',
        [checkPy, '--closing', '--remind'],
        { detached: true, stdio: 'ignore', windowsHide: true },
      )
      child.on('error', () => {
        // python 缺失等异步失败：门禁是提醒机制，绝不能以 uncaught error 崩溃宿主
      })
      child.unref()
    } catch {
      // 门禁失败静默：检查是提醒机制，不打断会话生命周期
    }
  })
}
