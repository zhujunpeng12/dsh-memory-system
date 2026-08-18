// test-attribution.mjs — evidence-ledger 工作区归属回归测试（模拟事件流单测）
//
// 背景：真实运行中，session/event post-commit 流不携带会话头事件
// （头事件走 sessionPersistence.create(meta)，与 append() 是两条路径），
// 旧代码只从事件流缓存 cwd → 全部实时调用落入 unknown（账本中 subagent 类
// 工具 100% unknown 的根因）。修复后活 Session 的 header.cwd 成为主路径。
//
// 用法：node test-attribution.mjs   （exit 0 = 全绿；不触碰真实账本）

import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// 重定向 DSH_HOME：测试只写临时目录，绝不碰真实账本
const home = mkdtempSync(join(tmpdir(), 'evidence-ledger-test-'))
process.env.DSH_HOME = home

const { apply } = await import('./index.js')

const handlers = {}
const ctx = { on: (ev, fn) => { handlers[ev] = fn } }
apply(ctx)
const feed = handlers['session/event']
if (typeof feed !== 'function') {
  console.error('FAIL: session/event handler not registered')
  process.exit(1)
}
if (typeof handlers['session/disposed'] !== 'function') {
  console.error('FAIL: session/disposed handler not registered')
  process.exit(1)
}
if (typeof handlers['dispose'] !== 'function') {
  console.error('FAIL: dispose handler not registered')
  process.exit(1)
}

const now = 1786800000000
const callEv = (name, callId) => ({ type: 'tool/call', time: now, data: { name, callId } })
const resultEv = (callId, text, isError = false) => ({
  type: 'tool/result',
  time: now,
  data: { message: { source: { callId }, content: [{ isError, content: [{ type: 'text', text }] }] } },
})
const sessionEv = (id, cwd) => ({ type: 'session', id, cwd })

// 活 Session 形状（真实验证过）：.header 是不变 SessionHeader{id,cwd,...}。
// 场景 A 刻意不给 .id —— 旧代码 `session?.id` 会得到 undefined 且不缓存任何 cwd。
const liveNoId = (id, cwd) => ({ header: { id, cwd, version: 0, createdAt: now } })
const liveWithId = (id, cwd) => ({ id, header: { id, cwd, version: 0, createdAt: now } })

// ── 场景 A：主回归 —— session 事件从未抵达流，session 只有 header（无 .id）──
feed(liveNoId('s1', 'D:\\my-project'), callEv('read', 'c1'))
feed(liveNoId('s1', 'D:\\my-project'), resultEv('c1', 'ok'))

// ── 场景 B：传统路径 —— 有 .id 且 session 事件带 cwd ──
feed(liveWithId('s2', 'D:\\my-project'), sessionEv('s2', 'D:\\my-project'))
feed(liveWithId('s2', 'D:\\my-project'), callEv('grep', 'c2'))
feed(liveWithId('s2', 'D:\\my-project'), resultEv('c2', '[exit code: 1] error', false))

// ── 场景 C：session 事件抵达但缺 cwd → 回退 header.cwd ──
feed(liveNoId('s3', 'C:\\ws-A'), sessionEv('s3', undefined))
feed(liveNoId('s3', 'C:\\ws-A'), callEv('pwsh', 'c3'))
feed(liveNoId('s3', 'C:\\ws-A'), resultEv('c3', 'ok'))

// ── 场景 D：错误分类仍工作（flagged）──
feed(liveNoId('s4', 'C:\\ws-A'), callEv('write', 'c4'))
feed(liveNoId('s4', 'C:\\ws-A'), resultEv('c4', 'TypeError: boom', true))

// ── 场景 E：全缺失 → unknown（兜底语义不变）──
feed({}, callEv('todo_write', 'c5'))
feed({}, resultEv('c5', 'ok'))

// ── 场景 F：call/result 间 session 形状翻转（.id 与 header.id 互换）──
// sidOf 用 ?? 链取同源 id，pending 键必须仍匹配
feed(liveNoId('s6', 'C:\\ws-B'), callEv('edit', 'c6'))
feed(liveWithId('s6', 'C:\\ws-B'), resultEv('c6', 'ok'))

// 等防抖 flush（2s）落地
await new Promise((r) => setTimeout(r, 2300))

const store = JSON.parse(readFileSync(join(home, 'storages', 'tool-telemetry.json'), 'utf8'))
rmSync(home, { recursive: true, force: true })

const failures = []
function check(label, actual, expected) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    failures.push(`${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
  }
}

// 场景 A：header 路径 → 正确工作区，且没有 unknown 污染
check('A read ws', store.tools.read.workspaces['D:\\my-project'], { calls: 1, errors: 0 })
check('A read unknown absent', store.tools.read.workspaces.unknown, undefined)
check('A read top calls', store.tools.read.calls, 1)
// 场景 B：事件路径 + exit 错误分类
check('B grep ws', store.tools.grep.workspaces['D:\\my-project'], { calls: 1, errors: 1 })
check('B grep errors', store.tools.grep.errors, { total: 1, flagged: 0, exit: 1, sandbox: 0, exception: 0 })
check('B grep top calls', store.tools.grep.calls, 1)
// 场景 C：事件缺 cwd → header 回退
check('C pwsh ws', store.tools.pwsh.workspaces['C:\\ws-A'], { calls: 1, errors: 0 })
check('C pwsh unknown absent', store.tools.pwsh.workspaces.unknown, undefined)
// 场景 D：flagged + exception 双类命中，total 只计一次
check('D write ws', store.tools.write.workspaces['C:\\ws-A'], { calls: 1, errors: 1 })
check('D write errors', store.tools.write.errors, { total: 1, flagged: 1, exit: 0, sandbox: 0, exception: 1 })
check('D write top calls', store.tools.write.calls, 1)
// 场景 E：兜底 unknown
check('E todo unknown', store.tools.todo_write.workspaces.unknown, { calls: 1, errors: 0 })
check('E todo top calls', store.tools.todo_write.calls, 1)
// 场景 F：形状翻转下 pending 键仍匹配（header.id 同源）
check('F edit ws', store.tools.edit.workspaces['C:\\ws-B'], { calls: 1, errors: 0 })
check('F edit unknown absent', store.tools.edit.workspaces.unknown, undefined)

if (failures.length) {
  console.error('FAIL:')
  for (const f of failures) console.error('  - ' + f)
  process.exit(1)
}
console.log('PASS: attribution regression suite (6 scenarios) all green')
